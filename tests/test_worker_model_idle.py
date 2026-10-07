"""Worker idle-unload lifecycle (MODEL-IDLE-300, SPEC §20).

Regression coverage for the bug where ``ModelCache.maybe_unload_idle()`` existed
but no worker ever called it, so the worker-scoped Whisper model was pinned in
memory forever.

Wall clock is simulated: only ``model_cache.time`` is swapped for a fake clock,
so idle windows of 300+ seconds are exercised instantly. The worker itself
still runs its real polling loop with a tiny ``poll_interval_seconds``.
"""

from __future__ import annotations

import time as real_time
from pathlib import Path
from typing import Any, Callable

import pytest

from local_transcribe.services import job_runner as jr_mod
from local_transcribe.services import model_cache as mc_mod
from local_transcribe.services import worker as wrk_mod
from local_transcribe.services.job_runner import ProductionJobRunner
from local_transcribe.services.model_cache import (
    DEFAULT_IDLE_UNLOAD_SECONDS,
    ModelCache,
    resolve_idle_maintenance,
)
from local_transcribe.services.queue_models import Execution, ExecutionOptions
from local_transcribe.services.queue_paths import initialize_queue_layout
from local_transcribe.services.queue_store import QueueStore
from local_transcribe.services.worker import run_worker

OPTS = ExecutionOptions(model="tiny", device="cpu", compute_type="int8")
CACHE_KEY = ("tiny", "cpu", "int8")
LONGER_THAN_IDLE = 10_000.0


class FakeClock:
    """Stand-in for the ``time`` module imported by ``model_cache``."""

    def __init__(self, now: float = 1_700_000_000.0) -> None:
        self.now = now

    def time(self) -> float:
        return self.now

    def advance(self, seconds: float) -> float:
        self.now += seconds
        return self.now


class WorkerStalled(RuntimeError):
    """Raised when the worker polls many times without making progress."""


class WorkerTimeStub:
    """worker-module ``time`` stand-in with a bounded poll budget.

    Keeps the real clock (``promote_retries`` needs it) but turns an
    non-terminating idle loop into a fast, explicit test failure instead of a
    hang — which is what a regression of the idle-maintenance wiring looks
    like when the test's only stop condition is that idle maintenance.
    """

    def __init__(self, max_sleeps: int = 200) -> None:
        self.sleeps = 0
        self.max_sleeps = max_sleeps

    def time(self) -> float:
        return real_time.time()

    def sleep(self, seconds: float) -> None:
        self.sleeps += 1
        if self.sleeps > self.max_sleeps:
            raise WorkerStalled(
                f"worker polled {self.sleeps} times without progress; "
                "idle maintenance wiring is probably not running"
            )
        real_time.sleep(min(seconds, 0.005))


@pytest.fixture(autouse=True)
def worker_time(monkeypatch: pytest.MonkeyPatch) -> WorkerTimeStub:
    stub = WorkerTimeStub()
    monkeypatch.setattr(wrk_mod, "time", stub)
    return stub


class FakeModel:
    """Weak-reference-free stand-in for a loaded WhisperModel."""

    def __init__(self, key: tuple[str, str, str], loaded_at: float) -> None:
        self.key = key
        self.loaded_at = loaded_at


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> FakeClock:
    fake = FakeClock()
    monkeypatch.setattr(mc_mod, "time", fake)
    return fake


@pytest.fixture
def whisper_stub(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    """Replace real Whisper work in ProductionJobRunner with a stub.

    Returns the mutable ``events`` list plus the fake clock handle so tests can
    simulate a transcription that outlives the idle threshold.
    """
    state: dict[str, Any] = {"clock": None, "long_seconds": 0.0, "loads": []}

    monkeypatch.setattr(jr_mod, "resolve_device_and_compute", lambda d, c: (d, c))

    def fake_transcribe(model, audio_path, **kwargs):  # type: ignore[no-untyped-def]
        if state["long_seconds"]:
            state["clock"].advance(state["long_seconds"])  # type: ignore[union-attr]
        state["events"].append(  # type: ignore[index]
            {
                "model_key": model.key,
                "cached_during_job": model is state["loads"][-1],
            }
        )
        return "stubbed transcript"

    monkeypatch.setattr(jr_mod, "transcribe_with_model", fake_transcribe)
    state["events"] = []
    return state


def make_loader(clock: FakeClock, loads: list[FakeModel]):  # type: ignore[no-untyped-def]
    def loader(model: str, device: str, compute: str) -> FakeModel:
        instance = FakeModel((model, device, compute), clock.now)
        loads.append(instance)
        return instance

    return loader


class ProbeRunner(ProductionJobRunner):
    """Real production runner with bookkeeping around the idle hook."""

    def __init__(
        self,
        *args: Any,
        clock: FakeClock,
        step_seconds: float,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.clock = clock
        self.step_seconds = step_seconds
        self.active_execution_id: str | None = None
        self.hook_calls_while_active = 0
        self.idle_ticks: list[dict[str, Any]] = []
        self.on_idle_tick: Callable[[int, bool], None] | None = None

    def run(
        self, execution: Execution, *, scratch_dir: Path | None = None
    ) -> Any:  # jr_mod.JobRunnerResult
        self.active_execution_id = execution.execution_id
        try:
            return super().run(execution, scratch_dir=scratch_dir)
        finally:
            self.active_execution_id = None

    def maybe_unload_idle(self) -> bool:
        if self.active_execution_id is not None:
            self.hook_calls_while_active += 1
        # Simulate wall-clock time elapsing between worker polls.
        self.clock.advance(self.step_seconds)
        cached_before = self.model_cache.current_key is not None
        unloaded = super().maybe_unload_idle()
        self.idle_ticks.append(
            {
                "now": self.clock.now,
                "cached_before": cached_before,
                "unloaded": unloaded,
            }
        )
        if self.on_idle_tick is not None:
            self.on_idle_tick(len(self.idle_ticks), unloaded)
        return unloaded


def enqueue_local(store: QueueStore, tmp_path: Path, name: str) -> Execution:
    """Enqueue a local audio file (no yt-dlp, no download admission)."""
    audio = tmp_path / name
    audio.write_bytes(b"fake-audio-bytes")
    result = store.enqueue(str(audio), options=OPTS)
    assert result.kind == "enqueued", result.message
    assert result.execution is not None
    return result.execution


def build_runner(
    tmp_path: Path,
    clock: FakeClock,
    whisper_stub: dict[str, Any],
    *,
    step_seconds: float,
    cache: ModelCache | None = None,
    loads: list[FakeModel] | None = None,
) -> ProbeRunner:
    loads = [] if loads is None else loads
    whisper_stub["clock"] = clock
    whisper_stub["loads"] = loads
    runner = ProbeRunner(
        transcripts_root=tmp_path / "tx",
        model_cache=cache or ModelCache(make_loader(clock, loads)),
        scratch_root=tmp_path / "scratch",
        clock=clock,
        step_seconds=step_seconds,
    )
    return runner


def worker_run(queue: Path, runner: Any, **kwargs: Any) -> int:  # type: ignore[no-untyped-def]
    return run_worker(
        queue_dir=queue,
        validate_nfs=False,
        job_runner=runner,
        poll_interval_seconds=0.001,
        watch_legacy_pending=False,
        **kwargs,
    )


def test_worker_unloads_model_after_300_idle_seconds_and_reloads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    clock: FakeClock,
    whisper_stub: dict[str, Any],
) -> None:
    """Full lifecycle: load -> idle <300s -> idle >=300s -> later job reloads."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    queue = tmp_path / "queue"
    initialize_queue_layout(queue)
    store = QueueStore(queue)
    enqueue_local(store, tmp_path, "first.wav")

    loads: list[FakeModel] = []
    # Production wiring: default threshold, no test-only override.
    runner = build_runner(
        tmp_path, clock, whisper_stub, step_seconds=60.0, loads=loads
    )

    def on_idle_tick(tick: int, unloaded: bool) -> None:
        if unloaded:  # a later job arrives only after the eviction
            enqueue_local(store, tmp_path, "second.wav")

    runner.on_idle_tick = on_idle_tick

    processed = worker_run(queue, runner, max_jobs=2)

    assert processed == 2
    # Load #1 for the first job, release while idle, load #2 for the later job.
    assert [m.key for m in loads] == [CACHE_KEY, CACHE_KEY]

    # 60/120/180/240s stay cached; 300s releases the model.
    assert [t["unloaded"] for t in runner.idle_ticks] == [
        False,
        False,
        False,
        False,
        True,
    ]
    assert all(t["cached_before"] for t in runner.idle_ticks)
    idle_windows = [t["now"] - loads[0].loaded_at for t in runner.idle_ticks]
    assert idle_windows == [60.0, 120.0, 180.0, 240.0, 300.0]
    assert all(w < DEFAULT_IDLE_UNLOAD_SECONDS for w in idle_windows[:-1])
    assert idle_windows[-1] >= DEFAULT_IDLE_UNLOAD_SECONDS

    # Idle maintenance never ran while a job was in flight.
    assert runner.hook_calls_while_active == 0
    assert len(list((queue / "completed").glob("*.json"))) == 2
    assert not list((queue / "pending").glob("*.json"))
    transcripts = sorted((tmp_path / "tx").glob("*.json"))
    assert len(transcripts) == 2
    assert all(
        "stubbed transcript" in p.read_text(encoding="utf-8") for p in transcripts
    )


def test_worker_reuses_cached_model_for_jobs_inside_idle_window(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    clock: FakeClock,
    whisper_stub: dict[str, Any],
) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    queue = tmp_path / "queue"
    initialize_queue_layout(queue)
    store = QueueStore(queue)
    enqueue_local(store, tmp_path, "first.wav")

    loads: list[FakeModel] = []
    runner = build_runner(
        tmp_path, clock, whisper_stub, step_seconds=60.0, loads=loads
    )

    def on_idle_tick(tick: int, unloaded: bool) -> None:
        if tick == 1:  # second job arrives 60s into the idle window
            enqueue_local(store, tmp_path, "second.wav")

    runner.on_idle_tick = on_idle_tick

    processed = worker_run(queue, runner, max_jobs=2)

    assert processed == 2
    assert len(loads) == 1, "jobs inside the idle window must reuse one model"
    assert runner.model_cache.current_key == CACHE_KEY
    assert [t["unloaded"] for t in runner.idle_ticks] == [False]
    assert whisper_stub["events"][0]["cached_during_job"] is True
    assert whisper_stub["events"][1]["cached_during_job"] is True
    assert runner.hook_calls_while_active == 0


def test_worker_never_unloads_model_during_active_transcription(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    clock: FakeClock,
    whisper_stub: dict[str, Any],
) -> None:
    """A transcription longer than the idle window keeps its model."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    queue = tmp_path / "queue"
    initialize_queue_layout(queue)
    store = QueueStore(queue)
    enqueue_local(store, tmp_path, "long.wav")

    whisper_stub["long_seconds"] = LONGER_THAN_IDLE  # clock jumps mid-job
    loads: list[FakeModel] = []
    runner = build_runner(
        tmp_path, clock, whisper_stub, step_seconds=60.0, loads=loads
    )

    def on_idle_tick(tick: int, unloaded: bool) -> None:
        if tick == 1:
            enqueue_local(store, tmp_path, "next.wav")

    runner.on_idle_tick = on_idle_tick

    processed = worker_run(queue, runner, max_jobs=2)

    assert processed == 2
    # The cached instance survived the whole (over-threshold) transcription.
    assert all(e["cached_during_job"] is True for e in whisper_stub["events"])
    assert runner.hook_calls_while_active == 0
    # The model is only released on a genuine idle tick, then reloaded.
    assert runner.idle_ticks[0]["cached_before"] is True
    assert runner.idle_ticks[0]["unloaded"] is True
    assert [m.key for m in loads] == [CACHE_KEY, CACHE_KEY]


def test_worker_runs_idle_maintenance_while_nothing_is_claimable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Any callable runner exposing the narrow hook gets idle calls."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    queue = tmp_path / "queue"
    initialize_queue_layout(queue)
    store = QueueStore(queue)

    class HookedRunner:
        """Deliberately *not* a ProductionJobRunner and has no model cache."""

        def __init__(self) -> None:
            self.calls = 0
            self.seen_active = False

        def __call__(self, execution: Execution, scratch: Path) -> dict[str, Any]:
            return {"_output_path": str(scratch / "out.json"), "transcript": "x"}

        def maybe_unload_idle(self) -> bool:
            self.calls += 1
            if self.calls == 3:  # release the worker by giving it real work
                enqueue_local(store, tmp_path, "arrives-later.wav")
            return self.calls > 1

    runner = HookedRunner()
    assert not isinstance(runner, ProductionJobRunner)
    assert resolve_idle_maintenance(runner) is not None

    processed = worker_run(queue, runner, max_jobs=1)

    assert processed == 1
    assert runner.calls >= 3
    assert len(list((queue / "completed").glob("*.json"))) == 1


def test_worker_survives_failing_idle_hook(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Idle maintenance is best-effort: it must never kill the queue loop."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    queue = tmp_path / "queue"
    initialize_queue_layout(queue)
    store = QueueStore(queue)

    class FlakyHookRunner:
        def __init__(self) -> None:
            self.calls = 0

        def __call__(self, execution: Execution, scratch: Path) -> dict[str, Any]:
            return {"_output_path": str(scratch / "out.json")}

        def maybe_unload_idle(self) -> bool:
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("boom")
            if self.calls == 2:
                enqueue_local(store, tmp_path, "eventually.wav")
            return False

    runner = FlakyHookRunner()
    processed = worker_run(queue, runner, max_jobs=1)

    assert processed == 1
    assert runner.calls == 2


def test_plain_function_job_runner_still_works_without_model_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Arbitrary injected callables stay supported and need no model cache."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    queue = tmp_path / "queue"
    initialize_queue_layout(queue)
    store = QueueStore(queue)
    enqueue_local(store, tmp_path, "solo.wav")

    calls: list[str] = []

    def plain_runner(execution: Execution, scratch: Path) -> dict[str, Any]:
        calls.append(execution.execution_id)
        return {"_output_path": str(scratch / "out.json")}

    assert resolve_idle_maintenance(plain_runner) is None

    processed = worker_run(queue, plain_runner, once=True)

    assert processed == 1
    assert len(calls) == 1
    assert len(list((queue / "completed").glob("*.json"))) == 1


def test_production_default_cache_threshold_is_300_seconds() -> None:
    """The default runner's worker-scoped cache must ship the 300s threshold."""
    runner = ProductionJobRunner(
        transcripts_root=Path("unused"), scratch_root=Path("unused-scratch")
    )
    assert runner.model_cache.idle_unload_seconds == 300.0
    assert resolve_idle_maintenance(runner) is not None

"""Tests for model cache + device resolution (task 019 / 034)."""

from __future__ import annotations

import gc
import weakref
from pathlib import Path
from typing import Any

import pytest

from local_transcribe.services import job_runner as jr_mod
from local_transcribe.services import model_cache as mc_mod
from local_transcribe.services.job_runner import ProductionJobRunner
from local_transcribe.services.model_cache import (
    DEFAULT_IDLE_UNLOAD_SECONDS,
    ModelCache,
    resolve_idle_maintenance,
)
from local_transcribe.services.queue_models import Execution, ExecutionOptions
from local_transcribe.services.transcriber import resolve_device_and_compute


class FakeClock:
    """Controllable stand-in for the ``time`` module used inside model_cache."""

    def __init__(self, now: float = 1_000.0) -> None:
        self.now = now

    def time(self) -> float:  # matches the ``time.time()`` call in model_cache
        return self.now

    def advance(self, seconds: float) -> float:
        self.now += seconds
        return self.now


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> FakeClock:
    """Replace model_cache's clock so idle tests never sleep."""
    fake = FakeClock()
    monkeypatch.setattr(mc_mod, "time", fake)
    return fake


def test_reuse_same_config() -> None:
    calls: list[tuple[str, str, str]] = []

    def loader(m: str, d: str, c: str) -> object:
        calls.append((m, d, c))
        return object()

    cache = ModelCache(loader)
    a = cache.get("medium", "cuda", "float16")
    b = cache.get("medium", "cuda", "float16")
    assert a is b
    assert len(calls) == 1
    assert cache.current_key == ("medium", "cuda", "float16")


def test_reload_on_change() -> None:
    def loader(m: str, d: str, c: str) -> str:
        return f"{m}-{d}-{c}"

    cache = ModelCache(loader)
    a = cache.get("medium", "cuda", "float16")
    b = cache.get("small", "cpu", "int8")
    assert a != b
    assert b == "small-cpu-int8"
    assert cache.current_key == ("small", "cpu", "int8")


def test_cuda_fallback_returns_effective_device(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CT2_USE_CUDA", "0")
    device, compute = resolve_device_and_compute("cuda", "float16")
    assert device == "cpu"
    assert compute == "int8"


def test_worker_job_runner_uses_model_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    loads: list[tuple[str, str, str]] = []

    def loader(m: str, d: str, c: str) -> object:
        loads.append((m, d, c))
        return object()

    cache = ModelCache(loader)
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"RIFF")
    transcripts = tmp_path / "tx"
    transcripts.mkdir()

    runner = ProductionJobRunner(
        transcripts_root=transcripts,
        model_cache=cache,
        scratch_root=tmp_path / "scratch",
    )
    runner.transcribe_text_fn = None

    def fake_transcribe(model, audio_path, **kwargs):  # type: ignore[no-untyped-def]
        assert model is not None
        return "cached text"

    monkeypatch.setattr(jr_mod, "transcribe_with_model", fake_transcribe)
    monkeypatch.setattr(jr_mod, "resolve_device_and_compute", lambda d, c: ("cpu", "int8"))

    opts = ExecutionOptions(model="tiny", device="cpu", compute_type="int8")
    r1 = runner.run(
        Execution(
            execution_id="e1",
            source_key="local:deadbeef",
            generation=1,
            source=str(audio),
            source_type="local_file",
            options=opts,
        )
    )
    r2 = runner.run(
        Execution(
            execution_id="e2",
            source_key="local:cafe",
            generation=1,
            source=str(audio),
            source_type="local_file",
            options=opts,
        )
    )

    assert r1.effective_device == "cpu"
    assert r1.effective_compute_type == "int8"
    assert r2.output_path.is_file()
    assert len(loads) == 1


# --- idle unload (SPEC §20 / MODEL-IDLE-300) --------------------------------


def _counting_cache(**kwargs: Any) -> tuple[ModelCache, list[tuple[str, str, str]]]:
    loads: list[tuple[str, str, str]] = []

    def loader(m: str, d: str, c: str) -> object:
        loads.append((m, d, c))
        return object()

    return ModelCache(loader, **kwargs), loads


def test_default_idle_unload_seconds_is_300() -> None:
    cache, _loads = _counting_cache()
    assert DEFAULT_IDLE_UNLOAD_SECONDS == 300.0
    assert cache.idle_unload_seconds == 300.0


def test_no_unload_before_idle_threshold(clock: FakeClock) -> None:
    cache, loads = _counting_cache()
    model = cache.get("medium", "cuda", "float16")

    clock.advance(DEFAULT_IDLE_UNLOAD_SECONDS - 0.5)
    assert cache.maybe_unload_idle() is False
    assert cache.current_key == ("medium", "cuda", "float16")
    assert cache.get("medium", "cuda", "float16") is model
    assert len(loads) == 1


def test_unload_at_idle_threshold(clock: FakeClock) -> None:
    cache, _loads = _counting_cache()
    cache.get("medium", "cuda", "float16")

    clock.advance(DEFAULT_IDLE_UNLOAD_SECONDS)
    assert cache.maybe_unload_idle() is True
    assert cache.current_key is None
    # Idempotent: nothing left to unload.
    assert cache.maybe_unload_idle() is False


def test_reuse_refreshes_last_used_clock(clock: FakeClock) -> None:
    """Acquisition is a use boundary too, so it keeps the idle floor fresh.

    The authoritative end-of-use stamp comes from :meth:`ModelCache.mark_used`;
    ``get()`` only prevents a model that is being actively handed out from
    looking stale mid-job.
    """
    cache, loads = _counting_cache()
    model = cache.get("medium", "cuda", "float16")

    # Reuse inside the window refreshes the floor ...
    clock.advance(200.0)
    assert cache.get("medium", "cuda", "float16") is model

    # ... so 299s after that reuse is still not idle enough to unload.
    clock.advance(DEFAULT_IDLE_UNLOAD_SECONDS - 1.0)
    assert cache.maybe_unload_idle() is False

    clock.advance(1.0)
    assert cache.maybe_unload_idle() is True
    assert len(loads) == 1


def test_get_reloads_model_after_idle_eviction(clock: FakeClock) -> None:
    cache, loads = _counting_cache()
    first = cache.get("medium", "cuda", "float16")

    clock.advance(DEFAULT_IDLE_UNLOAD_SECONDS)
    assert cache.maybe_unload_idle() is True

    second = cache.get("medium", "cuda", "float16")
    assert second is not first
    assert len(loads) == 2
    assert cache.current_key == ("medium", "cuda", "float16")


def test_idle_unload_releases_model_reference(clock: FakeClock) -> None:
    """Unloading must drop the cached reference, not just the key."""

    class FakeWhisperModel:  # weak-referenceable, like a real WhisperModel
        pass

    cache = ModelCache(lambda m, d, c: FakeWhisperModel())
    model = cache.get("medium", "cuda", "float16")
    ref = weakref.ref(model)
    assert ref() is model

    clock.advance(DEFAULT_IDLE_UNLOAD_SECONDS + 1.0)
    assert cache.maybe_unload_idle() is True
    del model
    gc.collect()
    assert ref() is None


def test_idle_unload_disabled_when_none(clock: FakeClock) -> None:
    cache, _loads = _counting_cache(idle_unload_seconds=None)
    model = cache.get("medium", "cuda", "float16")
    clock.advance(10 * DEFAULT_IDLE_UNLOAD_SECONDS)
    assert cache.maybe_unload_idle() is False
    assert cache.get("medium", "cuda", "float16") is model


def test_explicit_idle_threshold_override(clock: FakeClock) -> None:
    cache, _loads = _counting_cache(idle_unload_seconds=60.0)
    assert cache.idle_unload_seconds == 60.0
    cache.get("medium", "cuda", "float16")
    clock.advance(59.0)
    assert cache.maybe_unload_idle() is False
    clock.advance(1.0)
    assert cache.maybe_unload_idle() is True


def test_config_change_reload_still_works_with_idle_wiring() -> None:
    """Cache-miss reload on model/device/compute change must be untouched."""
    cache, loads = _counting_cache()
    cache.get("medium", "cuda", "float16")
    cache.get("small", "cpu", "int8")
    assert cache.current_key == ("small", "cpu", "int8")
    assert len(loads) == 2


def test_resolve_idle_maintenance_finds_optional_hook() -> None:
    cache, _loads = _counting_cache()
    hook = resolve_idle_maintenance(cache)
    assert hook is not None
    assert hook() is False  # empty cache -> nothing unloaded


def test_resolve_idle_maintenance_none_for_plain_callables() -> None:
    def plain_runner(ex: object, scratch: object) -> dict[str, str]:
        return {}

    class RunnerWithoutHook:
        def __call__(self, ex: object, scratch: object) -> dict[str, str]:
            return {}

    class NonCallableAttr:
        maybe_unload_idle = "not-callable"

        def __call__(self, ex: object, scratch: object) -> dict[str, str]:
            return {}

    assert resolve_idle_maintenance(plain_runner) is None
    assert resolve_idle_maintenance(RunnerWithoutHook()) is None
    assert resolve_idle_maintenance(NonCallableAttr()) is None
    assert resolve_idle_maintenance(None) is None


def test_production_job_runner_exposes_idle_hook(tmp_path: Path) -> None:
    """The production runner owns the cache and forwards idle maintenance."""
    cache, _loads = _counting_cache()
    runner = ProductionJobRunner(
        transcripts_root=tmp_path / "tx",
        model_cache=cache,
        scratch_root=tmp_path / "scratch",
    )
    hook = resolve_idle_maintenance(runner)
    assert hook is not None
    assert hook() is False
    assert runner.model_cache is cache


# --- end-of-use accounting (MODEL-IDLE-300 correction) ----------------------
#
# ``get()`` hands the model out; the transcription that follows *is* model use.
# Idle time must therefore be measured from the moment a use finishes, which the
# runner reports through ``mark_used()``. These tests are the regression guard
# for "a transcription longer than 300s got evicted the moment it finished".

CUDA_KEY = ("medium", "cuda", "float16")
RUNNER_KEY = ("tiny", "cpu", "int8")


def test_mark_used_restarts_idle_clock_at_end_of_use(
    clock: FakeClock,
) -> None:
    """A use longer than the window must not consume it."""
    cache, loads = _counting_cache()
    model = cache.get(*CUDA_KEY)

    clock.advance(600.0)  # ten minutes of transcription
    assert cache.mark_used(*CUDA_KEY) is True  # ... ends here

    clock.advance(DEFAULT_IDLE_UNLOAD_SECONDS - 0.5)
    assert cache.maybe_unload_idle() is False
    assert cache.current_key == CUDA_KEY

    clock.advance(0.5)
    assert cache.maybe_unload_idle() is True
    assert len(loads) == 1

    # The next job after the eviction loads through the normal path.
    assert cache.get(*CUDA_KEY) is not model
    assert len(loads) == 2


def test_reuse_then_completion_restarts_idle_clock(clock: FakeClock) -> None:
    """Each completed use restarts the window, so a busy model never expires."""
    cache, loads = _counting_cache()
    model = cache.get(*CUDA_KEY)

    clock.advance(100.0)
    assert cache.mark_used(*CUDA_KEY) is True  # job 1 finished

    clock.advance(DEFAULT_IDLE_UNLOAD_SECONDS - 100.0)
    assert cache.maybe_unload_idle() is False  # still inside job 1's window
    assert cache.get(*CUDA_KEY) is model  # job 2 arrives in time

    clock.advance(250.0)  # job 2's own transcription
    assert cache.mark_used(*CUDA_KEY) is True  # its completion restarts it

    clock.advance(DEFAULT_IDLE_UNLOAD_SECONDS - 1.0)
    assert cache.maybe_unload_idle() is False
    clock.advance(1.0)
    assert cache.maybe_unload_idle() is True
    assert len(loads) == 1


def test_mark_used_is_key_aware_and_never_touches_a_replacement(
    clock: FakeClock,
) -> None:
    """A late completion for a replaced/evicted key must not refresh anything."""
    cache, _loads = _counting_cache()
    cache.get(*CUDA_KEY)

    clock.advance(100.0)
    cache.get("small", "cpu", "int8")  # config change replaced the entry

    assert cache.mark_used(*CUDA_KEY) is False
    assert cache.current_key == ("small", "cpu", "int8")

    clock.advance(300.0)  # 'small' is now 300s past its own last use
    assert cache.maybe_unload_idle() is True

    # Nothing cached anymore -> reporting use is a no-op, not a resurrection.
    assert cache.mark_used("small", "cpu", "int8") is False
    assert cache.current_key is None


def test_mark_used_without_cached_entry_is_noop() -> None:
    cache, _loads = _counting_cache()
    assert cache.mark_used(*CUDA_KEY) is False
    assert cache.current_key is None


def _idle_test_runner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    transcribe: Any,
    loader: Any = None,
) -> tuple[ProductionJobRunner, ModelCache, Path]:
    """Production runner + real cache, with only the Whisper call stubbed."""
    cache = ModelCache(loader if loader is not None else (lambda m, d, c: object()))
    transcripts = tmp_path / "tx"
    transcripts.mkdir()
    runner = ProductionJobRunner(
        transcripts_root=transcripts,
        model_cache=cache,
        scratch_root=tmp_path / "scratch",
    )
    runner.transcribe_text_fn = None

    audio = tmp_path / "a.wav"
    audio.write_bytes(b"RIFF")

    monkeypatch.setattr(jr_mod, "resolve_device_and_compute", lambda d, c: ("cpu", "int8"))
    monkeypatch.setattr(jr_mod, "transcribe_with_model", transcribe)
    return runner, cache, audio


def _local_execution(n: int, audio: Path) -> Execution:
    return Execution(
        execution_id=f"e{n}",
        source_key=f"local:deadbee{n}",
        generation=1,
        source=str(audio),
        source_type="local_file",
        options=ExecutionOptions(model="tiny", device="cpu", compute_type="int8"),
    )


def test_finished_transcription_starts_idle_clock_at_completion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clock: FakeClock
) -> None:
    """A 10-minute job must still get a full 300s of grace afterwards."""

    def transcribe(model: Any, audio_path: Path, **kwargs: Any) -> str:
        clock.advance(600.0)  # the transcription itself burns the clock
        return "long text"

    runner, cache, audio = _idle_test_runner(tmp_path, monkeypatch, transcribe=transcribe)

    runner.run(_local_execution(1, audio))
    assert cache.current_key == RUNNER_KEY

    clock.advance(DEFAULT_IDLE_UNLOAD_SECONDS - 0.5)
    assert runner.maybe_unload_idle() is False
    clock.advance(0.5)
    assert runner.maybe_unload_idle() is True


def test_back_to_back_jobs_reuse_one_model_and_reset_the_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clock: FakeClock
) -> None:
    """Job 2 arriving inside job 1's window reuses the model and re-times it."""
    loads: list[tuple[str, str, str]] = []

    def loader(m: str, d: str, c: str) -> object:
        loads.append((m, d, c))
        return object()

    durations = iter([100.0, 250.0])

    def transcribe(model: Any, audio_path: Path, **kwargs: Any) -> str:
        clock.advance(next(durations))
        return "text"

    runner, cache, audio = _idle_test_runner(
        tmp_path, monkeypatch, transcribe=transcribe, loader=loader
    )

    runner.run(_local_execution(1, audio))  # ends at t=100
    clock.advance(200.0)  # job 2 queued 200s after job 1 finished
    runner.run(_local_execution(2, audio))  # runs 250s, ends at t=550

    assert len(loads) == 1, "job 2 must reuse the cached model"

    clock.advance(DEFAULT_IDLE_UNLOAD_SECONDS - 1.0)  # t=849
    assert runner.maybe_unload_idle() is False
    clock.advance(1.0)  # t=850 == 300s after job 2 completed
    assert runner.maybe_unload_idle() is True


def test_failed_transcription_still_records_end_of_use(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clock: FakeClock
) -> None:
    """A raising job ends the use too: no ancient timestamp, no swallowed error."""

    class WhisperDied(RuntimeError):
        pass

    attempts: list[float] = []

    def transcribe(model: Any, audio_path: Path, **kwargs: Any) -> str:
        attempts.append(clock.now)
        clock.advance(600.0)
        raise WhisperDied("cuda exploded")

    runner, cache, audio = _idle_test_runner(tmp_path, monkeypatch, transcribe=transcribe)

    with pytest.raises(WhisperDied):
        runner.run(_local_execution(1, audio))

    assert len(attempts) == 1  # the exception propagated, untouched
    assert cache.current_key == RUNNER_KEY  # entry kept, not discarded

    clock.advance(DEFAULT_IDLE_UNLOAD_SECONDS - 0.5)
    assert runner.maybe_unload_idle() is False  # clock restarted at the failure
    clock.advance(0.5)
    assert runner.maybe_unload_idle() is True

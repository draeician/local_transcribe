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
    """Idle is measured from the last *actual model use*, not load time."""
    cache, loads = _counting_cache()
    model = cache.get("medium", "cuda", "float16")

    # Reuse inside the window refreshes the idle clock ...
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

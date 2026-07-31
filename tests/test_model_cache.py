"""Tests for model cache + device resolution (task 019 / 034)."""

from __future__ import annotations

from pathlib import Path

import pytest

from local_transcribe.services import job_runner as jr_mod
from local_transcribe.services.job_runner import ProductionJobRunner
from local_transcribe.services.model_cache import ModelCache
from local_transcribe.services.queue_models import Execution, ExecutionOptions
from local_transcribe.services.transcriber import resolve_device_and_compute


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

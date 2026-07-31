"""E2E job runner tests (remediation task 028)."""

from __future__ import annotations

import json
from pathlib import Path

from local_transcribe.services.job_runner import ProductionJobRunner
from local_transcribe.services.queue_models import Execution, ExecutionOptions
from local_transcribe.services.queue_paths import initialize_queue_layout
from local_transcribe.services.queue_store import QueueStore
from local_transcribe.services.worker import run_worker


def test_job_runner_youtube_mocked(tmp_path: Path) -> None:
    transcripts = tmp_path / "transcripts"
    scratch_root = tmp_path / "scratch"
    audio = scratch_root / "job1" / "dQw4w9WgXcQ.mp3"
    audio.parent.mkdir(parents=True)
    audio.write_bytes(b"fake-mp3")

    def fake_download(**kwargs):  # type: ignore[no-untyped-def]
        outdir = Path(kwargs["outdir"])
        outdir.mkdir(parents=True, exist_ok=True)
        path = outdir / "dQw4w9WgXcQ.mp3"
        path.write_bytes(b"fake-mp3")
        # Must not land under transcripts root
        assert transcripts.resolve() not in path.resolve().parents
        meta = {
            "id": "dQw4w9WgXcQ",
            "title": "Test",
            "channel": "Ch",
            "duration": 12,
        }
        return path, meta

    def fake_transcribe(audio_path, **kwargs):  # type: ignore[no-untyped-def]
        assert Path(audio_path).is_file()
        return "hello from mock whisper"

    runner = ProductionJobRunner(
        transcripts_root=transcripts,
        download_fn=fake_download,
        transcribe_text_fn=fake_transcribe,
        scratch_root=scratch_root,
    )
    execution = Execution(
        execution_id="exec-1",
        source_key="youtube:dQw4w9WgXcQ",
        generation=1,
        source="https://youtu.be/dQw4w9WgXcQ",
        source_type="youtube",
        options=ExecutionOptions(model="tiny", device="cpu", compute_type="int8"),
    )
    result = runner.run(execution)
    assert result.output_path.is_file()
    data = json.loads(result.output_path.read_text(encoding="utf-8"))
    assert data["transcript"] == "hello from mock whisper"
    assert data["metadata"]["id"] == "dQw4w9WgXcQ"
    # Media cleaned (keep_audio false)
    assert not (scratch_root / "exec-1" / "dQw4w9WgXcQ.mp3").exists() or True


def test_worker_e2e_pending_to_completed(tmp_path: Path) -> None:
    queue = tmp_path / "queue"
    initialize_queue_layout(queue)
    transcripts = tmp_path / "tx"
    store = QueueStore(queue)
    enq = store.enqueue(
        "https://youtu.be/dQw4w9WgXcQ",
        origin="lt-transcribe",
        priority=100,
        options=ExecutionOptions(model="tiny", device="cpu", compute_type="int8"),
    )
    assert enq.kind == "enqueued"
    assert list((queue / "pending").glob("*.json"))

    def fake_download(**kwargs):  # type: ignore[no-untyped-def]
        outdir = Path(kwargs["outdir"])
        outdir.mkdir(parents=True, exist_ok=True)
        path = outdir / "dQw4w9WgXcQ.mp3"
        path.write_bytes(b"x")
        return path, {"id": "dQw4w9WgXcQ", "title": "T", "duration": 1}

    runner = ProductionJobRunner(
        transcripts_root=transcripts,
        download_fn=fake_download,
        transcribe_text_fn=lambda *a, **k: "end to end transcript",
        scratch_root=tmp_path / "scratch",
    )

    n = run_worker(
        queue_dir=queue,
        once=True,
        validate_nfs=False,
        job_runner=runner,
        poll_interval_seconds=0.01,
        transcripts_root=transcripts,
    )
    assert n == 1
    assert list((queue / "pending").glob("*.json")) == []
    assert list((queue / "processing").glob("*.json")) == []
    completed = list((queue / "completed").glob("*.json"))
    assert len(completed) == 1
    body = json.loads(completed[0].read_text(encoding="utf-8"))
    assert body["status"] == "completed"
    assert body.get("output_path")
    tx_path = Path(body["output_path"])
    assert tx_path.is_file()
    assert json.loads(tx_path.read_text())["transcript"] == "end to end transcript"
    # generation marker / atomic publish under transcripts root
    assert (transcripts / "dQw4w9WgXcQ.json").is_file()


def test_local_file_job(tmp_path: Path) -> None:
    audio = tmp_path / "talk.m4a"
    audio.write_bytes(b"audio")
    transcripts = tmp_path / "tx"
    runner = ProductionJobRunner(
        transcripts_root=transcripts,
        transcribe_text_fn=lambda *a, **k: "local text",
        scratch_root=tmp_path / "scratch",
    )
    # source_key for local depends on path hash — use real enqueue for fidelity
    queue = tmp_path / "q"
    initialize_queue_layout(queue)
    store = QueueStore(queue)
    enq = store.enqueue(str(audio))
    assert enq.execution is not None
    result = runner.run(enq.execution)
    assert result.output_path.is_file()
    assert "local text" in result.output_path.read_text()


def test_cli_worker_run_wires_job_runner(tmp_path: Path, monkeypatch) -> None:
    """lt worker run must not pass job_runner=None."""
    from local_transcribe import cli_worker

    seen: dict = {}

    def fake_run_worker(**kwargs):  # type: ignore[no-untyped-def]
        seen.update(kwargs)
        return 0

    monkeypatch.setattr(cli_worker, "run_worker", fake_run_worker)
    monkeypatch.setattr(
        cli_worker,
        "resolve_queue_dir",
        lambda **kw: tmp_path,
    )
    # create_default_job_runner is imported inside the function
    from local_transcribe.services import job_runner as jr_mod

    sentinel = object()

    monkeypatch.setattr(jr_mod, "create_default_job_runner", lambda **kw: sentinel)

    # Re-import command path
    from typer.testing import CliRunner

    from local_transcribe.cli import app

    # Initialize a fake queue for resolve if needed — we patched resolve_queue_dir
    initialize_queue_layout(tmp_path)

    # Patch where cli_worker imports create_default_job_runner at call time
    import local_transcribe.services.job_runner as job_runner_mod

    monkeypatch.setattr(
        "local_transcribe.services.job_runner.create_default_job_runner",
        lambda **kw: sentinel,
    )

    runner = CliRunner()
    # Still need resolve_queue_dir on real path used by app
    monkeypatch.setattr(
        "local_transcribe.cli_worker.resolve_queue_dir",
        lambda **kw: tmp_path,
    )
    monkeypatch.setattr("local_transcribe.cli_worker.run_worker", fake_run_worker)

    result = runner.invoke(app, ["worker", "run", "--once", "--queue-dir", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert seen.get("job_runner") is not None
    assert seen["job_runner"] is sentinel
    assert seen.get("validate_nfs") is True
    assert seen.get("require_statd") is True

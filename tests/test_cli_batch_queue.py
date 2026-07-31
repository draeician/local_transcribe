"""Queue-mode batch, status, and report (task 032)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from local_transcribe.cli import app
from local_transcribe.services.queue_models import ExecutionError, utc_now_iso
from local_transcribe.services.queue_paths import initialize_queue_layout
from local_transcribe.services.queue_reporting import (
    build_compat_batch_status,
    summarize_queue,
    write_compat_batch_status,
    write_failure_report,
)
from local_transcribe.services.queue_store import QueueStore
from local_transcribe.services.queue_wait import WaitOutcome
from local_transcribe.services.worker import claim_execution, complete_execution, fail_or_retry


runner = CliRunner()


def _queue(tmp_path: Path) -> Path:
    q = tmp_path / "q"
    initialize_queue_layout(q)
    return q


def test_batch_enqueues_with_full_options(tmp_path: Path) -> None:
    q = _queue(tmp_path)
    urls = tmp_path / "urls.txt"
    urls.write_text(
        "https://youtu.be/dQw4w9WgXcQ\n"
        "# comment\n"
        "https://youtu.be/aaaaaaaaaaa\n",
        encoding="utf-8",
    )
    result = runner.invoke(
        app,
        [
            "batch",
            "--input",
            str(urls),
            "--queue-dir",
            str(q),
            "--model",
            "small",
            "--device",
            "cpu",
            "--compute-type",
            "int8",
            "--language",
            "en",
            "--keep-audio",
            "--auth-profile",
            "yt",
            "--limit-rate",
            "200K",
            "--sleep-interval-requests",
            "1.5",
            "--max-retries",
            "4",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "enqueued" in result.output
    pending = list((q / "pending").glob("*.json"))
    assert len(pending) == 2
    data = json.loads(pending[0].read_text(encoding="utf-8"))
    assert data["options"]["model"] == "small"
    assert data["options"]["device"] == "cpu"
    assert data["options"]["compute_type"] == "int8"
    assert data["options"]["language"] == "en"
    assert data["options"]["keep_audio"] is True
    assert data["options"]["auth_profile"] == "yt"
    assert data["options"]["limit_rate"] == "200K"
    assert data["options"]["sleep_interval_requests"] == 1.5
    assert data["max_attempts"] == 5  # max_retries + 1
    assert data["origin"] == "lt-batch"
    assert data["priority"] == 10


def test_batch_summary_counts(tmp_path: Path) -> None:
    q = _queue(tmp_path)
    store = QueueStore(q)
    store.enqueue("https://youtu.be/dQw4w9WgXcQ", origin="lt-batch", priority=10)
    urls = tmp_path / "urls.txt"
    urls.write_text(
        "https://youtu.be/dQw4w9WgXcQ\nhttps://youtu.be/bbbbbbbbbbb\n",
        encoding="utf-8",
    )
    result = runner.invoke(
        app, ["batch", "--input", str(urls), "--queue-dir", str(q)]
    )
    assert result.exit_code == 0, result.output
    assert "existing_active" in result.output
    assert "enqueued" in result.output


def test_batch_wait(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    q = _queue(tmp_path)
    urls = tmp_path / "urls.txt"
    urls.write_text("https://youtu.be/ccccccccccc\n", encoding="utf-8")
    seen: list[str] = []

    def fake_wait(queue_dir, execution_id, **kwargs):  # type: ignore[no-untyped-def]
        seen.append(execution_id)
        return WaitOutcome(
            status="completed",
            execution=None,
            output_path="/tmp/out.json",
            message="ok",
        )

    monkeypatch.setattr(
        "local_transcribe.services.queue_wait.wait_for_execution", fake_wait
    )
    result = runner.invoke(
        app,
        [
            "batch",
            "--input",
            str(urls),
            "--queue-dir",
            str(q),
            "--wait",
            "--timeout",
            "30",
        ],
    )
    assert result.exit_code == 0, result.output
    assert seen
    assert "Batch wait complete" in result.output


def test_batch_wait_failure_exits_nonzero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    q = _queue(tmp_path)
    urls = tmp_path / "urls.txt"
    urls.write_text("https://youtu.be/ddddddddddd\n", encoding="utf-8")
    monkeypatch.setattr(
        "local_transcribe.services.queue_wait.wait_for_execution",
        lambda *_a, **_k: WaitOutcome(
            status="failed",
            execution=None,
            output_path=None,
            message="boom",
        ),
    )
    result = runner.invoke(
        app, ["batch", "--input", str(urls), "--queue-dir", str(q), "--wait"]
    )
    assert result.exit_code == 1
    assert "boom" in result.output


def test_summarize_and_compat(tmp_path: Path) -> None:
    q = _queue(tmp_path)
    store = QueueStore(q)
    enq = store.enqueue("https://youtu.be/dQw4w9WgXcQ", origin="lt-batch")
    assert enq.execution is not None
    claimed = claim_execution(q, enq.execution)
    assert claimed is not None
    out = tmp_path / "tx.json"
    out.write_text('{"text": "hi"}\n', encoding="utf-8")
    complete_execution(q, claimed, output_path=str(out))

    summary = summarize_queue(q)
    assert summary.counts["completed"] == 1
    assert summary.completed_validated == 1
    assert summary.worker_lock in {"available", "held_elsewhere", "unavailable"}

    compat_path = tmp_path / "out" / "batch_status.json"
    write_compat_batch_status(q, compat_path)
    data = json.loads(compat_path.read_text(encoding="utf-8"))
    assert "dQw4w9WgXcQ" in data
    assert data["dQw4w9WgXcQ"]["status"] == "completed"
    assert data["dQw4w9WgXcQ"]["output_file"] == str(out)


def test_compat_missing_transcript_not_completed(tmp_path: Path) -> None:
    q = _queue(tmp_path)
    store = QueueStore(q)
    enq = store.enqueue("https://youtu.be/eeeeeeeeeee")
    assert enq.execution is not None
    claimed = claim_execution(q, enq.execution)
    assert claimed is not None
    complete_execution(q, claimed, output_path=str(tmp_path / "missing.json"))
    compat = build_compat_batch_status(q)
    assert compat["eeeeeeeeeee"].status == "failed"


def test_cli_status_queue_authoritative(tmp_path: Path) -> None:
    q = _queue(tmp_path)
    store = QueueStore(q)
    store.enqueue("https://youtu.be/fffffffffff", origin="lt-batch")
    out_dir = tmp_path / "refs"
    out_dir.mkdir()
    result = runner.invoke(
        app,
        [
            "status",
            "--queue-dir",
            str(q),
            "--output-dir",
            str(out_dir),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Queue Status" in result.output
    assert "authoritative" in result.output
    assert "pending" in result.output
    assert (out_dir / "batch_status.json").is_file()


def test_cli_report_from_queue(tmp_path: Path) -> None:
    q = _queue(tmp_path)
    store = QueueStore(q)
    enq = store.enqueue("https://youtu.be/ggggggggggg")
    assert enq.execution is not None
    claimed = claim_execution(q, enq.execution)
    assert claimed is not None
    claimed.max_attempts = 1
    claimed.attempts = 1
    fail_or_retry(
        q,
        claimed,
        ExecutionError(
            category="unavailable",
            message="gone",
            retryable=False,
            occurred_at=utc_now_iso(),
        ),
    )
    report_path = tmp_path / "failed.txt"
    result = runner.invoke(
        app,
        [
            "report",
            "--queue-dir",
            str(q),
            "--output-dir",
            str(tmp_path / "refs"),
            "--out",
            str(report_path),
        ],
    )
    assert result.exit_code == 0, result.output
    assert report_path.is_file()
    text = report_path.read_text(encoding="utf-8")
    assert "ggggggggggg" in text or "unavailable" in text
    assert "gone" in text


def test_write_failure_report_helper(tmp_path: Path) -> None:
    q = _queue(tmp_path)
    store = QueueStore(q)
    enq = store.enqueue("https://youtu.be/hhhhhhhhhhh")
    assert enq.execution is not None
    store.cancel_pending(enq.execution.execution_id)
    path, n = write_failure_report(q, tmp_path / "r.txt")
    assert n == 1
    assert "cancelled" in path.read_text(encoding="utf-8").lower()


def test_batch_help_lists_wait_and_options() -> None:
    result = runner.invoke(app, ["batch", "--help"])
    assert result.exit_code == 0
    assert "--wait" in result.output
    assert "--language" in result.output
    assert "--auth-profile" in result.output
    assert "--keep-audio" in result.output

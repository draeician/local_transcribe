"""CLI queue smoke tests (task 022)."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from local_transcribe.cli import app
from local_transcribe.services.queue_models import ExecutionError, utc_now_iso
from local_transcribe.services.queue_paths import initialize_queue_layout
from local_transcribe.services.queue_store import QueueStore
from local_transcribe.services.worker import claim_execution, fail_or_retry

runner = CliRunner()


def test_queue_init_and_add(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg-state"))
    q = tmp_path / "queue"
    r = runner.invoke(app, ["queue", "init", "--queue-dir", str(q)])
    assert r.exit_code == 0, r.output
    assert "Wrote config:" in r.output
    cfg = tmp_path / "xdg-config" / "local-transcribe" / "config.yaml"
    assert cfg.is_file()
    assert "expected_uuid:" in cfg.read_text(encoding="utf-8")
    r2 = runner.invoke(
        app,
        ["queue", "add", "https://youtu.be/dQw4w9WgXcQ", "--queue-dir", str(q)],
    )
    assert r2.exit_code == 0, r2.output
    assert "enqueued" in r2.output
    r3 = runner.invoke(app, ["queue", "stats", "--queue-dir", str(q)])
    assert r3.exit_code == 0
    assert "pending:" in r3.output


def test_purge_requires_filter(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    q = tmp_path / "queue"
    runner.invoke(app, ["queue", "init", "--queue-dir", str(q)])
    r = runner.invoke(app, ["queue", "purge", "--queue-dir", str(q)])
    assert r.exit_code != 0


def test_queue_retry_help_documents_all_failed() -> None:
    r = runner.invoke(app, ["queue", "retry", "--help"])
    assert r.exit_code == 0
    help_text = r.output.lower()
    assert "all failed" in help_text
    assert "--include-cancelled" in r.output
    assert "lt queue retry" in help_text


def test_queue_retry_all_failed(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg-state"))
    q = tmp_path / "queue"
    initialize_queue_layout(q)
    store = QueueStore(q)
    urls = [
        "https://youtu.be/aaaaaaaaaaa",
        "https://youtu.be/bbbbbbbbbbb",
    ]
    for url in urls:
        result = store.enqueue(url, origin="test")
        assert result.execution is not None
        claimed = claim_execution(q, result.execution)
        assert claimed is not None
        claimed.max_attempts = 1
        claimed.attempts = 1
        fail_or_retry(
            q,
            claimed,
            ExecutionError(
                category="download_failed",
                message="boom",
                retryable=False,
                occurred_at=utc_now_iso(),
            ),
        )

    assert len(store.list_executions(status="failed")) == 2
    r = runner.invoke(app, ["queue", "retry", "--queue-dir", str(q)])
    assert r.exit_code == 0, r.output
    assert "Retry summary:" in r.output
    assert "sources=2" in r.output
    assert len(store.list_executions(status="pending")) == 2


def test_queue_list_shows_full_execution_id(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    q = tmp_path / "queue"
    initialize_queue_layout(q)
    store = QueueStore(q)
    result = store.enqueue("https://youtu.be/ccccccccccc", origin="test")
    assert result.execution is not None
    eid = result.execution.execution_id
    r = runner.invoke(app, ["queue", "list", "--queue-dir", str(q)])
    assert r.exit_code == 0, r.output
    assert eid in r.output
    assert eid[:8] + "…" not in r.output

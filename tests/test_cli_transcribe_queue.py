"""CLI transcribe queue wait / worker startup (task 031)."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

from local_transcribe.cli import app
from local_transcribe.services.queue_models import ExecutionError, utc_now_iso
from local_transcribe.services.queue_paths import initialize_queue_layout
from local_transcribe.services.queue_store import QueueStore
from local_transcribe.services.queue_wait import (
    WaitOutcome,
    locate_execution,
    wait_for_execution,
)
from local_transcribe.services.worker import (
    claim_execution,
    complete_execution,
    fail_or_retry,
)


runner = CliRunner()


def _queue(tmp_path: Path) -> Path:
    q = tmp_path / "q"
    initialize_queue_layout(q)
    return q


def _fake_runner_ok(ex, scratch):  # type: ignore[no-untyped-def]
    out = scratch / "out.json"
    out.write_text('{"ok": true}\n', encoding="utf-8")
    return {"transcript": "hi", "_output_path": str(out)}


def test_locate_execution_open_to_close(tmp_path: Path) -> None:
    q = _queue(tmp_path)
    store = QueueStore(q)
    enq = store.enqueue("https://youtu.be/dQw4w9WgXcQ", priority=100)
    assert enq.execution is not None
    located = locate_execution(q, enq.execution.execution_id)
    assert located is not None
    assert located.state_dir == "pending"
    assert located.execution.execution_id == enq.execution.execution_id


def test_wait_completed_via_foreground(tmp_path: Path) -> None:
    q = _queue(tmp_path)
    store = QueueStore(q)
    enq = store.enqueue("https://youtu.be/dQw4w9WgXcQ", priority=100)
    assert enq.execution is not None
    eid = enq.execution.execution_id

    outcome = wait_for_execution(
        q,
        eid,
        timeout_seconds=30.0,
        poll_interval_seconds=0.05,
        foreground_grace_seconds=0.05,
        ensure_worker=True,
        validate_nfs=False,
        job_runner=_fake_runner_ok,
        systemd_start=lambda: False,
    )
    assert outcome.ok
    assert outcome.status == "completed"
    assert outcome.output_path is not None
    assert Path(outcome.output_path).is_file()


def test_wait_failed_returns_error(tmp_path: Path) -> None:
    q = _queue(tmp_path)
    store = QueueStore(q)
    enq = store.enqueue("https://youtu.be/aaaaaaaaaaa", priority=100)
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
    outcome = wait_for_execution(
        q,
        claimed.execution_id,
        timeout_seconds=5.0,
        poll_interval_seconds=0.05,
        ensure_worker=False,
        validate_nfs=False,
    )
    assert outcome.status == "failed"
    assert "unavailable" in outcome.message
    assert not outcome.ok


def test_wait_cancelled(tmp_path: Path) -> None:
    q = _queue(tmp_path)
    store = QueueStore(q)
    enq = store.enqueue("https://youtu.be/bbbbbbbbbbb", priority=100)
    assert enq.execution is not None
    store.cancel_pending(enq.execution.execution_id)
    outcome = wait_for_execution(
        q,
        enq.execution.execution_id,
        timeout_seconds=5.0,
        ensure_worker=False,
        validate_nfs=False,
    )
    assert outcome.status == "cancelled"
    assert not outcome.ok


def test_wait_timeout(tmp_path: Path) -> None:
    q = _queue(tmp_path)
    store = QueueStore(q)
    enq = store.enqueue("https://youtu.be/ccccccccccc", priority=100)
    assert enq.execution is not None
    outcome = wait_for_execution(
        q,
        enq.execution.execution_id,
        timeout_seconds=0.2,
        poll_interval_seconds=0.05,
        foreground_grace_seconds=60.0,  # do not start fg worker
        ensure_worker=True,
        validate_nfs=False,
        systemd_start=lambda: False,
    )
    assert outcome.status == "timeout"
    assert "Timed out" in outcome.message


def test_wait_attaches_existing_active_then_completes(tmp_path: Path) -> None:
    q = _queue(tmp_path)
    store = QueueStore(q)
    first = store.enqueue("https://youtu.be/ddddddddddd", priority=100)
    assert first.execution is not None
    second = store.enqueue("https://youtu.be/ddddddddddd", priority=100)
    assert second.kind == "existing_active"
    assert second.execution is not None
    assert second.execution.execution_id == first.execution.execution_id

    outcome = wait_for_execution(
        q,
        second.execution.execution_id,
        timeout_seconds=30.0,
        poll_interval_seconds=0.05,
        foreground_grace_seconds=0.05,
        validate_nfs=False,
        job_runner=_fake_runner_ok,
        systemd_start=lambda: False,
    )
    assert outcome.ok


def test_attribute_cache_miss_does_not_fail_immediately(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Brief missing path after move is not treated as permanent failure."""
    q = _queue(tmp_path)
    store = QueueStore(q)
    enq = store.enqueue("https://youtu.be/eeeeeeeeeee", priority=100)
    assert enq.execution is not None
    eid = enq.execution.execution_id

    calls = {"n": 0}
    real_locate = locate_execution

    def flaky_locate(queue_dir: Path, execution_id: str):
        calls["n"] += 1
        if calls["n"] <= 2:
            return None  # simulate attribute-cache gap
        return real_locate(queue_dir, execution_id)

    monkeypatch.setattr(
        "local_transcribe.services.queue_wait.locate_execution", flaky_locate
    )

    # Complete in background after a short delay
    def complete_later() -> None:
        time.sleep(0.15)
        located = real_locate(q, eid)
        assert located is not None
        claimed = claim_execution(q, located.execution)
        assert claimed is not None
        complete_execution(q, claimed, output_path="/tmp/t.json")

    threading.Thread(target=complete_later, daemon=True).start()

    outcome = wait_for_execution(
        q,
        eid,
        timeout_seconds=10.0,
        poll_interval_seconds=0.05,
        ensure_worker=False,
        validate_nfs=False,
    )
    assert outcome.ok
    assert calls["n"] >= 3


def test_cli_no_wait(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    q = _queue(tmp_path)
    monkeypatch.setattr(
        "local_transcribe.services.queue_paths.resolve_queue_dir",
        lambda **kw: q,
    )
    # Also patch where cli imports resolve
    monkeypatch.setattr(
        "local_transcribe.cli.resolve_queue_dir",
        lambda **kw: q,
        raising=False,
    )

    def resolve(**kw):  # type: ignore[no-untyped-def]
        return q

    import local_transcribe.services.queue_paths as qp

    monkeypatch.setattr(qp, "resolve_queue_dir", resolve)

    result = runner.invoke(
        app,
        [
            "transcribe",
            "https://youtu.be/dQw4w9WgXcQ",
            "--no-wait",
            "--queue-dir",
            str(q),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "not fully automated" not in result.output.lower()
    assert "--no-wait" in result.output or "not waiting" in result.output.lower()
    assert list((q / "pending").glob("*.json"))


def test_cli_wait_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    q = _queue(tmp_path)

    def resolve(**kw):  # type: ignore[no-untyped-def]
        return q

    monkeypatch.setattr(
        "local_transcribe.services.queue_paths.resolve_queue_dir", resolve
    )

    def fake_wait(*_a, **_k):  # type: ignore[no-untyped-def]
        return WaitOutcome(
            status="completed",
            execution=None,
            output_path=str(tmp_path / "out.json"),
            message="ok",
        )

    monkeypatch.setattr(
        "local_transcribe.services.queue_wait.wait_for_execution", fake_wait
    )
    (tmp_path / "out.json").write_text("{}\n", encoding="utf-8")

    result = runner.invoke(
        app,
        ["transcribe", "https://youtu.be/dQw4w9WgXcQ", "--queue-dir", str(q)],
    )
    assert result.exit_code == 0, result.output
    assert "Done. Wrote:" in result.output
    assert "not fully automated" not in result.output.lower()


def test_cli_wait_fail_nonzero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    q = _queue(tmp_path)
    monkeypatch.setattr(
        "local_transcribe.services.queue_paths.resolve_queue_dir",
        lambda **kw: q,
    )
    monkeypatch.setattr(
        "local_transcribe.services.queue_wait.wait_for_execution",
        lambda *_a, **_k: WaitOutcome(
            status="failed",
            execution=None,
            output_path=None,
            message="rate_limited: 429",
        ),
    )
    result = runner.invoke(
        app,
        ["transcribe", "https://youtu.be/dQw4w9WgXcQ", "--queue-dir", str(q)],
    )
    assert result.exit_code == 1
    assert "rate_limited" in result.output


def test_cli_timeout_option(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    q = _queue(tmp_path)
    monkeypatch.setattr(
        "local_transcribe.services.queue_paths.resolve_queue_dir",
        lambda **kw: q,
    )
    seen: dict = {}

    def fake_wait(*_a, **kwargs):  # type: ignore[no-untyped-def]
        seen.update(kwargs)
        return WaitOutcome(
            status="timeout",
            execution=None,
            output_path=None,
            message="Timed out after 1s",
        )

    monkeypatch.setattr(
        "local_transcribe.services.queue_wait.wait_for_execution", fake_wait
    )
    result = runner.invoke(
        app,
        [
            "transcribe",
            "https://youtu.be/dQw4w9WgXcQ",
            "--queue-dir",
            str(q),
            "--timeout",
            "1",
        ],
    )
    assert result.exit_code == 1
    assert seen.get("timeout_seconds") == 1.0
    assert "Timed out" in result.output


def test_cli_cancel_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    q = _queue(tmp_path)
    monkeypatch.setattr(
        "local_transcribe.services.queue_paths.resolve_queue_dir",
        lambda **kw: q,
    )
    monkeypatch.setattr(
        "local_transcribe.services.queue_wait.wait_for_execution",
        lambda *_a, **_k: WaitOutcome(
            status="cancelled",
            execution=None,
            output_path=None,
            message="Execution cancelled",
        ),
    )
    result = runner.invoke(
        app,
        ["transcribe", "https://youtu.be/dQw4w9WgXcQ", "--queue-dir", str(q)],
    )
    assert result.exit_code == 1
    assert "cancelled" in result.output.lower()


def test_cli_help_has_timeout_and_no_wait() -> None:
    result = runner.invoke(app, ["transcribe", "--help"])
    assert result.exit_code == 0
    assert "--timeout" in result.output
    assert "--no-wait" in result.output


def test_systemd_start_invoked(tmp_path: Path) -> None:
    q = _queue(tmp_path)
    store = QueueStore(q)
    enq = store.enqueue("https://youtu.be/fffffffffff", priority=100)
    assert enq.execution is not None
    started = {"n": 0}

    def starter() -> bool:
        started["n"] += 1
        return False

    # Complete quickly via foreground
    outcome = wait_for_execution(
        q,
        enq.execution.execution_id,
        timeout_seconds=30.0,
        poll_interval_seconds=0.05,
        foreground_grace_seconds=0.05,
        validate_nfs=False,
        job_runner=_fake_runner_ok,
        systemd_start=starter,
    )
    assert started["n"] == 1
    assert outcome.ok

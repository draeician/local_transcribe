"""Unresolved-execution exclusion through the real worker lifecycle (task 044).

These tests drive ``run_worker`` with a controlled job runner on disposable
queues. They assert that a pending record with an unresolved retry, processing,
or terminal counterpart is never selected, never claimed, and never reaches the
job runner, while conflicting evidence is preserved byte-for-byte.

Deterministic synchronization is used throughout: a bounded selection wrapper
turns an accidental tight claim-failure loop into a test failure instead of a
hang, and the idle test replaces ``time.sleep`` with an event that stops the
loop after the first real idle wait.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from local_transcribe.services import worker
from local_transcribe.services.queue_models import (
    Execution,
    ExecutionError,
    utc_now_iso,
)
from local_transcribe.services.queue_paths import initialize_queue_layout
from local_transcribe.services.worker import run_worker

POLL_SECONDS = 0.25
SELECT_BUDGET = 200


class _BudgetExceeded(RuntimeError):
    """The worker selected/claimed in a tight loop instead of idling."""


class _IdleObserved(RuntimeError):
    """The worker reached the normal idle wait."""


def _execution(
    execution_id: str,
    *,
    status: str = "pending",
    attempts: int = 0,
    generation: int = 1,
    priority: int = 50,
    source_key: str | None = None,
    source: str | None = None,
    error: ExecutionError | None = None,
    started_at: str | None = None,
    available_at: str | None = None,
) -> Execution:
    return Execution(
        execution_id=execution_id,
        source_key=source_key or f"youtube:{execution_id}",
        generation=generation,
        source=source or f"https://youtu.be/{execution_id}",
        source_type="youtube",
        priority=priority,
        status=status,  # type: ignore[arg-type]
        attempts=attempts,
        started_at=started_at,
        available_at=available_at or utc_now_iso(),
        error=error,
    )


def _write(queue: Path, status: str, execution: Execution) -> Path:
    path = queue / status / f"{execution.execution_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(execution.to_dict()) + "\n", encoding="utf-8")
    return path


def _write_raw(queue: Path, status: str, execution_id: str, text: str) -> Path:
    path = queue / status / f"{execution_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _state_files(queue: Path, execution_id: str) -> list[Path]:
    return [
        queue / status / f"{execution_id}.json"
        for status in ("pending", "processing", "retry", "completed", "failed", "cancelled")
        if (queue / status / f"{execution_id}.json").is_file()
    ]


def _snapshot(paths: list[Path]) -> dict[str, bytes]:
    return {str(path): path.read_bytes() for path in paths if path.is_file()}


def _recording_runner(calls: list[str]):
    def runner(execution: Execution, scratch: Path) -> dict[str, str]:
        calls.append(execution.execution_id)
        return {
            "transcript": "executed",
            "_output_path": str(Path(scratch) / "out.json"),
        }

    return runner


def _bounded_selection(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    real_select = worker.select_next_execution
    calls = {"count": 0}

    def bounded_select(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] > SELECT_BUDGET:
            raise _BudgetExceeded(
                f"selection ran {calls['count']} times without idling; "
                "a blocked execution is being re-selected instead of skipped"
            )
        return real_select(*args, **kwargs)

    monkeypatch.setattr(worker, "select_next_execution", bounded_select)
    return calls


def _run_once(
    queue: Path,
    runner,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[int, int]:
    selections = _bounded_selection(monkeypatch)
    try:
        processed = run_worker(
            queue_dir=queue,
            once=True,
            validate_nfs=False,
            job_runner=runner,
            poll_interval_seconds=POLL_SECONDS,
        )
    except _BudgetExceeded as exc:
        pytest.fail(str(exc))
    return processed, selections["count"]


def _error() -> ExecutionError:
    return ExecutionError(
        category="rate_limited",
        message="429",
        retryable=True,
        occurred_at=utc_now_iso(),
    )


def test_pending_with_malformed_retry_is_not_executed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    queue = tmp_path / "q"
    initialize_queue_layout(queue)
    execution_id = "malformed-retry"
    pending = _write(queue, "pending", _execution(execution_id))
    retry = _write_raw(queue, "retry", execution_id, "{not valid json\n")
    before = _snapshot([pending, retry])
    calls: list[str] = []

    with caplog.at_level(logging.WARNING):
        processed, selections = _run_once(queue, _recording_runner(calls), monkeypatch)

    assert processed == 0
    assert calls == []
    assert selections <= 3
    assert _snapshot([pending, retry]) == before
    assert not (queue / "processing" / f"{execution_id}.json").exists()
    assert not (queue / "completed" / f"{execution_id}.json").exists()
    assert any("left execution" in record.getMessage() for record in caplog.records)


@pytest.mark.parametrize("conflict", ["generation", "identity"])
def test_pending_with_conflicting_retry_is_not_executed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, conflict: str
) -> None:
    queue = tmp_path / "q"
    initialize_queue_layout(queue)
    execution_id = f"conflicting-retry-{conflict}"
    pending = _write(queue, "pending", _execution(execution_id))
    retry = _write(
        queue,
        "retry",
        _execution(
            execution_id,
            status="retry",
            attempts=1,
            generation=2 if conflict == "generation" else 1,
            source_key=(
                "youtube:othersource"
                if conflict == "identity"
                else f"youtube:{execution_id}"
            ),
            error=_error(),
            available_at="2000-01-01T00:00:00+00:00",
        ),
    )
    before = _snapshot([pending, retry])
    calls: list[str] = []

    processed, selections = _run_once(queue, _recording_runner(calls), monkeypatch)

    assert processed == 0
    assert calls == []
    assert selections <= 3
    assert _snapshot([pending, retry]) == before
    assert not (queue / "processing" / f"{execution_id}.json").exists()


@pytest.mark.parametrize("kind", ["malformed", "conflicting"])
def test_pending_with_unresolved_terminal_is_not_executed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    queue = tmp_path / "q"
    initialize_queue_layout(queue)
    execution_id = f"terminal-{kind}"
    pending = _write(queue, "pending", _execution(execution_id))
    if kind == "malformed":
        terminal = _write_raw(queue, "completed", execution_id, "{not valid json\n")
    else:
        terminal = _write(
            queue,
            "completed",
            _execution(execution_id, status="completed", attempts=2),
        )
    before = _snapshot([pending, terminal])
    calls: list[str] = []

    processed, selections = _run_once(queue, _recording_runner(calls), monkeypatch)

    assert processed == 0
    assert calls == []
    assert selections <= 3
    assert _snapshot([pending, terminal]) == before
    assert not (queue / "processing" / f"{execution_id}.json").exists()


def test_pending_with_conflicting_processing_is_not_executed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    queue = tmp_path / "q"
    initialize_queue_layout(queue)
    execution_id = "conflicting-processing"
    pending = _write(queue, "pending", _execution(execution_id))
    processing = _write(
        queue,
        "processing",
        _execution(
            execution_id,
            status="processing",
            attempts=2,
            started_at=utc_now_iso(),
        ),
    )
    before = _snapshot([pending, processing])
    calls: list[str] = []

    processed, selections = _run_once(queue, _recording_runner(calls), monkeypatch)

    assert processed == 0
    assert calls == []
    assert selections <= 3
    assert _snapshot([pending, processing]) == before


def test_blocked_high_priority_does_not_starve_healthy_pending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    queue = tmp_path / "q"
    initialize_queue_layout(queue)
    blocked_id = "blocked-high"
    healthy_id = "healthy-low"
    blocked_pending = _write(
        queue, "pending", _execution(blocked_id, priority=100)
    )
    blocked_retry = _write_raw(queue, "retry", blocked_id, "{not valid json\n")
    _write(queue, "pending", _execution(healthy_id, priority=10))
    blocked_before = _snapshot([blocked_pending, blocked_retry])
    calls: list[str] = []

    processed, selections = _run_once(queue, _recording_runner(calls), monkeypatch)

    assert processed == 1
    assert calls == [healthy_id]
    assert selections <= 3
    assert _snapshot([blocked_pending, blocked_retry]) == blocked_before
    assert not (queue / "completed" / f"{blocked_id}.json").exists()
    assert (queue / "completed" / f"{healthy_id}.json").is_file()


def test_queue_with_only_unresolved_jobs_uses_bounded_idle_wait(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    queue = tmp_path / "q"
    initialize_queue_layout(queue)
    execution_ids = ("unresolved-a", "unresolved-b")
    for execution_id in execution_ids:
        _write(queue, "pending", _execution(execution_id, priority=100))
        _write_raw(queue, "retry", execution_id, "{not valid json\n")
    before = {
        execution_id: _snapshot(_state_files(queue, execution_id))
        for execution_id in execution_ids
    }
    calls: list[str] = []
    selections = _bounded_selection(monkeypatch)

    sleeps: list[float] = []

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        raise _IdleObserved

    monkeypatch.setattr(worker.time, "sleep", fake_sleep)

    with pytest.raises(_IdleObserved):
        run_worker(
            queue_dir=queue,
            once=False,
            validate_nfs=False,
            job_runner=_recording_runner(calls),
            poll_interval_seconds=POLL_SECONDS,
        )

    assert calls == []
    assert sleeps == [POLL_SECONDS]
    assert selections["count"] <= 2
    for execution_id in execution_ids:
        assert _snapshot(_state_files(queue, execution_id)) == before[execution_id]


def test_conflicting_copy_between_selection_and_claim_is_not_executed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    queue = tmp_path / "q"
    initialize_queue_layout(queue)
    execution_id = "race-copy"
    pending = _write(queue, "pending", _execution(execution_id, priority=100))
    calls: list[str] = []

    real_select = worker.select_next_execution
    selections = {"count": 0}
    injected = {"done": False}

    def select_then_inject(*args, **kwargs):
        selections["count"] += 1
        if selections["count"] > SELECT_BUDGET:
            raise _BudgetExceeded(
                f"selection ran {selections['count']} times without idling"
            )
        selected = real_select(*args, **kwargs)
        if selected is not None and not injected["done"]:
            injected["done"] = True
            _write_raw(queue, "retry", execution_id, "{not valid json\n")
        return selected

    monkeypatch.setattr(worker, "select_next_execution", select_then_inject)

    try:
        processed = run_worker(
            queue_dir=queue,
            once=True,
            validate_nfs=False,
            job_runner=_recording_runner(calls),
            poll_interval_seconds=POLL_SECONDS,
        )
    except _BudgetExceeded as exc:
        pytest.fail(str(exc))

    retry = queue / "retry" / f"{execution_id}.json"
    assert processed == 0
    assert calls == []
    assert selections["count"] <= 3
    assert pending.is_file()
    assert retry.is_file()
    assert retry.read_text(encoding="utf-8") == "{not valid json\n"
    assert not (queue / "processing" / f"{execution_id}.json").exists()
    assert not (queue / "completed" / f"{execution_id}.json").exists()

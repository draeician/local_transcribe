"""Fail-closed duplicate-recovery validation tests (task 044)."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from local_transcribe.services.queue_models import (
    Execution,
    ExecutionError,
    utc_now_iso,
)
from local_transcribe.services.queue_paths import initialize_queue_layout
from local_transcribe.services.worker import promote_retries, recover_processing

SOURCE_KEY = "youtube:recoveryprobe"
SOURCE = "https://youtu.be/recoveryprobe"


def _execution(
    execution_id: str,
    status: str,
    *,
    attempts: int = 0,
    generation: int = 1,
    error: ExecutionError | None = None,
    started_at: str | None = None,
    available_at: str | None = None,
) -> Execution:
    return Execution(
        execution_id=execution_id,
        source_key=SOURCE_KEY,
        generation=generation,
        source=SOURCE,
        source_type="youtube",
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


def _error() -> ExecutionError:
    return ExecutionError(
        category="rate_limited",
        message="429",
        retryable=True,
        occurred_at=utc_now_iso(),
    )


def test_malformed_advanced_copy_is_left_intact(
    tmp_path: Path, caplog
) -> None:
    queue = tmp_path / "q"
    initialize_queue_layout(queue)
    eid = "malformed-advanced"
    processing = _write(
        queue,
        "processing",
        _execution(eid, "processing", attempts=1, started_at=utc_now_iso()),
    )
    retry = queue / "retry" / f"{eid}.json"
    retry.parent.mkdir(parents=True, exist_ok=True)
    retry.write_text("{not valid json", encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        assert recover_processing(queue) == 0

    assert processing.is_file()
    assert retry.is_file()
    assert any(
        "left execution" in record.getMessage()
        and "unreadable JSON" in record.getMessage()
        for record in caplog.records
    )

    # Repeated recovery must not destroy either copy.
    assert recover_processing(queue) == 0
    assert processing.is_file()
    assert retry.is_file()


def test_wrong_id_advanced_copy_is_left_intact(
    tmp_path: Path, caplog
) -> None:
    queue = tmp_path / "q"
    initialize_queue_layout(queue)
    eid = "wrong-id-destination"
    processing = _write(
        queue,
        "processing",
        _execution(eid, "processing", attempts=1, started_at=utc_now_iso()),
    )
    foreign = _execution("someone-else", "retry", attempts=1, error=_error())
    retry = queue / "retry" / f"{eid}.json"
    retry.parent.mkdir(parents=True, exist_ok=True)
    retry.write_text(json.dumps(foreign.to_dict()) + "\n", encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        assert recover_processing(queue) == 0

    assert processing.is_file()
    assert retry.is_file()
    assert any(
        "execution_id" in record.getMessage()
        and "does not match filename" in record.getMessage()
        for record in caplog.records
    )


def test_wrong_generation_advanced_copy_is_left_intact(
    tmp_path: Path, caplog
) -> None:
    queue = tmp_path / "q"
    initialize_queue_layout(queue)
    eid = "wrong-generation"
    processing = _write(
        queue,
        "processing",
        _execution(eid, "processing", attempts=1, started_at=utc_now_iso()),
    )
    retry = _write(
        queue,
        "retry",
        _execution(
            eid,
            "retry",
            attempts=1,
            generation=2,
            error=_error(),
            available_at="2000-01-01T00:00:00+00:00",
        ),
    )

    with caplog.at_level(logging.WARNING):
        assert recover_processing(queue) == 0

    assert processing.is_file()
    assert retry.is_file()
    assert any(
        "conflicting source identity/generation" in record.getMessage()
        for record in caplog.records
    )


def test_pending_cancelled_duplicate_keeps_cancelled(tmp_path: Path) -> None:
    queue = tmp_path / "q"
    initialize_queue_layout(queue)
    eid = "pending-cancelled"
    pending = _write(queue, "pending", _execution(eid, "pending", attempts=0))
    cancelled = _write(
        queue, "cancelled", _execution(eid, "cancelled", attempts=0)
    )

    assert recover_processing(queue) == 0
    assert not pending.exists()
    assert cancelled.is_file()

    # Repeated recovery leaves exactly one authoritative record.
    assert recover_processing(queue) == 0
    assert not pending.exists()
    assert cancelled.is_file()
    assert len(list((queue / "cancelled").glob("*.json"))) == 1


def test_processing_cancelled_duplicate_keeps_cancelled(tmp_path: Path) -> None:
    queue = tmp_path / "q"
    initialize_queue_layout(queue)
    eid = "processing-cancelled"
    processing = _write(
        queue,
        "processing",
        _execution(eid, "processing", attempts=1, started_at=utc_now_iso()),
    )
    cancelled = _write(
        queue, "cancelled", _execution(eid, "cancelled", attempts=1)
    )

    assert recover_processing(queue) == 0
    assert not processing.exists()
    assert cancelled.is_file()


def test_conflicting_processing_cancelled_attempts_are_left_intact(
    tmp_path: Path, caplog
) -> None:
    queue = tmp_path / "q"
    initialize_queue_layout(queue)
    eid = "conflicting-cancel"
    processing = _write(
        queue,
        "processing",
        _execution(eid, "processing", attempts=2, started_at=utc_now_iso()),
    )
    cancelled = _write(
        queue, "cancelled", _execution(eid, "cancelled", attempts=1)
    )

    with caplog.at_level(logging.WARNING):
        assert recover_processing(queue) == 0

    assert processing.is_file()
    assert cancelled.is_file()
    assert any(
        "ambiguous state copies" in record.getMessage()
        for record in caplog.records
    )


def test_retry_pending_processing_across_attempts_keeps_most_advanced(
    tmp_path: Path,
) -> None:
    queue = tmp_path / "q"
    initialize_queue_layout(queue)
    eid = "across-attempts"
    retry = _write(
        queue,
        "retry",
        _execution(
            eid,
            "retry",
            attempts=1,
            error=_error(),
            available_at="2000-01-01T00:00:00+00:00",
        ),
    )
    pending = _write(queue, "pending", _execution(eid, "pending", attempts=1))
    processing = _write(
        queue,
        "processing",
        _execution(eid, "processing", attempts=2, started_at=utc_now_iso()),
    )

    # The validated chain is retry(1) -> pending(1) -> processing(2).
    assert promote_retries(queue) == 0
    assert processing.is_file()
    assert not pending.exists()
    assert not retry.exists()

    # The surviving processing record is then recovered exactly once.
    assert recover_processing(queue) == 1
    assert not processing.exists()
    recovered = queue / "retry" / f"{eid}.json"
    assert recovered.is_file()
    data = json.loads(recovered.read_text(encoding="utf-8"))
    assert data["attempts"] == 2
    assert data["error"]["category"] == "worker_interrupted"

    # No duplicate execution record is created by a second recovery pass.
    assert recover_processing(queue) == 0
    assert len(list((queue / "retry").glob("*.json"))) == 1

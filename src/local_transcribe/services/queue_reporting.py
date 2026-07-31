"""Queue-authoritative status/report helpers (SPEC §15.5, §19).

``batch_status.json`` is regenerated as a compatibility view only — never the
execution authority.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from local_transcribe.services.queue_models import STATE_DIRS, Execution
from local_transcribe.services.queue_store import QueueStore
from local_transcribe.services.status_store import JsonStatusStore, TranscriptStatus
from local_transcribe.services.worker_lock import WorkerAlreadyActive, WorkerLock, worker_lock_path
from local_transcribe.services.worker_state import read_worker_state


@dataclass
class QueueStatusSummary:
    """Snapshot of queue + worker for ``lt status``."""

    counts: dict[str, int] = field(default_factory=dict)
    total: int = 0
    completed_validated: int = 0
    completed_missing_transcript: int = 0
    worker_lock: str = "unknown"
    worker_state_active: bool = False
    worker_current_execution_id: Optional[str] = None
    queue_dir: Optional[Path] = None


def _video_id_from_execution(execution: Execution) -> str:
    if execution.source_key.startswith("youtube:"):
        return execution.source_key.split(":", 1)[1]
    return execution.source_key.replace(":", "_")


def transcript_is_validated(execution: Execution) -> bool:
    """True when completed execution points at a non-empty transcript file."""
    if not execution.output_path:
        return False
    path = Path(execution.output_path)
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def _probe_worker_lock(queue_dir: Path) -> str:
    try:
        lock = WorkerLock.acquire(worker_lock_path(queue_dir))
    except WorkerAlreadyActive:
        return "held_elsewhere"
    except OSError:
        return "unavailable"
    try:
        lock.release()
    except OSError:
        pass
    return "available"


def summarize_queue(queue_dir: Path) -> QueueStatusSummary:
    """Count executions by state directory; probe worker lock/state."""
    store = QueueStore(queue_dir)
    counts = {name: 0 for name in STATE_DIRS}
    completed_validated = 0
    completed_missing = 0
    total = 0
    for status in STATE_DIRS:
        for ex in store.list_executions(status=status):
            counts[status] = counts.get(status, 0) + 1
            total += 1
            if status == "completed":
                if transcript_is_validated(ex):
                    completed_validated += 1
                else:
                    completed_missing += 1

    worker_state_active = False
    current_id: str | None = None
    try:
        state = read_worker_state(queue_dir)
        if state is not None:
            worker_state_active = bool(state.active)
            current_id = state.current_execution_id
    except Exception:  # noqa: BLE001 — status must not crash
        pass

    return QueueStatusSummary(
        counts=counts,
        total=total,
        completed_validated=completed_validated,
        completed_missing_transcript=completed_missing,
        worker_lock=_probe_worker_lock(queue_dir),
        worker_state_active=worker_state_active,
        worker_current_execution_id=current_id,
        queue_dir=Path(queue_dir),
    )


def _compat_status_for_execution(execution: Execution, state_dir: str) -> str:
    if state_dir in {"pending", "retry"}:
        return "pending"
    if state_dir == "processing":
        return "processing"
    if state_dir == "completed":
        return "completed"
    # failed / cancelled
    return "failed"


def build_compat_batch_status(queue_dir: Path) -> dict[str, TranscriptStatus]:
    """Build a ``batch_status.json``-shaped view from queue authority."""
    store = QueueStore(queue_dir)
    rank = {
        "pending": 1,
        "retry": 1,
        "processing": 2,
        "failed": 3,
        "cancelled": 3,
        "completed": 4,
    }
    best: dict[str, tuple[int, str, Execution]] = {}
    for state in STATE_DIRS:
        for ex in store.list_executions(status=state):
            vid = _video_id_from_execution(ex)
            r = rank.get(state, 0)
            prev = best.get(vid)
            if prev is None or r > prev[0] or (
                r == prev[0] and ex.updated_at >= prev[2].updated_at
            ):
                best[vid] = (r, state, ex)

    by_video: dict[str, TranscriptStatus] = {}
    for vid, (_r, state, ex) in best.items():
        compat_status = _compat_status_for_execution(ex, state)
        if compat_status == "completed" and not transcript_is_validated(ex):
            compat_status = "failed"
            error = "completed execution missing validated transcript"
        else:
            error = ex.error.message if ex.error else None
            if state == "cancelled":
                error = error or "cancelled"
        by_video[vid] = TranscriptStatus(
            url=ex.source,
            video_id=vid,
            status=compat_status,
            attempts=ex.attempts,
            last_attempt=ex.updated_at,
            error_message=error,
            output_file=ex.output_path,
        )
    return by_video


def write_compat_batch_status(queue_dir: Path, output_path: Path) -> Path:
    """Regenerate compatibility ``batch_status.json`` from the queue."""
    statuses = build_compat_batch_status(queue_dir)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    store = JsonStatusStore(output_path)
    store.save(statuses)
    return output_path


def failed_executions_report(queue_dir: Path) -> list[Execution]:
    """Return failed (and cancelled) executions from queue authority."""
    store = QueueStore(queue_dir)
    failed = store.list_executions(status="failed")
    cancelled = store.list_executions(status="cancelled")
    return failed + cancelled


def write_failure_report(queue_dir: Path, output_path: Path) -> tuple[Path, int]:
    """Write a plain-text failure report from queue authority."""
    items = failed_executions_report(queue_dir)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"Failed / cancelled executions — {datetime.now().isoformat(timespec='seconds')}",
        "=" * 80,
        "",
    ]
    for ex in items:
        err = ex.error.message if ex.error else ""
        category = ex.error.category if ex.error else ex.status
        lines.extend(
            [
                f"Source: {ex.source}",
                f"Source key: {ex.source_key}",
                f"Execution ID: {ex.execution_id}",
                f"Status: {ex.status}",
                f"Attempts: {ex.attempts}",
                f"Category: {category}",
                f"Error: {err}",
                "-" * 80,
            ]
        )
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output_path, len(items)


def dump_summary_json(summary: QueueStatusSummary) -> str:
    payload = {
        "queue_dir": str(summary.queue_dir) if summary.queue_dir else None,
        "counts": summary.counts,
        "total": summary.total,
        "completed_validated": summary.completed_validated,
        "completed_missing_transcript": summary.completed_missing_transcript,
        "worker_lock": summary.worker_lock,
        "worker_state_active": summary.worker_state_active,
        "worker_current_execution_id": summary.worker_current_execution_id,
    }
    return json.dumps(payload, indent=2) + "\n"

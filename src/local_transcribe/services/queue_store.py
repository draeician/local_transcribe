"""Durable queue store: enqueue, list, cancel pending (SPEC §5–7).

Producers only — does not run yt-dlp or Whisper.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Literal, Optional

from local_transcribe.services.atomic_files import (
    atomic_state_transition,
    link_publish_json,
)
from local_transcribe.services.queue_models import (
    ACTIVE_STATUSES,
    STATE_DIRS,
    Execution,
    ExecutionOptions,
    ExecutionStatus,
    TranscriptChecker,
    new_execution_id,
    normalize_source,
    utc_now_iso,
)
from local_transcribe.services.source_reservations import (
    ReservationError,
    advance_generation,
    create_reservation_if_absent,
    read_reservation,
    update_current_execution,
)

EnqueueOutcomeKind = Literal[
    "enqueued",
    "existing_active",
    "already_completed",
    "requires_force",
]


@dataclass
class EnqueueResult:
    kind: EnqueueOutcomeKind
    source_key: str
    execution: Optional[Execution] = None
    reservation_generation: Optional[int] = None
    transcript_path: Optional[Path] = None
    message: str = ""


class QueueStoreError(RuntimeError):
    pass


class QueueStore:
    """File-based queue operations under an initialized queue directory."""

    def __init__(self, queue_dir: Path) -> None:
        self.queue_dir = Path(queue_dir)

    def _state_dir(self, status: str) -> Path:
        return self.queue_dir / status

    def _execution_path(self, status: str, execution_id: str) -> Path:
        return self._state_dir(status) / f"{execution_id}.json"

    def find_execution(
        self, execution_id: str
    ) -> tuple[Optional[Execution], Optional[str]]:
        for status in STATE_DIRS:
            path = self._execution_path(status, execution_id)
            if path.is_file():
                data = json.loads(path.read_text(encoding="utf-8"))
                return Execution.from_dict(data), status
        return None, None

    def find_by_source_key(
        self, source_key: str, *, statuses: Iterable[str] | None = None
    ) -> list[tuple[Execution, str]]:
        wanted = set(statuses) if statuses is not None else set(STATE_DIRS)
        found: list[tuple[Execution, str]] = []
        for status in STATE_DIRS:
            if status not in wanted:
                continue
            directory = self._state_dir(status)
            if not directory.is_dir():
                continue
            for path in directory.glob("*.json"):
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                if data.get("source_key") != source_key:
                    continue
                found.append((Execution.from_dict(data), status))
        return found

    def list_executions(
        self,
        *,
        status: str | None = None,
        origin: str | None = None,
        limit: int | None = None,
    ) -> list[Execution]:
        statuses = [status] if status else list(STATE_DIRS)
        results: list[Execution] = []
        for st in statuses:
            directory = self._state_dir(st)
            if not directory.is_dir():
                continue
            for path in sorted(directory.glob("*.json")):
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    ex = Execution.from_dict(data)
                except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
                    continue
                if origin is not None and ex.origin != origin:
                    continue
                results.append(ex)
                if limit is not None and len(results) >= limit:
                    return results
        return results

    def _publish_execution(self, execution: Execution) -> bool:
        dest = self._execution_path("pending", execution.execution_id)
        tmp_dir = self.queue_dir / "tmp"
        return link_publish_json(dest, execution.to_dict(), tmp_dir=tmp_dir)

    def enqueue(
        self,
        source: str,
        *,
        origin: str = "lt-transcribe",
        priority: int = 50,
        force: bool = False,
        retry_failed: bool = False,
        options: ExecutionOptions | None = None,
        required_host: str | None = None,
        transcript_checker: TranscriptChecker | None = None,
        max_attempts: int = 3,
    ) -> EnqueueResult:
        """Enqueue a source. Never calls yt-dlp or Whisper."""
        source_type, source_key, canonical = normalize_source(source)

        if transcript_checker is not None and not force:
            existing_tx = transcript_checker(source_key)
            if existing_tx is not None:
                return EnqueueResult(
                    kind="already_completed",
                    source_key=source_key,
                    transcript_path=Path(existing_tx),
                    message=f"Valid transcript already exists: {existing_tx}",
                )

        # Active executions for this source
        active = self.find_by_source_key(source_key, statuses=ACTIVE_STATUSES)
        if active and not force:
            ex, st = active[0]
            return EnqueueResult(
                kind="existing_active",
                source_key=source_key,
                execution=ex,
                reservation_generation=ex.generation,
                message=f"Active execution {ex.execution_id} in {st}/",
            )

        failed = self.find_by_source_key(source_key, statuses=("failed",))
        cancelled = self.find_by_source_key(source_key, statuses=("cancelled",))
        if (failed or cancelled) and not force and not retry_failed:
            return EnqueueResult(
                kind="requires_force",
                source_key=source_key,
                execution=(failed or cancelled)[0][0],
                message="Failed/cancelled source requires force=True or retry_failed=True",
            )

        exec_id = new_execution_id()
        reservation, created = create_reservation_if_absent(
            self.queue_dir,
            source_key=source_key,
            source_type=source_type,
            execution_id=exec_id,
            generation=1,
        )

        if force and not created:
            reservation = advance_generation(
                self.queue_dir, source_key, new_execution_id=exec_id
            )
        elif not created:
            # Reservation exists; reuse or attach new pending execution if none active
            if active:
                ex, st = active[0]
                return EnqueueResult(
                    kind="existing_active",
                    source_key=source_key,
                    execution=ex,
                    reservation_generation=reservation.generation,
                    message=f"Active execution {ex.execution_id} in {st}/",
                )
            # Point reservation at new execution without force if terminal only
            if failed or cancelled or retry_failed:
                if not force and not retry_failed:
                    return EnqueueResult(
                        kind="requires_force",
                        source_key=source_key,
                        message="Requires force or retry_failed",
                    )
                reservation = advance_generation(
                    self.queue_dir, source_key, new_execution_id=exec_id
                )
            else:
                # Fresh re-enqueue when reservation exists but no active (e.g. completed without transcript checker)
                reservation = update_current_execution(
                    self.queue_dir, source_key, execution_id=exec_id
                )
                # keep generation

        generation = reservation.generation
        # Ensure execution id matches reservation
        exec_id = reservation.current_execution_id
        # If we advanced, exec_id is new; if we only updated current, same

        # If reservation was just created, exec_id is set; publish that execution.
        # If force advanced, reservation has new id.
        if force or created or retry_failed or failed or cancelled:
            # re-read after possible advance
            reservation = read_reservation(self.queue_dir, source_key) or reservation
            exec_id = reservation.current_execution_id
            generation = reservation.generation

        # For existing reservation with no active and not force: need new execution id
        existing_same = self.find_execution(exec_id)[0]
        if existing_same is not None and existing_same.status in ACTIVE_STATUSES:
            return EnqueueResult(
                kind="existing_active",
                source_key=source_key,
                execution=existing_same,
                reservation_generation=generation,
            )

        if existing_same is not None and not force:
            # completed execution still current — create new id only if force/retry
            exec_id = new_execution_id()
            reservation = update_current_execution(
                self.queue_dir, source_key, execution_id=exec_id
            )
            generation = reservation.generation

        execution = Execution(
            execution_id=exec_id,
            source_key=source_key,
            generation=generation,
            source=canonical,
            source_type=source_type,
            origin=origin,
            priority=priority,
            status="pending",
            max_attempts=max_attempts,
            required_host=required_host,
            options=options or ExecutionOptions(),
        )

        published = self._publish_execution(execution)
        if not published:
            # Another writer published same execution id (unlikely) — load it
            loaded, st = self.find_execution(exec_id)
            return EnqueueResult(
                kind="existing_active" if st in ACTIVE_STATUSES else "enqueued",
                source_key=source_key,
                execution=loaded or execution,
                reservation_generation=generation,
                message="Execution id already present",
            )

        return EnqueueResult(
            kind="enqueued",
            source_key=source_key,
            execution=execution,
            reservation_generation=generation,
            message="Enqueued",
        )

    def cancel_pending(self, execution_id: str) -> Execution:
        """Cancel a pending execution (producer-safe: pending → cancelled only).

        Processing cancel requires the worker lock (not implemented here).
        Uses exclusive rename + atomic JSON write (no copy+unlink).
        """
        path = self._execution_path("pending", execution_id)
        if not path.is_file():
            raise QueueStoreError(
                f"Execution {execution_id} is not in pending/ "
                "(processing cancel requires worker lock)"
            )
        data = json.loads(path.read_text(encoding="utf-8"))
        execution = Execution.from_dict(data)
        execution.status = "cancelled"
        execution.updated_at = utc_now_iso()
        dest = self._execution_path("cancelled", execution_id)
        atomic_state_transition(path, dest, execution.to_dict())
        return execution

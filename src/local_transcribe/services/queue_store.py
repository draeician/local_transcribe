"""Durable queue store: enqueue, list, cancel pending (SPEC §5–7).

Producers only — does not run yt-dlp or Whisper.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Literal, Optional

from local_transcribe.services.atomic_files import (
    AtomicFileError,
    atomic_state_transition,
    link_publish_json,
)
from local_transcribe.services.queue_models import (
    ACTIVE_STATUSES,
    STATE_DIRS,
    Execution,
    ExecutionOptions,
    TranscriptChecker,
    new_execution_id,
    normalize_source,
    utc_now_iso,
)
from local_transcribe.services.source_reservations import (
    create_reservation_if_absent,
    read_reservation,
    try_advance_generation,
    try_update_current_execution,
)

logger = logging.getLogger(__name__)

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
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    return Execution.from_dict(data), status
                except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
                    continue
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

    def _cancel_orphan_pending(self, execution_id: str) -> None:
        """Best-effort cancel of a pending execution that lost a reservation race."""
        try:
            self.cancel_pending(execution_id)
        except QueueStoreError:
            pass
        except (AtomicFileError, OSError, json.JSONDecodeError, KeyError, TypeError):
            logger.warning("Failed to cancel orphan pending %s", execution_id)

    def _active_for_source(
        self, source_key: str
    ) -> list[tuple[Execution, str]]:
        return self.find_by_source_key(source_key, statuses=ACTIVE_STATUSES)

    def _preferred_active(
        self, source_key: str
    ) -> tuple[Execution, str] | None:
        """Return the active execution matching the reservation pointer.

        Without a reservation, returns None — concurrent producers must race
        via publish-then-claim rather than attaching to an arbitrary pending.
        """
        res = read_reservation(self.queue_dir, source_key)
        if res is None:
            return None
        for ex, st in self._active_for_source(source_key):
            if ex.execution_id == res.current_execution_id:
                return ex, st
        return None

    def repair_source(self, source_key: str) -> int:
        """Cancel pending executions that are not the reservation current pointer.

        Returns the number of orphan pending jobs cancelled. Safe for producers.
        If the reservation pointer is stale (no matching pending), do not mass-cancel
        the remaining pendings — that would leave a pointer with zero actives.
        """
        res = read_reservation(self.queue_dir, source_key)
        preferred = res.current_execution_id if res else None
        pendings = self.find_by_source_key(source_key, statuses=("pending",))
        if preferred is not None:
            preferred_pending = any(
                ex.execution_id == preferred for ex, _ in pendings
            )
            if not preferred_pending:
                return 0
        cancelled = 0
        for ex, st in pendings:
            if preferred is not None and ex.execution_id == preferred:
                continue
            try:
                self.cancel_pending(ex.execution_id)
                cancelled += 1
            except (QueueStoreError, FileNotFoundError, AtomicFileError):
                continue
        return cancelled

    def _attach_existing(
        self, source_key: str, *, message: str
    ) -> EnqueueResult:
        """Return the reservation's active execution after losing a race."""
        preferred = self._preferred_active(source_key)
        if preferred is not None:
            ex, st = preferred
            return EnqueueResult(
                kind="existing_active",
                source_key=source_key,
                execution=ex,
                reservation_generation=ex.generation,
                message=f"{message}; active in {st}/",
            )
        res = read_reservation(self.queue_dir, source_key)
        if res is not None:
            ex, st = self.find_execution(res.current_execution_id)
            if ex is not None and st in ACTIVE_STATUSES:
                return EnqueueResult(
                    kind="existing_active",
                    source_key=source_key,
                    execution=ex,
                    reservation_generation=res.generation,
                    message=message,
                )
            return EnqueueResult(
                kind="existing_active",
                source_key=source_key,
                execution=ex,
                reservation_generation=res.generation,
                message=f"{message}; reservation current={res.current_execution_id}",
            )
        return EnqueueResult(
            kind="existing_active",
            source_key=source_key,
            execution=None,
            message=message,
        )

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
        """Enqueue a source. Never calls yt-dlp or Whisper.

        Concurrent producers use publish-pending-first via ``link()``, then
        claim the reservation. Losers cancel orphan pendings.
        """
        source_type, source_key, canonical = normalize_source(source)
        opts = options or ExecutionOptions()

        if transcript_checker is not None and not force:
            existing_tx = transcript_checker(source_key)
            if existing_tx is not None:
                return EnqueueResult(
                    kind="already_completed",
                    source_key=source_key,
                    transcript_path=Path(existing_tx),
                    message=f"Valid transcript already exists: {existing_tx}",
                )

        preferred = self._preferred_active(source_key)
        if preferred is not None and not force:
            ex, st = preferred
            return EnqueueResult(
                kind="existing_active",
                source_key=source_key,
                execution=ex,
                reservation_generation=ex.generation,
                message=f"Active execution {ex.execution_id} in {st}/",
            )

        reservation = read_reservation(self.queue_dir, source_key)
        failed = self.find_by_source_key(source_key, statuses=("failed",))
        cancelled = self.find_by_source_key(source_key, statuses=("cancelled",))

        incomplete = False
        if reservation is not None:
            cur_ex, cur_st = self.find_execution(reservation.current_execution_id)
            incomplete = cur_ex is None and preferred is None
            if (
                cur_ex is not None
                and cur_st in ACTIVE_STATUSES
                and not force
            ):
                return EnqueueResult(
                    kind="existing_active",
                    source_key=source_key,
                    execution=cur_ex,
                    reservation_generation=reservation.generation,
                    message=f"Active execution {cur_ex.execution_id} in {cur_st}/",
                )

        # Terminal history blocks re-enqueue unless force/retry, except when
        # repairing an incomplete reservation pointer.
        if (
            (failed or cancelled)
            and not force
            and not retry_failed
            and not incomplete
        ):
            return EnqueueResult(
                kind="requires_force",
                source_key=source_key,
                execution=(failed or cancelled)[0][0],
                message="Failed/cancelled source requires force=True or retry_failed=True",
            )

        # --- Force / retry: new generation ---
        if force or retry_failed:
            if reservation is None:
                # No reservation yet — treat as first enqueue below.
                pass
            else:
                current_gen = reservation.generation
                new_id = new_execution_id()
                new_gen = current_gen + 1
                execution = Execution(
                    execution_id=new_id,
                    source_key=source_key,
                    generation=new_gen,
                    source=canonical,
                    source_type=source_type,
                    origin=origin,
                    priority=priority,
                    status="pending",
                    max_attempts=max_attempts,
                    required_host=required_host,
                    options=opts,
                )
                if not self._publish_execution(execution):
                    loaded, st = self.find_execution(new_id)
                    return EnqueueResult(
                        kind=(
                            "existing_active"
                            if st in ACTIVE_STATUSES
                            else "enqueued"
                        ),
                        source_key=source_key,
                        execution=loaded or execution,
                        reservation_generation=new_gen,
                        message="Force execution id collision",
                    )
                won = try_advance_generation(
                    self.queue_dir,
                    source_key,
                    expected_generation=current_gen,
                    new_execution_id=new_id,
                )
                if won is None:
                    # Only cancel if we do not currently own the pointer.
                    after = read_reservation(self.queue_dir, source_key)
                    if after is None or after.current_execution_id != new_id:
                        self._cancel_orphan_pending(new_id)
                    return self._attach_existing(
                        source_key, message="Lost force race"
                    )
                self.repair_source(source_key)
                alive, st = self.find_execution(new_id)
                if alive is None or st not in ACTIVE_STATUSES:
                    return self._attach_existing(
                        source_key, message="Force superseded after win"
                    )
                return EnqueueResult(
                    kind="enqueued",
                    source_key=source_key,
                    execution=execution,
                    reservation_generation=won.generation,
                    message="Enqueued (new generation)",
                )

        # --- Incomplete reservation: CAS-repair pointer (publish first) ---
        if incomplete and reservation is not None:
            current_id = reservation.current_execution_id
            current_gen = reservation.generation
            new_id = new_execution_id()
            execution = Execution(
                execution_id=new_id,
                source_key=source_key,
                generation=current_gen,
                source=canonical,
                source_type=source_type,
                origin=origin,
                priority=priority,
                status="pending",
                max_attempts=max_attempts,
                required_host=required_host,
                options=opts,
            )
            if not self._publish_execution(execution):
                loaded, st = self.find_execution(new_id)
                return EnqueueResult(
                    kind=(
                        "existing_active" if st in ACTIVE_STATUSES else "enqueued"
                    ),
                    source_key=source_key,
                    execution=loaded or execution,
                    reservation_generation=current_gen,
                )
            won = try_update_current_execution(
                self.queue_dir,
                source_key,
                expected_execution_id=current_id,
                new_execution_id=new_id,
                expected_generation=current_gen,
            )
            if won is None:
                self._cancel_orphan_pending(new_id)
                return self._attach_existing(
                    source_key, message="Lost incomplete-repair race"
                )
            self.repair_source(source_key)
            return EnqueueResult(
                kind="enqueued",
                source_key=source_key,
                execution=execution,
                reservation_generation=won.generation,
                message="Enqueued (repaired incomplete reservation)",
            )

        # --- First enqueue: publish pending, then claim reservation ---
        exec_id = new_execution_id()
        execution = Execution(
            execution_id=exec_id,
            source_key=source_key,
            generation=1,
            source=canonical,
            source_type=source_type,
            origin=origin,
            priority=priority,
            status="pending",
            max_attempts=max_attempts,
            required_host=required_host,
            options=opts,
        )
        if not self._publish_execution(execution):
            loaded, st = self.find_execution(exec_id)
            if loaded is not None:
                return EnqueueResult(
                    kind=(
                        "existing_active" if st in ACTIVE_STATUSES else "enqueued"
                    ),
                    source_key=source_key,
                    execution=loaded,
                    reservation_generation=1,
                    message="Execution id already present",
                )
            return EnqueueResult(
                kind="enqueued",
                source_key=source_key,
                execution=execution,
                reservation_generation=1,
                message="Pending publish raced",
            )

        claim, created = create_reservation_if_absent(
            self.queue_dir,
            source_key=source_key,
            source_type=source_type,
            execution_id=exec_id,
            generation=1,
        )
        if created:
            self.repair_source(source_key)
            return EnqueueResult(
                kind="enqueued",
                source_key=source_key,
                execution=execution,
                reservation_generation=1,
                message="Enqueued",
            )

        # Lost reservation race — cancel orphan and attach to winner.
        self._cancel_orphan_pending(exec_id)
        _ = claim  # existing reservation
        return self._attach_existing(source_key, message="Lost enqueue race")

    def cancel_pending(self, execution_id: str) -> Execution:
        """Cancel a pending execution (producer-safe: pending → cancelled only).

        Does not create a contradictory pending+cancelled pair for the same id.
        Processing cancel requires the worker lock (not implemented here).
        """
        path = self._execution_path("pending", execution_id)
        cancelled_path = self._execution_path("cancelled", execution_id)
        if cancelled_path.is_file() and not path.is_file():
            data = json.loads(cancelled_path.read_text(encoding="utf-8"))
            return Execution.from_dict(data)
        if not path.is_file():
            raise QueueStoreError(
                f"Execution {execution_id} is not in pending/ "
                "(processing cancel requires worker lock)"
            )
        data = json.loads(path.read_text(encoding="utf-8"))
        execution = Execution.from_dict(data)
        execution.status = "cancelled"
        execution.updated_at = utc_now_iso()
        dest = cancelled_path
        try:
            atomic_state_transition(path, dest, execution.to_dict())
        except (FileNotFoundError, AtomicFileError, FileExistsError):
            # Concurrent cancel won: pending gone and cancelled present.
            if cancelled_path.is_file() and not path.is_file():
                data = json.loads(cancelled_path.read_text(encoding="utf-8"))
                return Execution.from_dict(data)
            # Destination collision while pending still exists — fail closed.
            if cancelled_path.is_file() and path.is_file():
                raise
            if not path.is_file():
                raise QueueStoreError(
                    f"Execution {execution_id} disappeared during cancel"
                ) from None
            raise
        # Invariant: not still pending
        if path.is_file():
            raise QueueStoreError(
                f"Cancel left contradictory pending state for {execution_id}"
            )
        return execution

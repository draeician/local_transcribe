"""Background transcription worker (SPEC §9, §11)."""

from __future__ import annotations

import json
import logging
import os
import socket
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from local_transcribe.services.atomic_files import (
    AtomicFileError,
    atomic_state_transition,
    atomic_write_json,
)
from local_transcribe.services.config import load_config
from local_transcribe.services.download_admission import DownloadAdmission
from local_transcribe.services.legacy_import import (
    LegacyPendingWatchState,
    default_legacy_pending_path,
    maybe_import_legacy_pending,
)
from local_transcribe.services.model_cache import resolve_idle_maintenance
from local_transcribe.services.mount_validation import validate_queue_mount
from local_transcribe.services.queue_locks import (
    execution_lock_path,
    queue_lock,
)
from local_transcribe.services.queue_models import (
    SCHEMA_VERSION,
    STATE_DIRS,
    TERMINAL_STATUSES,
    Execution,
    ExecutionError,
    utc_now_iso,
)
from local_transcribe.services.queue_paths import (
    resolve_queue_dir,
    verify_queue_identity,
)
from local_transcribe.services.source_reservations import read_reservation
from local_transcribe.services.worker_errors import (
    available_at_elapsed,
    available_at_iso,
    classify_job_exception,
    retry_delay_seconds,
)
from local_transcribe.services.worker_lock import (
    WorkerAlreadyActive,
    WorkerLock,
    worker_lock_path,
)
from local_transcribe.services.worker_slots import (
    DEFAULT_LEASE_SECONDS,
    HeldLock,
    acquire_any_slot,
    claim_held_locally,
    release_all_claims,
    release_claim,
    take_claim_lock,
    try_take_abandoned_claim,
)
from local_transcribe.services.worker_state import (
    WorkerState,
    mark_worker_inactive,
    write_worker_state,
)

logger = logging.getLogger(__name__)

# Callable: (execution, scratch_dir) -> transcript payload dict or raises
JobRunner = Callable[[Execution, Path], dict[str, Any]]


def local_runtime_lock_path() -> Path:
    xdg = os.environ.get("XDG_RUNTIME_DIR")
    base = Path(xdg) if xdg else Path.home() / ".local" / "run"
    return base / "local-transcribe" / "worker.lock"


class LocalRuntimeLock:
    """Best-effort same-host lock (not cross-host authority)."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or local_runtime_lock_path()
        self._file = None

    def acquire(self) -> None:
        import fcntl

        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file = self.path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(self._file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self._file.close()
            self._file = None
            raise WorkerAlreadyActive(
                f"Local worker concurrency is full ({self.path}): {exc}"
            ) from exc
        self._file.seek(0)
        self._file.truncate()
        self._file.write(f"{os.getpid()}\n")
        self._file.flush()

    def release(self) -> None:
        import fcntl

        if self._file is not None:
            try:
                fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
            finally:
                self._file.close()
                self._file = None


def acquire_local_runtime_slot(base: Path, concurrency: int) -> LocalRuntimeLock:
    """Take one same-host slot. Index 0 keeps the historical lock path."""
    if concurrency < 1:
        raise ValueError(f"host concurrency must be >= 1, got {concurrency}")
    last: WorkerAlreadyActive | None = None
    for index in range(concurrency):
        path = base if index == 0 else base.with_name(f"{base.stem}-{index}{base.suffix}")
        candidate = LocalRuntimeLock(path)
        try:
            candidate.acquire()
        except WorkerAlreadyActive as exc:
            last = exc
            continue
        return candidate
    assert last is not None
    raise last


def _lease_expires_at(lease_seconds: float) -> str:
    deadline = datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)
    return deadline.astimezone().isoformat(timespec="seconds")


def _require_claim_token(queue_dir: Path, execution: Execution) -> None:
    """Fail closed when recovery has replaced this claim."""
    if not execution.claim_token:
        return
    path = queue_dir / "processing" / f"{execution.execution_id}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return
    if isinstance(data, dict) and data.get("claim_token") not in (
        None,
        execution.claim_token,
    ):
        raise AtomicFileError(
            f"Claim token mismatch for {execution.execution_id}; "
            "the job was recovered by another worker"
        )


def refresh_processing_lease(
    queue_dir: Path,
    execution: Execution,
    lease_seconds: float,
) -> None:
    """Push ``lease_expires_at`` forward on the processing record."""
    if not execution.claim_token:
        return
    path = queue_dir / "processing" / f"{execution.execution_id}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return
    if not isinstance(data, dict):
        return
    if data.get("claim_token") != execution.claim_token:
        return
    if data.get("status") != "processing":
        return
    data["lease_expires_at"] = _lease_expires_at(lease_seconds)
    data["updated_at"] = utc_now_iso()
    try:
        atomic_write_json(path, data)
    except OSError:
        return
    execution.lease_expires_at = data["lease_expires_at"]


@dataclass(frozen=True)
class _StateRecord:
    """One validated execution document in a state directory."""

    status: str
    path: Path
    execution: Execution


def _load_state_record(
    path: Path, status: str
) -> tuple[_StateRecord | None, str | None]:
    """Parse and validate one state document without trusting its filename."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        return None, f"unreadable JSON ({exc})"
    if not isinstance(data, dict):
        return None, "document is not a JSON object"
    try:
        execution = Execution.from_dict(data)
    except (KeyError, TypeError, ValueError) as exc:
        return None, f"invalid execution schema ({exc})"

    if execution.execution_id != path.stem:
        return None, (
            f"execution_id {execution.execution_id!r} does not match "
            f"filename {path.stem!r}"
        )
    if execution.status != status:
        return None, (
            f"embedded status {execution.status!r} does not match "
            f"{status}/ directory"
        )
    if execution.schema_version != SCHEMA_VERSION:
        return None, f"unsupported schema_version {execution.schema_version}"
    if execution.source_type not in ("youtube", "local_file"):
        return None, f"invalid source_type {execution.source_type!r}"
    if not execution.source_key.strip() or not execution.source.strip():
        return None, "missing source identity"
    if execution.generation < 1:
        return None, f"invalid generation {execution.generation}"
    if execution.max_attempts < 1:
        return None, f"invalid max_attempts {execution.max_attempts}"
    if execution.attempts < 0 or execution.attempts > execution.max_attempts:
        return None, (
            f"invalid attempts {execution.attempts}/{execution.max_attempts}"
        )
    return _StateRecord(status=status, path=path, execution=execution), None


def _valid_transition(source: _StateRecord, successor: _StateRecord) -> bool:
    """Return True when ``successor`` can validly follow ``source``.

    Attempt counts and transition metadata are validated, not just filenames:
    a claim increments attempts, a retry/terminal transition preserves the
    claimed attempt count, cancellation preserves the pre-claim count, and a
    retry promotion preserves the retry's attempt count.
    """
    a = source.execution
    b = successor.execution

    if a.status == "pending" and b.status == "processing":
        return b.attempts == a.attempts + 1 and b.started_at is not None
    if a.status == "pending" and b.status == "retry":
        return b.attempts == a.attempts + 1 and b.error is not None
    if a.status == "pending" and b.status == "completed":
        return b.attempts == a.attempts + 1
    if a.status == "pending" and b.status == "failed":
        return b.attempts == a.attempts + 1 and b.error is not None
    if a.status == "pending" and b.status == "cancelled":
        return b.attempts == a.attempts
    if a.status == "processing" and b.status in {
        "retry",
        "completed",
        "failed",
        "cancelled",
    }:
        if b.attempts != a.attempts:
            return False
        return not (b.status in {"retry", "failed"} and b.error is None)
    if a.status == "retry" and b.status == "pending":
        return b.attempts == a.attempts and available_at_elapsed(a.available_at)
    if a.status == "retry" and b.status in {"completed", "failed", "cancelled"}:
        if b.attempts != a.attempts:
            return False
        return not (b.status == "failed" and b.error is None)
    return False


def _find_recovery_winner(records: list[_StateRecord]) -> _StateRecord | None:
    """Return the unique most-advanced record, or None when ambiguous.

    A valid chain must have exactly one sink and every record must be able to
    reach it. Multiple sinks, cycles, or unreachable records are conflicting
    evidence and must be left intact rather than guessed at.
    """
    by_status = {record.status: record for record in records}
    if len(by_status) != len(records):
        return None

    edges: dict[str, set[str]] = {status: set() for status in by_status}
    for source in records:
        for successor in records:
            if source is successor:
                continue
            if _valid_transition(source, successor):
                edges[source.status].add(successor.status)

    visiting: set[str] = set()
    visited: set[str] = set()

    def has_cycle(status: str) -> bool:
        if status in visiting:
            return True
        if status in visited:
            return False
        visiting.add(status)
        for nxt in edges[status]:
            if has_cycle(nxt):
                return True
        visiting.remove(status)
        visited.add(status)
        return False

    if any(has_cycle(status) for status in edges):
        return None

    sinks = [status for status, outgoing in edges.items() if not outgoing]
    if len(sinks) != 1:
        return None
    sink = sinks[0]

    reverse: dict[str, set[str]] = {status: set() for status in edges}
    for status, outgoing in edges.items():
        for nxt in outgoing:
            reverse[nxt].add(status)

    seen: set[str] = set()
    stack = [sink]
    while stack:
        status = stack.pop()
        if status in seen:
            continue
        seen.add(status)
        stack.extend(reverse[status])
    if seen != set(edges):
        return None
    return by_status[sink]


def _state_paths(queue_dir: Path, execution_id: str) -> list[tuple[str, Path]]:
    return [
        (status, queue_dir / status / f"{execution_id}.json")
        for status in STATE_DIRS
        if (queue_dir / status / f"{execution_id}.json").is_file()
    ]


def _other_state_paths(
    queue_dir: Path, execution_id: str, *, exclude: str
) -> list[Path]:
    return [
        path
        for status, path in _state_paths(queue_dir, execution_id)
        if status != exclude
    ]


def _recover_duplicates(queue_dir: Path) -> set[str]:
    """Resolve interrupted-transition duplicates by validated transition order.

    Returns the execution ids that could not be resolved safely. Unresolved
    records are left exactly as found so a human/operator can inspect them.
    """
    ids: set[str] = set()
    for status in STATE_DIRS:
        directory = queue_dir / status
        if not directory.is_dir():
            continue
        for path in directory.glob("*.json"):
            ids.add(path.stem)

    unresolved: set[str] = set()
    for execution_id in sorted(ids):
        if len(_state_paths(queue_dir, execution_id)) <= 1:
            continue

        with queue_lock(execution_lock_path(queue_dir, execution_id)):
            paths = _state_paths(queue_dir, execution_id)
            if len(paths) <= 1:
                continue

            records: list[_StateRecord] = []
            for status, path in paths:
                record, error = _load_state_record(path, status)
                if record is None:
                    logger.warning(
                        "Duplicate recovery left execution %s intact: %s (%s)",
                        execution_id,
                        error,
                        path,
                    )
                    unresolved.add(execution_id)
                    records = []
                    break
                records.append(record)
            if not records:
                continue

            identities = {
                (
                    record.execution.source_key,
                    record.execution.generation,
                    record.execution.source_type,
                )
                for record in records
            }
            if len(identities) != 1:
                logger.warning(
                    "Duplicate recovery left execution %s intact: conflicting "
                    "source identity/generation across %s",
                    execution_id,
                    ", ".join(record.status for record in records),
                )
                unresolved.add(execution_id)
                continue

            winner = _find_recovery_winner(records)
            if winner is None:
                logger.warning(
                    "Duplicate recovery left execution %s intact: ambiguous "
                    "state copies %s",
                    execution_id,
                    ", ".join(
                        f"{record.status}(attempts={record.execution.attempts})"
                        for record in records
                    ),
                )
                unresolved.add(execution_id)
                continue

            for record in records:
                if record.path == winner.path:
                    continue
                try:
                    record.path.unlink()
                except OSError as exc:
                    logger.warning(
                        "Duplicate recovery could not remove stale %s copy %s "
                        "for execution %s: %s",
                        record.status,
                        record.path,
                        execution_id,
                        exc,
                    )
                    unresolved.add(execution_id)
            logger.info(
                "Duplicate recovery kept %s copy for execution %s",
                winner.status,
                execution_id,
            )
    return unresolved


def recover_processing(queue_dir: Path) -> int:
    """Move abandoned processing jobs to retry, or failed if attempts exhausted.

    SIGKILL/OOM never reaches ``fail_or_retry``, so recovery must honor
    ``max_attempts`` or the same job can death-loop forever.

    A processing job whose per-job NLM claim lock is still held is left alone:
    another live worker owns it. When that process dies, NLM releases the lock
    and the next recovery pass requeues the job. No manual cleanup.

    Interrupted transitions are reconciled only after validating the proposed
    successor (JSON/schema, execution id, source identity, generation, state,
    attempts). Malformed or conflicting copies remain intact and are reported.
    """
    unresolved = _recover_duplicates(queue_dir)
    processing = queue_dir / "processing"
    if not processing.is_dir():
        return 0

    count = 0
    for path in list(processing.glob("*.json")):
        execution_id = path.stem
        if execution_id in unresolved:
            continue
        if _other_state_paths(queue_dir, execution_id, exclude="processing"):
            continue
        if claim_held_locally(execution_id):
            continue
        recovery_lock = try_take_abandoned_claim(queue_dir, execution_id)
        if recovery_lock is None:
            logger.info(
                "Leaving processing job %s; its NLM claim lock is still held",
                execution_id,
            )
            continue

        try:
            if _recover_one_processing(queue_dir, path, execution_id):
                count += 1
        finally:
            recovery_lock.release()
    return count


def _recover_one_processing(queue_dir: Path, path: Path, execution_id: str) -> bool:
    """Move one abandoned processing file. Caller holds its claim lock."""
    with queue_lock(execution_lock_path(queue_dir, execution_id)):
        if not path.is_file():
            return False
        if _other_state_paths(queue_dir, execution_id, exclude="processing"):
            return False
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            ex = Execution.from_dict(data)
        except (
            OSError,
            json.JSONDecodeError,
            KeyError,
            TypeError,
            ValueError,
        ) as exc:
            logger.warning("Skipping unreadable processing file %s: %s", path, exc)
            return False

        can_retry = ex.attempts < ex.max_attempts
        if can_retry:
            status: str = "retry"
            dest = queue_dir / "retry" / path.name
            message = "Recovered abandoned processing job"
        else:
            status = "failed"
            dest = queue_dir / "failed" / path.name
            message = (
                "Recovered abandoned processing job; max attempts exceeded "
                f"({ex.attempts}/{ex.max_attempts})"
            )
        ex.error = ExecutionError(
            category="worker_interrupted",
            message=message,
            retryable=can_retry,
            occurred_at=utc_now_iso(),
        )
        ex.status = status  # type: ignore[assignment]
        ex.updated_at = utc_now_iso()
        if can_retry:
            ex.available_at = utc_now_iso()
        # The holder is gone. Drop the claim token so a later owner can set
        # its own, and clear the lease so doctor does not show a live worker.
        ex.claim_token = None
        ex.lease_expires_at = None
        try:
            atomic_state_transition(path, dest, ex.to_dict())
        except (AtomicFileError, OSError) as exc:
            logger.warning(
                "Failed to recover processing job %s: %s", path.name, exc
            )
            return False
        logger.info("Recovered processing job %s -> %s/", ex.execution_id, status)
        return True


def promote_retries(queue_dir: Path, *, now: float | None = None) -> int:
    """Move retry jobs with available_at <= now to pending.

    Uses parsed ISO timestamps (not string compare) so timezone/format
    differences do not strand jobs. Interrupted promotions are reconciled by
    the same validated duplicate recovery as claim/retry transitions.
    """
    unresolved = _recover_duplicates(queue_dir)
    retry_dir = queue_dir / "retry"
    if not retry_dir.is_dir():
        return 0

    current = time.time() if now is None else now
    count = 0
    for path in list(retry_dir.glob("*.json")):
        execution_id = path.stem
        if execution_id in unresolved:
            continue
        if _other_state_paths(queue_dir, execution_id, exclude="retry"):
            continue

        with queue_lock(execution_lock_path(queue_dir, execution_id)):
            if not path.is_file():
                continue
            if _other_state_paths(queue_dir, execution_id, exclude="retry"):
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                ex = Execution.from_dict(data)
            except (
                OSError,
                json.JSONDecodeError,
                KeyError,
                TypeError,
                ValueError,
            ):
                continue
            if not available_at_elapsed(ex.available_at, now=current):
                continue
            ex.status = "pending"  # type: ignore[assignment]
            ex.updated_at = utc_now_iso()
            dest = queue_dir / "pending" / path.name
            try:
                atomic_state_transition(path, dest, ex.to_dict())
                count += 1
            except (AtomicFileError, OSError):
                continue
    return count


def select_next_execution(
    queue_dir: Path,
    *,
    hostname: str | None = None,
    max_interactive_streak: int = 5,
    interactive_streak: int = 0,
) -> Execution | None:
    """Select next pending job by priority then created_at; fairness streak.

    A pending record is eligible only when it is the sole record for its
    execution id. If recovery left a conflicting retry, processing, or terminal
    counterpart intact, the pending JSON is not enough to make the execution
    claimable. Blocked executions are filtered before priority ordering so a
    blocked high-priority record cannot starve a healthy lower-priority job.
    """
    pending = queue_dir / "pending"
    if not pending.is_dir():
        return None
    jobs: list[Execution] = []
    for path in pending.glob("*.json"):
        record, _ = _load_state_record(path, "pending")
        if record is None:
            continue
        ex = record.execution
        if _other_state_paths(queue_dir, ex.execution_id, exclude="pending"):
            continue
        if ex.required_host and hostname and ex.required_host != hostname:
            continue
        jobs.append(ex)
    if not jobs:
        return None

    noninteractive = [j for j in jobs if j.priority < 100]

    def sort_key(j: Execution) -> tuple:
        return (-j.priority, j.created_at, j.execution_id)

    if interactive_streak >= max_interactive_streak and noninteractive:
        return min(noninteractive, key=sort_key)
    pool = jobs
    return min(pool, key=sort_key)


def claim_execution(
    queue_dir: Path,
    execution: Execution,
    *,
    hold_claim_lock: bool = False,
    worker_slot: int | None = None,
    lease_seconds: float = DEFAULT_LEASE_SECONDS,
) -> Execution | None:
    """Claim pending -> processing under the shared execution lock.

    Claim and producer cancellation serialize on the same lock, so exactly one
    can win. A claim never publishes processing after a cancellation has
    published cancelled, and a cancel never publishes cancelled after a claim
    has published processing.

    The selection snapshot is revalidated under the lock. A pending JSON that
    parses is not sufficient: if any other state record exists for this
    execution id (an unresolved retry/processing/terminal counterpart, including
    a malformed or conflicting copy that recovery left intact), the claim fails
    closed. A copy that appears between selection and claim is therefore still
    excluded.

    When ``hold_claim_lock`` is set, the per-job NLM lock is taken *before*
    the pending file is published into ``processing/`` and held until
    :func:`release_claim`. A second worker cannot take that lock, so it cannot
    claim or recover the job. The lock is released if the publish fails.
    ``O_EXCL`` is not used: NFSv3 does not make it atomic across clients.
    """
    src = queue_dir / "pending" / f"{execution.execution_id}.json"
    dest = queue_dir / "processing" / f"{execution.execution_id}.json"
    with queue_lock(execution_lock_path(queue_dir, execution.execution_id)):
        record, _ = _load_state_record(src, "pending")
        if record is None:
            return None
        current = record.execution
        if _other_state_paths(queue_dir, current.execution_id, exclude="pending"):
            return None
        if (
            current.execution_id != execution.execution_id
            or current.source_key != execution.source_key
            or current.generation != execution.generation
            or current.attempts != execution.attempts
        ):
            # The pending record changed after selection; never claim a stale
            # snapshot. The next selection pass reads the current record.
            return None

        host = socket.gethostname()
        pid = os.getpid()
        token = uuid.uuid4().hex if hold_claim_lock else None
        if hold_claim_lock:
            try:
                take_claim_lock(
                    queue_dir,
                    current.execution_id,
                    extra={
                        "hostname": host,
                        "pid": pid,
                        "slot": worker_slot,
                        "execution_id": current.execution_id,
                    },
                )
            except WorkerAlreadyActive:
                return None

        claimed = replace(
            current,
            status="processing",
            started_at=utc_now_iso(),
            updated_at=utc_now_iso(),
            attempts=current.attempts + 1,
            worker=f"{host}:{pid}",
            worker_host=host,
            worker_pid=pid,
            worker_slot=worker_slot,
            lease_expires_at=(
                _lease_expires_at(lease_seconds) if hold_claim_lock else None
            ),
            claim_token=token,
        )
        try:
            atomic_state_transition(src, dest, claimed.to_dict())
        except FileNotFoundError:
            if hold_claim_lock:
                release_claim(current.execution_id)
            return None
        except AtomicFileError:
            if hold_claim_lock:
                release_claim(current.execution_id)
            return None
        return claimed


def complete_execution(
    queue_dir: Path, execution: Execution, *, output_path: str | None = None
) -> None:
    _require_claim_token(queue_dir, execution)
    src = queue_dir / "processing" / f"{execution.execution_id}.json"
    dest = queue_dir / "completed" / f"{execution.execution_id}.json"
    completed = replace(
        execution,
        status="completed",
        completed_at=utc_now_iso(),
        updated_at=utc_now_iso(),
        output_path=output_path,
        error=None,
    )
    atomic_state_transition(src, dest, completed.to_dict())
    execution.status = completed.status
    execution.completed_at = completed.completed_at
    execution.updated_at = completed.updated_at
    execution.output_path = completed.output_path
    execution.error = None


def fail_or_retry(
    queue_dir: Path,
    execution: Execution,
    error: ExecutionError,
    *,
    available_at: str | None = None,
) -> None:
    """Move processing → retry or failed; set future ``available_at`` on retry."""
    _require_claim_token(queue_dir, execution)
    src = queue_dir / "processing" / f"{execution.execution_id}.json"
    if error.retryable and execution.attempts < execution.max_attempts:
        status: str = "retry"
        dest = queue_dir / "retry" / f"{execution.execution_id}.json"
        if available_at is None:
            delay = retry_delay_seconds(error.category, execution.attempts)
            available_at = available_at_iso(delay_seconds=delay)
        error = replace(error, next_retry_at=available_at)
    else:
        status = "failed"
        dest = queue_dir / "failed" / f"{execution.execution_id}.json"
        available_at = execution.available_at
    updated = replace(
        execution,
        status=status,  # type: ignore[arg-type]
        error=error,
        updated_at=utc_now_iso(),
        available_at=available_at or execution.available_at,
    )
    atomic_state_transition(src, dest, updated.to_dict())
    execution.status = updated.status
    execution.error = updated.error
    execution.updated_at = updated.updated_at
    execution.available_at = updated.available_at


def _execution_file_in_terminal_dir(queue_dir: Path, execution_id: str) -> bool:
    """True if ``execution_id`` JSON exists under a terminal state directory."""
    name = f"{execution_id}.json"
    for status in TERMINAL_STATUSES:
        if (queue_dir / status / name).is_file():
            return True
    return False


def run_worker(
    *,
    queue_dir: Path | None = None,
    once: bool = False,
    standby: bool = False,
    standby_retry_seconds: float = 30.0,
    validate_nfs: bool = True,
    require_statd: bool | None = None,
    job_runner: JobRunner | None = None,
    poll_interval_seconds: float = 5.0,
    max_jobs: int | None = None,
    transcripts_root: Path | None = None,
    until_execution_id: str | None = None,
    legacy_pending_file: Path | None = None,
    watch_legacy_pending: bool | None = None,
    max_workers: int | None = None,
    host_concurrency: int | None = None,
    lease_seconds: float | None = None,
    max_startup_waits: int | None = None,
) -> int:
    """Run the worker loop. Returns number of jobs processed.

    Production defaults are fail-closed: NFS validation and rpc.statd checks
    are on. ``validate_nfs=False`` is for unit tests / local fakes only — the
    CLI must not expose a casual disable flag. The worker never auto-inits a
    missing queue layout; ``queue.id`` must already exist.

    When ``until_execution_id`` is set, stop once that execution reaches a
    terminal state directory (foreground fallback from ``lt transcribe``).

    Startup takes three locks, in order:

    1. A same-host flock slot (``worker.concurrency``, default 1).
    2. A shared NLM lock on ``worker/worker.lock``. This blocks a pre-0.9
       worker, which needs that file exclusively, and fails while one is
       still running. Without ``standby`` the process exits. With
       ``standby`` it waits and does not claim jobs.
    3. One exclusive slot lock ``worker/slots/slot-N.lock`` for
       ``N < queue.max_workers`` (default 1). Extra workers stay in standby
       until a slot is released by process exit or NLM recovery.
    """
    if job_runner is None:
        from local_transcribe.services.job_runner import create_default_job_runner

        job_runner = create_default_job_runner(transcripts_root=transcripts_root)

    # Optional idle model maintenance (SPEC §20). A runner that owns a
    # worker-scoped model cache exposes ``maybe_unload_idle()``; arbitrary
    # injected callables are not required to, and are used as-is.
    idle_maintenance = resolve_idle_maintenance(job_runner)

    app_cfg = load_config()
    queue_cfg = app_cfg.queue
    worker_cfg = app_cfg.worker
    cap = queue_cfg.max_workers if max_workers is None else max_workers
    local_cap = worker_cfg.concurrency if host_concurrency is None else host_concurrency
    lease = worker_cfg.lease_seconds if lease_seconds is None else lease_seconds
    if cap < 1:
        raise ValueError(f"max_workers must be >= 1, got {cap}")
    if local_cap < 1:
        raise ValueError(f"host concurrency must be >= 1, got {local_cap}")
    if require_statd is None:
        require_statd = validate_nfs

    if queue_dir is None:
        resolved = resolve_queue_dir(verify_identity=True)
    else:
        resolved = Path(queue_dir).expanduser()
        # Never create layout/queue.id from the worker — init is operator-owned.
        # Unit tests pass validate_nfs=False with disposable queues; do not
        # enforce the operator's configured UUID against those paths.
        expected = queue_cfg.expected_uuid if validate_nfs else None
        verify_queue_identity(resolved, expected_uuid=expected)

    mount_result = validate_queue_mount(
        resolved,
        queue_config=queue_cfg,
        validate_nfs=validate_nfs,
        require_statd=require_statd,
    )
    if validate_nfs:
        mount_result.raise_if_failed()

    expected_uuid = queue_cfg.expected_uuid if validate_nfs else None
    queue_uuid = verify_queue_identity(resolved, expected_uuid=expected_uuid)
    # Keep unit-test workers off the live XDG runtime lock held by systemd.
    local_base = (
        local_runtime_lock_path()
        if validate_nfs
        else resolved / "worker" / "local-runtime.lock"
    )
    local_lock: LocalRuntimeLock | None = None
    compat: WorkerLock | None = None
    slot_held: HeldLock | None = None
    state: WorkerState | None = None
    startup_waits = 0

    def _wait_or_raise(exc: WorkerAlreadyActive, reason: str) -> None:
        nonlocal startup_waits
        if not standby:
            raise exc
        startup_waits += 1
        logger.info("%s; standby sleep %.0fs", reason, standby_retry_seconds)
        time.sleep(standby_retry_seconds)
        if max_startup_waits is not None and startup_waits >= max_startup_waits:
            raise exc

    try:
        while local_lock is None:
            try:
                local_lock = acquire_local_runtime_slot(local_base, local_cap)
            except WorkerAlreadyActive as exc:
                _wait_or_raise(
                    exc, f"local worker concurrency ({local_cap}) is full"
                )

        while compat is None:
            try:
                compat = WorkerLock.acquire_shared(worker_lock_path(resolved))
            except WorkerAlreadyActive as exc:
                _wait_or_raise(
                    exc, "pre-upgrade worker holds exclusive worker.lock"
                )

        while slot_held is None:
            slot_held = acquire_any_slot(resolved, cap)
            if slot_held is None:
                _wait_or_raise(
                    WorkerAlreadyActive(f"All {cap} worker slots are busy"),
                    f"all {cap} worker slots are busy",
                )

        assert slot_held is not None
        nlm = slot_held.lock
        state = WorkerState.create(queue_uuid, slot=slot_held.slot)
        write_worker_state(resolved, state)
        processed = 0
        interactive_streak = 0
        host = socket.gethostname()

        watch_pending = (
            queue_cfg.watch_legacy_pending
            if watch_legacy_pending is None
            else watch_legacy_pending
        )
        pending_path = legacy_pending_file
        if pending_path is None:
            pending_path = queue_cfg.legacy_pending_file
        # Default home pending path only in production (validate_nfs=True) so unit
        # tests with fake queues never touch the operator's real file.
        if pending_path is None and watch_pending and validate_nfs:
            pending_path = default_legacy_pending_path()
        pending_watch = LegacyPendingWatchState()

        def _import_legacy_pending(*, force: bool = False) -> None:
            if not watch_pending or pending_path is None:
                return
            maybe_import_legacy_pending(
                resolved,
                pending_path,
                state=pending_watch,
                force=force,
            )

        def _done_for_target() -> bool:
            return bool(
                until_execution_id
                and _execution_file_in_terminal_dir(resolved, until_execution_id)
            )

        def _run_idle_maintenance() -> None:
            """Give the job runner a chance to drop an idle cached model.

            Only ever called on the "nothing claimable" path, i.e. while this
            process holds no running job, so a transcription in flight can never
            lose its model. Failures must not stop the queue loop.
            """
            if idle_maintenance is None:
                return
            try:
                idle_maintenance()
            except Exception:
                logger.exception("Idle model maintenance failed; continuing worker loop")

        def _note_current_job(execution_id: str | None) -> None:
            assert slot_held is not None and state is not None
            state.current_execution_id = execution_id
            state.heartbeat_at = utc_now_iso()
            write_worker_state(resolved, state)
            try:
                slot_held.lock.write_metadata(
                    {
                        "slot": slot_held.slot,
                        "current_execution_id": execution_id,
                    }
                )
            except OSError:
                logger.exception("Failed to refresh slot metadata")

        recover_processing(resolved)
        _import_legacy_pending(force=True)
        while True:
            if _done_for_target():
                break
            _import_legacy_pending()
            # Pick up jobs whose owners died and dropped their NLM claim locks.
            recover_processing(resolved)
            promote_retries(resolved)
            job = select_next_execution(
                resolved, hostname=host, interactive_streak=interactive_streak
            )
            if job is None:
                if _done_for_target():
                    break
                if once or (max_jobs is not None and processed >= max_jobs):
                    break
                # Idle: reap the cached Whisper model once it has been unused
                # past the cache threshold instead of holding VRAM forever.
                _run_idle_maintenance()
                if until_execution_id is not None:
                    # Target still non-terminal but nothing claimable yet (e.g. retry
                    # delay). Keep polling briefly.
                    time.sleep(min(poll_interval_seconds, 0.5))
                    continue
                time.sleep(poll_interval_seconds)
                continue

            claimed = claim_execution(
                resolved,
                job,
                hold_claim_lock=True,
                worker_slot=slot_held.slot,
                lease_seconds=lease,
            )
            if claimed is None:
                continue

            stop_beat = threading.Event()
            beater: threading.Thread | None = None
            try:
                res = read_reservation(resolved, claimed.source_key)
                if res and res.current_execution_id != claimed.execution_id:
                    fail_or_retry(
                        resolved,
                        claimed,
                        ExecutionError(
                            category="internal_error",
                            message="Execution superseded by newer generation",
                            retryable=False,
                            occurred_at=utc_now_iso(),
                        ),
                    )
                else:
                    _note_current_job(claimed.execution_id)

                    def _beat(
                        stop_beat: threading.Event = stop_beat,
                        claimed: Execution = claimed,
                    ) -> None:
                        interval = min(30.0, max(1.0, float(lease) / 3.0))
                        while not stop_beat.wait(interval):
                            try:
                                refresh_processing_lease(resolved, claimed, lease)
                                _note_current_job(claimed.execution_id)
                            except Exception:
                                logger.exception("worker lease heartbeat failed")

                    beater = threading.Thread(
                        target=_beat,
                        name="lt-worker-lease",
                        daemon=True,
                    )
                    beater.start()

                    scratch = (
                        Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
                        / "local-transcribe"
                        / "jobs"
                        / claimed.execution_id
                    )
                    scratch.mkdir(parents=True, exist_ok=True)
                    admission = DownloadAdmission(resolved)
                    job_error: Exception | None = None
                    output_path: str | None = None
                    try:
                        # YouTube only: persist admission attempt before yt-dlp.
                        # Local files never consume download budget.
                        if claimed.source_type == "youtube":
                            admission.admit_and_record(worker_lock=nlm)
                        payload = job_runner(claimed, scratch)
                        out = payload.get("_output_path")
                        output_path = str(out) if out else None
                        if claimed.source_type == "youtube":
                            admission.clear_throttle_streak(worker_lock=nlm)
                    except Exception as exc:  # noqa: BLE001 - runner may raise anything
                        job_error = exc
                    finally:
                        # Stop the lease thread before the terminal rename so
                        # it cannot recreate processing/ after the move.
                        stop_beat.set()
                        beater.join(timeout=2)
                        beater = None
                    if job_error is None:
                        complete_execution(
                            resolved, claimed, output_path=output_path
                        )
                        if claimed.priority >= 100:
                            interactive_streak += 1
                        else:
                            interactive_streak = 0
                        processed += 1
                    else:
                        logger.error(
                            "Job %s failed", claimed.execution_id, exc_info=job_error
                        )
                        error = classify_job_exception(job_error)
                        avail: str | None = None
                        if claimed.source_type == "youtube":
                            if error.category == "rate_limited":
                                st = admission.record_429(worker_lock=nlm)
                                avail = st.blocked_until
                            elif error.category == "temporary_forbidden":
                                st = admission.record_403(
                                    worker_lock=nlm, temporary=True
                                )
                                avail = st.blocked_until
                        fail_or_retry(resolved, claimed, error, available_at=avail)
            finally:
                stop_beat.set()
                if beater is not None:
                    beater.join(timeout=2)
                release_claim(claimed.execution_id)
                if state is not None and state.current_execution_id:
                    _note_current_job(None)

            if _done_for_target():
                break
            if once or (max_jobs is not None and processed >= max_jobs):
                break
    finally:
        release_all_claims()
        if state is not None:
            mark_worker_inactive(resolved, state)
        if slot_held is not None:
            slot_held.release()
        if compat is not None:
            compat.release()
        if local_lock is not None:
            local_lock.release()

    return processed

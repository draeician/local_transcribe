"""NFSv3 worker slots and per-job claim locks.

Cross-host exclusion is ``fcntl.lockf`` (NLM on NFSv3), never ``O_EXCL``.
POSIX record locks are per-process, so each held lock also owns a
``threading.Lock`` for threads inside this process. Lock files are created
on demand and never unlinked: unlinking an inode that a waiter already
opened would split the lock identity.
"""

from __future__ import annotations

import threading
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from local_transcribe.services.worker_lock import (
    WorkerAlreadyActive,
    WorkerLock,
    probe_legacy_lock,
    read_lock_metadata,
    worker_lock_path,
)

DEFAULT_MAX_WORKERS = 1
DEFAULT_HOST_CONCURRENCY = 1
DEFAULT_LEASE_SECONDS = 90.0
MAX_WORKER_CAP = 64

_THREAD_LOCKS: dict[str, threading.Lock] = {}
_THREAD_LOCKS_GUARD = threading.Lock()
_HELD_CLAIMS: dict[str, HeldLock] = {}
_HELD_SLOTS: set[int] = set()
_HELD_GUARD = threading.Lock()


@dataclass
class HeldLock:
    """One exclusive NLM lock plus the in-process thread lock that guards it."""

    lock: WorkerLock
    thread_lock: threading.Lock
    slot: int | None = None

    def release(self) -> None:
        try:
            self.lock.release()
        finally:
            if self.slot is not None:
                with _HELD_GUARD:
                    _HELD_SLOTS.discard(self.slot)
            self.thread_lock.release()


def slot_lock_path(queue_dir: Path, slot: int) -> Path:
    return Path(queue_dir) / "worker" / "slots" / f"slot-{int(slot)}.lock"


def claim_lock_path(queue_dir: Path, execution_id: str) -> Path:
    safe = execution_id.replace("/", "_").replace("\\", "_")
    return Path(queue_dir) / "worker" / "claims" / f"{safe}.lock"


def _thread_lock(path: Path) -> threading.Lock:
    key = str(Path(path).resolve(strict=False))
    with _THREAD_LOCKS_GUARD:
        lock = _THREAD_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _THREAD_LOCKS[key] = lock
        return lock


def _acquire_exclusive(
    path: Path,
    *,
    write_metadata: bool,
    extra: Mapping[str, Any] | None = None,
    slot: int | None = None,
) -> HeldLock:
    thread_lock = _thread_lock(path)
    if not thread_lock.acquire(blocking=False):
        raise WorkerAlreadyActive(f"Lock already held in this process: {path}")
    try:
        lock = WorkerLock.acquire(
            path,
            shared=False,
            write_metadata=write_metadata,
            extra=extra,
        )
    except Exception:
        thread_lock.release()
        raise
    if slot is not None:
        with _HELD_GUARD:
            _HELD_SLOTS.add(slot)
    return HeldLock(lock=lock, thread_lock=thread_lock, slot=slot)


def try_acquire_slot(
    queue_dir: Path,
    slot: int,
    *,
    extra: Mapping[str, Any] | None = None,
) -> HeldLock | None:
    """Take ``slot`` if it is free. ``None`` when another worker holds it."""
    path = slot_lock_path(queue_dir, slot)
    payload: dict[str, Any] = {"slot": slot, "current_execution_id": None}
    if extra:
        payload.update(dict(extra))
    try:
        return _acquire_exclusive(path, write_metadata=True, extra=payload, slot=slot)
    except WorkerAlreadyActive:
        return None


def acquire_any_slot(
    queue_dir: Path,
    max_workers: int,
    *,
    extra: Mapping[str, Any] | None = None,
) -> HeldLock | None:
    """Take the lowest free slot in ``0 .. max_workers-1``, or ``None``."""
    cap = int(max_workers)
    if cap < 1:
        raise ValueError(f"max_workers must be >= 1, got {max_workers}")
    for slot in range(cap):
        held = try_acquire_slot(queue_dir, slot, extra=extra)
        if held is not None:
            return held
    return None


def take_claim_lock(
    queue_dir: Path,
    execution_id: str,
    *,
    extra: Mapping[str, Any] | None = None,
) -> None:
    """Hold the per-job NLM lock until :func:`release_claim`.

    Raises:
        WorkerAlreadyActive: another process or thread already holds it.
    """
    with _HELD_GUARD:
        if execution_id in _HELD_CLAIMS:
            raise WorkerAlreadyActive(
                f"Claim lock already held for {execution_id}"
            )
    held = _acquire_exclusive(
        claim_lock_path(queue_dir, execution_id),
        write_metadata=True,
        extra=extra,
    )
    with _HELD_GUARD:
        _HELD_CLAIMS[execution_id] = held


def release_claim(execution_id: str) -> None:
    """Release a claim lock held by this process. Idempotent."""
    with _HELD_GUARD:
        held = _HELD_CLAIMS.pop(execution_id, None)
    if held is not None:
        held.release()


def release_all_claims() -> None:
    with _HELD_GUARD:
        items = list(_HELD_CLAIMS.items())
        _HELD_CLAIMS.clear()
    for _execution_id, held in items:
        held.release()


def claim_held_locally(execution_id: str) -> bool:
    with _HELD_GUARD:
        return execution_id in _HELD_CLAIMS


def try_take_abandoned_claim(queue_dir: Path, execution_id: str) -> HeldLock | None:
    """Lock a claim whose owner is gone.

    Returns ``None`` when this process already holds the claim or another
    live process holds the NLM lock. The caller must ``release()`` the
    returned lock after it finishes the recovery transition.
    """
    if claim_held_locally(execution_id):
        return None
    path = claim_lock_path(queue_dir, execution_id)
    thread_lock = _thread_lock(path)
    if not thread_lock.acquire(blocking=False):
        return None
    try:
        lock = WorkerLock.acquire(path, write_metadata=False)
    except WorkerAlreadyActive:
        thread_lock.release()
        return None
    except Exception:
        thread_lock.release()
        raise
    return HeldLock(lock=lock, thread_lock=thread_lock)


@dataclass(frozen=True)
class ActiveWorker:
    """One live worker, as proven by a held NLM lock plus advisory metadata."""

    slot: int | None
    hostname: str | None
    pid: int | None
    current_execution_id: str | None
    kind: str


@dataclass(frozen=True)
class WorkerInspection:
    max_workers: int
    legacy_lock: str
    active: tuple[ActiveWorker, ...]

    @property
    def active_count(self) -> int:
        return len(self.active)


def _meta_str(meta: Mapping[str, Any], key: str) -> str | None:
    value = meta.get(key)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _meta_pid(meta: Mapping[str, Any]) -> int | None:
    value = meta.get("pid")
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _slot_busy(queue_dir: Path, slot: int) -> bool:
    if slot in _HELD_SLOTS:
        return True
    path = slot_lock_path(queue_dir, slot)
    thread_lock = _thread_lock(path)
    if not thread_lock.acquire(blocking=False):
        return True
    try:
        try:
            lock = WorkerLock.acquire(path, write_metadata=False)
        except WorkerAlreadyActive:
            return True
        except OSError:
            return False
        else:
            lock.release()
            return False
    finally:
        thread_lock.release()


def _processing_owner_index(queue_dir: Path) -> dict[int, str]:
    """Map ``worker_slot`` → execution id for processing JSON, best effort."""
    processing = Path(queue_dir) / "processing"
    owners: dict[int, str] = {}
    if not processing.is_dir():
        return owners
    import json

    for path in processing.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        slot = data.get("worker_slot")
        if isinstance(slot, bool) or slot is None:
            continue
        try:
            owners[int(slot)] = str(data.get("execution_id") or path.stem)
        except (TypeError, ValueError):
            continue
    return owners


def inspect_workers(queue_dir: Path, max_workers: int) -> WorkerInspection:
    """Report the cap and lock-backed active workers.

    A slot counts as active only while its exclusive NLM lock is held.
    Stale JSON left by a crashed worker is ignored once the lock is free.
    """
    queue_dir = Path(queue_dir)
    cap = int(max_workers)
    legacy = probe_legacy_lock(worker_lock_path(queue_dir))
    if legacy == "exclusive":
        meta = read_lock_metadata(worker_lock_path(queue_dir))
        owners = _processing_owner_index(queue_dir)
        current = next(iter(owners.values()), None) if len(owners) == 1 else None
        if current is None and owners:
            current = next(iter(owners.values()))
        active = (
            ActiveWorker(
                slot=None,
                hostname=_meta_str(meta, "hostname"),
                pid=_meta_pid(meta),
                current_execution_id=_meta_str(meta, "current_execution_id") or current,
                kind="pre-0.9",
            ),
        )
        return WorkerInspection(max_workers=cap, legacy_lock=legacy, active=active)

    owners = _processing_owner_index(queue_dir)
    active_list: list[ActiveWorker] = []
    for slot in range(cap):
        if not _slot_busy(queue_dir, slot):
            continue
        meta = read_lock_metadata(slot_lock_path(queue_dir, slot))
        current = _meta_str(meta, "current_execution_id") or owners.get(slot)
        active_list.append(
            ActiveWorker(
                slot=slot,
                hostname=_meta_str(meta, "hostname"),
                pid=_meta_pid(meta),
                current_execution_id=current,
                kind="slot",
            )
        )
    return WorkerInspection(
        max_workers=cap,
        legacy_lock=legacy,
        active=tuple(active_list),
    )


def format_worker_lines(report: WorkerInspection) -> list[str]:
    """Plain-text lines for ``lt queue stats`` / ``lt status`` / doctor."""
    legacy = report.legacy_lock
    if legacy == "exclusive":
        legacy_text = "exclusive (pre-0.9 worker; new workers stay standby)"
    elif legacy == "shared":
        legacy_text = "shared (0.9 workers)"
    elif legacy == "free":
        legacy_text = "free"
    else:
        legacy_text = legacy
    lines = [
        f"Worker cap: {report.max_workers}",
        f"Legacy worker.lock: {legacy_text}",
    ]
    if not report.active:
        lines.append("Active workers: none")
        return lines
    if report.legacy_lock == "exclusive":
        lines.append("Active workers: 1 (pre-0.9 global lock)")
    else:
        lines.append(f"Active workers: {report.active_count}/{report.max_workers}")
    for worker in report.active:
        slot = "-" if worker.slot is None else str(worker.slot)
        host = worker.hostname or "?"
        pid = "?" if worker.pid is None else str(worker.pid)
        job = worker.current_execution_id or "-"
        lines.append(f"  slot {slot} host={host} pid={pid} job={job}")
    return lines

"""Advisory locks for queue authority writes.

Reservation updates and execution claim/cancel transitions must serialize
read-modify-write sequences without adding another durable authority. The lock
files under ``.locks/`` are ephemeral coordination only: they are never read as
queue state, never contain metadata, and are never removed while a process may
still open them.

``fcntl.lockf`` provides the cross-process (and, on NFSv3, NLM-backed) lock.
The in-process thread lock is required because POSIX record locks are owned by
the process, not by individual threads.
"""

from __future__ import annotations

import fcntl
import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from local_transcribe.services.queue_models import source_key_filename

_THREAD_LOCKS: dict[str, threading.Lock] = {}
_THREAD_LOCKS_GUARD = threading.Lock()


def execution_lock_path(queue_dir: Path, execution_id: str) -> Path:
    """Return the advisory lock path for one execution transition."""
    return Path(queue_dir) / ".locks" / "executions" / f"{execution_id}.lock"


def reservation_lock_path(queue_dir: Path, source_key: str) -> Path:
    """Return the advisory lock path for one source reservation update."""
    return (
        Path(queue_dir)
        / ".locks"
        / "reservations"
        / f"{source_key_filename(source_key)}.lock"
    )


def _thread_lock(path: Path) -> threading.Lock:
    key = str(path.resolve(strict=False))
    with _THREAD_LOCKS_GUARD:
        lock = _THREAD_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _THREAD_LOCKS[key] = lock
        return lock


@contextmanager
def queue_lock(path: Path) -> Iterator[None]:
    """Hold an exclusive advisory lock for ``path``.

    The lock file is created on demand and intentionally left in place. Removing
    it after release would allow a waiter that already opened the old inode to
    proceed while a new process creates a different inode, breaking exclusion.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    thread_lock = _thread_lock(path)
    with thread_lock:
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.lockf(fd, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.lockf(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

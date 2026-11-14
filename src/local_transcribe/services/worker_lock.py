"""NLM-backed POSIX worker lock (SPEC §9.5).

``WorkerLock.acquire`` takes a nonblocking exclusive lock and is the pre-0.9
global ownership primitive on ``worker/worker.lock``. 0.9 workers instead hold
a shared lock on that same file (so a still-running exclusive holder blocks
them, and they block an old exclusive acquire) plus an exclusive slot lock.
"""

from __future__ import annotations

import errno
import fcntl
import json
import os
import socket
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import IO, TYPE_CHECKING, Any

if TYPE_CHECKING:
    from typing_extensions import Self


class WorkerLockError(RuntimeError):
    pass


class WorkerAlreadyActive(WorkerLockError):
    pass


@dataclass
class WorkerLock:
    path: Path
    file: IO[str]
    shared: bool = False

    @classmethod
    def acquire(
        cls,
        path: Path,
        *,
        shared: bool = False,
        write_metadata: bool = True,
        extra: Mapping[str, Any] | None = None,
    ) -> WorkerLock:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        file = path.open("a+", encoding="utf-8")
        try:
            mode = fcntl.LOCK_SH if shared else fcntl.LOCK_EX
            fcntl.lockf(file.fileno(), mode | fcntl.LOCK_NB)
        except OSError as exc:
            file.close()
            if exc.errno in (errno.EACCES, errno.EAGAIN):
                if shared:
                    raise WorkerAlreadyActive(
                        "Pre-upgrade worker holds worker/worker.lock exclusively. "
                        "Stop that worker, upgrade, then start the new worker."
                    ) from exc
                raise WorkerAlreadyActive(
                    "Another worker currently owns the queue lock"
                ) from exc
            raise WorkerLockError(f"Unable to obtain NFS worker lock: {exc}") from exc

        lock = cls(path=path, file=file, shared=shared)
        # Shared holders must not rewrite the file: the lock is advisory and
        # several processes may hold LOCK_SH at once.
        if write_metadata and not shared:
            lock.write_metadata(extra)
        return lock

    @classmethod
    def acquire_shared(cls, path: Path) -> WorkerLock:
        """Hold a nonblocking shared lock and do not rewrite the file.

        Multiple 0.9 workers may hold this together. It fails while any
        process holds the pre-0.9 exclusive lock on the same inode.
        """
        return cls.acquire(path, shared=True, write_metadata=False)

    def write_metadata(self, extra: Mapping[str, Any] | None = None) -> None:
        """Rewrite advisory metadata. Exclusive holders only; never unlocks."""
        if self.shared or self.file.closed:
            return
        payload: dict[str, Any] = {
            "hostname": socket.gethostname(),
            "pid": os.getpid(),
            "acquired_at": datetime.now(timezone.utc).isoformat(),
        }
        if extra:
            payload.update(dict(extra))
        self.file.seek(0)
        self.file.truncate()
        json.dump(payload, self.file, indent=2)
        self.file.write("\n")
        self.file.flush()
        os.fsync(self.file.fileno())

    def release(self) -> None:
        try:
            fcntl.lockf(self.file.fileno(), fcntl.LOCK_UN)
        finally:
            self.file.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        self.release()

    @property
    def fileno(self) -> int:
        return self.file.fileno()


def worker_lock_path(queue_dir: Path) -> Path:
    return Path(queue_dir) / "worker" / "worker.lock"


def read_lock_metadata(path: Path) -> dict[str, Any]:
    """Best-effort read of advisory lock-file JSON. Empty dict if unreadable."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return {}
    if not text.strip():
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    return data


def probe_legacy_lock(path: Path) -> str:
    """Classify ``worker.lock`` without keeping the lock.

    Returns ``free`` (no holders), ``shared`` (0.9 workers), ``exclusive``
    (pre-0.9 worker), or ``unavailable`` (NLM/OS error).

    Must not be called from a process that already holds this file's lock:
    POSIX record locks are per-process, so a second ``lockf`` would replace
    the caller's own lock.
    """
    try:
        lock = WorkerLock.acquire(path, write_metadata=False)
    except WorkerAlreadyActive:
        pass
    except OSError:
        return "unavailable"
    else:
        lock.release()
        return "free"

    try:
        lock = WorkerLock.acquire_shared(path)
    except WorkerAlreadyActive:
        return "exclusive"
    except OSError:
        return "unavailable"
    else:
        lock.release()
        return "shared"

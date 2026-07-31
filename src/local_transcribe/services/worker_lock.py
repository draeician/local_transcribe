"""NLM-backed POSIX worker lock (SPEC §9.5)."""

from __future__ import annotations

import errno
import fcntl
import json
import os
import socket
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import IO, Optional


class WorkerLockError(RuntimeError):
    pass


class WorkerAlreadyActive(WorkerLockError):
    pass


@dataclass
class WorkerLock:
    path: Path
    file: IO[str]

    @classmethod
    def acquire(cls, path: Path) -> "WorkerLock":
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        file = path.open("a+", encoding="utf-8")
        try:
            fcntl.lockf(file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            file.close()
            if exc.errno in (errno.EACCES, errno.EAGAIN):
                raise WorkerAlreadyActive(
                    "Another worker currently owns the queue lock"
                ) from exc
            raise WorkerLockError(f"Unable to obtain NFS worker lock: {exc}") from exc

        metadata = {
            "hostname": socket.gethostname(),
            "pid": os.getpid(),
            "acquired_at": datetime.now(timezone.utc).isoformat(),
        }
        file.seek(0)
        file.truncate()
        json.dump(metadata, file, indent=2)
        file.write("\n")
        file.flush()
        os.fsync(file.fileno())
        return cls(path=path, file=file)

    def release(self) -> None:
        try:
            fcntl.lockf(self.file.fileno(), fcntl.LOCK_UN)
        finally:
            self.file.close()

    def __enter__(self) -> "WorkerLock":
        return self

    def __exit__(self, *args: object) -> None:
        self.release()

    @property
    def fileno(self) -> int:
        return self.file.fileno()


def worker_lock_path(queue_dir: Path) -> Path:
    return Path(queue_dir) / "worker" / "worker.lock"

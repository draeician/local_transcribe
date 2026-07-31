"""Tests for NLM POSIX worker lock (task 016)."""

from __future__ import annotations

import multiprocessing
from pathlib import Path

import pytest

from local_transcribe.services.worker_lock import (
    WorkerAlreadyActive,
    WorkerLock,
    worker_lock_path,
)


def _hold_lock(path_str: str, ready: multiprocessing.synchronize.Event, done: multiprocessing.synchronize.Event) -> None:
    lock = WorkerLock.acquire(Path(path_str))
    ready.set()
    done.wait(timeout=10)
    lock.release()


def test_second_acquire_fails_while_held(tmp_path: Path) -> None:
    path = worker_lock_path(tmp_path)
    ready = multiprocessing.Event()
    done = multiprocessing.Event()
    proc = multiprocessing.Process(target=_hold_lock, args=(str(path), ready, done))
    proc.start()
    assert ready.wait(timeout=5)
    with pytest.raises(WorkerAlreadyActive):
        WorkerLock.acquire(path)
    done.set()
    proc.join(timeout=5)
    # After release, acquire succeeds
    lock = WorkerLock.acquire(path)
    lock.release()


def test_context_manager(tmp_path: Path) -> None:
    path = worker_lock_path(tmp_path)
    with WorkerLock.acquire(path) as lock:
        assert lock.path == path
        assert lock.fileno >= 0
    # After release, re-acquire works
    lock2 = WorkerLock.acquire(path)
    lock2.release()

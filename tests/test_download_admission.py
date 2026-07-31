"""Tests for download admission controller (task 021 / 033)."""

from __future__ import annotations

from pathlib import Path

import pytest

from local_transcribe.services.download_admission import (
    DownloadAdmission,
    RateLimitConfig,
    load_state,
)
from local_transcribe.services.queue_paths import initialize_queue_layout
from local_transcribe.services.worker_lock import WorkerLock, worker_lock_path


def _lock(queue: Path) -> WorkerLock:
    return WorkerLock.acquire(worker_lock_path(queue))


def test_admit_records_before_return(tmp_path: Path) -> None:
    queue = tmp_path / "q"
    initialize_queue_layout(queue)
    sleeps: list[float] = []
    clock = {"t": 1_000_000.0}

    def time_fn() -> float:
        return clock["t"]

    def sleep_fn(s: float) -> None:
        sleeps.append(s)
        clock["t"] += s

    adm = DownloadAdmission(
        queue,
        RateLimitConfig(minimum_download_interval_seconds=10),
        sleep_fn=sleep_fn,
        time_fn=time_fn,
    )
    lock = _lock(queue)
    try:
        s1 = adm.admit_and_record(worker_lock=lock)
        assert s1.download_attempts_this_hour == 1
        assert load_state(queue).download_attempts_this_hour == 1

        s2 = adm.admit_and_record(worker_lock=lock)
        assert s2.download_attempts_this_hour == 2
        assert sleeps  # waited for interval
    finally:
        lock.release()


def test_blocked_until(tmp_path: Path) -> None:
    queue = tmp_path / "q"
    initialize_queue_layout(queue)
    clock = {"t": 1_000_000.0}
    adm = DownloadAdmission(
        queue,
        sleep_fn=lambda s: clock.__setitem__("t", clock["t"] + s),
        time_fn=lambda: clock["t"],
    )
    lock = _lock(queue)
    try:
        adm.admit_and_record(worker_lock=lock)
        adm.record_429(worker_lock=lock, retry_after_seconds=60)
        wait = adm.seconds_until_admitted()
        assert wait > 0
    finally:
        lock.release()


def test_admit_requires_worker_lock_object(tmp_path: Path) -> None:
    queue = tmp_path / "q"
    initialize_queue_layout(queue)
    adm = DownloadAdmission(queue)
    with pytest.raises(RuntimeError, match="WorkerLock"):
        adm.admit_and_record(worker_lock=None)  # type: ignore[arg-type]


def test_record_403_temporary_sets_blocked_until(tmp_path: Path) -> None:
    queue = tmp_path / "q"
    initialize_queue_layout(queue)
    clock = {"t": 2_000_000.0}
    adm = DownloadAdmission(queue, time_fn=lambda: clock["t"], sleep_fn=lambda _s: None)
    lock = _lock(queue)
    try:
        st = adm.record_403(worker_lock=lock, temporary=True, backoff_seconds=90)
        assert st.total_403_errors == 1
        assert st.blocked_until is not None
        assert adm.seconds_until_admitted() > 0
    finally:
        lock.release()

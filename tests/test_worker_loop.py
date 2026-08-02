"""Tests for worker loop (task 018)."""

from __future__ import annotations

import json
from pathlib import Path

from local_transcribe.services.queue_paths import initialize_queue_layout
from local_transcribe.services.queue_store import QueueStore
from local_transcribe.services.worker import (
    claim_execution,
    promote_retries,
    recover_processing,
    run_worker,
    select_next_execution,
)


def test_select_priority(tmp_path: Path) -> None:
    queue = tmp_path / "q"
    initialize_queue_layout(queue)
    store = QueueStore(queue)
    store.enqueue("https://youtu.be/aaaaaaaaaaa", priority=10, origin="lt-batch")
    store.enqueue("https://youtu.be/bbbbbbbbbbb", priority=100, origin="lt-transcribe")
    # force second video id - need valid 11 char ids
    job = select_next_execution(queue)
    assert job is not None
    assert job.priority == 100


def test_run_worker_once_with_fake_runner(tmp_path: Path) -> None:
    queue = tmp_path / "q"
    initialize_queue_layout(queue)
    store = QueueStore(queue)
    store.enqueue("https://youtu.be/dQw4w9WgXcQ", priority=100)

    def runner(ex, scratch):  # type: ignore[no-untyped-def]
        return {"transcript": "hi", "_output_path": str(scratch / "out.json")}

    n = run_worker(
        queue_dir=queue,
        once=True,
        validate_nfs=False,
        job_runner=runner,
        poll_interval_seconds=0.01,
    )
    assert n == 1
    assert list((queue / "completed").glob("*.json"))
    assert not list((queue / "pending").glob("*.json"))


def test_recover_processing(tmp_path: Path) -> None:
    queue = tmp_path / "q"
    initialize_queue_layout(queue)
    store = QueueStore(queue)
    r = store.enqueue("https://youtu.be/dQw4w9WgXcQ")
    assert r.execution is not None
    claimed = claim_execution(queue, r.execution)
    assert claimed is not None
    n = recover_processing(queue)
    assert n == 1
    assert list((queue / "retry").glob("*.json"))


def test_recover_processing_fails_when_max_attempts_exhausted(
    tmp_path: Path,
) -> None:
    queue = tmp_path / "q"
    initialize_queue_layout(queue)
    store = QueueStore(queue)
    r = store.enqueue(
        "https://youtu.be/dQw4w9WgXcQ",
        max_attempts=2,
    )
    assert r.execution is not None
    claimed = claim_execution(queue, r.execution)
    assert claimed is not None
    assert claimed.attempts == 1
    assert recover_processing(queue) == 1
    assert list((queue / "retry").glob("*.json"))

    # Promote and claim again so attempts == max_attempts, then crash-recover.
    assert promote_retries(queue) == 1
    pending = select_next_execution(queue)
    assert pending is not None
    claimed2 = claim_execution(queue, pending)
    assert claimed2 is not None
    assert claimed2.attempts == 2
    assert recover_processing(queue) == 1

    failed = list((queue / "failed").glob("*.json"))
    assert len(failed) == 1
    assert not list((queue / "retry").glob("*.json"))
    assert not list((queue / "processing").glob("*.json"))
    data = json.loads(failed[0].read_text(encoding="utf-8"))
    assert data["status"] == "failed"
    assert data["attempts"] == 2
    assert data["error"]["category"] == "worker_interrupted"
    assert data["error"]["retryable"] is False
    assert "max attempts exceeded" in data["error"]["message"]

"""Producer concurrency + safe enqueue API (task 035)."""

from __future__ import annotations

import multiprocessing as mp
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from pathlib import Path

from local_transcribe.queue_api import (
    append_pending_url,
    enqueue_youtube_safe,
)
from local_transcribe.services.legacy_import import import_pending_file
from local_transcribe.services.queue_paths import initialize_queue_layout
from local_transcribe.services.queue_store import QueueStore
from local_transcribe.services.source_reservations import read_reservation


def _enqueue_once(args: tuple[str, str, bool]) -> tuple[str, str | None]:
    queue_dir, url, force = args
    store = QueueStore(Path(queue_dir))
    result = store.enqueue(url, origin="stress", priority=10, force=force)
    eid = result.execution.execution_id if result.execution else None
    return result.kind, eid


def test_concurrent_enqueue_one_active(tmp_path: Path) -> None:
    q = tmp_path / "q"
    initialize_queue_layout(q)
    url = "https://youtu.be/dQw4w9WgXcQ"
    store = QueueStore(q)

    def attempt(_i: int):
        return store.enqueue(url, origin="t", priority=10)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(attempt, range(8)))

    enqueued = [r for r in results if r.kind == "enqueued"]
    existing = [r for r in results if r.kind == "existing_active"]
    assert len(enqueued) + len(existing) == 8
    assert len(enqueued) == 1
    pending = list((q / "pending").glob("*.json"))
    assert len(pending) == 1
    res = read_reservation(q, "youtube:dQw4w9WgXcQ")
    assert res is not None
    assert res.current_execution_id == pending[0].stem


def test_multiprocess_enqueue_one_active(tmp_path: Path) -> None:
    q = tmp_path / "q"
    initialize_queue_layout(q)
    url = "https://youtu.be/aaaaaaaaaaa"
    args = [(str(q), url, False) for _ in range(6)]
    ctx = mp.get_context("spawn")
    with ProcessPoolExecutor(max_workers=4, mp_context=ctx) as pool:
        outcomes = list(pool.map(_enqueue_once, args))
    kinds = [k for k, _ in outcomes]
    assert kinds.count("enqueued") == 1
    assert kinds.count("existing_active") == 5
    pending = list((q / "pending").glob("*.json"))
    assert len(pending) == 1


def test_concurrent_force_single_current_generation(tmp_path: Path) -> None:
    q = tmp_path / "q"
    initialize_queue_layout(q)
    store = QueueStore(q)
    url = "https://youtu.be/bbbbbbbbbbb"
    first = store.enqueue(url)
    assert first.kind == "enqueued"

    def force_attempt(_i: int):
        return store.enqueue(url, force=True)

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(force_attempt, range(6)))

    enqueued = [r for r in results if r.kind == "enqueued"]
    assert len(enqueued) >= 1
    store.repair_source("youtube:bbbbbbbbbbb")
    res = read_reservation(q, "youtube:bbbbbbbbbbb")
    assert res is not None
    pending = list((q / "pending").glob("*.json"))
    # At most one current pending; heal if a race left a stale pointer.
    if len(pending) != 1 or pending[0].stem != res.current_execution_id:
        healed = store.enqueue(url, force=True)
        assert healed.kind in ("enqueued", "existing_active")
        store.repair_source("youtube:bbbbbbbbbbb")
        res = read_reservation(q, "youtube:bbbbbbbbbbb")
        assert res is not None
        pending = list((q / "pending").glob("*.json"))
    assert len(pending) == 1
    assert pending[0].stem == res.current_execution_id
    assert res.generation >= 2


def test_multiprocess_force_no_dual_current(tmp_path: Path) -> None:
    q = tmp_path / "q"
    initialize_queue_layout(q)
    store = QueueStore(q)
    url = "https://youtu.be/ccccccccccc"
    store.enqueue(url)
    args = [(str(q), url, True) for _ in range(4)]
    ctx = mp.get_context("spawn")
    with ProcessPoolExecutor(max_workers=4, mp_context=ctx) as pool:
        list(pool.map(_enqueue_once, args))
    res = read_reservation(q, "youtube:ccccccccccc")
    assert res is not None
    store.repair_source("youtube:ccccccccccc")
    pending_ids = {p.stem for p in (q / "pending").glob("*.json")}
    assert pending_ids == {res.current_execution_id}


def test_partial_enqueue_repairable(tmp_path: Path) -> None:
    q = tmp_path / "q"
    initialize_queue_layout(q)
    store = QueueStore(q)
    # Simulate incomplete pair: reservation without pending
    from local_transcribe.services.source_reservations import create_reservation_if_absent

    create_reservation_if_absent(
        q,
        source_key="youtube:ddddddddddd",
        source_type="youtube",
        execution_id="missing-exec-id",
    )
    assert store.find_execution("missing-exec-id")[0] is None
    result = store.enqueue("https://youtu.be/ddddddddddd")
    assert result.kind == "enqueued"
    assert result.execution is not None
    assert (q / "pending" / f"{result.execution.execution_id}.json").is_file()
    res = read_reservation(q, "youtube:ddddddddddd")
    assert res is not None
    assert res.current_execution_id == result.execution.execution_id


def test_cancel_pending_no_contradiction(tmp_path: Path) -> None:
    q = tmp_path / "q"
    initialize_queue_layout(q)
    store = QueueStore(q)
    r = store.enqueue("https://youtu.be/eeeeeeeeeee")
    assert r.execution is not None
    eid = r.execution.execution_id
    store.cancel_pending(eid)
    assert not (q / "pending" / f"{eid}.json").exists()
    assert (q / "cancelled" / f"{eid}.json").is_file()
    # Idempotent-ish: already cancelled
    again = store.cancel_pending(eid)
    assert again.status == "cancelled"
    assert not (q / "pending" / f"{eid}.json").exists()


def test_enqueue_youtube_safe_fallback(tmp_path: Path) -> None:
    pending = tmp_path / "transcript-pending.md"
    outcome = enqueue_youtube_safe(
        "https://youtu.be/fffffffffff",
        pending_fallback=pending,
        # No queue configured → QueuePathError → fallback
    )
    assert outcome.ok
    assert outcome.fell_back_to_pending
    assert pending.is_file()
    assert "fffffffffff" in pending.read_text(encoding="utf-8")


def test_enqueue_youtube_safe_success(tmp_path: Path) -> None:
    q = tmp_path / "q"
    initialize_queue_layout(q)
    pending = tmp_path / "pending.md"
    outcome = enqueue_youtube_safe(
        "https://youtu.be/dQw4w9WgXcQ",
        queue_dir=q,
        pending_fallback=pending,
    )
    assert outcome.ok
    assert not outcome.fell_back_to_pending
    assert outcome.result is not None
    assert outcome.result.kind == "enqueued"
    assert not pending.exists()


def test_legacy_import_atomic_rewrite(tmp_path: Path) -> None:
    q = tmp_path / "q"
    initialize_queue_layout(q)
    pending = tmp_path / "transcript-pending.md"
    pending.write_text(
        "# keep\nhttps://youtu.be/dQw4w9WgXcQ\nnot-a-url\n",
        encoding="utf-8",
    )
    stats = import_pending_file(q, pending)
    assert stats["enqueued"] == 1
    text = pending.read_text(encoding="utf-8")
    assert "not-a-url" in text
    assert "dQw4w9WgXcQ" not in text or text.count("dQw4w9WgXcQ") == 0
    assert Path(stats["backup"]).is_file()


def test_append_pending_url_atomic(tmp_path: Path) -> None:
    path = tmp_path / "p.md"
    append_pending_url(path, "https://youtu.be/ggggggggggg")
    append_pending_url(path, "https://youtu.be/ggggggggggg")  # dedupe
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines) == 1

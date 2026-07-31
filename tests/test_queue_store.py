"""Tests for queue store enqueue (task 014)."""

from __future__ import annotations

from pathlib import Path

from local_transcribe.services.queue_paths import initialize_queue_layout
from local_transcribe.services.queue_store import QueueStore


def _store(tmp_path: Path) -> QueueStore:
    queue = tmp_path / "q"
    initialize_queue_layout(queue)
    return QueueStore(queue)


def test_enqueue_youtube(tmp_path: Path) -> None:
    store = _store(tmp_path)
    result = store.enqueue(
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        origin="lt-transcribe",
        priority=100,
    )
    assert result.kind == "enqueued"
    assert result.source_key == "youtube:dQw4w9WgXcQ"
    assert result.execution is not None
    assert result.execution.execution_id.count("-") >= 4  # uuid shape
    assert (store.queue_dir / "pending" / f"{result.execution.execution_id}.json").is_file()


def test_alternate_urls_same_key(tmp_path: Path) -> None:
    store = _store(tmp_path)
    r1 = store.enqueue("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    r2 = store.enqueue("https://youtu.be/dQw4w9WgXcQ")
    assert r1.kind == "enqueued"
    assert r2.kind == "existing_active"
    assert r1.source_key == r2.source_key == "youtube:dQw4w9WgXcQ"
    assert r2.execution is not None
    assert r1.execution is not None
    assert r2.execution.execution_id == r1.execution.execution_id


def test_force_new_generation(tmp_path: Path) -> None:
    store = _store(tmp_path)
    r1 = store.enqueue("https://youtu.be/dQw4w9WgXcQ")
    r2 = store.enqueue("https://youtu.be/dQw4w9WgXcQ", force=True)
    assert r1.kind == "enqueued"
    assert r2.kind == "enqueued"
    assert r1.execution is not None and r2.execution is not None
    assert r2.execution.execution_id != r1.execution.execution_id
    assert r2.reservation_generation == 2


def test_transcript_checker_short_circuit(tmp_path: Path) -> None:
    store = _store(tmp_path)
    tx = tmp_path / "dQw4w9WgXcQ.json"
    tx.write_text("{}", encoding="utf-8")

    def checker(source_key: str) -> Path | None:
        if source_key == "youtube:dQw4w9WgXcQ":
            return tx
        return None

    result = store.enqueue(
        "https://youtu.be/dQw4w9WgXcQ",
        transcript_checker=checker,
    )
    assert result.kind == "already_completed"
    assert result.transcript_path == tx
    assert list((store.queue_dir / "pending").glob("*.json")) == []


def test_enqueue_local_file(tmp_path: Path) -> None:
    store = _store(tmp_path)
    audio = tmp_path / "talk.m4a"
    audio.write_bytes(b"fake-audio")
    result = store.enqueue(str(audio), origin="lt-transcribe")
    assert result.kind == "enqueued"
    assert result.source_key.startswith("local:")
    assert result.execution is not None
    assert result.execution.source_type == "local_file"


def test_cancel_pending(tmp_path: Path) -> None:
    store = _store(tmp_path)
    r = store.enqueue("https://youtu.be/dQw4w9WgXcQ")
    assert r.execution is not None
    cancelled = store.cancel_pending(r.execution.execution_id)
    assert cancelled.status == "cancelled"
    assert not (store.queue_dir / "pending" / f"{r.execution.execution_id}.json").exists()
    assert (store.queue_dir / "cancelled" / f"{r.execution.execution_id}.json").is_file()

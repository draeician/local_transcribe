"""Worker auto-import of transcript-pending.md."""

from __future__ import annotations

from pathlib import Path

import pytest

from local_transcribe.services.legacy_import import (
    LegacyPendingWatchState,
    import_pending_file,
    maybe_import_legacy_pending,
)
from local_transcribe.services.queue_paths import initialize_queue_layout
from local_transcribe.services.queue_store import QueueStore
from local_transcribe.services.worker import run_worker


def test_maybe_import_skips_unchanged(tmp_path: Path) -> None:
    q = tmp_path / "q"
    initialize_queue_layout(q)
    pending = tmp_path / "transcript-pending.md"
    pending.write_text("https://youtu.be/dQw4w9WgXcQ\n", encoding="utf-8")
    state = LegacyPendingWatchState()
    first = maybe_import_legacy_pending(q, pending, state=state)
    assert first is not None
    assert first["enqueued"] == 1
    second = maybe_import_legacy_pending(q, pending, state=state)
    assert second is None


def test_maybe_import_picks_up_new_append(tmp_path: Path) -> None:
    q = tmp_path / "q"
    initialize_queue_layout(q)
    pending = tmp_path / "transcript-pending.md"
    pending.write_text("https://youtu.be/dQw4w9WgXcQ\n", encoding="utf-8")
    state = LegacyPendingWatchState()
    maybe_import_legacy_pending(q, pending, state=state)
    pending.write_text("https://youtu.be/aaaaaaaaaaa\n", encoding="utf-8")
    again = maybe_import_legacy_pending(q, pending, state=state)
    assert again is not None
    assert again["enqueued"] == 1
    store = QueueStore(q)
    assert len(store.list_executions(status="pending")) == 2


def test_import_preserves_concurrent_append(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    q = tmp_path / "q"
    initialize_queue_layout(q)
    pending = tmp_path / "transcript-pending.md"
    pending.write_text("https://youtu.be/dQw4w9WgXcQ\n", encoding="utf-8")

    from local_transcribe.services import legacy_import as li

    real_enqueue = QueueStore.enqueue

    def enqueue_and_append(self, url, **kwargs):  # type: ignore[no-untyped-def]
        result = real_enqueue(self, url, **kwargs)
        if "dQw4w9WgXcQ" in url:
            pending.write_text(
                "https://youtu.be/dQw4w9WgXcQ\nhttps://youtu.be/bbbbbbbbbbb\n",
                encoding="utf-8",
            )
        return result

    monkeypatch.setattr(li.QueueStore, "enqueue", enqueue_and_append)
    summary = import_pending_file(q, pending, backup=False, origin="ref")
    assert summary["enqueued"] == 1
    body = pending.read_text(encoding="utf-8")
    assert "bbbbbbbbbbb" in body
    assert "dQw4w9WgXcQ" not in body


def test_worker_imports_legacy_pending_before_jobs(tmp_path: Path) -> None:
    q = tmp_path / "q"
    initialize_queue_layout(q)
    pending = tmp_path / "transcript-pending.md"
    pending.write_text("https://youtu.be/dQw4w9WgXcQ\n", encoding="utf-8")

    def runner(ex, scratch):  # type: ignore[no-untyped-def]
        return {"transcript": "hi", "_output_path": str(scratch / "out.json")}

    n = run_worker(
        queue_dir=q,
        once=True,
        validate_nfs=False,
        job_runner=runner,
        poll_interval_seconds=0.01,
        legacy_pending_file=pending,
        watch_legacy_pending=True,
    )
    assert n == 1
    assert pending.read_text(encoding="utf-8").strip() == ""
    assert list((q / "completed").glob("*.json"))

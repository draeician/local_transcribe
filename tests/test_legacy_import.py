"""Legacy pending import tests (task 025)."""

from __future__ import annotations

from pathlib import Path

from local_transcribe.services.legacy_import import import_pending_file
from local_transcribe.services.queue_paths import initialize_queue_layout
from local_transcribe.services.queue_store import QueueStore


def test_import_idempotent(tmp_path: Path) -> None:
    q = tmp_path / "q"
    initialize_queue_layout(q)
    pending = tmp_path / "transcript-pending.md"
    pending.write_text(
        "# pending\n"
        "https://youtu.be/dQw4w9WgXcQ\n"
        "not-a-url\n",
        encoding="utf-8",
    )
    s1 = import_pending_file(q, pending)
    assert s1["enqueued"] == 1
    # URL was removed from pending file after successful enqueue
    s2 = import_pending_file(q, pending)
    assert s2["enqueued"] == 0
    store = QueueStore(q)
    assert len(store.list_executions(status="pending")) == 1
    # Re-import same URL via store still dedupes
    r = store.enqueue("https://youtu.be/dQw4w9WgXcQ", origin="import")
    assert r.kind == "existing_active"

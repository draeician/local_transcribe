"""Public enqueue API for ref-cli and other producers (task 026)."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from local_transcribe.services.queue_models import ExecutionOptions
from local_transcribe.services.queue_paths import resolve_queue_dir
from local_transcribe.services.queue_store import EnqueueResult, QueueStore


def enqueue_youtube(
    url: str,
    *,
    origin: str = "ref",
    priority: int = 20,
    queue_dir: Path | None = None,
    force: bool = False,
    options: ExecutionOptions | None = None,
) -> EnqueueResult:
    """Enqueue a YouTube URL into the local-transcribe queue.

    Safe for ref-cli: raises only on path/config errors; callers should catch
    and fall back to transcript-pending.md.
    """
    qdir = resolve_queue_dir(queue_dir=queue_dir)
    store = QueueStore(qdir)
    return store.enqueue(
        url,
        origin=origin,
        priority=priority,
        force=force,
        options=options,
    )

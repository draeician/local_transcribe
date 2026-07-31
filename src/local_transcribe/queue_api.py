"""Public enqueue API for ref-cli and other producers (task 026 / 035).

Preferred adapter pattern for ref-cli:

```python
from local_transcribe.queue_api import enqueue_youtube_safe

outcome = enqueue_youtube_safe(
    url,
    pending_fallback=Path.home() / "references" / "transcript-pending.md",
)
if outcome.fell_back_to_pending:
    # queue unavailable — capture still succeeded via pending file
    ...
else:
    result = outcome.result  # EnqueueResult
```
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from local_transcribe.services.atomic_files import fsync_directory, unique_tmp_path
from local_transcribe.services.queue_models import ExecutionOptions
from local_transcribe.services.queue_paths import QueuePathError, resolve_queue_dir
from local_transcribe.services.queue_store import EnqueueResult, QueueStore

logger = logging.getLogger(__name__)


@dataclass
class SafeEnqueueOutcome:
    """Result of :func:`enqueue_youtube_safe`."""

    result: EnqueueResult | None
    fell_back_to_pending: bool
    pending_path: Path | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        """True when enqueue succeeded or pending fallback was written."""
        if self.fell_back_to_pending and self.pending_path is not None:
            return True
        return self.result is not None


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

    Raises on path/config errors. Prefer :func:`enqueue_youtube_safe` from
    ref-cli so capture can fall back to ``transcript-pending.md``.
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


def append_pending_url(pending_file: Path, url: str) -> Path:
    """Append a URL to a pending markdown file (atomic rewrite + fsync)."""
    pending_file = Path(pending_file).expanduser()
    pending_file.parent.mkdir(parents=True, exist_ok=True)
    existing = ""
    if pending_file.is_file():
        existing = pending_file.read_text(encoding="utf-8")
    line = url.strip()
    if line and line not in existing:
        if existing and not existing.endswith("\n"):
            existing += "\n"
        existing += f"- {line}\n"
    tmp = unique_tmp_path(pending_file)
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        try:
            os.write(fd, existing.encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp, pending_file)
        fsync_directory(pending_file.parent)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
    return pending_file


def enqueue_youtube_safe(
    url: str,
    *,
    origin: str = "ref",
    priority: int = 20,
    queue_dir: Path | None = None,
    force: bool = False,
    options: ExecutionOptions | None = None,
    pending_fallback: Path | None = None,
) -> SafeEnqueueOutcome:
    """Enqueue with optional fallback to ``transcript-pending.md``.

    Never raises for normal queue/NFS failures when ``pending_fallback`` is set;
    ref capture must not fail solely because the queue is unavailable.
    """
    try:
        result = enqueue_youtube(
            url,
            origin=origin,
            priority=priority,
            queue_dir=queue_dir,
            force=force,
            options=options,
        )
        return SafeEnqueueOutcome(result=result, fell_back_to_pending=False)
    except (QueuePathError, OSError, RuntimeError, ValueError) as exc:
        logger.warning("Queue enqueue failed for %s: %s", url, exc)
        if pending_fallback is None:
            raise
        path = append_pending_url(pending_fallback, url)
        return SafeEnqueueOutcome(
            result=None,
            fell_back_to_pending=True,
            pending_path=path,
            error=str(exc),
        )

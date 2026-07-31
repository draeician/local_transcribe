"""Legacy transcript-pending.md import (task 025)."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from local_transcribe.services.queue_store import QueueStore
from local_transcribe.utils.youtube import is_valid_youtube_url


def import_pending_file(queue_dir: Path, pending_file: Path) -> dict[str, Any]:
    """Import YouTube URLs from a pending file into the queue.

    Backs up the source file, rewrites unparseable lines preserved.
    """
    pending_file = Path(pending_file)
    text = pending_file.read_text(encoding="utf-8")
    lines = text.splitlines()
    backup = pending_file.with_suffix(pending_file.suffix + ".bak")
    shutil.copy2(pending_file, backup)

    store = QueueStore(queue_dir)
    enqueued = 0
    skipped = 0
    existing = 0
    kept: list[str] = []

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            kept.append(line)
            continue
        # markdown list bullets
        url = stripped.lstrip("-* ").strip()
        if not is_valid_youtube_url(url) and "youtu" not in url:
            kept.append(line)
            continue
        if not url.startswith("http"):
            kept.append(line)
            continue
        result = store.enqueue(url, origin="import", priority=10)
        if result.kind == "enqueued":
            enqueued += 1
        elif result.kind == "existing_active":
            existing += 1
        elif result.kind == "already_completed":
            skipped += 1
        else:
            kept.append(line)

    # Atomic-ish rewrite of remaining unparseable/unhandled lines
    new_body = "\n".join(kept)
    if kept and not new_body.endswith("\n"):
        new_body += "\n"
    tmp = pending_file.with_suffix(pending_file.suffix + ".tmp")
    tmp.write_text(new_body, encoding="utf-8")
    tmp.replace(pending_file)

    return {
        "enqueued": enqueued,
        "existing_active": existing,
        "already_completed": skipped,
        "preserved_lines": len(kept),
        "backup": str(backup),
    }

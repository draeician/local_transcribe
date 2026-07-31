"""Legacy transcript-pending.md import (task 025 / 035) + worker watcher."""

from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from local_transcribe.services.atomic_files import fsync_directory, unique_tmp_path
from local_transcribe.services.queue_store import QueueStore
from local_transcribe.utils.youtube import is_valid_youtube_url

logger = logging.getLogger(__name__)


def default_legacy_pending_path() -> Path:
    """Historical ref-cli pending file location."""
    return Path.home() / "references" / "transcripts" / "transcript-pending.md"


def _atomic_rewrite_text(path: Path, body: str) -> None:
    """Write ``body`` via tmp + fsync + replace (no in-place truncate)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = unique_tmp_path(path)
    payload = body.encode("utf-8")
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        try:
            os.write(fd, payload)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp, path)
        fsync_directory(path.parent)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def _extract_url(line: str) -> str | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    url = stripped.lstrip("-* ").strip()
    if not url.startswith("http"):
        return None
    if not is_valid_youtube_url(url) and "youtu" not in url.lower():
        return None
    return url


def import_pending_file(
    queue_dir: Path,
    pending_file: Path,
    *,
    origin: str = "import",
    priority: int = 10,
    backup: bool = True,
) -> dict[str, Any]:
    """Import YouTube URLs from a pending file into the queue.

    Successfully handled lines (enqueued / already active / already completed)
    are removed. Non-URL lines and ``requires_force`` rows are preserved.

    Re-reads the file before rewrite so concurrent appends (ref-cli) are kept.
    """
    pending_file = Path(pending_file).expanduser()
    if not pending_file.is_file():
        return {
            "enqueued": 0,
            "existing_active": 0,
            "already_completed": 0,
            "preserved_lines": 0,
            "backup": None,
            "missing": True,
        }

    text = pending_file.read_text(encoding="utf-8")
    lines = text.splitlines()
    backup_path: str | None = None
    if backup:
        bak = pending_file.with_suffix(pending_file.suffix + ".bak")
        shutil.copy2(pending_file, bak)
        backup_path = str(bak)

    store = QueueStore(queue_dir)
    enqueued = 0
    skipped = 0
    existing = 0
    removed_urls: set[str] = set()
    kept_from_snapshot: list[str] = []

    for line in lines:
        url = _extract_url(line)
        if url is None:
            kept_from_snapshot.append(line)
            continue
        result = store.enqueue(url, origin=origin, priority=priority)
        if result.kind == "enqueued":
            enqueued += 1
            removed_urls.add(url)
        elif result.kind == "existing_active":
            existing += 1
            removed_urls.add(url)
        elif result.kind == "already_completed":
            skipped += 1
            removed_urls.add(url)
        else:
            kept_from_snapshot.append(line)

    # Preserve lines appended by producers while we were enqueueing: rebuild
    # from the latest file contents, dropping only URLs we already handled.
    try:
        latest = pending_file.read_text(encoding="utf-8")
    except OSError:
        latest = text
    if latest != text:
        merged: list[str] = []
        for line in latest.splitlines():
            url = _extract_url(line)
            if url is not None and url in removed_urls:
                continue
            merged.append(line)
        kept_from_snapshot = merged

    new_body = "\n".join(kept_from_snapshot)
    if kept_from_snapshot and not new_body.endswith("\n"):
        new_body += "\n"
    _atomic_rewrite_text(pending_file, new_body)

    return {
        "enqueued": enqueued,
        "existing_active": existing,
        "already_completed": skipped,
        "preserved_lines": len(kept_from_snapshot),
        "backup": backup_path,
        "missing": False,
    }


@dataclass
class LegacyPendingWatchState:
    """mtime/size fingerprint so idle polls stay cheap."""

    mtime_ns: int | None = None
    size: int | None = None


def maybe_import_legacy_pending(
    queue_dir: Path,
    pending_file: Path | None,
    *,
    state: LegacyPendingWatchState | None = None,
    origin: str = "ref",
    priority: int = 20,
    force: bool = False,
) -> dict[str, Any] | None:
    """Import the legacy pending file when it exists and has changed.

    Returns the import summary when work ran, else ``None``.
    """
    if pending_file is None:
        return None
    path = Path(pending_file).expanduser()
    if not path.is_file():
        return None

    try:
        st = path.stat()
    except OSError as exc:
        logger.warning("Cannot stat legacy pending file %s: %s", path, exc)
        return None

    watch = state if state is not None else LegacyPendingWatchState()
    unchanged = (
        not force
        and watch.mtime_ns == st.st_mtime_ns
        and watch.size == st.st_size
    )
    if unchanged:
        return None
    if st.st_size == 0:
        watch.mtime_ns = st.st_mtime_ns
        watch.size = st.st_size
        return None

    # Peek for any importable URL before taking backup / rewrite.
    try:
        peek = path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("Cannot read legacy pending file %s: %s", path, exc)
        return None
    if not any(_extract_url(line) for line in peek.splitlines()):
        watch.mtime_ns = st.st_mtime_ns
        watch.size = st.st_size
        return None

    summary = import_pending_file(
        queue_dir,
        path,
        origin=origin,
        priority=priority,
        backup=False,
    )
    try:
        st2 = path.stat()
        watch.mtime_ns = st2.st_mtime_ns
        watch.size = st2.st_size
    except OSError:
        watch.mtime_ns = st.st_mtime_ns
        watch.size = st.st_size

    if (
        summary.get("enqueued", 0)
        or summary.get("existing_active", 0)
        or summary.get("already_completed", 0)
    ):
        logger.info(
            "Imported legacy pending file %s: enqueued=%s existing=%s completed=%s",
            path,
            summary.get("enqueued"),
            summary.get("existing_active"),
            summary.get("already_completed"),
        )
    return summary

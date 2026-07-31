"""Generation-aware atomic transcript publication (SPEC §12)."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from local_transcribe.services.atomic_files import fsync_directory, unique_tmp_path


class TranscriptPublishError(RuntimeError):
    pass


@dataclass
class PublishResult:
    path: Path
    generation: int
    generation_copy: Path | None = None


def _video_id_from_source_key(source_key: str) -> str:
    if source_key.startswith("youtube:"):
        return source_key.split(":", 1)[1]
    if source_key.startswith("file:"):
        # Direct-mode local stems (preserve caller-chosen filename id)
        return source_key.split(":", 1)[1]
    if source_key.startswith("local:"):
        return source_key.replace(":", "_")
    return re.sub(r"[^A-Za-z0-9._-]", "_", source_key)


def validate_transcript_payload(
    data: Mapping[str, Any],
    *,
    expected_source_id: str | None = None,
    allow_empty: bool = False,
) -> None:
    if "transcript" not in data:
        raise TranscriptPublishError("Transcript JSON missing 'transcript' field")
    text = data.get("transcript")
    if not isinstance(text, str):
        raise TranscriptPublishError("'transcript' must be a string")
    if not allow_empty and not text.strip():
        raise TranscriptPublishError("'transcript' is empty")
    if expected_source_id is not None:
        meta = data.get("metadata") or {}
        mid = meta.get("id") if isinstance(meta, Mapping) else None
        if mid is not None and str(mid) != expected_source_id:
            raise TranscriptPublishError(
                f"Transcript metadata.id {mid!r} does not match expected {expected_source_id!r}"
            )


def _read_generation_marker(path: Path) -> int:
    marker = path.parent / f".{path.name}.generation"
    if not marker.is_file():
        return 0
    try:
        return int(marker.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return 0


def _write_generation_marker(path: Path, generation: int) -> None:
    marker = path.parent / f".{path.name}.generation"
    tmp = unique_tmp_path(marker)
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        try:
            os.write(fd, f"{generation}\n".encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp, marker)
        fsync_directory(marker.parent)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def _write_json_tmp(directory: Path, payload: Mapping[str, Any], *, hint: str) -> Path:
    """Write validated JSON to a unique tmp file under ``directory``."""
    directory.mkdir(parents=True, exist_ok=True)
    target_hint = directory / hint
    tmp_path = unique_tmp_path(target_hint)
    body = (json.dumps(dict(payload), ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        os.write(fd, body)
        os.fsync(fd)
    finally:
        os.close(fd)
    with tmp_path.open("r", encoding="utf-8") as handle:
        json.load(handle)
    return tmp_path


def publish_transcript(
    transcripts_root: Path,
    *,
    source_key: str,
    payload: Mapping[str, Any],
    generation: int,
    allow_empty: bool = False,
) -> PublishResult:
    """Atomically publish transcript; refuse lower generations overwriting higher.

    Never truncates the final path in place: content is written to a temporary
    file, fsynced, validated, then ``os.replace``d into place.
    """
    if generation < 1:
        raise TranscriptPublishError("generation must be >= 1")

    root = Path(transcripts_root)
    root.mkdir(parents=True, exist_ok=True)
    video_id = _video_id_from_source_key(source_key)
    final_path = root / f"{video_id}.json"

    expected_id = video_id if source_key.startswith("youtube:") else None
    validate_transcript_payload(
        payload, expected_source_id=expected_id, allow_empty=allow_empty
    )

    current_gen = _read_generation_marker(final_path)
    if final_path.is_file() and current_gen > generation:
        raise TranscriptPublishError(
            f"Refusing to publish generation {generation}: "
            f"existing transcript is generation {current_gen}"
        )
    if final_path.is_file() and current_gen == generation:
        try:
            existing = json.loads(final_path.read_text(encoding="utf-8"))
            validate_transcript_payload(
                existing, expected_source_id=expected_id, allow_empty=allow_empty
            )
            return PublishResult(path=final_path, generation=generation)
        except (OSError, json.JSONDecodeError, TranscriptPublishError):
            pass  # rewrite invalid same-generation file

    gen_dir = root / ".generations" / video_id
    gen_dir.mkdir(parents=True, exist_ok=True)
    gen_copy = gen_dir / f"{generation:06d}.json"

    # Archive copy (tmp + replace) then final path (tmp + replace).
    archive_tmp = _write_json_tmp(gen_dir, payload, hint=gen_copy.name)
    try:
        validate_transcript_payload(
            json.loads(archive_tmp.read_text(encoding="utf-8")),
            expected_source_id=expected_id,
            allow_empty=allow_empty,
        )
        os.replace(archive_tmp, gen_copy)
        fsync_directory(gen_dir)
    finally:
        try:
            archive_tmp.unlink(missing_ok=True)
        except OSError:
            pass

    final_tmp = _write_json_tmp(root, payload, hint=final_path.name)
    published = False
    try:
        validate_transcript_payload(
            json.loads(final_tmp.read_text(encoding="utf-8")),
            expected_source_id=expected_id,
            allow_empty=allow_empty,
        )
        os.replace(final_tmp, final_path)
        published = True
        fsync_directory(root)
        _write_generation_marker(final_path, generation)

        reopened = json.loads(final_path.read_text(encoding="utf-8"))
        validate_transcript_payload(
            reopened, expected_source_id=expected_id, allow_empty=allow_empty
        )
        return PublishResult(
            path=final_path, generation=generation, generation_copy=gen_copy
        )
    finally:
        if not published:
            try:
                final_tmp.unlink(missing_ok=True)
            except OSError:
                pass

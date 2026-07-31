"""Explicit transcription queue path resolution and layout initialization.

No multi-candidate discovery: path comes from CLI ``--queue-dir`` or config
``queue.path`` only. See SPEC-queue.md §4.
"""

from __future__ import annotations

import json
import os
import socket
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional, Sequence

from local_transcribe.services.config import AppConfig, QueueConfig, load_config

# State / support directories under the queue root (SPEC §5).
QUEUE_STATE_DIRS: tuple[str, ...] = (
    "keys",
    "pending",
    "processing",
    "retry",
    "completed",
    "failed",
    "cancelled",
    "tmp",
    "worker",
)

QUEUE_ID_FILENAME = "queue.id"


class QueuePathError(RuntimeError):
    """Base error for queue path resolution and layout problems."""


class QueueIdentityError(QueuePathError):
    """Queue identity marker missing, unparseable, or UUID mismatch."""


class QueueMountError(QueuePathError):
    """Mount validation failure (reserved for mount_validation module)."""


def queue_id_path(queue_dir: Path) -> Path:
    """Return the path to the queue identity marker file."""
    return queue_dir / QUEUE_ID_FILENAME


def iter_state_dirs(queue_dir: Path) -> Iterable[Path]:
    """Yield absolute paths for each required state directory."""
    root = queue_dir.expanduser().resolve(strict=False)
    for name in QUEUE_STATE_DIRS:
        yield root / name


def _local_created_at() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _read_queue_id_document(path: Path) -> dict:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise QueueIdentityError(f"Unable to read queue identity file {path}: {exc}") from exc

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise QueueIdentityError(
            f"Queue identity file {path} is not valid JSON: {exc}"
        ) from exc

    if not isinstance(data, dict):
        raise QueueIdentityError(f"Queue identity file {path} must contain a JSON object")

    queue_uuid = data.get("queue_uuid")
    if not queue_uuid or not isinstance(queue_uuid, str):
        raise QueueIdentityError(
            f"Queue identity file {path} missing string field 'queue_uuid'"
        )
    return data


def verify_queue_identity(
    queue_dir: Path,
    *,
    expected_uuid: str | None = None,
) -> str:
    """Read and validate ``queue.id``; return the queue UUID.

    Raises:
        QueueIdentityError: missing file, unparseable content, or UUID mismatch.
        QueuePathError: path is not a directory.
    """
    root = queue_dir.expanduser()
    if not root.exists():
        raise QueuePathError(f"Queue directory does not exist: {root}")
    if not root.is_dir():
        raise QueuePathError(f"Queue path is not a directory: {root}")

    id_path = queue_id_path(root)
    if not id_path.is_file():
        raise QueueIdentityError(
            f"Queue identity marker missing: {id_path}. "
            "Run queue init for this path or fix configuration."
        )

    data = _read_queue_id_document(id_path)
    queue_uuid = str(data["queue_uuid"]).strip()
    if not queue_uuid:
        raise QueueIdentityError(f"Queue identity file {id_path} has empty queue_uuid")

    if expected_uuid is not None and expected_uuid.strip():
        want = expected_uuid.strip()
        if queue_uuid != want:
            raise QueueIdentityError(
                f"Queue UUID mismatch for {root}: "
                f"found {queue_uuid!r}, expected {want!r}"
            )

    return queue_uuid


def resolve_queue_dir(
    *,
    queue_dir: Path | None = None,
    config: AppConfig | QueueConfig | None = None,
    config_path: Path | None = None,
    verify_identity: bool = True,
) -> Path:
    """Resolve the authoritative queue directory (explicit path only).

    Precedence:
        1. ``queue_dir`` argument (CLI ``--queue-dir``)
        2. Configured ``queue.path``
        3. Error — no fall-through or candidate search

    When ``verify_identity`` is True (default), ``queue.id`` must exist and
    match ``expected_uuid`` when configured.
    """
    queue_cfg: QueueConfig
    if config is None:
        queue_cfg = load_config(config_path).queue
    elif isinstance(config, AppConfig):
        queue_cfg = config.queue
    else:
        queue_cfg = config

    if queue_dir is not None:
        resolved = queue_dir.expanduser()
    elif queue_cfg.path is not None:
        resolved = queue_cfg.path.expanduser()
    else:
        raise QueuePathError(
            "No queue directory configured. Pass --queue-dir or set "
            "queue.path in ~/.config/local-transcribe/config.yaml"
        )

    if not resolved.exists():
        raise QueuePathError(f"Queue directory does not exist: {resolved}")
    if not resolved.is_dir():
        raise QueuePathError(f"Queue path is not a directory: {resolved}")

    if verify_identity:
        verify_queue_identity(resolved, expected_uuid=queue_cfg.expected_uuid)

    return resolved.resolve(strict=False)


def initialize_queue_layout(queue_dir: Path) -> str:
    """Create required queue directories and write ``queue.id`` once.

    If ``queue.id`` already exists and is valid, returns the existing UUID
    without modifying it. Missing state directories are still created.

    Returns:
        The queue UUID (existing or newly generated).
    """
    root = queue_dir.expanduser()
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise QueuePathError(f"Unable to create queue directory {root}: {exc}") from exc

    if not root.is_dir():
        raise QueuePathError(f"Queue path is not a directory: {root}")

    for state_dir in iter_state_dirs(root):
        try:
            state_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise QueuePathError(f"Unable to create {state_dir}: {exc}") from exc

    id_path = queue_id_path(root)
    if id_path.exists():
        if not id_path.is_file():
            raise QueueIdentityError(f"Queue identity path exists but is not a file: {id_path}")
        return verify_queue_identity(root, expected_uuid=None)

    queue_uuid = str(uuid.uuid4())
    document = {
        "queue_uuid": queue_uuid,
        "created_at": _local_created_at(),
        "created_by": socket.gethostname(),
    }

    # Write once without using generic in-place helpers that may be shared elsewhere.
    # Create-exclusively so concurrent inits cannot clobber an existing marker.
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    try:
        fd = os.open(id_path, flags, 0o644)
    except FileExistsError:
        return verify_queue_identity(root, expected_uuid=None)
    except OSError as exc:
        raise QueuePathError(f"Unable to create queue identity file {id_path}: {exc}") from exc

    try:
        payload = (json.dumps(document, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
        os.write(fd, payload)
        os.fsync(fd)
    except OSError as exc:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            id_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise QueuePathError(f"Unable to write queue identity file {id_path}: {exc}") from exc
    finally:
        try:
            os.close(fd)
        except OSError:
            pass

    return queue_uuid


def required_layout_names() -> Sequence[str]:
    """Return the required subdirectory names (for tests and doctor output)."""
    return QUEUE_STATE_DIRS

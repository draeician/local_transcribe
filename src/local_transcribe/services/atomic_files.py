"""NFS-safe atomic file primitives for queue authority writes.

Create-if-absent publication uses hard ``link()`` (POSIX ``rename()`` overwrites
destinations). In-place replacement of worker-owned metadata uses unique tmp +
``os.replace``.

Do **not** use :func:`local_transcribe.utils.files.safe_write_json` for queue,
worker, rate, reservation, or transcript authority — it writes in place.
"""

from __future__ import annotations

import errno
import json
import os
import secrets
import socket
from pathlib import Path
from typing import Any, Mapping


class AtomicFileError(RuntimeError):
    """Raised when an atomic file operation fails."""


def unique_tmp_name(target: Path, *, prefix: str = ".") -> str:
    """Build a unique temporary filename sibling to ``target``.

    Includes hostname, pid, and random hex so concurrent producers do not collide.
    """
    host = socket.gethostname().replace("/", "_") or "host"
    pid = os.getpid()
    token = secrets.token_hex(8)
    return f"{prefix}{target.name}.tmp.{host}.{pid}.{token}"


def unique_tmp_path(target: Path, *, prefix: str = ".") -> Path:
    """Return a unique temporary path next to ``target``."""
    return target.parent / unique_tmp_name(target, prefix=prefix)


def fsync_directory(directory: Path) -> None:
    """Best-effort ``fsync`` of a directory fd (durability of directory entries)."""
    try:
        dir_fd = os.open(str(directory), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(dir_fd)
    except OSError:
        # Some filesystems do not support directory fsync.
        pass
    finally:
        try:
            os.close(dir_fd)
        except OSError:
            pass


def atomic_write_json(path: Path, data: Mapping[str, Any] | dict[str, Any]) -> None:
    """Write JSON via unique tmp, fsync, parse-validate, ``os.replace``, fsync parent.

    The destination is never truncated mid-write: content is fully written to a
    temporary file first. On failure, leftover temps are removed when possible.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    tmp_path = unique_tmp_path(path)
    published = False
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        fd = os.open(tmp_path, flags, 0o644)
        try:
            payload = (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
            os.write(fd, payload)
            os.fsync(fd)
        finally:
            os.close(fd)

        with tmp_path.open("r", encoding="utf-8") as handle:
            parsed = json.load(handle)
        if not isinstance(parsed, dict):
            raise AtomicFileError(
                f"atomic_write_json requires a JSON object, got {type(parsed).__name__}"
            )

        os.replace(tmp_path, path)
        published = True
        fsync_directory(path.parent)
    finally:
        if not published:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass


def write_json_tmp(
    directory: Path,
    data: Mapping[str, Any] | dict[str, Any],
    *,
    name_hint: str = "payload.json",
) -> Path:
    """Write JSON to a unique temp file under ``directory`` and return its path.

    Used as step 1 of create-if-absent publication: write tmp, then
    :func:`link_publish` into the final name. Caller owns tmp cleanup unless
    using :func:`link_publish_json`.
    """
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    hint = Path(name_hint)
    tmp_path = directory / unique_tmp_name(hint, prefix=".")

    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = os.open(tmp_path, flags, 0o644)
    try:
        payload = (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        os.write(fd, payload)
        os.fsync(fd)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    else:
        os.close(fd)

    try:
        with tmp_path.open("r", encoding="utf-8") as handle:
            json.load(handle)
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise

    return tmp_path


def link_publish(tmp_path: Path, dest_path: Path) -> bool:
    """Publish ``tmp_path`` to ``dest_path`` via hard ``link()``.

    Returns:
        True if this call created ``dest_path``.
        False if ``dest_path`` already existed (``EEXIST`` / ``FileExistsError``).

    Does **not** unlink ``tmp_path``; the caller must remove the temporary file
    on both success and failure (SPEC enqueue sequence).

    Raises:
        AtomicFileError: unexpected errors other than destination exists.
    """
    tmp_path = Path(tmp_path)
    dest_path = Path(dest_path)
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    if not tmp_path.is_file():
        raise AtomicFileError(f"link_publish source is not a file: {tmp_path}")

    try:
        os.link(tmp_path, dest_path)
    except FileExistsError:
        return False
    except OSError as exc:
        if exc.errno == errno.EEXIST:
            return False
        raise AtomicFileError(
            f"link_publish failed {tmp_path} -> {dest_path}: {exc}"
        ) from exc

    fsync_directory(dest_path.parent)
    return True


def link_publish_json(
    dest_path: Path,
    data: Mapping[str, Any] | dict[str, Any],
    *,
    tmp_dir: Path | None = None,
) -> bool:
    """Write JSON to a temp file and ``link()``-publish to ``dest_path``.

    Cleans up the temporary file on both success and duplicate-destination paths.

    Returns:
        True if published, False if destination already existed.
    """
    dest_path = Path(dest_path)
    directory = Path(tmp_dir) if tmp_dir is not None else dest_path.parent
    tmp_path = write_json_tmp(directory, data, name_hint=dest_path.name)
    try:
        return link_publish(tmp_path, dest_path)
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass


def rename_exclusive(src: Path, dest: Path) -> None:
    """Move ``src`` to ``dest`` without replacing an existing destination.

    POSIX ``rename``/``os.replace`` silently overwrite ``dest``. Queue state
    transitions must fail closed on collision, so this uses hard ``link()``
    then ``unlink(src)`` (same-filesystem). Parent directories are fsynced
    where supported.

    Raises:
        FileNotFoundError: ``src`` is missing.
        AtomicFileError: ``dest`` already exists, or link/unlink failed.
    """
    src = Path(src)
    dest = Path(dest)
    if not src.is_file():
        raise FileNotFoundError(str(src))
    dest.parent.mkdir(parents=True, exist_ok=True)

    try:
        os.link(src, dest)
    except FileExistsError as exc:
        raise AtomicFileError(f"Destination already exists: {dest}") from exc
    except OSError as exc:
        if exc.errno == errno.EEXIST:
            raise AtomicFileError(f"Destination already exists: {dest}") from exc
        raise AtomicFileError(
            f"rename_exclusive failed {src} -> {dest}: {exc}"
        ) from exc

    try:
        src.unlink()
    except OSError as exc:
        # Dest is published (hard-linked). Do not unlink dest; leave both names
        # for recovery rather than destroying the only remaining link.
        raise AtomicFileError(
            f"Published {dest} but failed to unlink source {src}: {exc}"
        ) from exc

    fsync_directory(dest.parent)
    if src.parent.resolve() != dest.parent.resolve():
        fsync_directory(src.parent)


def atomic_state_transition(
    src: Path,
    dest: Path,
    data: Mapping[str, Any] | dict[str, Any],
) -> None:
    """Exclusive-move execution JSON ``src`` → ``dest``, then write ``data``.

    Order matters for crash safety:

    1. :func:`rename_exclusive` — directory membership is the state authority;
       collision leaves ``src`` untouched.
    2. :func:`atomic_write_json` — durable metadata update at the new path
       (tmp + fsync + ``os.replace`` + parent fsync).

    Never copy-then-unlink and never truncate the authoritative file in place.
    """
    rename_exclusive(src, dest)
    atomic_write_json(dest, data)

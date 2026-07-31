"""NFSv3 mount validation for the transcription queue.

Fail-closed checks that the queue path sits on a safe NFSv3 mount suitable
for NLM-backed POSIX locking. Pure diagnostics + validation API; the worker
calls this before acquiring the worker lock (wired in later tasks).

Parse strategy (in order):
1. ``findmnt -T <path> -J`` when available (structured)
2. ``/proc/self/mountinfo`` fallback

Unit tests inject a :class:`MountInfo` or mock the probe functions.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping, MutableMapping, Sequence

from local_transcribe.services.config import QueueConfig
from local_transcribe.services.queue_paths import QueueMountError

# Options that break cross-host NLM exclusion or data safety (SPEC §10).
_UNSAFE_OPTIONS: frozenset[str] = frozenset(
    {
        "nolock",
        "soft",
        "softerr",
    }
)

# local_lock values that make POSIX locks local-only (not NLM).
_UNSAFE_LOCAL_LOCK: frozenset[str] = frozenset(
    {
        "posix",
        "all",
        "flock",
    }
)


@dataclass(frozen=True)
class MountInfo:
    """Normalized view of the mount covering a path."""

    target: str
    fstype: str
    source: str
    options: tuple[str, ...]
    vfs_options: tuple[str, ...] = ()
    fs_options: tuple[str, ...] = ()
    raw: dict = field(default_factory=dict, hash=False, compare=False)

    def all_options(self) -> tuple[str, ...]:
        """Return combined option tokens (order not significant)."""
        seen: list[str] = []
        for group in (self.options, self.vfs_options, self.fs_options):
            for opt in group:
                if opt and opt not in seen:
                    seen.append(opt)
        return tuple(seen)

    def option_map(self) -> dict[str, str | None]:
        """Map option name -> value (None if flag-only)."""
        result: dict[str, str | None] = {}
        for opt in self.all_options():
            if "=" in opt:
                key, _, value = opt.partition("=")
                result[key.strip().lower()] = value.strip()
            else:
                result[opt.strip().lower()] = None
        return result

    @property
    def is_nfs(self) -> bool:
        ft = self.fstype.lower()
        return ft in {"nfs", "nfs4"} or ft.startswith("nfs")

    @property
    def nfs_version(self) -> int | None:
        """Best-effort NFS major version from fstype / options."""
        ft = self.fstype.lower()
        if ft == "nfs4" or ft.startswith("nfs4"):
            return 4
        opts = self.option_map()
        for key in ("vers", "nfsvers"):
            if key in opts and opts[key] is not None:
                raw = opts[key] or ""
                # vers=3 or vers=4.2
                match = re.match(r"(\d+)", raw)
                if match:
                    return int(match.group(1))
        if ft == "nfs":
            # NFSv3 is the historical default when vers is omitted on many systems.
            return 3
        return None

    @property
    def is_readonly(self) -> bool:
        opts = self.option_map()
        if "ro" in opts:
            return True
        if "rw" in opts:
            return False
        return False

    def split_source(self) -> tuple[str | None, str | None]:
        """Return (server, export) for ``server:/export`` style sources."""
        source = self.source
        if ":" not in source:
            return None, None
        # IPv6 might use [addr]:/path — handle simply.
        if source.startswith("["):
            end = source.find("]")
            if end != -1 and end + 1 < len(source) and source[end + 1] == ":":
                return source[1:end], source[end + 2 :]
        server, _, export = source.partition(":")
        if not server or not export:
            return None, None
        return server, export


@dataclass
class MountValidationResult:
    """Structured validation outcome (for doctor / status)."""

    ok: bool
    path: Path
    mount: MountInfo | None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    checks: dict[str, str] = field(default_factory=dict)
    skipped: bool = False

    def raise_if_failed(self) -> None:
        if not self.ok:
            msg = "; ".join(self.errors) if self.errors else "mount validation failed"
            raise QueueMountError(msg)


def _split_options(option_string: str | None) -> tuple[str, ...]:
    if not option_string:
        return ()
    return tuple(part.strip() for part in option_string.split(",") if part.strip())


def mount_info_from_findmnt_filesystem(fs: Mapping[str, object]) -> MountInfo:
    """Build :class:`MountInfo` from one findmnt JSON filesystem object."""
    target = str(fs.get("target") or fs.get("TARGET") or "")
    fstype = str(fs.get("fstype") or fs.get("FSTYPE") or "")
    source = str(fs.get("source") or fs.get("SOURCE") or "")
    options = _split_options(str(fs.get("options") or fs.get("OPTIONS") or ""))
    vfs = _split_options(str(fs.get("vfs-options") or fs.get("VFS-OPTIONS") or ""))
    fs_opts = _split_options(str(fs.get("fs-options") or fs.get("FS-OPTIONS") or ""))
    return MountInfo(
        target=target,
        fstype=fstype,
        source=source,
        options=options,
        vfs_options=vfs,
        fs_options=fs_opts,
        raw=dict(fs),
    )


def _prefer_mount(candidates: Sequence[MountInfo]) -> MountInfo | None:
    """Prefer a real NFS mount over autofs/bind overlays at the same path."""
    if not candidates:
        return None
    nfs = [m for m in candidates if m.is_nfs]
    if nfs:
        # Last NFS entry wins (findmnt/mountinfo often list autofs then nfs).
        return nfs[-1]
    non_autofs = [m for m in candidates if m.fstype.lower() != "autofs"]
    if non_autofs:
        return non_autofs[-1]
    return candidates[-1]


def parse_findmnt_json(payload: str | bytes | Mapping[str, object]) -> MountInfo | None:
    """Parse ``findmnt -J`` output; prefer NFS over autofs when both appear."""
    if isinstance(payload, (str, bytes)):
        data = json.loads(payload)
    else:
        data = payload
    if not isinstance(data, Mapping):
        return None
    filesystems = data.get("filesystems")
    if not isinstance(filesystems, list) or not filesystems:
        return None
    mounts: list[MountInfo] = []
    for entry in filesystems:
        if isinstance(entry, Mapping):
            mounts.append(mount_info_from_findmnt_filesystem(entry))
    return _prefer_mount(mounts)


def _probe_findmnt(path: Path) -> MountInfo | None:
    findmnt = shutil.which("findmnt")
    if not findmnt:
        return None
    try:
        proc = subprocess.run(
            [findmnt, "-T", str(path), "-J", "-o", "TARGET,SOURCE,FSTYPE,OPTIONS"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    try:
        return parse_findmnt_json(proc.stdout)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None


def _parse_mountinfo_line(line: str) -> MountInfo | None:
    # See mount_namespaces(7) / kernel Documentation/filesystems/proc.rst
    # Format: id parent major:minor root mount_point options optional_fields - fstype source super_opts
    if " - " not in line:
        return None
    left, right = line.split(" - ", 1)
    left_parts = left.split()
    right_parts = right.split()
    if len(left_parts) < 6 or len(right_parts) < 3:
        return None
    mount_point = left_parts[4]
    # Unescape octal sequences used in mountinfo (\040 for space, etc.)
    mount_point = _unescape_mount_field(mount_point)
    options = _split_options(left_parts[5])
    fstype = right_parts[0]
    source = _unescape_mount_field(right_parts[1])
    super_opts = _split_options(right_parts[2]) if len(right_parts) > 2 else ()
    return MountInfo(
        target=mount_point,
        fstype=fstype,
        source=source,
        options=options,
        fs_options=super_opts,
        raw={"mountinfo": line},
    )


def _unescape_mount_field(value: str) -> str:
    def repl(match: re.Match[str]) -> str:
        return chr(int(match.group(1), 8))

    return re.sub(r"\\([0-7]{3})", repl, value)


def parse_mountinfo(
    text: str,
    path: Path,
) -> MountInfo | None:
    """Select the mount covering ``path`` from mountinfo text.

    Longest mount-point prefix wins. Ties prefer NFS over autofs (common
    when systemd automounts sit under the same target as the NFS overlay).
    """
    try:
        resolved = path.resolve()
    except OSError:
        resolved = path

    covering: list[MountInfo] = []
    best_len = -1
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        info = _parse_mountinfo_line(line)
        if info is None:
            continue
        mount_path = Path(info.target)
        try:
            resolved.relative_to(mount_path)
        except ValueError:
            if resolved != mount_path:
                continue
        length = len(str(mount_path))
        if length > best_len:
            covering = [info]
            best_len = length
        elif length == best_len:
            covering.append(info)
    return _prefer_mount(covering)


def _probe_mountinfo(path: Path, mountinfo_path: Path | None = None) -> MountInfo | None:
    mi_path = mountinfo_path or Path("/proc/self/mountinfo")
    try:
        text = mi_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    return parse_mountinfo(text, path)


def probe_mount(
    path: Path,
    *,
    findmnt_json: str | Mapping[str, object] | None = None,
    mountinfo_text: str | None = None,
) -> MountInfo | None:
    """Discover mount covering ``path``.

    Injection hooks for tests:
    - ``findmnt_json``: skip subprocess, parse this payload
    - ``mountinfo_text``: skip /proc, parse this text
    """
    path = Path(path)
    if findmnt_json is not None:
        return parse_findmnt_json(findmnt_json)
    if mountinfo_text is not None:
        return parse_mountinfo(mountinfo_text, path)

    info = _probe_findmnt(path)
    if info is not None:
        return info
    return _probe_mountinfo(path)


def _looks_like_unmounted_mountpoint(path: Path, mount: MountInfo | None) -> bool:
    """Heuristic: path exists as empty local dir while expected to be NFS.

    True when the covering mount is not NFS and the directory is empty — a
    common failure when the NFS share failed to mount and the mountpoint is a
    local empty directory.
    """
    if mount is None:
        return False
    if mount.is_nfs:
        return False
    try:
        if not path.is_dir():
            return False
        # Only flag empty directories that are the mount target itself or under
        # a non-nfs root filesystem entry that looks like a mountpoint.
        next(path.iterdir())
        return False  # not empty
    except StopIteration:
        return True
    except OSError:
        return False


def check_rpc_statd_running(
    *,
    proc_dir: Path | None = None,
) -> bool | None:
    """Best-effort check that rpc.statd appears in the process list.

    Returns:
        True / False if /proc is readable, None if indeterminate.
    """
    base = proc_dir or Path("/proc")
    if not base.is_dir():
        return None
    try:
        entries = list(base.iterdir())
    except OSError:
        return None

    for entry in entries:
        if not entry.name.isdigit():
            continue
        cmdline_path = entry / "cmdline"
        try:
            raw = cmdline_path.read_bytes()
        except OSError:
            continue
        text = raw.replace(b"\x00", b" ").decode("utf-8", errors="replace").lower()
        if "rpc.statd" in text or "statd" in text.split()[:1]:
            return True
        # also match comm
        try:
            comm = (entry / "comm").read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            continue
        if comm in {"rpc.statd", "statd"}:
            return True
    return False


def validate_queue_mount(
    path: Path,
    *,
    queue_config: QueueConfig | None = None,
    validate_nfs: bool = True,
    mount: MountInfo | None = None,
    findmnt_json: str | Mapping[str, object] | None = None,
    mountinfo_text: str | None = None,
    require_statd: bool = False,
    proc_dir: Path | None = None,
) -> MountValidationResult:
    """Validate that ``path`` is on a safe NFSv3 mount.

    Args:
        path: Queue directory path.
        queue_config: Optional expectations (version, server, export).
        validate_nfs: When False, skip NFS checks (unit tests / local fakes only).
        mount: Injected MountInfo (skips probe).
        findmnt_json / mountinfo_text: Injected probe inputs.
        require_statd: If True, missing rpc.statd is an error; else warning.
        proc_dir: Override /proc for statd checks in tests.
    """
    path = Path(path)
    cfg = queue_config or QueueConfig()
    result = MountValidationResult(ok=True, path=path, mount=None)

    if not validate_nfs:
        result.skipped = True
        result.checks["nfs_validation"] = "skipped (validate_nfs=False)"
        result.warnings.append(
            "NFS mount validation skipped (validate_nfs=False); "
            "production workers must not use this flag"
        )
        return result

    if not path.exists():
        result.ok = False
        result.errors.append(f"Queue path does not exist: {path}")
        result.checks["exists"] = "fail"
        return result

    info = mount if mount is not None else probe_mount(
        path, findmnt_json=findmnt_json, mountinfo_text=mountinfo_text
    )
    result.mount = info

    if info is None:
        result.ok = False
        result.errors.append(
            f"Unable to determine mount information for {path}. "
            "Install util-linux findmnt or ensure /proc/self/mountinfo is readable."
        )
        result.checks["probe"] = "fail"
        return result

    result.checks["probe"] = "ok"
    result.checks["fstype"] = info.fstype
    result.checks["source"] = info.source
    result.checks["target"] = info.target

    # Filesystem type
    if not info.is_nfs:
        result.ok = False
        result.errors.append(
            f"Queue path {path} is on filesystem type {info.fstype!r}, "
            f"expected NFS (mount {info.target})."
        )
        result.checks["nfs"] = "fail"
        if _looks_like_unmounted_mountpoint(path, info):
            result.errors.append(
                f"Path {path} looks like an empty local directory under a non-NFS "
                "mount — the NFS share may be unmounted."
            )
            result.checks["unmounted_mountpoint"] = "suspect"
        return result
    result.checks["nfs"] = "ok"

    # Version
    expected_version = cfg.expected_nfs_version
    actual_version = info.nfs_version
    if expected_version is not None:
        if actual_version is None:
            result.ok = False
            result.errors.append(
                f"Could not determine NFS version for {path}; "
                f"expected NFSv{expected_version}."
            )
            result.checks["nfs_version"] = "unknown"
        elif actual_version != expected_version:
            result.ok = False
            result.errors.append(
                f"NFS version mismatch for {path}: found {actual_version}, "
                f"expected {expected_version} (SPEC requires NFSv3 for NLM)."
            )
            result.checks["nfs_version"] = f"fail (got {actual_version})"
        else:
            result.checks["nfs_version"] = f"ok (v{actual_version})"
    elif actual_version is not None and actual_version != 3:
        result.warnings.append(
            f"NFS version is {actual_version}; this design targets NFSv3 + NLM."
        )
        result.checks["nfs_version"] = f"warn (v{actual_version})"
    else:
        result.checks["nfs_version"] = f"ok (v{actual_version})" if actual_version else "unknown"

    opts = info.option_map()

    # Unsafe options
    for bad in _UNSAFE_OPTIONS:
        if bad in opts:
            result.ok = False
            result.errors.append(
                f"Unsafe mount option {bad!r} is set on {info.target}. "
                f"Remount without {bad} (require hard NFS with remote locking)."
            )
            result.checks[f"option_{bad}"] = "fail"
        else:
            result.checks[f"option_{bad}"] = "ok"

    local_lock = opts.get("local_lock")
    if local_lock is not None and local_lock.lower() in _UNSAFE_LOCAL_LOCK:
        result.ok = False
        result.errors.append(
            f"Unsafe mount option local_lock={local_lock} on {info.target}. "
            "POSIX locks must not be local-only; use NLM (local_lock=none or omit)."
        )
        result.checks["local_lock"] = f"fail ({local_lock})"
    else:
        result.checks["local_lock"] = "ok"

    # hard preferred; soft already failed. If neither hard nor soft, warn.
    if "hard" not in opts and "soft" not in opts and "softerr" not in opts:
        result.warnings.append(
            f"Mount {info.target} does not explicitly list 'hard'; "
            "prefer hard mounts for authoritative queue writes."
        )
        result.checks["hard"] = "warn (not explicit)"
    elif "hard" in opts:
        result.checks["hard"] = "ok"

    # TCP
    proto = opts.get("proto") or opts.get("transport")
    if proto is not None and proto.lower() not in {"tcp", "rdma"}:
        result.ok = False
        result.errors.append(
            f"NFS transport {proto!r} is not acceptable; use proto=tcp."
        )
        result.checks["proto"] = f"fail ({proto})"
    elif proto is None:
        result.warnings.append(
            "NFS proto not listed in mount options; prefer proto=tcp."
        )
        result.checks["proto"] = "warn (not explicit)"
    else:
        result.checks["proto"] = f"ok ({proto})"

    # read-write
    if info.is_readonly:
        result.ok = False
        result.errors.append(f"Queue mount {info.target} is read-only; need rw.")
        result.checks["rw"] = "fail"
    else:
        result.checks["rw"] = "ok"

    # expected server / export
    server, export = info.split_source()
    if cfg.expected_server:
        if server is None:
            result.ok = False
            result.errors.append(
                f"Could not parse NFS server from source {info.source!r}; "
                f"expected server {cfg.expected_server!r}."
            )
            result.checks["server"] = "fail"
        elif server != cfg.expected_server:
            result.ok = False
            result.errors.append(
                f"NFS server mismatch: found {server!r}, "
                f"expected {cfg.expected_server!r}."
            )
            result.checks["server"] = "fail"
        else:
            result.checks["server"] = "ok"
    if cfg.expected_export:
        if export is None:
            result.ok = False
            result.errors.append(
                f"Could not parse NFS export from source {info.source!r}; "
                f"expected export {cfg.expected_export!r}."
            )
            result.checks["export"] = "fail"
        else:
            # Normalize trailing slashes
            got = export.rstrip("/") or "/"
            want = cfg.expected_export.rstrip("/") or "/"
            if got != want and not got.startswith(want.rstrip("/") + "/"):
                # Allow path under export
                result.ok = False
                result.errors.append(
                    f"NFS export mismatch: found {export!r}, "
                    f"expected {cfg.expected_export!r}."
                )
                result.checks["export"] = "fail"
            else:
                result.checks["export"] = "ok"

    # rpc.statd — production (require_statd) fails closed on missing *or* unknown
    statd = check_rpc_statd_running(proc_dir=proc_dir)
    if statd is True:
        result.checks["rpc_statd"] = "ok"
    elif statd is False:
        msg = (
            "rpc.statd does not appear to be running; NFSv3 lock recovery "
            "via NSM may not work. Ensure lockd/statd are active."
        )
        if require_statd:
            result.ok = False
            result.errors.append(msg)
            result.checks["rpc_statd"] = "fail"
        else:
            result.warnings.append(msg)
            result.checks["rpc_statd"] = "warn"
    else:
        msg = (
            "Could not determine rpc.statd status; diagnostic uncertainty "
            "is not a safe pass for production workers."
        )
        if require_statd:
            result.ok = False
            result.errors.append(msg)
            result.checks["rpc_statd"] = "fail (unknown)"
        else:
            result.warnings.append(msg)
            result.checks["rpc_statd"] = "unknown"

    return result


def ensure_queue_mount(
    path: Path,
    *,
    queue_config: QueueConfig | None = None,
    validate_nfs: bool = True,
    **kwargs: object,
) -> MountValidationResult:
    """Validate mount and raise :class:`QueueMountError` on failure."""
    result = validate_queue_mount(
        path,
        queue_config=queue_config,
        validate_nfs=validate_nfs,
        **kwargs,  # type: ignore[arg-type]
    )
    result.raise_if_failed()
    return result

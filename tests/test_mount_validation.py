"""Tests for NFSv3 mount validation (task 013)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from local_transcribe.services.config import QueueConfig
from local_transcribe.services.mount_validation import (
    MountInfo,
    ensure_queue_mount,
    parse_findmnt_json,
    parse_mountinfo,
    validate_queue_mount,
)
from local_transcribe.services.queue_paths import QueueMountError


def _nfs3_mount(
    *,
    options: str = "rw,relatime,vers=3,proto=tcp,hard,local_lock=none",
    source: str = "nas.example.internal:/exports/transcripts",
    target: str = "/opt/md2/music/youtube/transcripts",
) -> MountInfo:
    return MountInfo(
        target=target,
        fstype="nfs",
        source=source,
        options=tuple(o.strip() for o in options.split(",") if o.strip()),
    )


def test_validate_nfs_false_skips(tmp_path: Path) -> None:
    result = validate_queue_mount(tmp_path, validate_nfs=False)
    assert result.ok is True
    assert result.skipped is True
    assert "skipped" in result.checks["nfs_validation"]


def test_safe_nfs3_mount_passes(tmp_path: Path) -> None:
    mount = _nfs3_mount()
    cfg = QueueConfig(
        expected_nfs_version=3,
        expected_server="nas.example.internal",
        expected_export="/exports/transcripts",
    )
    result = validate_queue_mount(
        tmp_path,
        queue_config=cfg,
        mount=mount,
        require_statd=False,
        proc_dir=tmp_path / "empty-proc",
    )
    assert result.ok is True
    assert result.errors == []
    assert result.checks["nfs"] == "ok"
    assert result.checks["nfs_version"].startswith("ok")


@pytest.mark.parametrize(
    "bad_option",
    ["nolock", "soft", "softerr"],
)
def test_unsafe_options_rejected(tmp_path: Path, bad_option: str) -> None:
    base = "rw,vers=3,proto=tcp,hard"
    options = f"{base},{bad_option}"
    mount = _nfs3_mount(options=options)
    result = validate_queue_mount(tmp_path, mount=mount, proc_dir=tmp_path / "no-proc")
    assert result.ok is False
    assert any(bad_option in e for e in result.errors)


@pytest.mark.parametrize("local_lock", ["posix", "all", "flock"])
def test_local_lock_rejected(tmp_path: Path, local_lock: str) -> None:
    options = f"rw,vers=3,proto=tcp,hard,local_lock={local_lock}"
    mount = _nfs3_mount(options=options)
    result = validate_queue_mount(tmp_path, mount=mount, proc_dir=tmp_path / "no-proc")
    assert result.ok is False
    assert any("local_lock" in e for e in result.errors)


def test_readonly_rejected(tmp_path: Path) -> None:
    mount = _nfs3_mount(options="ro,vers=3,proto=tcp,hard")
    result = validate_queue_mount(tmp_path, mount=mount, proc_dir=tmp_path / "no-proc")
    assert result.ok is False
    assert any("read-only" in e for e in result.errors)


def test_wrong_nfs_version_rejected(tmp_path: Path) -> None:
    mount = _nfs3_mount(options="rw,vers=4,proto=tcp,hard")
    # fstype nfs with vers=4
    cfg = QueueConfig(expected_nfs_version=3)
    result = validate_queue_mount(
        tmp_path, queue_config=cfg, mount=mount, proc_dir=tmp_path / "no-proc"
    )
    assert result.ok is False
    assert any("version mismatch" in e for e in result.errors)


def test_nfs4_fstype_version_mismatch(tmp_path: Path) -> None:
    mount = MountInfo(
        target="/mnt",
        fstype="nfs4",
        source="nas:/export",
        options=("rw", "proto=tcp", "hard"),
    )
    cfg = QueueConfig(expected_nfs_version=3)
    result = validate_queue_mount(
        tmp_path, queue_config=cfg, mount=mount, proc_dir=tmp_path / "no-proc"
    )
    assert result.ok is False


def test_non_nfs_rejected(tmp_path: Path) -> None:
    mount = MountInfo(
        target="/",
        fstype="ext4",
        source="/dev/sda1",
        options=("rw", "relatime"),
    )
    result = validate_queue_mount(tmp_path, mount=mount, proc_dir=tmp_path / "no-proc")
    assert result.ok is False
    assert any("expected NFS" in e for e in result.errors)


def test_server_export_mismatch(tmp_path: Path) -> None:
    mount = _nfs3_mount(source="other-nas:/wrong/export")
    cfg = QueueConfig(
        expected_nfs_version=3,
        expected_server="nas.example.internal",
        expected_export="/exports/transcripts",
    )
    result = validate_queue_mount(
        tmp_path, queue_config=cfg, mount=mount, proc_dir=tmp_path / "no-proc"
    )
    assert result.ok is False
    assert any("server mismatch" in e for e in result.errors)


def test_ensure_raises_queue_mount_error(tmp_path: Path) -> None:
    mount = _nfs3_mount(options="rw,vers=3,proto=tcp,soft")
    with pytest.raises(QueueMountError, match="soft"):
        ensure_queue_mount(tmp_path, mount=mount, proc_dir=tmp_path / "no-proc")


def test_parse_findmnt_json() -> None:
    payload = {
        "filesystems": [
            {
                "target": "/mnt/nfs",
                "source": "nas:/data",
                "fstype": "nfs",
                "options": "rw,vers=3,proto=tcp,hard",
            }
        ]
    }
    info = parse_findmnt_json(json.dumps(payload))
    assert info is not None
    assert info.fstype == "nfs"
    assert info.nfs_version == 3
    assert info.split_source() == ("nas", "/data")


def test_parse_mountinfo_longest_prefix(tmp_path: Path) -> None:
    # Synthetic mountinfo covering tmp_path
    root = str(tmp_path.resolve())
    text = (
        f"21 1 0:20 / / rw,relatime - ext4 /dev/sda1 rw\n"
        f"99 21 0:50 / {root} rw,relatime - nfs nas:/exports/transcripts "
        f"rw,vers=3,proto=tcp,hard,local_lock=none\n"
    )
    nested = tmp_path / "queue" / "sub"
    nested.mkdir(parents=True)
    info = parse_mountinfo(text, nested)
    assert info is not None
    assert info.fstype == "nfs"
    assert info.source == "nas:/exports/transcripts"
    assert info.nfs_version == 3


def test_udp_proto_rejected(tmp_path: Path) -> None:
    mount = _nfs3_mount(options="rw,vers=3,proto=udp,hard")
    result = validate_queue_mount(tmp_path, mount=mount, proc_dir=tmp_path / "no-proc")
    assert result.ok is False
    assert any("transport" in e or "proto" in e for e in result.errors)


def test_empty_local_dir_non_nfs_hint(tmp_path: Path) -> None:
    empty = tmp_path / "mntpoint"
    empty.mkdir()
    mount = MountInfo(
        target=str(empty),
        fstype="ext4",
        source="/dev/sda1",
        options=("rw",),
    )
    result = validate_queue_mount(empty, mount=mount, proc_dir=tmp_path / "no-proc")
    assert result.ok is False
    assert any("unmounted" in e.lower() or "empty local" in e.lower() for e in result.errors)


def test_identity_not_checked_here(tmp_path: Path) -> None:
    """Mount validation does not require queue.id (that is task 011)."""
    # empty dir, no queue.id — still only fails for non-NFS, not identity
    mount = _nfs3_mount()
    result = validate_queue_mount(
        tmp_path, mount=mount, require_statd=False, proc_dir=tmp_path / "no-proc"
    )
    assert result.ok is True
    assert not any("queue.id" in e for e in result.errors)


def test_require_statd_missing_is_fatal(tmp_path: Path) -> None:
    mount = _nfs3_mount()
    proc = tmp_path / "proc"
    proc.mkdir()
    # empty /proc with no rpc.statd → False
    result = validate_queue_mount(
        tmp_path, mount=mount, require_statd=True, proc_dir=proc
    )
    assert result.ok is False
    assert any("rpc.statd" in e for e in result.errors)
    assert result.checks["rpc_statd"] == "fail"


def test_require_statd_unknown_is_fatal(tmp_path: Path) -> None:
    """Diagnostic uncertainty must not pass production validation."""
    mount = _nfs3_mount()
    # Non-existent proc_dir → indeterminate
    result = validate_queue_mount(
        tmp_path,
        mount=mount,
        require_statd=True,
        proc_dir=tmp_path / "missing-proc",
    )
    assert result.ok is False
    assert any("uncertainty" in e.lower() or "could not determine" in e.lower() for e in result.errors)
    assert "fail" in result.checks["rpc_statd"]


def test_require_statd_present_passes(tmp_path: Path) -> None:
    mount = _nfs3_mount()
    proc = tmp_path / "proc"
    pid = proc / "1234"
    pid.mkdir(parents=True)
    (pid / "cmdline").write_bytes(b"/usr/sbin/rpc.statd\x00")
    (pid / "comm").write_text("rpc.statd\n", encoding="utf-8")
    result = validate_queue_mount(
        tmp_path, mount=mount, require_statd=True, proc_dir=proc
    )
    assert result.ok is True
    assert result.checks["rpc_statd"] == "ok"

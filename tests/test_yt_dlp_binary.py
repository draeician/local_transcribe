"""Tests for runtime yt-dlp binary preference."""

from __future__ import annotations

import os
from pathlib import Path

from local_transcribe.utils.ytdlp_update import (
    find_yt_dlp_binary,
    runtime_bin_dir,
    yt_dlp_invocation,
)


def test_find_yt_dlp_binary_prefers_venv_sibling(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    python = bin_dir / "python"
    yt = bin_dir / "yt-dlp"
    python.write_text("#!/bin/sh\n", encoding="utf-8")
    yt.write_text("#!/bin/sh\n", encoding="utf-8")
    python.chmod(0o755)
    yt.chmod(0o755)

    found = find_yt_dlp_binary(str(python))
    assert Path(found).resolve() == yt.resolve()


def test_find_yt_dlp_binary_falls_back_to_path(
    tmp_path: Path, monkeypatch
) -> None:
    bin_dir = tmp_path / "venv"
    bin_dir.mkdir()
    python = bin_dir / "python"
    python.write_text("#!/bin/sh\n", encoding="utf-8")
    python.chmod(0o755)

    path_yt = tmp_path / "path" / "yt-dlp"
    path_yt.parent.mkdir()
    path_yt.write_text("#!/bin/sh\n", encoding="utf-8")
    path_yt.chmod(0o755)

    monkeypatch.setenv("PATH", str(path_yt.parent) + os.pathsep + os.environ.get("PATH", ""))
    # Clear any accidental sibling
    found = find_yt_dlp_binary(str(python))
    assert Path(found).resolve() == path_yt.resolve()


def test_yt_dlp_invocation_module_fallback(tmp_path: Path, monkeypatch) -> None:
    bin_dir = tmp_path / "empty"
    bin_dir.mkdir()
    python = bin_dir / "python"
    python.write_text("#!/bin/sh\n", encoding="utf-8")
    python.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path / "no-such-bin"))

    inv = yt_dlp_invocation(str(python))
    assert inv == [str(python), "-m", "yt_dlp"]


def test_runtime_bin_dir(tmp_path: Path) -> None:
    py = tmp_path / "bin" / "python"
    py.parent.mkdir()
    py.write_text("x", encoding="utf-8")
    assert runtime_bin_dir(str(py)) == py.parent.resolve()


def test_runtime_bin_dir_does_not_follow_python_symlink(tmp_path: Path) -> None:
    """pipx uses bin/python -> python3; must not resolve into /usr/bin."""
    bin_dir = tmp_path / "venv" / "bin"
    bin_dir.mkdir(parents=True)
    target = bin_dir / "python3"
    target.write_text("#!/bin/sh\n", encoding="utf-8")
    target.chmod(0o755)
    link = bin_dir / "python"
    link.symlink_to("python3")
    yt = bin_dir / "yt-dlp"
    yt.write_text("#!/bin/sh\n", encoding="utf-8")
    yt.chmod(0o755)

    assert runtime_bin_dir(str(link)) == bin_dir
    assert Path(find_yt_dlp_binary(str(link))).resolve() == yt.resolve()

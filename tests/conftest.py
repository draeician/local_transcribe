"""Shared pytest configuration."""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from pathlib import Path

import pytest

_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


def strip_ansi(text: str) -> str:
    """Remove ANSI SGR/CSI escapes from captured CLI output.

    ``Console(force_terminal=True)`` styles even captured output, which can
    split literal substrings (for example ``yt-dlp\\x1b[1m[\\x1b[0m...``).
    Tests assert on the underlying text, not on terminal formatting.
    """
    return _ANSI_RE.sub("", text)


@pytest.fixture
def plain() -> Callable[[str], str]:
    """Return a callable that strips ANSI escapes from captured output."""
    return strip_ansi


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "nfs: real NFSv3 lab tests (require LT_NFS_QUEUE_DIR)",
    )


@pytest.fixture
def nfs_queue_dir() -> Path:
    """Queue root on a real NFSv3 mount (skipped when unset)."""
    raw = os.environ.get("LT_NFS_QUEUE_DIR", "").strip()
    if not raw:
        pytest.skip("LT_NFS_QUEUE_DIR not set")
    path = Path(raw).expanduser().resolve()
    if not path.is_dir():
        pytest.skip(f"LT_NFS_QUEUE_DIR is not a directory: {path}")
    return path

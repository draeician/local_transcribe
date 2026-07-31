"""Shared pytest configuration."""

from __future__ import annotations

import os
from pathlib import Path

import pytest


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

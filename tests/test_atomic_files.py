"""Tests for atomic file helpers (task 012)."""

from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from local_transcribe.services import atomic_files
from local_transcribe.services.atomic_files import (
    AtomicFileError,
    atomic_write_json,
    link_publish,
    link_publish_json,
    write_json_tmp,
)
from local_transcribe.utils.files import safe_write_json


def test_atomic_write_json_roundtrip(tmp_path: Path) -> None:
    target = tmp_path / "state.json"
    atomic_write_json(target, {"schema_version": 1, "value": "ok"})
    data = json.loads(target.read_text(encoding="utf-8"))
    assert data == {"schema_version": 1, "value": "ok"}


def test_atomic_write_json_replaces_without_truncating_final(tmp_path: Path) -> None:
    target = tmp_path / "state.json"
    atomic_write_json(target, {"n": 1, "payload": "x" * 100})
    atomic_write_json(target, {"n": 2, "payload": "y" * 50})
    data = json.loads(target.read_text(encoding="utf-8"))
    assert data["n"] == 2
    assert data["payload"] == "y" * 50


def test_atomic_write_json_cleans_tmp_on_validation_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "state.json"

    def bad_load(handle):  # type: ignore[no-untyped-def]
        raise json.JSONDecodeError("boom", "doc", 0)

    monkeypatch.setattr(atomic_files.json, "load", bad_load)
    with pytest.raises(json.JSONDecodeError):
        atomic_write_json(target, {"a": 1})

    assert not target.exists()
    leftovers = list(tmp_path.glob(".*"))
    assert leftovers == []


def test_link_publish_creates_dest(tmp_path: Path) -> None:
    tmp_file = write_json_tmp(tmp_path, {"id": "a"}, name_hint="job.json")
    dest = tmp_path / "pending" / "job.json"
    assert link_publish(tmp_file, dest) is True
    assert dest.is_file()
    assert json.loads(dest.read_text(encoding="utf-8"))["id"] == "a"
    # Caller cleans tmp
    tmp_file.unlink()
    assert not tmp_file.exists()
    # Dest remains (hard link content)
    assert dest.is_file()


def test_link_publish_fails_closed_when_dest_exists(tmp_path: Path) -> None:
    dest = tmp_path / "keys" / "youtube-x.json"
    assert link_publish_json(dest, {"source_key": "youtube:x", "generation": 1}) is True
    assert link_publish_json(dest, {"source_key": "youtube:x", "generation": 99}) is False
    data = json.loads(dest.read_text(encoding="utf-8"))
    assert data["generation"] == 1


def test_link_publish_concurrent_one_winner(tmp_path: Path) -> None:
    dest = tmp_path / "pending" / "same.json"
    dest.parent.mkdir(parents=True)

    def attempt(i: int) -> bool:
        return link_publish_json(
            dest,
            {"writer": i, "token": f"w{i}"},
            tmp_dir=tmp_path / "tmp",
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(attempt, range(8)))

    assert sum(1 for r in results if r) == 1
    assert sum(1 for r in results if not r) == 7
    assert dest.is_file()
    body = json.loads(dest.read_text(encoding="utf-8"))
    assert "writer" in body


def test_link_publish_missing_source_raises(tmp_path: Path) -> None:
    with pytest.raises(AtomicFileError, match="not a file"):
        link_publish(tmp_path / "missing.json", tmp_path / "dest.json")


def test_tmp_cleaned_by_link_publish_json(tmp_path: Path) -> None:
    dest = tmp_path / "out.json"
    tmp_dir = tmp_path / "tmp"
    assert link_publish_json(dest, {"ok": True}, tmp_dir=tmp_dir) is True
    # No leftover temps under tmp_dir
    assert list(tmp_dir.iterdir()) == [] or all(
        not p.name.startswith(".") for p in tmp_dir.iterdir()
    )
    # Stronger: no .*.tmp.* files
    assert list(tmp_dir.glob(".*.tmp.*")) == []


def test_safe_write_json_unchanged_semantics(tmp_path: Path) -> None:
    """Queue helpers must not alter legacy safe_write_json behavior."""
    path = tmp_path / "legacy.json"
    assert safe_write_json(path, {"legacy": True}) is True
    assert json.loads(path.read_text(encoding="utf-8"))["legacy"] is True
    # Still the in-place writer (module still exportable and callable)
    assert safe_write_json(path, {"legacy": False}) is True
    assert json.loads(path.read_text(encoding="utf-8"))["legacy"] is False


def test_atomic_write_does_not_import_mutate_safe_write() -> None:
    import inspect

    import local_transcribe.utils.files as files_mod

    src = inspect.getsource(files_mod.safe_write_json)
    assert "open(path" in src or "open(path," in src or 'open(path' in src
    # atomic_files must not re-export or wrap safe_write_json as the authority API
    assert not hasattr(atomic_files, "safe_write_json")

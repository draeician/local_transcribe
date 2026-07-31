"""Fault-injection tests for atomic worker/producer state transitions (task 029)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from local_transcribe.services import atomic_files
from local_transcribe.services.atomic_files import (
    AtomicFileError,
    atomic_state_transition,
    rename_exclusive,
)
from local_transcribe.services.queue_models import ExecutionError, utc_now_iso
from local_transcribe.services.queue_paths import initialize_queue_layout
from local_transcribe.services.queue_store import QueueStore
from local_transcribe.services.worker import (
    claim_execution,
    complete_execution,
    fail_or_retry,
    promote_retries,
    recover_processing,
)


def _enqueue(queue: Path, url: str = "https://youtu.be/dQw4w9WgXcQ"):
    store = QueueStore(queue)
    result = store.enqueue(url, priority=100)
    assert result.execution is not None
    return result.execution


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _authority_files(queue: Path) -> list[Path]:
    files: list[Path] = []
    for name in (
        "pending",
        "processing",
        "retry",
        "completed",
        "failed",
        "cancelled",
    ):
        d = queue / name
        if d.is_dir():
            files.extend(sorted(d.glob("*.json")))
    return files


def test_rename_exclusive_moves_and_fsyncs(tmp_path: Path) -> None:
    src_dir = tmp_path / "a"
    dest_dir = tmp_path / "b"
    src_dir.mkdir()
    dest_dir.mkdir()
    src = src_dir / "job.json"
    dest = dest_dir / "job.json"
    src.write_text('{"ok": true}\n', encoding="utf-8")

    synced: list[Path] = []
    original = atomic_files.fsync_directory

    def tracking(directory: Path) -> None:
        synced.append(Path(directory))
        return original(directory)

    atomic_files.fsync_directory = tracking  # type: ignore[assignment]
    try:
        rename_exclusive(src, dest)
    finally:
        atomic_files.fsync_directory = original  # type: ignore[assignment]

    assert not src.exists()
    assert dest.is_file()
    assert dest_dir in synced
    assert src_dir in synced


def test_rename_exclusive_destination_collision_fails_safely(tmp_path: Path) -> None:
    src = tmp_path / "pending" / "job.json"
    dest = tmp_path / "cancelled" / "job.json"
    src.parent.mkdir(parents=True)
    dest.parent.mkdir(parents=True)
    src.write_text('{"v": 1}\n', encoding="utf-8")
    dest.write_text('{"v": "existing"}\n', encoding="utf-8")

    with pytest.raises(AtomicFileError, match="already exists"):
        rename_exclusive(src, dest)

    assert src.is_file()
    assert _read_json(src)["v"] == 1
    assert _read_json(dest)["v"] == "existing"


def test_atomic_state_transition_happy_path(tmp_path: Path) -> None:
    src = tmp_path / "pending" / "e.json"
    dest = tmp_path / "processing" / "e.json"
    src.parent.mkdir(parents=True)
    src.write_text('{"status": "pending", "n": 1}\n', encoding="utf-8")

    atomic_state_transition(src, dest, {"status": "processing", "n": 2})

    assert not src.exists()
    assert _read_json(dest) == {"status": "processing", "n": 2}


def test_fault_before_rename_leaves_source_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = tmp_path / "pending" / "e.json"
    dest = tmp_path / "processing" / "e.json"
    src.parent.mkdir(parents=True)
    src.write_text('{"status": "pending"}\n', encoding="utf-8")

    def boom(*_a: Any, **_k: Any) -> None:
        raise OSError("injected: before link")

    monkeypatch.setattr(atomic_files.os, "link", boom)

    with pytest.raises(AtomicFileError):
        atomic_state_transition(src, dest, {"status": "processing"})

    assert src.is_file()
    assert not dest.exists()
    assert _read_json(src)["status"] == "pending"


def test_fault_after_rename_before_write_leaves_file_in_dest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = tmp_path / "processing" / "e.json"
    dest = tmp_path / "completed" / "e.json"
    src.parent.mkdir(parents=True)
    src.write_text('{"status": "processing", "attempts": 1}\n', encoding="utf-8")

    real_write = atomic_files.atomic_write_json

    def boom(path: Path, data: Any) -> None:
        raise RuntimeError("injected: after rename before write")

    monkeypatch.setattr(atomic_files, "atomic_write_json", boom)

    with pytest.raises(RuntimeError, match="injected"):
        atomic_files.atomic_state_transition(
            src, dest, {"status": "completed", "attempts": 1}
        )

    assert not src.exists()
    assert dest.is_file()
    # Directory membership advanced; metadata may still be pre-write.
    assert _read_json(dest)["status"] == "processing"
    # Ensure we did not leave a truncated final via in-place open.
    text = dest.read_text(encoding="utf-8")
    json.loads(text)


def test_fault_during_atomic_write_does_not_truncate_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = tmp_path / "processing" / "e.json"
    dest = tmp_path / "failed" / "e.json"
    src.parent.mkdir(parents=True)
    original = {"status": "processing", "payload": "x" * 200}
    src.write_text(json.dumps(original) + "\n", encoding="utf-8")

    # Succeed rename, fail mid atomic_write after tmp created but before replace.
    real_replace = os.replace

    def flaky_replace(tmp: str | os.PathLike[str], path: str | os.PathLike[str]) -> None:
        raise OSError("injected: before os.replace")

    monkeypatch.setattr(atomic_files.os, "replace", flaky_replace)

    with pytest.raises(OSError, match="injected"):
        atomic_state_transition(
            src, dest, {"status": "failed", "payload": "y" * 50}
        )

    assert not src.exists()
    assert dest.is_file()
    data = _read_json(dest)
    assert data["status"] == "processing"
    assert data["payload"] == "x" * 200
    # No leftover truncated finals named like the authority file.
    assert dest.stat().st_size >= len(json.dumps(original))


def test_claim_execution_atomic(tmp_path: Path) -> None:
    queue = tmp_path / "q"
    initialize_queue_layout(queue)
    ex = _enqueue(queue)
    claimed = claim_execution(queue, ex)
    assert claimed is not None
    assert claimed.status == "processing"
    assert claimed.attempts == 1
    path = queue / "processing" / f"{ex.execution_id}.json"
    assert path.is_file()
    assert not (queue / "pending" / f"{ex.execution_id}.json").exists()
    assert _read_json(path)["status"] == "processing"


def test_claim_destination_collision_returns_none(tmp_path: Path) -> None:
    queue = tmp_path / "q"
    initialize_queue_layout(queue)
    ex = _enqueue(queue)
    # Pre-create colliding processing file
    dest = queue / "processing" / f"{ex.execution_id}.json"
    dest.write_text('{"status": "processing", "execution_id": "other"}\n', encoding="utf-8")
    assert claim_execution(queue, ex) is None
    assert (queue / "pending" / f"{ex.execution_id}.json").is_file()
    assert _read_json(dest)["execution_id"] == "other"


def test_complete_and_fail_or_retry_atomic(tmp_path: Path) -> None:
    queue = tmp_path / "q"
    initialize_queue_layout(queue)
    ex = _enqueue(queue)
    claimed = claim_execution(queue, ex)
    assert claimed is not None
    complete_execution(queue, claimed, output_path="/tmp/out.json")
    assert list((queue / "completed").glob("*.json"))
    assert not list((queue / "processing").glob("*.json"))
    data = _read_json(next((queue / "completed").glob("*.json")))
    assert data["status"] == "completed"
    assert data["output_path"] == "/tmp/out.json"

    queue2 = tmp_path / "q2"
    initialize_queue_layout(queue2)
    ex2 = _enqueue(queue2, "https://youtu.be/aaaaaaaaaaa")
    claimed2 = claim_execution(queue2, ex2)
    assert claimed2 is not None
    fail_or_retry(
        queue2,
        claimed2,
        ExecutionError(
            category="rate_limited",
            message="429",
            retryable=True,
            occurred_at=utc_now_iso(),
        ),
    )
    assert list((queue2 / "retry").glob("*.json"))
    retry_data = _read_json(next((queue2 / "retry").glob("*.json")))
    assert retry_data["status"] == "retry"


def test_recover_and_promote_use_atomic_transition(tmp_path: Path) -> None:
    queue = tmp_path / "q"
    initialize_queue_layout(queue)
    ex = _enqueue(queue)
    claimed = claim_execution(queue, ex)
    assert claimed is not None
    n = recover_processing(queue)
    assert n == 1
    assert list((queue / "retry").glob("*.json"))
    assert not list((queue / "processing").glob("*.json"))
    retry_data = _read_json(next((queue / "retry").glob("*.json")))
    assert retry_data["status"] == "retry"
    assert retry_data["error"]["category"] == "worker_interrupted"

    n2 = promote_retries(queue)
    assert n2 == 1
    assert list((queue / "pending").glob("*.json"))
    assert not list((queue / "retry").glob("*.json"))
    assert _read_json(next((queue / "pending").glob("*.json")))["status"] == "pending"


def test_cancel_pending_atomic_and_collision_safe(tmp_path: Path) -> None:
    queue = tmp_path / "q"
    initialize_queue_layout(queue)
    store = QueueStore(queue)
    ex = _enqueue(queue)
    cancelled = store.cancel_pending(ex.execution_id)
    assert cancelled.status == "cancelled"
    assert (queue / "cancelled" / f"{ex.execution_id}.json").is_file()
    assert not (queue / "pending" / f"{ex.execution_id}.json").exists()

    ex2 = _enqueue(queue, "https://youtu.be/bbbbbbbbbbb")
    dest = queue / "cancelled" / f"{ex2.execution_id}.json"
    dest.write_text('{"status": "cancelled", "keep": true}\n', encoding="utf-8")
    with pytest.raises(AtomicFileError):
        store.cancel_pending(ex2.execution_id)
    assert (queue / "pending" / f"{ex2.execution_id}.json").is_file()
    assert _read_json(dest)["keep"] is True


def test_no_copy_unlink_normal_path_uses_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Normal transitions must use link-based exclusive move, not write+unlink."""
    queue = tmp_path / "q"
    initialize_queue_layout(queue)
    ex = _enqueue(queue)
    links: list[tuple[str, str]] = []
    real_link = os.link

    def tracking_link(src: str | os.PathLike[str], dst: str | os.PathLike[str]) -> None:
        links.append((str(src), str(dst)))
        return real_link(src, dst)

    monkeypatch.setattr(atomic_files.os, "link", tracking_link)

    claimed = claim_execution(queue, ex)
    assert claimed is not None
    assert links, "claim_execution must use os.link for exclusive rename"
    complete_execution(queue, claimed, output_path=None)
    assert len(links) >= 2


def test_worker_transitions_survive_inject_at_each_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Inject failure at rename and write boundaries; authority stays parseable."""
    queue = tmp_path / "q"
    initialize_queue_layout(queue)
    ex = _enqueue(queue)
    eid = ex.execution_id

    # Boundary 1: fail claim rename
    monkeypatch.setattr(
        atomic_files.os,
        "link",
        lambda *_a, **_k: (_ for _ in ()).throw(OSError("inject claim")),
    )
    assert claim_execution(queue, ex) is None
    assert (queue / "pending" / f"{eid}.json").is_file()
    monkeypatch.undo()

    claimed = claim_execution(queue, ex)
    assert claimed is not None

    # Boundary 2: fail complete after rename, before write
    real_write = atomic_files.atomic_write_json
    calls = {"n": 0}

    def write_once(path: Path, data: Any) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("inject complete write")
        return real_write(path, data)

    monkeypatch.setattr(atomic_files, "atomic_write_json", write_once)
    with pytest.raises(RuntimeError, match="inject complete write"):
        complete_execution(queue, claimed, output_path="/out.json")
    # File should be in completed/ (rename succeeded) with parseable JSON
    completed = queue / "completed" / f"{eid}.json"
    assert completed.is_file()
    json.loads(completed.read_text(encoding="utf-8"))
    # No dual authority in processing
    assert not (queue / "processing" / f"{eid}.json").exists()
    for path in _authority_files(queue):
        json.loads(path.read_text(encoding="utf-8"))

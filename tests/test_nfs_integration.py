"""Real NFSv3 lab tests (task 036).

Requires::

    export LT_NFS_QUEUE_DIR=/path/on/nfsv3/queue
    pytest -q -m nfs

With ``local_lock=none``, dual-process locks on one client still go through
NLM to the server. True two-host reboot cases remain in the operator checklist.
"""

from __future__ import annotations

import json
import multiprocessing as mp
import os
import signal
import subprocess
import sys
import time
import uuid
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pytest

from local_transcribe.services.atomic_files import atomic_state_transition
from local_transcribe.services.mount_validation import validate_queue_mount
from local_transcribe.services.queue_models import utc_now_iso
from local_transcribe.services.queue_paths import initialize_queue_layout
from local_transcribe.services.queue_store import QueueStore
from local_transcribe.services.source_reservations import read_reservation
from local_transcribe.services.transcript_publish import (
    TranscriptPublishError,
    publish_transcript,
)
from local_transcribe.services.worker_lock import WorkerLock, worker_lock_path

pytestmark = pytest.mark.nfs

_HELPER = Path(__file__).resolve().parent / "nfs_lock_helper.py"


def _fresh_queue(nfs_queue_dir: Path, name: str) -> Path:
    # Unique per run — NFS may leave .nfs* deleters that break rmtree.
    q = nfs_queue_dir / f"{name}-{uuid.uuid4().hex[:8]}"
    initialize_queue_layout(q)
    return q


def _helper_env() -> dict[str, str]:
    env = os.environ.copy()
    src = str(Path(__file__).resolve().parents[1] / "src")
    prev = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = src if not prev else f"{src}{os.pathsep}{prev}"
    return env


def _start_holder(lock_path: Path, *, mode: str = "hold", hold_seconds: float = 60.0):
    args = [sys.executable, str(_HELPER), str(lock_path), mode]
    if mode == "hold":
        args.append(str(hold_seconds))
    proc = subprocess.Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=_helper_env(),
    )
    assert proc.stdout is not None
    line = proc.stdout.readline().strip()
    if line != "HELD":
        err = proc.stderr.read() if proc.stderr else ""
        proc.kill()
        raise AssertionError(f"holder failed to acquire: {line!r} stderr={err!r}")
    return proc


def _try_lock(lock_path: Path) -> str:
    proc = subprocess.run(
        [sys.executable, str(_HELPER), str(lock_path), "try"],
        capture_output=True,
        text=True,
        env=_helper_env(),
        timeout=60,
        check=False,
    )
    return proc.stdout.strip() or f"error:{proc.stderr.strip()}"


def _enqueue_once(args: tuple[str, str]) -> str:
    queue_dir, url = args
    store = QueueStore(Path(queue_dir))
    return store.enqueue(url, origin="nfs-lab", priority=10).kind


def _attr_cache_writer(path: str, ready_path: str) -> None:
    Path(path).write_text('{"ok": true}\n', encoding="utf-8")
    Path(ready_path).write_text("1\n", encoding="utf-8")


def test_mount_validation_real_nfs(nfs_queue_dir: Path) -> None:
    q = _fresh_queue(nfs_queue_dir, "mount-check")
    result = validate_queue_mount(q, validate_nfs=True, require_statd=True)
    assert result.ok, result.errors
    assert result.checks.get("nfs") == "ok"
    assert "ok" in str(result.checks.get("nfs_version", ""))


def test_nlm_contention_two_processes(nfs_queue_dir: Path) -> None:
    q = _fresh_queue(nfs_queue_dir, "nlm-contention")
    lock_path = worker_lock_path(q)
    holder = _start_holder(lock_path, hold_seconds=30)
    try:
        assert _try_lock(lock_path) == "BUSY"
    finally:
        holder.terminate()
        holder.wait(timeout=10)
    assert _try_lock(lock_path) == "OK"


def test_nlm_lock_after_normal_exit(nfs_queue_dir: Path) -> None:
    q = _fresh_queue(nfs_queue_dir, "nlm-exit")
    path = worker_lock_path(q)
    lock = WorkerLock.acquire(path)
    lock.release()
    again = WorkerLock.acquire(path)
    again.release()


def test_nlm_lock_after_kill(nfs_queue_dir: Path) -> None:
    q = _fresh_queue(nfs_queue_dir, "nlm-kill")
    lock_path = worker_lock_path(q)
    holder = _start_holder(lock_path, mode="hold-kill")
    try:
        assert _try_lock(lock_path) == "BUSY"
        os.kill(holder.pid, signal.SIGKILL)
        holder.wait(timeout=10)
    finally:
        if holder.poll() is None:
            holder.kill()
            holder.wait(timeout=5)
    deadline = time.time() + 60
    last = "BUSY"
    while time.time() < deadline:
        last = _try_lock(lock_path)
        if last == "OK":
            break
        time.sleep(0.5)
    assert last == "OK", f"lock not reclaimable after kill: {last}"


def test_cross_process_duplicate_enqueue(nfs_queue_dir: Path) -> None:
    q = _fresh_queue(nfs_queue_dir, "dup-enqueue")
    url = "https://youtu.be/nfsLab00001"
    args = [(str(q), url) for _ in range(6)]
    ctx = mp.get_context("spawn")
    with ProcessPoolExecutor(max_workers=4, mp_context=ctx) as pool:
        kinds = list(pool.map(_enqueue_once, args))
    assert kinds.count("enqueued") == 1
    assert kinds.count("existing_active") == 5
    pending = list((q / "pending").glob("*.json"))
    assert len(pending) == 1
    res = read_reservation(q, "youtube:nfsLab00001")
    assert res is not None
    assert res.current_execution_id == pending[0].stem


def test_pending_to_processing_rename(nfs_queue_dir: Path) -> None:
    q = _fresh_queue(nfs_queue_dir, "rename-pp")
    store = QueueStore(q)
    result = store.enqueue("https://youtu.be/nfsLab00002")
    assert result.execution is not None
    eid = result.execution.execution_id
    src = q / "pending" / f"{eid}.json"
    dest = q / "processing" / f"{eid}.json"
    data = json.loads(src.read_text(encoding="utf-8"))
    data["status"] = "processing"
    data["updated_at"] = utc_now_iso()
    atomic_state_transition(src, dest, data)
    assert not src.exists()
    assert dest.is_file()
    loaded = json.loads(dest.read_text(encoding="utf-8"))
    assert loaded["status"] == "processing"


def test_processing_to_completed_rename(nfs_queue_dir: Path) -> None:
    q = _fresh_queue(nfs_queue_dir, "rename-pc")
    store = QueueStore(q)
    result = store.enqueue("https://youtu.be/nfsLab00003")
    assert result.execution is not None
    eid = result.execution.execution_id
    pending = q / "pending" / f"{eid}.json"
    processing = q / "processing" / f"{eid}.json"
    completed = q / "completed" / f"{eid}.json"
    data = json.loads(pending.read_text(encoding="utf-8"))
    data["status"] = "processing"
    atomic_state_transition(pending, processing, data)
    data["status"] = "completed"
    data["completed_at"] = utc_now_iso()
    atomic_state_transition(processing, completed, data)
    assert not processing.exists()
    assert completed.is_file()
    assert json.loads(completed.read_text(encoding="utf-8"))["status"] == "completed"


def test_interrupted_transcript_publication(nfs_queue_dir: Path) -> None:
    q = _fresh_queue(nfs_queue_dir, "tx-publish")
    root = q / "transcripts"
    root.mkdir()
    # Simulate crash: leave a tmp sibling, then complete a real publish.
    stale = root / ".aaaaaaaaaaa.json.tmp.dead"
    stale.write_text("{broken", encoding="utf-8")
    payload = {
        "transcript": "hello from nfs lab",
        "metadata": {"id": "aaaaaaaaaaa"},
    }
    published = publish_transcript(
        root,
        source_key="youtube:aaaaaaaaaaa",
        payload=payload,
        generation=1,
    )
    assert published.path.is_file()
    assert json.loads(published.path.read_text(encoding="utf-8"))["transcript"]
    # Same generation with valid payload is idempotent (no truncate rewrite).
    again = publish_transcript(
        root,
        source_key="youtube:aaaaaaaaaaa",
        payload=payload,
        generation=1,
    )
    assert again.path == published.path
    # Newer generation wins; older cannot overwrite.
    publish_transcript(
        root,
        source_key="youtube:aaaaaaaaaaa",
        payload={
            "transcript": "gen2",
            "metadata": {"id": "aaaaaaaaaaa"},
        },
        generation=2,
    )
    with pytest.raises(TranscriptPublishError):
        publish_transcript(
            root,
            source_key="youtube:aaaaaaaaaaa",
            payload={
                "transcript": "old",
                "metadata": {"id": "aaaaaaaaaaa"},
            },
            generation=1,
        )


def test_attribute_cache_open_to_close(nfs_queue_dir: Path) -> None:
    """Directory listing may be stale; open-to-close revalidation sees writes."""
    q = _fresh_queue(nfs_queue_dir, "attr-cache")
    target = q / "pending" / "attr-cache-probe.json"
    ready = q / "tmp" / "attr-ready"
    ready.parent.mkdir(parents=True, exist_ok=True)
    if ready.exists():
        ready.unlink()
    before = {p.name for p in (q / "pending").glob("*.json")}
    assert "attr-cache-probe.json" not in before
    ctx = mp.get_context("spawn")
    proc = ctx.Process(target=_attr_cache_writer, args=(str(target), str(ready)))
    proc.start()
    proc.join(timeout=30)
    assert proc.exitcode == 0
    assert ready.is_file()
    deadline = time.time() + 30
    seen = False
    while time.time() < deadline:
        try:
            with target.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
            if data.get("ok") is True:
                seen = True
                break
        except (OSError, json.JSONDecodeError):
            time.sleep(0.1)
    assert seen


def test_doctor_paths_accept_lab_queue(nfs_queue_dir: Path) -> None:
    q = _fresh_queue(nfs_queue_dir, "doctor")
    result = validate_queue_mount(q, validate_nfs=True, require_statd=True)
    assert result.ok, result.errors
    # Queue identity file present after init
    assert (q / "queue.id").is_file()

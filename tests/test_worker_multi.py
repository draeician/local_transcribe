"""Multi-worker claim, slot cap, recovery, and pre-0.9 lock exclusion."""

from __future__ import annotations

import json
import multiprocessing as mp
import os
import signal
import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

from local_transcribe.cli import app
from local_transcribe.services.config import load_config
from local_transcribe.services.queue_paths import initialize_queue_layout
from local_transcribe.services.queue_store import QueueStore
from local_transcribe.services.worker import (
    claim_execution,
    recover_processing,
    release_claim,
    run_worker,
)
from local_transcribe.services.worker_lock import (
    WorkerAlreadyActive,
    WorkerLock,
    worker_lock_path,
)
from local_transcribe.services.worker_slots import (
    format_worker_lines,
    inspect_workers,
    take_claim_lock,
)

_URLS = (
    "https://youtu.be/dQw4w9WgXcQ",
    "https://youtu.be/aaaaaaaaaaa",
)


def _enqueue(queue: Path, url: str) -> str:
    result = QueueStore(queue).enqueue(url, origin="test", priority=10)
    assert result.execution is not None
    return result.execution.execution_id


def _claim_and_sleep(queue_dir: str, ready_path: str) -> None:
    queue = Path(queue_dir)
    pending = next((queue / "pending").glob("*.json"))
    from local_transcribe.services.queue_models import Execution

    execution = Execution.from_dict(json.loads(pending.read_text(encoding="utf-8")))
    claimed = claim_execution(queue, execution, hold_claim_lock=True, worker_slot=0)
    Path(ready_path).write_text(
        "NONE" if claimed is None else claimed.execution_id,
        encoding="utf-8",
    )
    if claimed is None:
        return
    time.sleep(3600)


def _hold_exclusive(path_str: str, ready_path: str, stop_path: str) -> None:
    lock = WorkerLock.acquire(Path(path_str))
    Path(ready_path).write_text("held", encoding="utf-8")
    deadline = time.time() + 30
    while time.time() < deadline and not Path(stop_path).exists():
        time.sleep(0.05)
    lock.release()


def _hold_shared(path_str: str, ready_path: str, stop_path: str) -> None:
    lock = WorkerLock.acquire_shared(Path(path_str))
    Path(ready_path).write_text("held", encoding="utf-8")
    deadline = time.time() + 30
    while time.time() < deadline and not Path(stop_path).exists():
        time.sleep(0.05)
    lock.release()


def _run_one_job(
    queue_dir: str,
    index: int,
    seen_dir: str,
    release_path: str,
) -> None:
    (Path(seen_dir) / f"started-{index}").write_text("1", encoding="utf-8")
    try:
        _run_one_job_inner(queue_dir, index, seen_dir, release_path)
    except Exception:
        import traceback

        (Path(seen_dir) / f"err-{index}").write_text(traceback.format_exc(), encoding="utf-8")
        raise


def _run_one_job_inner(
    queue_dir: str,
    index: int,
    seen_dir: str,
    release_path: str,
) -> None:
    seen = Path(seen_dir) / f"seen-{index}"

    def runner(execution, scratch: Path):  # type: ignore[no-untyped-def]
        seen.write_text(execution.execution_id, encoding="utf-8")
        deadline = time.time() + 30
        while time.time() < deadline and not Path(release_path).exists():
            time.sleep(0.05)
        return {"transcript": "ok", "_output_path": str(scratch / "out.json")}

    processed = run_worker(
        queue_dir=Path(queue_dir),
        once=True,
        validate_nfs=False,
        job_runner=runner,
        max_workers=2,
        host_concurrency=2,
        poll_interval_seconds=0.05,
        standby=True,
        standby_retry_seconds=0.05,
        max_startup_waits=40,
        lease_seconds=30,
    )
    (Path(seen_dir) / f"done-{index}").write_text(str(processed), encoding="utf-8")


def _block_in_runner(queue_dir: str, seen_path: str, release_path: str) -> None:
    def runner(execution, scratch: Path):  # type: ignore[no-untyped-def]
        Path(seen_path).write_text(execution.execution_id, encoding="utf-8")
        deadline = time.time() + 20
        while time.time() < deadline and not Path(release_path).exists():
            time.sleep(0.05)
        return {"transcript": "ok", "_output_path": str(scratch / "out.json")}

    run_worker(
        queue_dir=Path(queue_dir),
        once=True,
        validate_nfs=False,
        job_runner=runner,
        max_workers=1,
        host_concurrency=2,
        poll_interval_seconds=0.05,
        lease_seconds=30,
    )


def _wait_for(path: Path, timeout: float = 15) -> str:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if path.is_file() and path.stat().st_size > 0:
            return path.read_text(encoding="utf-8")
        time.sleep(0.05)
    raise AssertionError(f"timed out waiting for {path}")


def _spawn(target, args: tuple) -> mp.Process:  # type: ignore[no-untyped-def]
    ctx = mp.get_context("spawn")
    proc = ctx.Process(target=target, args=args)
    proc.start()
    return proc


def test_config_worker_caps_default_and_parse(tmp_path: Path) -> None:
    missing = tmp_path / "missing.yaml"
    cfg = load_config(missing)
    assert cfg.queue.max_workers == 1
    assert cfg.worker.concurrency == 1
    assert cfg.worker.lease_seconds == 90.0

    path = tmp_path / "config.yaml"
    path.write_text(
        "queue:\n  max_workers: 2\nworker:\n  concurrency: 1\n  lease_seconds: 45\n",
        encoding="utf-8",
    )
    loaded = load_config(path)
    assert loaded.queue.max_workers == 2
    assert loaded.worker.concurrency == 1
    assert loaded.worker.lease_seconds == 45.0


def test_config_rejects_non_positive_cap(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text("queue:\n  max_workers: 0\n", encoding="utf-8")
    with pytest.raises(ValueError, match="queue.max_workers"):
        load_config(path)


def test_claim_records_hostname_and_pid(tmp_path: Path) -> None:
    queue = tmp_path / "q"
    initialize_queue_layout(queue)
    eid = _enqueue(queue, _URLS[0])
    pending = json.loads((queue / "pending" / f"{eid}.json").read_text(encoding="utf-8"))
    from local_transcribe.services.queue_models import Execution

    claimed = claim_execution(queue, Execution.from_dict(pending))
    assert claimed is not None
    assert claimed.worker_host
    assert claimed.worker_pid == os.getpid()
    assert claimed.worker == f"{claimed.worker_host}:{claimed.worker_pid}"
    stored = json.loads((queue / "processing" / f"{eid}.json").read_text(encoding="utf-8"))
    assert stored["worker_host"] == claimed.worker_host
    assert stored["worker_pid"] == os.getpid()


def test_held_claim_is_not_recovered_until_released(tmp_path: Path) -> None:
    queue = tmp_path / "q"
    initialize_queue_layout(queue)
    eid = _enqueue(queue, _URLS[0])
    from local_transcribe.services.queue_models import Execution

    execution = Execution.from_dict(
        json.loads((queue / "pending" / f"{eid}.json").read_text(encoding="utf-8"))
    )
    claimed = claim_execution(queue, execution, hold_claim_lock=True, worker_slot=0)
    assert claimed is not None
    assert claimed.claim_token
    assert claimed.lease_expires_at
    try:
        assert recover_processing(queue) == 0
        assert (queue / "processing" / f"{eid}.json").is_file()
    finally:
        release_claim(eid)
    assert recover_processing(queue) == 1
    assert (queue / "retry" / f"{eid}.json").is_file()
    release_claim(eid)


def test_other_process_cannot_claim_or_recover_held_job(tmp_path: Path) -> None:
    queue = tmp_path / "q"
    initialize_queue_layout(queue)
    eid = _enqueue(queue, _URLS[0])
    ready = tmp_path / "ready"
    proc = _spawn(_claim_and_sleep, (str(queue), str(ready)))
    try:
        assert _wait_for(ready) == eid
        with pytest.raises(WorkerAlreadyActive):
            take_claim_lock(queue, eid)
        assert recover_processing(queue) == 0
        assert (queue / "processing" / f"{eid}.json").is_file()
    finally:
        if proc.is_alive():
            proc.kill()
        proc.join(timeout=5)


def test_killed_worker_claim_is_requeued(tmp_path: Path) -> None:
    queue = tmp_path / "q"
    initialize_queue_layout(queue)
    eid = _enqueue(queue, _URLS[0])
    ready = tmp_path / "ready"
    proc = _spawn(_claim_and_sleep, (str(queue), str(ready)))
    assert _wait_for(ready) == eid
    assert proc.pid is not None
    assert recover_processing(queue) == 0
    os.kill(proc.pid, signal.SIGKILL)
    proc.join(timeout=5)

    recovered = 0
    deadline = time.time() + 10
    while time.time() < deadline:
        recovered = recover_processing(queue)
        if recovered == 1:
            break
        time.sleep(0.1)
    assert recovered == 1
    stored = json.loads((queue / "retry" / f"{eid}.json").read_text(encoding="utf-8"))
    assert stored["error"]["category"] == "worker_interrupted"
    assert stored["worker_host"]
    assert stored["worker_pid"] == proc.pid


def test_expired_lease_does_not_steal_a_held_lock(tmp_path: Path) -> None:
    queue = tmp_path / "q"
    initialize_queue_layout(queue)
    eid = _enqueue(queue, _URLS[0])
    ready = tmp_path / "ready"
    proc = _spawn(_claim_and_sleep, (str(queue), str(ready)))
    try:
        assert _wait_for(ready) == eid
        path = queue / "processing" / f"{eid}.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["lease_expires_at"] = "2000-01-01T00:00:00+00:00"
        path.write_text(json.dumps(data), encoding="utf-8")
        assert recover_processing(queue) == 0
        assert path.is_file()
    finally:
        if proc.is_alive():
            os.kill(proc.pid, signal.SIGKILL)  # type: ignore[arg-type]
        proc.join(timeout=5)
    assert recover_processing(queue) == 1


def test_two_workers_never_share_a_job(tmp_path: Path) -> None:
    queue = tmp_path / "q"
    initialize_queue_layout(queue)
    # Local files skip YouTube download admission, so two workers are not
    # serialized on the shared 30s download interval.
    for name in ("a.bin", "b.bin"):
        path = tmp_path / name
        path.write_bytes(b"audio")
        _enqueue(queue, str(path))
    seen_dir = tmp_path / "seen"
    seen_dir.mkdir()
    release = tmp_path / "release"
    procs = [
        _spawn(_run_one_job, (str(queue), index, str(seen_dir), str(release)))
        for index in (0, 1)
    ]
    try:
        try:
            first = _wait_for(seen_dir / "seen-0", timeout=20)
            second = _wait_for(seen_dir / "seen-1", timeout=20)
        except AssertionError:
            details = {
                path.name: path.read_text(encoding="utf-8")[:2000]
                for path in sorted(seen_dir.iterdir())
            }
            raise AssertionError(f"workers did not both claim; files={details}") from None
        assert first != second
        report = inspect_workers(queue, max_workers=2)
        lines = "\n".join(format_worker_lines(report))
        assert "Worker cap: 2" in lines
        assert "Active workers: 2/2" in lines
        assert first in lines
        assert second in lines
        jobs = {worker.current_execution_id for worker in report.active}
        assert jobs == {first, second}
        assert all(worker.hostname and worker.pid for worker in report.active)
    finally:
        release.write_text("go", encoding="utf-8")
        for proc in procs:
            proc.join(timeout=20)
    assert all(proc.exitcode == 0 for proc in procs)
    assert len(list((queue / "completed").glob("*.json"))) == 2
    assert not list((queue / "processing").glob("*.json"))


def test_second_worker_stays_out_when_cap_is_full(tmp_path: Path) -> None:
    queue = tmp_path / "q"
    initialize_queue_layout(queue)
    eid = _enqueue(queue, _URLS[0])
    seen = tmp_path / "seen"
    release = tmp_path / "release"
    proc = _spawn(_block_in_runner, (str(queue), str(seen), str(release)))
    try:
        assert _wait_for(seen) == eid
        calls: list[str] = []

        def runner(execution, _scratch: Path):  # type: ignore[no-untyped-def]
            calls.append(execution.execution_id)
            return {"transcript": "nope"}

        with pytest.raises(WorkerAlreadyActive, match="worker slots are busy"):
            run_worker(
                queue_dir=queue,
                once=True,
                validate_nfs=False,
                job_runner=runner,
                max_workers=1,
                host_concurrency=2,
                poll_interval_seconds=0.05,
            )
        assert calls == []
        assert (queue / "processing" / f"{eid}.json").is_file()
    finally:
        release.write_text("go", encoding="utf-8")
        proc.join(timeout=20)


def test_pre_upgrade_exclusive_lock_refuses_new_worker(tmp_path: Path) -> None:
    queue = tmp_path / "q"
    initialize_queue_layout(queue)
    eid = _enqueue(queue, _URLS[0])
    ready = tmp_path / "ready"
    stop = tmp_path / "stop"
    proc = _spawn(
        _hold_exclusive, (str(worker_lock_path(queue)), str(ready), str(stop))
    )
    try:
        assert _wait_for(ready) == "held"
        with pytest.raises(WorkerAlreadyActive, match="Pre-upgrade"):
            run_worker(
                queue_dir=queue,
                once=True,
                validate_nfs=False,
                job_runner=lambda _ex, _scratch: {"transcript": "no"},
                poll_interval_seconds=0.05,
            )
        assert (queue / "pending" / f"{eid}.json").is_file()
        with pytest.raises(WorkerAlreadyActive, match="Pre-upgrade"):
            run_worker(
                queue_dir=queue,
                once=True,
                standby=True,
                standby_retry_seconds=0.01,
                max_startup_waits=1,
                validate_nfs=False,
                job_runner=lambda _ex, _scratch: {"transcript": "no"},
                poll_interval_seconds=0.05,
            )
        assert (queue / "pending" / f"{eid}.json").is_file()
    finally:
        stop.write_text("go", encoding="utf-8")
        proc.join(timeout=10)

    processed = run_worker(
        queue_dir=queue,
        once=True,
        validate_nfs=False,
        job_runner=lambda _ex, scratch: {
            "transcript": "ok",
            "_output_path": str(scratch / "out.json"),
        },
        poll_interval_seconds=0.05,
    )
    assert processed == 1
    assert (queue / "completed" / f"{eid}.json").is_file()


def test_shared_lock_blocks_legacy_exclusive_acquire(tmp_path: Path) -> None:
    queue = tmp_path / "q"
    initialize_queue_layout(queue)
    ready = tmp_path / "ready"
    stop = tmp_path / "stop"
    proc = _spawn(_hold_shared, (str(worker_lock_path(queue)), str(ready), str(stop)))
    try:
        assert _wait_for(ready) == "held"
        with pytest.raises(WorkerAlreadyActive):
            WorkerLock.acquire(worker_lock_path(queue))
    finally:
        stop.write_text("go", encoding="utf-8")
        proc.join(timeout=10)
    lock = WorkerLock.acquire(worker_lock_path(queue))
    lock.release()


def test_queue_stats_and_status_show_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    queue = tmp_path / "q"
    initialize_queue_layout(queue)
    cli = CliRunner()
    stats = cli.invoke(app, ["queue", "stats", "--queue-dir", str(queue)])
    assert stats.exit_code == 0, stats.output
    assert "Worker cap: 1" in stats.output
    assert "Active workers: none" in stats.output

    status = cli.invoke(
        app,
        ["status", "--queue-dir", str(queue), "--output-dir", str(tmp_path / "out")],
    )
    assert status.exit_code == 0, status.output
    assert "Worker cap: 1" in status.output
    assert "Active workers: none" in status.output

    doctor = cli.invoke(app, ["queue", "doctor", "--queue-dir", str(queue)])
    assert "Worker cap: 1" in doctor.output

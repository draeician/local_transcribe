"""Enqueue-and-wait helpers for ``lt transcribe`` (SPEC §15.1, §16.2).

Polls specific execution paths with open-to-close revalidation (NFS attribute
cache aware). Starts the user systemd worker when installed; falls back to a
foreground queue worker that holds the NLM lock until the target execution is
terminal.
"""

from __future__ import annotations

import json
import logging
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from local_transcribe.services.queue_models import (
    STATE_DIRS,
    TERMINAL_STATUSES,
    Execution,
)
from local_transcribe.services.worker import JobRunner, run_worker
from local_transcribe.services.worker_lock import WorkerAlreadyActive, WorkerLock, worker_lock_path

logger = logging.getLogger(__name__)

SERVICE_NAME = "local-transcribe-worker.service"
SystemdStarter = Callable[[], bool]


@dataclass
class LocatedExecution:
    """Execution located via a concrete state-directory path."""

    state_dir: str
    path: Path
    execution: Execution


@dataclass
class WaitOutcome:
    """Result of waiting for one execution to reach a terminal state."""

    status: str  # completed | failed | cancelled | timeout | missing
    execution: Execution | None
    output_path: str | None
    message: str

    @property
    def ok(self) -> bool:
        return self.status == "completed"


def locate_execution(
    queue_dir: Path, execution_id: str
) -> LocatedExecution | None:
    """Find ``execution_id`` by opening each state-dir path (not dir listings).

    Open-to-close revalidation: each candidate is opened, read, and closed.
    Transient OSError / JSON errors are treated as not-yet-visible (attribute
    cache / mid-write), not as permanent failure.
    """
    queue_dir = Path(queue_dir)
    name = f"{execution_id}.json"
    for state in STATE_DIRS:
        path = queue_dir / state / name
        try:
            with path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
            if not isinstance(data, dict):
                continue
            execution = Execution.from_dict(data)
            return LocatedExecution(state_dir=state, path=path, execution=execution)
        except FileNotFoundError:
            continue
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            logger.debug("Transient read for %s: %s", path, exc)
            continue
    return None


def systemd_unit_installed(*, home: Path | None = None) -> bool:
    """Return True if the user systemd unit file exists."""
    base = home if home is not None else Path.home()
    return (base / ".config" / "systemd" / "user" / SERVICE_NAME).is_file()


def try_start_systemd_worker(
    *,
    home: Path | None = None,
    systemctl: str = "systemctl",
) -> bool:
    """Best-effort ``systemctl --user start`` for the worker unit.

    Returns True if the start command reported success. Missing unit or
    systemctl failures return False (caller may fall back to foreground).
    """
    if not systemd_unit_installed(home=home):
        logger.info("Systemd worker unit not installed; skip start")
        return False
    try:
        proc = subprocess.run(
            [systemctl, "--user", "start", SERVICE_NAME],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("Failed to start systemd worker: %s", exc)
        return False
    if proc.returncode != 0:
        logger.warning(
            "systemctl start %s failed (%s): %s",
            SERVICE_NAME,
            proc.returncode,
            (proc.stderr or proc.stdout or "").strip(),
        )
        return False
    logger.info("Started systemd unit %s", SERVICE_NAME)
    return True


def nlm_lock_held_elsewhere(queue_dir: Path) -> bool:
    """Return True if another process holds the NLM worker lock."""
    try:
        lock = WorkerLock.acquire(worker_lock_path(queue_dir))
    except WorkerAlreadyActive:
        return True
    except OSError as exc:
        logger.debug("NLM probe failed: %s", exc)
        return False
    try:
        lock.release()
    except OSError:
        pass
    return False


def _start_foreground_worker(
    queue_dir: Path,
    execution_id: str,
    *,
    validate_nfs: bool,
    job_runner: JobRunner | None,
    poll_interval_seconds: float,
) -> threading.Thread:
    """Spawn a daemon thread that runs the queue worker until ``execution_id`` is terminal."""

    def _target() -> None:
        try:
            run_worker(
                queue_dir=queue_dir,
                once=False,
                standby=False,
                validate_nfs=validate_nfs,
                require_statd=validate_nfs,
                job_runner=job_runner,
                poll_interval_seconds=poll_interval_seconds,
                until_execution_id=execution_id,
            )
        except WorkerAlreadyActive:
            logger.info(
                "Foreground fallback: worker lock already held; relying on active worker"
            )
        except Exception:  # noqa: BLE001
            logger.exception("Foreground queue worker failed")

    thread = threading.Thread(
        target=_target,
        name=f"lt-fg-worker-{execution_id[:8]}",
        daemon=True,
    )
    thread.start()
    return thread


def wait_for_execution(
    queue_dir: Path,
    execution_id: str,
    *,
    timeout_seconds: float | None = None,
    poll_interval_seconds: float = 0.5,
    ensure_worker: bool = True,
    foreground_grace_seconds: float = 2.0,
    validate_nfs: bool = True,
    job_runner: JobRunner | None = None,
    systemd_start: SystemdStarter | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
    monotonic_fn: Callable[[], float] = time.monotonic,
) -> WaitOutcome:
    """Wait until ``execution_id`` is terminal, starting a worker if needed.

    Args:
        timeout_seconds: Max wait; ``None`` waits indefinitely.
        ensure_worker: Try systemd start, then foreground fallback.
        foreground_grace_seconds: Wait this long for an external worker before
            taking the NLM lock in-process.
        systemd_start: Injected starter (tests); default uses systemctl.
        validate_nfs / job_runner: Passed to foreground ``run_worker``.
    """
    queue_dir = Path(queue_dir)
    start = monotonic_fn()
    deadline = (
        None if timeout_seconds is None else start + max(0.0, timeout_seconds)
    )
    starter = systemd_start if systemd_start is not None else try_start_systemd_worker
    foreground_started = False
    fg_thread: threading.Thread | None = None
    ever_seen = False

    if ensure_worker:
        try:
            starter()
        except Exception as exc:  # noqa: BLE001 — best effort
            logger.warning("Worker start attempt failed: %s", exc)

    while True:
        located = locate_execution(queue_dir, execution_id)
        if located is not None:
            ever_seen = True
            status = located.execution.status
            # Prefer directory membership when it disagrees with the field.
            if located.state_dir in TERMINAL_STATUSES:
                status = located.state_dir  # type: ignore[assignment]
            if status == "completed" or located.state_dir == "completed":
                out = located.execution.output_path
                return WaitOutcome(
                    status="completed",
                    execution=located.execution,
                    output_path=out,
                    message="Execution completed",
                )
            if status == "failed" or located.state_dir == "failed":
                err = located.execution.error
                detail = err.message if err else "execution failed"
                category = err.category if err else "failed"
                return WaitOutcome(
                    status="failed",
                    execution=located.execution,
                    output_path=None,
                    message=f"{category}: {detail}",
                )
            if status == "cancelled" or located.state_dir == "cancelled":
                return WaitOutcome(
                    status="cancelled",
                    execution=located.execution,
                    output_path=None,
                    message="Execution cancelled",
                )

        # Attribute-cache: missing briefly after a move is not failure.
        now = monotonic_fn()
        if deadline is not None and now >= deadline:
            if ever_seen:
                msg = (
                    f"Timed out after {timeout_seconds:g}s waiting for "
                    f"execution {execution_id} to finish"
                )
            else:
                msg = (
                    f"Timed out after {timeout_seconds:g}s; execution "
                    f"{execution_id} not visible on queue paths"
                )
            return WaitOutcome(
                status="timeout",
                execution=located.execution if located else None,
                output_path=None,
                message=msg,
            )

        if (
            ensure_worker
            and not foreground_started
            and (now - start) >= foreground_grace_seconds
            and not nlm_lock_held_elsewhere(queue_dir)
        ):
            logger.info(
                "No active NLM worker after %.1fs; starting foreground fallback "
                "for %s",
                foreground_grace_seconds,
                execution_id,
            )
            fg_thread = _start_foreground_worker(
                queue_dir,
                execution_id,
                validate_nfs=validate_nfs,
                job_runner=job_runner,
                poll_interval_seconds=min(poll_interval_seconds, 0.5),
            )
            foreground_started = True

        remaining = poll_interval_seconds
        if deadline is not None:
            remaining = min(remaining, max(0.0, deadline - monotonic_fn()))
        if remaining > 0:
            sleep_fn(remaining)
        elif deadline is not None and monotonic_fn() >= deadline:
            continue  # hit timeout branch next iteration

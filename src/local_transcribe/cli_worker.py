"""lt worker subcommands (task 023 / 030)."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

from local_transcribe.logging_setup import configure_logging
from local_transcribe.services.mount_validation import validate_queue_mount
from local_transcribe.services.queue_paths import resolve_queue_dir
from local_transcribe.services.worker import run_worker
from local_transcribe.services.worker_lock import WorkerAlreadyActive, WorkerLock, worker_lock_path
from local_transcribe.services.worker_state import state_path

console = Console(force_terminal=True)
worker_app = typer.Typer(help="Background transcription worker")

SERVICE_NAME = "local-transcribe-worker.service"


def render_systemd_unit(lt_path: str) -> str:
    """Return the systemd --user unit body for the fail-closed worker.

    ExecStart runs ``worker run --standby`` with no flag that disables NFS
    validation. Validation is always on for this entry point.
    """
    return f"""[Unit]
Description=Local Transcribe Background Worker
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart={lt_path} worker run --standby
Restart=on-failure
RestartSec=10

[Install]
WantedBy=default.target
"""


@worker_app.command("run")
def worker_run(
    once: bool = typer.Option(False, "--once", help="Process at most one job and exit"),
    standby: bool = typer.Option(False, "--standby", help="Retry NLM lock while waiting"),
    queue_dir: Optional[Path] = typer.Option(None, "--queue-dir"),
    verbose: bool = typer.Option(False, "-v", "--verbose"),
) -> None:
    """Run the worker loop with fail-closed NFS validation (production default)."""
    configure_logging(verbose=verbose, log_file_prefix="worker")
    from local_transcribe.services.job_runner import create_default_job_runner

    qdir = resolve_queue_dir(queue_dir=queue_dir) if queue_dir else resolve_queue_dir()
    runner = create_default_job_runner()
    try:
        n = run_worker(
            queue_dir=qdir,
            once=once,
            standby=standby,
            validate_nfs=True,
            require_statd=True,
            job_runner=runner,
        )
    except WorkerAlreadyActive as exc:
        console.print(f"[yellow]{exc}[/yellow]")
        raise typer.Exit(1) from exc
    console.print(f"Worker finished; processed {n} job(s)")


@worker_app.command("status")
def worker_status(
    queue_dir: Optional[Path] = typer.Option(None, "--queue-dir"),
) -> None:
    """Show worker lock and diagnostic state."""
    qdir = resolve_queue_dir(queue_dir=queue_dir) if queue_dir else resolve_queue_dir()
    mount = validate_queue_mount(qdir, validate_nfs=True, require_statd=True)
    if not mount.ok:
        console.print("[red]queue mounted but unsafe[/red]")
        for e in mount.errors:
            console.print(f"  {e}")
    try:
        lock = WorkerLock.acquire(worker_lock_path(qdir))
        lock.release()
        console.print("NLM lock: available (acquired locally then released)")
    except WorkerAlreadyActive:
        console.print("NLM lock held elsewhere")
    except Exception as exc:  # noqa: BLE001
        console.print(f"NLM environment unavailable: {exc}")

    sp = state_path(qdir)
    if sp.is_file():
        console.print(f"worker state file: {sp.read_text(encoding='utf-8')[:500]}")
    else:
        console.print("worker state: none")


@worker_app.command("doctor")
def worker_doctor(
    queue_dir: Optional[Path] = typer.Option(None, "--queue-dir"),
) -> None:
    """Alias-style health check for worker environment."""
    worker_status(queue_dir=queue_dir)


@worker_app.command("install")
def worker_install() -> None:
    """Install a systemd --user unit (does not enable linger)."""
    lt_path = shutil.which("lt") or os.path.expanduser("~/.local/bin/lt")
    unit_dir = Path.home() / ".config" / "systemd" / "user"
    unit_dir.mkdir(parents=True, exist_ok=True)
    unit_path = unit_dir / SERVICE_NAME
    unit_path.write_text(render_systemd_unit(lt_path), encoding="utf-8")
    console.print(f"[green]✓[/green] Wrote {unit_path}")
    console.print("Run: systemctl --user daemon-reload")
    console.print(f"Then: systemctl --user enable --now {SERVICE_NAME}")
    console.print("Optional logged-out runs: loginctl enable-linger $USER")


@worker_app.command("start")
def worker_start() -> None:
    os.system(f"systemctl --user start {SERVICE_NAME}")


@worker_app.command("stop")
def worker_stop() -> None:
    os.system(f"systemctl --user stop {SERVICE_NAME}")


@worker_app.command("restart")
def worker_restart() -> None:
    os.system(f"systemctl --user restart {SERVICE_NAME}")


@worker_app.command("logs")
def worker_logs() -> None:
    os.system(f"journalctl --user -u {SERVICE_NAME} -n 100 --no-pager")

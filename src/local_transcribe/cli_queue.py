"""lt queue subcommands (task 022)."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from local_transcribe.logging_setup import configure_logging
from local_transcribe.services.config import load_config
from local_transcribe.services.mount_validation import validate_queue_mount
from local_transcribe.services.queue_paths import (
    QueueIdentityError,
    QueuePathError,
    initialize_queue_layout,
    queue_id_path,
    resolve_queue_dir,
    verify_queue_identity,
)
from local_transcribe.services.queue_store import QueueStore, QueueStoreError
from local_transcribe.services.worker_lock import WorkerAlreadyActive, WorkerLock, worker_lock_path

console = Console(force_terminal=True)
queue_app = typer.Typer(help="Manage the transcription queue")


def _queue_dir_opt(
    queue_dir: Optional[Path],
    *,
    verify: bool = True,
) -> Path:
    try:
        if queue_dir is not None:
            return resolve_queue_dir(queue_dir=queue_dir, verify_identity=verify)
        return resolve_queue_dir(verify_identity=verify)
    except (QueuePathError, QueueIdentityError) as exc:
        console.print(f"[red]✗[/red] {exc}")
        raise typer.Exit(1) from exc


@queue_app.command("init")
def queue_init(
    queue_dir: Path = typer.Option(
        ...,
        "--queue-dir",
        help="Explicit path for the queue directory",
        exists=False,
        file_okay=False,
        dir_okay=True,
        writable=True,
        resolve_path=False,
    ),
    verbose: bool = typer.Option(False, "-v", "--verbose"),
) -> None:
    """Create queue layout and queue.id at an explicit path."""
    configure_logging(verbose=verbose, log_file_prefix="queue")
    uuid = initialize_queue_layout(queue_dir)
    console.print(f"[green]✓[/green] Queue initialized at {queue_dir}")
    console.print(f"Queue UUID: {uuid}")


@queue_app.command("path")
def queue_path_cmd(
    queue_dir: Optional[Path] = typer.Option(None, "--queue-dir"),
    verbose: bool = typer.Option(False, "-v", "--verbose"),
) -> None:
    """Show configured and resolved queue path details."""
    configure_logging(verbose=verbose, log_file_prefix="queue")
    cfg = load_config()
    console.print(f"Configured path: {cfg.queue.path}")
    try:
        resolved = _queue_dir_opt(queue_dir, verify=True)
        qid = verify_queue_identity(resolved, expected_uuid=cfg.queue.expected_uuid)
        console.print(f"Resolved path: {resolved}")
        console.print(f"Queue UUID: {qid}")
    except typer.Exit:
        raise
    mount = validate_queue_mount(resolved if queue_dir or cfg.queue.path else Path("."), validate_nfs=True)
    if mount.mount:
        console.print(f"Mount: {mount.mount.fstype} {mount.mount.source} -> {mount.mount.target}")
        console.print(f"Options: {','.join(mount.mount.all_options())}")
    for err in mount.errors:
        console.print(f"[red]Mount error:[/red] {err}")
    for warn in mount.warnings:
        console.print(f"[yellow]Mount warn:[/yellow] {warn}")


@queue_app.command("doctor")
def queue_doctor(
    queue_dir: Optional[Path] = typer.Option(None, "--queue-dir"),
    verbose: bool = typer.Option(False, "-v", "--verbose"),
) -> None:
    """Diagnose queue path, identity, and NFS mount safety."""
    configure_logging(verbose=verbose, log_file_prefix="queue")
    cfg = load_config()
    ok = True
    try:
        resolved = _queue_dir_opt(queue_dir, verify=True)
        console.print(f"[green]✓[/green] Queue resolves: {resolved}")
    except typer.Exit:
        raise
    result = validate_queue_mount(resolved, queue_config=cfg.queue, validate_nfs=True)
    for k, v in result.checks.items():
        console.print(f"  {k}: {v}")
    for err in result.errors:
        ok = False
        console.print(f"[red]✗[/red] {err}")
    for warn in result.warnings:
        console.print(f"[yellow]![/yellow] {warn}")
    # Lock probe
    try:
        lock = WorkerLock.acquire(worker_lock_path(resolved))
        lock.release()
        console.print("[green]✓[/green] NLM lock acquire/release probe OK")
    except WorkerAlreadyActive:
        console.print("[yellow]![/yellow] NLM lock currently held by another worker")
    except Exception as exc:  # noqa: BLE001
        ok = False
        console.print(f"[red]✗[/red] NLM lock probe failed: {exc}")
    if not ok:
        raise typer.Exit(1)


@queue_app.command("add")
def queue_add(
    source: str = typer.Argument(..., help="YouTube URL or local audio path"),
    queue_dir: Optional[Path] = typer.Option(None, "--queue-dir"),
    priority: int = typer.Option(50, "--priority"),
    force: bool = typer.Option(False, "--force"),
    origin: str = typer.Option("manual", "--origin"),
    verbose: bool = typer.Option(False, "-v", "--verbose"),
) -> None:
    """Enqueue a source into the queue."""
    configure_logging(verbose=verbose, log_file_prefix="queue")
    qdir = _queue_dir_opt(queue_dir)
    store = QueueStore(qdir)
    result = store.enqueue(source, origin=origin, priority=priority, force=force)
    console.print(f"{result.kind}: {result.message or result.source_key}")
    if result.execution:
        console.print(f"  execution_id={result.execution.execution_id}")
        console.print(f"  source_key={result.source_key}")


@queue_app.command("list")
def queue_list(
    queue_dir: Optional[Path] = typer.Option(None, "--queue-dir"),
    status: Optional[str] = typer.Option(None, "--status"),
    origin: Optional[str] = typer.Option(None, "--origin"),
    limit: int = typer.Option(50, "--limit"),
    verbose: bool = typer.Option(False, "-v", "--verbose"),
) -> None:
    """List queue executions."""
    configure_logging(verbose=verbose, log_file_prefix="queue")
    store = QueueStore(_queue_dir_opt(queue_dir))
    rows = store.list_executions(status=status, origin=origin, limit=limit)
    table = Table("execution_id", "status", "priority", "source_key", "origin")
    for ex in rows:
        table.add_row(ex.execution_id[:8] + "…", ex.status, str(ex.priority), ex.source_key, ex.origin)
    console.print(table)
    console.print(f"Total shown: {len(rows)}")


@queue_app.command("show")
def queue_show(
    execution_id: str = typer.Argument(...),
    queue_dir: Optional[Path] = typer.Option(None, "--queue-dir"),
) -> None:
    """Show one execution record."""
    store = QueueStore(_queue_dir_opt(queue_dir))
    ex, st = store.find_execution(execution_id)
    if ex is None:
        console.print(f"[red]✗[/red] Not found: {execution_id}")
        raise typer.Exit(1)
    console.print(f"state_dir={st}")
    console.print(ex.to_dict())


@queue_app.command("show-source")
def queue_show_source(
    source_key: str = typer.Argument(...),
    queue_dir: Optional[Path] = typer.Option(None, "--queue-dir"),
) -> None:
    """Show reservation and executions for a source key."""
    from local_transcribe.services.source_reservations import read_reservation

    qdir = _queue_dir_opt(queue_dir)
    res = read_reservation(qdir, source_key)
    if res:
        console.print(res.to_dict())
    else:
        console.print("[yellow]No reservation[/yellow]")
    store = QueueStore(qdir)
    for ex, st in store.find_by_source_key(source_key):
        console.print(f"  [{st}] {ex.execution_id} gen={ex.generation}")


@queue_app.command("cancel")
def queue_cancel(
    execution_id: str = typer.Argument(...),
    queue_dir: Optional[Path] = typer.Option(None, "--queue-dir"),
) -> None:
    """Cancel a pending execution (pending only)."""
    store = QueueStore(_queue_dir_opt(queue_dir))
    try:
        ex = store.cancel_pending(execution_id)
    except QueueStoreError as exc:
        console.print(f"[red]✗[/red] {exc}")
        raise typer.Exit(1) from exc
    console.print(f"[green]✓[/green] Cancelled {ex.execution_id}")


@queue_app.command("stats")
def queue_stats(
    queue_dir: Optional[Path] = typer.Option(None, "--queue-dir"),
) -> None:
    """Show counts by state directory."""
    qdir = _queue_dir_opt(queue_dir)
    for name in ("pending", "processing", "retry", "completed", "failed", "cancelled"):
        d = qdir / name
        n = len(list(d.glob("*.json"))) if d.is_dir() else 0
        console.print(f"{name}: {n}")


@queue_app.command("purge")
def queue_purge(
    queue_dir: Optional[Path] = typer.Option(None, "--queue-dir"),
    completed: bool = typer.Option(False, "--completed"),
    failed: bool = typer.Option(False, "--failed"),
    cancelled: bool = typer.Option(False, "--cancelled"),
    older_than_days: Optional[int] = typer.Option(None, "--older-than-days"),
) -> None:
    """Purge terminal execution records (never deletes transcript JSON)."""
    if not any([completed, failed, cancelled]):
        console.print("[red]✗[/red] Specify at least one of --completed --failed --cancelled")
        raise typer.Exit(1)
    qdir = _queue_dir_opt(queue_dir)
    targets = []
    if completed:
        targets.append("completed")
    if failed:
        targets.append("failed")
    if cancelled:
        targets.append("cancelled")
    removed = 0
    import time

    cutoff = None
    if older_than_days is not None:
        cutoff = time.time() - older_than_days * 86400
    for name in targets:
        d = qdir / name
        if not d.is_dir():
            continue
        for path in d.glob("*.json"):
            if cutoff is not None and path.stat().st_mtime > cutoff:
                continue
            path.unlink()
            removed += 1
    console.print(f"[green]✓[/green] Removed {removed} execution records (transcripts untouched)")


@queue_app.command("import")
def queue_import(
    pending_file: Path = typer.Argument(..., exists=True, dir_okay=False),
    queue_dir: Optional[Path] = typer.Option(None, "--queue-dir"),
    verbose: bool = typer.Option(False, "-v", "--verbose"),
) -> None:
    """Import URLs from a legacy transcript-pending.md style file."""
    from local_transcribe.services.legacy_import import import_pending_file

    configure_logging(verbose=verbose, log_file_prefix="queue")
    qdir = _queue_dir_opt(queue_dir)
    summary = import_pending_file(qdir, pending_file)
    console.print(summary)


@queue_app.command("export-pending")
def queue_export_pending(
    queue_dir: Optional[Path] = typer.Option(None, "--queue-dir"),
    output: Optional[Path] = typer.Option(None, "--output", "-o"),
) -> None:
    """Export a human-readable report of pending/retry/processing jobs."""
    store = QueueStore(_queue_dir_opt(queue_dir))
    lines = []
    for status in ("pending", "processing", "retry"):
        for ex in store.list_executions(status=status):
            lines.append(f"{status}\t{ex.source_key}\t{ex.source}\t{ex.execution_id}")
    text = "\n".join(lines) + ("\n" if lines else "")
    if output:
        output.write_text(text, encoding="utf-8")
        console.print(f"Wrote {output}")
    else:
        console.print(text or "[dim](empty)[/dim]")


@queue_app.command("repair")
def queue_repair(
    queue_dir: Optional[Path] = typer.Option(None, "--queue-dir"),
) -> None:
    """Repair queue state (requires NLM worker lock)."""
    from local_transcribe.services.worker import promote_retries, recover_processing

    qdir = _queue_dir_opt(queue_dir)
    try:
        lock = WorkerLock.acquire(worker_lock_path(qdir))
    except WorkerAlreadyActive as exc:
        console.print(f"[red]✗[/red] {exc}")
        raise typer.Exit(1) from exc
    try:
        n1 = recover_processing(qdir)
        n2 = promote_retries(qdir)
        console.print(f"[green]✓[/green] Recovered {n1} processing, promoted {n2} retries")
    finally:
        lock.release()


@queue_app.command("retry")
def queue_retry(
    execution_id: str = typer.Argument(...),
    queue_dir: Optional[Path] = typer.Option(None, "--queue-dir"),
) -> None:
    """Re-queue a failed/cancelled source via force enqueue of its source."""
    store = QueueStore(_queue_dir_opt(queue_dir))
    ex, st = store.find_execution(execution_id)
    if ex is None:
        console.print(f"[red]✗[/red] Not found: {execution_id}")
        raise typer.Exit(1)
    result = store.enqueue(ex.source, force=True, origin=ex.origin, priority=ex.priority)
    console.print(f"{result.kind}: {result.execution.execution_id if result.execution else ''}")

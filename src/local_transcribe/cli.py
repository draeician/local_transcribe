"""Command-line interface for local transcription."""

import os
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from local_transcribe import __version__
from local_transcribe.logging_setup import configure_logging
from local_transcribe.services.pipeline import BatchConfig, BatchPipeline
from local_transcribe.services.reconcile import reconcile as reconcile_service, write_reconcile_outputs
from local_transcribe.services.status_store import JsonStatusStore
from local_transcribe.services.verify_status import verify_finished_dat, verify_full
from local_transcribe.services.transcriber import TranscribeConfig, transcribe_local_file, transcribe_url
from local_transcribe.utils.files import safe_read_lines, safe_write_lines, find_cookies_file
from local_transcribe.utils.youtube import is_valid_youtube_url

app = typer.Typer(help="Local transcription with Whisper (YouTube URLs or audio files)")
console = Console(force_terminal=True)

# Queue / worker sub-apps (SPEC-queue v3)
from local_transcribe.cli_queue import queue_app  # noqa: E402
from local_transcribe.cli_worker import worker_app  # noqa: E402

app.add_typer(queue_app, name="queue")
app.add_typer(worker_app, name="worker")

# Default values
DEFAULT_MODEL = "medium"
DEFAULT_DEVICE = "cuda"
DEFAULT_COMPUTE_TYPE = "float16"
DEFAULT_OUTPUT_DIR = Path.home() / "references" / "transcripts"

# Module-level flag to show startup warnings only once
_startup_warnings_shown = False


@app.command(name="version")
def version_cmd():
    """Show version information."""
    console.print(f"local-transcribe version {__version__}")


@app.command()
def transcribe(
    source: str = typer.Argument(
        ...,
        metavar="URL_OR_PATH",
        help="YouTube video URL or path to a local audio file (e.g. m4a, mp3; ffmpeg should be available)",
    ),
    model: str = typer.Option(DEFAULT_MODEL, help="Whisper model"),
    device: str = typer.Option(DEFAULT_DEVICE, help="Device (cpu/cuda/auto)"),
    compute_type: str = typer.Option(DEFAULT_COMPUTE_TYPE, help="Compute type"),
    output_dir: str = typer.Option(str(DEFAULT_OUTPUT_DIR), help="Output directory"),
    keep_audio: bool = typer.Option(
        False,
        help="Keep downloaded audio after transcription (no-op for local files)",
    ),
    cookies_from_browser: str = typer.Option(
        None,
        help="Browser to extract cookies from (YouTube only)",
    ),
    cookies_file: str = typer.Option(None, help="Path to cookies.txt file (YouTube only)"),
    limit_rate: str = typer.Option(
        None,
        "--limit-rate",
        help="Max download rate, e.g. 200K, 4.2M (YouTube only)",
    ),
    sleep_interval_requests: float = typer.Option(
        None,
        "--sleep-interval-requests",
        help="Seconds to sleep between yt-dlp requests (YouTube only)",
    ),
    direct: bool = typer.Option(
        False,
        "--direct",
        help="Emergency/dev: run in-process without the queue (not the default)",
    ),
    no_wait: bool = typer.Option(
        False,
        "--no-wait",
        help="Enqueue and return without waiting for the worker (queue mode)",
    ),
    timeout: Optional[float] = typer.Option(
        None,
        "--timeout",
        help="Seconds to wait for queue completion (default: wait indefinitely)",
    ),
    force: bool = typer.Option(False, "--force", help="Force a new queue generation"),
    queue_dir: Optional[str] = typer.Option(None, "--queue-dir", help="Queue directory override"),
    verbose: bool = typer.Option(False, "-v", "--verbose", help="Verbose logging"),
):
    """Transcribe one YouTube video or one local audio file.

    Default: enqueue into the transcription queue (interactive priority) and wait.
    Use --direct for legacy in-process execution.
    """
    raw = source.strip()
    expanded = Path(raw).expanduser()

    if expanded.exists():
        if expanded.is_dir():
            console.print(f"[red]✗[/red] Error: Path is a directory, not an audio file: {expanded}")
            raise typer.Exit(1)
        if expanded.is_file():
            is_local = True
        else:
            console.print(f"[red]✗[/red] Error: Not a regular file: {expanded}")
            raise typer.Exit(1)
    elif is_valid_youtube_url(raw):
        is_local = False
    else:
        console.print(
            "[red]✗[/red] Error: Not an existing file path and not a valid HTTPS YouTube URL."
        )
        console.print(f"[dim]{raw}[/dim]")
        raise typer.Exit(1)

    logger = configure_logging(verbose=verbose, log_file_prefix="transcribe")

    from local_transcribe.utils.doctor import check_deno_with_guidance, check_pytorch_with_guidance

    if not is_local:
        deno_available, deno_info = check_deno_with_guidance()
        if not deno_available:
            console.print("\n[yellow]⚠️  Warning: Deno runtime not detected[/yellow]")
            console.print(f"[dim]{deno_info}[/dim]")
            console.print("[dim]Downloads may fail with HTTP 403 errors without Deno.[/dim]\n")

    if device.lower() in ("cuda", "gpu"):
        pytorch_available, pytorch_info = check_pytorch_with_guidance()
        if not pytorch_available:
            console.print("\n[yellow]⚠️  Warning: PyTorch not detected[/yellow]")
            console.print(f"[dim]{pytorch_info}[/dim]")
            console.print("[dim]GPU acceleration requires PyTorch. Falling back to CPU mode.[/dim]\n")

    try:
        output_dir_path = Path(output_dir).expanduser()

        cfg = TranscribeConfig(
            model=model,
            device=device,
            compute_type=compute_type,
            output_dir=output_dir_path,
            keep_audio=keep_audio,
            cookies_from_browser=cookies_from_browser,
            cookies_file=cookies_file,
            limit_rate=limit_rate,
            sleep_interval_requests=sleep_interval_requests if sleep_interval_requests is not None else None,
        )

        if not direct:
            # Queue mode (default)
            from local_transcribe.services.queue_models import ExecutionOptions
            from local_transcribe.services.queue_paths import (
                QueuePathError,
                resolve_queue_dir,
            )
            from local_transcribe.services.queue_store import QueueStore

            try:
                qdir = resolve_queue_dir(
                    queue_dir=Path(queue_dir) if queue_dir else None
                )
            except QueuePathError as exc:
                console.print(f"[red]✗[/red] Queue not configured: {exc}")
                console.print(
                    "[dim]Configure queue.path or pass --queue-dir, "
                    "or use --direct for emergency in-process mode.[/dim]"
                )
                raise typer.Exit(1) from exc

            store = QueueStore(qdir)
            opts = ExecutionOptions(
                model=model,
                device=device,
                compute_type=compute_type,
                keep_audio=keep_audio,
            )
            result = store.enqueue(
                str(expanded.resolve()) if is_local else raw,
                origin="lt-transcribe",
                priority=100,
                force=force,
                options=opts,
            )
            console.print(
                f"[green]✓[/green] Queue {result.kind}: {result.source_key}"
            )
            if result.execution:
                console.print(f"  execution_id={result.execution.execution_id}")
            if result.kind == "already_completed":
                if result.transcript_path:
                    console.print(f"[green]✓[/green] Done. Wrote: {result.transcript_path}")
                else:
                    console.print("[green]✓[/green] Already completed (transcript on record)")
                return
            if no_wait:
                if result.transcript_path:
                    console.print(f"  transcript={result.transcript_path}")
                console.print("[dim]--no-wait: not waiting for worker[/dim]")
                return
            if result.execution is None:
                console.print(
                    f"[red]✗[/red] Nothing to wait for ({result.kind}): {result.message}"
                )
                raise typer.Exit(1)

            from local_transcribe.services.queue_wait import wait_for_execution

            console.print("[dim]Waiting for worker…[/dim]")
            outcome = wait_for_execution(
                qdir,
                result.execution.execution_id,
                timeout_seconds=timeout,
                ensure_worker=True,
                validate_nfs=True,
            )
            if outcome.ok:
                path = outcome.output_path or (
                    str(result.transcript_path) if result.transcript_path else None
                )
                if path:
                    console.print(f"[green]✓[/green] Done. Wrote: {path}")
                else:
                    console.print("[green]✓[/green] Done (execution completed)")
                return
            console.print(f"[red]✗[/red] {outcome.message}")
            raise typer.Exit(1)

        console.print(
            "[yellow]![/yellow] --direct: bypassing queue (dev/emergency only)"
        )
        if is_local:
            logger.info(f"Transcribing local file: {expanded.resolve()}")
            result_path = transcribe_local_file(expanded, cfg)
        else:
            logger.info(f"Transcribing: {raw}")
            result_path = transcribe_url(raw, cfg)
        console.print(f"[green]✓[/green] Done. Wrote: {result_path}")

    except Exception as e:
        logger.error(f"Transcription failed: {e}", exc_info=verbose)
        console.print(f"[red]✗[/red] Error: {e}")
        raise typer.Exit(1)


@app.command()
def batch(
    input: str = typer.Option(None, "--input", "-i", help="Input file with URLs (defaults to inputfile.txt in current directory)"),
    resume: bool = typer.Option(False, help="Resume from previous run"),
    max_retries: int = typer.Option(2, help="Max retry attempts (maps to queue max_attempts)"),
    model: str = typer.Option(DEFAULT_MODEL, help="Whisper model"),
    device: str = typer.Option(DEFAULT_DEVICE, help="Device (cpu/cuda/auto)"),
    compute_type: str = typer.Option(DEFAULT_COMPUTE_TYPE, help="Compute type"),
    language: Optional[str] = typer.Option(None, "--language", help="Whisper language hint (queue mode)"),
    keep_audio: bool = typer.Option(False, "--keep-audio", help="Keep downloaded audio (queue mode)"),
    auth_profile: Optional[str] = typer.Option(
        None, "--auth-profile", help="Auth/cookies profile name stored on the execution"
    ),
    output_dir: str = typer.Option(None, "--output-dir", "-o", help=f"Output directory (defaults to {DEFAULT_OUTPUT_DIR})"),
    cookies_from_browser: str = typer.Option(None, help="Browser to extract cookies from"),
    cookies_file: str = typer.Option(None, help="Path to cookies.txt file"),
    sleep_interval: float = typer.Option(1.0, "--sleep-interval", help="Seconds to sleep between videos"),
    limit_rate: str = typer.Option(None, "--limit-rate", help="Max download rate (e.g., 200K, 4.2M)"),
    sleep_interval_requests: float = typer.Option(None, "--sleep-interval-requests", help="Seconds to sleep between yt-dlp requests"),
    wait: bool = typer.Option(
        False,
        "--wait",
        help="Wait for enqueued/active executions to finish (queue mode)",
    ),
    timeout: Optional[float] = typer.Option(
        None,
        "--timeout",
        help="Seconds to wait per execution when using --wait (default: indefinite)",
    ),
    direct: bool = typer.Option(
        False,
        "--direct",
        help="Legacy in-process batch pipeline (bypass queue)",
    ),
    queue_dir: Optional[str] = typer.Option(None, "--queue-dir", help="Queue directory override"),
    verbose: bool = typer.Option(False, "-v", "--verbose", help="Verbose logging"),
):
    """
    Process multiple videos in batch.

    Default: bulk-enqueue URLs into the transcription queue.
    Use --direct for the legacy BatchPipeline (in-process).
    --resume is a compatibility no-op in queue mode (state is durable).
    """
    logger = configure_logging(verbose=verbose)

    if not direct:
        from local_transcribe.services.queue_models import ExecutionOptions
        from local_transcribe.services.queue_paths import QueuePathError, resolve_queue_dir
        from local_transcribe.services.queue_store import QueueStore
        from local_transcribe.services.queue_wait import wait_for_execution

        input_path = Path(input).expanduser() if input else Path("inputfile.txt")
        if not input_path.is_file():
            console.print(f"[red]✗[/red] Input file not found: {input_path}")
            raise typer.Exit(1)
        try:
            qdir = resolve_queue_dir(queue_dir=Path(queue_dir) if queue_dir else None)
        except QueuePathError as exc:
            console.print(f"[red]✗[/red] Queue not configured: {exc}")
            console.print("[dim]Use --direct for legacy batch, or configure queue.path[/dim]")
            raise typer.Exit(1) from exc
        if resume:
            console.print("[dim]--resume ignored in queue mode (queue state is durable)[/dim]")

        opts = ExecutionOptions(
            model=model,
            device=device,
            compute_type=compute_type,
            language=language,
            keep_audio=keep_audio,
            auth_profile=auth_profile,
            limit_rate=limit_rate,
            sleep_interval_requests=(
                sleep_interval_requests if sleep_interval_requests is not None else None
            ),
        )
        # Historical --max-retries is retries after the first attempt.
        max_attempts = max(1, int(max_retries) + 1)
        store = QueueStore(qdir)
        urls = safe_read_lines(input_path)
        counts = {
            "enqueued": 0,
            "existing_active": 0,
            "already_completed": 0,
            "requires_force": 0,
            "other": 0,
        }
        wait_ids: list[str] = []
        for url in urls:
            url = url.strip()
            if not url or url.startswith("#"):
                continue
            result = store.enqueue(
                url,
                origin="lt-batch",
                priority=10,
                options=opts,
                max_attempts=max_attempts,
            )
            key = result.kind if result.kind in counts else "other"
            counts[key] = counts.get(key, 0) + 1
            if result.execution and result.kind in {"enqueued", "existing_active"}:
                wait_ids.append(result.execution.execution_id)

        console.print(f"[green]✓[/green] Batch enqueue summary: {counts}")

        if wait and wait_ids:
            console.print(
                f"[dim]Waiting for {len(wait_ids)} execution(s)…[/dim]"
            )
            failures = 0
            for eid in wait_ids:
                outcome = wait_for_execution(
                    qdir,
                    eid,
                    timeout_seconds=timeout,
                    ensure_worker=True,
                    validate_nfs=True,
                )
                if outcome.ok:
                    console.print(
                        f"  [green]✓[/green] {eid}: {outcome.output_path or 'completed'}"
                    )
                else:
                    failures += 1
                    console.print(f"  [red]✗[/red] {eid}: {outcome.message}")
            if failures:
                console.print(f"[red]✗[/red] Batch wait finished with {failures} failure(s)")
                raise typer.Exit(1)
            console.print("[green]✓[/green] Batch wait complete")
        elif wait and not wait_ids:
            console.print("[dim]--wait: nothing active to wait for[/dim]")
        return
    
    # Check prerequisites
    from local_transcribe.utils.doctor import check_deno_with_guidance, check_pytorch_with_guidance
    
    # Always check Deno (required for YouTube downloads)
    deno_available, deno_info = check_deno_with_guidance()
    if not deno_available:
        console.print("\n[yellow]⚠️  Warning: Deno runtime not detected[/yellow]")
        console.print(f"[dim]{deno_info}[/dim]")
        console.print("[dim]Downloads may fail with HTTP 403 errors without Deno.[/dim]\n")
    
    # Check PyTorch if using CUDA
    if device.lower() in ("cuda", "gpu"):
        pytorch_available, pytorch_info = check_pytorch_with_guidance()
        if not pytorch_available:
            console.print("\n[yellow]⚠️  Warning: PyTorch not detected[/yellow]")
            console.print(f"[dim]{pytorch_info}[/dim]")
            console.print("[dim]GPU acceleration requires PyTorch. Falling back to CPU mode.[/dim]\n")
    
    try:
        # Handle defaults properly - check if value is actually a string or OptionInfo
        if output_dir is None or not isinstance(output_dir, str):
            output_path = Path(DEFAULT_OUTPUT_DIR).expanduser()
        else:
            output_path = Path(output_dir).expanduser()
        
        if input is None or not isinstance(input, str):
            # Try pending file first (created by reconcile/verify commands)
            pending_file = output_path / "transcript-pending.md"
            if pending_file.exists():
                input_path = pending_file
                console.print(f"[blue]Using pending file: {pending_file}[/blue]")
            else:
                # Fall back to inputfile.txt for backward compatibility
                input_path = Path("inputfile.txt")
        else:
            input_path = Path(input).expanduser()
        
        # For batch, only use cookies when explicitly provided on the CLI.
        
        # Status store and finished file will default to output_dir in BatchPipeline
        config = BatchConfig(
            input_file=input_path,
            output_dir=output_path,
            model=model,
            device=device,
            compute_type=compute_type,
            max_retries=max_retries,
            status_store=None,  # Will default to output_dir / "batch_status.json"
            cookies_from_browser=cookies_from_browser,
            cookies_file=cookies_file,
            sleep_interval_between_videos=sleep_interval,
            limit_rate=limit_rate,
            sleep_interval_requests=sleep_interval_requests if sleep_interval_requests is not None else None,
        )
        
        pipeline = BatchPipeline(config)
        summary = pipeline.run(resume=resume)
        
        # Generate reports
        log_dir = Path("logs")
        pipeline.generate_reports(log_dir)
        
        # Print summary
        console.print("\n[bold]Batch Processing Complete[/bold]")
        table = Table(show_header=True, header_style="bold")
        table.add_column("Metric")
        table.add_column("Count")
        table.add_row("Total", str(summary.total))
        table.add_row("Completed", f"[green]{summary.completed}[/green]")
        table.add_row("Failed", f"[red]{summary.failed}[/red]")
        table.add_row("Skipped", f"[yellow]{summary.skipped}[/yellow]")
        table.add_row("Pending", str(summary.pending))
        console.print(table)
        
        # Print rate limit stats
        rate_stats = pipeline.rate_limiter.get_stats()
        console.print("\n[bold]Rate Limit Status[/bold]")
        rate_table = Table(show_header=True, header_style="bold")
        rate_table.add_column("Period")
        rate_table.add_column("Usage")
        rate_table.add_column("Limit")
        rate_table.add_column("Percent")
        
        hour_color = "green"
        if rate_stats['hour_percent'] >= 80:
            hour_color = "yellow"
        if rate_stats['hour_percent'] >= 100:
            hour_color = "red"
        
        day_color = "green"
        if rate_stats['day_percent'] >= 80:
            day_color = "yellow"
        if rate_stats['day_percent'] >= 100:
            day_color = "red"
        
        rate_table.add_row(
            "Per Hour",
            str(rate_stats['requests_this_hour']),
            str(rate_stats['max_per_hour']),
            f"[{hour_color}]{rate_stats['hour_percent']:.1f}%[/{hour_color}]"
        )
        rate_table.add_row(
            "Per Day",
            str(rate_stats['requests_today']),
            str(rate_stats['max_per_day']),
            f"[{day_color}]{rate_stats['day_percent']:.1f}%[/{day_color}]"
        )
        console.print(rate_table)
        
        if rate_stats['total_429_errors'] > 0:
            console.print(
                f"\n[yellow]⚠[/yellow] Encountered {rate_stats['total_429_errors']} rate limit error(s). "
                f"Consider increasing --sleep-interval between videos."
            )
        
    except Exception as e:
        logger.error(f"Batch processing failed: {e}", exc_info=verbose)
        console.print(f"[red]✗[/red] Error: {e}")
        raise typer.Exit(1)


@app.command(name="reconcile")
def reconcile_cmd(
    input: str = typer.Option(None, "--input", "-i", help="Input file with URLs (defaults to inputfile.txt in current directory)"),
    finished: str = typer.Option(None, "--finished", "-f", help="Finished file (defaults to output_dir/finished.dat)"),
    out: str = typer.Option(None, "--out", "-o", help=f"Output directory (defaults to {DEFAULT_OUTPUT_DIR})"),
    verbose: bool = typer.Option(False, "-v", "--verbose", help="Verbose logging"),
):
    """
    Reconcile input file with finished transcripts.
    
    With input file: Performs three-way reconciliation (input vs finished.dat vs transcripts).
    Without input file: Runs quick verification (finished.dat vs transcripts only).
    
    For full verification (batch_status.json + finished.dat), use 'lt verify --mode full' instead.
    
    Examples:
        lt reconcile                                    # Quick verification (no input file needed)
        lt reconcile --input urls.txt                   # Full reconciliation with input file
        lt reconcile --input urls.txt --out ~/transcripts  # Custom output directory
        lt verify --mode full                           # Full verification (all sources)
    """
    logger = configure_logging(verbose=verbose, log_file_prefix="reconcile")
    
    try:
        # Handle defaults properly - check if value is actually a string or OptionInfo
        if input is None or not isinstance(input, str):
            input_file = Path("inputfile.txt")
        else:
            input_file = Path(input).expanduser()
        
        if out is None or not isinstance(out, str):
            output_dir = Path(DEFAULT_OUTPUT_DIR).expanduser()
        else:
            output_dir = Path(out).expanduser()
        
        # Default finished file to output_dir if not provided
        if finished is None or not isinstance(finished, str):
            finished_file = output_dir / "finished.dat"
        else:
            finished_file = Path(finished).expanduser()
        
        # Check if input file exists - if not, run quick verification instead
        if not input_file.exists():
            # Set up paths for verification
            status_store_path = output_dir / "batch_status.json"
            pending_file_path = output_dir / "transcript-pending.md"
            
            # Initialize status store
            status_store = JsonStatusStore(status_store_path)
            
            # Run quick verification (finished.dat only)
            console.print("[blue]Running quick verification (finished.dat vs transcripts)...[/blue]")
            console.print("[dim]Tip: Use 'lt verify --mode full' for comprehensive verification[/dim]")
            
            result = verify_finished_dat(
                finished_file=finished_file,
                output_dir=output_dir,
                status_store=status_store,
                pending_file=pending_file_path,
                clean_finished=False,  # Don't clean finished.dat in quick mode
            )
            
            # Print Verification Report
            console.print(f"\n[bold]Quick Verification Report[/bold]")
            table = Table(show_header=True, header_style="bold")
            table.add_column("Metric")
            table.add_column("Count")
            table.add_row("Total Checked", str(result.total_checked))
            table.add_row("Missing Transcripts", f"[red]{result.missing_transcripts}[/red]")
            table.add_row("Status Updates", f"[yellow]{result.status_updates}[/yellow]")
            table.add_row("URLs Added to Pending", f"[blue]{len(result.urls_to_retry)}[/blue]")
            console.print(table)
            
            if result.pending_file_updated:
                console.print(f"\n[green]✓[/green] Updated pending file: {pending_file_path}")
            
            if result.missing_transcripts > 0:
                console.print(f"\n[yellow]⚠[/yellow] Use [bold]lt batch --input {pending_file_path.name}[/bold] to process missing videos.")
            
            if result.missing_transcripts == 0:
                console.print(f"\n[green]✓[/green] All transcripts verified successfully!")
            
            return
        
        # Full reconciliation path (input file exists)
        # Load original URLs for output generation
        input_urls = safe_read_lines(input_file)
        from local_transcribe.utils.youtube import extract_video_id
        input_ids = [extract_video_id(url) for url in input_urls]
        
        finished_urls = safe_read_lines(finished_file) if finished_file.exists() else []
        finished_ids = [extract_video_id(url) for url in finished_urls]
        
        # Run reconciliation
        report = reconcile_service(input_file, finished_file, output_dir)
        
        # Write output files
        outputs = write_reconcile_outputs(
            report, input_urls, input_ids, finished_urls, finished_ids, output_dir
        )
        
        # Update transcript-pending.md if it was used as input
        pending_file_updated = False
        if input_file.name == "transcript-pending.md":
            actually_pending_urls = [
                url for url, vid_id in zip(input_urls, input_ids)
                if vid_id in report.actually_pending
            ]
            if actually_pending_urls:
                safe_write_lines(input_file, actually_pending_urls)
                pending_file_updated = True
                console.print(f"\n[green]✓[/green] Updated {input_file.name} with {len(actually_pending_urls)} videos that need processing")
            else:
                # Remove all URLs if nothing is pending
                safe_write_lines(input_file, [])
                pending_file_updated = True
                console.print(f"\n[green]✓[/green] Cleared {input_file.name} - all videos have transcripts")
        
        # Print summary
        console.print("\n[bold]Full Reconciliation Report[/bold]")
        console.print(f"Input file: {report.input_unique} unique videos")
        console.print(f"Finished file: {report.finished_unique} unique videos")
        console.print(f"Transcript files: {report.transcript_count}")
        console.print(f"\n[green]Completed:[/green] {len(report.completed)} (in both input and finished.dat)")
        console.print(f"[yellow]Pending (not in finished.dat):[/yellow] {len(report.pending)}")
        console.print(f"[red]Actually Pending (no transcript file):[/red] {len(report.actually_pending)}")
        console.print(f"[red]Failed/Missing:[/red] {len(report.finished_but_no_file)}")
        
        if outputs:
            console.print("\n[bold]Generated files:[/bold]")
            for name, path in outputs.items():
                console.print(f"  - {name}: {path}")
        
    except Exception as e:
        logger.error(f"Reconciliation failed: {e}", exc_info=verbose)
        console.print(f"[red]✗[/red] Error: {e}")
        raise typer.Exit(1)


@app.command()
def verify(
    mode: str = typer.Option("full", "--mode", "-m", help="Verification mode: 'quick' or 'full'"),
    finished: str = typer.Option(None, "--finished", "-f", help="Path to finished.dat file (defaults to output_dir/finished.dat)"),
    status_store: str = typer.Option(None, "--status-store", "-s", help="Path to batch_status.json file (defaults to output_dir/batch_status.json)"),
    output_dir: str = typer.Option(None, "--output-dir", "-o", help=f"Transcript output directory (defaults to {DEFAULT_OUTPUT_DIR})"),
    pending_file: str = typer.Option(None, "--pending-file", "-p", help="Path to pending file (defaults to output_dir/transcript-pending.md)"),
    clean_finished: bool = typer.Option(None, "--clean-finished/--no-clean-finished", help="Remove invalid entries from finished.dat (default: True for full mode, False for quick)"),
    verbose: bool = typer.Option(False, "-v", "--verbose", help="Verbose logging"),
):
    """
    Verify transcripts exist for completed URLs and mark missing ones as pending.
    
    Quick mode: Checks finished.dat vs transcript files only.
    Full mode: Checks batch_status.json + finished.dat vs transcript files.
    
    Examples:
        lt verify                          # Full verification (default)
        lt verify --mode quick             # Quick verification (finished.dat only)
        lt verify --mode full              # Full verification (all sources)
        lt verify --clean-finished         # Clean finished.dat of invalid entries
    """
    logger = configure_logging(verbose=verbose, log_file_prefix="verify")
    
    try:
        # Validate mode
        if mode not in ("quick", "full"):
            console.print(f"[red]✗[/red] Error: Mode must be 'quick' or 'full', got '{mode}'")
            raise typer.Exit(1)
        
        # Handle defaults properly - check if value is actually a string or OptionInfo
        if output_dir is None or not isinstance(output_dir, str):
            output_path = Path(DEFAULT_OUTPUT_DIR).expanduser()
        else:
            output_path = Path(output_dir).expanduser()
        
        # Set defaults relative to output_dir
        if finished is None or not isinstance(finished, str):
            finished_path = output_path / "finished.dat"
        else:
            finished_path = Path(finished).expanduser()
        
        if status_store is None or not isinstance(status_store, str):
            status_store_path = output_path / "batch_status.json"
        else:
            status_store_path = Path(status_store).expanduser()
        
        if pending_file is None or not isinstance(pending_file, str):
            pending_path = output_path / "transcript-pending.md"
        else:
            pending_path = Path(pending_file).expanduser()
        
        # Set default for clean_finished based on mode
        if clean_finished is None:
            clean_finished = (mode == "full")
        
        # Initialize status store
        status_store_instance = JsonStatusStore(status_store_path)
        
        # Run verification
        if mode == "quick":
            logger.info(f"Running quick verification (finished.dat only)")
            result = verify_finished_dat(
                finished_path,
                output_path,
                status_store_instance,
                pending_path,
                clean_finished=clean_finished,
            )
        else:  # full
            logger.info(f"Running full verification (batch_status.json + finished.dat)")
            result = verify_full(
                finished_path,
                output_path,
                status_store_instance,
                pending_path,
                clean_finished=clean_finished,
            )
        
        # Print summary
        console.print("\n[bold]Verification Report[/bold]")
        table = Table(show_header=True, header_style="bold")
        table.add_column("Metric")
        table.add_column("Count")
        table.add_row("Total Checked", str(result.total_checked))
        table.add_row("Missing Transcripts", f"[red]{result.missing_transcripts}[/red]")
        table.add_row("Status Updates", f"[yellow]{result.status_updates}[/yellow]")
        table.add_row("URLs Added to Pending", f"[blue]{len(result.urls_to_retry)}[/blue]")
        console.print(table)
        
        if result.pending_file_updated:
            console.print(f"\n[green]✓[/green] Updated pending file: {pending_path}")
        
        if result.finished_cleaned:
            console.print(f"[green]✓[/green] Cleaned finished.dat: removed invalid entries")
        
        if result.missing_transcripts > 0:
            console.print(f"\n[yellow]⚠[/yellow] Found {result.missing_transcripts} missing transcript(s)")
            console.print(f"URLs have been added to pending file and marked as pending in status store")
        else:
            console.print(f"\n[green]✓[/green] All transcripts verified successfully!")
        
    except Exception as e:
        logger.error(f"Verification failed: {e}", exc_info=verbose)
        console.print(f"[red]✗[/red] Error: {e}")
        raise typer.Exit(1)


@app.command()
def status(
    store: str = typer.Option(
        None,
        help="Legacy status store file (compat only; ignored when queue is available)",
    ),
    output_dir: str = typer.Option(
        None,
        "--output-dir",
        "-o",
        help=f"Transcript / compat output directory (defaults to {DEFAULT_OUTPUT_DIR})",
    ),
    queue_dir: Optional[str] = typer.Option(None, "--queue-dir", help="Queue directory override"),
    write_compat: bool = typer.Option(
        True,
        "--write-compat/--no-write-compat",
        help="Regenerate batch_status.json from queue authority (compatibility only)",
    ),
    verbose: bool = typer.Option(False, "-v", "--verbose", help="Verbose logging"),
):
    """Show queue-authoritative status (worker lock + execution counts).

    ``batch_status.json`` is compatibility output only, not authority.
    """
    logger = configure_logging(verbose=verbose, log_file_prefix="status")

    try:
        if output_dir is None or not isinstance(output_dir, str):
            output_path = Path(DEFAULT_OUTPUT_DIR).expanduser()
        else:
            output_path = Path(output_dir).expanduser()

        from local_transcribe.services.queue_paths import QueuePathError, resolve_queue_dir
        from local_transcribe.services.queue_reporting import (
            summarize_queue,
            write_compat_batch_status,
        )

        qdir: Path | None = None
        try:
            qdir = resolve_queue_dir(
                queue_dir=Path(queue_dir) if queue_dir else None
            )
        except QueuePathError:
            qdir = None

        if qdir is not None:
            summary = summarize_queue(qdir)
            console.print("\n[bold]Queue Status[/bold] (authoritative)")
            console.print(f"[dim]queue: {qdir}[/dim]")
            table = Table(show_header=True, header_style="bold")
            table.add_column("State")
            table.add_column("Count")
            table.add_row("Total", str(summary.total))
            for name in (
                "pending",
                "processing",
                "retry",
                "completed",
                "failed",
                "cancelled",
            ):
                table.add_row(name, str(summary.counts.get(name, 0)))
            table.add_row(
                "completed (validated transcript)",
                f"[green]{summary.completed_validated}[/green]",
            )
            if summary.completed_missing_transcript:
                table.add_row(
                    "completed (missing transcript)",
                    f"[yellow]{summary.completed_missing_transcript}[/yellow]",
                )
            console.print(table)
            console.print(f"Worker NLM lock: {summary.worker_lock}")
            if summary.worker_state_active:
                console.print(
                    f"Worker state: active"
                    + (
                        f" (execution={summary.worker_current_execution_id})"
                        if summary.worker_current_execution_id
                        else ""
                    )
                )
            else:
                console.print("Worker state: inactive / none")

            if write_compat:
                compat_path = output_path / "batch_status.json"
                write_compat_batch_status(qdir, compat_path)
                console.print(
                    f"[dim]Wrote compatibility batch_status.json → {compat_path}[/dim]"
                )
            return

        # Legacy fallback — not authoritative
        console.print(
            "[yellow]![/yellow] Queue not configured; showing legacy "
            "batch_status.json (not authoritative)"
        )
        if store is None or not isinstance(store, str):
            store_path = output_path / "batch_status.json"
        else:
            store_path = Path(store).expanduser()
        status_store = JsonStatusStore(store_path)
        statuses = status_store.load()

        if not statuses:
            console.print("[yellow]No status data found[/yellow]")
            return

        counts = {"pending": 0, "processing": 0, "completed": 0, "failed": 0}
        for s in statuses.values():
            counts[s.status] = counts.get(s.status, 0) + 1

        console.print("\n[bold]Batch Status[/bold] (legacy compat)")
        table = Table(show_header=True, header_style="bold")
        table.add_column("Status")
        table.add_column("Count")
        table.add_row("Total", str(len(statuses)))
        table.add_row("Pending", f"[yellow]{counts['pending']}[/yellow]")
        table.add_row("Processing", f"[blue]{counts['processing']}[/blue]")
        table.add_row("Completed", f"[green]{counts['completed']}[/green]")
        table.add_row("Failed", f"[red]{counts['failed']}[/red]")
        console.print(table)

    except Exception as e:
        logger.error(f"Status check failed: {e}", exc_info=verbose)
        console.print(f"[red]✗[/red] Error: {e}")
        raise typer.Exit(1)


@app.command()
def report(
    store: str = typer.Option(
        None,
        help="Legacy status store file (compat only; ignored when queue is available)",
    ),
    output_dir: str = typer.Option(
        None,
        "--output-dir",
        "-o",
        help=f"Transcript / compat output directory (defaults to {DEFAULT_OUTPUT_DIR})",
    ),
    out: str = typer.Option("logs/failed_videos.txt", help="Output report file"),
    queue_dir: Optional[str] = typer.Option(None, "--queue-dir", help="Queue directory override"),
    write_compat: bool = typer.Option(
        True,
        "--write-compat/--no-write-compat",
        help="Also regenerate batch_status.json from queue authority",
    ),
    verbose: bool = typer.Option(False, "-v", "--verbose", help="Verbose logging"),
):
    """Generate failure report from queue authority (failed + cancelled)."""
    logger = configure_logging(verbose=verbose, log_file_prefix="report")

    try:
        if output_dir is None or not isinstance(output_dir, str):
            output_path = Path(DEFAULT_OUTPUT_DIR).expanduser()
        else:
            output_path = Path(output_dir).expanduser()

        from local_transcribe.services.queue_paths import QueuePathError, resolve_queue_dir
        from local_transcribe.services.queue_reporting import (
            write_compat_batch_status,
            write_failure_report,
        )

        try:
            qdir = resolve_queue_dir(
                queue_dir=Path(queue_dir) if queue_dir else None
            )
        except QueuePathError:
            qdir = None

        if qdir is not None:
            report_path, n = write_failure_report(qdir, Path(out))
            if write_compat:
                write_compat_batch_status(qdir, output_path / "batch_status.json")
            if n == 0:
                console.print("[green]No failed/cancelled executions[/green]")
                return
            console.print(f"[green]✓[/green] Report written: {report_path}")
            console.print(f"Failed/cancelled executions: {n}")
            return

        console.print(
            "[yellow]![/yellow] Queue not configured; using legacy "
            "batch_status.json (not authoritative)"
        )
        if store is None or not isinstance(store, str):
            store_path = output_path / "batch_status.json"
        else:
            store_path = Path(store).expanduser()
        status_store = JsonStatusStore(store_path)
        statuses = status_store.load()

        failed = [s for s in statuses.values() if s.status == "failed"]

        if not failed:
            console.print("[green]No failed videos[/green]")
            return

        report_path = Path(out)
        report_path.parent.mkdir(parents=True, exist_ok=True)

        from datetime import datetime

        with open(report_path, "w", encoding="utf-8") as f:
            f.write(f"Failed Videos Report - {datetime.now()}\n")
            f.write("=" * 80 + "\n\n")
            for v in failed:
                f.write(f"URL: {v.url}\n")
                f.write(f"Video ID: {v.video_id}\n")
                f.write(f"Attempts: {v.attempts}\n")
                f.write(f"Error: {v.error_message}\n")
                f.write("-" * 80 + "\n")

        console.print(f"[green]✓[/green] Report written: {report_path}")
        console.print(f"Failed videos: {len(failed)}")

    except Exception as e:
        logger.error(f"Report generation failed: {e}", exc_info=verbose)
        console.print(f"[red]✗[/red] Error: {e}")
        raise typer.Exit(1)


@app.command()
def update(
    stable: bool = typer.Option(
        False,
        "--stable",
        help="Install stable yt-dlp releases only (omit --pre)",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Print the pip command without modifying the environment",
    ),
    verbose: bool = typer.Option(False, "-v", "--verbose", help="Verbose logging"),
):
    """Update yt-dlp in the current lt runtime environment."""
    import sys

    from local_transcribe.utils.ytdlp_update import (
        format_pip_command,
        perform_yt_dlp_update,
        resolve_deno_on_path,
    )

    logger = configure_logging(verbose=verbose, log_file_prefix="update")

    try:
        deno_available, deno_version = resolve_deno_on_path()
        if not deno_available:
            console.print("[red]✗[/red] Deno is not on PATH")
            console.print("\n[bold]Deno is required for YouTube 2026 SABR support.[/bold]")
            console.print("\nInstall Deno and ensure it is on PATH, for example:")
            console.print("\n  [cyan]curl -fsSL https://deno.land/install.sh | sh[/cyan]")
            console.print("  [cyan]export DENO_INSTALL=\"$HOME/.deno\"[/cyan]")
            console.print("  [cyan]export PATH=\"$DENO_INSTALL/bin:$PATH\"[/cyan]")
            console.print("  [cyan]sudo ln -sf \"$DENO_INSTALL/bin/deno\" /usr/local/bin/deno[/cyan]")
            raise typer.Exit(1)

        console.print(f"[green]✓[/green] Deno: {deno_version or 'available'}")
        console.print("\n[bold]Updating yt-dlp in the current lt environment[/bold]")

        outcome = perform_yt_dlp_update(
            stable=stable,
            dry_run=dry_run,
            python_executable=sys.executable,
        )

        before = outcome.before_version or "unknown"
        console.print(f"Before: {before}")

        if dry_run:
            console.print(
                f"Dry run: {format_pip_command(outcome.pip_command)}",
                markup=False,
            )
            console.print(f"After: {before}")
            return

        if not outcome.success:
            console.print("[red]✗[/red] yt-dlp update failed")
            console.print(
                f"Command: {format_pip_command(outcome.pip_command)}",
                markup=False,
            )
            if outcome.pip_stderr_tail:
                console.print(f"stderr (tail):\n{outcome.pip_stderr_tail}")
            raise typer.Exit(1)

        after = outcome.after_version or "unknown"
        console.print(f"After: {after}")
        console.print("\n[green]✓[/green] yt-dlp update complete")

    except typer.Exit:
        raise
    except Exception as e:
        logger.error(f"Update failed: {e}", exc_info=verbose)
        console.print(f"[red]✗[/red] Error: {e}")
        raise typer.Exit(1)


@app.command()
def doctor(
    verbose: bool = typer.Option(False, "-v", "--verbose", help="Verbose logging"),
):
    """Run environment diagnostics."""
    import sys
    from local_transcribe.utils.doctor import run_diagnostics
    
    try:
        results = run_diagnostics()
        
        # Use console with explicit file=sys.stdout to ensure output
        output_console = Console(file=sys.stdout, force_terminal=True)
        output_console.print("\n[bold]Environment Diagnostics[/bold]")
        
        table = Table(show_header=True, header_style="bold")
        table.add_column("Check")
        table.add_column("Status")
        table.add_column("Details")
        
        for check_name, (status, details) in results.items():
            status_icon = "[green]✓[/green]" if status else "[red]✗[/red]"
            table.add_row(check_name, status_icon, details or "")
        
        output_console.print(table)
        
        # Exit with error if any critical checks failed
        if not all(status for status, _ in results.values()):
            raise typer.Exit(1)
        
    except Exception as e:
        output_console = Console(file=sys.stdout, force_terminal=True)
        output_console.print(f"[red]✗[/red] Error: {e}")
        if verbose:
            import traceback
            traceback.print_exc()
        raise typer.Exit(1)


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    version: bool = typer.Option(False, "--version", help="Show version and exit"),
):
    """Local transcription with Whisper (YouTube URLs or audio files)."""
    global _startup_warnings_shown
    
    # Handle --version flag
    if version:
        console.print(f"local-transcribe version {__version__}")
        raise typer.Exit(0)
    
    # First-run check: Auto-upgrade PyTorch to CUDA version if needed
    # Only run for actual commands (not --help), in pipx environment, and if marker doesn't exist
    if ctx.invoked_subcommand is not None:
        from local_transcribe.utils.doctor import (
            is_pipx_environment,
            has_pytorch_upgrade_marker,
            ensure_pytorch_cuda,
        )
        
        # Only attempt auto-upgrade in pipx environment and if marker doesn't exist
        if is_pipx_environment() and not has_pytorch_upgrade_marker():
            # Attempt to upgrade PyTorch to CUDA version if CUDA is available
            success, message = ensure_pytorch_cuda()
            if success:
                # Silent success - PyTorch was upgraded or already has CUDA
                pass
            elif "CUDA not available" in message:
                # CUDA not available - this is fine, keep CPU version
                pass
            elif "PyTorch not installed" in message:
                # PyTorch not installed yet - will be installed by dependencies
                pass
            # Other errors are logged but don't block execution
    
    # Show startup warnings once per session (only for actual commands, not --help)
    if not _startup_warnings_shown and ctx.invoked_subcommand is not None:
        from local_transcribe.utils.doctor import check_deno_with_guidance, check_pytorch_with_guidance
        
        # Check Deno
        deno_available, deno_info = check_deno_with_guidance()
        if not deno_available:
            console.print("\n[yellow]⚠️  Warning: Deno runtime not detected[/yellow]")
            console.print(f"[dim]{deno_info}[/dim]")
        
        # Check PyTorch (informational)
        pytorch_available, pytorch_info = check_pytorch_with_guidance()
        if not pytorch_available:
            console.print("\n[yellow]⚠️  Warning: PyTorch not detected[/yellow]")
            console.print(f"[dim]{pytorch_info}[/dim]")
        
        if not deno_available or not pytorch_available:
            console.print()  # Add spacing before command output
        
        _startup_warnings_shown = True
    
    # If a subcommand is being invoked, let it handle everything
    if ctx.invoked_subcommand is not None:
        return
    
    # No subcommand and no --version? Show help
    ctx.get_help()
    raise typer.Exit(0)


def main():
    """Entry point for the CLI."""
    app()


if __name__ == "__main__":
    main()

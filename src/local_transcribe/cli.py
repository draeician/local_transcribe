"""Command-line interface for local transcription."""

import os
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from local_transcribe import __version__
from local_transcribe.logging_setup import configure_logging
from local_transcribe.services.pipeline import BatchConfig, BatchPipeline
from local_transcribe.services.reconcile import reconcile as reconcile_service, write_reconcile_outputs
from local_transcribe.services.status_store import JsonStatusStore
from local_transcribe.services.verify_status import verify_finished_dat, verify_full
from local_transcribe.services.transcriber import TranscribeConfig, transcribe_url
from local_transcribe.utils.files import safe_read_lines, safe_write_lines, find_cookies_file
from local_transcribe.utils.youtube import is_valid_youtube_url

app = typer.Typer(help="Local YouTube transcription with Whisper")
console = Console(force_terminal=True)

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
    url: str = typer.Argument(..., help="YouTube video URL"),
    model: str = typer.Option(DEFAULT_MODEL, help="Whisper model"),
    device: str = typer.Option(DEFAULT_DEVICE, help="Device (cpu/cuda/auto)"),
    compute_type: str = typer.Option(DEFAULT_COMPUTE_TYPE, help="Compute type"),
    output_dir: str = typer.Option(str(DEFAULT_OUTPUT_DIR), help="Output directory"),
    keep_audio: bool = typer.Option(False, help="Keep downloaded audio"),
    cookies_from_browser: str = typer.Option(None, help="Browser to extract cookies from"),
    cookies_file: str = typer.Option(None, help="Path to cookies.txt file"),
    limit_rate: str = typer.Option(None, "--limit-rate", help="Max download rate (e.g., 200K, 4.2M)"),
    sleep_interval_requests: float = typer.Option(None, "--sleep-interval-requests", help="Seconds to sleep between yt-dlp requests"),
    verbose: bool = typer.Option(False, "-v", "--verbose", help="Verbose logging"),
):
    """Transcribe a single YouTube video."""
    logger = configure_logging(verbose=verbose, log_file_prefix="transcribe")
    
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
        # Expand ~ in output_dir
        output_path = Path(output_dir).expanduser()
        
        cfg = TranscribeConfig(
            model=model,
            device=device,
            compute_type=compute_type,
            output_dir=output_path,
            keep_audio=keep_audio,
            cookies_from_browser=cookies_from_browser,
            cookies_file=cookies_file,
            limit_rate=limit_rate,
            sleep_interval_requests=sleep_interval_requests if sleep_interval_requests is not None else None,
        )
        
        logger.info(f"Transcribing: {url}")
        output_path = transcribe_url(url, cfg)
        console.print(f"[green]✓[/green] Done. Wrote: {output_path}")
        
    except Exception as e:
        logger.error(f"Transcription failed: {e}", exc_info=verbose)
        console.print(f"[red]✗[/red] Error: {e}")
        raise typer.Exit(1)


@app.command()
def batch(
    input: str = typer.Option(None, "--input", "-i", help="Input file with URLs (defaults to inputfile.txt in current directory)"),
    resume: bool = typer.Option(False, help="Resume from previous run"),
    max_retries: int = typer.Option(2, help="Max retry attempts"),
    model: str = typer.Option(DEFAULT_MODEL, help="Whisper model"),
    device: str = typer.Option(DEFAULT_DEVICE, help="Device (cpu/cuda/auto)"),
    compute_type: str = typer.Option(DEFAULT_COMPUTE_TYPE, help="Compute type"),
    output_dir: str = typer.Option(None, "--output-dir", "-o", help=f"Output directory (defaults to {DEFAULT_OUTPUT_DIR})"),
    cookies_from_browser: str = typer.Option(None, help="Browser to extract cookies from"),
    cookies_file: str = typer.Option(None, help="Path to cookies.txt file"),
    sleep_interval: float = typer.Option(1.0, "--sleep-interval", help="Seconds to sleep between videos"),
    limit_rate: str = typer.Option(None, "--limit-rate", help="Max download rate (e.g., 200K, 4.2M)"),
    sleep_interval_requests: float = typer.Option(None, "--sleep-interval-requests", help="Seconds to sleep between yt-dlp requests"),
    verbose: bool = typer.Option(False, "-v", "--verbose", help="Verbose logging"),
):
    """
    Process multiple videos in batch.
    
    Transcribes all URLs from an input file, with automatic resume capability.
    Status is tracked in batch_status.json and completed URLs are logged to finished.dat.
    
    Examples:
        lt batch --input urls.txt              # Process URLs from file
        lt batch --input urls.txt --resume     # Resume from previous run
        lt batch --input urls.txt --model large # Use larger model
    """
    logger = configure_logging(verbose=verbose)
    
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
    store: str = typer.Option(None, help="Status store file (defaults to output_dir/batch_status.json)"),
    output_dir: str = typer.Option(None, "--output-dir", "-o", help=f"Transcript output directory (defaults to {DEFAULT_OUTPUT_DIR})"),
    verbose: bool = typer.Option(False, "-v", "--verbose", help="Verbose logging"),
):
    """Show batch processing status."""
    logger = configure_logging(verbose=verbose, log_file_prefix="status")
    
    try:
        # Handle defaults properly
        if output_dir is None or not isinstance(output_dir, str):
            output_path = Path(DEFAULT_OUTPUT_DIR).expanduser()
        else:
            output_path = Path(output_dir).expanduser()
        
        if store is None or not isinstance(store, str):
            store_path = output_path / "batch_status.json"
        else:
            store_path = Path(store).expanduser()
        status_store = JsonStatusStore(store_path)
        statuses = status_store.load()
        
        if not statuses:
            console.print("[yellow]No status data found[/yellow]")
            return
        
        # Count by status
        counts = {"pending": 0, "processing": 0, "completed": 0, "failed": 0}
        for s in statuses.values():
            counts[s.status] = counts.get(s.status, 0) + 1
        
        console.print("\n[bold]Batch Status[/bold]")
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
    store: str = typer.Option(None, help="Status store file (defaults to output_dir/batch_status.json)"),
    output_dir: str = typer.Option(None, "--output-dir", "-o", help=f"Transcript output directory (defaults to {DEFAULT_OUTPUT_DIR})"),
    out: str = typer.Option("logs/failed_videos.txt", help="Output report file"),
    verbose: bool = typer.Option(False, "-v", "--verbose", help="Verbose logging"),
):
    """Generate failure report."""
    logger = configure_logging(verbose=verbose, log_file_prefix="report")
    
    try:
        # Handle defaults properly
        if output_dir is None or not isinstance(output_dir, str):
            output_path = Path(DEFAULT_OUTPUT_DIR).expanduser()
        else:
            output_path = Path(output_dir).expanduser()
        
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
        
        output_path = Path(out)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        from datetime import datetime
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(f"Failed Videos Report - {datetime.now()}\n")
            f.write("=" * 80 + "\n\n")
            for v in failed:
                f.write(f"URL: {v.url}\n")
                f.write(f"Video ID: {v.video_id}\n")
                f.write(f"Attempts: {v.attempts}\n")
                f.write(f"Error: {v.error_message}\n")
                f.write("-" * 80 + "\n")
        
        console.print(f"[green]✓[/green] Report written: {output_path}")
        console.print(f"Failed videos: {len(failed)}")
        
    except Exception as e:
        logger.error(f"Report generation failed: {e}", exc_info=verbose)
        console.print(f"[red]✗[/red] Error: {e}")
        raise typer.Exit(1)


@app.command()
def update(
    verbose: bool = typer.Option(False, "-v", "--verbose", help="Verbose logging"),
):
    """Update local-transcribe package with Deno verification."""
    import subprocess
    import sys
    
    logger = configure_logging(verbose=verbose, log_file_prefix="update")
    
    try:
        # Step 1: Check if Deno is installed
        console.print("[blue]Checking for Deno runtime...[/blue]")
        deno_available = False
        deno_version = None
        
        try:
            result = subprocess.run(
                ["deno", "--version"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                deno_available = True
                deno_version = result.stdout.split('\n')[0].strip()
                console.print(f"[green]✓[/green] Deno found: {deno_version}")
            else:
                console.print("[red]✗[/red] Deno command returned error")
        except FileNotFoundError:
            # Check common locations
            import os
            common_paths = [
                "/usr/local/bin/deno",
                os.path.expanduser("~/.deno/bin/deno"),
            ]
            for path in common_paths:
                if os.path.exists(path) and os.access(path, os.X_OK):
                    try:
                        result = subprocess.run(
                            [path, "--version"],
                            capture_output=True,
                            text=True,
                            timeout=5,
                        )
                        if result.returncode == 0:
                            deno_available = True
                            deno_version = result.stdout.split('\n')[0].strip()
                            console.print(f"[green]✓[/green] Deno found: {deno_version} (at {path})")
                            break
                    except Exception:
                        continue
        
        if not deno_available:
            console.print("\n[red]✗[/red] Deno is not installed or not accessible")
            console.print("\n[bold]Deno is required for YouTube 2026 SABR support.[/bold]")
            console.print("\nPlease install Deno first:")
            console.print("\n  [cyan]curl -fsSL https://deno.land/install.sh | sh[/cyan]")
            console.print("  [cyan]export DENO_INSTALL=\"$HOME/.deno\"[/cyan]")
            console.print("  [cyan]export PATH=\"$DENO_INSTALL/bin:$PATH\"[/cyan]")
            console.print("  [cyan]sudo ln -sf \"$DENO_INSTALL/bin/deno\" /usr/local/bin/deno[/cyan]")
            console.print("\nFor more information, visit: [link=https://deno.com/]https://deno.com/[/link]")
            raise typer.Exit(1)
        
        # Step 2: Detect installation method
        console.print("\n[blue]Detecting installation method...[/blue]")
        is_pipx = False
        is_local = False
        
        # Check if installed via pipx
        try:
            result = subprocess.run(
                ["pipx", "list"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0 and "local-transcribe" in result.stdout:
                is_pipx = True
                console.print("[green]✓[/green] Detected pipx installation")
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
        
        # Check if in local development directory
        if not is_pipx:
            current_dir = Path.cwd()
            pyproject_path = current_dir / "pyproject.toml"
            src_path = current_dir / "src" / "local_transcribe"
            
            if pyproject_path.exists() and src_path.exists():
                is_local = True
                console.print("[green]✓[/green] Detected local development installation")
        
        if not is_pipx and not is_local:
            console.print("[yellow]⚠[/yellow] Could not detect installation method")
            console.print("Attempting pipx upgrade as default...")
            is_pipx = True
        
        # Step 3: Update the package
        console.print("\n[blue]Updating local-transcribe...[/blue]")
        
        if is_pipx:
            try:
                # Try to upgrade from git if possible, otherwise just upgrade
                result = subprocess.run(
                    ["pipx", "upgrade", "local-transcribe"],
                    capture_output=True,
                    text=True,
                    timeout=300,
                )
                if result.returncode == 0:
                    console.print("[green]✓[/green] Package updated via pipx")
                else:
                    console.print(f"[red]✗[/red] pipx upgrade failed: {result.stderr}")
                    raise typer.Exit(1)
            except FileNotFoundError:
                console.print("[red]✗[/red] pipx not found. Please install pipx first.")
                raise typer.Exit(1)
            except subprocess.TimeoutExpired:
                console.print("[red]✗[/red] Update timed out")
                raise typer.Exit(1)
        
        elif is_local:
            try:
                # Update local installation
                result = subprocess.run(
                    [sys.executable, "-m", "pip", "install", "-e", ".", "--upgrade"],
                    capture_output=True,
                    text=True,
                    timeout=300,
                )
                if result.returncode == 0:
                    console.print("[green]✓[/green] Local package updated")
                else:
                    console.print(f"[red]✗[/red] pip install failed: {result.stderr}")
                    raise typer.Exit(1)
            except subprocess.TimeoutExpired:
                console.print("[red]✗[/red] Update timed out")
                raise typer.Exit(1)
        
        # Step 4: Verify installation
        console.print("\n[blue]Verifying installation...[/blue]")
        try:
            from local_transcribe.utils.doctor import run_diagnostics
            results = run_diagnostics()
            
            # Check critical components
            critical_checks = ["Deno Runtime", "FFmpeg", "yt-dlp"]
            all_critical_ok = all(
                results.get(check, (False, ""))[0] for check in critical_checks
            )
            
            if all_critical_ok:
                console.print("[green]✓[/green] All critical checks passed")
            else:
                console.print("[yellow]⚠[/yellow] Some checks failed. Run 'lt doctor' for details.")
                # Show failed checks
                for check in critical_checks:
                    status, details = results.get(check, (False, ""))
                    if not status:
                        console.print(f"  [red]✗[/red] {check}: {details}")
        except Exception as e:
            logger.warning(f"Verification failed: {e}", exc_info=verbose)
            console.print(f"[yellow]⚠[/yellow] Verification failed: {e}")
            console.print("Run 'lt doctor' manually to check your installation")
        
        console.print("\n[green]✓[/green] Update complete!")
        
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
    """Local YouTube transcription with Whisper."""
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

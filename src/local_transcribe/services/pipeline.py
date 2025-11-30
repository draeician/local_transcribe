"""Batch transcription pipeline orchestrator."""

import logging
import signal
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set

from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn, TimeElapsedColumn, TimeRemainingColumn

from local_transcribe.services.rate_limiter import RateLimiter
from local_transcribe.services.status_store import JsonStatusStore, StatusStore, TranscriptStatus
from local_transcribe.services.transcriber import TranscribeConfig, transcribe_url
from local_transcribe.services.downloader import RateLimitError
from local_transcribe.utils.files import safe_append_line, safe_read_lines, validate_transcript_file
from local_transcribe.utils.youtube import extract_video_id, is_valid_youtube_url


@dataclass
class BatchConfig:
    """Configuration for batch transcription."""
    input_file: Path
    output_dir: Path
    model: str = "medium"
    device: str = "cuda"
    compute_type: str = "float16"
    max_retries: int = 2
    status_store: Optional[StatusStore] = None
    cookies_from_browser: Optional[str] = None
    cookies_file: Optional[str] = None
    timeout_seconds: int = 1800  # 30 minutes
    finished_file: Optional[Path] = None  # Defaults to output_dir / "finished.dat"
    sleep_interval_between_videos: float = 1.0
    limit_rate: Optional[str] = None
    sleep_interval_requests: Optional[float] = None


@dataclass
class BatchSummary:
    """Summary of batch processing results."""
    total: int = 0
    completed: int = 0
    failed: int = 0
    skipped: int = 0
    pending: int = 0


class BatchPipeline:
    """Orchestrates batch transcription with resume capability."""
    
    def __init__(self, config: BatchConfig):
        """
        Initialize batch pipeline.
        
        Args:
            config: Batch configuration
        """
        self.config = config
        self.config.output_dir.mkdir(parents=True, exist_ok=True)
        
        # Set finished_file default to output_dir if not provided
        if config.finished_file is None:
            self.config.finished_file = config.output_dir / "finished.dat"
        
        # Initialize status store
        if config.status_store is None:
            status_file = config.output_dir / "batch_status.json"
            self.status_store = JsonStatusStore(status_file)
        else:
            self.status_store = config.status_store
        
        # Initialize logger
        self.logger = logging.getLogger('local_transcribe.pipeline')
        
        # Initialize rate limiter
        rate_limit_file = config.output_dir / "rate_limits.json"
        self.rate_limiter = RateLimiter(rate_limit_file)
        
        # Interrupt handling
        self.interrupted = False
        self.current_audio_file: Optional[Path] = None
        
        # Register signal handler for graceful shutdown
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
        
        # Statistics
        self.stats = BatchSummary()
        self.videos: Dict[str, TranscriptStatus] = {}
    
    def _signal_handler(self, signum, frame):
        """Handle interrupt signals (SIGINT/SIGTERM) gracefully."""
        if self.interrupted:
            # Second interrupt - force exit
            self.logger.error("Force exit requested. Exiting immediately.")
            raise KeyboardInterrupt
        
        self.interrupted = True
        self.logger.warning("Interrupt received. Cleaning up and saving progress...")
        
        # Clean up current audio file if it exists
        if self.current_audio_file and self.current_audio_file.exists():
            try:
                self.current_audio_file.unlink()
                self.logger.info(f"Cleaned up audio file: {self.current_audio_file}")
            except Exception as e:
                self.logger.warning(f"Failed to clean up audio file {self.current_audio_file}: {e}")
        
        # Save current status
        if hasattr(self, 'status_store') and hasattr(self, 'videos'):
            for vid_id, status in self.videos.items():
                if status.status == "processing":
                    status.status = "pending"
                    self.status_store.set(vid_id, status)
            self.logger.info("Progress saved. Current video marked as pending for retry.")
    
    def load_input_urls(self) -> List[str]:
        """
        Load URLs from input file, removing duplicates and invalid entries.
        
        Returns:
            List of valid, unique URLs
        """
        if not self.config.input_file.exists():
            raise FileNotFoundError(f"Input file not found: {self.config.input_file}")
        
        urls = []
        seen_ids: Set[str] = set()
        
        for line in safe_read_lines(self.config.input_file):
            # Skip comments
            if line.startswith('#'):
                continue
            
            # Skip invalid URLs
            if not is_valid_youtube_url(line):
                continue
            
            vid_id = extract_video_id(line)
            
            # Skip duplicates
            if vid_id in seen_ids:
                continue
            
            seen_ids.add(vid_id)
            urls.append(line)
        
        return urls
    
    def check_existing_transcript(self, video_id: str) -> bool:
        """
        Check if a valid transcript file already exists.
        
        Args:
            video_id: Video ID to check
            
        Returns:
            True if valid transcript exists
        """
        json_path = self.config.output_dir / f"{video_id}.json"
        return validate_transcript_file(json_path)
    
    def initialize_videos(self, urls: List[str], resume: bool = False) -> None:
        """
        Initialize video tracking, checking existing files and status.
        
        Args:
            urls: List of URLs to process
            resume: If True, load existing status
        """
        # Load existing status if resuming
        if resume:
            self.videos = self.status_store.load()
        
        for url in urls:
            vid_id = extract_video_id(url)
            
            # If already tracked from previous run, keep that status
            if vid_id in self.videos:
                # But re-check if file exists now
                if self.check_existing_transcript(vid_id):
                    self.videos[vid_id].status = "completed"
                    self.videos[vid_id].output_file = str(self.config.output_dir / f"{vid_id}.json")
                continue
            
            # Check if transcript already exists
            if self.check_existing_transcript(vid_id):
                self.videos[vid_id] = TranscriptStatus(
                    url=url,
                    video_id=vid_id,
                    status="completed",
                    output_file=str(self.config.output_dir / f"{vid_id}.json")
                )
            else:
                self.videos[vid_id] = TranscriptStatus(
                    url=url,
                    video_id=vid_id,
                    status="pending"
                )
        
        self._update_stats()
    
    def _update_stats(self) -> None:
        """Update statistics from current video statuses."""
        self.stats.total = len(self.videos)
        self.stats.completed = sum(1 for v in self.videos.values() if v.status == "completed")
        self.stats.failed = sum(1 for v in self.videos.values() if v.status == "failed")
        self.stats.skipped = sum(1 for v in self.videos.values() if v.status == "completed" and v.attempts == 0)
        self.stats.pending = sum(1 for v in self.videos.values() if v.status == "pending")
    
    def _set_current_audio_file(self, audio_path: Path):
        """Callback to track current audio file for cleanup on interrupt."""
        self.current_audio_file = audio_path
    
    def transcribe_video(self, status: TranscriptStatus, cleanup_callback=None) -> bool:
        """
        Transcribe a single video.
        
        Args:
            status: TranscriptStatus for the video
            
        Returns:
            True if transcription succeeded
        """
        vid_id = status.video_id
        status.status = "processing"
        status.attempts += 1
        status.last_attempt = datetime.now().isoformat()
        self.status_store.set(vid_id, status)
        
        start_time = time.time()
        
        try:
            # Create transcription config
            cfg = TranscribeConfig(
                model=self.config.model,
                device=self.config.device,
                compute_type=self.config.compute_type,
                output_dir=self.config.output_dir,
                keep_audio=False,
                cookies_from_browser=self.config.cookies_from_browser,
                cookies_file=self.config.cookies_file,
                limit_rate=self.config.limit_rate,
                sleep_interval_requests=self.config.sleep_interval_requests,
            )
            
            # Transcribe
            output_path = transcribe_url(status.url, cfg)
            
            duration = time.time() - start_time
            status.duration_seconds = duration
            
            # Verify output file
            if output_path.exists() and validate_transcript_file(output_path):
                status.status = "completed"
                status.output_file = str(output_path)
                status.error_message = None
                self.status_store.set(vid_id, status)
                
                # Update finished.dat only on verified success
                safe_append_line(self.config.finished_file, status.url)
                
                return True
            else:
                status.error_message = "Output file validation failed"
                status.status = "failed"
                self.status_store.set(vid_id, status)
                return False
        
        except Exception as e:
            duration = time.time() - start_time
            status.duration_seconds = duration
            status.error_message = str(e)[:500]  # Truncate long errors
            
            # Check if should retry
            if status.attempts < self.config.max_retries:
                status.status = "pending"
            else:
                status.status = "failed"
            
            self.status_store.set(vid_id, status)
            return False
    
    def run(self, resume: bool = False) -> BatchSummary:
        """
        Run batch processing.
        
        Args:
            resume: If True, resume from previous run
            
        Returns:
            BatchSummary with statistics
        """
        # Load URLs
        urls = self.load_input_urls()
        self.logger.info(f"Loaded {len(urls)} URLs from {self.config.input_file}")
        
        # Initialize video tracking
        self.initialize_videos(urls, resume=resume)
        
        # Get list of pending videos
        pending_videos = [(vid_id, status) for vid_id, status in self.videos.items() 
                         if status.status == "pending"]
        
        if not pending_videos:
            self.logger.info("No pending videos to process")
            return self.stats
        
        self.logger.info(f"Starting batch processing: {len(pending_videos)} pending videos")
        self.logger.info(f"Model: {self.config.model}, Device: {self.config.device}, Compute Type: {self.config.compute_type}")
        
        # Display rate limit stats at start
        stats = self.rate_limiter.get_stats()
        self.logger.info(
            f"Rate limits: {stats['requests_this_hour']}/{stats['max_per_hour']} per hour "
            f"({stats['hour_percent']:.1f}%), {stats['requests_today']}/{stats['max_per_day']} per day "
            f"({stats['day_percent']:.1f}%)"
        )
        if stats['total_429_errors'] > 0:
            self.logger.warning(f"Total 429 errors encountered: {stats['total_429_errors']}")
        
        # Create progress bar
        with Progress(
            SpinnerColumn(),
            TextColumn("[bold blue]{task.fields[current_url]}"),
            BarColumn(),
            TaskProgressColumn(),
            TextColumn("•"),
            TimeElapsedColumn(),
            TextColumn("•"),
            TimeRemainingColumn(),
            TextColumn("• [cyan]{task.fields[last_duration]}"),
        ) as progress:
            
            task = progress.add_task(
                "[cyan]Processing videos...", 
                total=len(pending_videos),
                current_url="Initializing...",
                last_duration="--"
            )
            
            # Process all pending videos
            for idx, (vid_id, status) in enumerate(pending_videos, 1):
                # Check for interrupt before processing
                if self.interrupted:
                    self.logger.warning("Batch processing interrupted by user")
                    break
                
                # Update progress with current URL
                current_url_display = f"{status.video_id} ({idx}/{len(pending_videos)})"
                progress.update(task, current_url=current_url_display)
                
                # Check rate limits before processing
                should_warn, warning_msg = self.rate_limiter.check_limits()
                if should_warn:
                    self.logger.warning(f"Rate limit warning: {warning_msg}")
                
                # Log processing start
                self.logger.info(f"[{idx}/{len(pending_videos)}] Processing: {status.url} (ID: {vid_id})")
                
                # Transcribe the video
                start_time = time.time()
                try:
                    success = self.transcribe_video(status, cleanup_callback=self._set_current_audio_file)
                    duration = time.time() - start_time
                    
                    # Record successful request
                    self.rate_limiter.record_request()
                except RateLimitError as e:
                    # Handle rate limit error
                    self.rate_limiter.record_429_error()
                    duration = time.time() - start_time
                    status.error_message = f"Rate limit error: {e}"
                    status.duration_seconds = duration
                    
                    # Mark for retry if under max attempts
                    if status.attempts < self.config.max_retries:
                        status.status = "pending"
                        self.logger.warning(f"Rate limited {vid_id}, will retry later")
                    else:
                        status.status = "failed"
                        self.logger.error(f"Rate limited {vid_id}, max retries reached")
                    
                    self.status_store.set(vid_id, status)
                    success = False
                    
                    # Increase sleep interval after 429
                    recommended_delay = self.rate_limiter.get_recommended_delay()
                    if recommended_delay > self.config.sleep_interval_between_videos:
                        self.logger.info(f"Increasing sleep interval to {recommended_delay:.1f}s")
                        self.config.sleep_interval_between_videos = recommended_delay
                finally:
                    # Clear current audio file after processing
                    self.current_audio_file = None
                
                # Format duration for display
                if duration < 60:
                    duration_str = f"{duration:.1f}s"
                elif duration < 3600:
                    mins = int(duration // 60)
                    secs = int(duration % 60)
                    duration_str = f"{mins}m {secs}s"
                else:
                    hours = int(duration // 3600)
                    mins = int((duration % 3600) // 60)
                    duration_str = f"{hours}h {mins}m"
                
                # Log completion/failure
                if success:
                    self.logger.info(f"✓ Completed {vid_id} in {duration:.1f}s")
                else:
                    error = status.error_message or "Unknown error"
                    self.logger.warning(f"✗ Failed {vid_id} after {duration:.1f}s: {error}")
                
                self._update_stats()
                
                # Update progress
                progress.update(
                    task, 
                    advance=1,
                    last_duration=f"Last: {duration_str} {'✓' if success else '✗'}"
                )
                
                # Brief pause between videos
                if idx < len(pending_videos):  # Don't sleep after last video
                    time.sleep(self.config.sleep_interval_between_videos)
        
        # Log final summary
        self.logger.info(
            f"Batch complete - Total: {self.stats.total}, "
            f"Completed: {self.stats.completed}, "
            f"Failed: {self.stats.failed}, "
            f"Skipped: {self.stats.skipped}, "
            f"Pending: {self.stats.pending}"
        )
        
        # Display final rate limit stats
        stats = self.rate_limiter.get_stats()
        self.logger.info(
            f"Final rate limits: {stats['requests_this_hour']}/{stats['max_per_hour']} per hour "
            f"({stats['hour_percent']:.1f}%), {stats['requests_today']}/{stats['max_per_day']} per day "
            f"({stats['day_percent']:.1f}%)"
        )
        if stats['total_429_errors'] > 0:
            self.logger.warning(
                f"Total 429 errors this session: {stats['total_429_errors']}. "
                f"Consider increasing --sleep-interval between videos."
            )
        
        return self.stats
    
    def generate_reports(self, log_dir: Path) -> None:
        """
        Generate failure reports.
        
        Args:
            log_dir: Directory for report files
        """
        log_dir.mkdir(parents=True, exist_ok=True)
        
        # Failed videos report
        failed_videos = [v for v in self.videos.values() if v.status == "failed"]
        if failed_videos:
            failed_report = log_dir / "failed_videos.txt"
            with open(failed_report, 'w', encoding='utf-8') as f:
                f.write(f"Failed Videos Report - {datetime.now()}\n")
                f.write("=" * 80 + "\n\n")
                for v in failed_videos:
                    f.write(f"URL: {v.url}\n")
                    f.write(f"Video ID: {v.video_id}\n")
                    f.write(f"Attempts: {v.attempts}\n")
                    f.write(f"Error: {v.error_message}\n")
                    f.write("-" * 80 + "\n")


def run_batch(config: BatchConfig, resume: bool = False) -> BatchSummary:
    """
    Run batch transcription pipeline.
    
    Args:
        config: Batch configuration
        resume: If True, resume from previous run
        
    Returns:
        BatchSummary with statistics
    """
    pipeline = BatchPipeline(config)
    return pipeline.run(resume=resume)


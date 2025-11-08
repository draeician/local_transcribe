"""Batch transcription pipeline orchestrator."""

import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set

from local_transcribe.services.status_store import JsonStatusStore, StatusStore, TranscriptStatus
from local_transcribe.services.transcriber import TranscribeConfig, transcribe_url
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
    finished_file: Path = Path("finished.dat")


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
        
        # Initialize status store
        if config.status_store is None:
            status_file = Path("batch_status.json")
            self.status_store = JsonStatusStore(status_file)
        else:
            self.status_store = config.status_store
        
        # Statistics
        self.stats = BatchSummary()
        self.videos: Dict[str, TranscriptStatus] = {}
    
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
    
    def transcribe_video(self, status: TranscriptStatus) -> bool:
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
        
        # Initialize video tracking
        self.initialize_videos(urls, resume=resume)
        
        # Process all pending videos
        for vid_id, status in list(self.videos.items()):
            if status.status == "pending":
                self.transcribe_video(status)
                self._update_stats()
                
                # Brief pause between videos
                time.sleep(1)
        
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


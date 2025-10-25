#!/usr/bin/env python3
"""
Robust batch transcription manager with resume capability, failure tracking, and logging.

Features:
- Resume from interruption (checks actual output files)
- Detailed logging (per-video + master log)
- Failure tracking with retry logic
- Progress reporting
- Status file for tracking in-progress work
- Validates output files

Usage:
    python batch_transcribe.py --input inputfile.txt [options]
    python batch_transcribe.py --resume  # Resume previous run
    python batch_transcribe.py --clear-completed  # Clear completed videos from status file
"""

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional, Set, List, Dict
from urllib.parse import urlparse, parse_qs


@dataclass
class TranscriptStatus:
    """Track status of a single video transcription."""
    url: str
    video_id: str
    status: str  # pending, processing, completed, failed
    attempts: int = 0
    last_attempt: Optional[str] = None
    error_message: Optional[str] = None
    output_file: Optional[str] = None
    duration_seconds: Optional[float] = None


class BatchTranscriber:
    """Manages batch transcription with resume capability and failure tracking."""
    
    def __init__(
        self,
        input_file: Path,
        output_dir: Path,
        model: str = "medium",
        device: str = "cuda",
        compute_type: str = "float16",
        max_retries: int = 2,
        log_dir: Optional[Path] = None,
        status_file: Optional[Path] = None,
        cookies_from_browser: Optional[str] = None,
        cookies_file: Optional[str] = None,
    ):
        self.input_file = input_file
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        self.model = model
        self.device = device
        self.compute_type = compute_type
        self.max_retries = max_retries
        self.cookies_from_browser = cookies_from_browser
        self.cookies_file = cookies_file
        
        # Setup logging
        self.log_dir = Path(log_dir or "logs")
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.setup_logging()
        
        # Status file for resume capability
        self.status_file = Path(status_file or "batch_status.json")
        
        # Track all videos
        self.videos: Dict[str, TranscriptStatus] = {}
        
        # Statistics
        self.stats = {
            "total": 0,
            "completed": 0,
            "failed": 0,
            "skipped": 0,
            "pending": 0,
        }
    
    def setup_logging(self):
        """Setup logging to both file and console."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_file = self.log_dir / f"batch_transcribe_{timestamp}.log"
        
        # Create formatters
        detailed_formatter = logging.Formatter(
            '%(asctime)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        simple_formatter = logging.Formatter('%(message)s')
        
        # File handler - detailed
        file_handler = logging.FileHandler(log_file)
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(detailed_formatter)
        
        # Console handler - simplified
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(simple_formatter)
        
        # Setup logger
        self.logger = logging.getLogger('BatchTranscriber')
        self.logger.setLevel(logging.DEBUG)
        self.logger.addHandler(file_handler)
        self.logger.addHandler(console_handler)
        
        self.logger.info(f"Logging to: {log_file}")
    
    def extract_video_id(self, url: str) -> str:
        """Extract YouTube video ID from URL."""
        url = url.strip()
        
        if "youtube.com/watch" in url:
            parsed = urlparse(url)
            qs = parse_qs(parsed.query)
            return qs.get('v', [''])[0]
        elif "youtube.com/shorts/" in url:
            return url.split("/shorts/")[-1].split("?")[0]
        elif "youtube.com/live/" in url:
            return url.split("/live/")[-1].split("?")[0]
        elif "youtu.be/" in url:
            return url.split("youtu.be/")[-1].split("?")[0]
        
        return url  # Return as-is if can't extract
    
    def load_input_urls(self) -> List[str]:
        """Load URLs from input file, removing duplicates and invalid entries."""
        if not self.input_file.exists():
            self.logger.error(f"Input file not found: {self.input_file}")
            sys.exit(1)
        
        urls = []
        seen_ids = set()
        
        with open(self.input_file, 'r', encoding='utf-8') as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                
                # Skip empty lines and comments
                if not line or line.startswith('#'):
                    continue
                
                # Skip invalid URLs
                if "results?search" in line:
                    self.logger.warning(f"Skipping invalid URL at line {line_num}: {line}")
                    continue
                
                vid_id = self.extract_video_id(line)
                
                # Skip duplicates
                if vid_id in seen_ids:
                    self.logger.debug(f"Skipping duplicate video ID {vid_id} at line {line_num}")
                    continue
                
                seen_ids.add(vid_id)
                urls.append(line)
        
        self.logger.info(f"Loaded {len(urls)} unique URLs from {self.input_file}")
        return urls
    
    def check_existing_transcript(self, video_id: str) -> bool:
        """Check if a valid transcript file already exists."""
        json_path = self.output_dir / f"{video_id}.json"
        
        if not json_path.exists():
            return False
        
        # Validate it's actual valid JSON with expected structure
        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                # Check for required fields
                if "transcript" in data and "metadata" in data:
                    return True
        except (json.JSONDecodeError, OSError):
            self.logger.warning(f"Invalid or corrupted transcript file: {json_path}")
            return False
        
        return False
    
    def load_status(self) -> bool:
        """Load status from previous run if exists."""
        if not self.status_file.exists():
            return False
        
        try:
            with open(self.status_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            for vid_id, status_data in data.items():
                self.videos[vid_id] = TranscriptStatus(**status_data)
            
            self.logger.info(f"Loaded status for {len(self.videos)} videos from previous run")
            return True
        except Exception as e:
            self.logger.error(f"Failed to load status file: {e}")
            return False
    
    def save_status(self):
        """Save current status to disk for resume capability."""
        try:
            with open(self.status_file, 'w', encoding='utf-8') as f:
                data = {vid_id: asdict(status) for vid_id, status in self.videos.items()}
                json.dump(data, f, indent=2)
        except Exception as e:
            self.logger.error(f"Failed to save status file: {e}")
    
    def initialize_videos(self, urls: List[str]):
        """Initialize video tracking, checking existing files and status."""
        for url in urls:
            vid_id = self.extract_video_id(url)
            
            # If already tracked from previous run, keep that status
            if vid_id in self.videos:
                # But re-check if file exists now
                if self.check_existing_transcript(vid_id):
                    self.videos[vid_id].status = "completed"
                    self.videos[vid_id].output_file = str(self.output_dir / f"{vid_id}.json")
                continue
            
            # Check if transcript already exists
            if self.check_existing_transcript(vid_id):
                self.videos[vid_id] = TranscriptStatus(
                    url=url,
                    video_id=vid_id,
                    status="completed",
                    output_file=str(self.output_dir / f"{vid_id}.json")
                )
                self.stats["skipped"] += 1
            else:
                self.videos[vid_id] = TranscriptStatus(
                    url=url,
                    video_id=vid_id,
                    status="pending"
                )
        
        self.stats["total"] = len(self.videos)
        self.update_stats()
    
    def update_stats(self):
        """Update statistics from current video statuses."""
        self.stats = {
            "total": len(self.videos),
            "completed": sum(1 for v in self.videos.values() if v.status == "completed"),
            "failed": sum(1 for v in self.videos.values() if v.status == "failed"),
            "skipped": sum(1 for v in self.videos.values() if v.status == "completed" and v.attempts == 0),
            "pending": sum(1 for v in self.videos.values() if v.status == "pending"),
        }
    
    def transcribe_video(self, status: TranscriptStatus) -> bool:
        """Transcribe a single video using local_transcribe.py."""
        vid_id = status.video_id
        status.status = "processing"
        status.attempts += 1
        status.last_attempt = datetime.now().isoformat()
        self.save_status()
        
        self.logger.info(f"[{self.stats['completed']}/{self.stats['total']}] Processing: {vid_id} (attempt {status.attempts})")
        
        # Build command (use current Python interpreter)
        cmd = [
            sys.executable, "local_transcribe.py",
            "--url", status.url,
            "--model", self.model,
            "--device", self.device,
            "--compute-type", self.compute_type,
            "--output-dir", str(self.output_dir),
        ]
        
        # Add cookie options if specified
        if self.cookies_from_browser:
            cmd.extend(["--cookies-from-browser", self.cookies_from_browser])
        if self.cookies_file:
            cmd.extend(["--cookies-file", self.cookies_file])
        
        start_time = time.time()
        
        try:
            # Run transcription with timeout
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=1800,  # 30 minute timeout
            )
            
            duration = time.time() - start_time
            status.duration_seconds = duration
            
            # Check if successful
            if result.returncode == 0 and self.check_existing_transcript(vid_id):
                status.status = "completed"
                status.output_file = str(self.output_dir / f"{vid_id}.json")
                self.logger.info(f"✅ SUCCESS: {vid_id} ({duration:.1f}s)")
                self.stats["completed"] += 1
                return True
            else:
                # Failed
                error_msg = result.stderr[-500:] if result.stderr else "Unknown error"
                status.error_message = error_msg
                self.logger.error(f"❌ FAILED: {vid_id} - {error_msg[:200]}")
                
                # Check if should retry
                if status.attempts < self.max_retries:
                    status.status = "pending"
                    self.logger.info(f"   Will retry (attempt {status.attempts + 1}/{self.max_retries})")
                else:
                    status.status = "failed"
                    self.stats["failed"] += 1
                
                return False
        
        except subprocess.TimeoutExpired:
            status.error_message = "Timeout after 30 minutes"
            self.logger.error(f"⏱️  TIMEOUT: {vid_id}")
            
            if status.attempts < self.max_retries:
                status.status = "pending"
            else:
                status.status = "failed"
                self.stats["failed"] += 1
            
            return False
        
        except Exception as e:
            status.error_message = str(e)
            self.logger.error(f"💥 EXCEPTION: {vid_id} - {e}")
            
            if status.attempts < self.max_retries:
                status.status = "pending"
            else:
                status.status = "failed"
                self.stats["failed"] += 1
            
            return False
        
        finally:
            self.save_status()
    
    def run(self):
        """Main processing loop with resume capability."""
        self.logger.info("=" * 80)
        self.logger.info("BATCH TRANSCRIPTION STARTING")
        self.logger.info("=" * 80)
        
        # Load URLs
        urls = self.load_input_urls()
        
        # Try to resume from previous run
        resumed = self.load_status()
        if resumed:
            self.logger.info("Resuming from previous run")
        
        # Initialize video tracking
        self.initialize_videos(urls)
        
        self.logger.info(f"Total videos: {self.stats['total']}")
        self.logger.info(f"Completed: {self.stats['completed']}")
        self.logger.info(f"Failed: {self.stats['failed']}")
        self.logger.info(f"Pending: {self.stats['pending']}")
        self.logger.info("")
        
        # Process all pending videos
        try:
            for vid_id, status in self.videos.items():
                if status.status == "pending":
                    self.transcribe_video(status)
                    self.update_stats()
                    
                    # Brief pause between videos
                    time.sleep(1)
        
        except KeyboardInterrupt:
            self.logger.warning("\n⚠️  Interrupted by user - saving progress...")
            self.save_status()
            self.logger.info("Progress saved. Run with --resume to continue.")
            sys.exit(130)
        
        # Final report
        self.logger.info("")
        self.logger.info("=" * 80)
        self.logger.info("BATCH TRANSCRIPTION COMPLETE")
        self.logger.info("=" * 80)
        self.logger.info(f"Total: {self.stats['total']}")
        self.logger.info(f"Completed: {self.stats['completed']}")
        self.logger.info(f"Failed: {self.stats['failed']}")
        self.logger.info(f"Pending: {self.stats['pending']}")
        
        # Generate reports
        self.generate_reports()
    
    def generate_reports(self):
        """Generate summary reports."""
        # Failed videos report
        failed_videos = [v for v in self.videos.values() if v.status == "failed"]
        if failed_videos:
            failed_report = self.log_dir / "failed_videos.txt"
            with open(failed_report, 'w', encoding='utf-8') as f:
                f.write(f"Failed Videos Report - {datetime.now()}\n")
                f.write("=" * 80 + "\n\n")
                for v in failed_videos:
                    f.write(f"URL: {v.url}\n")
                    f.write(f"Video ID: {v.video_id}\n")
                    f.write(f"Attempts: {v.attempts}\n")
                    f.write(f"Error: {v.error_message}\n")
                    f.write("-" * 80 + "\n")
            
            self.logger.info(f"Failed videos report: {failed_report}")
        
        # Update finished.dat with completed videos
        finished_file = Path("finished.dat")
        existing = set()
        if finished_file.exists():
            with open(finished_file, 'r', encoding='utf-8') as f:
                existing = {line.strip() for line in f if line.strip()}
        
        with open(finished_file, 'a', encoding='utf-8') as f:
            for v in self.videos.values():
                if v.status == "completed" and v.url not in existing:
                    f.write(f"{v.url}\n")


def main():
    parser = argparse.ArgumentParser(
        description="Robust batch YouTube transcription with resume capability"
    )
    
    parser.add_argument(
        "--input", "-i",
        type=Path,
        default=Path("inputfile.txt"),
        help="Input file with URLs (default: inputfile.txt)"
    )
    
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("out"),
        help="Output directory for transcripts (default: out/)"
    )
    
    parser.add_argument(
        "--model",
        default="medium",
        help="Whisper model size (default: medium)"
    )
    
    parser.add_argument(
        "--device",
        default="cuda",
        choices=["cpu", "cuda", "auto"],
        help="Device for inference (default: cuda)"
    )
    
    parser.add_argument(
        "--compute-type",
        default="float16",
        choices=["auto", "int8", "int8_float16", "float16", "float32"],
        help="Compute type (default: float16)"
    )
    
    parser.add_argument(
        "--max-retries",
        type=int,
        default=2,
        help="Max retry attempts per video (default: 2)"
    )
    
    parser.add_argument(
        "--log-dir",
        type=Path,
        default=Path("logs"),
        help="Directory for log files (default: logs/)"
    )
    
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume from previous run (uses batch_status.json)"
    )
    
    parser.add_argument(
        "--cookies-from-browser",
        default="firefox",
        help='Load cookies from browser (default: firefox; other options: "chrome", "brave")'
    )
    
    parser.add_argument(
        "--cookies-file",
        default=None,
        help="Path to cookies.txt file in Netscape format"
    )
    
    parser.add_argument(
        "--clear-completed", "-c",
        action="store_true",
        help="Clear completed videos from batch_status.json (keeps failed and pending)"
    )
    
    args = parser.parse_args()
    
    # Handle --clear-completed flag
    if args.clear_completed:
        status_file = Path("batch_status.json")
        if not status_file.exists():
            print("No batch_status.json found. Nothing to clear.")
            sys.exit(0)
        
        with open(status_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        original_count = len(data)
        completed_count = sum(1 for v in data.values() if v['status'] == 'completed')
        
        # Keep only failed and pending videos
        filtered_data = {
            vid_id: status_data 
            for vid_id, status_data in data.items() 
            if status_data['status'] in ['failed', 'pending', 'processing']
        }
        
        with open(status_file, 'w', encoding='utf-8') as f:
            json.dump(filtered_data, f, indent=2)
        
        print(f"Cleared {completed_count} completed videos from batch_status.json")
        print(f"Kept {len(filtered_data)} videos (failed/pending/processing)")
        print(f"Total before: {original_count} → Total after: {len(filtered_data)}")
        sys.exit(0)
    
    # Create transcriber
    transcriber = BatchTranscriber(
        input_file=args.input,
        output_dir=args.output_dir,
        model=args.model,
        device=args.device,
        compute_type=args.compute_type,
        max_retries=args.max_retries,
        log_dir=args.log_dir,
        cookies_from_browser=args.cookies_from_browser,
        cookies_file=args.cookies_file,
    )
    
    # Run
    transcriber.run()


if __name__ == "__main__":
    main()


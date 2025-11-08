"""Reconciliation service for comparing input files with finished transcripts."""

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Set, Tuple

from local_transcribe.utils.files import (
    extract_video_ids_from_files,
    safe_read_lines,
    safe_write_lines,
    validate_transcript_file,
)
from local_transcribe.utils.youtube import extract_video_id, is_valid_youtube_url


@dataclass
class ReconcileReport:
    """Report from reconciliation analysis."""
    # Input file stats
    input_total: int = 0
    input_unique: int = 0
    input_duplicates: Dict[str, List[Tuple[int, str]]] = None
    
    # Finished file stats
    finished_total: int = 0
    finished_unique: int = 0
    finished_duplicates: Dict[str, List[Tuple[int, str]]] = None
    
    # Transcript files
    transcript_count: int = 0
    transcript_ids: Set[str] = None
    
    # Discrepancies
    extra_in_finished: Set[str] = None  # In finished but not in input
    pending: Set[str] = None  # In input but not in finished
    completed: Set[str] = None  # In both input and finished
    finished_but_no_file: Set[str] = None  # In finished but no transcript file
    orphaned_transcripts: Set[str] = None  # Transcript file but not in finished
    actually_completed: Set[str] = None  # In input and has transcript file
    actually_pending: Set[str] = None  # In input but no transcript file
    
    # Invalid URLs
    input_invalid: List[Tuple[int, str, str]] = None  # (line_no, url, reason)
    
    def __post_init__(self):
        """Initialize default values for mutable fields."""
        if self.input_duplicates is None:
            self.input_duplicates = {}
        if self.finished_duplicates is None:
            self.finished_duplicates = {}
        if self.transcript_ids is None:
            self.transcript_ids = set()
        if self.extra_in_finished is None:
            self.extra_in_finished = set()
        if self.pending is None:
            self.pending = set()
        if self.completed is None:
            self.completed = set()
        if self.finished_but_no_file is None:
            self.finished_but_no_file = set()
        if self.orphaned_transcripts is None:
            self.orphaned_transcripts = set()
        if self.actually_completed is None:
            self.actually_completed = set()
        if self.actually_pending is None:
            self.actually_pending = set()
        if self.input_invalid is None:
            self.input_invalid = []


def find_duplicates(urls: List[str], video_ids: List[str]) -> Dict[str, List[Tuple[int, str]]]:
    """
    Find duplicate video IDs in a list.
    
    Args:
        urls: List of URLs
        video_ids: List of corresponding video IDs
        
    Returns:
        Dictionary mapping video_id -> list of (line_number, url) tuples
    """
    seen: Dict[str, Tuple[int, str]] = {}
    duplicates: Dict[str, List[Tuple[int, str]]] = {}
    
    for idx, (url, vid_id) in enumerate(zip(urls, video_ids), 1):
        if vid_id in seen:
            if vid_id not in duplicates:
                duplicates[vid_id] = [seen[vid_id]]
            duplicates[vid_id].append((idx, url))
        else:
            seen[vid_id] = (idx, url)
    
    return duplicates


def find_invalid_urls(urls: List[str]) -> List[Tuple[int, str, str]]:
    """
    Find URLs that don't look like valid YouTube URLs.
    
    Args:
        urls: List of URLs to check
        
    Returns:
        List of (line_number, url, reason) tuples
    """
    invalid = []
    for idx, url in enumerate(urls, 1):
        if not url.startswith("https://"):
            invalid.append((idx, url, "Not HTTPS"))
        elif "youtube.com" not in url and "youtu.be" not in url:
            invalid.append((idx, url, "Not a YouTube URL"))
        elif "results?search" in url:
            invalid.append((idx, url, "Search results page, not a video"))
    
    return invalid


def reconcile(
    input_file: Path,
    finished_file: Path,
    output_dir: Path,
) -> ReconcileReport:
    """
    Reconcile input file with finished transcripts.
    
    Args:
        input_file: Path to input file with URLs
        finished_file: Path to finished.dat file
        output_dir: Directory containing transcript JSON files
        
    Returns:
        ReconcileReport with analysis results
    """
    report = ReconcileReport()
    
    # Load input file
    input_urls = safe_read_lines(input_file)
    input_ids = [extract_video_id(url) for url in input_urls]
    input_unique = set(input_ids)
    
    report.input_total = len(input_urls)
    report.input_unique = len(input_unique)
    report.input_duplicates = find_duplicates(input_urls, input_ids)
    report.input_invalid = find_invalid_urls(input_urls)
    
    # Scan output directory
    transcript_ids = extract_video_ids_from_files(output_dir)
    report.transcript_count = len(transcript_ids)
    report.transcript_ids = transcript_ids
    
    # Load finished file
    finished_urls = safe_read_lines(finished_file) if finished_file.exists() else []
    finished_ids = [extract_video_id(url) for url in finished_urls]
    finished_unique = set(finished_ids)
    
    report.finished_total = len(finished_urls)
    report.finished_unique = len(finished_unique)
    report.finished_duplicates = find_duplicates(finished_urls, finished_ids)
    
    # Calculate discrepancies
    report.extra_in_finished = finished_unique - input_unique
    report.pending = input_unique - finished_unique
    report.completed = input_unique & finished_unique
    report.finished_but_no_file = finished_unique - transcript_ids
    report.orphaned_transcripts = transcript_ids - finished_unique
    report.actually_completed = input_unique & transcript_ids
    report.actually_pending = input_unique - transcript_ids
    
    return report


def write_reconcile_outputs(report: ReconcileReport, input_urls: List[str], input_ids: List[str],
                           finished_urls: List[str], finished_ids: List[str],
                           output_dir: Path) -> Dict[str, Path]:
    """
    Write reconciliation output files.
    
    Args:
        report: ReconcileReport with analysis
        input_urls: Original input URLs list
        input_ids: Corresponding video IDs
        finished_urls: Finished URLs list
        finished_ids: Corresponding finished video IDs
        output_dir: Output directory for transcripts
        
    Returns:
        Dictionary mapping output name -> Path
    """
    outputs = {}
    
    # Generate pending.txt (based on ACTUAL missing transcripts)
    if report.actually_pending:
        pending_path = Path("pending.txt")
        pending_lines = [
            url for url, vid_id in zip(input_urls, input_ids)
            if vid_id in report.actually_pending
        ]
        safe_write_lines(pending_path, pending_lines)
        outputs["pending"] = pending_path
    
    # Generate cleaned inputfile (deduplicated, no invalid)
    cleaned_path = Path("inputfile_clean.txt")
    seen_ids: Set[str] = set()
    clean_lines = []
    invalid_vid_ids = {extract_video_id(inv_url) for _, inv_url, _ in report.input_invalid}
    
    for url, vid_id in zip(input_urls, input_ids):
        if vid_id and vid_id not in invalid_vid_ids and vid_id not in seen_ids:
            clean_lines.append(url)
            seen_ids.add(vid_id)
    
    safe_write_lines(cleaned_path, clean_lines)
    outputs["inputfile_clean"] = cleaned_path
    
    # Generate corrected finished.dat (based on actual transcript files)
    finished_corrected_path = Path("finished_corrected.dat")
    written: Set[str] = set()
    corrected_lines = []
    
    for url, vid_id in zip(input_urls, input_ids):
        if vid_id in report.transcript_ids and vid_id not in written:
            corrected_lines.append(url)
            written.add(vid_id)
    
    safe_write_lines(finished_corrected_path, corrected_lines)
    outputs["finished_corrected"] = finished_corrected_path
    
    # Generate missing transcripts report
    if report.finished_but_no_file:
        missing_path = Path("missing_transcripts.txt")
        missing_lines = []
        for url, vid_id in zip(finished_urls, finished_ids):
            if vid_id in report.finished_but_no_file:
                missing_lines.append(f"{url}  # Video ID: {vid_id}, expected file: {output_dir}/{vid_id}.json")
        safe_write_lines(missing_path, missing_lines)
        outputs["missing_transcripts"] = missing_path
    
    # Generate orphaned transcripts report
    if report.orphaned_transcripts:
        orphaned_path = Path("orphaned_transcripts.txt")
        orphaned_lines = [
            f"{vid_id}  # File exists: {output_dir}/{vid_id}.json, not tracked in finished.dat"
            for vid_id in sorted(report.orphaned_transcripts)
        ]
        safe_write_lines(orphaned_path, orphaned_lines)
        outputs["orphaned_transcripts"] = orphaned_path
    
    return outputs


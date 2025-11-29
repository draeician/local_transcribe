"""Status verification service for reconciling completed URLs with transcript files."""

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Set, Tuple

from local_transcribe.services.status_store import JsonStatusStore, TranscriptStatus
from local_transcribe.utils.files import safe_read_lines, safe_write_lines, validate_transcript_file
from local_transcribe.utils.youtube import extract_video_id


@dataclass
class VerificationResult:
    """Results from verification process."""
    total_checked: int = 0
    missing_transcripts: int = 0
    urls_to_retry: List[str] = None
    video_ids_to_retry: Set[str] = None
    status_updates: int = 0
    finished_cleaned: bool = False
    pending_file_updated: bool = False
    
    def __post_init__(self):
        """Initialize default values for mutable fields."""
        if self.urls_to_retry is None:
            self.urls_to_retry = []
        if self.video_ids_to_retry is None:
            self.video_ids_to_retry = set()


def verify_transcripts_in_finished_dat(
    finished_file: Path,
    output_dir: Path,
) -> Tuple[List[str], Set[str]]:
    """
    Verify transcripts exist for URLs in finished.dat.
    
    Args:
        finished_file: Path to finished.dat file
        output_dir: Directory containing transcript JSON files
        
    Returns:
        Tuple of (list of URLs missing transcripts, set of video IDs missing transcripts)
    """
    if not finished_file.exists():
        return [], set()
    
    finished_urls = safe_read_lines(finished_file)
    missing_urls = []
    missing_video_ids = set()
    
    for url in finished_urls:
        # Skip comments and empty lines
        url = url.strip()
        if not url or url.startswith('#'):
            continue
        
        video_id = extract_video_id(url)
        if not video_id:
            continue
        
        transcript_path = output_dir / f"{video_id}.json"
        if not validate_transcript_file(transcript_path):
            missing_urls.append(url)
            missing_video_ids.add(video_id)
    
    return missing_urls, missing_video_ids


def verify_transcripts_in_status_store(
    status_store: JsonStatusStore,
    output_dir: Path,
) -> Tuple[List[TranscriptStatus], Set[str]]:
    """
    Verify transcripts exist for entries marked as completed in status store.
    
    Args:
        status_store: Status store instance
        output_dir: Directory containing transcript JSON files
        
    Returns:
        Tuple of (list of TranscriptStatus objects missing transcripts, set of video IDs)
    """
    statuses = status_store.load()
    missing_statuses = []
    missing_video_ids = set()
    
    for video_id, status in statuses.items():
        if status.status == "completed":
            transcript_path = output_dir / f"{video_id}.json"
            if not validate_transcript_file(transcript_path):
                missing_statuses.append(status)
                missing_video_ids.add(video_id)
    
    return missing_statuses, missing_video_ids


def update_pending_file(
    pending_file: Path,
    new_urls: List[str],
    existing_video_ids: Set[str] = None,
) -> Tuple[int, Set[str]]:
    """
    Update pending file with new URLs, avoiding duplicates.
    
    Args:
        pending_file: Path to pending file
        new_urls: List of URLs to add
        existing_video_ids: Set of video IDs already in pending file (optional, will read if not provided)
        
    Returns:
        Tuple of (number of new URLs added, set of all video IDs in pending file)
    """
    # Read existing pending file if it exists
    if existing_video_ids is None:
        existing_video_ids = set()
        if pending_file.exists():
            existing_lines = safe_read_lines(pending_file)
            for line in existing_lines:
                line = line.strip()
                if line and not line.startswith('#'):
                    vid_id = extract_video_id(line)
                    if vid_id:
                        existing_video_ids.add(vid_id)
    
    # Collect new URLs (deduplicate by video ID)
    new_urls_to_add = []
    added_video_ids = set()
    
    for url in new_urls:
        url = url.strip()
        if not url or url.startswith('#'):
            continue
        
        video_id = extract_video_id(url)
        if video_id and video_id not in existing_video_ids and video_id not in added_video_ids:
            new_urls_to_add.append(url)
            added_video_ids.add(video_id)
            existing_video_ids.add(video_id)
    
    # Append new URLs to pending file
    if new_urls_to_add:
        # Read existing content
        existing_lines = safe_read_lines(pending_file) if pending_file.exists() else []
        
        # Combine existing and new, ensuring no duplicates
        all_urls = existing_lines.copy()
        existing_vid_ids_in_file = {extract_video_id(line.strip()) for line in existing_lines if line.strip() and not line.strip().startswith('#')}
        
        for url in new_urls_to_add:
            vid_id = extract_video_id(url)
            if vid_id not in existing_vid_ids_in_file:
                all_urls.append(url)
                existing_vid_ids_in_file.add(vid_id)
        
        # Write back
        pending_file.parent.mkdir(parents=True, exist_ok=True)
        safe_write_lines(pending_file, all_urls)
    
    return len(new_urls_to_add), existing_video_ids


def mark_as_pending_in_status_store(
    status_store: JsonStatusStore,
    video_ids: Set[str],
) -> int:
    """
    Mark video IDs as pending in status store.
    
    Args:
        status_store: Status store instance
        video_ids: Set of video IDs to mark as pending
        
    Returns:
        Number of entries updated
    """
    if not video_ids:
        return 0
    
    statuses = status_store.load()
    updated_count = 0
    
    for video_id in video_ids:
        if video_id in statuses:
            status = statuses[video_id]
            if status.status == "completed":
                status.status = "pending"
                updated_count += 1
    
    if updated_count > 0:
        status_store.save(statuses)
    
    return updated_count


def clean_finished_dat(
    finished_file: Path,
    output_dir: Path,
) -> bool:
    """
    Remove entries from finished.dat that don't have transcript files.
    
    Args:
        finished_file: Path to finished.dat file
        output_dir: Directory containing transcript JSON files
        
    Returns:
        True if file was modified, False otherwise
    """
    if not finished_file.exists():
        return False
    
    finished_urls = safe_read_lines(finished_file)
    valid_urls = []
    modified = False
    
    for url in finished_urls:
        url = url.strip()
        # Keep comments and empty lines
        if not url or url.startswith('#'):
            valid_urls.append(url)
            continue
        
        video_id = extract_video_id(url)
        if not video_id:
            valid_urls.append(url)  # Keep invalid URLs for manual review
            continue
        
        transcript_path = output_dir / f"{video_id}.json"
        if validate_transcript_file(transcript_path):
            valid_urls.append(url)
        else:
            modified = True  # This entry is being removed
    
    if modified:
        safe_write_lines(finished_file, valid_urls)
    
    return modified


def verify_finished_dat(
    finished_file: Path,
    output_dir: Path,
    status_store: JsonStatusStore,
    pending_file: Path,
    clean_finished: bool = False,
) -> VerificationResult:
    """
    Quick verification mode: check finished.dat only.
    
    Args:
        finished_file: Path to finished.dat file
        output_dir: Directory containing transcript JSON files
        status_store: Status store instance
        pending_file: Path to pending file
        clean_finished: Whether to clean finished.dat
        
    Returns:
        VerificationResult with verification details
    """
    result = VerificationResult()
    
    # Verify transcripts in finished.dat
    missing_urls, missing_video_ids = verify_transcripts_in_finished_dat(
        finished_file, output_dir
    )
    
    result.total_checked = len(safe_read_lines(finished_file)) if finished_file.exists() else 0
    result.missing_transcripts = len(missing_urls)
    result.urls_to_retry = missing_urls
    result.video_ids_to_retry = missing_video_ids
    
    # Update status store
    if missing_video_ids:
        result.status_updates = mark_as_pending_in_status_store(
            status_store, missing_video_ids
        )
    
    # Update pending file
    if missing_urls:
        added_count, _ = update_pending_file(pending_file, missing_urls)
        result.pending_file_updated = added_count > 0
    
    # Clean finished.dat if requested
    if clean_finished:
        result.finished_cleaned = clean_finished_dat(finished_file, output_dir)
    
    return result


def verify_full(
    finished_file: Path,
    output_dir: Path,
    status_store: JsonStatusStore,
    pending_file: Path,
    clean_finished: bool = True,
) -> VerificationResult:
    """
    Full verification mode: check both batch_status.json and finished.dat.
    
    Args:
        finished_file: Path to finished.dat file
        output_dir: Directory containing transcript JSON files
        status_store: Status store instance
        pending_file: Path to pending file
        clean_finished: Whether to clean finished.dat (default: True)
        
    Returns:
        VerificationResult with verification details
    """
    result = VerificationResult()
    
    # Check status store
    missing_statuses, missing_video_ids_from_status = verify_transcripts_in_status_store(
        status_store, output_dir
    )
    
    # Check finished.dat
    missing_urls_from_finished, missing_video_ids_from_finished = verify_transcripts_in_finished_dat(
        finished_file, output_dir
    )
    
    # Combine results
    all_missing_video_ids = missing_video_ids_from_status | missing_video_ids_from_finished
    
    # Collect all URLs to retry
    all_missing_urls = []
    url_video_ids = set()
    
    # Add URLs from status store
    for status in missing_statuses:
        if status.video_id not in url_video_ids:
            all_missing_urls.append(status.url)
            url_video_ids.add(status.video_id)
    
    # Add URLs from finished.dat (avoid duplicates)
    for url in missing_urls_from_finished:
        vid_id = extract_video_id(url)
        if vid_id and vid_id not in url_video_ids:
            all_missing_urls.append(url)
            url_video_ids.add(vid_id)
    
    # Count totals
    statuses = status_store.load()
    completed_count = sum(1 for s in statuses.values() if s.status == "completed")
    finished_count = len(safe_read_lines(finished_file)) if finished_file.exists() else 0
    
    result.total_checked = completed_count + finished_count
    result.missing_transcripts = len(all_missing_video_ids)
    result.urls_to_retry = all_missing_urls
    result.video_ids_to_retry = all_missing_video_ids
    
    # Update status store
    if missing_video_ids_from_status:
        result.status_updates = mark_as_pending_in_status_store(
            status_store, missing_video_ids_from_status
        )
    
    # Update pending file
    if all_missing_urls:
        added_count, _ = update_pending_file(pending_file, all_missing_urls)
        result.pending_file_updated = added_count > 0
    
    # Clean finished.dat if requested
    if clean_finished:
        result.finished_cleaned = clean_finished_dat(finished_file, output_dir)
    
    return result


"""Status store for tracking batch transcription progress."""

from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Protocol

from local_transcribe.utils.files import safe_read_json, safe_write_json


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


class StatusStore(Protocol):
    """Protocol for status store implementations."""
    
    def load(self) -> Dict[str, TranscriptStatus]:
        """Load all statuses."""
        ...
    
    def save(self, statuses: Dict[str, TranscriptStatus]) -> bool:
        """Save all statuses."""
        ...
    
    def get(self, video_id: str) -> Optional[TranscriptStatus]:
        """Get status for a video ID."""
        ...
    
    def set(self, video_id: str, status: TranscriptStatus) -> None:
        """Set status for a video ID."""
        ...


class JsonStatusStore:
    """JSON-based status store implementation."""
    
    def __init__(self, status_file: Path):
        """
        Initialize JSON status store.
        
        Args:
            status_file: Path to JSON status file
        """
        self.status_file = status_file
        self._cache: Optional[Dict[str, TranscriptStatus]] = None
    
    def load(self) -> Dict[str, TranscriptStatus]:
        """Load all statuses from JSON file."""
        if self._cache is not None:
            return self._cache
        
        if not self.status_file.exists():
            self._cache = {}
            return self._cache
        
        data = safe_read_json(self.status_file)
        self._cache = {}
        
        for vid_id, status_data in data.items():
            try:
                self._cache[vid_id] = TranscriptStatus(**status_data)
            except (TypeError, KeyError):
                # Skip invalid entries
                continue
        
        return self._cache
    
    def save(self, statuses: Dict[str, TranscriptStatus]) -> bool:
        """
        Save all statuses to JSON file.
        
        Args:
            statuses: Dictionary of video_id -> TranscriptStatus
            
        Returns:
            True if save succeeded
        """
        self._cache = statuses
        data = {vid_id: asdict(status) for vid_id, status in statuses.items()}
        return safe_write_json(self.status_file, data)
    
    def get(self, video_id: str) -> Optional[TranscriptStatus]:
        """Get status for a video ID."""
        statuses = self.load()
        return statuses.get(video_id)
    
    def set(self, video_id: str, status: TranscriptStatus) -> None:
        """Set status for a video ID and save."""
        statuses = self.load()
        statuses[video_id] = status
        self.save(statuses)
    
    def clear_cache(self) -> None:
        """Clear the in-memory cache."""
        self._cache = None


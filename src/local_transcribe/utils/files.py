"""Filesystem utility functions."""

import json
from pathlib import Path
from typing import List, Optional, Set, Tuple


def safe_read_json(path: Path) -> dict:
    """
    Safely read a JSON file.
    
    Args:
        path: Path to JSON file
        
    Returns:
        Parsed JSON dict, or empty dict if file doesn't exist or is invalid
    """
    if not path.exists():
        return {}
    
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def safe_write_json(path: Path, data: dict, indent: int = 2) -> bool:
    """
    Safely write a JSON file.
    
    Args:
        path: Path to write to
        data: Dictionary to serialize
        indent: JSON indentation level
        
    Returns:
        True if write succeeded, False otherwise
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=indent)
        return True
    except (OSError, TypeError):
        return False


def safe_read_lines(path: Path) -> List[str]:
    """
    Safely read lines from a text file.
    
    Args:
        path: Path to text file
        
    Returns:
        List of non-empty lines (stripped)
    """
    if not path.exists():
        return []
    
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return [line.strip() for line in f if line.strip()]
    except OSError:
        return []


def safe_write_lines(path: Path, lines: List[str]) -> bool:
    """
    Safely write lines to a text file.
    
    Args:
        path: Path to write to
        lines: List of strings to write
        
    Returns:
        True if write succeeded, False otherwise
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            for line in lines:
                f.write(line + '\n')
        return True
    except OSError:
        return False


def safe_append_line(path: Path, line: str) -> bool:
    """
    Safely append a line to a text file.
    
    Args:
        path: Path to append to
        line: Line to append
        
    Returns:
        True if append succeeded, False otherwise
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, 'a', encoding='utf-8') as f:
            f.write(line + '\n')
        return True
    except OSError:
        return False


def find_json_files(directory: Path, pattern: str = "*.json") -> List[Path]:
    """
    Find all JSON files in a directory.
    
    Args:
        directory: Directory to search
        pattern: Glob pattern (default: "*.json")
        
    Returns:
        List of Path objects
    """
    if not directory.exists():
        return []
    
    return list(directory.glob(pattern))


def extract_video_ids_from_files(directory: Path) -> Set[str]:
    """
    Extract video IDs from JSON transcript filenames.
    
    Args:
        directory: Directory containing transcript JSON files
        
    Returns:
        Set of video ID strings
    """
    json_files = find_json_files(directory)
    return {f.stem for f in json_files}


def validate_transcript_file(json_path: Path) -> bool:
    """
    Validate that a JSON file is a valid transcript.
    
    Args:
        json_path: Path to JSON file
        
    Returns:
        True if file exists and has required structure
    """
    if not json_path.exists():
        return False
    
    try:
        data = safe_read_json(json_path)
        # Check for required fields
        return "transcript" in data and "metadata" in data
    except Exception:
        return False


def find_cookies_file() -> Optional[Path]:
    """
    Find cookies file in common locations.
    
    Checks in order:
    1. ./cookies.txt (current directory)
    2. ~/cookies.txt (home directory)
    
    Returns:
        Path to cookies file if found, None otherwise
    """
    # Check current directory first
    current_dir_cookies = Path("cookies.txt")
    if current_dir_cookies.exists() and current_dir_cookies.is_file():
        return current_dir_cookies
    
    # Check home directory
    home_cookies = Path.home() / "cookies.txt"
    if home_cookies.exists() and home_cookies.is_file():
        return home_cookies
    
    return None


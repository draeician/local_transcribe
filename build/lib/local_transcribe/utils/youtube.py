"""YouTube URL parsing and validation utilities."""

from urllib.parse import urlparse, parse_qs


def extract_video_id(url: str) -> str:
    """
    Extract YouTube video ID from various URL formats.
    
    Handles:
    - youtube.com/watch?v=VIDEO_ID
    - youtube.com/shorts/VIDEO_ID
    - youtube.com/live/VIDEO_ID
    - youtu.be/VIDEO_ID
    
    Args:
        url: YouTube URL string
        
    Returns:
        Video ID string, or original URL if extraction fails
    """
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
    
    # Return as-is if we can't extract
    return url


def is_valid_youtube_url(url: str) -> bool:
    """
    Check if a URL looks like a valid YouTube URL.
    
    Args:
        url: URL string to validate
        
    Returns:
        True if URL appears to be a valid YouTube URL
    """
    url = url.strip()
    
    if not url.startswith("https://"):
        return False
    
    if "youtube.com" not in url and "youtu.be" not in url:
        return False
    
    # Reject search results pages
    if "results?search" in url:
        return False
    
    return True


def pick_channel(meta: dict) -> str:
    """
    Extract channel name from metadata dict.
    
    Args:
        meta: Metadata dictionary from yt-dlp
        
    Returns:
        Channel name string (empty if not found)
    """
    return meta.get("channel") or meta.get("uploader") or ""


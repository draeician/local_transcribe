"""YouTube audio download service with multi-strategy fallback."""

import sys
from pathlib import Path
from typing import Dict, Optional, Tuple

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

from local_transcribe.utils.youtube import pick_channel

class RateLimitError(Exception):
    """Raised when HTTP 429 (Too Many Requests) is detected."""
    pass

class VideoUnavailableError(Exception):
    """Raised when a video is unavailable."""
    pass

class ForbiddenError(Exception):
    """Raised when HTTP 403 (Forbidden) is detected."""
    pass

def make_base_ydl_opts(
    outdir: Path,
    retries: int = 10,
    fragment_retries: int = 10,
    concurrent_frags: int = 4,
    cookies_from_browser: Optional[str] = None,
    cookies_file: Optional[str] = None,
    limit_rate: Optional[str] = None,
    sleep_interval_requests: Optional[float] = None,
) -> Dict:
    """Create base yt-dlp options."""
    opts = {
        "outtmpl": str(outdir / "%(id)s.%(ext)s"),
        "quiet": True,
        "noprogress": True,
        "no_warnings": True,
        "retries": retries,
        "fragment_retries": fragment_retries,
        "concurrent_fragment_downloads": max(1, int(concurrent_frags)),
        "geo_bypass": True,
        "prefer_ipv4": True,
        # Use the standard backend if curl_cffi is unstable in this venv
        "http_backend": "requests", 
        # Prefer local JavaScript runtime (Deno) for YouTube SABR challenge solving
        # yt-dlp will auto-detect Deno from PATH if available
        "esm_preference": "local",
    }
    if cookies_from_browser:
        opts["cookiesfrombrowser"] = (cookies_from_browser, None, None)
    if cookies_file:
        opts["cookiefile"] = str(Path(cookies_file).expanduser())
    if limit_rate:
        opts["limit_rate"] = limit_rate
    if sleep_interval_requests is not None:
        opts["sleep_interval_requests"] = sleep_interval_requests
    return opts

def try_download_with_strategy(
    url: str,
    base_opts: dict,
    fmt: str,
    remux_codec: Optional[str],
    extractor_args: Dict,
    impersonate: Optional[str],
    label: str,
) -> Tuple[Path, dict]:
    """Try downloading with a specific strategy."""
    opts = dict(base_opts)
    opts["format"] = fmt
    
    # Only apply impersonation if explicitly requested and available
    if impersonate:
        opts["impersonate"] = impersonate
        opts["http_backend"] = "curl_cffi"
    
    if remux_codec:
        opts["postprocessors"] = [
            {"key": "FFmpegExtractAudio", "preferredcodec": remux_codec, "preferredquality": "0"}
        ]
    
    if extractor_args:
        opts["extractor_args"] = extractor_args

    # Get client info for logging (handle None case)
    client_info = "default"
    if extractor_args:
        client_info = extractor_args.get('youtube', {}).get('player_client', 'default')
    print(f"[info] Strategy: {label} | client={client_info}", file=sys.stderr)

    try:
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except DownloadError as e:
        # Check if it's a format error - if so, try without format restriction
        error_str = str(e).lower()
        if "format is not available" in error_str or "requested format" in error_str:
            # Retry with bestaudio/best as a last resort
            if fmt != "bestaudio/best":
                print(f"[info] Retrying {label} with bestaudio/best format", file=sys.stderr)
                opts["format"] = "bestaudio/best"
                with YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(url, download=True)
            else:
                raise
        else:
            raise

    vid = info["id"]
    candidates = list(Path(base_opts["outtmpl"]).parent.glob(f"{vid}.*"))

    if remux_codec:
        for c in candidates:
            if c.suffix.lstrip(".") == remux_codec:
                return c, info

    if not candidates:
        raise FileNotFoundError("Downloaded audio file not found.")

    return candidates[0], info

def download_audio_and_metadata(
    url: str,
    outdir: Path,
    cookies_from_browser: Optional[str] = None,
    cookies_file: Optional[str] = None,
    retries: int = 10,
    fragment_retries: int = 10,
    concurrent_frags: int = 4,
    limit_rate: Optional[str] = None,
    sleep_interval_requests: Optional[float] = None,
) -> Tuple[Path, dict]:
    """Download audio and metadata with 2026 stable strategies."""
    outdir.mkdir(parents=True, exist_ok=True)
    base_opts = make_base_ydl_opts(
        outdir, retries, fragment_retries, concurrent_frags, cookies_from_browser, cookies_file,
        limit_rate=limit_rate, sleep_interval_requests=sleep_interval_requests
    )

    # Format selectors - try specific formats first, then fall back to bestaudio/best
    preferred_audio_fmt = "140/251/250/249/bestaudio/best"
    fallback_audio_fmt = "bestaudio/best"
    # Last resort: no format restriction, let yt-dlp choose and extract audio
    unrestricted_fmt = "best"
    
    strategies = [
        # Strategy 1: Default client (auto-detects best client, usually Android/TV which don't require n-challenge)
        (preferred_audio_fmt, "m4a", None, None, "default-client"),
        # Strategy 2: Default client with simpler format selector
        (fallback_audio_fmt, "m4a", None, None, "default-client-simple"),
        # Strategy 3: Default client with no format restriction
        (unrestricted_fmt, "m4a", None, None, "default-client-unrestricted"),
        # Strategy 4: iOS fallback (may work for some restricted videos)
        (preferred_audio_fmt, "m4a", {"youtube": {"player_client": ["ios"]}}, None, "ios-fallback"),
        # Strategy 5: iOS with simpler format selector
        (fallback_audio_fmt, "m4a", {"youtube": {"player_client": ["ios"]}}, None, "ios-fallback-simple"),
        # Strategy 6: Web client (requires n-challenge solving, use as last resort)
        (preferred_audio_fmt, "m4a", {"youtube": {"player_client": ["web"]}}, None, "web-client"),
        # Strategy 7: Web client with simpler format selector
        (fallback_audio_fmt, "m4a", {"youtube": {"player_client": ["web"]}}, None, "web-client-simple"),
    ]

    last_err: Optional[Exception] = None
    format_errors = 0
    for fmt, remux, ea, imp, label in strategies:
        try:
            return try_download_with_strategy(url, base_opts, fmt, remux, ea, imp, label)
        except DownloadError as e:
            last_err = e
            error_str = str(e).lower()
            if "format is not available" in error_str or "requested format" in error_str:
                format_errors += 1
            print(f"[warn] Strategy '{label}' failed: {e}", file=sys.stderr)
        except Exception as e:
            last_err = e
            print(f"[warn] Strategy '{label}' encountered unexpected error: {e}", file=sys.stderr)

    # Provide helpful error message if all strategies failed with format errors
    if format_errors == len(strategies):
        error_msg = (
            f"All download strategies failed. YouTube is not providing any formats for this video.\n"
            f"This may indicate:\n"
            f"  1. The video requires authentication - try using --cookies-file or --cookies-from-browser\n"
            f"  2. Deno is not being used by yt-dlp - verify with 'lt doctor' that Deno is accessible\n"
            f"  3. The video may be region-locked or age-restricted\n"
            f"\nLast error: {last_err}"
        )
        raise RuntimeError(error_msg)
    
    raise RuntimeError(f"All download strategies failed. Last error: {last_err}")

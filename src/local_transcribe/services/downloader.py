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


SAFARI_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/15.6 Safari/605.1.15"
)


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
    """
    Create base yt-dlp options.
    
    Args:
        outdir: Output directory for downloads
        retries: Number of retries for failed downloads
        fragment_retries: Number of retries for failed fragments
        concurrent_frags: Number of concurrent fragment downloads
        cookies_from_browser: Browser to extract cookies from (e.g., "firefox")
        cookies_file: Path to cookies.txt file
        limit_rate: Max download rate (e.g., "200K", "4.2M")
        sleep_interval_requests: Sleep between yt-dlp requests (seconds)
        
    Returns:
        Dictionary of yt-dlp options
    """
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
        "http_headers": {"User-Agent": SAFARI_UA},
    }
    if cookies_from_browser:
        opts["cookiesfrombrowser"] = (cookies_from_browser, None, None)
    if cookies_file:
        opts["cookiefile"] = cookies_file
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
    label: str,
) -> Tuple[Path, dict]:
    """
    Try downloading with a specific strategy.
    
    Args:
        url: YouTube URL
        base_opts: Base yt-dlp options
        fmt: Format selector string
        remux_codec: Codec for remuxing (e.g., "m4a")
        extractor_args: Extractor arguments dict
        label: Strategy label for logging
        
    Returns:
        Tuple of (audio_path, metadata_dict)
        
    Raises:
        DownloadError: If download fails
        FileNotFoundError: If downloaded file not found
    """
    opts = dict(base_opts)
    opts["format"] = fmt
    if remux_codec:
        opts["postprocessors"] = [
            {"key": "FFmpegExtractAudio", "preferredcodec": remux_codec, "preferredquality": "0"}
        ]
    else:
        opts.pop("postprocessors", None)
    if extractor_args:
        opts["extractor_args"] = extractor_args

    print(f"[info] Strategy: {label} | format={fmt} | remux={remux_codec or 'none'}", file=sys.stderr)

    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)

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
    user_format: Optional[str] = None,
    user_remux_codec: Optional[str] = None,
    user_extractor_args: Optional[str] = None,
    limit_rate: Optional[str] = None,
    sleep_interval_requests: Optional[float] = None,
) -> Tuple[Path, dict]:
    """
    Download audio and metadata from YouTube URL with multi-strategy fallback.
    
    Tries multiple download strategies in order:
    1. User-specified format/args (if provided)
    2. web_safari + explicit audio formats
    3. tv + explicit audio formats
    4. android + explicit audio formats
    5. fallback progressive format 18
    
    Args:
        url: YouTube video URL
        outdir: Output directory for audio file
        cookies_from_browser: Browser to extract cookies from
        cookies_file: Path to cookies.txt file
        retries: Number of retries for failed downloads
        fragment_retries: Number of retries for failed fragments
        concurrent_frags: Number of concurrent fragment downloads
        user_format: User-specified format selector
        user_remux_codec: User-specified remux codec
        user_extractor_args: User-specified extractor args (space-separated)
        limit_rate: Max download rate (e.g., "200K", "4.2M")
        sleep_interval_requests: Sleep between yt-dlp requests (seconds)
        
    Returns:
        Tuple of (audio_path, metadata_dict)
        
    Raises:
        RuntimeError: If all download strategies fail
    """
    outdir.mkdir(parents=True, exist_ok=True)
    base_opts = make_base_ydl_opts(
        outdir, retries, fragment_retries, concurrent_frags, cookies_from_browser, cookies_file,
        limit_rate=limit_rate, sleep_interval_requests=sleep_interval_requests
    )

    parsed_user_ea: Dict[str, Dict[str, object]] = {}
    if user_extractor_args:
        for block in user_extractor_args.split():
            if ":" not in block:
                continue
            site, kvs = block.split(":", 1)
            site = site.strip()
            parsed_user_ea.setdefault(site, {})
            for item in kvs.split(";"):
                if "=" in item:
                    k, v = item.split("=", 1)
                    v = v.strip()
                    parsed_user_ea[site][k.strip()] = v.split(",") if "," in v else v

    strategies = []
    if user_format or user_extractor_args:
        strategies.append((
            user_format or "bestaudio/best",
            user_remux_codec or "m4a",
            parsed_user_ea if parsed_user_ea else {"youtube": {"player_client": ["web_safari"]}},
            "user-specified",
        ))

    default_audio_fmt = "140/251/250/249/bestaudio/best"
    strategies.extend([
        (default_audio_fmt, "m4a", {"youtube": {"player_client": ["web_safari"]}}, "web_safari+explicit-audio"),
        (default_audio_fmt, "m4a", {"youtube": {"player_client": ["tv"]}}, "tv+explicit-audio"),
        (default_audio_fmt, "m4a", {"youtube": {"player_client": ["android"]}}, "android+explicit-audio"),
        ("18", "m4a", {"youtube": {"player_client": ["web_safari"]}}, "fallback-progressive-18"),
    ])

    last_err: Optional[Exception] = None
    rate_limit_detected = False
    for fmt, remux, ea, label in strategies:
        try:
            return try_download_with_strategy(url, base_opts, fmt, remux, ea, label)
        except DownloadError as e:
            last_err = e
            error_str = str(e).lower()
            # Check for HTTP 429 (Too Many Requests)
            if "429" in error_str or "too many requests" in error_str:
                rate_limit_detected = True
                print(f"[error] HTTP 429 (Rate Limit) detected: {e}", file=sys.stderr)
                raise RateLimitError(f"HTTP 429 Too Many Requests: {e}")
            print(f"[warn] Strategy '{label}' failed: {e}", file=sys.stderr)
        except RateLimitError:
            # Re-raise rate limit errors immediately
            raise
        except Exception as e:
            last_err = e
            print(f"[warn] Strategy '{label}' failed: {e}", file=sys.stderr)

    raise RuntimeError(f"All download strategies failed. Last error: {last_err}")


"""YouTube audio download service that delegates to yt-dlp CLI."""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Tuple

from local_transcribe.utils.youtube import pick_channel


class RateLimitError(Exception):
    """Raised when HTTP 429 (Too Many Requests) is detected."""
    pass


class VideoUnavailableError(Exception):
    """Raised when a video is unavailable."""
    pass


class ForbiddenError(Exception):
    """Raised when HTTP 403 looks like a temporary throttle."""
    pass


class PrivateVideoError(Exception):
    """Raised when the video is private (terminal)."""
    pass


class AuthenticationRequiredError(Exception):
    """Raised when cookies / auth are required (terminal)."""


class DownloadTimeoutError(Exception):
    """Raised when yt-dlp exceeds the configured timeout."""


class StaleYtDlpError(RuntimeError):
    """Raised when failure looks like outdated yt-dlp / extractor breakage."""


@dataclass
class DownloadErrorInfo:
    category: str
    message: str
    retryable: bool = False


def classify_yt_dlp_failure(stdout: str, stderr: str, returncode: int) -> DownloadErrorInfo:
    """Classify yt-dlp failure into structured categories (task 020)."""
    combined = f"{stdout}\n{stderr}".lower()
    # Prefer stderr for user-facing detail; cap size.
    detail = (stderr or stdout or f"exit {returncode}")[-4000:]

    if "429" in combined or "too many requests" in combined:
        return DownloadErrorInfo("rate_limited", detail, retryable=True)
    if "sign in" in combined or "login required" in combined or "cookies" in combined and "403" in combined:
        return DownloadErrorInfo("authentication_required", detail, retryable=False)
    if "private video" in combined:
        return DownloadErrorInfo("private_video", detail, retryable=False)
    if "http error 403" in combined or "forbidden" in combined:
        return DownloadErrorInfo("temporary_forbidden", detail, retryable=True)
    if any(
        s in combined
        for s in (
            "sabr",
            "n-challenge",
            "signature",
            "format is not available",
            "requested format is not available",
            "only images are available",
        )
    ):
        return DownloadErrorInfo(
            "extractor_failure",
            detail + "\nHint: run `lt update` to refresh yt-dlp.",
            retryable=True,
        )
    if "video unavailable" in combined or " is unavailable" in combined:
        return DownloadErrorInfo("video_unavailable", detail, retryable=False)
    if "timed out" in combined or "timeout" in combined or "network" in combined:
        return DownloadErrorInfo("network_failure", detail, retryable=True)
    if "postprocessing" in combined:
        return DownloadErrorInfo("postprocessing_failure", detail, retryable=True)
    return DownloadErrorInfo("download_failed", detail, retryable=True)


def _find_yt_dlp_binary() -> str:
    """
    Locate the yt-dlp executable in PATH.

    Returns:
        Absolute path to yt-dlp binary.

    Raises:
        RuntimeError: If yt-dlp is not found.
    """
    candidate = shutil.which("yt-dlp") or shutil.which("yt_dlp")
    if not candidate:
        raise RuntimeError(
            "yt-dlp executable not found in PATH. "
            "Install yt-dlp (e.g. with pipx or your package manager) "
            "so local-transcribe can delegate downloads to it."
        )
    return candidate

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
    timeout_seconds: Optional[float] = None,
    ignore_errors: bool = False,
) -> Tuple[Path, dict]:
    """
    Download audio and metadata by delegating to the system yt-dlp CLI.

    ``outdir`` should be a job-local scratch directory (not the final NFS
    transcript root). Uses a new process group for cancellation/timeouts.
    """
    outdir.mkdir(parents=True, exist_ok=True)
    yt_dlp_bin = _find_yt_dlp_binary()

    # Use the same general pattern as the user's CLI script:
    # extract audio and let yt-dlp choose the best stream, defaulting to mp3.
    outtmpl = str(outdir / "%(id)s.%(ext)s")

    cmd = [
        yt_dlp_bin,
        "--restrict-filenames",
        "--no-progress",
        "--no-warnings",
        "--newline",
        "--extract-audio",
        "--audio-format",
        "mp3",
        "-o",
        outtmpl,
        "--print-json",
    ]
    # Broad --ignore-errors is off by default for daemon safety (task 020).
    if ignore_errors:
        cmd.insert(2, "--ignore-errors")

    # Map cookies and throttling options from our config to yt-dlp flags.
    # Important: we only pass cookies when the user explicitly supplied them
    # on the lt CLI, to match typical direct yt-dlp usage.
    if cookies_from_browser:
        cmd.extend(["--cookies-from-browser", cookies_from_browser])
    if cookies_file:
        cmd.extend(["--cookies", str(Path(cookies_file).expanduser())])
    if limit_rate:
        cmd.extend(["--limit-rate", str(limit_rate)])
    if sleep_interval_requests is not None:
        # yt-dlp's --sleep-interval applies between requests; this is the closest match.
        cmd.extend(["--sleep-interval", str(sleep_interval_requests)])

    # Allow advanced users to append extra yt-dlp flags via env var.
    extra_args = (os.environ.get("LT_YTDLP_EXTRA_ARGS") or "").strip()
    if extra_args:
        import shlex
        cmd.extend(shlex.split(extra_args))

    cmd.append(url)

    # Run yt-dlp in its own process group; capture streams with size-conscious handling.
    print(f"[info] Running yt-dlp CLI: {' '.join(cmd)}", file=sys.stderr)
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout_seconds,
            start_new_session=True,
        )
    except subprocess.TimeoutExpired as exc:
        # Kill process group if still running.
        if exc.pid:
            try:
                os.killpg(exc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                pass
        raise DownloadTimeoutError(
            f"yt-dlp timed out after {timeout_seconds}s"
        ) from exc

    stdout = proc.stdout or ""
    stderr = proc.stderr or ""

    if proc.returncode != 0:
        info = classify_yt_dlp_failure(stdout, stderr, proc.returncode)
        # Keep exception messages stderr-focused (avoid flooding with --print-json).
        msg = f"yt-dlp {info.category}: {info.message}"
        if info.category == "rate_limited":
            raise RateLimitError(msg)
        if info.category == "authentication_required":
            raise AuthenticationRequiredError(msg)
        if info.category == "private_video":
            raise PrivateVideoError(msg)
        if info.category == "temporary_forbidden":
            raise ForbiddenError(msg)
        if info.category == "video_unavailable":
            raise VideoUnavailableError(msg)
        if info.category == "extractor_failure":
            raise StaleYtDlpError(msg)
        raise RuntimeError(msg)

    # Find the last JSON line in stdout.
    info: Dict = {}
    for line in stdout.splitlines()[::-1]:
        line = line.strip()
        if not line:
            continue
        try:
            info = json.loads(line)
            break
        except json.JSONDecodeError:
            continue

    if not info:
        raise RuntimeError(
            "yt-dlp did not produce JSON metadata on stdout. "
            "Ensure your yt-dlp is up to date and not overridden by a custom config."
        )

    vid = info.get("id")
    if not vid:
        raise RuntimeError("yt-dlp JSON metadata missing video id.")

    candidates = list(outdir.glob(f"{vid}.*"))
    if not candidates:
        raise FileNotFoundError("Downloaded audio file not found after yt-dlp run.")

    # Prefer typical audio extensions where multiple files exist.
    preferred_exts = ["m4a", "mp3", "opus", "webm"]
    by_ext = {c.suffix.lstrip(".").lower(): c for c in candidates}
    for ext in preferred_exts:
        if ext in by_ext:
            return by_ext[ext], info

    return candidates[0], info

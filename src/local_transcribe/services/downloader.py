"""YouTube audio download service that delegates to yt-dlp CLI."""

import json
import os
import shutil
import subprocess
import sys
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
    """Raised when HTTP 403 (Forbidden) is detected."""
    pass


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
) -> Tuple[Path, dict]:
    """
    Download audio and metadata by delegating to the system yt-dlp CLI.

    This intentionally avoids custom client/strategy tuning and relies on
    the user's yt-dlp installation (version, config, and workarounds).
    """
    outdir.mkdir(parents=True, exist_ok=True)
    yt_dlp_bin = _find_yt_dlp_binary()

    # Use the same general pattern as the user's CLI script:
    # extract audio and let yt-dlp choose the best stream, defaulting to mp3.
    outtmpl = str(outdir / "%(id)s.%(ext)s")

    cmd = [
        yt_dlp_bin,
        "--restrict-filenames",
        "--ignore-errors",
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

    # Run yt-dlp and capture JSON metadata from stdout.
    print(f"[info] Running yt-dlp CLI: {' '.join(cmd)}", file=sys.stderr)
    proc = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    stdout = proc.stdout or ""
    stderr = proc.stderr or ""

    if proc.returncode != 0:
        lower_err = (stdout + "\n" + stderr).lower()
        if "http error 403" in lower_err or "forbidden" in lower_err:
            raise ForbiddenError(f"yt-dlp reported HTTP 403 Forbidden:\n{stderr.strip()}")
        if "429" in lower_err or "too many requests" in lower_err:
            raise RateLimitError(f"yt-dlp reported rate limiting:\n{stderr.strip()}")
        if "unavailable" in lower_err:
            raise VideoUnavailableError(f"yt-dlp reported video unavailable:\n{stderr.strip()}")
        raise RuntimeError(
            f"yt-dlp failed with exit code {proc.returncode}.\n"
            f"stdout:\n{stdout}\n\nstderr:\n{stderr}"
        )

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

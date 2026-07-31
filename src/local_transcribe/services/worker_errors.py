"""Map job exceptions to structured ExecutionError categories (SPEC §7.2)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from local_transcribe.services.downloader import (
    AuthenticationRequiredError,
    DownloadTimeoutError,
    ForbiddenError,
    PrivateVideoError,
    RateLimitError,
    StaleYtDlpError,
    VideoUnavailableError,
)
from local_transcribe.services.queue_models import ExecutionError, utc_now_iso

# Default retry delays (seconds) by category / attempt number.
_RATE_LIMIT_DELAYS = {1: 300.0, 2: 900.0, 3: 3600.0}
_MAX_BACKOFF = 21600.0


def retry_delay_seconds(category: str, attempts: int) -> float:
    """Return backoff seconds before a retryable execution is eligible again."""
    attempts = max(1, attempts)
    if category == "rate_limited":
        if attempts in _RATE_LIMIT_DELAYS:
            return _RATE_LIMIT_DELAYS[attempts]
        return min(_MAX_BACKOFF, 3600.0 * (2 ** (attempts - 3)))
    if category == "temporary_forbidden":
        return min(3600.0, 120.0 * (2 ** (attempts - 1)))
    if category in {
        "network_failure",
        "download_failed",
        "extractor_failure",
        "postprocessing_failure",
        "transcription_failed",
        "worker_interrupted",
    }:
        return min(600.0, 30.0 * (2 ** (attempts - 1)))
    return 60.0


def available_at_iso(*, delay_seconds: float, now: float | None = None) -> str:
    """ISO timestamp ``now + delay`` (UTC)."""
    import time

    base = time.time() if now is None else now
    return datetime.fromtimestamp(base + delay_seconds, tz=timezone.utc).isoformat()


def classify_job_exception(exc: BaseException) -> ExecutionError:
    """Convert a job-boundary exception into a structured ExecutionError."""
    occurred = utc_now_iso()
    if isinstance(exc, RateLimitError):
        return ExecutionError(
            category="rate_limited",
            message=str(exc),
            retryable=True,
            occurred_at=occurred,
        )
    if isinstance(exc, AuthenticationRequiredError):
        return ExecutionError(
            category="authentication_required",
            message=str(exc) + " (configure auth_profile / cookies)",
            retryable=False,
            occurred_at=occurred,
        )
    if isinstance(exc, PrivateVideoError):
        return ExecutionError(
            category="private_video",
            message=str(exc),
            retryable=False,
            occurred_at=occurred,
        )
    if isinstance(exc, VideoUnavailableError):
        return ExecutionError(
            category="video_unavailable",
            message=str(exc),
            retryable=False,
            occurred_at=occurred,
        )
    if isinstance(exc, ForbiddenError):
        return ExecutionError(
            category="temporary_forbidden",
            message=str(exc),
            retryable=True,
            occurred_at=occurred,
        )
    if isinstance(exc, StaleYtDlpError):
        return ExecutionError(
            category="extractor_failure",
            message=str(exc),
            retryable=True,
            occurred_at=occurred,
        )
    if isinstance(exc, DownloadTimeoutError):
        return ExecutionError(
            category="network_failure",
            message=str(exc),
            retryable=True,
            occurred_at=occurred,
        )
    # Transcription / publish failures
    name = type(exc).__name__.lower()
    msg = str(exc)
    if "transcript" in msg.lower() or "whisper" in name:
        return ExecutionError(
            category="transcription_failed",
            message=msg,
            retryable=True,
            occurred_at=occurred,
        )
    if "output" in msg.lower() and "valid" in msg.lower():
        return ExecutionError(
            category="output_validation_failed",
            message=msg,
            retryable=False,
            occurred_at=occurred,
        )
    if isinstance(exc, FileNotFoundError):
        return ExecutionError(
            category="source_not_accessible",
            message=msg,
            retryable=False,
            occurred_at=occurred,
        )
    if isinstance(exc, ValueError) and "source" in msg.lower():
        return ExecutionError(
            category="invalid_source",
            message=msg,
            retryable=False,
            occurred_at=occurred,
        )
    return ExecutionError(
        category="internal_error",
        message=msg,
        retryable=True,
        occurred_at=occurred,
    )


def parse_iso_timestamp(value: str | None) -> Optional[float]:
    """Parse ISO-8601 to epoch seconds; None if missing/unparseable."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        return None


def available_at_elapsed(available_at: str | None, *, now: float | None = None) -> bool:
    """True when ``available_at`` is missing, unparseable, or <= now.

    Unparseable timestamps are treated as ready so stuck retry jobs can promote
    (timestamp-robust promotion).
    """
    import time

    current = time.time() if now is None else now
    ts = parse_iso_timestamp(available_at)
    if ts is None:
        return True
    return ts <= current

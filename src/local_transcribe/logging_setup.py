"""Logging configuration for the transcription system."""

from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

# Long-running commands announce the log path; short CLI ops stay quiet.
_ANNOUNCE_PREFIXES = frozenset(
    {
        "worker",
        "transcribe",
        "batch_transcribe",
        "batch",
    }
)


def default_log_dir() -> Path:
    """Host-local log directory (XDG state, not CWD).

    Default: ``~/.local/state/local-transcribe/logs``
    Override with ``LOCAL_TRANSCRIBE_LOG_DIR``.
    """
    override = os.environ.get("LOCAL_TRANSCRIBE_LOG_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    xdg_state = os.environ.get("XDG_STATE_HOME", "").strip()
    if xdg_state:
        return Path(xdg_state).expanduser() / "local-transcribe" / "logs"
    return Path.home() / ".local" / "state" / "local-transcribe" / "logs"


def configure_logging(
    verbose: bool = False,
    log_dir: Optional[Path] = None,
    log_file_prefix: str = "batch_transcribe",
    *,
    file_logging: bool = True,
    announce: bool | None = None,
) -> logging.Logger:
    """Configure logging for console and a stable rotating file.

    Args:
        verbose: If True, show DEBUG level on console.
        log_dir: Directory for log files (default: :func:`default_log_dir`).
        log_file_prefix: Log basename prefix (``{prefix}.log``).
        file_logging: When False, console only (tests / tiny helpers).
        announce: Print log path on console. Default: only for long-running
            prefixes (worker/transcribe/batch).

    Returns:
        Configured logger instance.
    """
    logger = logging.getLogger("local_transcribe")
    logger.setLevel(logging.DEBUG)
    # Avoid stacking handlers across multiple configure_logging calls in one process.
    logger.handlers.clear()
    logger.propagate = False

    detailed_formatter = logging.Formatter(
        "%(asctime)s - %(levelname)s - %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    simple_formatter = logging.Formatter("%(message)s")

    console_handler = logging.StreamHandler(sys.stderr)
    console_level = logging.DEBUG if verbose else logging.INFO
    console_handler.setLevel(console_level)
    console_handler.setFormatter(simple_formatter)
    logger.addHandler(console_handler)

    log_file: Path | None = None
    if file_logging:
        resolved_dir = Path(log_dir) if log_dir is not None else default_log_dir()
        resolved_dir.mkdir(parents=True, exist_ok=True)
        log_file = resolved_dir / f"{log_file_prefix}.log"
        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(detailed_formatter)
        logger.addHandler(file_handler)

    should_announce = (
        announce
        if announce is not None
        else (log_file_prefix in _ANNOUNCE_PREFIXES)
    )
    if log_file is not None and (should_announce or verbose):
        if verbose:
            logger.debug(
                "Logging configured: console=%s, file=%s",
                console_level,
                log_file,
            )
        else:
            logger.info("Logging to: %s", log_file)

    return logger

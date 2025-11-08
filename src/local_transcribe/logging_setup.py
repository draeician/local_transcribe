"""Logging configuration for the transcription system."""

import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional


def configure_logging(
    verbose: bool = False,
    log_dir: Optional[Path] = None,
    log_file_prefix: str = "batch_transcribe",
) -> logging.Logger:
    """
    Configure logging for console and file output.
    
    Args:
        verbose: If True, show DEBUG level on console
        log_dir: Directory for log files (default: ./logs)
        log_file_prefix: Prefix for log file names
        
    Returns:
        Configured logger instance
    """
    log_dir = log_dir or Path("logs")
    log_dir.mkdir(parents=True, exist_ok=True)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = log_dir / f"{log_file_prefix}_{timestamp}.log"
    
    # Create formatters
    detailed_formatter = logging.Formatter(
        '%(asctime)s - %(levelname)s - %(name)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    simple_formatter = logging.Formatter('%(message)s')
    
    # File handler - detailed
    file_handler = logging.FileHandler(log_file)
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(detailed_formatter)
    
    # Console handler - simplified
    console_handler = logging.StreamHandler(sys.stderr)
    console_level = logging.DEBUG if verbose else logging.INFO
    console_handler.setLevel(console_level)
    console_handler.setFormatter(simple_formatter)
    
    # Setup root logger
    logger = logging.getLogger('local_transcribe')
    logger.setLevel(logging.DEBUG)
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    
    # Prevent duplicate logs from propagating to root
    logger.propagate = False
    
    if verbose:
        logger.debug(f"Logging configured: console={console_level}, file={log_file}")
    else:
        logger.info(f"Logging to: {log_file}")
    
    return logger


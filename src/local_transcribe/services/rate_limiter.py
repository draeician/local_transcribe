"""Rate limit tracking for YouTube API requests."""

import json
import logging
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional


@dataclass
class RateLimitConfig:
    """Configuration for rate limits."""
    max_requests_per_hour: int = 100
    max_requests_per_day: int = 800
    warning_threshold: float = 0.8  # Warn at 80% of limit
    requests_this_hour: int = 0
    requests_today: int = 0
    last_hour_reset: str = ""  # ISO timestamp
    last_day_reset: str = ""  # ISO timestamp
    last_429_error: str = ""  # ISO timestamp of last 429 error
    total_429_errors: int = 0


class RateLimiter:
    """Tracks API request rates and enforces limits."""
    
    def __init__(self, config_path: Path, max_per_hour: int = 100, max_per_day: int = 800):
        """
        Initialize rate limiter.
        
        Args:
            config_path: Path to rate_limits.json file
            max_per_hour: Maximum requests per hour (default: 100)
            max_per_day: Maximum requests per day (default: 800)
        """
        self.config_path = config_path
        self.logger = logging.getLogger('local_transcribe.rate_limiter')
        
        # Load or create config
        if config_path.exists():
            try:
                with open(config_path, 'r') as f:
                    data = json.load(f)
                    self.config = RateLimitConfig(**data)
            except Exception as e:
                self.logger.warning(f"Failed to load rate limit config: {e}. Using defaults.")
                self.config = RateLimitConfig(
                    max_requests_per_hour=max_per_hour,
                    max_requests_per_day=max_per_day
                )
        else:
            self.config = RateLimitConfig(
                max_requests_per_hour=max_per_hour,
                max_requests_per_day=max_per_day
            )
        
        # Override with provided values if different from defaults
        if max_per_hour != 100:
            self.config.max_requests_per_hour = max_per_hour
        if max_per_day != 800:
            self.config.max_requests_per_day = max_per_day
        
        # Reset counters if needed
        self.reset_if_needed()
    
    def reset_if_needed(self) -> None:
        """Reset hourly/daily counters if periods have expired."""
        now = datetime.now()
        reset_hour = False
        reset_day = False
        
        # Check hourly reset
        if self.config.last_hour_reset:
            try:
                last_reset = datetime.fromisoformat(self.config.last_hour_reset)
                if now - last_reset >= timedelta(hours=1):
                    reset_hour = True
            except Exception:
                reset_hour = True
        else:
            reset_hour = True
        
        # Check daily reset
        if self.config.last_day_reset:
            try:
                last_reset = datetime.fromisoformat(self.config.last_day_reset)
                if now - last_reset >= timedelta(days=1):
                    reset_day = True
            except Exception:
                reset_day = True
        else:
            reset_day = True
        
        if reset_hour:
            if self.config.requests_this_hour > 0:
                self.logger.info(f"Hourly reset: processed {self.config.requests_this_hour} requests")
            self.config.requests_this_hour = 0
            self.config.last_hour_reset = now.isoformat()
        
        if reset_day:
            if self.config.requests_today > 0:
                self.logger.info(f"Daily reset: processed {self.config.requests_today} requests")
            self.config.requests_today = 0
            self.config.last_day_reset = now.isoformat()
        
        if reset_hour or reset_day:
            self._save()
    
    def record_request(self) -> None:
        """Record a single API request."""
        self.reset_if_needed()
        self.config.requests_this_hour += 1
        self.config.requests_today += 1
        self._save()
    
    def record_429_error(self) -> None:
        """Record a 429 (Too Many Requests) error."""
        now = datetime.now()
        self.config.last_429_error = now.isoformat()
        self.config.total_429_errors += 1
        self.logger.warning(
            f"HTTP 429 error detected (total: {self.config.total_429_errors}). "
            f"Consider increasing --sleep-interval between videos."
        )
        self._save()
    
    def check_limits(self) -> tuple[bool, Optional[str]]:
        """
        Check if we're approaching or exceeding rate limits.
        
        Returns:
            Tuple of (should_warn, warning_message)
        """
        self.reset_if_needed()
        
        warnings = []
        
        # Check hourly limit
        hour_pct = self.config.requests_this_hour / self.config.max_requests_per_hour
        if hour_pct >= 1.0:
            return True, f"Hourly limit exceeded: {self.config.requests_this_hour}/{self.config.max_requests_per_hour}"
        elif hour_pct >= self.config.warning_threshold:
            warnings.append(
                f"Approaching hourly limit: {self.config.requests_this_hour}/{self.config.max_requests_per_hour} "
                f"({hour_pct*100:.1f}%)"
            )
        
        # Check daily limit
        day_pct = self.config.requests_today / self.config.max_requests_per_day
        if day_pct >= 1.0:
            return True, f"Daily limit exceeded: {self.config.requests_today}/{self.config.max_requests_per_day}"
        elif day_pct >= self.config.warning_threshold:
            warnings.append(
                f"Approaching daily limit: {self.config.requests_today}/{self.config.max_requests_per_day} "
                f"({day_pct*100:.1f}%)"
            )
        
        if warnings:
            return True, " | ".join(warnings)
        
        return False, None
    
    def get_recommended_delay(self) -> float:
        """
        Calculate recommended delay between requests based on current rate.
        
        Returns:
            Recommended delay in seconds
        """
        self.reset_if_needed()
        
        # Base delay: ensure we don't exceed hourly limit
        if self.config.requests_this_hour > 0:
            # Calculate seconds per request to stay under limit
            seconds_per_request = 3600.0 / self.config.max_requests_per_hour
            # Add 10% buffer
            return seconds_per_request * 1.1
        
        # Default: 1 second if no requests yet
        return 1.0
    
    def get_stats(self) -> dict:
        """
        Get current rate limit statistics.
        
        Returns:
            Dictionary with stats
        """
        self.reset_if_needed()
        
        hour_pct = (self.config.requests_this_hour / self.config.max_requests_per_hour) * 100
        day_pct = (self.config.requests_today / self.config.max_requests_per_day) * 100
        
        return {
            "requests_this_hour": self.config.requests_this_hour,
            "requests_today": self.config.requests_today,
            "max_per_hour": self.config.max_requests_per_hour,
            "max_per_day": self.config.max_requests_per_day,
            "hour_percent": hour_pct,
            "day_percent": day_pct,
            "total_429_errors": self.config.total_429_errors,
            "last_429_error": self.config.last_429_error,
        }
    
    def _save(self) -> None:
        """Save configuration to file."""
        try:
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.config_path, 'w') as f:
                json.dump(asdict(self.config), f, indent=2)
        except Exception as e:
            self.logger.warning(f"Failed to save rate limit config: {e}")


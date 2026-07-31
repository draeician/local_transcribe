"""Shared YouTube download admission controller (SPEC §13)."""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from local_transcribe.services.atomic_files import atomic_write_json
from local_transcribe.services.queue_models import utc_now_iso
from local_transcribe.services.worker_lock import WorkerLock


@dataclass
class RateLimitConfig:
    minimum_download_interval_seconds: float = 30.0
    max_download_attempts_per_hour: int = 60
    max_download_attempts_per_day: int = 500
    maximum_backoff_seconds: float = 21600.0


@dataclass
class RateLimitState:
    schema_version: int = 1
    last_download_started_at: Optional[str] = None
    download_attempts_this_hour: int = 0
    download_attempts_today: int = 0
    hour_window_started_at: Optional[str] = None
    day_window_started_at: Optional[str] = None
    blocked_until: Optional[str] = None
    consecutive_throttle_failures: int = 0
    last_429_at: Optional[str] = None
    last_403_at: Optional[str] = None
    total_429_errors: int = 0
    total_403_errors: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RateLimitState":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


def rate_limit_path(queue_dir: Path) -> Path:
    return Path(queue_dir) / "worker" / "rate-limit.json"


def _parse_ts(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        return None


def load_state(queue_dir: Path) -> RateLimitState:
    path = rate_limit_path(queue_dir)
    if not path.is_file():
        return RateLimitState()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return RateLimitState.from_dict(data)
    except (OSError, json.JSONDecodeError, TypeError):
        return RateLimitState()


def save_state(queue_dir: Path, state: RateLimitState) -> None:
    atomic_write_json(rate_limit_path(queue_dir), state.to_dict())


def _require_held_lock(worker_lock: WorkerLock | None) -> WorkerLock:
    """Admission mutations require a live NLM :class:`WorkerLock` object."""
    if worker_lock is None:
        raise RuntimeError(
            "DownloadAdmission requires a held WorkerLock object "
            "(boolean lock_held is not accepted)"
        )
    if getattr(worker_lock, "file", None) is None or worker_lock.file.closed:
        raise RuntimeError("DownloadAdmission WorkerLock is closed or invalid")
    return worker_lock


class DownloadAdmission:
    """Hard admission for YouTube downloads; call only while holding worker lock."""

    def __init__(
        self,
        queue_dir: Path,
        config: RateLimitConfig | None = None,
        *,
        sleep_fn=time.sleep,
        time_fn=time.time,
    ) -> None:
        self.queue_dir = Path(queue_dir)
        self.config = config or RateLimitConfig()
        self._sleep = sleep_fn
        self._time = time_fn

    def _normalize(self, state: RateLimitState, now: float) -> RateLimitState:
        hour_start = _parse_ts(state.hour_window_started_at)
        day_start = _parse_ts(state.day_window_started_at)
        if hour_start is None or now - hour_start >= 3600:
            state.download_attempts_this_hour = 0
            state.hour_window_started_at = datetime.fromtimestamp(
                now, tz=timezone.utc
            ).isoformat()
        if day_start is None or now - day_start >= 86400:
            state.download_attempts_today = 0
            state.day_window_started_at = datetime.fromtimestamp(
                now, tz=timezone.utc
            ).isoformat()
        return state

    def seconds_until_admitted(self, state: RateLimitState | None = None) -> float:
        now = self._time()
        state = self._normalize(state or load_state(self.queue_dir), now)
        blocked = _parse_ts(state.blocked_until)
        wait = 0.0
        if blocked is not None and blocked > now:
            wait = max(wait, blocked - now)
        last = _parse_ts(state.last_download_started_at)
        if last is not None:
            elapsed = now - last
            need = self.config.minimum_download_interval_seconds - elapsed
            if need > 0:
                wait = max(wait, need)
        if state.download_attempts_this_hour >= self.config.max_download_attempts_per_hour:
            hour_start = _parse_ts(state.hour_window_started_at) or now
            wait = max(wait, 3600 - (now - hour_start))
        if state.download_attempts_today >= self.config.max_download_attempts_per_day:
            day_start = _parse_ts(state.day_window_started_at) or now
            wait = max(wait, 86400 - (now - day_start))
        return max(0.0, wait)

    def admit_and_record(self, *, worker_lock: WorkerLock) -> RateLimitState:
        """Sleep until admitted, persist attempt **before** yt-dlp, return state.

        Requires a held :class:`WorkerLock` object (not a boolean flag).
        """
        _require_held_lock(worker_lock)
        while True:
            state = self._normalize(load_state(self.queue_dir), self._time())
            wait = self.seconds_until_admitted(state)
            if wait <= 0:
                break
            self._sleep(min(wait, 1.0) if wait > 1 else wait)
        now = self._time()
        state = self._normalize(load_state(self.queue_dir), now)
        state.last_download_started_at = datetime.fromtimestamp(
            now, tz=timezone.utc
        ).isoformat()
        state.download_attempts_this_hour += 1
        state.download_attempts_today += 1
        if state.hour_window_started_at is None:
            state.hour_window_started_at = state.last_download_started_at
        if state.day_window_started_at is None:
            state.day_window_started_at = state.last_download_started_at
        save_state(self.queue_dir, state)
        return state

    def record_429(
        self,
        *,
        worker_lock: WorkerLock,
        retry_after_seconds: float | None = None,
    ) -> RateLimitState:
        """Record HTTP 429; set global ``blocked_until`` (lock holder only)."""
        _require_held_lock(worker_lock)
        state = load_state(self.queue_dir)
        state.total_429_errors += 1
        state.consecutive_throttle_failures += 1
        state.last_429_at = utc_now_iso()
        n = state.consecutive_throttle_failures
        delays = {1: 300.0, 2: 900.0, 3: 3600.0}
        delay = (
            retry_after_seconds
            if retry_after_seconds is not None
            else delays.get(
                n, min(self.config.maximum_backoff_seconds, 3600.0 * (2 ** (n - 3)))
            )
        )
        delay = min(delay, self.config.maximum_backoff_seconds)
        state.blocked_until = datetime.fromtimestamp(
            self._time() + delay, tz=timezone.utc
        ).isoformat()
        save_state(self.queue_dir, state)
        return state

    def record_403(
        self,
        *,
        worker_lock: WorkerLock,
        temporary: bool = True,
        backoff_seconds: float | None = None,
    ) -> RateLimitState:
        """Record HTTP 403; temporary throttle updates backoff / counters."""
        _require_held_lock(worker_lock)
        state = load_state(self.queue_dir)
        state.total_403_errors += 1
        state.last_403_at = utc_now_iso()
        if temporary:
            state.consecutive_throttle_failures += 1
            n = state.consecutive_throttle_failures
            delay = (
                backoff_seconds
                if backoff_seconds is not None
                else min(3600.0, 120.0 * (2 ** (n - 1)))
            )
            delay = min(delay, self.config.maximum_backoff_seconds)
            state.blocked_until = datetime.fromtimestamp(
                self._time() + delay, tz=timezone.utc
            ).isoformat()
        save_state(self.queue_dir, state)
        return state

    def clear_throttle_streak(self, *, worker_lock: WorkerLock) -> RateLimitState:
        """Reset consecutive throttle counter after a successful download."""
        _require_held_lock(worker_lock)
        state = load_state(self.queue_dir)
        if state.consecutive_throttle_failures:
            state.consecutive_throttle_failures = 0
            save_state(self.queue_dir, state)
        return state

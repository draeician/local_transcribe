"""Worker-scoped Whisper model cache (SPEC §20)."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, Tuple

logger = logging.getLogger(__name__)

CacheKey = Tuple[str, str, str]


@dataclass
class CachedModel:
    key: CacheKey
    model: Any
    loaded_at: float
    last_used: float


class ModelCache:
    """Cache WhisperModel instances by (model, effective_device, effective_compute).

    Callers should pass already-resolved device/compute (after CUDA preflight)
    so the cache key matches the runtime model.
    """

    def __init__(
        self,
        loader: Callable[[str, str, str], Any] | None = None,
        *,
        idle_unload_seconds: float | None = 1800.0,
    ) -> None:
        self._loader = loader
        self._idle_unload_seconds = idle_unload_seconds
        self._entry: CachedModel | None = None

    @property
    def current_key(self) -> CacheKey | None:
        return None if self._entry is None else self._entry.key

    def get(self, model: str, device: str, compute_type: str) -> Any:
        key: CacheKey = (model, device, compute_type)
        now = time.time()
        if self._entry is not None and self._entry.key == key:
            self._entry.last_used = now
            return self._entry.model
        if self._entry is not None:
            logger.info("Model cache miss; reloading %s", key)
            self._entry = None
        if self._loader is None:
            raise RuntimeError("ModelCache has no loader configured")
        instance = self._loader(model, device, compute_type)
        self._entry = CachedModel(key=key, model=instance, loaded_at=now, last_used=now)
        return instance

    def maybe_unload_idle(self) -> bool:
        if self._entry is None or self._idle_unload_seconds is None:
            return False
        if time.time() - self._entry.last_used >= self._idle_unload_seconds:
            logger.info("Unloading idle model %s", self._entry.key)
            self._entry = None
            return True
        return False

    def clear(self) -> None:
        self._entry = None

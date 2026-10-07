"""Worker-scoped Whisper model cache (SPEC §20).

The long-running worker holds one cached Whisper model and releases it after
:data:`DEFAULT_IDLE_UNLOAD_SECONDS` of *actual model inactivity*. The worker
loop performs that eviction while it is sitting idle waiting for work (see
``services.worker.run_worker``), so an unload can never land in the middle of
a transcription.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, Protocol, Tuple, runtime_checkable

logger = logging.getLogger(__name__)

CacheKey = Tuple[str, str, str]

#: Authoritative idle-unload threshold (SPEC §20 ``model_idle_unload_seconds``).
#: Measured from the cached entry's last actual model use, not worker uptime.
DEFAULT_IDLE_UNLOAD_SECONDS: float = 300.0

#: Attribute name the worker looks up for optional idle maintenance.
IDLE_MAINTENANCE_METHOD = "maybe_unload_idle"


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
        idle_unload_seconds: float | None = DEFAULT_IDLE_UNLOAD_SECONDS,
    ) -> None:
        self._loader = loader
        self._idle_unload_seconds = idle_unload_seconds
        self._entry: CachedModel | None = None

    @property
    def current_key(self) -> CacheKey | None:
        return None if self._entry is None else self._entry.key

    @property
    def idle_unload_seconds(self) -> float | None:
        """Configured idle threshold; ``None`` disables idle unloading."""
        return self._idle_unload_seconds

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
        """Drop the cached model when it has been unused past the threshold.

        Idle time is measured from ``last_used`` (refreshed by every
        :meth:`get`, reuse included), so back-to-back jobs inside the window
        keep the same instance. Only the *cache reference* is released; the
        model itself is freed by normal reference counting once no caller
        still holds it. Never call this while a job is transcribing.
        """
        if self._entry is None or self._idle_unload_seconds is None:
            return False
        if time.time() - self._entry.last_used >= self._idle_unload_seconds:
            logger.info(
                "Unloading idle model %s (idle %.0fs >= %.0fs)",
                self._entry.key,
                time.time() - self._entry.last_used,
                self._idle_unload_seconds,
            )
            # Drop the entry (and its model reference) without a local alias:
            # CPython frees the CTranslate2 model as soon as callers let go.
            self._entry = None
            return True
        return False

    def clear(self) -> None:
        self._entry = None


@runtime_checkable
class SupportsIdleMaintenance(Protocol):
    """Narrow optional interface: runners may expose idle model maintenance.

    The worker only needs "release any model you have been holding if it has
    gone idle". It never inspects model-cache internals and never requires a
    concrete runner type, so arbitrary injected ``job_runner`` callables keep
    working (they simply have no eviction to run).
    """

    def maybe_unload_idle(self) -> bool: ...  # pragma: no cover - protocol


def resolve_idle_maintenance(
    candidate: Any,
) -> Callable[[], bool] | None:
    """Return ``candidate``'s idle-maintenance hook, or ``None``.

    Duck-typed on purpose: any callable object exposing a callable
    :data:`IDLE_MAINTENANCE_METHOD` attribute participates; plain functions
    and lambdas return ``None``.
    """
    hook = getattr(candidate, IDLE_MAINTENANCE_METHOD, None)
    return hook if callable(hook) else None

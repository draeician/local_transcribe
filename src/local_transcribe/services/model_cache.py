"""Worker-scoped Whisper model cache (SPEC §20).

The long-running worker holds one cached Whisper model and releases it after
:data:`DEFAULT_IDLE_UNLOAD_SECONDS` of *actual model inactivity*. The worker
loop performs that eviction while it is sitting idle waiting for work (see
``services.worker.run_worker``), so an unload can never land in the middle of
a transcription.

Idle time is measured from the **end of the most recent use** of the model, not
from when it was handed out: a transcription that runs for an hour has used the
model for that hour, so its idle clock starts when the call returns. Callers
that finish using a model report that through :meth:`ModelCache.mark_used`.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, Protocol, Tuple, runtime_checkable

logger = logging.getLogger(__name__)

CacheKey = Tuple[str, str, str]

#: Authoritative idle-unload threshold (SPEC §20 ``model_idle_unload_seconds``).
#: Measured from the *end* of the cached entry's last actual model use (see
#: :meth:`ModelCache.mark_used`), never from worker uptime, load, or acquisition.
DEFAULT_IDLE_UNLOAD_SECONDS: float = 300.0

#: Attribute name the worker looks up for optional idle maintenance.
IDLE_MAINTENANCE_METHOD = "maybe_unload_idle"


@dataclass
class CachedModel:
    key: CacheKey
    model: Any
    loaded_at: float
    #: End of the most recent actual use of the model. ``get()`` seeds it with
    #: acquisition time (start of a use); :meth:`ModelCache.mark_used` moves it
    #: to the moment the use finished, which is what the idle clock measures.
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
        """Acquire (load or reuse) the model for this key.

        This is the *start* of a use, so ``last_used`` is only seeded/kept fresh
        as a floor here. Callers that actually run work against the model must
        report completion with :meth:`mark_used` when the call returns,
        otherwise time spent transcribing would be billed as idle time.
        """
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

    def mark_used(self, model: str, device: str, compute_type: str) -> bool:
        """Record that an actual use of this key's model has just finished.

        The idle clock must restart when the model stops being used, so callers
        invoke this when a call that used the model returns — including when it
        raises (the attempt is over either way; use ``try/finally``).

        Key-aware: only the entry matching ``(model, device, compute_type)`` is
        refreshed. If the entry was replaced (different model/device/compute) or
        already evicted, nothing is touched and ``False`` is returned, so a late
        completion can never keep an unrelated entry alive.
        """
        key: CacheKey = (model, device, compute_type)
        if self._entry is None or self._entry.key != key:
            return False
        self._entry.last_used = time.time()
        return True

    def maybe_unload_idle(self) -> bool:
        """Drop the cached model when it has been unused past the threshold.

        Idle time is measured from ``last_used``, i.e. the end of the most
        recent use reported by :meth:`mark_used` (acquisition via :meth:`get`
        only seeds it), so back-to-back jobs inside the window keep the same
        instance and a long transcription never consumes the idle budget. Only
        the *cache reference* is released; the model itself is freed by normal
        reference counting once no caller still holds it. Never call this while
        a job is transcribing.
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

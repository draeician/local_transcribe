"""Hardware detection and compute-backend resolution.

The transcription stack is ``faster-whisper`` on top of CTranslate2, which does
**not** use PyTorch. This module decides, at runtime on the machine that will
actually execute a job, whether CUDA acceleration is usable and which compute
type to request. It never installs anything and never imports PyTorch.

Two distinct questions are answered, because they are not the same:

* Is there a working NVIDIA GPU / driver? (:func:`detect_nvidia_gpu`) — a
  functional ``nvidia-smi`` probe, not the mere existence of ``nvcc``.
* Can CTranslate2 actually run on CUDA here? (:func:`cuda_runtime_status`) —
  exercises the CTranslate2 CUDA probe so a present GPU with a broken or
  incompatible driver/library stack falls back to CPU instead of crashing.

Producers and workers can live on different hardware, so a queue execution
stores the *requested* device/compute type (typically ``auto``) and the worker
resolves it through :func:`resolve_device_and_compute` when it claims the job.
"""

from __future__ import annotations

import logging
import os
import subprocess
from typing import Callable, Tuple

logger = logging.getLogger(__name__)

#: Sentinel meaning "pick the best backend/compute type for this machine".
AUTO = "auto"

#: Minimum NVIDIA driver for the CUDA 12 runtime shipped with CUDA-12 wheels.
MINIMUM_CUDA12_DRIVER: Tuple[int, int] = (525, 60)

#: Preferred compute types per device, most preferred first. CTranslate2
#: reports the authoritative set at runtime via ``get_supported_compute_types``.
CUDA_COMPUTE_PREFERENCE: Tuple[str, ...] = (
    "float16",
    "int8_float16",
    "int8",
    "float32",
)
CPU_COMPUTE_PREFERENCE: Tuple[str, ...] = (
    "int8",
    "int8_float32",
    "float32",
)

Runner = Callable[..., "subprocess.CompletedProcess[str]"]


def normalize(value: str | None) -> str:
    """Lower-case/strip a user-supplied device or compute type."""
    return (value or "").strip().lower()


def parse_driver_version(version: str) -> Tuple[int, int] | None:
    """Parse ``"535.274.02"`` into ``(535, 274)``; ``None`` when unparseable."""
    parts = (version or "").strip().split(".")
    try:
        return int(parts[0]), int(parts[1]) if len(parts) > 1 else 0
    except (IndexError, ValueError):
        return None


def driver_supports_cuda12(version: str) -> bool:
    """Whether a driver version meets the CUDA 12 minimum."""
    parsed = parse_driver_version(version)
    if parsed is None:
        return False
    return parsed >= MINIMUM_CUDA12_DRIVER


def detect_nvidia_gpu(run: Runner | None = None) -> Tuple[bool, str]:
    """Functional NVIDIA GPU/driver probe via ``nvidia-smi``.

    Returns ``(available, detail)``. A non-zero exit, empty GPU list, missing
    binary, or timeout all count as unavailable. This intentionally ignores
    ``nvcc``: a CUDA toolkit can be installed without a usable driver and vice
    versa.
    """
    runner = run if run is not None else subprocess.run
    try:
        result = runner(
            [
                "nvidia-smi",
                "--query-gpu=name,driver_version",
                "--format=csv,noheader",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except FileNotFoundError:
        return False, "nvidia-smi not found"
    except subprocess.TimeoutExpired:
        return False, "nvidia-smi timed out"
    except Exception as exc:  # noqa: BLE001 — probe must never raise
        return False, f"nvidia-smi probe failed: {exc}"

    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        return False, f"nvidia-smi exited {result.returncode}: {detail[:200]}"
    rows = [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]
    if not rows:
        return False, "nvidia-smi reported no GPUs"
    return True, "; ".join(rows)


def _import_ctranslate2():
    import ctranslate2 as ct2  # local import: optional at import-time

    return ct2


def supported_compute_types(device: str) -> Tuple[str, ...]:
    """Compute types CTranslate2 reports for ``device`` (empty when unknown)."""
    try:
        ct2 = _import_ctranslate2()
    except Exception:  # noqa: BLE001 — optional dependency / broken build
        return ()
    try:
        return tuple(ct2.get_supported_compute_types(device))
    except Exception:  # noqa: BLE001 — probing must never raise
        return ()


def cuda_runtime_status() -> Tuple[bool, str]:
    """Whether CTranslate2 can actually execute on CUDA right now.

    This is the authoritative runtime check: it asks CTranslate2 for visible
    devices *and* the CUDA compute types it can build. A machine with a GPU but
    an insufficient driver, missing cuDNN/cuBLAS, or a CPU-only CTranslate2
    build reports unusable here.
    """
    if normalize(os.environ.get("CT2_USE_CUDA")) == "0":
        return False, "CT2_USE_CUDA=0"

    try:
        ct2 = _import_ctranslate2()
    except Exception as exc:  # noqa: BLE001
        return False, f"ctranslate2 import failed: {exc}"

    get_count = getattr(ct2, "get_cuda_device_count", None)
    if not callable(get_count):
        return False, "ctranslate2 has no CUDA device probe"
    try:
        count = get_count()
    except Exception as exc:  # noqa: BLE001
        return False, f"CUDA device probe failed: {exc}"
    if not count or count < 1:
        return False, "no CUDA devices visible to ctranslate2"

    try:
        types = supported_compute_types("cuda")
    except Exception as exc:  # noqa: BLE001
        return False, f"CUDA compute types unavailable: {exc}"
    if not types:
        return False, "CUDA driver/libraries unusable (no supported compute types)"
    return True, ""


def cuda_usable() -> bool:
    """Boolean form of :func:`cuda_runtime_status`."""
    ok, _reason = cuda_runtime_status()
    return ok


def select_compute_type(device: str, requested: str | None) -> str:
    """Choose a compute type for ``device``.

    An explicit request is returned unchanged so ``--compute-type`` behaves as
    documented. ``auto``/empty picks the best type CTranslate2 supports, falling
    back to a safe default if the runtime cannot be introspected.
    """
    explicit = normalize(requested)
    if explicit and explicit != AUTO:
        return explicit

    supported = supported_compute_types(device)
    preference = (
        CUDA_COMPUTE_PREFERENCE if normalize(device) == "cuda" else CPU_COMPUTE_PREFERENCE
    )
    for candidate in preference:
        if not supported or candidate in supported:
            return candidate
    return "float16" if normalize(device) == "cuda" else "int8"


def resolve_device_and_compute(
    device: str | None, compute_type: str | None
) -> Tuple[str, str]:
    """Resolve requested options into an ``(effective_device, compute_type)``.

    * ``auto``/``cuda``/``gpu`` with usable CUDA → CUDA and the best supported
      CUDA compute type.
    * ``auto``/``cuda``/``gpu`` without usable CUDA → CPU ``int8`` (the explicit
      requested compute type is intentionally overridden: it was written for the
      GPU that turned out to be unusable, and e.g. ``float16`` is not a CPU type).
    * explicit ``cpu`` (or any other device) → that device, with the requested
      compute type preserved, or the best CPU type for ``auto``.

    Called on the worker that executes the job, never on the producer.
    """
    requested = normalize(device) or AUTO

    if requested in (AUTO, "cuda", "gpu"):
        usable, reason = cuda_runtime_status()
        if usable:
            os.environ.pop("CT2_USE_CUDA", None)
            return "cuda", select_compute_type("cuda", compute_type)
        os.environ["CT2_USE_CUDA"] = "0"
        if requested == AUTO:
            logger.info("CUDA not usable (%s); using CPU int8", reason)
        else:
            logger.warning(
                "CUDA requested but not usable (%s); falling back to CPU int8", reason
            )
        return "cpu", "int8"

    if requested == "cpu":
        return "cpu", select_compute_type("cpu", compute_type)

    return requested, (normalize(compute_type) or select_compute_type(requested, compute_type))


def describe_backend(device: str | None, compute_type: str | None) -> str:
    """Human-readable summary used by diagnostics and the CLI."""
    eff_device, eff_compute = resolve_device_and_compute(device, compute_type)
    if eff_device == "cuda":
        return f"CUDA ({eff_compute})"
    return f"CPU ({eff_compute})"

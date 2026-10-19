"""Hardware detection and compute-backend resolution.

The transcription stack is ``faster-whisper`` on top of CTranslate2, which does
**not** use PyTorch. This module decides, at runtime on the machine that will
actually execute a job, which acceleration backend is usable and which compute
type to request. It never installs anything and never imports PyTorch.

Three questions are answered separately, because they are not the same:

* Is there a working GPU / driver? — :func:`detect_nvidia_gpu` (functional
  ``nvidia-smi`` probe, not the mere existence of ``nvcc``) and
  :func:`detect_amd_gpu` (``/dev/kfd`` + ``rocminfo``).
* Can CTranslate2 actually run on the GPU here? (:func:`gpu_runtime_status`) —
  exercises the CTranslate2 device probe so a present GPU with a broken or
  incompatible driver/library stack falls back to CPU instead of crashing.
* *Which* GPU runtime is behind CTranslate2's ``cuda`` device name?
  (:func:`gpu_backend`) — CTranslate2 keeps the ``cuda`` device name for its
  ROCm/HIP build too, so NVIDIA CUDA and AMD ROCm are distinguished by the
  native libraries the process actually loaded, never by a fabricated device
  name or a spoofed architecture (``HSA_OVERRIDE_GFX_VERSION`` is never used).

Producers and workers can live on different hardware, so a queue execution
stores the *requested* device/compute type (typically ``auto``) and the worker
resolves it through :func:`resolve_device_and_compute` when it claims the job.
"""

from __future__ import annotations

import ctypes
import glob
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Callable, Iterable, List, Optional, Tuple

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

#: CTranslate2's internal device name for both its CUDA and ROCm builds.
CT2_GPU_DEVICE = "cuda"

#: Public backend identifiers.
BACKEND_CPU = "cpu"
BACKEND_CUDA = "nvidia-cuda"
BACKEND_ROCM = "amd-rocm"

#: Device names the caller may request that select a GPU backend.
_GPU_DEVICE_ALIASES = frozenset({AUTO, "gpu", "cuda", "rocm", "hip", "amd"})

#: Names of the TheRock ROCm SDK Python packages (venv-installable, no root).
ROCM_SDK_PACKAGE_DIR_GLOBS: Tuple[str, ...] = (
    "_rocm_sdk_core",
    "_rocm_sdk_libraries*",
)

#: Extra system directories that may hold a ROCm runtime.
_SYSTEM_ROCM_LIB_DIRS: Tuple[str, ...] = (
    "/opt/rocm/lib",
    "/opt/rocm/lib/llvm/lib",
)

Runner = Callable[..., "subprocess.CompletedProcess[str]"]

#: Libraries whose presence proves which GPU runtime CTranslate2 is using.
_ROCM_MARKERS = ("libamdhip64.so", "libhipblas.so", "librocblas.so")
_CUDA_MARKERS = ("libcudart.so", "libcublas.so", "libcudnn.so")

#: Library-name prefixes that may be preloaded. Only ROCm/GPU-related libraries
#: are ever ``dlopen``ed — never arbitrary files from a shared system directory
#: (that could load a sanitizer runtime or otherwise corrupt the process).
_PRELOAD_ALLOWED_PREFIXES: Tuple[str, ...] = (
    "libamd",
    "libhip",
    "libroc",
    "libhsa",
    "libMIOpen",
    "libcomgr",
    "libomp",
    "libnuma",
    "libdrm",
    "libelf",
    "libz",
    "libbz2",
    "liblzma",
    "libzstd",
)


def _is_preloadable_rocm_library(path: str) -> bool:
    """Whether ``path`` is a ROCm/GPU-adjacent shared object safe to dlopen.

    Anything inside a TheRock ROCm SDK package directory (``_rocm_sdk_*``) is
    ROCm by construction; elsewhere only known ROCm/system-library prefixes are
    accepted, so a shared system directory can never contribute an unrelated
    library such as a sanitizer runtime.
    """
    name = os.path.basename(path)
    if ".so" not in name:
        return False
    parent = os.path.basename(os.path.dirname(path))
    if parent.startswith("_rocm_sdk"):
        return True
    return name.startswith(_PRELOAD_ALLOWED_PREFIXES)

#: Process-wide guard so each library is dlopen'ed at most once.
_PRELOADED: set[str] = set()


class BackendUnavailableError(RuntimeError):
    """An explicitly requested acceleration backend cannot be used here."""


def normalize(value: str | None) -> str:
    """Lower-case/strip a user-supplied device or compute type."""
    return (value or "").strip().lower()


# --------------------------------------------------------------------------- #
# NVIDIA detection
# --------------------------------------------------------------------------- #


def parse_driver_version(version: str) -> Optional[Tuple[int, int]]:
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


# --------------------------------------------------------------------------- #
# AMD detection + ROCm library discovery / preloading
# --------------------------------------------------------------------------- #


def rocm_sdk_library_dirs() -> List[Path]:
    """Return ROCm library directories discoverable without root.

    TheRock's ROCm SDK ships as ordinary Python wheels (``rocm-sdk-core`` and
    ``rocm-sdk-libraries-gfxNNNN``) whose native libraries live under
    ``_rocm_sdk_core/lib`` and ``_rocm_sdk_libraries_*/lib`` inside
    site-packages. Any system ROCm under ``/opt/rocm`` is included too.
    """
    dirs: List[Path] = []
    seen: set[str] = set()

    search_roots: List[Path] = []
    for entry in sys.path:
        if entry:
            search_roots.append(Path(entry))
    try:  # pragma: no cover - defensive
        import site

        for attr in ("getsitepackages", "getusersitepackages"):
            getter = getattr(site, attr, None)
            if callable(getter):
                value = getter()
                for item in value if isinstance(value, (list, tuple)) else [value]:
                    search_roots.append(Path(item))
    except Exception:  # noqa: BLE001
        pass

    for root in search_roots:
        for pattern in ROCM_SDK_PACKAGE_DIR_GLOBS:
            for pkg_dir in root.glob(pattern):
                lib = pkg_dir / "lib"
                if lib.is_dir():
                    dirs.append(lib)

    for path in _SYSTEM_ROCM_LIB_DIRS:
        candidate = Path(path)
        if candidate.is_dir():
            dirs.append(candidate)

    unique: List[Path] = []
    for d in dirs:
        key = str(d)
        if key not in seen:
            seen.add(key)
            unique.append(d)
    return unique


def rocm_library_present() -> bool:
    """Whether ROCm SDK libraries are discoverable on this machine."""
    return bool(rocm_sdk_library_dirs())


def preload_rocm_libraries(dirs: Optional[Iterable[Path]] = None) -> Tuple[int, List[str]]:
    """``dlopen`` ROCm libraries with ``RTLD_GLOBAL`` (idempotent).

    CTranslate2's ROCm wheel links against ROCm sonames (e.g.
    ``libamdhip64.so.7``, ``libhipblas.so.3``) but carries no RPATH to them, and
    the ROCm SDK wheels place those libraries inside site-packages. Preloading
    them makes the sonames resolvable **without any global library-path
    change** — no ``/etc/ld.so.conf.d`` edit and no system-wide
    ``LD_LIBRARY_PATH``. The worker process gets exactly the libraries it was
    installed with.

    Returns ``(loaded_count, errors)``. Failures are reported, never raised.
    """
    search = list(dirs) if dirs is not None else rocm_sdk_library_dirs()
    loaded = 0
    errors: List[str] = []
    for directory in search:
        pattern = str(Path(directory) / "*.so*")
        for so in sorted(glob.glob(pattern)):
            if not os.path.exists(so):
                continue
            if so in _PRELOADED:
                continue
            if not _is_preloadable_rocm_library(so):
                continue
            try:
                ctypes.CDLL(so, mode=ctypes.RTLD_GLOBAL)
                _PRELOADED.add(so)
                loaded += 1
            except OSError as exc:
                errors.append(f"{Path(so).name}: {exc}")
    return loaded, errors


def detect_amd_gpu(run: Runner | None = None) -> Tuple[bool, str]:
    """Functional AMD GPU/ROCm probe via ``/dev/kfd`` + ``rocminfo``.

    Returns ``(available, detail)`` where ``detail`` includes the GPU
    architecture (e.g. ``gfx1151``) when it can be determined. A missing
    ``/dev/kfd`` is itself decisive: no ROCm GPU compute is possible.
    """
    if not Path("/dev/kfd").exists():
        return False, "/dev/kfd not present (no AMD GPU compute device)"

    runner = run if run is not None else subprocess.run
    arch = ""
    for cmd in (["rocminfo"], ["rocm-smi", "--showproductname"]):
        try:
            result = runner(cmd, capture_output=True, text=True, timeout=15)
        except FileNotFoundError:
            continue
        except subprocess.TimeoutExpired:
            continue
        except Exception:  # noqa: BLE001
            continue
        output = (result.stdout or "") + (result.stderr or "")
        if "gfx" in output:
            for token in output.replace(",", " ").split():
                if token.startswith("gfx") and token[3:].strip():
                    arch = token.split(":")[0]
                    break
        if arch:
            break

    details = ["/dev/kfd present"]
    if arch:
        details.append(f"arch {arch}")
    return True, "; ".join(details)


def amd_gpu_architecture(run: Runner | None = None) -> str:
    """Return the AMD GPU architecture string (``gfxXXXX``) or ``""``."""
    ok, detail = detect_amd_gpu(run=run)
    if not ok:
        return ""
    for part in detail.split(";"):
        part = part.strip()
        if part.startswith("arch "):
            return part[len("arch "):].strip()
    return ""


# --------------------------------------------------------------------------- #
# CTranslate2 runtime probing (backend-agnostic)
# --------------------------------------------------------------------------- #


def _import_ctranslate2():
    import ctranslate2 as ct2  # local import: optional at import-time

    return ct2


def _loaded_gpu_libraries() -> set[str]:
    """Basenames of GPU runtime libraries mapped into the current process."""
    found: set[str] = set()
    try:
        with open("/proc/self/maps", encoding="utf-8") as handle:
            for line in handle:
                marker = ("libamdhip64", "libhipblas", "librocblas", "libcudart", "libcublas")
                if any(m in line for m in marker):
                    found.add(line.rsplit("/", 1)[-1].strip())
    except OSError:  # pragma: no cover - non-Linux / restricted
        pass
    return found


def gpu_backend() -> str:
    """Identify which GPU runtime CTranslate2 is using.

    Returns ``"amd-rocm"``, ``"nvidia-cuda"``, or ``"cpu"``. Because
    CTranslate2 names its GPU device ``cuda`` for both its CUDA and ROCm
    builds, the backend is decided by the native libraries actually loaded into
    the process (after :func:`prepare_gpu_runtime`), never by a spoofed device
    name.
    """
    libs = _loaded_gpu_libraries()
    if any(any(m in lib for m in _ROCM_MARKERS) for lib in libs):
        return BACKEND_ROCM
    if any(any(m in lib for m in _CUDA_MARKERS) for lib in libs):
        return BACKEND_CUDA
    return BACKEND_CPU


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


def prepare_gpu_runtime() -> List[str]:
    """Best-effort, side-effect-local preparation for GPU inference.

    Preloads ROCm SDK libraries when they are present so the CTranslate2 ROCm
    wheel can resolve its sonames. Returns human-readable notes (empty when
    nothing was needed).
    """
    notes: List[str] = []
    if rocm_library_present():
        loaded, errors = preload_rocm_libraries()
        if loaded:
            notes.append(f"preloaded {loaded} ROCm libraries from site-packages")
        if errors:
            logger.debug("ROCm preload skipped %d libraries: %s", len(errors), errors[:3])
    return notes


def gpu_runtime_status() -> Tuple[bool, str]:
    """Whether CTranslate2 can actually execute on a GPU right now.

    This is the authoritative runtime check: it prepares the ROCm runtime,
    then asks CTranslate2 for visible devices *and* the compute types it can
    build. A machine with a GPU but an insufficient driver, missing
    cuDNN/cuBLAS/HIP libraries, or a CPU-only CTranslate2 build reports
    unusable here.
    """
    if normalize(os.environ.get("CT2_USE_CUDA")) == "0":
        return False, "CT2_USE_CUDA=0"

    prepare_gpu_runtime()

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
        return False, f"GPU device probe failed: {exc}"
    if not count or count < 1:
        return False, "no GPU devices visible to ctranslate2"

    types = supported_compute_types(CT2_GPU_DEVICE)
    if not types:
        return False, "GPU driver/libraries unusable (no supported compute types)"
    return True, ""


def cuda_runtime_status() -> Tuple[bool, str]:
    """Backward-compatible alias for :func:`gpu_runtime_status`."""
    return gpu_runtime_status()


def cuda_usable() -> bool:
    """Whether any GPU backend is usable (kept for compatibility)."""
    ok, _reason = gpu_runtime_status()
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
        CUDA_COMPUTE_PREFERENCE if normalize(device) == CT2_GPU_DEVICE else CPU_COMPUTE_PREFERENCE
    )
    for candidate in preference:
        if not supported or candidate in supported:
            return candidate
    return "float16" if normalize(device) == CT2_GPU_DEVICE else "int8"


def resolve_device_and_compute(
    device: str | None, compute_type: str | None
) -> Tuple[str, str]:
    """Resolve requested options into an ``(effective_device, compute_type)``.

    * ``auto``/``gpu`` with a usable GPU → the GPU backend and the best
      supported compute type.
    * ``cuda`` with usable GPU → same; without a usable GPU → CPU ``int8``
      (the requested compute type is intentionally overridden: it was written
      for the GPU that turned out to be unusable).
    * ``rocm`` (explicit AMD request) → the GPU backend when ROCm is usable;
      otherwise raises :class:`BackendUnavailableError` rather than silently
      falling back, so an operator is told the truth.
    * explicit ``cpu`` (or any other device) → that device, with the requested
      compute type preserved, or the best CPU type for ``auto``.

    CTranslate2's effective GPU device string stays ``cuda`` for both its CUDA
    and ROCm builds; use :func:`gpu_backend` to tell them apart.

    Called on the worker that executes the job, never on the producer.
    """
    requested = normalize(device) or AUTO

    if requested == "rocm" or requested == "hip" or requested == "amd":
        usable, reason = gpu_runtime_status()
        if not usable:
            raise BackendUnavailableError(
                "AMD ROCm was requested but CTranslate2 cannot use the GPU here: "
                f"{reason}. Install the ROCm profile "
                "(`./install_local_transcribe.py --rocm`) on a host with an AMD GPU "
                "and a ROCm runtime, or use --device auto / --device cpu."
            )
        if gpu_backend() != BACKEND_ROCM:
            raise BackendUnavailableError(
                "AMD ROCm was requested but the loaded CTranslate2 build is not the "
                "ROCm/HIP build. Reinstall the ROCm wheel with "
                "`./install_local_transcribe.py --rocm`."
            )
        os.environ.pop("CT2_USE_CUDA", None)
        return CT2_GPU_DEVICE, select_compute_type(CT2_GPU_DEVICE, compute_type)

    if requested in (AUTO, "gpu", "cuda"):
        usable, reason = gpu_runtime_status()
        if usable:
            os.environ.pop("CT2_USE_CUDA", None)
            return CT2_GPU_DEVICE, select_compute_type(CT2_GPU_DEVICE, compute_type)
        os.environ["CT2_USE_CUDA"] = "0"
        if requested == AUTO:
            logger.info("GPU not usable (%s); using CPU int8", reason)
        else:
            logger.warning(
                "GPU requested but not usable (%s); falling back to CPU int8", reason
            )
        return "cpu", "int8"

    if requested == "cpu":
        return "cpu", select_compute_type("cpu", compute_type)

    return requested, (normalize(compute_type) or select_compute_type(requested, compute_type))


def describe_backend(device: str | None, compute_type: str | None) -> str:
    """Human-readable summary used by diagnostics and the CLI."""
    eff_device, eff_compute = resolve_device_and_compute(device, compute_type)
    if eff_device == CT2_GPU_DEVICE:
        backend = gpu_backend()
        if backend == BACKEND_ROCM:
            return f"AMD ROCm ({eff_compute})"
        if backend == BACKEND_CUDA:
            return f"NVIDIA CUDA ({eff_compute})"
        return f"GPU ({eff_compute})"
    return f"CPU ({eff_compute})"

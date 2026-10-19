"""Environment diagnostics for local-transcribe.

CPU-only operation is a first-class, healthy configuration. Acceleration is
optional and reported as one of three backends: AMD ROCm, NVIDIA CUDA, or CPU.
Required checks are the packages needed to transcribe at all (CTranslate2,
faster-whisper, ffmpeg, yt-dlp). GPU libraries, a GPU driver, and Deno are
*optional acceleration / YouTube* components: their absence is reported but
never fails diagnostics, and nothing is ever installed here.
"""

from __future__ import annotations

import importlib
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple


@dataclass(frozen=True)
class DiagnosticCheck:
    """One diagnostic result. ``required=False`` never fails the exit code."""

    name: str
    ok: bool
    detail: str
    required: bool = True


def check_required_modules() -> Tuple[bool, str]:
    """Verify the required inference/download dependency set (no torch/nvidia)."""
    required = {
        "ctranslate2": "ctranslate2",
        "faster-whisper": "faster_whisper",
        "yt-dlp": "yt_dlp",
        "soundfile": "soundfile",
    }
    missing: List[str] = []
    for label, module in required.items():
        try:
            importlib.import_module(module)
        except Exception as exc:  # noqa: BLE001 — report, never raise
            missing.append(f"{label} ({exc})")
    if missing:
        return False, f"Missing: {', '.join(missing)}"
    return True, "ctranslate2, faster-whisper, yt-dlp, soundfile present"


def check_cpu_backend() -> Tuple[bool, str]:
    """Confirm CTranslate2 can actually run CPU inference."""
    from local_transcribe.utils.hardware import supported_compute_types

    types = supported_compute_types("cpu")
    if not types:
        return False, "ctranslate2 reports no CPU compute types"
    preferred = "int8" if "int8" in types else sorted(types)[0]
    return True, f"CPU inference healthy (compute_type={preferred}; supported={', '.join(sorted(types))})"


def check_nvidia_smi() -> Tuple[bool, str]:
    """Check whether a working NVIDIA GPU/driver is visible."""
    from local_transcribe.utils.hardware import detect_nvidia_gpu

    return detect_nvidia_gpu()


def check_amd_gpu() -> Tuple[bool, str]:
    """Check whether an AMD GPU/ROCm compute device is visible."""
    from local_transcribe.utils.hardware import detect_amd_gpu

    return detect_amd_gpu()


def check_gpu_runtime() -> Tuple[bool, str]:
    """Check whether CTranslate2 can actually execute on a GPU (CUDA or ROCm)."""
    from local_transcribe.utils import hardware

    ok, reason = hardware.gpu_runtime_status()
    if not ok:
        return False, reason
    backend = hardware.gpu_backend()
    types = hardware.supported_compute_types(hardware.CT2_GPU_DEVICE)
    label = {
        hardware.BACKEND_ROCM: "AMD ROCm",
        hardware.BACKEND_CUDA: "NVIDIA CUDA",
        hardware.BACKEND_CPU: "GPU",
    }.get(backend, backend)
    return True, (
        f"CTranslate2 {label} inference usable "
        f"(backend={backend}; compute types={', '.join(sorted(types)) or 'unknown'})"
    )


def check_cuda_runtime() -> Tuple[bool, str]:
    """Backward-compatible alias for :func:`check_gpu_runtime`."""
    return check_gpu_runtime()


def check_rocm_sdk() -> Tuple[bool, str]:
    """Report whether ROCm SDK libraries are discoverable (no root needed)."""
    from local_transcribe.utils.hardware import rocm_sdk_library_dirs

    dirs = rocm_sdk_library_dirs()
    if not dirs:
        return False, "no ROCm library directories discovered"
    listing = ", ".join(str(d) for d in dirs[:3])
    suffix = "" if len(dirs) <= 3 else f" (+{len(dirs) - 3} more)"
    return True, f"ROCm libraries under: {listing}{suffix}"


def check_ffmpeg() -> Tuple[bool, str]:
    """Check if ffmpeg is available."""
    try:
        result = subprocess.run(
            ["ffmpeg", "-version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            version_line = result.stdout.split("\n")[0]
            return True, version_line.strip()
        return False, "ffmpeg returned error"
    except FileNotFoundError:
        return False, "ffmpeg not found"
    except subprocess.TimeoutExpired:
        return False, "ffmpeg timeout"
    except Exception as e:
        return False, f"Error: {e}"


def check_yt_dlp() -> Tuple[bool, str]:
    """Check if yt-dlp is available (module + downloader binary)."""
    try:
        import yt_dlp
        from local_transcribe.utils.ytdlp_update import (
            find_yt_dlp_binary,
            get_yt_dlp_version,
        )

        binary = find_yt_dlp_binary()
        bin_ver = get_yt_dlp_version() or "?"
        mod_ver = yt_dlp.version.__version__
        return True, f"module={mod_ver}; binary={binary} ({bin_ver})"
    except ImportError:
        return False, "yt-dlp not installed"
    except Exception as e:
        return False, f"Error: {e}"


def check_deno() -> Tuple[bool, str]:
    """Check if Deno is installed and accessible (YouTube downloads only)."""
    try:
        result = subprocess.run(
            ["deno", "--version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            version_line = result.stdout.split("\n")[0]
            return True, version_line.strip()
        return False, "deno returned error"
    except FileNotFoundError:
        common_paths = [
            "/usr/local/bin/deno",
            os.path.expanduser("~/.deno/bin/deno"),
        ]
        for path in common_paths:
            if os.path.exists(path) and os.access(path, os.X_OK):
                try:
                    result = subprocess.run(
                        [path, "--version"],
                        capture_output=True,
                        text=True,
                        timeout=5,
                    )
                    if result.returncode == 0:
                        version_line = result.stdout.split("\n")[0]
                        return True, f"{version_line.strip()} (found at {path})"
                except Exception:
                    continue
        return False, "deno not found (only needed for YouTube downloads)"
    except subprocess.TimeoutExpired:
        return False, "deno timeout"
    except Exception as e:
        return False, f"Error: {e}"


def get_deno_installation_guidance() -> str:
    """Get installation instructions for Deno."""
    return """Deno is required for YouTube 2026 SABR support (not for local audio files).
Install with:
  curl -fsSL https://deno.land/install.sh | sh
  export DENO_INSTALL="$HOME/.deno"
  export PATH="$DENO_INSTALL/bin:$PATH"
  sudo ln -sf "$DENO_INSTALL/bin/deno" /usr/local/bin/deno

For more information, visit: https://deno.com/"""


def check_deno_with_guidance() -> Tuple[bool, str]:
    """Check if Deno is installed, returning guidance if missing."""
    is_available, result = check_deno()
    if is_available:
        return True, result
    return False, get_deno_installation_guidance()


def is_pipx_environment() -> bool:
    """Detect if running in a pipx environment."""
    import sys

    if os.environ.get("PIPX_HOME") or os.environ.get("PIPX_BIN_DIR"):
        return True

    python_path = Path(sys.executable)
    if "pipx" in str(python_path).lower() or ".local/pipx" in str(python_path):
        return True

    try:
        venv_parent = python_path.parent.parent
        if venv_parent.name == "venvs" and "pipx" in str(venv_parent):
            return True
    except Exception:
        pass

    return False


def run_diagnostics() -> List[DiagnosticCheck]:
    """Run all diagnostic checks, required checks first.

    Returns an ordered list; the CLI fails only if a ``required`` check is not ok.
    """
    from local_transcribe.utils import hardware

    ffmpeg_ok, ffmpeg_detail = check_ffmpeg()
    ytdlp_ok, ytdlp_detail = check_yt_dlp()
    modules_ok, modules_detail = check_required_modules()
    cpu_ok, cpu_detail = check_cpu_backend()
    nvidia_ok, nvidia_detail = check_nvidia_smi()
    amd_ok, amd_detail = check_amd_gpu()
    rocm_sdk_ok, rocm_sdk_detail = check_rocm_sdk()
    gpu_ok, gpu_detail = check_gpu_runtime()
    deno_ok, deno_detail = check_deno()

    # Identify the active backend (AMD ROCm / NVIDIA CUDA / CPU).
    if gpu_ok:
        detected = hardware.gpu_backend()
        active = {
            hardware.BACKEND_ROCM: "AMD ROCm",
            hardware.BACKEND_CUDA: "NVIDIA CUDA",
            hardware.BACKEND_CPU: "CPU",
        }.get(detected, detected)
        arch = hardware.amd_gpu_architecture() if detected == hardware.BACKEND_ROCM else ""
        active_detail = f"active: {active}"
        if arch:
            active_detail += f" ({arch})"
        compute = hardware.supported_compute_types(hardware.CT2_GPU_DEVICE)
        if compute:
            active_detail += f"; compute types: {', '.join(sorted(compute))}"
    else:
        active_detail = "active: CPU"

    if not gpu_ok and (nvidia_ok or amd_ok):
        # A GPU is physically present but CTranslate2 cannot use it.
        hint = (
            "install AMD ROCm libraries with install_local_transcribe.py --rocm"
            if amd_ok and not nvidia_ok
            else "install CUDA libraries with install_local_transcribe.py --cuda"
        )
        gpu_detail = (
            f"{gpu_detail}. GPU detected but CTranslate2 cannot use it; "
            f"{hint} or rely on CPU."
        )

    return [
        DiagnosticCheck("Required Python Packages", modules_ok, modules_detail, True),
        DiagnosticCheck("CPU Backend", cpu_ok, cpu_detail, True),
        DiagnosticCheck("FFmpeg", ffmpeg_ok, ffmpeg_detail, True),
        DiagnosticCheck("yt-dlp", ytdlp_ok, ytdlp_detail, True),
        DiagnosticCheck("Acceleration Backend", True, active_detail, False),
        DiagnosticCheck("GPU Detection (nvidia-smi)", nvidia_ok, nvidia_detail, False),
        DiagnosticCheck("AMD GPU (/dev/kfd, rocminfo)", amd_ok, amd_detail, False),
        DiagnosticCheck("ROCm SDK Libraries", rocm_sdk_ok, rocm_sdk_detail, False),
        DiagnosticCheck("ctranslate2 GPU", gpu_ok, gpu_detail, False),
        DiagnosticCheck("Deno Runtime (YouTube)", deno_ok, deno_detail, False),
    ]

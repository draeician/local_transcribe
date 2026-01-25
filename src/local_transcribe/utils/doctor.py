"""Environment diagnostics for CUDA/cuDNN setup."""

import ctypes
import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, Tuple


CUDNN_CANDIDATES = [
    "libcudnn_ops.so.9.1.0", "libcudnn_ops.so.9.1", "libcudnn_ops.so.9",
    "libcudnn.so.9", "libcudnn.so",
]


def check_nvidia_smi() -> Tuple[bool, str]:
    """Check if nvidia-smi is available and working."""
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,driver_version,memory.total", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return True, result.stdout.strip()
        return False, "nvidia-smi returned no output"
    except FileNotFoundError:
        return False, "nvidia-smi not found"
    except subprocess.TimeoutExpired:
        return False, "nvidia-smi timeout"
    except Exception as e:
        return False, f"Error: {e}"


def check_nvcc() -> Tuple[bool, str]:
    """Check if nvcc (CUDA compiler) is available."""
    try:
        result = subprocess.run(
            ["nvcc", "--version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            # Extract version line
            for line in result.stdout.split('\n'):
                if 'release' in line.lower():
                    return True, line.strip()
            return True, "CUDA toolkit found"
        return False, "nvcc returned error"
    except FileNotFoundError:
        return False, "nvcc not found"
    except subprocess.TimeoutExpired:
        return False, "nvcc timeout"
    except Exception as e:
        return False, f"Error: {e}"


def check_python_packages() -> Tuple[bool, str]:
    """Check if required Python packages are installed."""
    packages = ["nvidia-cudnn", "nvidia-cublas", "torch", "ctranslate2", "faster-whisper"]
    found = []
    missing = []
    
    for pkg in packages:
        try:
            if pkg == "nvidia-cudnn":
                import nvidia.cudnn
                found.append("nvidia-cudnn")
            elif pkg == "nvidia-cublas":
                # nvidia-cublas package installs as nvidia.cu13 (for CUDA 13)
                # Try both possible import names
                try:
                    import nvidia.cublas
                    found.append("nvidia-cublas")
                except ImportError:
                    try:
                        import nvidia.cu13
                        found.append("nvidia-cublas")
                    except ImportError:
                        raise ImportError("nvidia-cublas not found")
            elif pkg == "torch":
                import torch
                found.append("torch")
            elif pkg == "ctranslate2":
                import ctranslate2
                found.append("ctranslate2")
            elif pkg == "faster-whisper":
                import faster_whisper
                found.append("faster-whisper")
        except ImportError:
            missing.append(pkg)
    
    if missing:
        return False, f"Missing: {', '.join(missing)}"
    return True, f"Found: {', '.join(found)}"


def check_cudnn_location() -> Tuple[bool, str]:
    """Check cuDNN library location."""
    try:
        import nvidia.cudnn
        import nvidia.cublas
        import os
        
        cudnn_path = os.path.dirname(nvidia.cudnn.__file__) + '/lib'
        cublas_path = os.path.dirname(nvidia.cublas.__file__) + '/lib'
        return True, f"cuDNN: {cudnn_path}, cuBLAS: {cublas_path}"
    except ImportError:
        return False, "nvidia.cudnn or nvidia.cublas not found"
    except Exception as e:
        return False, f"Error: {e}"


def check_ld_library_path() -> Tuple[bool, str]:
    """Check if CUDA paths are in LD_LIBRARY_PATH."""
    ld_path = os.environ.get("LD_LIBRARY_PATH", "")
    cuda_paths = [p for p in ld_path.split(':') if 'cuda' in p.lower() or 'cudnn' in p.lower()]
    
    if cuda_paths:
        return True, f"Found: {len(cuda_paths)} CUDA path(s)"
    return False, "No CUDA paths in LD_LIBRARY_PATH"


def check_ctranslate2_cuda() -> Tuple[bool, str]:
    """Check if ctranslate2 can see CUDA devices."""
    try:
        import ctranslate2 as ct2
        get_cnt = getattr(ct2, "get_cuda_device_count", None)
        if callable(get_cnt):
            count = get_cnt()
            if count > 0:
                return True, f"CUDA device count: {count}"
            return False, "No CUDA devices visible to ctranslate2"
        return False, "get_cuda_device_count not available"
    except Exception as e:
        return False, f"Error: {e}"


def check_pytorch_cuda() -> Tuple[bool, str]:
    """Check if PyTorch can use CUDA."""
    try:
        import torch
        if torch.cuda.is_available():
            device_name = torch.cuda.get_device_name(0)
            cudnn_version = torch.backends.cudnn.version()
            return True, f"Device: {device_name}, cuDNN: {cudnn_version}"
        return False, "CUDA not available in PyTorch"
    except Exception as e:
        return False, f"Error: {e}"


def check_cudnn_so() -> Tuple[bool, str]:
    """Check if cuDNN .so files can be loaded."""
    found = []
    for name in CUDNN_CANDIDATES:
        try:
            ctypes.CDLL(name)
            found.append(name)
            break  # Found one, that's enough
        except OSError:
            continue
    
    if found:
        return True, f"Found: {found[0]}"
    return False, "No cuDNN .so files found"


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
            # Extract version from first line
            version_line = result.stdout.split('\n')[0]
            return True, version_line.strip()
        return False, "ffmpeg returned error"
    except FileNotFoundError:
        return False, "ffmpeg not found"
    except subprocess.TimeoutExpired:
        return False, "ffmpeg timeout"
    except Exception as e:
        return False, f"Error: {e}"


def check_yt_dlp() -> Tuple[bool, str]:
    """Check if yt-dlp is available."""
    try:
        import yt_dlp
        return True, f"Version: {yt_dlp.version.__version__}"
    except ImportError:
        return False, "yt-dlp not installed"
    except Exception as e:
        return False, f"Error: {e}"


def check_deno() -> Tuple[bool, str]:
    """Check if Deno is installed and accessible."""
    try:
        result = subprocess.run(
            ["deno", "--version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            # Extract version from first line (e.g., "deno 2.6.6")
            version_line = result.stdout.split('\n')[0]
            return True, version_line.strip()
        return False, "deno returned error"
    except FileNotFoundError:
        # Check common locations
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
                        version_line = result.stdout.split('\n')[0]
                        return True, f"{version_line.strip()} (found at {path})"
                except Exception:
                    continue
        return False, "deno not found in PATH or common locations"
    except subprocess.TimeoutExpired:
        return False, "deno timeout"
    except Exception as e:
        return False, f"Error: {e}"


def run_diagnostics() -> Dict[str, Tuple[bool, str]]:
    """
    Run all diagnostic checks.
    
    Returns:
        Dictionary mapping check name -> (success, details)
    """
    results = {}
    
    results["Deno Runtime"] = check_deno()
    results["FFmpeg"] = check_ffmpeg()
    results["yt-dlp"] = check_yt_dlp()
    results["GPU Detection (nvidia-smi)"] = check_nvidia_smi()
    results["CUDA Toolkit (nvcc)"] = check_nvcc()
    results["Python Packages"] = check_python_packages()
    results["cuDNN Location"] = check_cudnn_location()
    results["LD_LIBRARY_PATH"] = check_ld_library_path()
    results["cuDNN .so Files"] = check_cudnn_so()
    results["ctranslate2 CUDA"] = check_ctranslate2_cuda()
    results["PyTorch CUDA"] = check_pytorch_cuda()
    
    return results


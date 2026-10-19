"""Local YouTube transcription library.

Importing this package makes the AMD ROCm runtime available *before* any module
imports CTranslate2. ``faster_whisper`` imports ``ctranslate2`` at module load,
and the ROCm CTranslate2 wheel links ROCm sonames (``libhiprand.so.1``,
``libhipblas.so.3``, ``libamdhip64.so.7``) that ship inside the venv's TheRock
ROCm SDK packages. Preloading them here means a plain ``import local_transcribe``
(or the ``lt`` entry point, or the worker) resolves those libraries without any
global ``LD_LIBRARY_PATH`` change.

The preload is best-effort and never raises: on CPU-only and NVIDIA CUDA hosts
there is nothing to preload, and a missing ROCm runtime must not prevent import.
"""

__version__ = "0.7.0"


def _preload_rocm_runtime() -> None:
    """Best-effort ROCm library preload; never raises."""
    try:
        from local_transcribe.utils import hardware

        hardware.prepare_gpu_runtime()
    except Exception:  # noqa: BLE001 — importing the package must always succeed
        pass


_preload_rocm_runtime()

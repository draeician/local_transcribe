"""Transcription service with hardware-aware device selection.

Model construction (:func:`load_whisper_model`) is separate from transcription
(:func:`transcribe_with_model`). Direct CLI paths publish via the generation-aware
publisher — never truncate final transcript JSON in place.

Device/compute selection delegates to :mod:`local_transcribe.utils.hardware`,
which probes CTranslate2 (the actual inference backend — PyTorch is not used) so
a machine without a usable NVIDIA CUDA or AMD ROCm stack simply runs on CPU. The
ROCm runtime is prepared through :func:`hardware.prepare_gpu_runtime`, which
preloads ROCm SDK libraries without any global library-path change.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from faster_whisper import WhisperModel

from local_transcribe.services.transcript_publish import publish_transcript
from local_transcribe.utils import hardware as _hardware
from local_transcribe.utils.hardware import (
    BACKEND_CPU,
    BACKEND_CUDA,
    BACKEND_ROCM,
    BackendUnavailableError,
    prepare_gpu_runtime,
    resolve_device_and_compute as _resolve_device_and_compute,
)
from local_transcribe.utils.youtube import pick_channel

logger = logging.getLogger(__name__)

__all__ = [
    "BackendUnavailableError",
    "BACKEND_CPU",
    "BACKEND_CUDA",
    "BACKEND_ROCM",
]


@dataclass
class TranscribeConfig:
    """Configuration for transcription."""

    model: str = "medium"
    device: str = "auto"
    compute_type: str = "auto"
    output_dir: Path = Path("./out")
    keep_audio: bool = False
    cookies_from_browser: Optional[str] = None
    cookies_file: Optional[str] = None
    language: Optional[str] = None
    beam_size: int = 5
    vad_filter: bool = True
    limit_rate: Optional[str] = None
    sleep_interval_requests: Optional[float] = None


def iso8601_from_ts(ts: Optional[float]) -> str:
    """Convert Unix timestamp to ISO8601 string."""
    if ts is None:
        return ""
    try:
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        return ""


def build_output_json(meta: dict, transcript: str) -> dict:
    """Build output JSON structure from metadata and transcript."""
    vid = meta.get("id", "")
    title = meta.get("title", "")
    channel = pick_channel(meta)
    duration = int(meta.get("duration") or 0)
    ts = meta.get("timestamp") or meta.get("release_timestamp")
    published_iso = iso8601_from_ts(ts)
    return {
        "transcript": transcript,
        "duration": duration,
        "comments": [],
        "metadata": {
            "id": vid,
            "title": title,
            "channel": channel,
            "published_at": published_iso,
        },
    }


_UNSAFE_FOR_FILENAME = re.compile(r'[\\/:*?"<>|\x00-\x1f]+')


def _slug_for_local_id(stem: str) -> str:
    """Stem safe for use as JSON filename id (cross-platform)."""
    s = _UNSAFE_FOR_FILENAME.sub("_", stem.strip())
    s = s.strip("._")
    return s if s else "output"


def local_file_metadata(audio_path: Path) -> dict:
    """Minimal yt-dlp-shaped meta dict for build_output_json."""
    resolved = audio_path.resolve()
    return {
        "id": _slug_for_local_id(resolved.stem),
        "title": resolved.name,
        "duration": 0,
    }


def resolve_device_and_compute(device: str, compute_type: str) -> tuple[str, str]:
    """Apply runtime hardware detection; return effective (device, compute_type).

    ``auto``/``gpu``/``cuda`` on a machine where CTranslate2 cannot use the GPU
    resolves to ``("cpu", "int8")``; an explicit ``rocm`` request raises
    :class:`~local_transcribe.utils.hardware.BackendUnavailableError` instead of
    silently pretending to use the GPU. An explicit ``cpu`` request keeps the
    caller's compute type (or picks a CPU-supported type for ``auto``).

    The ROCm runtime is prepared first so the ROCm CTranslate2 wheel can resolve
    its HIP/cuBLAS sonames from the pipx venv without a global library-path
    change.
    """
    prepare_gpu_runtime()
    effective_device, effective_compute = _resolve_device_and_compute(
        device, compute_type
    )
    if effective_device == "cpu" and (device or "").strip().lower() in (
        "cuda",
        "gpu",
        "rocm",
        "hip",
        "amd",
        "auto",
        "",
    ):
        os.environ["CT2_USE_CUDA"] = "0"
    return effective_device, effective_compute


def effective_backend(device: str, compute_type: str) -> str:
    """Return the backend that will actually run ``device``/``compute_type``."""
    eff_device, _eff_compute = resolve_device_and_compute(device, compute_type)
    if eff_device != "cuda":
        return BACKEND_CPU
    return _hardware.gpu_backend()


def load_whisper_model(
    model_name: str,
    device: str = "cpu",
    compute_type: str = "int8",
) -> tuple[WhisperModel, str, str]:
    """Construct a WhisperModel after device preflight.

    Returns:
        (model, effective_device, effective_compute_type)
    """
    effective_device, effective_compute = resolve_device_and_compute(device, compute_type)
    model = WhisperModel(
        model_name, device=effective_device, compute_type=effective_compute
    )
    return model, effective_device, effective_compute


def default_model_loader(model_name: str, device: str, compute_type: str) -> WhisperModel:
    """Loader for :class:`ModelCache` (returns model only; device already effective)."""
    model, _dev, _ct = load_whisper_model(
        model_name, device=device, compute_type=compute_type
    )
    return model


def transcribe_with_model(
    model: WhisperModel,
    audio_path: Path,
    *,
    language: Optional[str] = None,
    beam_size: int = 5,
    vad_filter: bool = True,
) -> str:
    """Run transcription with an already-loaded WhisperModel."""
    lang_arg = None if (language is None or language.lower() == "auto") else language
    segments, _info = model.transcribe(
        str(audio_path),
        language=lang_arg,
        beam_size=beam_size,
        vad_filter=vad_filter,
    )
    parts = []
    for seg in segments:
        t = (seg.text or "").strip()
        if t:
            parts.append(t)
    return " ".join(parts)


def transcribe_audio(
    audio_path: Path,
    model_name: str = "medium",
    language: Optional[str] = None,
    beam_size: int = 5,
    vad_filter: bool = True,
    device: str = "cpu",
    compute_type: str = "int8",
) -> str:
    """Transcribe with faster-whisper (constructs a one-shot model).

    Prefer :func:`load_whisper_model` + :func:`transcribe_with_model` (or
    :class:`~local_transcribe.services.model_cache.ModelCache`) for reuse.
    """
    model, _dev, _ct = load_whisper_model(
        model_name, device=device, compute_type=compute_type
    )
    return transcribe_with_model(
        model,
        audio_path,
        language=language,
        beam_size=beam_size,
        vad_filter=vad_filter,
    )


def _publish_direct_payload(
    cfg: TranscribeConfig,
    payload: dict,
    *,
    source_key: str,
    generation: int = 1,
) -> Path:
    """Publish direct-mode transcript atomically (generation 1 by default)."""
    result = publish_transcript(
        cfg.output_dir,
        source_key=source_key,
        payload=payload,
        generation=generation,
    )
    return result.path


def transcribe_local_file(audio_path: Path, cfg: TranscribeConfig) -> Path:
    """Transcribe a local audio file and publish transcript JSON atomically."""
    resolved = audio_path.resolve()
    if not resolved.is_file():
        raise ValueError(f"Not a regular file: {resolved}")

    cfg.output_dir.mkdir(parents=True, exist_ok=True)
    meta = local_file_metadata(resolved)
    transcript = transcribe_audio(
        audio_path=resolved,
        model_name=cfg.model,
        language=cfg.language,
        device=cfg.device,
        compute_type=cfg.compute_type,
        beam_size=cfg.beam_size,
        vad_filter=cfg.vad_filter,
    )
    output_obj = build_output_json(meta, transcript)
    vid = output_obj["metadata"]["id"] or "output"
    return _publish_direct_payload(cfg, output_obj, source_key=f"file:{vid}")


def transcribe_url(url: str, cfg: TranscribeConfig, cleanup_callback=None) -> Path:
    """Transcribe a YouTube URL: download, transcribe, publish atomically."""
    from local_transcribe.services.downloader import download_audio_and_metadata

    cfg.output_dir.mkdir(parents=True, exist_ok=True)

    audio_path, meta = download_audio_and_metadata(
        url=url,
        outdir=cfg.output_dir,
        cookies_from_browser=cfg.cookies_from_browser,
        cookies_file=cfg.cookies_file,
        limit_rate=cfg.limit_rate,
        sleep_interval_requests=cfg.sleep_interval_requests,
    )

    if cleanup_callback:
        cleanup_callback(audio_path)

    transcript = transcribe_audio(
        audio_path=audio_path,
        model_name=cfg.model,
        language=cfg.language,
        device=cfg.device,
        compute_type=cfg.compute_type,
        beam_size=cfg.beam_size,
        vad_filter=cfg.vad_filter,
    )

    output_obj = build_output_json(meta, transcript)
    vid = output_obj["metadata"]["id"] or "output"
    json_path = _publish_direct_payload(cfg, output_obj, source_key=f"youtube:{vid}")

    if not cfg.keep_audio:
        try:
            os.remove(audio_path)
        except OSError as exc:
            logger.warning("Failed to remove temp audio %s: %s", audio_path, exc)

    return json_path

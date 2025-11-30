"""Transcription service with CUDA preflight and CPU fallback."""

import os
import sys
import ctypes
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from faster_whisper import WhisperModel

from local_transcribe.utils.youtube import pick_channel


CUDNN_CANDIDATES = [
    # Common cuDNN 9 sonames seen in recent distros
    "libcudnn_ops.so.9.1.0", "libcudnn_ops.so.9.1", "libcudnn_ops.so.9",
    # Fallback generic names (older/newer)
    "libcudnn.so.9", "libcudnn.so",
]


@dataclass
class TranscribeConfig:
    """Configuration for transcription."""
    model: str = "medium"
    device: str = "cpu"
    compute_type: str = "int8"
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
    """
    Build output JSON structure from metadata and transcript.
    
    Args:
        meta: Metadata dictionary from yt-dlp
        transcript: Transcribed text
        
    Returns:
        Dictionary with transcript and metadata
    """
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


def _cuda_preflight() -> tuple[bool, str]:
    """
    Try to decide if CUDA/cuDNN are usable *without crashing the process*.
    
    Returns:
        Tuple of (ok, reason_if_not_ok)
    """
    # Respect explicit opt-out
    if os.environ.get("CT2_USE_CUDA", "").strip() == "0":
        return False, "CT2_USE_CUDA=0"

    # Check ctranslate2's view of GPUs
    try:
        import ctranslate2 as ct2  # type: ignore
        get_cnt = getattr(ct2, "get_cuda_device_count", None)
        if callable(get_cnt):
            if get_cnt() < 1:
                return False, "No CUDA devices visible to ctranslate2"
        else:
            # Older ct2 builds might not expose this; continue checks
            pass
    except Exception as e:
        return False, f"ctranslate2 import failed: {e}"

    # Try to see if cuDNN is present (best-effort)
    try:
        for name in CUDNN_CANDIDATES:
            try:
                ctypes.CDLL(name)
                return True, ""
            except OSError:
                continue
        return False, "cuDNN .so not found"
    except Exception as e:
        return False, f"cuDNN probe error: {e}"


def transcribe_audio(
    audio_path: Path,
    model_name: str = "medium",
    language: Optional[str] = None,
    beam_size: int = 5,
    vad_filter: bool = True,
    device: str = "cpu",
    compute_type: str = "int8",
) -> str:
    """
    Transcribe with faster-whisper.
    
    HARD CPU SAFEGUARD: If CUDA/cuDNN preflight fails, we force CPU by
    setting CT2_USE_CUDA=0 *before* model init to prevent native aborts.
    
    Args:
        audio_path: Path to audio file
        model_name: Whisper model name
        language: Language code (None for auto)
        beam_size: Beam size for decoding
        vad_filter: Enable VAD filtering
        device: Device (cpu/cuda/auto)
        compute_type: Compute type (int8/float16/etc)
        
    Returns:
        Transcribed text string
    """
    lang_arg = None if (language is None or language.lower() == "auto") else language

    # Preflight GPU if requested
    effective_device = device
    effective_compute = compute_type
    if device.lower() in ("cuda", "auto"):
        ok, reason = _cuda_preflight()
        if not ok:
            # Force CPU at environment level (read by ctranslate2)
            os.environ["CT2_USE_CUDA"] = "0"
            print(f"[warn] CUDA not usable ({reason}); forcing CPU int8.", file=sys.stderr)
            effective_device = "cpu"
            effective_compute = "int8"
        else:
            # Ensure GPU is allowed
            os.environ.pop("CT2_USE_CUDA", None)

    # Initialize and run
    model = WhisperModel(model_name, device=effective_device, compute_type=effective_compute)
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


def transcribe_url(url: str, cfg: TranscribeConfig, cleanup_callback=None) -> Path:
    """
    Transcribe a YouTube URL: download audio, transcribe, save JSON.
    
    Args:
        url: YouTube video URL
        cfg: Transcription configuration
        cleanup_callback: Optional callback function(Path) called with audio_path after download
        
    Returns:
        Path to output JSON file
        
    Raises:
        RuntimeError: If download or transcription fails
    """
    from local_transcribe.services.downloader import download_audio_and_metadata
    
    cfg.output_dir.mkdir(parents=True, exist_ok=True)
    
    # Download audio and metadata
    audio_path, meta = download_audio_and_metadata(
        url=url,
        outdir=cfg.output_dir,
        cookies_from_browser=cfg.cookies_from_browser,
        cookies_file=cfg.cookies_file,
        limit_rate=cfg.limit_rate,
        sleep_interval_requests=cfg.sleep_interval_requests,
    )
    
    # Notify callback about audio file for cleanup tracking
    if cleanup_callback:
        cleanup_callback(audio_path)
    
    # Transcribe
    transcript = transcribe_audio(
        audio_path=audio_path,
        model_name=cfg.model,
        language=cfg.language,
        device=cfg.device,
        compute_type=cfg.compute_type,
    )
    
    # Build and save JSON
    output_obj = build_output_json(meta, transcript)
    vid = output_obj["metadata"]["id"] or "output"
    json_path = cfg.output_dir / f"{vid}.json"
    
    import json
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(output_obj, f, ensure_ascii=False, indent=2)
    
    # Clean up audio if requested
    if not cfg.keep_audio:
        try:
            os.remove(audio_path)
        except Exception:
            pass
    
    return json_path


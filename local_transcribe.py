#!/usr/bin/env python3
"""
Robust local YouTube audio → Whisper transcript → JSON.

HARD CPU SAFEGUARD:
- Even if --device cuda is requested, we *preflight* CUDA/cuDNN.
- If anything looks off, we *force CPU* by setting CT2_USE_CUDA=0
  before model init to avoid native aborts.

Usage:
  python local_transcribe.py \
    --url "https://www.youtube.com/watch?v=VIDEO_ID" \
    --model medium \
    --language auto \
    --device cpu \
    --compute-type int8 \
    --output-dir ./out
"""

import argparse
import json
import os
import sys
import ctypes
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple, Dict

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError
from faster_whisper import WhisperModel


SAFARI_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/15.6 Safari/605.1.15"
)

CUDNN_CANDIDATES = [
    # Common cuDNN 9 sonames seen in recent distros
    "libcudnn_ops.so.9.1.0", "libcudnn_ops.so.9.1", "libcudnn_ops.so.9",
    # Fallback generic names (older/newer)
    "libcudnn.so.9", "libcudnn.so",
]


def iso8601_from_ts(ts: Optional[float]) -> str:
    if ts is None:
        return ""
    try:
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        return ""


def pick_channel(meta: dict) -> str:
    return meta.get("channel") or meta.get("uploader") or ""


def build_output_json(meta: dict, transcript: str) -> dict:
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


def make_base_ydl_opts(
    outdir: Path,
    retries: int,
    fragment_retries: int,
    concurrent_frags: int,
    cookies_from_browser: Optional[str],
    cookies_file: Optional[str],
) -> Dict:
    opts = {
        "outtmpl": str(outdir / "%(id)s.%(ext)s"),
        "quiet": True,
        "noprogress": True,
        "no_warnings": True,
        "retries": retries,
        "fragment_retries": fragment_retries,
        "concurrent_fragment_downloads": max(1, int(concurrent_frags)),
        "geo_bypass": True,
        "prefer_ipv4": True,
        "http_headers": {"User-Agent": SAFARI_UA},
    }
    if cookies_from_browser:
        opts["cookiesfrombrowser"] = (cookies_from_browser, None, None)
    if cookies_file:
        opts["cookiefile"] = cookies_file
    return opts


def try_download_with_strategy(
    url: str,
    base_opts: dict,
    fmt: str,
    remux_codec: Optional[str],
    extractor_args: Dict,
    label: str,
) -> Tuple[Path, dict]:
    opts = dict(base_opts)
    opts["format"] = fmt
    if remux_codec:
        opts["postprocessors"] = [
            {"key": "FFmpegExtractAudio", "preferredcodec": remux_codec, "preferredquality": "0"}
        ]
    else:
        opts.pop("postprocessors", None)
    if extractor_args:
        opts["extractor_args"] = extractor_args

    print(f"[info] Strategy: {label} | format={fmt} | remux={remux_codec or 'none'}", file=sys.stderr)

    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)

    vid = info["id"]
    candidates = list(Path(base_opts["outtmpl"]).parent.glob(f"{vid}.*"))

    if remux_codec:
        for c in candidates:
            if c.suffix.lstrip(".") == remux_codec:
                return c, info

    if not candidates:
        raise FileNotFoundError("Downloaded audio file not found.")

    return candidates[0], info


def download_audio_and_metadata(
    url: str,
    outdir: Path,
    cookies_from_browser: Optional[str],
    cookies_file: Optional[str],
    retries: int,
    fragment_retries: int,
    concurrent_frags: int,
    user_format: Optional[str],
    user_remux_codec: Optional[str],
    user_extractor_args: Optional[str],
) -> Tuple[Path, dict]:
    outdir.mkdir(parents=True, exist_ok=True)
    base_opts = make_base_ydl_opts(
        outdir, retries, fragment_retries, concurrent_frags, cookies_from_browser, cookies_file
    )

    parsed_user_ea: Dict[str, Dict[str, object]] = {}
    if user_extractor_args:
        for block in user_extractor_args.split():
            if ":" not in block:
                continue
            site, kvs = block.split(":", 1)
            site = site.strip()
            parsed_user_ea.setdefault(site, {})
            for item in kvs.split(";"):
                if "=" in item:
                    k, v = item.split("=", 1)
                    v = v.strip()
                    parsed_user_ea[site][k.strip()] = v.split(",") if "," in v else v

    strategies = []
    if user_format or user_extractor_args:
        strategies.append((
            user_format or "bestaudio/best",
            user_remux_codec or "m4a",
            parsed_user_ea if parsed_user_ea else {"youtube": {"player_client": ["web_safari"]}},
            "user-specified",
        ))

    default_audio_fmt = "140/251/250/249/bestaudio/best"
    strategies.extend([
        (default_audio_fmt, "m4a", {"youtube": {"player_client": ["web_safari"]}}, "web_safari+explicit-audio"),
        (default_audio_fmt, "m4a", {"youtube": {"player_client": ["tv"]}}, "tv+explicit-audio"),
        (default_audio_fmt, "m4a", {"youtube": {"player_client": ["android"]}}, "android+explicit-audio"),
        ("18", "m4a", {"youtube": {"player_client": ["web_safari"]}}, "fallback-progressive-18"),
    ])

    last_err: Optional[Exception] = None
    for fmt, remux, ea, label in strategies:
        try:
            return try_download_with_strategy(url, base_opts, fmt, remux, ea, label)
        except DownloadError as e:
            last_err = e
            print(f"[warn] Strategy '{label}' failed: {e}", file=sys.stderr)
        except Exception as e:
            last_err = e
            print(f"[warn] Strategy '{label}' failed: {e}", file=sys.stderr)

    raise RuntimeError(f"All download strategies failed. Last error: {last_err}")


def _cuda_preflight() -> tuple[bool, str]:
    """
    Try to decide if CUDA/cuDNN are usable *without crashing the process*.
    Returns (ok, reason_if_not_ok).
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


def main():
    p = argparse.ArgumentParser(description="YouTube → local audio → Whisper → JSON (API-free, CPU-safe)")
    p.add_argument("--url", required=True, help="YouTube video URL (or ID).")
    p.add_argument("--model", default="medium", help="Whisper model: tiny|base|small|medium|large-v3")
    p.add_argument("--language", default="auto", help='Language code (e.g., "en") or "auto"')
    p.add_argument("--output-dir", default="./out", help="Output directory (audio + JSON)")
    p.add_argument("--keep-audio", action="store_true", help="Keep the downloaded audio file")

    # Whisper device/precision
    p.add_argument("--device", default="cpu", choices=["cpu", "cuda", "auto"], help="Inference device (default: cpu)")
    p.add_argument("--compute-type", default="int8",
                   choices=["auto", "int8", "int8_float16", "float16", "float32"],
                   help="CT2 compute type (default: int8)")

    # Advanced/network/auth
    p.add_argument("--cookies-from-browser", default=None, help='e.g., "firefox", "chrome", "brave"')
    p.add_argument("--cookies-file", default=None, help="Path to cookies.txt (Netscape format)")
    p.add_argument("--retries", type=int, default=10)
    p.add_argument("--fragment-retries", type=int, default=10)
    p.add_argument("--concurrent-frags", type=int, default=8)

    # Optional overrides (yt-dlp)
    p.add_argument("--format", dest="user_format", default=None, help='yt-dlp format selector, e.g. "140"')
    p.add_argument("--remux-codec", dest="user_remux_codec", default=None, help='Override remux codec (e.g., "m4a")')
    p.add_argument("--extractor-args", dest="user_extractor_args", default=None,
                   help='Extractor args like \'youtube:player_client=android\' (space-separate for multiple sites)')

    args = p.parse_args()

    outdir = Path(args.output_dir).resolve()
    outdir.mkdir(parents=True, exist_ok=True)

    try:
        audio_path, meta = download_audio_and_metadata(
            url=args.url,
            outdir=outdir,
            cookies_from_browser=args.cookies_from_browser,
            cookies_file=args.cookies_file,
            retries=args.retries,
            fragment_retries=args.fragment_retries,
            concurrent_frags=args.concurrent_frags,
            user_format=args.user_format,
            user_remux_codec=args.user_remux_codec,
            user_extractor_args=args.user_extractor_args,
        )
    except Exception as e:
        print(f("[error] Failed to download audio/metadata: {e}"), file=sys.stderr)
        sys.exit(2)

    try:
        transcript = transcribe_audio(
            audio_path=audio_path,
            model_name=args.model,
            language=args.language,
            device=args.device,
            compute_type=args.compute_type,
        )
    except Exception as e:
        print(f("[error] Failed to transcribe audio: {e}"), file=sys.stderr)
        sys.exit(3)

    output_obj = build_output_json(meta, transcript)
    vid = output_obj["metadata"]["id"] or "output"
    json_path = outdir / f"{vid}.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(output_obj, f, ensure_ascii=False, indent=2)

    if not args.keep_audio:
        try:
            os.remove(audio_path)
        except Exception:
            pass

    print(f"Done. Wrote: {json_path}")


if __name__ == "__main__":
    main()


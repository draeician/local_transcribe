# BUILDPLAN.md - Original Design Document

> **Note**: This document represents the **original design** from October 2024. The system has since evolved significantly beyond this initial plan. See [IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md) for current features, or [QUICK_REFERENCE.md](QUICK_REFERENCE.md) to start using the system today.
>
> **Current status**: ✅ Core plan implemented and **significantly enhanced** with production features:
> - Resume capability for batch processing
> - File verification and reconciliation
> - Detailed logging and status tracking
> - Retry logic and failure handling
> - See [BATCH_TRANSCRIBE_README.md](BATCH_TRANSCRIBE_README.md) for the full feature set

---

## Original Requirements (October 2024)

**PROMPT:**
On a debian based linux system, I need a way to download the audio from a youtube video, and extract the text transcription from it.  The youtube api tends to throttle transcript downloads quickly, this will offload the work to my systems.  Create a plan on how to do this using python  and only free/open source solutions.  The output of this process should look like a normal transcript output in the json format below.

[Attached file: YOUTUBE_VIDEO_ID.json
Content:
```
{
  "transcript": "Transcript text here",
  "duration": 285,
  "comments": [],
  "metadata": {
    "id": "YOUTUBE_VIDEO_ID",
    "title": "YOUTUBE VIDEO TITLE",
    "channel": "YOUTUBE CHANNEL NAME",
    "published_at": "TIMESTAMP yearTtimestampZ"
  }
}
```
]

RESPONSE:
Awesome project. Here’s a clean, repeatable plan (all FOSS) to download YouTube audio locally on Debian/Ubuntu, transcribe it without touching YouTube’s transcript API, and emit exactly the JSON you showed.

# Plan (high level)

1. **Prereqs (system)**

   * Install FFmpeg (audio muxing/decoding).
   * Create a Python venv.

2. **Python deps (all FOSS)**

   * `yt-dlp` — robust YouTube downloader + rich metadata.
   * `faster-whisper` — fast, accurate Whisper transcription (no OpenAI API; runs fully local).
   * `soundfile` (optional) — only if you want to sanity-check duration from file (we’ll prefer yt-dlp metadata).
   * `tqdm` (optional) — progress bars.

3. **Workflow**

   * Use `yt-dlp` **as a Python library** (no shelling out) to:

     * Resolve the URL and **download best audio** (e.g., m4a/webm).
     * Collect metadata: `id`, `title`, `channel`/`uploader`, `duration`, `timestamp` (or `release_timestamp`), etc.
   * Transcribe the downloaded audio with `faster-whisper`:

     * Choose a local Whisper model (`tiny`, `base`, `small`, `medium`, or `large-v3`). You can change models via a CLI flag.
     * Concatenate all segments into a single transcript string.
   * Build the JSON payload matching your schema exactly and write it to `<VIDEO_ID>.json`.

4. **Performance & accuracy notes**

   * Use `small`/`medium` for good accuracy on CPU. If you have an NVIDIA GPU, `faster-whisper` can use CUDA automatically when installed with the right wheels; the script auto-detects and enables GPU if available.
   * Long videos are handled by chunked streaming in `faster-whisper`; memory use stays reasonable.

---

# One-time setup (Debian/Ubuntu)

```bash
# 1) System deps
sudo apt update
sudo apt install -y ffmpeg python3-venv

# 2) Python venv
python3 -m venv .venv
source .venv/bin/activate

# 3) Python packages
pip install --upgrade pip
pip install yt-dlp faster-whisper tqdm
# Optional: for reading durations directly from the audio file
pip install soundfile
```

> GPU users: If you have CUDA and want GPU acceleration, install the CUDA-enabled wheels for `ctranslate2` (pulled in by faster-whisper). The default `pip install faster-whisper` often just works; otherwise see faster-whisper’s README for CUDA wheel options.

---

# Usage

Save the script below as `yt_local_transcribe.py`, activate your venv, then run:

```bash
python yt_local_transcribe.py \
  --url "https://www.youtube.com/watch?v=YOUTUBE_VIDEO_ID" \
  --model medium \
  --language auto \
  --output-dir ./out
```

This will create `./out/YOUTUBE_VIDEO_ID.json` in **exactly** your target format.

---

# The script (ready to run)

```python
#!/usr/bin/env python3
import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from yt_dlp import YoutubeDL
from faster_whisper import WhisperModel


def iso8601_from_ts(ts: int | float | None) -> str | None:
    """
    Convert a unix timestamp to ISO-8601 Zulu (e.g., 2024-01-02T03:04:05Z).
    Returns None if ts is None.
    """
    if ts is None:
        return None
    try:
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        return None


def pick_channel(meta: dict) -> str:
    """
    Prefer the explicit 'channel' field; fall back to 'uploader', else empty.
    """
    return meta.get("channel") or meta.get("uploader") or ""


def download_audio_and_metadata(url: str, outdir: Path) -> tuple[Path, dict]:
    """
    Use yt-dlp (as a library) to download bestaudio and return (audio_path, info_dict).
    Audio is remuxed to m4a when possible.
    """
    outdir.mkdir(parents=True, exist_ok=True)
    # Create a deterministic template: <outdir>/<id>.%(ext)s
    ydl_opts = {
        "outtmpl": str(outdir / "%(id)s.%(ext)s"),
        "format": "bestaudio/best",
        # Remux to m4a if possible; otherwise leave as original container
        "postprocessors": [
            {"key": "FFmpegExtractAudio", "preferredcodec": "m4a", "preferredquality": "0"}
        ],
        "quiet": True,
        "no_warnings": True,
    }

    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)

    # Determine the final path:
    vid = info["id"]
    # Try preferred m4a first; fall back to any existing extension yt-dlp created.
    candidate_m4a = outdir / f"{vid}.m4a"
    if candidate_m4a.exists():
        audio_path = candidate_m4a
    else:
        # Search for any file starting with <id>.
        matches = list(outdir.glob(f"{vid}.*"))
        if not matches:
            raise FileNotFoundError("Downloaded audio file not found.")
        audio_path = matches[0]

    return audio_path, info


def transcribe_audio(
    audio_path: Path,
    model_name: str = "medium",
    language: str | None = None,
    beam_size: int = 5,
    vad_filter: bool = True,
) -> str:
    """
    Transcribe audio with faster-whisper and return a single concatenated transcript string.
    language: None or "auto" lets the model detect automatically; else pass a BCP-47 code like "en".
    """
    # Auto device: GPU if available, else CPU
    model = WhisperModel(model_name, device="auto", compute_type="auto")
    # If language == "auto", pass None to trigger detection
    lang_arg = None if (language is None or language.lower() == "auto") else language

    segments, _info = model.transcribe(
        str(audio_path),
        language=lang_arg,
        beam_size=beam_size,
        vad_filter=vad_filter,
        # You can add other params: temperature, compression_ratio_threshold, etc.
    )

    # Concatenate all segments with spaces (simple, "normal transcript")
    transcript_parts = []
    for seg in segments:
        # seg.text already contains leading spaces trimmed by faster-whisper
        transcript_parts.append(seg.text.strip())
    return " ".join([t for t in transcript_parts if t])


def build_output_json(meta: dict, transcript: str) -> dict:
    """
    Build final JSON matching the requested schema.

    Target:
    {
      "transcript": "Transcript text here",
      "duration": 285,
      "comments": [],
      "metadata": {
        "id": "YOUTUBE_VIDEO_ID",
        "title": "YOUTUBE VIDEO TITLE",
        "channel": "YOUTUBE CHANNEL NAME",
        "published_at": "TIMESTAMP yearTtimestampZ"
      }
    }
    """
    vid = meta.get("id", "")
    title = meta.get("title", "")
    channel = pick_channel(meta)
    # Duration in seconds from yt-dlp metadata if present
    duration = meta.get("duration")
    if duration is None:
        # Fallback to 0 if not known (you could derive via ffprobe or soundfile if desired)
        duration = 0

    # Prefer primary timestamp; fall back to release_timestamp; else None
    ts = meta.get("timestamp")
    if ts is None:
        ts = meta.get("release_timestamp")
    published_iso = iso8601_from_ts(ts) or ""  # empty string if unknown

    return {
        "transcript": transcript,
        "duration": int(duration) if duration is not None else 0,
        "comments": [],
        "metadata": {
            "id": vid,
            "title": title,
            "channel": channel,
            "published_at": published_iso,
        },
    }


def main():
    parser = argparse.ArgumentParser(description="Download YouTube audio and transcribe locally (no YouTube transcript API).")
    parser.add_argument("--url", required=True, help="YouTube video URL (or ID).")
    parser.add_argument("--model", default="medium", help="Whisper model: tiny | base | small | medium | large-v3 (default: medium).")
    parser.add_argument("--language", default="auto", help='Language code (e.g., "en") or "auto" to detect (default: auto).')
    parser.add_argument("--output-dir", default="./out", help="Directory to write the output JSON and audio (default: ./out).")
    parser.add_argument("--keep-audio", action="store_true", help="Keep the downloaded audio file (default: delete after).")
    args = parser.parse_args()

    outdir = Path(args.output_dir).resolve()
    outdir.mkdir(parents=True, exist_ok=True)

    try:
        audio_path, meta = download_audio_and_metadata(args.url, outdir)
    except Exception as e:
        print(f"[error] Failed to download audio/metadata: {e}", file=sys.stderr)
        sys.exit(2)

    try:
        transcript = transcribe_audio(
            audio_path=audio_path,
            model_name=args.model,
            language=args.language,
        )
    except Exception as e:
        print(f"[error] Failed to transcribe audio: {e}", file=sys.stderr)
        sys.exit(3)

    output_obj = build_output_json(meta, transcript)

    # Write JSON to <VIDEO_ID>.json
    vid = output_obj["metadata"]["id"] or "output"
    json_path = outdir / f"{vid}.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(output_obj, f, ensure_ascii=False, indent=2)

    # Optionally clean up audio
    if not args.keep_audio:
        try:
            os.remove(audio_path)
        except Exception:
            pass

    print(f"Done. Wrote: {json_path}")


if __name__ == "__main__":
    main()
```

---

## Notes & tweaks

* **Choosing a model:**

  * `tiny/base`: fastest, lowest accuracy.
  * `small/medium`: solid general-purpose CPU choices.
  * `large-v3`: best accuracy, heavier compute/RAM.
* **Language handling:** leave `--language auto` to detect; specify e.g. `--language en` if you know it to slightly improve speed.
* **Durations:** we trust `yt-dlp`’s `duration`. If you want to derive from the file, add an ffprobe or `soundfile` check as a fallback.
* **Comments array:** left empty per your schema. If later you want to populate comments *without the YouTube API*, you can add an HTML scraper with caching and rate-limiting, but that’s separate.

That's it—fully local, API-free, and emits exactly your target JSON.

---

## 🎉 Implementation Status

This plan was **successfully implemented** as `local_transcribe.py` with significant enhancements:

### ✅ Original Requirements Met
- YouTube audio download via yt-dlp ✅
- Local Whisper transcription via faster-whisper ✅
- Exact JSON schema output ✅
- FOSS-only solution ✅
- No API dependencies ✅

### 🚀 Beyond Original Plan
The implementation evolved into a **production-ready system** with:

1. **`local_transcribe.py`** - Enhanced single-video processor
   - CUDA safety checks with CPU fallback
   - Multiple download strategies
   - Cookie support for restricted videos
   - Better error handling

2. **`batch_transcribe.py`** - Production batch processor (NEW)
   - Process 500+ videos with resume capability
   - Status tracking in JSON
   - Retry logic with configurable attempts
   - Detailed logging and failure reports
   - File verification before marking complete
   - Real-time progress monitoring

3. **`reconcile.py`** - Three-way status checker (NEW)
   - Compares input file vs finished.dat vs actual transcript files
   - Identifies missing/orphaned files
   - Generates clean input files
   - Detects duplicates and invalid URLs

### 📚 Documentation
- [QUICK_REFERENCE.md](QUICK_REFERENCE.md) - Start here for common commands
- [BATCH_TRANSCRIBE_README.md](BATCH_TRANSCRIBE_README.md) - Complete feature guide
- [UPGRADE_SUMMARY.md](UPGRADE_SUMMARY.md) - Why the enhanced system
- [IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md) - Evolution timeline

### 🎯 Quick Start (Current System)

```bash
# For batch processing (recommended):
python batch_transcribe.py --input inputfile.txt

# For single videos:
python local_transcribe.py --url "https://youtube.com/watch?v=VIDEO_ID"

# Check status:
python reconcile.py
```

**See [QUICK_REFERENCE.md](QUICK_REFERENCE.md) for full usage guide.**


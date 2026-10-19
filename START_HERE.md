# Quick Start Guide

Get started with queue-first local YouTube transcription.

## Installation

### Option 1: Using pipx (Recommended - Global Installation)

**Quick Install (CPU or GPU):**
```bash
# CPU-only and GPU hosts are both supported. This never installs NVIDIA
# packages, so it works on machines without CUDA.
pipx install git+https://github.com/draeician/local_transcribe.git

# Optional: add CUDA 12 acceleration on a GPU host with a working driver.
# --auto (default) installs CUDA only when a usable GPU+driver is found.
./install_local_transcribe.py
./install_local_transcribe.py --cpu     # force CPU
./install_local_transcribe.py --cuda    # force CUDA (errors when unusable)

lt doctor
```

Deno is only needed for YouTube downloads; it is **not** required to install the
application or to transcribe local audio files.

**Manual Install:**
```bash
pipx install .
# or: pipx install git+https://github.com/draeician/local_transcribe.git

# Optional CUDA profile (GPU hosts only; stays inside the pipx venv)
pipx inject local-transcribe -r requirements-cuda.txt

lt doctor
```

### Option 2: Using pip in a Virtual Environment

```bash
cd ~/git/personal/local_transcribe
python3 -m venv venv
source venv/bin/activate
pip install -e .
# Optional CUDA acceleration (GPU hosts only):
pip install -e '.[cuda]'
lt doctor
```

## One-time queue + worker setup (GPU host)

Do this once on the host that will download and transcribe:

```bash
# Shared NFSv3 directory (must be rw, vers=3, with NLM locks)
lt queue init --queue-dir /path/to/transcription-queue
lt queue doctor

# YouTube cookies for the worker (Brave default; also --browser chrome|firefox)
lt cookies refresh

lt worker install
systemctl --user daemon-reload
systemctl --user enable --now local-transcribe-worker.service
systemctl --user status local-transcribe-worker.service

# Retry every failed queue job (one new pending per unique source)
lt queue retry
```

Full mount/config notes: [docs/QUEUE_OPERATOR.md](docs/QUEUE_OPERATOR.md).

## Basic usage

### Single video or local audio (default: enqueue + wait)

```bash
# --device/--compute-type default to "auto": CUDA when usable, else CPU int8.
lt transcribe "https://www.youtube.com/watch?v=VIDEO_ID" \
  --model medium \
  --device auto \
  --compute-type auto

lt transcribe "/path/to/recording.m4a" \
  --model medium \
  --device auto \
  --compute-type auto \
  --output-dir ./out

# Fire-and-forget enqueue
lt transcribe "https://www.youtube.com/watch?v=VIDEO_ID" --no-wait
```

### Batch (default: bulk enqueue)

```bash
echo "https://www.youtube.com/watch?v=VIDEO_ID" > inputfile.txt
lt batch --input inputfile.txt --device auto --compute-type auto

# Optional: wait for those jobs to finish
lt batch --input inputfile.txt --wait
```

### Watch progress

```bash
lt queue stats
lt queue list
lt queue list --status processing
lt queue list --status completed
journalctl --user -u local-transcribe-worker.service -f
```

Logs also land under `~/.local/state/local-transcribe/logs/`.

### Legacy pending file (ref-cli)

The worker auto-imports `~/references/transcripts/transcript-pending.md` when
that file changes — ref does not need `local-transcribe` installed. Manual
one-shot if needed:

```bash
lt queue import ~/references/transcripts/transcript-pending.md
```

### Emergency: bypass the queue

```bash
lt transcribe "https://www.youtube.com/watch?v=VIDEO_ID" --direct
lt batch --input inputfile.txt --direct --resume
```

## System Requirements

- Python 3.10+
- **Deno** (required for YouTube 2026 SABR support) - [Install Deno](https://deno.com/)
- Shared **NFSv3** with NLM locks for multi-host queue (single-host local path works for lab/dev)
- CUDA-capable GPU on the worker host (optional; CPU works too)
- FFmpeg
- See [README.md](README.md) for full setup instructions

### Installing Deno

```bash
curl -fsSL https://deno.land/install.sh | sh
export DENO_INSTALL="$HOME/.deno"
export PATH="$DENO_INSTALL/bin:$PATH"
sudo ln -sf "$DENO_INSTALL/bin/deno" /usr/local/bin/deno
deno --version
```

## Next Steps

- [QUICK_REFERENCE.md](QUICK_REFERENCE.md) — everyday queue/worker commands
- [docs/QUEUE_OPERATOR.md](docs/QUEUE_OPERATOR.md) — NFS, config, systemd
- [README.md](README.md) — troubleshooting (Deno, 403, CUDA)
- `lt doctor` / `lt queue doctor` — verify the environment

# Quick Start Guide

Get started with queue-first local YouTube transcription.

## Installation

### Option 1: Using pipx (Recommended - Global Installation)

**Quick Install (with GPU support):**
```bash
# First, ensure Deno is installed (see System Requirements below)
deno --version

# From local directory
./install_with_gpu.sh

# Or from git repository (one-liner)
pipx install git+https://github.com/draeician/local_transcribe.git && pipx runpip local-transcribe install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124
```

**Manual Install:**
```bash
pipx install .
# or: pipx install git+https://github.com/draeician/local_transcribe.git

# Optional manual CUDA torch (doctor/worker install may do this on NVIDIA hosts)
pipx runpip local-transcribe install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124

lt doctor
```

### Option 2: Using pip in a Virtual Environment

```bash
cd ~/git/personal/local_transcribe
python3 -m venv venv
source venv/bin/activate
pip install -e .
pip install -r requirements.txt
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124
lt doctor
```

## One-time queue + worker setup (GPU host)

Do this once on the host that will download and transcribe:

```bash
# Shared NFSv3 directory (must be rw, vers=3, with NLM locks)
lt queue init --queue-dir /path/to/transcription-queue
lt queue doctor

# Optional: YouTube cookies for the worker
# mkdir -p ~/.config/local-transcribe
# export Netscape cookies to youtube-cookies.txt, then add to config.yaml:
#   queue.default_auth_profile: yt
#   auth_profiles.yt.cookies_file: ~/.config/local-transcribe/youtube-cookies.txt

lt worker install
systemctl --user daemon-reload
systemctl --user enable --now local-transcribe-worker.service
systemctl --user status local-transcribe-worker.service
```

Full mount/config notes: [docs/QUEUE_OPERATOR.md](docs/QUEUE_OPERATOR.md).

## Basic usage

### Single video or local audio (default: enqueue + wait)

```bash
lt transcribe "https://www.youtube.com/watch?v=VIDEO_ID" \
  --model medium \
  --device cuda \
  --compute-type float16

lt transcribe "/path/to/recording.m4a" \
  --model medium \
  --device cuda \
  --compute-type float16 \
  --output-dir ./out

# Fire-and-forget enqueue
lt transcribe "https://www.youtube.com/watch?v=VIDEO_ID" --no-wait
```

### Batch (default: bulk enqueue)

```bash
echo "https://www.youtube.com/watch?v=VIDEO_ID" > inputfile.txt
lt batch --input inputfile.txt --device cuda --compute-type float16

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

### Import a legacy pending file

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

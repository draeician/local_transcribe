# Quick Start Guide

Get started with local YouTube transcription in under 10 commands.

## Installation

### Option 1: Using pipx (Recommended - Global Installation)

**Quick Install (with GPU support):**
```bash
# First, ensure Deno is installed (see System Requirements above)
deno --version  # Verify Deno is installed

# From local directory
./install_with_gpu.sh

# Or from git repository (one-liner)
pipx install git+https://github.com/draeician/local_transcribe.git && pipx runpip local-transcribe install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124
```

**Manual Install:**
```bash
# Install from local directory
pipx install .

# Or install directly from git repository
pipx install git+https://github.com/draeician/local_transcribe.git

# Install PyTorch with CUDA support (required for GPU)
pipx runpip local-transcribe install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124

# Verify installation
lt doctor
```

### Option 2: Using pip in a Virtual Environment

```bash
# 1. Clone and enter the repository
cd ~/git/personal/local_transcribe

# 2. Create and activate virtual environment
python3 -m venv venv
source venv/bin/activate

# 3. Install the package
pip install -e .

# 4. Install GPU dependencies (optional)
pip install -r requirements.txt
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124

# 5. Verify installation
lt doctor
```

## Basic Usage

### Single Video

```bash
lt transcribe "https://www.youtube.com/watch?v=VIDEO_ID" \
  --model medium \
  --device cuda \
  --compute-type float16 \
  --output-dir ./out
```

### Batch Processing

```bash
# 1. Create input file with URLs (one per line)
echo "https://www.youtube.com/watch?v=VIDEO_ID" > inputfile.txt

# 2. Run batch processing
lt batch --input inputfile.txt --device cuda --compute-type float16

# 3. If interrupted, resume
lt batch --resume

# 4. Check status
lt status

# 5. Reconcile and see what's done
lt reconcile
```

## System Requirements

- Python 3.10+
- **Deno** (required for YouTube 2026 SABR support) - [Install Deno](https://deno.com/)
- CUDA-capable GPU (optional, CPU works too)
- FFmpeg
- See [README.md](README.md) for full setup instructions

### Installing Deno

```bash
# Install Deno
curl -fsSL https://deno.land/install.sh | sh

# Add to PATH (add to ~/.bashrc for persistence)
export DENO_INSTALL="$HOME/.deno"
export PATH="$DENO_INSTALL/bin:$PATH"

# Create system-wide symlink (recommended for pipx environments)
sudo ln -sf "$DENO_INSTALL/bin/deno" /usr/local/bin/deno

# Verify installation
deno --version
```

## Next Steps

- Read [README.md](README.md) for detailed setup and troubleshooting
- Check [QUICK_REFERENCE.md](QUICK_REFERENCE.md) for common commands
- Run `lt doctor` to verify your environment


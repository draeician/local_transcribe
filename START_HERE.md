# Quick Start Guide

Get started with local YouTube transcription in under 10 commands.

## Installation

```bash
# 1. Clone and enter the repository
cd ~/git/personal/local_transcribe

# 2. Create and activate virtual environment
python3 -m venv venv
source venv/bin/activate

# 3. Install the package
pip install -e .

# 4. Verify installation
lt --help
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
- CUDA-capable GPU (optional, CPU works too)
- FFmpeg
- See [README.md](README.md) for full setup instructions

## Next Steps

- Read [README.md](README.md) for detailed setup and troubleshooting
- Check [QUICK_REFERENCE.md](QUICK_REFERENCE.md) for common commands
- Run `lt doctor` to verify your environment


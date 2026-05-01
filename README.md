# Local YouTube Transcription System

> **Production-ready batch transcription** with resume capability, file verification, and detailed logging.

**Features:**
- ✅ Local processing (no API throttling)
- ✅ CUDA GPU acceleration + CPU fallback
- ✅ Batch processing with resume capability
- ✅ File verification and status tracking
- ✅ Retry logic and failure handling
- ✅ FOSS only (yt-dlp + faster-whisper)

---

## 🚀 Quick Start

### Installation Options

**Option 1: pipx (Recommended - Global Installation)**
```bash
# Install from GitHub with GPU support
pipx install git+https://github.com/draeician/local_transcribe.git
pipx runpip local-transcribe install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124

# Verify installation
lt doctor
```

**Option 2: Local Development**
```bash
# Clone the repository
git clone https://github.com/draeician/local_transcribe.git
cd local_transcribe

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install the package
pip install -e .
pip install -r requirements.txt
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124
```

**Updating an Existing Installation**
```bash
# If installed via pipx from git
pipx upgrade local-transcribe

# If installed locally via git clone
cd ~/git/personal/local_transcribe
git pull
pip install -e . --upgrade
```

See [START_HERE.md](START_HERE.md) for detailed installation instructions.

### Batch Processing (Recommended)
```bash
# Process multiple videos with resume capability
lt batch --input inputfile.txt --device cuda --compute-type float16

# If interrupted, just resume
lt batch --resume

# Check status
lt status

# Reconcile and see what's done
lt reconcile
```

### Single Video or Local Audio File
```bash
# YouTube (HTTPS URL)
lt transcribe "https://youtube.com/watch?v=VIDEO_ID" \
  --model medium \
  --device cuda \
  --compute-type float16

# Local file (output: `<output-dir>/<slug-from-filename>.json`; ffmpeg should be on PATH for formats like m4a)
lt transcribe "/path/to/recording.m4a" \
  --model medium \
  --device cuda \
  --compute-type float16 \
  --output-dir ./out
```

### Using the CLI
All functionality is available through the unified `lt` command:
- `lt` or `lt --help` - Show help screen
- `lt --version` - Show version information
- `lt transcribe` - Transcribe a single YouTube video or local audio file
- `lt batch` - Process multiple videos
- `lt reconcile` - Reconcile input with finished transcripts
- `lt status` - Show batch processing status
- `lt report` - Generate failure report
- `lt doctor` - Run environment diagnostics
- `lt update` - Update the package (checks Deno requirement)

---

## 📚 Documentation

| Document | Purpose |
|----------|---------|
| **[START_HERE.md](START_HERE.md)** | ⭐ Quick installation guide |
| **[QUICK_REFERENCE.md](QUICK_REFERENCE.md)** | Common commands |
| **[BATCH_TRANSCRIBE_README.md](BATCH_TRANSCRIBE_README.md)** | Complete batch system guide |
| **[IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md)** | System evolution & features |
| **[UPGRADE_SUMMARY.md](UPGRADE_SUMMARY.md)** | Why use batch_transcribe.py |
| **[BUILDPLAN.md](BUILDPLAN.md)** | Original design document |
| **This file (README.md)** | System setup & troubleshooting |

---

## Installation Guide

### Prerequisites

```bash
# 1. Install system dependencies
sudo apt update
sudo apt install -y python3 python3-venv python3-pip ffmpeg pipx

# 2. Install Deno (required for YouTube 2026 SABR support)
curl -fsSL https://deno.land/install.sh | sh
# Add Deno to PATH (add to ~/.bashrc for persistence)
export DENO_INSTALL="$HOME/.deno"
export PATH="$DENO_INSTALL/bin:$PATH"
# Create symlink for system-wide access (optional but recommended)
sudo ln -sf "$DENO_INSTALL/bin/deno" /usr/local/bin/deno

# 3. Install CUDA Toolkit (for GPU support)
wget https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2404/x86_64/cuda-keyring_1.1-1_all.deb
sudo dpkg -i cuda-keyring_1.1-1_all.deb
sudo apt update
sudo apt install -y cuda-toolkit

# 4. Add CUDA to environment
echo 'export PATH=/usr/local/cuda/bin:$PATH' >> ~/.bashrc
echo 'export LD_LIBRARY_PATH=/usr/local/cuda/lib64:$LD_LIBRARY_PATH' >> ~/.bashrc
source ~/.bashrc

# 5. Verify installations
deno --version  # Should show Deno version
nvcc --version  # Should show CUDA version
nvidia-smi      # Should show GPU info
```

### Installation Methods

#### Method 1: pipx from GitHub (Recommended)

**First-time installation:**
```bash
# Install the package (includes GPU dependencies)
pipx install git+https://github.com/draeician/local_transcribe.git

# Install PyTorch with CUDA support
pipx runpip local-transcribe install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124

# Verify installation
lt doctor
```

**Updating an existing installation:**
```bash
# Update to latest version from GitHub
pipx upgrade local-transcribe

# Update PyTorch if needed
pipx runpip local-transcribe install --upgrade torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124

# Verify
lt doctor
```

#### Method 2: Local Development (git clone)

**First-time installation:**
```bash
# Clone the repository
git clone https://github.com/draeician/local_transcribe.git
cd local_transcribe

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install the package and dependencies
pip install -e .
pip install -r requirements.txt
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124

# Verify installation
lt doctor
```

**Updating an existing installation:**
```bash
# Navigate to the repository
cd ~/git/personal/local_transcribe  # or wherever you cloned it

# Pull latest changes
git pull

# Update the package
pip install -e . --upgrade

# Update dependencies if needed
pip install -r requirements.txt --upgrade
pip install --upgrade torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124

# Verify
lt doctor
```

### Quick Test

```bash
# Test single video transcription (YouTube)
lt transcribe "https://www.youtube.com/watch?v=DMQ_HcNSOAI" \
  --model medium \
  --device cuda \
  --compute-type float16

# Test local audio (writes ./out/<stem-slug>.json)
lt transcribe "/path/to/sample.m4a" --output-dir ./out --device cuda --compute-type float16

# Test batch processing
lt batch --input inputfile.txt
```

---

## Troubleshooting: CUDA/cuDNN Issues (2025-10-21)

### Summary
**Problem**: Script crashes with `Unable to load libcudnn_ops.so` error.  
**Root Cause**: cuDNN libraries in venv not in `LD_LIBRARY_PATH`.  
**Quick Fix**: The `lt` CLI handles this automatically. If issues persist, run `lt doctor` to diagnose.

### Issue
```
2025-10-21 23:27:44.354195612 [W:onnxruntime:Default, device_discovery.cc:164 DiscoverDevicesForPlatform] GPU device discovery failed: device_discovery.cc:89 ReadFileContents Failed to open file: "/sys/class/drm/card5/device/vendor"
Unable to load any of {libcudnn_ops.so.9.1.0, libcudnn_ops.so.9.1, libcudnn_ops.so.9, libcudnn_ops.so}
Invalid handle. Cannot load symbol cudnnCreateTensorDescriptor
Aborted (core dumped)
```

**Note about the DRM warning**: The first warning about `/sys/class/drm/card5/device/vendor` is **harmless and can be ignored**. 
- `card5` is a virtual display device (EVDI - "Extensible Virtual Display Interface") used by DisplayLink USB adapters
- It doesn't have a `device/vendor` file because it's a platform device, not a PCI device
- ONNX Runtime scans all DRM cards looking for GPUs, finds the EVDI device, and correctly skips it
- Your actual NVIDIA GPU is `card2` (PCI device 0x10de) and works fine
- This warning appears even when everything is working correctly

### Root Cause
The cuDNN libraries installed via `nvidia-cudnn-cu13` Python package are located in the venv at:
```
venv/lib/python3.12/site-packages/nvidia/cudnn/lib/
venv/lib/python3.12/site-packages/nvidia/cublas/lib/
```

However, these paths are **NOT** in the `LD_LIBRARY_PATH` by default, causing ctranslate2/onnxruntime to fail when trying to load cuDNN.

### Diagnosis Commands
```bash
# Verify GPU and driver
nvidia-smi  # Shows NVIDIA RTX A1000, Driver 535.274.02, CUDA 12.2

# Verify CUDA compiler
nvcc --version  # Shows CUDA 12.0.140

# Check cuDNN location
source venv/bin/activate
python3 -c "import nvidia.cudnn; import os; print(os.path.dirname(nvidia.cudnn.__file__))"
# Output: venv/lib/python3.12/site-packages/nvidia/cudnn

# List cuDNN libraries
ls -la venv/lib/python3.12/site-packages/nvidia/cudnn/lib/
# Shows: libcudnn_ops.so.9, libcudnn_adv.so.9, libcudnn_cnn.so.9, etc.

# Test without LD_LIBRARY_PATH (FAILS)
python3 -c "import ctranslate2; print(ctranslate2.get_cuda_device_count())"
# Results in crash/abort

# Test WITH LD_LIBRARY_PATH (WORKS)
export LD_LIBRARY_PATH=$(python3 -c "import nvidia.cublas, nvidia.cudnn, os; print(os.path.dirname(nvidia.cublas.__file__)+'/lib:'+os.path.dirname(nvidia.cudnn.__file__)+'/lib')"):$LD_LIBRARY_PATH
python3 -c "import ctranslate2; print('CT2 version:', ctranslate2.__version__); print('CUDA device count:', ctranslate2.get_cuda_device_count())"
# Output: CT2 version: 4.6.0, CUDA device count: 1

# Verify PyTorch CUDA
python3 -c "import torch; print('CUDA available:', torch.cuda.is_available()); print('Device:', torch.cuda.get_device_name(0)); print('cuDNN version:', torch.backends.cudnn.version())"
# Output: CUDA available: True, Device: NVIDIA RTX A1000 6GB Laptop GPU, cuDNN version: 90100
```

### Solution Options

**Option 1: Use the CLI (Recommended) ⭐**

The `lt` CLI handles CUDA/cuDNN setup automatically:

```bash
# Single video (URL or path to an audio file on disk)
lt transcribe "https://youtube.com/watch?v=VIDEO_ID" --device cuda

# Batch processing with resume capability
lt batch --input inputfile.txt

# If interrupted, just resume
lt batch --resume

# Check status anytime
lt status

# Reconcile and see what's done
lt reconcile
```

**Why use the CLI?**
- ✅ **Resume capability** - Never lose progress
- ✅ **File verification** - Checks actual output files
- ✅ **Status tracking** - JSON state for every video
- ✅ **Retry logic** - Automatic retry for failures
- ✅ **Detailed logging** - Complete audit trail
- ✅ **Failure reports** - Know what failed and why
- ✅ **Smart detection** - Auto-detects URLs vs files
- ✅ **Unified interface** - One command for everything

**Option 3: Add to activation script (Persistent for this venv)**
Add to `venv/bin/activate`:
```bash
# Add near the end, before the final comments
CUDNN_LIBS=$(python3 -c "import nvidia.cublas, nvidia.cudnn, os; print(os.path.dirname(nvidia.cublas.__file__)+'/lib:'+os.path.dirname(nvidia.cudnn.__file__)+'/lib')" 2>/dev/null)
if [ -n "$CUDNN_LIBS" ]; then
    export LD_LIBRARY_PATH="$CUDNN_LIBS:$LD_LIBRARY_PATH"
fi
```

### Current Status
- GPU detected: ✅ NVIDIA RTX A1000 6GB (Driver 535.274.02)
- CUDA Toolkit: ✅ 12.0.140 installed
- cuDNN libraries: ✅ Present in venv (version 9.1.0)
- PyTorch CUDA: ✅ Works when LD_LIBRARY_PATH is set
- ctranslate2 CUDA: ✅ Works when LD_LIBRARY_PATH is set
- **Issue**: LD_LIBRARY_PATH not configured by default

### Verification
Run diagnostics to verify your setup:
```bash
lt doctor
```

This will check:
- GPU detection (nvidia-smi)
- CUDA toolkit (nvcc)
- Python packages (nvidia-cudnn, torch, ctranslate2)
- Library paths and LD_LIBRARY_PATH
- ctranslate2 CUDA functionality
- PyTorch CUDA functionality

### Next Steps
Choose one of the solution options above to ensure the cuDNN libraries are in the library search path when running the transcription script.

### FAQ

**Q: I'm seeing a warning about `/sys/class/drm/card5/device/vendor` - is this a problem?**  
A: No, this is harmless. It's just ONNX Runtime scanning for GPUs and encountering a virtual display device (EVDI/DisplayLink). Your actual NVIDIA GPU (card2) is detected and works fine.

**Q: The script works now, but why did this happen?**  
A: The NVIDIA cuDNN libraries installed via pip go into your venv's `site-packages` directory. These need to be in `LD_LIBRARY_PATH` for native libraries (like ctranslate2) to find them at runtime. The wrapper script handles this automatically.

**Q: Can I suppress the DRM warning?**  
A: The DRM warning is harmless and can be ignored. It's just ONNX Runtime scanning for GPUs.

**Q: How do I process multiple videos?**  
A: Use `lt batch --input inputfile.txt`. It has resume capability, status tracking, and file verification. See [QUICK_REFERENCE.md](QUICK_REFERENCE.md).

**Q: What if I get interrupted while processing?**  
A: Just run `lt batch --resume` - it picks up exactly where you left off.

**Q: How do I check what's actually completed?**  
A: Run `lt reconcile` - it checks actual transcript files vs. what's recorded in finished.dat and generates reports.

---

## Troubleshooting: YouTube 2026 SABR Throttling & 403 Forbidden Errors (2026-01-25)

### Summary
**Problem**: Downloads fail with `HTTP 403: Forbidden` errors when attempting to download YouTube videos.  
**Root Cause**: YouTube's 2026 "SABR" (Server-Side Ad-Insertion and Rendering) protocol requires JavaScript-based signature generation via Deno runtime.  
**Quick Fix**: Install Deno and ensure it's accessible in `/usr/local/bin/deno` or in your PATH.

### Issue
In early 2026, YouTube escalated its "SABR" protocol, introducing a strict **"n-challenge"**—a rotating JavaScript-based signature required to authorize media downloads.

**Symptoms:**
- Automated tools like `yt-dlp` and `local_transcribe` hit `HTTP 403: Forbidden` errors during fragment downloads
- Only low-bitrate, fragmented HLS (m3u8) streams are available
- Standard high-quality direct audio formats (like `itag 140`) disappear from available formats

### Root Cause
Without solving the JavaScript challenge, YouTube only serves low-quality fragmented streams. The modern "n-challenge" puzzles are:
- Session-specific and dynamic
- Require JavaScript execution to generate correct signatures
- Cannot be solved with static cookies or user agents alone

### Solution: The "Harmony 2026" Stable Strategy

The solution relies on a combination of environment bridging and forcing the "Web" player client, which prioritizes JavaScript execution.

**1. Install Deno (JavaScript Runtime):**
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

**2. Verify Deno is Accessible:**
```bash
# Check if deno is in PATH
which deno

# Should show: /usr/local/bin/deno or ~/.deno/bin/deno

# Test that pipx can see it
pipx runpip local-transcribe which deno
```

**3. The System Configuration:**
- **Runtime Dependency**: Deno linked to `/usr/local/bin` (ensures pipx virtual environments can access it)
- **Strategy Order**: 
  1. `stable-web`: Uses standard Web client headers + local Deno solver
  2. `ios-fallback`: Standard fallback for restricted videos
- **HTTP Backend**: Uses `requests` (stable) instead of `curl_cffi` (was causing silent crashes)

### Verification
Run diagnostics to verify your setup:
```bash
lt doctor
```

This will check:
- Deno installation and accessibility
- GPU detection (nvidia-smi)
- CUDA toolkit (nvcc)
- Python packages
- FFmpeg availability
- yt-dlp version

### Expected Behavior After Fix
```bash
lt transcribe "$TARGET" --cookies-file ~/cookies.txt
[info] Strategy: stable-web | client=['web']
✓ Done.
```

The system should now:
- ✅ Correctly identify direct m4a audio stream (Format 140)
- ✅ Bypass fragmented HLS paths
- ✅ Avoid 403 errors
- ✅ Work reliably with the web client strategy

### FAQ

**Q: Why do I need Deno?**  
A: YouTube's 2026 SABR protocol requires JavaScript execution to solve dynamic "n-challenge" signatures. Deno provides a high-performance JavaScript runtime that yt-dlp can use to solve these challenges.

**Q: Can I use Node.js instead of Deno?**  
A: While Node.js was tried, the pipx isolated virtual environment had difficulty reliably calling the local Node binary across the environment boundary. Deno with a system-wide symlink in `/usr/local/bin` ensures reliable access.

**Q: What if I still get 403 errors?**  
A: Ensure Deno is installed and accessible. Run `lt doctor` to verify. Also consider using `--cookies-file` or `--cookies-from-browser` to authenticate with YouTube.

**Q: Do I need to update Deno regularly?**  
A: Deno updates are independent of this project. You can update Deno with `deno upgrade` if needed, but the current version should work fine.

**Q: What if Deno is not found in pipx environment?**  
A: The system-wide symlink (`/usr/local/bin/deno`) ensures pipx can access Deno. If issues persist, verify the symlink exists and is executable.

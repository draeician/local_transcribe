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

### Single Video
```bash
lt transcribe "https://youtube.com/watch?v=VIDEO_ID" \
  --model medium \
  --device cuda \
  --compute-type float16
```

### Using the CLI
All functionality is available through the unified `lt` command:
- `lt` or `lt --help` - Show help screen
- `lt --version` - Show version information
- `lt transcribe` - Transcribe a single video
- `lt batch` - Process multiple videos
- `lt reconcile` - Reconcile input with finished transcripts
- `lt status` - Show batch processing status
- `lt report` - Generate failure report
- `lt doctor` - Run environment diagnostics

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

# 2. Install CUDA Toolkit (for GPU support)
wget https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2404/x86_64/cuda-keyring_1.1-1_all.deb
sudo dpkg -i cuda-keyring_1.1-1_all.deb
sudo apt update
sudo apt install -y cuda-toolkit

# 3. Add CUDA to environment
echo 'export PATH=/usr/local/cuda/bin:$PATH' >> ~/.bashrc
echo 'export LD_LIBRARY_PATH=/usr/local/cuda/lib64:$LD_LIBRARY_PATH' >> ~/.bashrc
source ~/.bashrc

# 4. Verify CUDA
nvcc --version
nvidia-smi
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
# Test single video transcription
lt transcribe "https://www.youtube.com/watch?v=DMQ_HcNSOAI" \
  --model medium \
  --device cuda \
  --compute-type float16

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
# Single video
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

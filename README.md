# Local YouTube Transcription System

> **Queue-first local transcription** on NFSv3 with one NLM-locked GPU worker, durable jobs, and multi-host producers.

**Features:**
- ✅ Durable NFSv3 transcription queue (`pending` → `processing` → `completed` / `failed` / `retry`)
- ✅ Single exclusive worker via NLM lock (`lt worker` + systemd user unit)
- ✅ Producers enqueue from any host (`lt transcribe`, `lt batch`, `lt queue add`, ref-cli)
- ✅ CUDA GPU acceleration + CPU fallback; ModelCache keeps Whisper loaded
- ✅ Auth profiles (cookies file / browser) for YouTube downloads
- ✅ FOSS only (yt-dlp + faster-whisper)
- ✅ Legacy in-process mode still available via `--direct`

---

## 🚀 Quick Start

### Installation Options

**Option 1: pipx (Recommended - Global Installation)**
```bash
# Install from GitHub
pipx install git+https://github.com/draeician/local_transcribe.git

# On NVIDIA hosts, doctor/worker install can pull CUDA torch automatically;
# or install manually:
pipx runpip local-transcribe install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124

# Verify installation
lt doctor
```

**Option 2: Local Development**
```bash
git clone https://github.com/draeician/local_transcribe.git
cd local_transcribe
python3 -m venv venv
source venv/bin/activate
pip install -e .
pip install -r requirements.txt
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124
```

**Updating an Existing Installation**
```bash
# From a git checkout (recommended for this repo)
cd ~/git/personal/local_transcribe
git pull
pipx install . --force
systemctl --user restart local-transcribe-worker.service   # if the worker is installed
```

See [START_HERE.md](START_HERE.md) for detailed installation instructions.

### Queue bootstrap (one-time, GPU / worker host)

```bash
# On shared NFSv3 storage
lt queue init --queue-dir /path/to/transcription-queue
# writes ~/.config/local-transcribe/config.yaml (path, UUID, NFS identity)

lt queue doctor
lt worker install
systemctl --user daemon-reload
systemctl --user enable --now local-transcribe-worker.service
```

Configure YouTube cookies for the worker (recommended):

```yaml
# ~/.config/local-transcribe/config.yaml
queue:
  path: /path/to/transcription-queue
  expected_uuid: <from init>
  default_auth_profile: yt
auth_profiles:
  yt:
    cookies_file: ~/.config/local-transcribe/youtube-cookies.txt
```

Operator details: [docs/QUEUE_OPERATOR.md](docs/QUEUE_OPERATOR.md).

### Everyday use (producers)

```bash
# Single YouTube URL or local audio — default: enqueue and wait for the worker
lt transcribe "https://youtube.com/watch?v=VIDEO_ID"
lt transcribe "/path/to/recording.m4a" --output-dir ./out

# Enqueue without waiting
lt transcribe "https://youtube.com/watch?v=VIDEO_ID" --no-wait

# Bulk-enqueue a URL list (durable; --resume is a no-op in queue mode)
lt batch --input inputfile.txt

# Watch the queue
lt queue stats
lt queue list
lt queue list --status completed
lt queue list --status processing
```

### Emergency / legacy in-process (bypass queue)

```bash
lt transcribe "https://youtube.com/watch?v=VIDEO_ID" --direct
lt batch --input inputfile.txt --direct --resume
```

### Using the CLI
All functionality is available through the unified `lt` command:
- `lt` / `lt --help` / `lt --version`
- `lt queue` — init, doctor, add, list, stats, import, purge, cancel, retry, …
- `lt worker` — run, install, status, start/stop/restart, logs
- `lt transcribe` — enqueue (default) or `--direct` in-process
- `lt batch` — bulk enqueue (default) or `--direct` BatchPipeline
- `lt reconcile` / `lt verify` / `lt status` / `lt report` — legacy batch ledger helpers
- `lt doctor` — environment + queue/NFS diagnostics
- `lt update` — refresh yt-dlp / check Deno

---

## 📚 Documentation

| Document | Purpose |
|----------|---------|
| **[START_HERE.md](START_HERE.md)** | ⭐ Quick installation + first queue run |
| **[QUICK_REFERENCE.md](QUICK_REFERENCE.md)** | Common queue/worker commands |
| **[docs/QUEUE_OPERATOR.md](docs/QUEUE_OPERATOR.md)** | NFSv3 mounts, config, systemd worker |
| **[docs/QUEUE_NFS_LAB.md](docs/QUEUE_NFS_LAB.md)** | Multi-host NFS lab checklist |
| **[docs/REF_CLI_QUEUE_ADAPTER.md](docs/REF_CLI_QUEUE_ADAPTER.md)** | ref-cli enqueue contract |
| **[CHANGELOG.md](CHANGELOG.md)** | Release notes (0.5.0 queue) |
| **[BATCH_TRANSCRIBE_README.md](BATCH_TRANSCRIBE_README.md)** | Legacy direct-batch guide |
| **This file (README.md)** | Setup & troubleshooting |

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
A: With the queue worker running, `lt batch --input inputfile.txt` bulk-enqueues URLs. Progress is durable on NFS — use `lt queue stats` / `lt queue list`. See [QUICK_REFERENCE.md](QUICK_REFERENCE.md).

**Q: What if I get interrupted while processing?**  
A: In queue mode nothing is lost; the worker continues from `pending` / `retry`. `--resume` is only meaningful with `lt batch --direct`.

**Q: How do I check what's actually completed?**  
A: Prefer `lt queue stats` and `lt queue list --status completed`. Legacy `lt reconcile` still compares input / `finished.dat` / transcript files.

**Q: Why does `lt queue list` still show many pending rows while transcripts appear?**  
A: `lt queue list` defaults to pending (first 50). Completed jobs leave that list — use `lt queue stats` to see counts drop.

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
A: Ensure Deno is installed and accessible (`lt doctor`). For the background worker, configure `default_auth_profile` + `auth_profiles` with a Netscape cookies file in `~/.config/local-transcribe/config.yaml` (a YouTube **API key** does not authorize yt-dlp downloads). For `--direct` runs, pass `--cookies-file` or `--cookies-from-browser`.

**Q: Do I need to update Deno regularly?**  
A: Deno updates are independent of this project. You can update Deno with `deno upgrade` if needed, but the current version should work fine.

**Q: What if Deno is not found in pipx environment?**  
A: The system-wide symlink (`/usr/local/bin/deno`) ensures pipx can access Deno. If issues persist, verify the symlink exists and is executable.

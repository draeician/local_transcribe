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
# CPU-only or GPU host — installs into an isolated pipx venv, never system-wide.
# This never installs NVIDIA/PyTorch packages; CPU inference just works.
pipx install git+https://github.com/draeician/local_transcribe.git

# Verify installation
lt doctor
```

**Adding CUDA acceleration (optional, GPU hosts only):**

```bash
# --auto (default) picks CUDA only when a working NVIDIA GPU + driver is found,
# otherwise installs the CPU profile.
./install_local_transcribe.py

# Force CPU, or force CUDA with a clear error when CUDA is unusable:
./install_local_transcribe.py --cpu
./install_local_transcribe.py --cuda

# Equivalent one-liner with pipx (CUDA 12 wheel profile, stays in the venv):
pipx inject local-transcribe -r requirements-cuda.txt
```

`install_local_transcribe.py` replaces the old `install_with_gpu.sh`
(which is now a thin wrapper forwarding to it). Detection uses a functional
`nvidia-smi` probe plus a driver-version threshold — an NVIDIA GPU alone does
not imply a compatible CUDA driver/library stack. Deno is **not** required to
install the application or to transcribe local audio files.

**Option 2: Local Development**

```bash
git clone https://github.com/draeician/local_transcribe.git
cd local_transcribe
python3 -m venv venv
source venv/bin/activate
pip install -e .
# Optional CUDA acceleration (GPU hosts only):
pip install -e '.[cuda]'
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

# 2. (Optional) Install Deno — required for YouTube 2026 SABR support only,
#    not for installing the app or transcribing local audio files.
curl -fsSL https://deno.land/install.sh | sh
# Add Deno to PATH (add to ~/.bashrc for persistence)
export DENO_INSTALL="$HOME/.deno"
export PATH="$DENO_INSTALL/bin:$PATH"
# Create symlink for system-wide access (optional but recommended)
sudo ln -sf "$DENO_INSTALL/bin/deno" /usr/local/bin/deno

# 3. (Optional) NVIDIA driver for CUDA acceleration.
#    The driver must be >= 525.60 for the CUDA 12 profile. A GPU alone is not
#    enough: install_local_transcribe.py --cuda verifies a working driver.
nvidia-smi      # Optional; should show GPU + driver version

# 4. Verify installations
deno --version  # Optional (YouTube downloads only)
nvidia-smi      # Optional (CUDA acceleration only)
```

### Installation Methods

#### Method 1: pipx from GitHub (Recommended)

**First-time installation:**
```bash
# CPU-only or GPU host. Core dependencies are CPU-only and never pull NVIDIA
# packages, so this succeeds on GPU-less Linux hosts.
pipx install git+https://github.com/draeician/local_transcribe.git

# Optional: add CUDA 12 acceleration (GPU hosts with a working driver)
pipx inject local-transcribe -r requirements-cuda.txt
#   or: ./install_local_transcribe.py --cuda

# Verify installation
lt doctor
```

**Updating an existing installation:**
```bash
# Update to latest version from GitHub (safe and idempotent for first installs too)
pipx install --force git+https://github.com/draeician/local_transcribe.git

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

# Install the package and CPU dependencies
pip install -e .

# Optional CUDA acceleration (GPU hosts only)
pip install -e '.[cuda]'

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

# Verify
lt doctor
```

### Quick Test

```bash
# Test single video transcription (YouTube). --device/--compute-type default to
# "auto": CUDA when usable, otherwise CPU int8. Pass explicit values to override.
lt transcribe "https://www.youtube.com/watch?v=DMQ_HcNSOAI" \
  --model medium \
  --device auto \
  --compute-type auto

# Test local audio (writes ./out/<stem-slug>.json)
lt transcribe "/path/to/sample.m4a" --output-dir ./out --device cuda --compute-type float16

# Test batch processing
lt batch --input inputfile.txt
```

---

## Troubleshooting: CUDA/cuDNN Issues (2025-10-21)

### Summary
**Problem**: Script crashes with `Unable to load libcudnn_ops.so` error.  
**Root Cause**: The CUDA profile (`nvidia-cudnn-cu12`, `nvidia-cublas-cu12`) is not installed, or the NVIDIA driver is too old for the CUDA 12 runtime.  
**Quick Fix**: run `lt doctor` to see whether CUDA is usable. If you want GPU acceleration, install the CUDA profile (`./install_local_transcribe.py --cuda`) on a host with a working driver >= 525.60. Otherwise, do nothing: CPU inference is a fully supported configuration and requires no NVIDIA packages.

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
On GPU hosts the CUDA profile (`nvidia-cudnn-cu12`, `nvidia-cublas-cu12`) installs its
libraries inside the venv at:

```
venv/lib/python3.12/site-packages/nvidia/cudnn/lib/
venv/lib/python3.12/site-packages/nvidia/cublas/lib/
```

If those packages are missing (the default CPU install) or the NVIDIA driver is
older than the CUDA 12 runtime requires (>= 525.60), CTranslate2 cannot load
cuDNN. `local-transcribe` no longer crashes in that situation: it detects that
CUDA is unusable and runs on CPU. GPU acceleration is entirely optional.

### Diagnosis Commands
```bash
# Whether CUDA is actually usable (authoritative; no torch involved)
lt doctor

# What the installed venv actually contains
pipx runpip local-transcribe list | grep -iE 'nvidia|torch'   # empty on CPU hosts
pipx runpip local-transcribe show nvidia-cudnn-cu12 nvidia-cublas-cu12

# Raw CTranslate2 probe (device count + supported CUDA compute types)
pipx runpython -c "import ctranslate2 as c; print(c.get_cuda_device_count(), c.get_supported_compute_types('cuda'))" 2>/dev/null \
  || python3 -c "import ctranslate2 as c; print(c.get_cuda_device_count(), c.get_supported_compute_types('cuda'))"
```

### Solution Options

**Option 1: Stay on CPU (Recommended unless you need speed)**

Nothing to do. A clean `pipx install` yields a working CPU backend
(`int8`); `lt doctor` reports it as healthy and exits 0.

**Option 2: Enable CUDA acceleration**

```bash
# GPU hosts with a working driver (>= 525.60)
./install_local_transcribe.py --cuda
#   equivalent: pipx inject local-transcribe -r requirements-cuda.txt

lt doctor            # confirms "ctranslate2 CUDA: ... usable"
```

If cuDNN still cannot be loaded after installing the CUDA profile, add the
wheel library directories to `LD_LIBRARY_PATH`:

```bash
export LD_LIBRARY_PATH=$(
  python3 -c "import nvidia.cublas, nvidia.cudnn, os; \
print(os.path.dirname(nvidia.cublas.__file__)+'/lib:'+os.path.dirname(nvidia.cudnn.__file__)+'/lib')"
):$LD_LIBRARY_PATH
```

### Current Status
- CPU backend: ✅ always available (`ctranslate2`, `faster-whisper`)
- GPU detected: reported by `lt doctor` (informational)
- CUDA usability: reported by `lt doctor`; falls back to CPU automatically
- PyTorch: not required — CTranslate2 is the inference backend

### Verification
```bash
lt doctor
```

This checks the required inference stack, the active backend (CPU or CUDA),
ffmpeg, and yt-dlp, and treats CUDA/GPU/Deno as optional components.

### Next Steps
If you want GPU inference, install the CUDA profile on a host with a compatible
driver. Otherwise use CPU — no further configuration is needed.

### FAQ

**Q: I'm seeing a warning about `/sys/class/drm/card5/device/vendor` - is this a problem?**  
A: No, this is harmless. It's just ONNX Runtime scanning for GPUs and encountering a virtual display device (EVDI/DisplayLink). Your actual NVIDIA GPU (card2) is detected and works fine.

**Q: The script works now, but why did this happen?**  
A: The NVIDIA cuDNN/cuBLAS wheels installed via pip go into your venv's `site-packages` directory. CTranslate2 needs to find them at runtime; the CUDA profile (`install_local_transcribe.py --cuda` or `pipx inject local-transcribe -r requirements-cuda.txt`) installs matching CUDA 12 wheels. Without them, `local-transcribe` simply uses CPU.

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

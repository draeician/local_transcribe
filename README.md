```bash
# System setup for Whisper + CUDA 13 + cuDNN 9 on Linux Mint 22

# 1. Install dependencies
sudo apt update
sudo apt install -y python3 python3-venv python3-pip ffmpeg

# 2. Install CUDA Toolkit
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

# 5. Create project virtual environment
cd ~/git/personal/local_transcribe
python3 -m venv venv
source venv/bin/activate

# 6. Install CUDA‑13 cuDNN 9 (Python wheels)
pip install -U nvidia-cublas-cu13 nvidia-cudnn-cu13
export LD_LIBRARY_PATH=$(python3 -c "import nvidia.cublas, nvidia.cudnn, os; print(os.path.dirname(nvidia.cublas.__file__)+':'+os.path.dirname(nvidia.cudnn.__file__))"):$LD_LIBRARY_PATH

# 7. Install PyTorch GPU + Faster Whisper
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124
pip install -U ctranslate2>=4.6.0 faster-whisper

# 8. (Optional) Symlink legacy cuDNN names if needed
sudo ln -s /usr/lib/x86_64-linux-gnu/libcudnn_ops.so.9 /usr/lib/x86_64-linux-gnu/libcudnn_ops_infer.so.8
sudo ln -s /usr/lib/x86_64-linux-gnu/libcudnn_cnn.so.9 /usr/lib/x86_64-linux-gnu/libcudnn_cnn_infer.so.8
sudo ldconfig

# 9. Verify GPU and cuDNN
python3 - <<'EOF'
import torch, ctranslate2
print("CUDA available:", torch.cuda.is_available())
print("Device:", torch.cuda.get_device_name(0))
print("cuDNN version:", torch.backends.cudnn.version())
print("CT2 version:", ctranslate2.__version__)
EOF

# 10. Run local transcribe pipeline
python local_transcribe.py \
  --url "https://www.youtube.com/watch?v=DMQ_HcNSOAI" \
  --model medium \
  --language auto \
  --device cuda \
  --compute-type float16 \
  --output-dir ./out
```

[1](https://github.com/SYSTRAN/faster-whisper)
[2](https://pypi.org/project/whisper-ctranslate2/)
[3](https://github.com/bungerr/faster-whisper-3)
[4](https://www.youtube.com/watch?v=eFmql0NqacU)
[5](https://www.youtube.com/watch?v=Kyc0AgMIBSU)
[6](https://opennmt.net/CTranslate2/installation.html)
[7](https://huggingface.co/Systran/faster-whisper-base)
[8](https://rocm.blogs.amd.com/artificial-intelligence/ctranslate2/README.html)
[9](https://www.reddit.com/r/LocalLLaMA/comments/1d1j31r/faster_whisper_server_an_openai_compatible_server/)
[10](https://huggingface.co/Systran/faster-whisper-large-v3)



wget https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2404/x86_64/cuda-keyring_1.1-1_all.deb
sudo dpkg -i cuda-keyring_1.1-1_all.deb
sudo apt update
sudo apt install -y cuda-toolkit
echo 'export PATH=/usr/local/cuda/bin:$PATH' >> ~/.bashrc
echo 'export LD_LIBRARY_PATH=/usr/local/cuda/lib64:$LD_LIBRARY_PATH' >> ~/.bashrc
source ~/.bashrc

nvcc --version

Install cuda
https://developer.nvidia.com/cudnn-downloads?target_os=Linux&target_arch=x86_64&Distribution=Ubuntu&target_version=24.04&target_type=deb_network

---

## Troubleshooting: CUDA/cuDNN Issues (2025-10-21)

### Summary
**Problem**: Script crashes with `Unable to load libcudnn_ops.so` error.  
**Root Cause**: cuDNN libraries in venv not in `LD_LIBRARY_PATH`.  
**Quick Fix**: Use `./run_transcribe.sh` instead of calling `python local_transcribe.py` directly.

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

**Option 1: Set LD_LIBRARY_PATH before running (Temporary)**
```bash
cd ~/git/personal/local_transcribe
source venv/bin/activate
export LD_LIBRARY_PATH=$(python3 -c "import nvidia.cublas, nvidia.cudnn, os; print(os.path.dirname(nvidia.cublas.__file__)+'/lib:'+os.path.dirname(nvidia.cudnn.__file__)+'/lib')"):$LD_LIBRARY_PATH
python local_transcribe.py --url <URL> --model medium --device cuda --compute-type float16 --output-dir ./out
```

**Option 2: Create a wrapper script (Recommended) ⭐**
A `run_transcribe.sh` wrapper script has been created for you with sensible defaults:
```bash
# Simple usage - just provide the URL!
./run_transcribe.sh --url <YOUTUBE_URL>

# Batch mode - process multiple URLs from a file
./run_transcribe.sh -i urls.txt

# Override defaults if needed
./run_transcribe.sh --url <YOUTUBE_URL> --model large-v3 --device cpu
./run_transcribe.sh -i urls.txt --model large-v3
```

**Default values:**
- `--model medium`
- `--device cuda`
- `--compute-type float16`
- `--output-dir /home/draeician/references/transcripts`

**Batch mode (`-i` flag):**
- Reads URLs from a file (one URL per line)
- Processes each URL in sequence
- Successfully completed URLs are logged to `finished.dat`
- Empty lines and lines starting with `#` are ignored
- Shows summary statistics at the end

This script automatically:
- Activates the venv
- Sets the correct LD_LIBRARY_PATH for cuDNN/cublas
- Applies sensible defaults for CUDA transcription
- Runs local_transcribe.py with your arguments

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
Run the test script to verify your setup:
```bash
./test_cuda_setup.sh
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
A: Yes, but it's not recommended. If you really want to, redirect stderr: `./run_transcribe.sh --url <URL> 2>/dev/null`. However, this will also hide legitimate error messages.

**Q: How do I use the simplified wrapper script?**  
A: Just run `./run_transcribe.sh --url <YOUTUBE_URL>` - it uses CUDA by default with the medium model. All other parameters (--model, --device, --compute-type, --output-dir) can be overridden if needed.

**Q: How do I process multiple videos at once?**  
A: Create a text file with one URL per line (e.g., `urls.txt`), then run `./run_transcribe.sh -i urls.txt`. Successfully completed URLs are logged to `finished.dat` so you can track progress. You can use `#` for comments and blank lines are ignored.

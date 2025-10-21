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
  --url "https://www.youtube.com/watch?v=VIDEO_ID" \
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

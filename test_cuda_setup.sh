#!/bin/bash
# Test script to verify CUDA/cuDNN setup
# Run this to check if your environment is properly configured

echo "================================================"
echo "CUDA/cuDNN Setup Verification Script"
echo "================================================"
echo ""

cd "$(dirname "$0")"
source venv/bin/activate

echo "1. GPU Detection (nvidia-smi)"
echo "---"
nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader 2>/dev/null || echo "ERROR: nvidia-smi failed"
echo ""

echo "2. CUDA Toolkit (nvcc)"
echo "---"
nvcc --version 2>/dev/null | grep "release" || echo "ERROR: nvcc not found"
echo ""

echo "3. Python CUDA Packages"
echo "---"
pip list 2>/dev/null | grep -E "nvidia-cudnn|nvidia-cublas|torch|ctranslate2|faster-whisper" | head -10
echo ""

echo "4. cuDNN Library Location"
echo "---"
python3 -c "import nvidia.cudnn, os; print('cuDNN path:', os.path.dirname(nvidia.cudnn.__file__)+'/lib')" 2>/dev/null || echo "ERROR: nvidia.cudnn package not found"
python3 -c "import nvidia.cublas, os; print('cublas path:', os.path.dirname(nvidia.cublas.__file__)+'/lib')" 2>/dev/null || echo "ERROR: nvidia.cublas package not found"
echo ""

echo "5. Current LD_LIBRARY_PATH"
echo "---"
echo "$LD_LIBRARY_PATH" | tr ':' '\n' | grep -E "cuda|cudnn" || echo "WARNING: No CUDA paths in LD_LIBRARY_PATH"
echo ""

echo "6. Test ctranslate2 WITHOUT proper LD_LIBRARY_PATH (Expected to FAIL)"
echo "---"
python3 -c "import ctranslate2; print('CT2 CUDA device count:', ctranslate2.get_cuda_device_count())" 2>&1 | head -3
echo ""

echo "7. Test ctranslate2 WITH proper LD_LIBRARY_PATH (Expected to WORK)"
echo "---"
export LD_LIBRARY_PATH=$(python3 -c "import nvidia.cublas, nvidia.cudnn, os; print(os.path.dirname(nvidia.cublas.__file__)+'/lib:'+os.path.dirname(nvidia.cudnn.__file__)+'/lib')" 2>/dev/null):$LD_LIBRARY_PATH
python3 -c "import ctranslate2; print('CT2 version:', ctranslate2.__version__); print('CUDA device count:', ctranslate2.get_cuda_device_count())" 2>&1
echo ""

echo "8. Test PyTorch CUDA"
echo "---"
python3 -c "import torch; print('CUDA available:', torch.cuda.is_available()); print('Device:', torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'N/A'); print('cuDNN version:', torch.backends.cudnn.version() if torch.cuda.is_available() else 'N/A')" 2>&1
echo ""

echo "================================================"
echo "Verification Complete"
echo "================================================"
echo ""
echo "✅ If tests 7 and 8 show CUDA devices, your setup is working!"
echo "⚠️  If test 6 crashed or test 7 failed, check the troubleshooting section in README.md"
echo ""
echo "To run transcriptions with CUDA, use:"
echo "  ./run_transcribe.sh --url <URL> --device cuda --model medium --output-dir ./out"
echo ""



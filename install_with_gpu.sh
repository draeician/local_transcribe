#!/bin/bash
# Installation script for local-transcribe with GPU support via pipx
# This script installs the package and PyTorch with CUDA support
#
# Usage:
#   ./install_with_gpu.sh                    # Install from local directory
#   ./install_with_gpu.sh <git-url>          # Install from git repository

set -e

GIT_URL="${1:-}"

echo "Installing local-transcribe with GPU support via pipx..."

# Install the package
if [ -n "$GIT_URL" ]; then
    echo "Installing from git repository: $GIT_URL"
    pipx install "$GIT_URL"
elif [ -d "." ] && [ -f "pyproject.toml" ]; then
    echo "Installing from local directory..."
    pipx install .
else
    echo "Error: Not in a valid local directory and no git URL provided"
    echo "Usage: $0 [git-url]"
    exit 1
fi

# Install PyTorch with CUDA support
echo "Installing PyTorch with CUDA support..."
pipx runpip local-transcribe install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124

echo ""
echo "✅ Installation complete! Verifying..."
lt doctor


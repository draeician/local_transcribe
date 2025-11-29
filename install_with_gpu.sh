#!/bin/bash
# Installation script for local-transcribe with GPU support via pipx
# This script installs the package and PyTorch with CUDA support
#
# Usage:
#   ./install_with_gpu.sh                    # Install from local directory
#   ./install_with_gpu.sh <git-url>          # Install from git repository
#
# If the package is already installed, it will just update the code
# without reinstalling PyTorch/CUDA dependencies.

set -e

GIT_URL="${1:-}"

# Check if local-transcribe is already installed
if pipx list | grep -q "local-transcribe"; then
    echo "local-transcribe is already installed. Updating package..."
    
    # Update the package without reinstalling dependencies
    if [ -n "$GIT_URL" ]; then
        echo "Updating from git repository: $GIT_URL"
        pipx install --force "$GIT_URL"
    elif [ -d "." ] && [ -f "pyproject.toml" ]; then
        echo "Updating from local directory..."
        pipx install --force .
    else
        echo "Error: Not in a valid local directory and no git URL provided"
        echo "Usage: $0 [git-url]"
        exit 1
    fi
    
    echo ""
    echo "✅ Update complete! Verifying..."
    lt doctor
else
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
    
    # Install PyTorch with CUDA support (only on fresh install)
    echo "Installing PyTorch with CUDA support..."
    pipx runpip local-transcribe install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124
    
    echo ""
    echo "✅ Installation complete! Verifying..."
    lt doctor
fi


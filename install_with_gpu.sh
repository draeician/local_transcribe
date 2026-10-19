#!/bin/bash
# Compatibility wrapper for the portable installer.
#
# The real installer is `install_local_transcribe.py`, which installs into a
# pipx-managed virtual environment and never runs pip against a system
# interpreter. All arguments are forwarded, e.g.:
#
#   ./install_with_gpu.sh                 # --auto (default)
#   ./install_with_gpu.sh --cpu
#   ./install_with_gpu.sh --cuda
#   ./install_with_gpu.sh git+https://github.com/draeician/local_transcribe.git
#
# Deno is NOT required for installation or for local audio transcription; it is
# only needed for YouTube downloads.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$SCRIPT_DIR/install_local_transcribe.py" "$@"

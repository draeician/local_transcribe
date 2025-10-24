#!/bin/bash
# Wrapper script to run local_transcribe.py with proper CUDA/cuDNN library paths
# This ensures ctranslate2 can find the cuDNN libraries installed in the venv
#
# Usage: 
#   ./run_transcribe.sh --url <YOUTUBE_URL> [options]
#   ./run_transcribe.sh -i <input_file> [options]
# 
# Defaults:
#   --model medium
#   --device cuda
#   --compute-type float16
#   --output-dir /home/draeician/references/transcripts

cd "$(dirname "$0")"
source venv/bin/activate

# Set LD_LIBRARY_PATH to include nvidia-cudnn and nvidia-cublas from venv
export LD_LIBRARY_PATH=$(python3 -c "import nvidia.cublas, nvidia.cudnn, os; print(os.path.dirname(nvidia.cublas.__file__)+'/lib:'+os.path.dirname(nvidia.cudnn.__file__)+'/lib')" 2>/dev/null):$LD_LIBRARY_PATH

# Default values
MODEL="medium"
DEVICE="cuda"
COMPUTE_TYPE="float16"
OUTPUT_DIR="/home/draeician/references/transcripts"
INPUT_FILE=""

# Parse arguments and override defaults
ARGS=()
while [[ $# -gt 0 ]]; do
  case $1 in
    -i)
      INPUT_FILE="$2"
      shift 2
      ;;
    --model)
      MODEL="$2"
      shift 2
      ;;
    --device)
      DEVICE="$2"
      shift 2
      ;;
    --compute-type)
      COMPUTE_TYPE="$2"
      shift 2
      ;;
    --output-dir)
      OUTPUT_DIR="$2"
      shift 2
      ;;
    *)
      # Pass through all other arguments
      ARGS+=("$1")
      shift
      ;;
  esac
done

# Function to process a single URL
process_url() {
  local url="$1"
  local finished_file="finished.dat"
  
  echo "=========================================="
  echo "Processing: $url"
  echo "=========================================="
  
  if python local_transcribe.py \
    --url "$url" \
    --model "$MODEL" \
    --device "$DEVICE" \
    --compute-type "$COMPUTE_TYPE" \
    --output-dir "$OUTPUT_DIR" \
    "${ARGS[@]}"; then
    
    # Success - log to finished.dat
    echo "$url" >> "$finished_file"
    echo "✅ SUCCESS: $url"
    return 0
  else
    # Failure
    echo "❌ FAILED: $url"
    return 1
  fi
}

# Main execution logic
if [[ -n "$INPUT_FILE" ]]; then
  # Batch mode: process URLs from file
  if [[ ! -f "$INPUT_FILE" ]]; then
    echo "Error: Input file '$INPUT_FILE' not found" >&2
    exit 1
  fi
  
  echo "Batch mode: processing URLs from $INPUT_FILE"
  echo "Finished URLs will be logged to: finished.dat"
  echo ""
  
  total=0
  success=0
  failed=0
  
  while IFS= read -r url || [[ -n "$url" ]]; do
    # Skip empty lines and comments
    [[ -z "$url" || "$url" =~ ^[[:space:]]*# ]] && continue
    
    total=$((total + 1))
    
    if process_url "$url"; then
      success=$((success + 1))
    else
      failed=$((failed + 1))
    fi
    
    echo ""
  done < "$INPUT_FILE"
  
  echo "=========================================="
  echo "Batch processing complete"
  echo "Total: $total | Success: $success | Failed: $failed"
  echo "=========================================="
  
else
  # Single URL mode: run once with passed arguments
  python local_transcribe.py \
    --model "$MODEL" \
    --device "$DEVICE" \
    --compute-type "$COMPUTE_TYPE" \
    --output-dir "$OUTPUT_DIR" \
    "${ARGS[@]}"
fi


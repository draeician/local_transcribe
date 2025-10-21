#!/usr/bin/env bash
set -euo pipefail

URL="${1:-https://www.youtube.com/watch?v=4Qj5y0Vtdds}"
OUTDIR="${2:-./out}"

# Device defaults to CPU to avoid cuDNN issues; override by exporting DEVICE=cuda if desired
DEVICE="${DEVICE:-cpu}"
COMPUTE_TYPE="${COMPUTE_TYPE:-int8}"

echo "=== yt-dlp probe (CLI formats) ==="
yt-dlp -F "$URL" | sed -n '1,60p'
echo

echo "=== running local_transcribe.py (CPU by default) ==="
python local_transcribe.py \
  --url "$URL" \
  --model medium \
  --language auto \
  --device "$DEVICE" \
  --compute-type "$COMPUTE_TYPE" \
  --output-dir "$OUTDIR" \
  ${COOKIES_FROM_BROWSER:+--cookies-from-browser "$COOKIES_FROM_BROWSER"} \
  ${COOKIES_FILE:+--cookies-file "$COOKIES_FILE"}

echo
echo "=== result ==="
ls -lh "$OUTDIR"
echo
jq . "$OUTDIR"/*.json | sed -n '1,80p'


# Quick Reference Guide

## 🎯 Most Common Commands

### First Time Setup
```bash
# 1. Install the package
pip install -e .

# 2. Clean your input file
lt reconcile

# 3. Start batch transcription
lt batch --input inputfile_clean.txt --device cuda --compute-type float16
```

### Resume After Interruption
```bash
# Just resume! It remembers everything
lt batch --resume
```

### Check Status Anytime
```bash
# Full reconciliation report
lt reconcile

# Check current batch status
lt status

# Or use jq for detailed queries
jq '[.[] | .status] | group_by(.) | map({status: .[0], count: length})' batch_status.json
```

### Handle Failures
```bash
# Generate failure report
lt report

# See what failed and why
cat logs/failed_videos.txt

# Extract failed URLs for retry
jq -r '.[] | select(.status=="failed") | .url' batch_status.json > failed.txt

# Retry with more attempts
lt batch --input failed.txt --max-retries 5
```

### Single file (YouTube or local audio)

```bash
# HTTPS YouTube URL — uses yt-dlp + Deno when downloading
lt transcribe "https://www.youtube.com/watch?v=VIDEO_ID" --output-dir ./out

# Existing audio file — no download; output is ./out/<slug-from-stem>.json (ffmpeg recommended for m4a etc.)
lt transcribe ./recording.m4a --output-dir ./out
```

---

## 📁 File Structure

```
local_transcribe/
├── src/local_transcribe/        # Package source code
│   ├── cli.py                   # Unified CLI (lt command)
│   ├── services/                # Core services
│   └── utils/                   # Utilities
│
├── inputfile.txt                # Your original URL list
├── inputfile_clean.txt          # Generated: Deduplicated, valid URLs
├── pending.txt                  # Generated: URLs without transcripts
├── finished.dat                 # Legacy: Completed URLs log
├── batch_status.json            # NEW: Resume state file (IMPORTANT!)
│
├── out/                         # Transcript output directory
│   └── VIDEO_ID.json            # Transcript files
│
└── logs/                        # NEW: Detailed logging
    ├── batch_transcribe_*.log   # Per-run detailed logs
    └── failed_videos.txt        # Failure report
```

---

## 🔄 Typical Workflows

### Workflow 1: Fresh Start
```bash
# Clean and prepare
lt reconcile

# Start processing (uses cleaned input)
lt batch --input inputfile_clean.txt --device cuda --compute-type float16

# Monitor in another terminal
tail -f logs/batch_transcribe_*.log
```

### Workflow 2: Resume Interrupted
```bash
# Your previous run was interrupted...
# Just resume!
lt batch --resume
```

### Workflow 3: Process Only New Videos
```bash
# reconcile creates pending.txt with unprocessed videos
lt reconcile

# Process just the pending ones
lt batch --input pending.txt
```

### Workflow 4: Retry Failures
```bash
# After a batch run, some videos failed
lt report  # Generate failure report

# Extract URLs
jq -r '.[] | select(.status=="failed") | .url' batch_status.json > retry.txt

# Retry with more attempts or different settings
lt batch --input retry.txt --max-retries 5 --device cpu
```

---

## 🎛️ Common Options

```bash
# CPU mode (slower but more stable)
lt batch --input INPUT.txt --device cpu --compute-type int8

# High quality model
lt batch --input INPUT.txt --model large-v3

# More retries for flaky network
lt batch --input INPUT.txt --max-retries 5

# Custom output location
lt batch --input INPUT.txt --output-dir /path/to/transcripts

# Single video transcription
lt transcribe "https://youtube.com/watch?v=VIDEO_ID" --model medium --device cuda

# Environment diagnostics
lt doctor
```

---

## 🔍 Status Checks

### Quick Status Count
```bash
jq '[.[] | .status] | group_by(.) | map({status: .[0], count: length})' batch_status.json
```

Output:
```json
[
  {"status": "completed", "count": 450},
  {"status": "failed", "count": 12},
  {"status": "pending", "count": 60}
]
```

### List Pending Videos
```bash
jq -r '.[] | select(.status=="pending") | .url' batch_status.json
```

### List Completed Videos
```bash
jq -r '.[] | select(.status=="completed") | .url' batch_status.json
```

### List Failed with Errors
```bash
jq -r '.[] | select(.status=="failed") | "\(.video_id): \(.error_message)"' batch_status.json
```

### Check Specific Video
```bash
jq '.["VIDEO_ID"]' batch_status.json
```

---

## 🚨 Troubleshooting

### "No module named 'dataclasses'"
You're using Python < 3.7. Upgrade Python or use:
```bash
pip install dataclasses
```

### Resume doesn't work
Check that `batch_status.json` exists:
```bash
ls -lh batch_status.json
lt status  # Check status
```

### Want to start completely fresh
```bash
rm batch_status.json
lt batch --input inputfile.txt
```

### Check what's actually completed
```bash
# Run reconciliation
lt reconcile

# Shows truth: claimed vs. actual files
```

### Video keeps failing
Check the error in logs:
```bash
grep "VIDEO_ID" logs/batch_transcribe_*.log
```

Or check status:
```bash
jq '.["VIDEO_ID"]' batch_status.json
```

---

## 📊 Monitor Progress

### Option 1: Watch Log File
```bash
tail -f logs/batch_transcribe_*.log
```

### Option 2: Watch Status (refreshes every 5 seconds)
```bash
watch -n 5 'jq "[.[] | .status] | group_by(.) | map({s: .[0], n: length})" batch_status.json'
```

### Option 3: Simple Progress
```bash
# Count completed
jq '[.[] | select(.status=="completed")] | length' batch_status.json

# Total videos
jq '. | length' batch_status.json
```

---

## 🎯 Pro Tips

### 1. Always reconcile first
```bash
lt reconcile  # Shows true state
```

### 2. Use clean input files
```bash
# lt reconcile generates these for you
lt batch --input inputfile_clean.txt
```

### 3. Monitor in separate terminal
```bash
# Terminal 1: Run batch
lt batch --input inputfile.txt

# Terminal 2: Watch progress
tail -f logs/batch_transcribe_*.log
```

### 4. Backup before big changes
```bash
cp batch_status.json batch_status.json.backup
cp finished.dat finished.dat.backup
```

### 5. Check disk space
```bash
# Each transcript is ~10-100KB
du -sh out/
```

---

## 🔗 Related Commands

### View a transcript
```bash
cat out/VIDEO_ID.json | jq .
```

### Count transcripts
```bash
ls out/*.json | wc -l
```

### Search transcripts
```bash
grep -r "search term" out/*.json
```

### Find largest transcripts
```bash
du -h out/*.json | sort -h | tail -10
```

---

## 📝 Important Files

| File | Purpose | Can Delete? |
|------|---------|------------|
| `batch_status.json` | Resume state | ⚠️ NO (loses resume) |
| `finished.dat` | Legacy ledger | ✅ Yes (regenerated) |
| `logs/*.log` | Audit trail | ✅ Yes (old runs) |
| `inputfile_clean.txt` | Clean URLs | ✅ Yes (regenerate) |
| `pending.txt` | Unprocessed URLs | ✅ Yes (regenerate) |
| `out/*.json` | **TRANSCRIPTS** | ⚠️ NO (your data!) |

---

## 🚀 Quick Start Checklist

- [ ] Install: `pip install -e .`
- [ ] Run `lt reconcile` to understand current state
- [ ] Review generated files (inputfile_clean.txt, pending.txt)
- [ ] Start with: `lt batch --input inputfile_clean.txt`
- [ ] If interrupted: `lt batch --resume`
- [ ] Check failures: `lt report` or `cat logs/failed_videos.txt`
- [ ] Verify completion: `lt reconcile`

---

## 📚 Full Documentation

- **This file**: Quick commands
- **START_HERE.md**: Quick installation guide
- **README.md**: Complete setup and troubleshooting
- **BATCH_TRANSCRIBE_README.md**: Complete guide
- **UPGRADE_SUMMARY.md**: Why upgrade from shell script
- **lt** or **lt --help**: Show help screen with all available commands
- **lt --version**: Show version information

---

**Remember:** The new system checks **actual files**, not just finished.dat. If a file is missing, it will re-transcribe regardless of what finished.dat says. This is intentional and correct behavior!


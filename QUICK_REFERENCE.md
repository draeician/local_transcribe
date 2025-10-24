# Quick Reference Guide

## 🎯 Most Common Commands

### First Time Setup
```bash
# 1. Clean your input file
python reconcile.py

# 2. Start batch transcription
python batch_transcribe.py --input inputfile_clean.txt
```

### Resume After Interruption
```bash
# Just resume! It remembers everything
python batch_transcribe.py --resume
```

### Check Status Anytime
```bash
# Full reconciliation report
python reconcile.py

# Check current batch status
cat batch_status.json | jq -r '.[] | [.status, .video_id] | @tsv' | sort | uniq -c

# Count by status
jq '[.[] | .status] | group_by(.) | map({status: .[0], count: length})' batch_status.json
```

### Handle Failures
```bash
# See what failed and why
cat logs/failed_videos.txt

# Extract failed URLs for retry
jq -r '.[] | select(.status=="failed") | .url' batch_status.json > failed.txt

# Retry with more attempts
python batch_transcribe.py --input failed.txt --max-retries 5
```

---

## 📁 File Structure

```
local_transcribe/
├── batch_transcribe.py          # NEW: Robust batch processor
├── reconcile.py                 # NEW: Three-way status checker
├── local_transcribe.py          # Core transcription (unchanged)
├── run_transcribe.sh            # OLD: Legacy shell script
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
python reconcile.py

# Start processing (uses cleaned input)
python batch_transcribe.py --input inputfile_clean.txt

# Monitor in another terminal
tail -f logs/batch_transcribe_*.log
```

### Workflow 2: Resume Interrupted
```bash
# Your previous run was interrupted...
# Just resume!
python batch_transcribe.py --resume
```

### Workflow 3: Process Only New Videos
```bash
# reconcile.py creates pending.txt with unprocessed videos
python reconcile.py

# Process just the pending ones
python batch_transcribe.py --input pending.txt
```

### Workflow 4: Retry Failures
```bash
# After a batch run, some videos failed
cat logs/failed_videos.txt  # Review failures

# Extract URLs
jq -r '.[] | select(.status=="failed") | .url' batch_status.json > retry.txt

# Retry with more attempts or different settings
python batch_transcribe.py --input retry.txt --max-retries 5 --device cpu
```

---

## 🎛️ Common Options

```bash
# CPU mode (slower but more stable)
python batch_transcribe.py --input INPUT.txt --device cpu --compute-type int8

# High quality model
python batch_transcribe.py --input INPUT.txt --model large-v3

# More retries for flaky network
python batch_transcribe.py --input INPUT.txt --max-retries 5

# Custom output location
python batch_transcribe.py --input INPUT.txt --output-dir /path/to/transcripts
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
```

### Want to start completely fresh
```bash
rm batch_status.json
python batch_transcribe.py --input inputfile.txt
```

### Check what's actually completed
```bash
# Run reconciliation
python reconcile.py

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
python reconcile.py  # Shows true state
```

### 2. Use clean input files
```bash
# reconcile.py generates these for you
python batch_transcribe.py --input inputfile_clean.txt
```

### 3. Monitor in separate terminal
```bash
# Terminal 1: Run batch
python batch_transcribe.py --input inputfile.txt

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

- [ ] Run `python reconcile.py` to understand current state
- [ ] Review generated files (inputfile_clean.txt, pending.txt)
- [ ] Start with: `python batch_transcribe.py --input inputfile_clean.txt`
- [ ] If interrupted: `python batch_transcribe.py --resume`
- [ ] Check failures: `cat logs/failed_videos.txt`
- [ ] Verify completion: `python reconcile.py`

---

## 📚 Full Documentation

- **This file**: Quick commands
- **BATCH_TRANSCRIBE_README.md**: Complete guide
- **UPGRADE_SUMMARY.md**: Why upgrade from shell script
- **reconcile.py --help**: Status checking options
- **batch_transcribe.py --help**: All batch options

---

**Remember:** The new system checks **actual files**, not just finished.dat. If a file is missing, it will re-transcribe regardless of what finished.dat says. This is intentional and correct behavior!


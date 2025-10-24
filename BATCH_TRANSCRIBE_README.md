# Batch Transcription Application

A **robust, production-ready** batch transcription system that replaces the fragile shell script with proper application features.

## 🎯 What This Solves

### Problems with `run_transcribe.sh`:
- ❌ No resume capability
- ❌ No failure detection (only exit codes)
- ❌ Minimal logging
- ❌ No status tracking
- ❌ Race conditions with finished.dat
- ❌ Restarts from beginning if interrupted

### Solutions with `batch_transcribe.py`:
- ✅ **Resume capability** - Picks up exactly where you left off
- ✅ **File verification** - Checks actual output JSON files exist and are valid
- ✅ **Detailed logging** - Per-batch logs + failed video reports
- ✅ **Status tracking** - `batch_status.json` tracks every video's state
- ✅ **Failure handling** - Retry logic with configurable attempts
- ✅ **Progress reporting** - Real-time statistics
- ✅ **Duplicate detection** - Automatically skips already-processed videos
- ✅ **Graceful interruption** - Ctrl+C saves progress cleanly

## 🚀 Quick Start

### Basic Usage

```bash
# First run - process all videos
python batch_transcribe.py --input inputfile.txt

# Interrupted? Just resume!
python batch_transcribe.py --resume
```

### Common Options

```bash
# Use CPU instead of CUDA
python batch_transcribe.py --input inputfile.txt --device cpu

# Use different model
python batch_transcribe.py --input inputfile.txt --model large-v3

# Custom output directory
python batch_transcribe.py --input inputfile.txt --output-dir /path/to/transcripts

# More retries for flaky videos
python batch_transcribe.py --input inputfile.txt --max-retries 5
```

## 📋 Full Options

```
--input, -i           Input file with URLs (default: inputfile.txt)
--output-dir          Output directory for transcripts (default: out/)
--model               Whisper model: tiny|base|small|medium|large-v3 (default: medium)
--device              Device: cpu|cuda|auto (default: cuda)
--compute-type        Precision: int8|float16|float32 (default: float16)
--max-retries         Retry attempts per video (default: 2)
--log-dir             Log directory (default: logs/)
--resume              Resume from batch_status.json
```

## 📊 What Gets Created

### Status File: `batch_status.json`
Tracks every video's state for resume capability:
```json
{
  "VIDEO_ID": {
    "url": "https://youtube.com/...",
    "video_id": "VIDEO_ID",
    "status": "completed|pending|failed|processing",
    "attempts": 1,
    "last_attempt": "2025-01-15T10:30:00",
    "error_message": null,
    "output_file": "out/VIDEO_ID.json",
    "duration_seconds": 45.2
  }
}
```

### Log Files: `logs/`
- `batch_transcribe_YYYYMMDD_HHMMSS.log` - Detailed log for each run
- `failed_videos.txt` - Report of all failures with error messages

### Output: `out/`
- `VIDEO_ID.json` - Transcript files (validated before marking complete)

### Ledger: `finished.dat`
- Updated with all completed videos (backwards compatible)

## 🔄 Resume Capability

The application automatically resumes from interruption:

```bash
# Start processing
python batch_transcribe.py --input inputfile.txt
# ... processing ...
# Press Ctrl+C

# Later, resume exactly where you left off
python batch_transcribe.py --resume
```

**What gets preserved:**
- ✅ Completed videos (verified by checking output files)
- ✅ Failed videos (with attempt counts)
- ✅ Pending videos (not yet attempted)
- ✅ Error messages from failures

## 🔍 Checking Status

### Using reconcile.py

```bash
# See complete status including actual files
python reconcile.py
```

This shows:
- Videos with transcripts vs. marked finished
- Missing transcript files
- Orphaned transcripts
- Duplicates in input

### Manual Check

```bash
# See current batch status
cat batch_status.json | jq '.[] | select(.status=="failed")'

# Count by status
jq '[.[] | .status] | group_by(.) | map({status: .[0], count: length})' batch_status.json
```

## 🛡️ Error Handling

### Automatic Retry
Videos are retried up to `--max-retries` times (default: 2):
1. First attempt fails → status: pending (will retry)
2. Second attempt fails → status: pending (will retry)  
3. Third attempt fails → status: failed (no more retries)

### Timeout Protection
Each video has a 30-minute timeout to prevent hanging.

### Failure Report
After completion, check `logs/failed_videos.txt` for all failures with error messages.

## 📈 Example Run

```
$ python batch_transcribe.py --input inputfile_clean.txt

Logging to: logs/batch_transcribe_20251024_143022.log
Loaded 521 unique URLs from inputfile_clean.txt
Total videos: 521
Already completed: 2
To process: 519

[1/521] Processing: VIDEO_ID_1 (attempt 1)
✅ SUCCESS: VIDEO_ID_1 (45.2s)

[2/521] Processing: VIDEO_ID_2 (attempt 1)
❌ FAILED: VIDEO_ID_2 - Network error
   Will retry (attempt 2/2)

... (continues) ...

^C
⚠️  Interrupted by user - saving progress...
Progress saved. Run with --resume to continue.
```

```
$ python batch_transcribe.py --resume

Loaded status for 521 videos from previous run
Resuming from previous run
Total videos: 521
Already completed: 50
To process: 470

[51/521] Processing: VIDEO_ID_51 (attempt 1)
...
```

## 🔄 Migration from Shell Script

### Old Way:
```bash
./run_transcribe.sh -i inputfile.txt --device cuda
# If interrupted: START OVER FROM BEGINNING
```

### New Way:
```bash
python batch_transcribe.py --input inputfile.txt --device cuda
# If interrupted: python batch_transcribe.py --resume
```

## 🎯 Best Practices

### 1. Start with Clean Input
```bash
# Use reconcile.py to generate clean files
python reconcile.py

# Use the cleaned input
python batch_transcribe.py --input inputfile_clean.txt
```

### 2. Monitor Progress
```bash
# In another terminal, watch the log
tail -f logs/batch_transcribe_*.log

# Or watch status file
watch -n 10 'jq "[.[] | .status] | group_by(.) | map({s: .[0], n: length})" batch_status.json'
```

### 3. Handle Failures
```bash
# After completion, check failures
cat logs/failed_videos.txt

# Extract failed URLs to retry separately
jq -r '.[] | select(.status=="failed") | .url' batch_status.json > failed_urls.txt

# Retry just the failures
python batch_transcribe.py --input failed_urls.txt --max-retries 5
```

### 4. Clean Restart
```bash
# To start completely fresh (ignoring previous status)
rm batch_status.json
python batch_transcribe.py --input inputfile.txt
```

## 🆚 Comparison

| Feature | Shell Script | Python App |
|---------|-------------|------------|
| Resume capability | ❌ | ✅ |
| File verification | ❌ | ✅ |
| Detailed logging | ❌ | ✅ |
| Status tracking | ❌ | ✅ |
| Retry logic | ❌ | ✅ |
| Progress reporting | Basic | Detailed |
| Failure reports | ❌ | ✅ |
| Duplicate detection | ❌ | ✅ |
| Timeout handling | ❌ | ✅ |
| Graceful interruption | ❌ | ✅ |

## 🚨 Important Notes

1. **The Python app checks actual output files** - It won't skip a video just because it's in `finished.dat`
2. **Status file is critical for resume** - Don't delete `batch_status.json` between runs
3. **Logs are timestamped** - Each run creates a new log file
4. **finished.dat is updated** - Backwards compatible with existing tools

## 🔧 Troubleshooting

### "No such file: local_transcribe.py"
Run from the project directory:
```bash
cd /home/draeician/git/personal/local_transcribe
python batch_transcribe.py --input inputfile.txt
```

### Resume doesn't skip completed videos
The app checks actual output files. If files are missing, it will re-transcribe regardless of status file.

### Want to force re-transcribe everything
```bash
rm batch_status.json
rm out/*.json
python batch_transcribe.py --input inputfile.txt
```

### Check what's actually completed
```bash
python reconcile.py  # Shows truth: status vs actual files
```

## 📚 Related Tools

- `reconcile.py` - Three-way reconciliation (input, finished.dat, actual files)
- `local_transcribe.py` - Core transcription engine (called by batch_transcribe.py)
- `run_transcribe.sh` - Legacy shell script (deprecated)

---

**Recommendation:** Use `batch_transcribe.py` for all batch operations. It's production-ready and handles edge cases the shell script misses.


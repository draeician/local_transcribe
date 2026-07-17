# Background Transcription Queue Feature Specification

**Project:** `local-transcribe`
**Related project:** `ref-cli`
**Document type:** Development specification
**Status:** Proposed
**Target platform:** Linux, including Linux Mint 22
**Primary storage:** NFS-mounted transcript directory
**Execution model:** One background transcription worker per environment

---

## 1. Purpose

Extend `local-transcribe` with a durable file-based transcription queue and a background worker that processes exactly one transcription job at a time.

The system must support all of the following without creating competing download or transcription processes:

```bash
lt transcribe <source>
lt batch --input <file>
ref <youtube-url>
```

All YouTube downloads must pass through the same worker and obey the same shared download pacing and rate-limit state.

The queue and completed transcripts reside on an NFS-mounted filesystem. Therefore, the design must remain file-based and must not depend on SQLite, SQLite WAL, local-only database locks, or a single mutable queue document.

---

## 2. Current System

### 2.1 `ref-cli`

`ref-cli` currently:

* Attempts to retrieve YouTube transcripts through `youtube-transcript-api`.
* Detects blocked transcript requests.
* Adds affected YouTube URLs to `transcript-pending.md`.
* Prevents duplicate URLs from being appended.
* Avoids queueing a video when a valid transcript already exists.
  The current queue operation reads the pending file, checks for an existing URL, and appends a new line. This is acceptable for one process but is not safe when a worker may simultaneously rewrite or consume the same file.

### 2.2 `local-transcribe`

`local-transcribe` already provides:

* `lt transcribe`
* `lt batch`
* Status tracking
* Retry handling
* Transcript validation
* A persisted rate-limit counter
* Downloading through `yt-dlp`
* Local transcription through `faster-whisper`

The current batch pipeline processes jobs sequentially and writes status to `batch_status.json`. It creates its own rate limiter under the configured output directory.
The current `lt transcribe` command directly calls `transcribe_url()` or `transcribe_local_file()`. It does not submit work to `BatchPipeline`, so it does not share the batch pipeline’s rate-limit state or execution serialization.

---

## 3. Design Principles

The implementation must follow these rules:

1. **Files remain authoritative.**
2. **The queue must work on an NFS-mounted filesystem.**
3. **Use one file per job.**
4. **Never use one shared mutable pending file as the authoritative queue.**
5. **Only one worker may execute transcription jobs.**
6. **CLI commands submit work; the worker executes work.**
7. **`lt transcribe` must not bypass the worker.**
8. **Final transcript files may reside on NFS.**
9. **Temporary downloaded media should remain on local storage.**
10. **State transitions must use same-filesystem atomic rename operations.**
11. **A completed job is valid only after its transcript JSON passes validation.**
12. **Existing files such as `transcript-pending.md`, `finished.dat`, and `batch_status.json` are compatibility artifacts, not queue authorities.**

---

## 4. Queue Location Resolution

All queue producers, consumers, status commands, migration commands, and worker processes must use one shared queue resolver.

### 4.1 Candidate locations

Queue locations must be checked in this exact order:

1. `~/references/transcripts/transcription-queue`
2. `/opt/md2/music/youtube/transcripts/transcription-queue`

### 4.2 Resolution behavior

The resolver must:

1. Expand `~` using the home directory of the account executing the command.
2. Return the first candidate that exists as a directory.
3. Never merge jobs from both locations.
4. Never silently select the second location if the first exists but is unreadable or malformed.
5. Report a clear error if the selected path cannot be accessed.
6. When initialization is explicitly requested and neither path exists, create:

```text
~/references/transcripts/transcription-queue
```

7. Log or display the selected queue directory at worker startup and in status output.

### 4.3 Required resolver interface

Create a central module such as:

```text
src/local_transcribe/services/queue_paths.py
```

Recommended interface:

```python
from pathlib import Path


QUEUE_CANDIDATES = (
    Path.home() / "references" / "transcripts" / "transcription-queue",
    Path("/opt/md2/music/youtube/transcripts/transcription-queue"),
)


class QueuePathError(RuntimeError):
    pass


def resolve_queue_dir(*, create: bool = False) -> Path:
    """Resolve the authoritative transcription queue directory."""


def initialize_queue_layout(queue_dir: Path) -> None:
    """Create and validate all required queue directories."""
```

Every component must import this resolver. No command may independently recreate the resolution logic.

---

## 5. Queue Directory Structure

The resolved queue must use this structure:

```text
transcription-queue/
├── pending/
├── processing/
├── retry/
├── completed/
├── failed/
├── cancelled/
├── tmp/
└── worker/
    ├── rate-limit.json
    └── state.json
```

### 5.1 Directory purposes

| Directory     | Purpose                                                    |
| ------------- | ---------------------------------------------------------- |
| `pending/`    | Jobs eligible for immediate processing                     |
| `processing/` | The job currently claimed by the worker                    |
| `retry/`      | Jobs waiting until a future retry time                     |
| `completed/`  | Successfully completed job records                         |
| `failed/`     | Permanently failed or retry-exhausted jobs                 |
| `cancelled/`  | Jobs explicitly cancelled by the user                      |
| `tmp/`        | Temporary queue metadata files used during atomic creation |
| `worker/`     | Worker state and shared rate-limit state                   |

Only the worker may move files between processing states.

Producers may only create jobs under `pending/` through the queue service.

---

## 6. Job Identity and Deduplication

### 6.1 YouTube jobs

The canonical job key for a YouTube source is the YouTube video ID.

Example:

```text
pending/abcd1234567.json
```

Different URL forms for the same video must resolve to the same job:

```text
https://www.youtube.com/watch?v=abcd1234567
https://youtu.be/abcd1234567
https://www.youtube.com/watch?v=abcd1234567&feature=shared
```

### 6.2 Local-file jobs

Local audio files must use a stable source key derived from:

```text
canonical absolute path
file size
modification timestamp
```

The resulting key should be hashed to produce a filesystem-safe filename.

Example:

```text
pending/local-1c7d80b50d12e37c.json
```

### 6.3 Duplicate rules

Before creating a job, check:

```text
pending/
processing/
retry/
completed/
failed/
cancelled/
```

Behavior:

* Existing valid transcript: return `already_completed`.
* Existing active job: return the existing job.
* Existing completed job with valid transcript: return the existing result.
* Existing failed job: require `--retry` or `--force`.
* Existing cancelled job: require `--force`.
* `--force`: create a new execution generation or reset the existing job safely.

A duplicate submission must never create parallel work for the same source.

---

## 7. Job File Format

Each job is represented by one JSON document.

Example:

```json
{
  "schema_version": 1,
  "job_id": "abcd1234567",
  "source": "https://www.youtube.com/watch?v=abcd1234567",
  "source_type": "youtube",
  "source_key": "abcd1234567",
  "origin": "ref",
  "priority": 20,
  "status": "pending",
  "created_at": "2026-07-17T08:15:00-05:00",
  "updated_at": "2026-07-17T08:15:00-05:00",
  "available_at": "2026-07-17T08:15:00-05:00",
  "started_at": null,
  "completed_at": null,
  "attempts": 0,
  "max_attempts": 3,
  "worker": null,
  "output_path": null,
  "error": null,
  "options": {
    "model": "medium",
    "device": "cuda",
    "compute_type": "float16",
    "language": null,
    "keep_audio": false,
    "cookies_file": null,
    "cookies_from_browser": null,
    "limit_rate": null,
    "sleep_interval_requests": null
  }
}
```

### 7.1 Required fields

| Field            | Requirement                                                      |
| ---------------- | ---------------------------------------------------------------- |
| `schema_version` | Queue schema version                                             |
| `job_id`         | Filesystem-safe unique job identifier                            |
| `source`         | Original source URL or path                                      |
| `source_type`    | `youtube` or `local_file`                                        |
| `source_key`     | Canonical deduplication key                                      |
| `origin`         | Producer such as `ref`, `lt-transcribe`, `lt-batch`, or `import` |
| `priority`       | Numeric processing priority                                      |
| `status`         | Current queue state                                              |
| `created_at`     | ISO 8601 timestamp                                               |
| `updated_at`     | ISO 8601 timestamp                                               |
| `available_at`   | Earliest time the worker may claim the job                       |
| `attempts`       | Number of execution attempts                                     |
| `max_attempts`   | Maximum allowed execution attempts                               |
| `options`        | Complete immutable transcription options                         |

### 7.2 Error object

Failure information must be structured:

```json
{
  "category": "rate_limited",
  "message": "HTTP 429 returned by yt-dlp",
  "retryable": true,
  "occurred_at": "2026-07-17T08:40:00-05:00",
  "next_retry_at": "2026-07-17T09:00:00-05:00"
}
```

Recommended categories:

```text
rate_limited
forbidden
authentication_required
video_unavailable
download_failed
transcription_failed
output_validation_failed
invalid_source
worker_interrupted
internal_error
```

---

## 8. Safe File Operations

### 8.1 Enqueue operation

A producer must not write directly to the final `pending/` path.

Required sequence:

1. Construct and validate the job object.
2. Write it under `tmp/` using a unique name:

```text
tmp/abcd1234567.<hostname>.<pid>.<random>.json
```

3. Flush the file.
4. Call `fsync()` on the file where supported.
5. Rename it into:

```text
pending/abcd1234567.json
```

6. Treat an existing destination as a duplicate submission.
7. Remove the temporary file on failure.

### 8.2 State transitions

State changes must use rename within the same resolved queue filesystem:

```text
pending/job.json   -> processing/job.json
processing/job.json -> completed/job.json
processing/job.json -> retry/job.json
processing/job.json -> failed/job.json
pending/job.json    -> cancelled/job.json
```

Do not copy and delete as the normal transition mechanism.

### 8.3 Metadata updates

When job contents must change:

1. Write a replacement document under `tmp/`.
2. Flush and validate it.
3. Replace the current state file atomically.
4. Keep the file in the same state directory unless the state itself is changing.

### 8.4 NFS assumption

The queue requires the NFS server and mount configuration to provide reliable same-directory or same-filesystem rename semantics.

The implementation must not depend on:

* SQLite locking
* `flock()` across different clients
* POSIX advisory locks being consistently honored by NFS
* Atomic append to a shared queue file

---

## 9. Worker Architecture

Create:

```text
src/local_transcribe/services/worker.py
```

The worker is the only component allowed to:

* Claim jobs.
* Run `yt-dlp`.
* Run `faster-whisper`.
* Move jobs into retry, completed, or failed states.
* Update shared rate-limit state.

### 9.1 Processing model

The worker processes one job at a time.

Required loop:

```text
resolve queue
validate layout
acquire worker ownership
recover abandoned processing jobs
promote eligible retry jobs
select next pending job
claim job
wait for rate admission
download or open local media
transcribe
write and validate transcript
move job to completed
repeat
```

### 9.2 Selection order

Pending jobs must be sorted by:

1. Highest priority.
2. Oldest `created_at`.
3. Stable filename ordering.

Suggested priority values:

| Origin                                  |                   Priority |
| --------------------------------------- | -------------------------: |
| Interactive `lt transcribe`             |                        100 |
| Explicit `lt queue add --priority high` |                         75 |
| Manual queue addition                   |                         50 |
| `ref` submission                        |                         20 |
| `lt batch` submission                   |                         10 |
| Retried job                             | Original priority or lower |

An interactive job may move ahead of pending work but must not interrupt the currently running job.

### 9.3 Worker ownership

The normal deployment must use one local systemd user service.

Also implement a local runtime lock to prevent accidental duplicate workers on the same machine.

Suggested local runtime path:

```text
${XDG_RUNTIME_DIR}/local-transcribe/worker.lock
```

The lock may use:

* `fcntl.flock()` locally
* PID validation
* Hostname and process metadata

Do not place the authoritative worker lock on NFS.

### 9.4 Cross-host worker protection

The supported architecture is one designated worker host per queue.

Write informational worker state to:

```text
worker/state.json
```

Example:

```json
{
  "worker_id": "nomnom-28451",
  "hostname": "nomnom",
  "pid": 28451,
  "started_at": "2026-07-17T08:00:00-05:00",
  "heartbeat_at": "2026-07-17T08:25:10-05:00",
  "current_job": "abcd1234567"
}
```

This file supports diagnostics and stale-worker warnings. It must not be treated as a fully reliable distributed lock.

Running active workers on multiple hosts against the same NFS queue is unsupported in the first implementation.

---

## 10. Crash Recovery

### 10.1 Processing recovery

When the worker starts and finds files in `processing/`:

1. Read each job.
2. Check the recorded worker hostname and PID where meaningful.
3. Check whether a valid final transcript already exists.
4. If a valid transcript exists:

   * Mark the job completed.
5. Otherwise:

   * Record `worker_interrupted`.
   * Increment attempts only if processing had actually begun.
   * Return it to `pending/` or `retry/`.

Because only one worker is supported, more than one file in `processing/` indicates an inconsistent queue and must be logged.

### 10.2 Temporary files

At startup, remove stale files under:

```text
transcription-queue/tmp/
```

Only remove files older than a configurable safety threshold, such as one hour.

### 10.3 Temporary media

Downloaded media must use local storage, for example:

```text
~/.cache/local-transcribe/jobs/<job-id>/
```

or:

```text
${XDG_CACHE_HOME}/local-transcribe/jobs/<job-id>/
```

On worker startup:

* Remove abandoned media directories older than the recovery threshold.
* Preserve media for an active processing job.
* Never place large temporary audio files in the NFS queue by default.

---

## 11. Transcript Output

The final transcript directory remains environment-dependent and may be on NFS.

The transcript JSON must retain the existing schema:

```json
{
  "transcript": "Transcript text",
  "duration": 285,
  "comments": [],
  "metadata": {
    "id": "VIDEO_ID",
    "title": "Video title",
    "channel": "Channel name",
    "published_at": "2026-01-01T12:00:00Z"
  }
}
```

### 11.1 Safe transcript writing

Required sequence:

1. Write locally or to a temporary file in the final transcript directory.
2. Flush and close.
3. Validate the JSON schema.
4. Confirm the transcript field is a non-empty string.
5. Rename to the final filename.
6. Confirm the final file remains readable and valid.
7. Only then mark the job completed.

Recommended temporary name:

```text
VIDEO_ID.json.tmp.<hostname>.<pid>
```

The temporary file and final transcript must be on the same filesystem when atomic rename is required.

---

## 12. Shared Rate-Limit Controller

The current rate limiter stores hourly and daily counts and warnings in `rate_limits.json`.

The new worker must replace advisory rate warnings with an admission controller that can delay execution.

Create or refactor:

```text
src/local_transcribe/services/rate_limiter.py
```

Shared state path:

```text
<resolved-queue>/worker/rate-limit.json
```

Only the worker writes this file.

### 12.1 State format

```json
{
  "schema_version": 1,
  "last_download_started_at": "2026-07-17T08:00:00-05:00",
  "download_attempts_this_hour": 7,
  "download_attempts_today": 28,
  "hour_window_started_at": "2026-07-17T08:00:00-05:00",
  "day_window_started_at": "2026-07-17T00:00:00-05:00",
  "blocked_until": null,
  "consecutive_throttle_failures": 0,
  "last_429_at": null,
  "last_403_at": null,
  "total_429_errors": 0,
  "total_403_errors": 0
}
```

### 12.2 Admission behavior

Before each YouTube download:

1. Reset expired hourly and daily windows.
2. Check `blocked_until`.
3. Enforce minimum time between download starts.
4. Enforce hourly download-attempt budget.
5. Enforce daily download-attempt budget.
6. Sleep until admission is permitted.
7. Persist the admitted attempt before launching `yt-dlp`.

Local-file transcription does not consume YouTube download allowance.

### 12.3 Throttle handling

For HTTP 429:

1. Record the event.
2. Use `Retry-After` when available.
3. Otherwise apply exponential backoff with jitter.
4. Move the job to `retry/`.
5. Set `available_at`.
6. Set global `blocked_until`.

Suggested fallback delays:

```text
first event:   5 minutes
second event: 15 minutes
third event:  60 minutes
later events: exponential increase capped at 6 hours
```

For HTTP 403:

* Permanent private or unavailable video: fail immediately.
* Authentication-required content: fail with clear cookie guidance.
* Suspected temporary YouTube throttling: use retry backoff.
* Do not classify every 403 as a rate-limit event without examining the downloader error category.

### 12.4 Rate-limit guarantee

Because `ref`, `lt batch`, and `lt transcribe` only enqueue jobs, and only the worker runs `yt-dlp`, all commands necessarily share the same rate-limit controller.

---

## 13. CLI Changes

## 13.1 `lt transcribe`

Existing syntax must remain valid:

```bash
lt transcribe <source>
```

New behavior:

1. Resolve the queue.
2. Validate the source.
3. Create or locate the queue job.
4. Give the job interactive priority.
5. Start or wake the worker.
6. Wait for the requested job unless `--no-wait` is used.
7. Display status changes.
8. Return the final transcript path or failure.

Example:

```text
Queue: /opt/md2/music/youtube/transcripts/transcription-queue
Queued: abcd1234567
Waiting behind 1 active job
Downloading
Transcribing
Done. Wrote: /opt/md2/music/youtube/transcripts/abcd1234567.json
```

Required options:

```text
--no-wait
--force
--retry
--priority
--timeout
```

Existing transcription options must be saved into the job’s `options` object.

`lt transcribe` must never call `transcribe_url()` directly when operating in queue mode.

## 13.2 `lt batch`

`lt batch` becomes a bulk queue producer.

```bash
lt batch --input urls.txt
```

Required behavior:

* Validate each URL.
* Normalize each video ID.
* Deduplicate within the input.
* Deduplicate against queue state.
* Skip valid existing transcripts.
* Enqueue remaining jobs.
* Return an enqueue summary.

Optional behavior:

```bash
lt batch --input urls.txt --wait
```

`--wait` waits until every newly submitted job reaches a terminal state.

## 13.3 Worker commands

Add:

```bash
lt worker run
lt worker run --once
lt worker install
lt worker start
lt worker stop
lt worker restart
lt worker status
lt worker logs
```

`lt worker run --once` processes at most one eligible job and exits.

## 13.4 Queue commands

Add:

```bash
lt queue init
lt queue path
lt queue add <source>
lt queue list
lt queue show <job-id>
lt queue retry <job-id>
lt queue cancel <job-id>
lt queue import <pending-file>
lt queue export-pending
lt queue repair
lt queue purge
lt queue stats
```

### `lt queue path`

Must display:

* Every candidate path.
* Whether it exists.
* Which path is selected.
* Whether it is readable and writable.

### `lt queue list`

Filters:

```text
--status
--origin
--limit
--json
```

### `lt queue purge`

Must require explicit filters such as:

```bash
lt queue purge --completed --older-than 30d
```

It must never delete transcript JSON files.

---

## 14. Background Service

Install a user-level systemd service:

```text
~/.config/systemd/user/local-transcribe-worker.service
```

Conceptual unit:

```ini
[Unit]
Description=Local Transcribe Background Worker
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=%h/.local/bin/lt worker run
Restart=on-failure
RestartSec=10

[Install]
WantedBy=default.target
```

### 14.1 Installation command

```bash
lt worker install
```

It must:

1. Resolve the installed `lt` executable.
2. Create the service file.
3. Run `systemctl --user daemon-reload`.
4. Print the commands required to enable the service.
5. Not enable lingering automatically.

Normal activation:

```bash
systemctl --user enable --now local-transcribe-worker.service
```

Optional logged-out execution:

```bash
loginctl enable-linger "$USER"
```

The user must explicitly choose lingering.

### 14.2 Worker fallback

When `lt transcribe` submits a job and no active worker is detected:

1. Attempt to start the systemd user service if installed.
2. Wait for a worker heartbeat.
3. If no service is available, acquire the local worker lock.
4. Process queue jobs in the foreground until the requested job reaches a terminal state.
5. Exit.

This fallback must still use the queue and shared rate-limit state.

---

## 15. `ref-cli` Integration

`ref-cli` owns reference records. `local-transcribe` owns transcription execution.

### 15.1 Preferred enqueue flow

When transcript API retrieval is blocked or no transcript is available:

1. Determine whether a valid local transcript already exists.
2. Invoke the `local-transcribe` queue adapter.
3. Enqueue the YouTube URL with:

```text
origin = ref
priority = 20
```

4. Record a pending transcript marker in `references.md`.
5. Continue processing the reference without waiting for local transcription.

The adapter may:

* Import a Python API from `local_transcribe`, when installed.
* Fall back to invoking `lt queue add`.
* Fall back to appending `transcript-pending.md` when `local-transcribe` is unavailable.

### 15.2 Failure isolation

`ref` must continue working when:

* `lt` is not installed.
* The queue cannot be resolved.
* The NFS mount is unavailable.
* The worker is stopped.

In those cases:

* Log the integration failure.
* Preserve the current `transcript-pending.md` fallback.
* Do not fail reference capture solely because local queue integration failed.

### 15.3 Completion reconciliation

Add a `ref` command such as:

```bash
ref reconcile-transcripts
```

It must:

1. Read reference entries with pending or unavailable transcript markers.
2. Extract the video ID.
3. Find a valid completed transcript JSON.
4. Update only the transcript field in `references.md`.
5. Preserve line order and unrelated fields.
6. Default to dry-run if consistent with existing repair tools.
7. Support `--apply`.

`local-transcribe` must not directly rewrite `references.md`.

---

## 16. Legacy Pending-File Compatibility

The existing file remains:

```text
transcript-pending.md
```

It is not the authoritative queue after migration.

### 16.1 Import

```bash
lt queue import ~/references/transcripts/transcript-pending.md
```

Import rules:

* Ignore blank lines.
* Ignore comments.
* Normalize YouTube URLs.
* Skip valid completed transcripts.
* Skip jobs already present in any queue state.
* Enqueue new jobs with `origin = import`.
* Preserve lines that could not be parsed or imported.
* Back up the source file before rewriting.
* Rewrite through a temporary file and atomic rename.

### 16.2 Export

```bash
lt queue export-pending
```

Generate a human-readable pending file from:

```text
pending/
retry/
processing/
```

The export is a report only.

### 16.3 Transition period

During migration, `lt batch` may continue detecting `transcript-pending.md`, but it should import the file into the queue rather than process it directly.

---

## 17. Existing Status Compatibility

The current project uses:

```text
batch_status.json
finished.dat
```

These may be retained temporarily for compatibility.

Rules:

* Queue state directories are authoritative.
* `batch_status.json` should be generated from job files.
* `finished.dat` should be generated or appended only after verified completion.
* A URL in `finished.dat` without a valid transcript does not count as completed.
* Reconciliation should repair compatibility artifacts from the queue and transcript files.

The existing pipeline already verifies transcript output before appending to `finished.dat`; that behavior must be preserved.

---

## 18. Model Lifecycle

The worker should avoid reloading the same Whisper model for every job.

Implement a worker-scoped model cache keyed by:

```text
model
device
compute_type
```

Behavior:

* Reuse the loaded model when the next job uses the same configuration.
* Release and reload when configuration changes.
* Preserve existing CUDA preflight and CPU fallback behavior.
* Record the effective device and compute type in logs.
* Optionally unload after a configurable idle timeout.

The existing transcriber initializes `WhisperModel` during each transcription call; this should be refactored behind a reusable transcriber instance.

---

## 19. Logging and Status

### 19.1 Local logs

Worker logs should remain local:

```text
${XDG_STATE_HOME}/local-transcribe/logs/worker.log
```

Fallback:

```text
~/.local/state/local-transcribe/logs/worker.log
```

Each job log entry should include:

```text
job_id
source_key
origin
priority
state
attempt
model
requested_device
effective_device
download_strategy
duration
error_category
next_retry_at
```

### 19.2 Status output

Example:

```text
Queue: /opt/md2/music/youtube/transcripts/transcription-queue
Worker: active on nomnom, PID 28451
Current job: abcd1234567
Stage: transcribing
Pending: 14
Retrying: 3
Processing: 1
Completed: 82
Failed: 2
Cancelled: 0
Next download allowed: 38 seconds
Model: medium / cuda / float16
```

### 19.3 Health warnings

Report:

* Both candidate queue directories exist.
* Selected queue is not writable.
* NFS mount is unavailable.
* More than one processing job exists.
* Worker heartbeat is stale.
* A valid transcript exists for a pending job.
* A completed job has a missing or invalid transcript.
* Temporary files are stale.
* Rate-limit state is malformed.

---

## 20. Configuration

Add queue configuration to `local-transcribe` configuration without overriding the required fallback order unless the user explicitly configures a path.

Suggested file:

```text
~/.config/local-transcribe/config.yaml
```

Example:

```yaml
queue:
  path: null
  candidates:
    - ~/references/transcripts/transcription-queue
    - /opt/md2/music/youtube/transcripts/transcription-queue

worker:
  poll_interval_seconds: 5
  stale_processing_seconds: 3600
  stale_tmp_seconds: 3600
  model_idle_unload_seconds: 1800

rate_limit:
  minimum_download_interval_seconds: 30
  max_download_attempts_per_hour: 60
  max_download_attempts_per_day: 500
  maximum_backoff_seconds: 21600
```

Resolution precedence:

1. Explicit CLI `--queue-dir`, when supported.
2. Explicit configured `queue.path`.
3. Required candidate discovery order.
4. Creation of the first candidate only when initialization is requested.

Normal commands should not create a queue silently unless their documented behavior requires enqueueing.

---

## 21. Security and Privacy

* Queue job files may contain URLs and local file paths.
* Queue directories must not be world-writable.
* Cookie contents must never be copied into job JSON.
* Job files may store cookie file paths or browser profile names only.
* Logs must not print cookie values.
* Temporary audio directories should be user-private.
* Validate job JSON before executing it.
* Reject unknown `source_type` values.
* Do not execute arbitrary shell strings from job metadata.
* Pass downloader arguments as argument arrays, not shell-formatted commands.
* Resolve and validate local file paths before transcription.
* Reject local directories and non-regular files.
* Do not follow untrusted job-specified output paths outside approved transcript roots without an explicit override.

---

## 22. Testing Requirements

### 22.1 Queue path tests

1. First candidate exists: select it.
2. First missing, second exists: select second.
3. Both exist: select first and warn.
4. Neither exists, `create=False`: return clear error.
5. Neither exists, `create=True`: create first.
6. First exists but is unreadable: error; do not fall through.
7. `~` resolves correctly under a systemd user service.

### 22.2 Enqueue tests

1. Enqueue a new YouTube URL.
2. Enqueue an alternate URL for the same video.
3. Concurrent duplicate enqueue attempts produce one final job.
4. Existing transcript prevents enqueue.
5. `--force` behaves according to specification.
6. Temporary file is removed after failed enqueue.
7. Malformed source is rejected.
8. Local files produce stable hashed job IDs.

### 22.3 Worker tests

1. Worker processes one job at a time.
2. Second local worker refuses to start.
3. Highest-priority pending job runs first.
4. Equal-priority jobs run oldest first.
5. Interactive job does not interrupt the active job.
6. Successful job produces valid transcript and completed record.
7. Output validation failure prevents completion.
8. Graceful termination returns active work to a recoverable state.
9. Startup recovers abandoned processing jobs.
10. Worker reuses the same Whisper model.
11. Worker reloads the model when options change.

### 22.4 Rate-limit tests

1. Background and interactive jobs use one rate state file.
2. Local-file jobs do not consume download allowance.
3. Minimum download interval is enforced.
4. Hourly limit delays processing.
5. Daily limit delays processing.
6. 429 sets global `blocked_until`.
7. Retry-After is honored.
8. Exponential fallback backoff works.
9. Malformed rate state is recovered safely.
10. Only the worker writes rate state.

### 22.5 Integration tests

1. `ref` and `lt transcribe` submit the same URL simultaneously.
2. Only one job is created.
3. Only one transcription runs.
4. `lt transcribe` waits for an existing `ref` job and returns its output.
5. `lt batch` deduplicates against jobs submitted by `ref`.
6. `ref` continues when queue integration is unavailable.
7. Pending-file import is idempotent.
8. Reconciliation updates only the transcript field.

### 22.6 NFS tests

Run integration tests against the actual NFS mount:

1. Temporary-to-pending rename.
2. Pending-to-processing rename.
3. Processing-to-completed rename.
4. Concurrent job visibility.
5. Final transcript temporary rename.
6. Recovery after worker termination.
7. Mount interruption and restoration.
8. Behavior when the mount becomes read-only.

---

## 23. Acceptance Criteria

The implementation is complete when all of the following are true:

1. `ref` can enqueue blocked YouTube transcripts.
2. `lt batch` can enqueue multiple jobs.
3. `lt transcribe <source>` uses the same queue.
4. Only one worker executes jobs.
5. The worker processes exactly one job at a time.
6. All YouTube downloads share one rate-limit controller.
7. Interactive submissions receive higher pending priority.
8. Interactive submissions do not bypass or interrupt active work.
9. The queue works from either required environment path.
10. The first existing candidate path is always selected.
11. No authoritative queue state depends on SQLite.
12. No authoritative queue state depends on `transcript-pending.md`.
13. Job state transitions use atomic same-filesystem renames.
14. Temporary media remains local by default.
15. Final transcripts may be stored on NFS.
16. Duplicate sources do not create duplicate active jobs.
17. Worker crashes do not permanently strand jobs.
18. A job is never completed before transcript validation.
19. Existing pending files can be imported safely.
20. Existing workflows remain usable during migration.

---

## 24. Implementation Phases

### Phase 1: Queue foundation

Create:

```text
queue_paths.py
job.py
queue_store.py
```

Implement:

* Queue resolution.
* Directory initialization.
* Job schema.
* Safe enqueue.
* Deduplication.
* Queue listing.
* Atomic metadata replacement.

### Phase 2: Worker execution

Create:

```text
worker.py
worker_state.py
```

Implement:

* Local singleton lock.
* Worker heartbeat.
* Job selection.
* Job claim.
* Sequential processing.
* State transitions.
* Startup recovery.
* Local temporary media management.

### Phase 3: Shared rate admission

Refactor:

```text
rate_limiter.py
```

Implement:

* Shared worker-owned state.
* Hard admission delays.
* 429 and 403 classification.
* Retry scheduling.
* Global backoff.

### Phase 4: CLI migration

Modify:

```text
cli.py
```

Implement:

* Queue commands.
* Worker commands.
* `lt transcribe` enqueue-and-wait.
* `lt batch` bulk enqueue.
* Foreground worker fallback.
* Queue-aware status.

### Phase 5: systemd integration

Implement:

* User service generation.
* Install/start/stop/status/log commands.
* Executable path detection.
* Explicit lingering guidance.

### Phase 6: Legacy migration

Implement:

* Pending-file import.
* Pending-file export.
* Compatibility generation for `finished.dat`.
* Compatibility generation for `batch_status.json`.
* Queue repair command.

### Phase 7: `ref-cli` integration

Implement:

* Queue adapter.
* Graceful fallback.
* Pending marker handling.
* Transcript reconciliation command.
* Cross-project tests.

### Phase 8: Hardening

Complete:

* Real NFS integration testing.
* Mount interruption testing.
* Security review.
* Logging review.
* Upgrade and rollback documentation.
* Final acceptance tests.

---

## 25. Non-Goals

The first implementation will not support:

* Multiple active workers across multiple hosts.
* Distributed consensus.
* Parallel transcription.
* Parallel downloading.
* A web-based queue interface.
* Remote worker APIs.
* Automatic cookie synchronization.
* Automatic modification of `references.md` by `local-transcribe`.
* Replacing the transcript JSON format.
* Storing temporary downloaded media permanently on NFS.
* SQLite or another embedded database as authoritative queue storage.

---

## 26. Final Architecture

```text
                         NFS transcript environment
┌───────────────────────────────────────────────────────────────────┐
│                                                                   │
│  resolved transcription-queue/                                    │
│  ├── pending/                                                      │
│  ├── processing/                                                   │
│  ├── retry/                                                        │
│  ├── completed/                                                    │
│  ├── failed/                                                       │
│  └── worker/rate-limit.json                                        │
│                                                                   │
│  final transcript JSON files                                      │
│                                                                   │
└───────────────────────────────────────────────────────────────────┘
                ▲                         ▲
                │ enqueue                 │ final validated output
                │                         │
       ┌────────┴────────┐       ┌────────┴────────────┐
       │                 │       │                     │
   ref-cli         lt commands   │ single worker       │
                  transcribe     │ systemd --user      │
                  batch          │                     │
                  queue          │ yt-dlp              │
                                 │ faster-whisper      │
                                 │ local temp audio    │
                                 └─────────────────────┘
```

The architectural boundary is:

> Producers submit durable job files. One worker performs every download and transcription.

This boundary guarantees sequential work, shared pacing, consistent queue discovery, NFS-compatible durability, and predictable recovery.


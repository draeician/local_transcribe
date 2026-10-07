# Background Transcription Queue Feature Specification (v3)

**Project:** `local-transcribe`  
**Related project:** `ref-cli`  
**Document type:** Development specification  
**Status:** Proposed (revision 3)  
**Supersedes:** SPEC-queue.md (v1, v2)  
**Target platform:** Linux, including Linux Mint 22  
**Primary storage:** Shared NFSv3 transcript filesystem  
**Execution model:** File-based durable queue with one NLM-locked worker active across all hosts  

**Companion documents:**

* [`docs/QUEUE_DESIGN_DECISIONS.md`](docs/QUEUE_DESIGN_DECISIONS.md) — terminology and decision summary  
* Architectural review (2026-07-18) — NLM lock model, source reservations, mount validation  

---

## Changes from v2

1. **Worker ownership replaced.** v2 used a JSON heartbeat lease (`O_CREAT|O_EXCL`, timestamp staleness, rename-to-steal). v3 uses an **NLM-backed POSIX record lock** on a stable `worker/worker.lock` file via `fcntl.lockf()` (`LOCK_EX | LOCK_NB`). The lock file is never renamed or replaced while held.
2. **Removed** `lease_renew_seconds`, `stale_lease_seconds`, lock-file heartbeat renewal, host-clock ownership, rename-to-steal, and JSON-content fencing.
3. **NFSv3 environment validation** required before the worker starts (`lt queue doctor` / `lt worker doctor` / worker startup fail-closed).
4. **Exactly-once claim softened.** Spec guarantees at-most-one cooperative owner while the NLM lock is valid, plus idempotent recovery; it does **not** claim absolute exactly-once CPU/GPU work across partitions and reboots.
5. **Permanent source reservations** under `keys/<source-key>.json`. Execution IDs are separate from source keys. Hard-link publication alone on `pending/` is no longer sufficient for lifetime dedup.
6. **Queue path is explicit.** Candidate auto-discovery (`~/references/...` then `/opt/md2/...`) is removed. Configured `queue.path` + `expected_uuid` (and optional NFS identity checks) are required. No silent multi-path fall-through.
7. **Local-file portability.** Shared roots and/or `required_host` affinity. Cookie paths replaced by **auth profiles** resolved locally on each worker.
8. **Generation-aware transcript publication.** Stale executions must not overwrite newer valid outputs.
9. **Priority aging / fairness.** Interactive work cannot starve batch/`ref` indefinitely.
10. **Downloader hardening** for daemon use (process groups, timeouts, structured errors, no broad `--ignore-errors`).
11. **`lt transcribe --direct`** for emergency/dev bypass; queue mode remains default.
12. Tests, phases, acceptance criteria, modules list, and architecture diagram updated accordingly.

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

The queue and completed transcripts reside on a shared **NFSv3** filesystem. The design remains file-based and must not depend on SQLite, SQLite WAL, or a single mutable queue document as authority.

### 1.1 Architectural division of responsibility

| Layer | Responsibility |
|-------|----------------|
| **NFSv3** | Durable shared files, same-filesystem atomic rename and hard-link semantics |
| **NLM / NSM** | Cooperative cross-host exclusive ownership of the worker role |
| **Queue application** | Source identity, reservations, generations, idempotent recovery, retries, admission control, artifact validation |

The resulting design:

> Producers on any host atomically publish jobs to an NFSv3 spool. Every worker competes for one NLM-backed POSIX lock. Only the lock holder may download, transcribe, modify queue state, or publish transcripts.

Idempotent recovery remains mandatory. No filesystem lock guarantees that a long-running local transcription is executed exactly once across every crash, server restart, process freeze, or network failure. The correct guarantee is:

> At most one cooperative worker owns the queue while the NLM lock is valid, and all queue transitions and outputs are recoverable and idempotent.

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

Verified against the current code:

* `lt transcribe` calls `transcribe_url()` / `transcribe_local_file()` directly, bypassing `BatchPipeline`, so it shares neither rate-limit state nor serialization with batch work.
* `BatchPipeline` creates `batch_status.json` and `rate_limits.json` under the configured **output directory**. Different output dirs (or hosts) maintain independent state.
* `RateLimiter` is advisory only: `check_limits()` returns a warning and `get_recommended_delay()` suggests a delay; nothing blocks execution. Attempt counts are recorded after processing rather than reserved before `yt-dlp`.
* `safe_write_json()` writes JSON **in place** with no temporary file or rename; concurrent readers can observe truncated documents. Unsuitable for authoritative multi-host queue state.
* `JsonStatusStore` rewrites the entire shared status dictionary on each job change.
* `transcribe_audio()` constructs a new `WhisperModel` for every call.
* `transcribe_url()` places downloaded media under the final output directory (often NFS).
* Transcript JSON is written directly to the final path (not atomic publish + revalidate).
* The batch pipeline verifies transcript output before appending to `finished.dat`; that verify-before-complete discipline must be preserved and generalized.

---

## 3. Design Principles

1. **Files remain authoritative** for queue and job state.
2. **The queue must work on shared NFSv3** with functional NLM.
3. **One execution file per generation**, plus **one permanent reservation per source**.
4. **Never use one shared mutable pending file** as the authoritative queue.
5. **Only the NLM lock holder** may execute transcription jobs or modify authoritative queue state.
6. **CLI commands submit work; the worker executes work.**
7. **`lt transcribe` defaults to the queue**; `--direct` is explicit emergency/dev only.
8. **Final transcript files may reside on NFS.**
9. **Temporary downloaded media remains on local storage**, keyed by execution id.
10. **State transitions use same-filesystem atomic rename**; job and reservation creation use atomic hard-link publication.
11. **A completed execution is valid only after its transcript JSON passes validation.**
12. **`transcript-pending.md`, `finished.dat`, and `batch_status.json` are compatibility artifacts**, not queue authorities.
13. **Worker ownership is NLM POSIX locking**, not application timestamps or rename-to-steal.
14. **Do not claim absolute exactly-once external work**; claim cooperative single ownership + idempotent publication.
15. **Queue path is configured explicitly**; no multi-candidate silent discovery.
16. **All mutable JSON (except the open lock file contents after lock) uses tmp + fsync + rename (or link for create-if-absent).**
17. **Never use `safe_write_json()` for queue, worker, rate, reservation, or transcript authority.**

---

## 4. Queue Location Resolution

All producers, consumers, status commands, migration commands, and workers must use one shared resolver.

### 4.1 Explicit path only (no candidate search)

v2 candidate auto-discovery is **removed**. Selecting the first existing directory independently on every host can create split-brain (one host local `~/references/...`, another NFS `/opt/md2/...`).

Configuration (required for normal operation):

```yaml
queue:
  path: /opt/md2/music/youtube/transcripts/transcription-queue
  expected_uuid: b6c1e0f2-0000-0000-0000-000000000000
  expected_nfs_version: 3
  expected_server: nas.example.internal
  expected_export: /exports/transcripts
```

### 4.2 Resolution precedence

1. Explicit CLI `--queue-dir`, when provided.
2. Configured `queue.path`.
3. **No fallback.** Error if neither is set.

Normal commands must not search multiple unrelated locations.

`lt queue init --queue-dir PATH` creates the queue only at an explicitly selected path and writes `queue.id` once.

### 4.3 Required resolver interface

```text
src/local_transcribe/services/queue_paths.py
src/local_transcribe/services/mount_validation.py
```

```python
from pathlib import Path


class QueuePathError(RuntimeError):
    pass


class QueueIdentityError(QueuePathError):
    pass


class QueueMountError(QueuePathError):
    pass


def resolve_queue_dir(*, queue_dir: Path | None = None) -> Path:
    """Resolve the configured queue directory; no multi-candidate search."""


def verify_queue_identity(queue_dir: Path, *, expected_uuid: str | None) -> str:
    """Read queue.id; fail if missing, unparseable, or UUID mismatch."""


def initialize_queue_layout(queue_dir: Path) -> str:
    """Create layout and write queue.id once; return the new UUID."""
```

Every component must import this resolver. No command may independently recreate path logic.

### 4.4 Queue identity marker

```text
transcription-queue/queue.id
```

```json
{
  "queue_uuid": "b6c1e0f2-...-generated-once",
  "created_at": "2026-07-18T08:00:00-05:00",
  "created_by": "nomnom"
}
```

1. Written exactly once at init; never modified.
2. Every resolve must verify `queue.id` exists and is parseable.
3. If `expected_uuid` is configured, it must match.
4. A directory without a valid `queue.id` is an error, not a soft warning to continue.

### 4.5 `lt queue path` output

Must display:

* Configured path  
* Resolved path  
* Queue UUID  
* Mountpoint  
* Filesystem type  
* NFS version  
* Server  
* Export  
* Mount options  
* Read/write status  
* NLM lock test status  

---

## 5. Queue Directory Structure

```text
transcription-queue/
├── queue.id
├── keys/
│   └── <source-key>.json
├── pending/
│   └── <execution-id>.json
├── processing/
│   └── <execution-id>.json
├── retry/
│   └── <execution-id>.json
├── completed/
│   └── <execution-id>.json
├── failed/
│   └── <execution-id>.json
├── cancelled/
│   └── <execution-id>.json
├── tmp/
└── worker/
    ├── worker.lock
    ├── state.json
    └── rate-limit.json
```

### 5.1 Directory and file purposes

| Entry | Purpose | Authority |
| ----- | ------- | --------- |
| `queue.id` | Queue identity marker | Authoritative identity |
| `keys/` | One permanent reservation per canonical source | Authoritative identity + current generation |
| `pending/` | Executions eligible for processing | Authoritative execution state |
| `processing/` | Executions claimed by the current worker | Authoritative execution state |
| `retry/` | Executions waiting until `available_at` | Authoritative execution state |
| `completed/` | Successfully completed executions | Authoritative execution state |
| `failed/` | Permanently failed or retry-exhausted executions | Authoritative execution state |
| `cancelled/` | Explicitly cancelled executions | Authoritative execution state |
| `tmp/` | Temporary files for atomic create / replace | Transient |
| `worker/worker.lock` | Stable file for NLM POSIX exclusive lock | Ownership target (lock state, not JSON contents) |
| `worker/state.json` | Diagnostic worker heartbeat / current job | **Informational only** |
| `worker/rate-limit.json` | Shared download admission state | Authoritative, worker-lock-holder only |

Only the NLM lock holder may move execution files between processing states or write rate-limit / reservation generation advances that imply execution ownership.

Producers may create reservations and publish executions under `keys/` and `pending/` through the queue service using atomic publication primitives.

---

## 6. Source Identity, Reservations, and Deduplication

### 6.1 Source keys vs execution IDs

| Concept | Role | Example |
| ------- | ---- | ------- |
| **Source key** | Permanent identity of a logical source | `youtube:abcd1234567`, `local:<hash>` |
| **Execution ID** | One attempt generation (UUIDv7 recommended) | `0190dc1a-43c4-7c28-bcb3-5980d90a28c2` |
| **Generation** | Monotonic integer on the reservation | `1`, `2`, … |

v2 used the video ID as the pending filename. That fails once the job leaves `pending/`: another producer can publish a second pending job for the same source. Claim-time dedup alone does not close enqueues after the claim-time check.

### 6.2 YouTube source keys

Canonical key: `youtube:<video_id>`.

Different URL forms must resolve to the same key:

```text
https://www.youtube.com/watch?v=abcd1234567
https://youtu.be/abcd1234567
https://www.youtube.com/watch?v=abcd1234567&feature=shared
```

### 6.3 Local-file source keys

Derived from:

```text
canonical absolute path
file size
modification timestamp
```

Hashed into a filesystem-safe key, e.g. `local:1c7d80b50d12e37c`.

### 6.4 Permanent source reservation

```text
keys/<source-key-safe>.json
```

Filename encoding must be filesystem-safe (escape `:` and other reserved characters as needed; document the codec in `source_reservations.py`).

```json
{
  "schema_version": 1,
  "source_key": "youtube:abcd1234567",
  "source_type": "youtube",
  "current_execution_id": "0190dc1a-43c4-7c28-bcb3-5980d90a28c2",
  "generation": 1,
  "created_at": "2026-07-18T12:00:00-05:00",
  "updated_at": "2026-07-18T12:00:00-05:00"
}
```

Rules:

1. Never release the reservation merely because an execution moved between queue states.
2. `--force` creates a **new generation** under the existing reservation (new execution id, incremented generation), not a second unrelated key.
3. Duplicate submissions resolve to the existing reservation and its current execution when still active or already successfully completed with a valid transcript.

### 6.5 Enqueue sequence

1. Normalize the source key.
2. Check for a valid existing final transcript for the source (advisory + then authoritative checks).
3. Try to publish `keys/<source-key>.json` atomically via tmp + `link()` (create-if-absent).
4. If the reservation already exists, resolve its `current_execution_id` and generation.
5. If a new execution is required, write the execution document under `tmp/` and publish to `pending/<execution-id>.json` via `link()`.
6. Update the reservation’s `current_execution_id` / `generation` only through worker-safe or carefully ordered atomic replace rules so incomplete pairs can be repaired.
7. If execution publication fails after creating a brand-new reservation, repair or remove the incomplete reservation **only when ownership of that incomplete state can be proven** (same producer cleanup of its own tmp; worker repair under NLM lock for abandoned incomplete pairs).
8. Never treat “no file in pending/” as “source is free.”

### 6.6 Duplicate rules (producer-facing)

| Situation | Behavior |
| --------- | -------- |
| Valid final transcript exists | Return `already_completed` with transcript path |
| Active execution (pending/processing/retry) | Return existing execution |
| Failed execution | Require `--retry` or `--force` (new generation) |
| Cancelled execution | Require `--force` (new generation) |
| `--force` | New execution generation on the same reservation |

### 6.7 Local-file portability

A path such as `/home/user/recordings/interview.m4a` may not exist on another worker host. Every local-file execution must use one of:

#### Shared source

File is under an approved shared root:

```yaml
sources:
  shared_roots:
    - /opt/md2/music/audio-input
```

Any worker may claim it.

#### Host-affined source

```json
{
  "required_host": "nomnom"
}
```

Only that hostname may claim the execution. Other workers skip it.

#### Staged source (future)

Producer copies into an approved shared staging directory before publish. Not required in the first implementation.

First version: **shared roots + host affinity**. Automatic staging later.

### 6.8 Authentication profiles (not host cookie paths)

Jobs must not embed host-specific cookie file paths as the portable contract.

```json
{
  "options": {
    "auth_profile": "youtube-personal"
  }
}
```

Each worker resolves the profile from local config:

```yaml
auth_profiles:
  youtube-personal:
    cookies_file: ~/.config/local-transcribe/cookies/youtube.txt
```

Cookie **contents** must never be written to NFS job metadata or logs.

---

## 7. Execution File Format

Each execution is one JSON document named by execution id:

```text
pending/0190dc1a-43c4-7c28-bcb3-5980d90a28c2.json
```

```json
{
  "schema_version": 1,
  "execution_id": "0190dc1a-43c4-7c28-bcb3-5980d90a28c2",
  "source_key": "youtube:abcd1234567",
  "generation": 1,
  "source": "https://www.youtube.com/watch?v=abcd1234567",
  "source_type": "youtube",
  "origin": "ref",
  "priority": 20,
  "status": "pending",
  "created_at": "2026-07-18T08:15:00-05:00",
  "updated_at": "2026-07-18T08:15:00-05:00",
  "available_at": "2026-07-18T08:15:00-05:00",
  "started_at": null,
  "completed_at": null,
  "attempts": 0,
  "max_attempts": 3,
  "worker": null,
  "required_host": null,
  "output_path": null,
  "error": null,
  "options": {
    "model": "medium",
    "device": "cuda",
    "compute_type": "float16",
    "language": null,
    "keep_audio": false,
    "auth_profile": null,
    "limit_rate": null,
    "sleep_interval_requests": null
  }
}
```

### 7.1 Required fields

| Field | Requirement |
| ----- | ----------- |
| `schema_version` | Queue schema version |
| `execution_id` | Filesystem-safe unique execution identifier |
| `source_key` | Canonical source identity |
| `generation` | Reservation generation this execution belongs to |
| `source` | Original source URL or path |
| `source_type` | `youtube` or `local_file` |
| `origin` | Producer: `ref`, `lt-transcribe`, `lt-batch`, `import`, … |
| `priority` | Base processing priority |
| `status` | Current queue state |
| `created_at` / `updated_at` / `available_at` | ISO 8601 timestamps |
| `attempts` / `max_attempts` | Retry accounting |
| `options` | Transcription options (immutable for this execution) |

Optional: `required_host` for host-affined local files.

### 7.2 Error object

```json
{
  "category": "rate_limited",
  "message": "HTTP 429 returned by yt-dlp",
  "retryable": true,
  "occurred_at": "2026-07-18T08:40:00-05:00",
  "next_retry_at": "2026-07-18T09:00:00-05:00"
}
```

Recommended categories:

```text
rate_limited
temporary_forbidden
authentication_required
private_video
video_unavailable
download_failed
extractor_failure
network_failure
postprocessing_failure
transcription_failed
output_validation_failed
invalid_source
source_not_accessible
worker_interrupted
internal_error
```

---

## 8. Safe File Operations

### 8.1 Atomic JSON helper

All mutable JSON **except** ownership of the open `worker.lock` descriptor must use temporary file + fsync + validate + rename. Implement:

```text
src/local_transcribe/services/atomic_files.py
```

```python
def atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    """Write via unique tmp, fsync, parse-validate, os.replace, fsync parent dir."""
```

Do **not** silently change the existing global `safe_write_json()`; create explicit helpers for queue authority. Do not use `safe_write_json()` for queue records, worker state, rate state, source reservations, or transcript output.

### 8.2 Create-if-absent publication (`link()`)

POSIX `rename()` **silently replaces** an existing destination. Enqueue and reservation create use:

1. Write unique file under `tmp/` (hostname, pid, random).
2. Flush and `fsync()` where supported.
3. `os.link(tmp_path, dest_path)` — fails with `EEXIST` if destination exists.
4. Unlink tmp on success or failure paths.

Applies to:

* `keys/<source-key>.json` first publication  
* `pending/<execution-id>.json` publication  

```python
try:
    os.link(tmp_path, dest_path)
except FileExistsError:
    os.unlink(tmp_path)
    return existing_record(dest_path)
os.unlink(tmp_path)
```

### 8.3 State transitions (`rename()`)

Performed **only** by the NLM lock holder, same filesystem:

```text
pending/<execution-id>.json    -> processing/<execution-id>.json
processing/<execution-id>.json -> completed/<execution-id>.json
processing/<execution-id>.json -> retry/<execution-id>.json
processing/<execution-id>.json -> failed/<execution-id>.json
pending/<execution-id>.json    -> cancelled/<execution-id>.json
```

Do not copy-and-delete as the normal mechanism. Claim = rename into `processing/`; loser sees `ENOENT`.

### 8.4 Metadata updates owned by the worker

1. Write replacement under `tmp/`.
2. Flush, fsync, validate.
3. `os.replace` onto the current path in the same state directory (or rename across state dirs when status changes).

### 8.5 NFS assumptions

Required:

* Atomic same-filesystem `rename()` / `os.replace`
* Atomic `link()` with correct `EEXIST`
* Functional NLM for POSIX record locks on NFSv3

Must **not** depend on:

* SQLite locking  
* Application-level timestamp leases  
* `rename()` failing when destination exists  
* Atomic append to a shared queue file  
* Strict cross-client attribute cache coherence for correctness (RFC 1813)  

Cross-host **reads** may be stale up to mount attribute-cache timeouts. Correctness comes from atomic write primitives and NLM; directory listings are advisory.

---

## 9. Worker Architecture

### 9.1 Modules

```text
src/local_transcribe/services/worker.py
src/local_transcribe/services/worker_lock.py
src/local_transcribe/services/worker_state.py
src/local_transcribe/services/source_reservations.py
src/local_transcribe/services/queue_store.py
src/local_transcribe/services/queue_models.py
src/local_transcribe/services/model_cache.py
src/local_transcribe/services/download_admission.py
src/local_transcribe/services/mount_validation.py
src/local_transcribe/services/atomic_files.py
```

Only the NLM lock holder may:

* Claim executions  
* Run `yt-dlp`  
* Run `faster-whisper`  
* Move executions into retry / completed / failed  
* Update shared rate-limit state  
* Publish final transcripts  
* Advance reservation generations that imply completed/failed outcomes (as specified)  

Producers may: validate sources, normalize identity, create reservations, publish pending executions, read status.

Producers must **not**: run `yt-dlp`, load Whisper, change processing state, write rate state, or publish final transcripts.

### 9.2 Processing loop

```text
resolve configured queue path
verify queue UUID
verify NFSv3 mount and options
verify rpc.statd / NLM environment
acquire optional local runtime lock
open stable worker.lock file
acquire NLM POSIX record lock (nonblocking exclusive)
write worker/state.json (diagnostic)
clean stale tmp files
recover processing records
promote eligible retry records
select next eligible execution
if nothing is claimable: run idle model maintenance (§20), sleep poll interval, re-poll
claim through rename to processing/
verify source reservation generation is current
check for valid existing transcript
enforce shared rate admission (YouTube)
download into local scratch storage
transcribe using cached model
write and validate transcript atomically (generation-aware)
verify execution is still current on the reservation
move execution record to completed/
update compatibility artifacts if enabled
update diagnostic state
repeat
```

### 9.3 Selection order and fairness

Base priorities (suggested):

| Origin | Base priority |
| ------ | ------------: |
| Interactive `lt transcribe` | 100 |
| Explicit `lt queue add --priority high` | 75 |
| Manual queue addition | 50 |
| `ref` submission | 20 |
| `lt batch` submission | 10 |

Strict priority alone allows interactive work to starve batch/`ref`. Use **effective priority** or weighted fairness:

```text
effective_priority = base_priority + waiting_age_bonus
```

Simpler first implementation (acceptable):

* At most `max_interactive_streak` (default **5**) interactive executions consecutively.
* Then process the oldest eligible noninteractive execution.
* **Never interrupt** an active execution.

Local-file jobs: skip if `required_host` is set and does not match; skip if path is not under an allowed shared root and not host-affined for this host.

### 9.4 Local runtime lock

Optional fast-fail against duplicate workers on the **same** machine:

```text
${XDG_RUNTIME_DIR}/local-transcribe/worker.lock
```

Uses local `fcntl`/`flock` with PID validation. Convenience only. Authoritative cross-host exclusion is §9.5.

### 9.5 NLM-backed POSIX worker lock (authoritative)

Replace the v2 JSON lease entirely.

#### Implementation rules

1. Open one **stable** file:

```text
<queue>/worker/worker.lock
```

2. Acquire a **nonblocking exclusive POSIX record lock** (`fcntl.lockf` with `LOCK_EX | LOCK_NB`).
3. Keep the file descriptor open for the **full worker lifetime**.
4. **Never** replace, rename, or unlink the lock file while the lock is held.
5. Release by unlock + close.
6. On `EACCES` / `EAGAIN`: standby or exit with a clear message.

Reference implementation (normative shape):

```python
from __future__ import annotations

import errno
import fcntl
import json
import os
import socket
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import IO


class WorkerLockError(RuntimeError):
    pass


class WorkerAlreadyActive(WorkerLockError):
    pass


@dataclass
class WorkerLock:
    path: Path
    file: IO[str]

    @classmethod
    def acquire(cls, path: Path) -> "WorkerLock":
        path.parent.mkdir(parents=True, exist_ok=True)
        file = path.open("a+", encoding="utf-8")
        try:
            fcntl.lockf(file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            file.close()
            if exc.errno in (errno.EACCES, errno.EAGAIN):
                raise WorkerAlreadyActive(
                    "Another worker currently owns the queue lock"
                ) from exc
            raise WorkerLockError(f"Unable to obtain NFS worker lock: {exc}") from exc

        metadata = {
            "hostname": socket.gethostname(),
            "pid": os.getpid(),
            "acquired_at": datetime.now(timezone.utc).isoformat(),
        }
        file.seek(0)
        file.truncate()
        json.dump(metadata, file, indent=2)
        file.write("\n")
        file.flush()
        os.fsync(file.fileno())
        return cls(path=path, file=file)

    def release(self) -> None:
        try:
            fcntl.lockf(self.file.fileno(), fcntl.LOCK_UN)
        finally:
            self.file.close()
```

Use `fcntl.lockf()` (POSIX record locking) explicitly rather than relying on `flock()` behavioral differences across clients.

#### Critical lock-file rule

Do **not** “renew” the lock by writing a temporary file and renaming it over `worker.lock`. The lock is associated with the open file and lock state. Replacing the pathname creates a different file identity and can allow another process to lock the replacement while the original still holds a lock on the old inode.

Diagnostic heartbeat / current job information belongs **only** in:

```text
worker/state.json
```

written with `atomic_write_json`. That file is **never** the ownership authority.

#### Removed from v2

* `O_CREAT|O_EXCL` lease acquisition as ownership  
* Timestamp renewal of the lock file  
* `stale_lease_seconds` / host-clock comparison  
* Rename-to-steal / delete-and-recreate takeover  
* Fencing by re-reading JSON ownership fields  

### 9.6 Diagnostic worker state

```json
{
  "worker_id": "nomnom-28451",
  "hostname": "nomnom",
  "pid": 28451,
  "queue_uuid": "b6c1e0f2-...",
  "started_at": "2026-07-18T08:00:00-05:00",
  "heartbeat_at": "2026-07-18T08:25:10-05:00",
  "current_execution_id": "0190dc1a-43c4-7c28-bcb3-5980d90a28c2",
  "active": true
}
```

Status commands may display this, but **only successful or failed NLM lock acquisition** determines authority.

### 9.7 Standby behavior

```bash
lt worker run --standby
```

If NLM lock acquisition fails:

* Exit clearly, or  
* Remain in standby and retry at `standby_retry_seconds` (default 30)

Standby workers must **not** inspect `worker/state.json` to decide availability. They only retry the NLM lock.

Useful when the systemd user service is installed on multiple hosts.

### 9.8 Post-claim checks

After rename into `processing/` and before external work:

1. Confirm the reservation’s `current_execution_id` and `generation` still match this execution (else cancel/supersede this execution without publishing).
2. If a valid final transcript already exists for the source at the required generation policy, complete without re-download when safe.
3. Enforce host affinity / shared-root accessibility for local files.
4. Proceed to rate admission (YouTube only).

---

## 10. NFSv3 Environment Validation

The worker must **fail closed** unless the queue mount meets required conditions.

### 10.1 Required checks

* Filesystem is NFS  
* Negotiated version is **NFSv3**  
* Mount is **not** `nolock`  
* POSIX locking is **not** local-only (`local_lock=posix` or `local_lock=all` rejected)  
* Mount uses **`hard`**, not `soft` or `softerr`  
* **TCP** transport  
* Mount is **read-write**  
* Queue path resides on the **expected server and export** when configured  
* Queue UUID matches `expected_uuid` when configured  
* Mountpoint is not an empty local directory under an **unmounted** NFS path  
* `rpc.statd` is active on the client (where observable)  
* NLM service reachable on the server (where observable)  
* A **functional** lock acquisition test has passed (two-client lab; single-client self-test where possible)  

Example expected characteristics (defaults may be omitted from `findmnt` output):

```text
vers=3,proto=tcp,hard,lock,local_lock=none,rw
```

Validation must reject explicitly unsafe options and perform a functional lock test where practical.

### 10.2 `rpc.statd`, firewalls, and ports

For NFSv3, `lockd` implements NLM and `rpc.statd` supports reboot detection and lock recovery. Linux normally starts these when an NFSv3 filesystem is mounted.

Firewalls must allow (as applicable):

* NFS  
* `rpcbind`  
* `mountd`  
* NLM / `nlockmgr`  
* `rpc.statd`  

Static ports should be used where firewall predictability is required.

The application cannot repair infrastructure automatically. `lt doctor`, `lt queue doctor`, and `lt worker doctor` diagnose it.

### 10.3 Why hard mounts

Soft NFS timeouts can produce I/O failures and, in some cases, silent data corruption. Hard mounts may block until the server returns; that is preferable to misleading success on authoritative queue writes.

---

## 11. Crash Recovery

### 11.1 Normal worker exit

* Unlock and close `worker.lock`  
* Mark `worker/state.json` inactive (or remove)  
* Leave no execution only in ambiguous in-memory state  

### 11.2 Worker process crash

The OS closes the lock descriptor. NLM releases or recovers lock state per client/server condition.

The next worker that obtains the NLM lock:

1. Scans `processing/`  
2. For each execution, checks whether a valid final transcript exists  
3. Checks whether the execution remains current on `keys/`  
4. Marks completed if publication already succeeded  
5. Otherwise records `worker_interrupted`, schedules retry or pending, increments attempts only if processing had begun  
6. Logs if more than one processing file exists (inconsistent / multi-crash residue)  

### 11.3 Client reboots

`rpc.statd`, NSM, and `lockd` participate in NFSv3 lock recovery after reboots. The queue **still** performs processing recovery because application state may diverge from lock state.

### 11.4 NFS server reboots

After the mount resumes, the worker must:

1. Confirm the same mount source  
2. Confirm the same queue UUID  
3. Confirm it still owns or can reacquire the NLM lock  
4. Reopen queue metadata  
5. Perform processing recovery  
6. Resume only after those checks pass  

### 11.5 Network partition

With a hard mount, NFS operations may block until the server returns. Do **not** infer lock loss from heartbeat age of `state.json`.

### 11.6 Temporary files

At startup (under lock), remove stale `tmp/` files older than `stale_tmp_seconds` (default 3600). Surviving tmp files indicate crashed producers or interrupted atomic writes.

### 11.7 Temporary media

```text
${XDG_CACHE_HOME:-$HOME/.cache}/local-transcribe/jobs/<execution-id>/
```

On worker startup under lock:

* Remove abandoned media directories older than the recovery threshold  
* Preserve media for executions still in `processing/` if this worker continues them  
* Never place large temporary audio in the NFS queue by default  

### 11.8 Exactly-once scope

The design does **not** claim absolute exactly-once Whisper/download CPU work across all failure modes. It claims:

> The queue maintains one authoritative execution generation per source. Only the current NLM lock holder may publish queue state or transcript output. Recovery is idempotent, and duplicate computation cannot create duplicate authoritative completion records.

Generation-aware transcript publication and reservation checks prevent a stale process from overwriting a newer generation’s output.

---

## 12. Transcript Output

Final transcript directory is environment-dependent and may be on NFS (`transcripts.root` config).

Existing schema is preserved:

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

### 12.1 Atomic generation-aware publication

1. Write a unique temporary file in the final transcript directory.  
2. Flush and `fsync()` the file.  
3. Parse and validate JSON (non-empty transcript unless an explicitly supported empty outcome exists; expected source identity present).  
4. Publish atomically (`os.replace` only when generation policy allows).  
5. `fsync` parent directory where supported.  
6. Reopen and re-validate the published transcript.  
7. Verify the execution is still the reservation’s current generation.  
8. Only then move the execution to `completed/`.  

Preferred generation strategy:

```text
transcripts/VIDEO_ID.json                  # current pointer / latest
transcripts/.generations/VIDEO_ID/000001.json
```

Or refuse replacement of an existing valid transcript unless the active execution’s generation is still current and explicitly superseding. A stale process must not replace a newer valid output.

---

## 13. Shared Download Admission Controller

Replace advisory `RateLimiter` with an admission controller owned exclusively by the NLM lock holder.

```text
src/local_transcribe/services/download_admission.py
```

State path:

```text
<queue>/worker/rate-limit.json
```

### 13.1 Admission sequence (before each YouTube download)

1. Confirm NLM lock is held (same process / open lock object).  
2. Read and normalize rate state.  
3. Calculate earliest permitted download time.  
4. Wait **while retaining** worker ownership.  
5. Persist the admitted attempt (atomic write) **before** launching `yt-dlp`.  
6. Re-confirm lock still held.  
7. Launch `yt-dlp`.  

Counts represent **download attempts**, not completed transcriptions. Local-file jobs do not consume YouTube download allowance.

### 13.2 State format

```json
{
  "schema_version": 1,
  "last_download_started_at": "2026-07-18T08:00:00-05:00",
  "download_attempts_this_hour": 7,
  "download_attempts_today": 28,
  "hour_window_started_at": "2026-07-18T08:00:00-05:00",
  "day_window_started_at": "2026-07-18T00:00:00-05:00",
  "blocked_until": null,
  "consecutive_throttle_failures": 0,
  "last_429_at": null,
  "last_403_at": null,
  "total_429_errors": 0,
  "total_403_errors": 0
}
```

### 13.3 Throttle handling

**HTTP 429:** record event; honor `Retry-After` when available; else exponential backoff with jitter; move execution to `retry/`; set `available_at`; set global `blocked_until`.

Suggested fallback delays:

```text
first: 5 minutes
second: 15 minutes
third: 60 minutes
later: exponential, cap 6 hours
```

**HTTP 403:** permanent private/unavailable → fail; auth required → fail with cookie/profile guidance; suspected temporary throttle → retry backoff. Do not classify every 403 as rate-limit without downloader category.

### 13.4 Guarantee

Because `ref`, `lt batch`, and `lt transcribe` only enqueue, and only the NLM lock holder runs `yt-dlp`, all commands share one admission controller regardless of which host holds the lock.

---

## 14. Downloader Hardening (daemon use)

Before using the downloader as a long-lived worker dependency, it must:

1. Remove broad `--ignore-errors` unless a specific tested case requires it.  
2. Accept a job-local scratch directory (`.../jobs/<execution-id>/`).  
3. Launch `yt-dlp` in its own process group.  
4. Stream logs rather than retaining unlimited output in memory.  
5. Apply a timeout.  
6. Terminate the process group on cancellation.  
7. Return a structured result.  
8. Classify errors (rate limited, temporary forbidden, authentication required, private, unavailable, extractor failure, network failure, postprocessing failure).  
9. Rely on admission controller having persisted the attempt before launch.  
10. Preserve enough sanitized stderr for diagnostics.  

---

## 15. CLI Changes

### 15.1 `lt transcribe`

Default (queue mode):

```bash
lt transcribe <source>
```

1. Validate source.  
2. Resolve configured queue.  
3. Create or find the source reservation.  
4. Enqueue an interactive-priority execution if required.  
5. Attempt to start the local worker service.  
6. Wait for the requested execution unless `--no-wait`.  
7. Return final transcript path or terminal error.  

Required options:

```text
--no-wait
--force
--retry
--priority
--timeout
--queue-dir
--direct
```

* `--force` creates a new execution **generation**. It must not bypass the queue.  
* `--direct` runs download/transcribe in-process **without** the queue (dev/emergency only). Must log a clear warning. Must not be the default.  
* Existing model/device options are stored on the execution’s `options` object in queue mode.  

**NFS latency note:** when waiting from a non-worker host, poll specific expected execution paths (open-to-close revalidation) rather than relying solely on directory listings; do not treat brief attribute-cache staleness as failure.

### 15.2 `lt batch`

Becomes a bulk producer:

1. Parse and normalize input.  
2. Deduplicate within the input.  
3. Resolve source reservations.  
4. Skip valid existing transcripts.  
5. Enqueue missing executions.  
6. Print enqueue summary.  
7. Optionally `--wait` for submitted execution IDs.  

`--resume` becomes unnecessary (queue state is durable). May remain temporarily as a compatibility no-op/alias with a deprecation notice.

### 15.3 Worker commands

```bash
lt worker run
lt worker run --once
lt worker run --standby
lt worker install
lt worker start
lt worker stop
lt worker restart
lt worker status
lt worker logs
lt worker doctor
```

`lt worker status` must distinguish:

```text
NLM lock acquired locally
NLM lock held elsewhere
NLM environment unavailable
queue mounted but unsafe
worker state active but lock status unknown
```

### 15.4 Queue commands

```bash
lt queue init --queue-dir PATH
lt queue path
lt queue doctor
lt queue add <source>
lt queue list
lt queue show <execution-id>
lt queue show-source <source-key>
lt queue retry <execution-id>
lt queue cancel <execution-id>
lt queue import <pending-file>
lt queue export-pending
lt queue repair
lt queue purge
lt queue stats
```

* `lt queue repair` must acquire the NLM worker lock before mutating queue state.  
* `lt queue purge` requires explicit filters (e.g. `--completed --older-than 30d`) and never deletes transcript JSON.  
* `lt queue list` filters: `--status`, `--origin`, `--limit`, `--json`.  

### 15.5 Status and report

`lt status` / `lt report` become queue-aware (and may still emit compatibility views). They must not treat `batch_status.json` as authority.

---

## 16. Background Service

User-level systemd unit:

```text
~/.config/systemd/user/local-transcribe-worker.service
```

```ini
[Unit]
Description=Local Transcribe Background Worker
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=%h/.local/bin/lt worker run --standby
Restart=on-failure
RestartSec=10

[Install]
WantedBy=default.target
```

`--standby` allows multi-host install: non-holders poll for the NLM lock.

### 16.1 Installation

`lt worker install` must:

1. Resolve the installed `lt` executable.  
2. Create the service file.  
3. Run `systemctl --user daemon-reload`.  
4. Print enable instructions.  
5. Not enable lingering automatically.  

```bash
systemctl --user enable --now local-transcribe-worker.service
# optional:
loginctl enable-linger "$USER"
```

### 16.2 Foreground fallback from `lt transcribe`

When a job is submitted and no active worker is detected:

1. Attempt to start the systemd user service if installed.  
2. Wait briefly for NLM lock activity / progress.  
3. If still none, acquire local runtime lock + NLM lock and process until the requested execution reaches a terminal state.  
4. Release locks and exit.  

Fallback still uses the queue and shared admission state (not a side-channel direct path unless the user passed `--direct`).

---

## 17. `ref-cli` Integration

`ref-cli` owns reference records. `local-transcribe` owns transcription execution.

### 17.1 Preferred enqueue flow

When transcript API retrieval is blocked or unavailable:

1. Check for a valid local transcript.  
2. Invoke the `local-transcribe` queue adapter.  
3. Enqueue with `origin = ref`, base priority 20.  
4. Record a pending transcript marker in `references.md`.  
5. Continue without waiting for local transcription.  

Adapter preference: in-process Python API if installed → `lt queue add` → append `transcript-pending.md` fallback.

### 17.2 Failure isolation

`ref` must continue when `lt` is missing, queue cannot resolve, NFS is unavailable, or the worker is stopped. Log the integration failure; preserve pending-file fallback; do not fail reference capture.

### 17.3 Completion reconciliation

```bash
ref reconcile-transcripts
```

Read pending markers, match valid transcript JSON, update only the transcript field in `references.md`, preserve order, default dry-run, support `--apply`.

`local-transcribe` must not rewrite `references.md`.

---

## 18. Legacy Pending-File Compatibility

`transcript-pending.md` remains non-authoritative after migration.

### 18.1 Import

```bash
lt queue import ~/references/transcripts/transcript-pending.md
```

Rules: ignore blanks/comments; normalize YouTube URLs; skip valid transcripts; skip sources with active reservations/executions as specified; enqueue with `origin = import`; preserve unparseable lines; back up source; rewrite via atomic replace.

### 18.2 Export

`lt queue export-pending` — human-readable report from pending/retry/processing. Report only.

### 18.3 Transition

`lt batch` may detect `transcript-pending.md` and import into the queue rather than process it directly.

---

## 19. Existing Status Compatibility

`batch_status.json` and `finished.dat` may be retained temporarily.

Rules:

* `keys/` + execution directories are authoritative.  
* Compatibility files may be generated from reservations, executions, and validated transcripts.  
* A URL in `finished.dat` without a valid transcript does not count as completed.  
* Verify-before-append behavior for `finished.dat` is preserved.  
* `status_store.py` may remain for import/compat/reporting but must not control execution.  

---

## 20. Model Lifecycle

Worker-scoped cache keyed by `(model, effective_device, effective_compute_type)`:

* Reuse when the next job matches.  
* Reload on configuration change.  
* Preserve CUDA preflight and CPU fallback.  
* Log effective device/compute type.  
* Unload the cached model after `model_idle_unload_seconds` (**default 300**, five minutes) of *model inactivity*.

Idle-unload rules (shipped):

* Idle time is measured from the cached entry's **last actual model use**; every cache `get()` — reuse included — refreshes it. It is never measured from worker start, queue poll, or loop iteration.
* The worker loop performs idle maintenance only on its "nothing claimable" path, between jobs, so an unload can never interrupt an active transcription.
* The runner that owns the cache (`ProductionJobRunner`) exposes the narrow optional hook `maybe_unload_idle()`. The worker looks the hook up duck-typed; arbitrary injected `job_runner` callables are not required to expose it and keep working unchanged.
* Unloading releases the cached model reference (freed by normal reference counting). No global CUDA/CTranslate2 reset or other GPU-wide side effect is performed.
* The next job after an eviction loads the model normally through the same cache path.
* Eviction is evaluated in the existing polling loop — no second timer or background thread.

Refactor `transcriber.py` to separate model construction from transcription and accept a reusable model or provider. Atomic transcript publication moves into the shared publisher path.

---

## 21. Logging and Status

### 21.1 Local logs

```text
${XDG_STATE_HOME:-$HOME/.local/state}/local-transcribe/logs/worker.log
```

Each job entry should include: `execution_id`, `source_key`, `generation`, `origin`, `priority`, `state`, `attempt`, `model`, `requested_device`, `effective_device`, `download_strategy`, `duration`, `error_category`, `next_retry_at`. Log NLM lock acquire/release/contention and recovery events.

### 21.2 Status output (example)

```text
Queue: /opt/md2/music/youtube/transcripts/transcription-queue
Queue ID: b6c1e0f2
NFS: vers=3 proto=tcp hard (server nas.example.internal)
Worker lock: held locally (PID 28451) | held elsewhere | unavailable
Worker state: nomnom PID 28451, execution 0190dc1a-...
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

### 21.3 Health warnings

* Configured path missing or not a directory  
* UUID mismatch  
* Unsafe mount options (`nolock`, `local_lock=*`, `soft`, `softerr`)  
* Wrong NFS version/server/export  
* Read-only mount  
* NLM / `rpc.statd` unavailable  
* More than one processing execution  
* Valid transcript exists for a still-pending execution  
* Completed execution missing or invalid transcript  
* Stale temporary files  
* Malformed rate state  
* Local-file job not accessible on this host  

---

## 22. Configuration

```text
~/.config/local-transcribe/config.yaml
```

```yaml
queue:
  path: /opt/md2/music/youtube/transcripts/transcription-queue
  expected_uuid: b6c1e0f2-0000-0000-0000-000000000000
  expected_nfs_version: 3
  expected_server: nas.example.internal
  expected_export: /exports/transcripts

transcripts:
  root: /opt/md2/music/youtube/transcripts

sources:
  shared_roots:
    - /opt/md2/music/audio-input

worker:
  poll_interval_seconds: 5
  standby_retry_seconds: 30
  stale_processing_seconds: 3600
  stale_tmp_seconds: 3600
  model_idle_unload_seconds: 300
  max_interactive_streak: 5

rate_limit:
  minimum_download_interval_seconds: 30
  max_download_attempts_per_hour: 60
  max_download_attempts_per_day: 500
  maximum_backoff_seconds: 21600

auth_profiles:
  youtube-personal:
    cookies_file: ~/.config/local-transcribe/cookies/youtube.txt
```

**Removed vs v2:** `lease_renew_seconds`, `stale_lease_seconds`, multi-path `candidates` auto-discovery.

**Status note:** the `worker:` block above documents the compiled defaults; these values are not read from `config.yaml` today. `model_idle_unload_seconds` ships as `DEFAULT_IDLE_UNLOAD_SECONDS = 300.0` in `services/model_cache.py` (see §20).

Queue and transcript roots may use NFS. Cookie files and temporary media remain local.

Resolution precedence: CLI `--queue-dir` → `queue.path` → error.

---

## 23. Security and Privacy

* Queue files may contain URLs and local paths.  
* Queue directories must not be world-writable.  
* Cookie contents never enter job JSON or logs.  
* Auth profile names only on the wire/NFS; paths resolve locally.  
* Validate job JSON before execution.  
* Reject unknown `source_type` values.  
* Do not execute shell strings from job metadata.  
* Pass downloader arguments as argv arrays.  
* Validate local file paths; reject directories and non-regular files.  
* Do not follow untrusted job-specified output paths outside approved roots without override.  

---

## 24. Required Repository Changes

### 24.1 New modules

```text
src/local_transcribe/services/queue_paths.py
src/local_transcribe/services/queue_models.py
src/local_transcribe/services/queue_store.py
src/local_transcribe/services/source_reservations.py
src/local_transcribe/services/worker.py
src/local_transcribe/services/worker_lock.py
src/local_transcribe/services/worker_state.py
src/local_transcribe/services/atomic_files.py
src/local_transcribe/services/model_cache.py
src/local_transcribe/services/download_admission.py
src/local_transcribe/services/mount_validation.py
```

### 24.2 Modify `cli.py`

* Add `queue` and `worker` Typer apps.  
* Convert `transcribe` to enqueue-and-wait; keep `--direct`.  
* Convert `batch` to bulk enqueue.  
* Stop treating `batch_status.json` as authoritative.  

### 24.3 Modify `transcriber.py`

* Separate model construction from transcription.  
* Accept reusable model / provider.  
* Atomic transcript publisher.  
* Stop using shared output dir as media scratch.  
* Structured result including effective device/compute type.  

### 24.4 Modify `downloader.py`

* Job-local scratch directory.  
* Structured metadata and categorized errors.  
* Process-group cancellation and timeouts.  
* Stream sanitized logs.  
* Remove broad `--ignore-errors`.  

### 24.5 Replace rate limiter role

New admission controller exclusively for the NLM lock holder; atomic persist before each YouTube download.

### 24.6 Deprecate `status_store.py` as authority

Retain for legacy import, compatibility generation, and reporting only.

---

## 25. Testing Requirements

Unit tests alone are insufficient. Correctness depends on server, client, mount, firewall, NLM, and NSM behavior.

### 25.1 Two-client NLM lock tests

From two NFS clients:

1. Client A opens `worker.lock` and obtains nonblocking exclusive POSIX lock.  
2. Client B attempts the same; must see contention.  
3. Client A exits without explicit unlock; B eventually acquires.  
4. Repeat with client A rebooted.  
5. Repeat with NFS server restarted (grace periods).  
6. Confirm firewalls do not block lock/statd traffic.  

### 25.2 Mount validation tests

Startup must fail for: `nolock`; `local_lock=posix`; `local_lock=all`; `soft` / `softerr`; wrong server/export; wrong queue UUID; unmounted mountpoint with local dir; read-only; NLM unavailable.

### 25.3 Queue race tests

* Two hosts enqueue the same source → one reservation, one current generation.  
* Enqueue races pending→processing.  
* Enqueue races completion.  
* Force generation races normal enqueue.  
* Stale processing recovery does not overwrite a newer generation.  
* Stale process cannot publish over a newer transcript.  

### 25.4 Worker tests

* Only one worker obtains the NLM lock.  
* Standby obtains ownership after active exit.  
* Worker never replaces the lock file pathname.  
* Multiple processing files → health warning.  
* Recovery for completed transcript + leftover processing record.  
* Recovery for invalid transcript.  
* Model cache reuse and reload.  
* Model cache idle unload after `model_idle_unload_seconds`, including worker-loop coverage that the model survives an in-flight transcription and reloads on the next job.
* Local-file shared-root and host-affinity rules.  

### 25.5 Interruption tests

Kill worker during download, transcription, transcript temp write, and after transcript publish before queue completion. Interrupt NFS during transitions. Restore and verify deterministic recovery.

### 25.6 Admission and integration tests

* Shared rate state for all origins.  
* Local files do not consume download budget.  
* Interval and hourly/daily budgets.  
* 429 / temporary 403 classification.  
* `ref` + `lt transcribe` same URL → one reservation.  
* `ref` continues when queue unavailable.  
* Pending import idempotent.  

### 25.7 Atomic publication tests

* `link()` create-if-absent for keys and pending.  
* Concurrent duplicate key publication → one winner.  
* `atomic_write_json` never leaves truncated durable files.  

---

## 26. Acceptance Criteria

1. Every normal transcription request is represented by a durable execution record.  
2. `lt transcribe`, `lt batch`, and `ref-cli` share the same queue.  
3. One permanent source reservation exists per canonical source.  
4. Duplicate submissions resolve to the existing source reservation.  
5. Only the holder of the NLM-backed POSIX worker lock may execute or modify authoritative queue state.  
6. Worker ownership does not depend on timestamps, JSON heartbeat age, or rename-to-steal.  
7. The worker lock file is never renamed or replaced while locked.  
8. All clients use NFSv3 with functional NLM and NSM support.  
9. Unsafe mounts using `nolock`, local-only POSIX locks, or soft timeout behavior are rejected.  
10. All YouTube downloads share one enforced admission controller.  
11. Temporary media stays on local worker storage.  
12. Final transcripts may be stored on NFS.  
13. Transcript output is published atomically and validated before completion.  
14. A stale execution cannot overwrite a newer generation.  
15. Worker crashes leave recoverable state.  
16. Client and server reboot recovery is tested on the actual NFS environment.  
17. The same Whisper model is reused across compatible jobs.  
18. Local-file jobs are processed only on a host that can access the source (shared root or affinity).  
19. Cookie contents never enter queue metadata or logs.  
20. Legacy files remain compatibility artifacts, not queue authorities.  
21. Duplicate computation around a failure cannot create duplicate authoritative completion records.  
22. The design does not claim absolute exactly-once execution where the underlying systems cannot provide it.  
23. Queue path is explicit; multi-candidate silent discovery is not used.  
24. Default `lt transcribe` uses the queue; `--direct` is opt-in only.  

---

## 27. Implementation Phases

### Phase 1: Atomic file foundation

Queue path configuration, UUID validation, mount validation, atomic JSON writing, atomic transcript publication, source reservations, one-file-per-execution schema. **Do not change normal CLI behavior yet.**

### Phase 2: NFSv3 worker ownership

Stable `worker.lock`, POSIX record locking via `fcntl`, NLM environment diagnostics, standby retry, worker state reporting, two-client integration tests. **No heartbeat lease or stale lock stealing.**

### Phase 3: Worker execution

Claiming, processing recovery, local scratch dirs, structured downloader results, model cache, transcript validation, terminal state transitions, generation checks.

### Phase 4: Shared download admission

Minimum interval, hourly/daily budgets, global backoff, 429 handling, temporary 403 classification, persistent `blocked_until`.

### Phase 5: CLI migration

Convert `lt transcribe`, `lt batch`, `lt status`, `lt report`. Add `lt queue` and `lt worker`. Retain `--direct` only as explicit override.

### Phase 6: Legacy migration

Pending-file import/export, `finished.dat` / `batch_status.json` generation, reconciliation against reservations, executions, and transcripts.

### Phase 7: `ref-cli` integration

Enqueue without waiting; graceful fallback when the queue is unavailable; reconcile-transcripts.

### Phase 8: Hardening

Real two-host NFSv3 race tests, client/server reboot tests, firewall validation, security review, upgrade/rollback documentation.

---

## 28. Non-Goals

The first implementation will not support:

* Multiple simultaneously active workers (single NLM lock holder only; multi-host standby OK)  
* Distributed consensus beyond NLM  
* Parallel transcription or parallel downloading  
* Web UI or remote worker APIs  
* Automatic cookie synchronization  
* Automatic modification of `references.md` by `local-transcribe`  
* Replacing the transcript JSON format  
* Storing temporary downloaded media permanently on NFS  
* SQLite or another embedded DB as authoritative queue storage  
* Application-level timestamp leases or rename-to-steal ownership  
* Multi-path automatic queue discovery  
* Automatic local-file staging (host affinity + shared roots only for v1)  

---

## 29. Final Architecture

```text
                         NFSv3 transcript environment
┌───────────────────────────────────────────────────────────────────┐
│                                                                   │
│  configured transcription-queue/                                  │
│  ├── queue.id                     (identity marker)               │
│  ├── keys/                        (permanent source reservations) │
│  ├── pending/                     (execution files)               │
│  ├── processing/                                                  │
│  ├── retry/                                                       │
│  ├── completed/                                                   │
│  ├── failed/                                                      │
│  └── worker/                                                      │
│      ├── worker.lock              (NLM POSIX lock target)         │
│      ├── state.json               (diagnostic only)               │
│      └── rate-limit.json          (admission; lock holder only)   │
│                                                                   │
│  final transcript JSON (+ optional generation history)            │
│                                                                   │
└───────────────────────────────────────────────────────────────────┘
        ▲                ▲                        ▲
        │ link() publish │ link() publish        │ NLM lock + renames
        │ keys + pending │ keys + pending        │ + validated output
   ┌────┴─────┐    ┌─────┴─────────┐    ┌────────┴────────────┐
   │          │    │               │    │ single NLM holder   │
   │ ref-cli  │    │ lt commands   │    │ (any one host)      │
   │          │    │  transcribe   │    │ systemd --user      │
   │          │    │  batch        │    │ yt-dlp              │
   │          │    │  queue        │    │ faster-whisper      │
   │          │    │               │    │ local temp audio    │
   └──────────┘    └───────────────┘    └─────────────────────┘
```

Boundary:

> Producers on any host publish durable reservations and executions atomically. Exactly one NLM lock holder, on any host, performs every download and transcription and is the sole publisher of authoritative completion state.

NFS stores durable shared files. NLM provides cooperative cross-host exclusion. The queue application owns source identity, generations, idempotent recovery, retries, admission, and artifact validation.

That division is simpler, easier to test, and safer than an application-managed stale heartbeat lease.

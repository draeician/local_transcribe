# Transcription Queue — Design Decision Summary

**Project:** `local-transcribe`  
**Related:** `ref-cli`  
**Source of truth (detailed):** [`SPEC-queue.md`](../SPEC-queue.md) (v3)  
**Branch:** `feat/queue-creation`  
**Status:** Proposed (not yet implemented)  
**Date:** 2026-07-18  
**Last aligned with:** Architectural review integrating NLM locks and source reservations  

---

## 1. What do we call this work?

| Term | Fits? | Why |
|------|-------|-----|
| **Feature** | **Primary** | New durable queue, background worker, NLM ownership, queue/worker CLI, multi-host coordination. |
| **Architectural rework** | **Secondary** | Execution path changes: producers enqueue; only the lock holder downloads/transcribes. |
| **Refactor** | **Partial only** | Internal pieces (rate limiter → admission, model cache, status compatibility). |
| **Rewrite** | **No** | `yt-dlp`, `faster-whisper`, and transcript JSON schema are reused. |

**Recommended naming:** changelog `feat: background transcription queue`; design language “feature + execution-model rework”; branch `feat/queue-creation`.

---

## 2. Problem this solves

1. **`lt transcribe` bypasses** shared orchestration and can race batch/`ref` downloads.  
2. **Rate and status files are local** to each output directory / process.  
3. **`transcript-pending.md` append** is not multi-writer safe.  
4. **Advisory rate limiter** never blocks downloads.  
5. **In-place JSON writes** and per-call Whisper loads are unfit for a multi-host NFS worker.  
6. **Temp media on NFS** wastes bandwidth and shared storage.

Desired end state:

> Producers on any host publish durable reservations and executions on NFSv3. Exactly one NLM lock holder runs downloads and transcriptions, one job at a time, with shared admission control and idempotent recovery.

---

## 3. Division of responsibility (core decision)

| Layer | Owns |
|-------|------|
| **NFSv3** | Durable shared files; hard-link and rename semantics |
| **NLM / NSM** | Cooperative cross-host exclusive worker ownership |
| **Queue app** | Source identity, reservations, generations, retries, admission, validation |

This replaces the v2 idea of rebuilding leases with JSON heartbeats and rename-to-steal.

---

## 4. Guiding principles

| Principle | Why |
|-----------|-----|
| Files authoritative | Fits NFS; inspectable; no DB server |
| One reservation per source + one file per execution | Lifetime dedup; clear recovery |
| CLI submits; worker executes | Shared rate limits and serialization |
| NLM POSIX lock for ownership | Uses real NFSv3 locking instead of clocks |
| Explicit queue path + UUID | Prevents split-brain path discovery |
| Temp media local; finals on NFS | Cost and durability split correctly |
| Validate before complete | No false success records |
| Idempotent recovery, not absolute exactly-once | Honest about crash/partition limits |
| Legacy files are compatibility only | Migration without big-bang |

---

## 5. Major design decisions

### 5.1 File-based queue, not SQLite or a broker

**Why:** Transcripts already live on NFS; home multi-host use is “mount the share.” SQLite locks are unreliable across NFS clients.

### 5.2 NLM-backed POSIX lock (not JSON lease)

**Decision:** Stable `worker/worker.lock`; `fcntl.lockf(LOCK_EX|LOCK_NB)`; hold fd for worker lifetime; never rename/replace the lock file while held.  
**Why:** NFSv3 locking is NLM-sideband; application timestamp leases fight cache incoherence (RFC 1813) and reinvent recovery poorly.  
**Diagnostic only:** `worker/state.json` heartbeats — never ownership authority.  
**Removed:** `lease_renew_seconds`, `stale_lease_seconds`, rename-to-steal, JSON fencing.

### 5.3 Fail-closed NFSv3 mount validation

**Decision:** Reject `nolock`, `local_lock=posix|all`, `soft`/`softerr`, wrong server/export/UUID, RO mounts, missing NLM/statd where detectable. Prefer `hard`, `vers=3`, TCP.  
**Why:** Soft mounts and local-only locks silently break cross-host exclusion or data safety.

### 5.4 Permanent source reservations (`keys/`)

**Decision:** `keys/<source-key>.json` holds `current_execution_id` + `generation`. Executions live as separate files named by execution id.  
**Why:** After `pending → processing`, a pending path no longer blocks re-enqueue. Claim-time dedup alone is insufficient.  
**`--force`:** new generation under the same reservation, not a second key.

### 5.5 Hard-link publication for create-if-absent

**Decision:** tmp write + `os.link()` for first reservation and pending execution publish.  
**Why:** `rename()` overwrites destinations; `link()` fails with `EEXIST`.

### 5.6 Same-filesystem rename for state transitions

**Decision:** Only the lock holder moves executions between state dirs.  
**Why:** Atomic claim and transitions without copy/delete races.

### 5.7 Explicit queue path (no candidate search)

**Decision:** Config `queue.path` + `expected_uuid` (+ optional NFS identity). CLI `--queue-dir` override. No `~/…` then `/opt/…` fall-through.  
**Why:** Independent discovery per host causes split-brain queues with different UUIDs.

### 5.8 Generation-aware transcript publish

**Decision:** Atomic tmp → validate → publish; refuse stale generations overwriting newer valid output; optional `.generations/` history.  
**Why:** Stale workers after lock recovery must not clobber newer results.

### 5.9 Local-file portability

**Decision:** Shared roots and/or `required_host` affinity; auth **profiles** instead of cookie paths in job JSON.  
**Why:** Home paths and cookie files are host-local; jobs must be portable or explicitly pinned.

### 5.10 Admission controller, not advisory warnings

**Decision:** Persist admitted download attempt **before** `yt-dlp`; budgets and `blocked_until` under lock.  
**Why:** Only hard admission on the single lock holder guarantees shared pacing.

### 5.11 Priority with fairness

**Decision:** Interactive base priority high, but cap interactive streak (default 5) or age bonus so batch/`ref` are not starved. Never preempt the running job.

### 5.12 Default queue; `--direct` opt-in

**Decision:** `lt transcribe` enqueues and waits by default; `--direct` is emergency/dev only.  
**Why:** Default path must not reintroduce concurrent downloads.

### 5.13 Soften exactly-once claims

**Decision:** Guarantee cooperative single publisher + idempotent authoritative records; do **not** claim Whisper/download never runs twice after partitions/reboots.  
**Why:** External work can continue after lock recovery; correctness is about durable outputs, not CPU uniqueness.

### 5.14 Downloader hardening for daemon use

**Decision:** Process groups, timeouts, structured errors, no broad `--ignore-errors`, local scratch per execution.  
**Why:** Batch one-shot assumptions are unsafe under a long-lived worker.

---

## 6. What stays vs what changes

| Stays | Changes |
|-------|---------|
| Transcript JSON shape | Who may publish (NLM lock holder only) |
| yt-dlp + faster-whisper | When they run (after claim + admission) |
| `lt` CLI entrypoint | Default `transcribe`/`batch` = enqueue |
| Model/device options | Stored on execution; auth via profiles |
| Verify-before-complete | Generation-aware atomic publish |
| File-based operations | Layout: keys + executions + NLM lock |

---

## 7. Correctness model (short)

```
Producers (any host)
  → normalize source_key
  → link() keys/<source-key> if absent
  → link() pending/<execution-id>
  → resolve duplicates via existing reservation

Worker (exactly one NLM lock holder)
  → fcntl exclusive lock on worker.lock (stable fd)
  → recover processing/
  → rename pending → processing
  → verify generation still current
  → admit download (YouTube) → local scratch → transcribe
  → atomic generation-aware transcript publish
  → completed/ + reservation consistency
```

Ownership = NLM. Identity/dedup = reservations. Safety of artifacts = atomic publish + generations.

---

## 8. Implementation phases (summary)

1. Atomic files, paths, UUID, mount checks, reservations  
2. NLM worker lock + standby + two-client tests  
3. Execution loop, recovery, model cache, hardened downloader  
4. Download admission  
5. CLI migration (`queue` / `worker` / default enqueue)  
6. Legacy import/compat  
7. `ref-cli` adapter  
8. Multi-host reboot/race hardening  

---

## 9. How to talk about it

| Audience | Phrase |
|----------|--------|
| Changelog Added | Background transcription queue and NLM-locked worker |
| Changelog Changed | Default `lt transcribe`/`batch` submit through the queue |
| Design review | NFSv3 spool + NLM ownership + source reservations |
| Avoid | “JSON lease” / “rename-to-steal ownership” / “exactly-once Whisper” |

---

## 10. Related documents

| Document | Role |
|----------|------|
| [`SPEC-queue.md`](../SPEC-queue.md) v3 | Full specification |
| This file | Decision summary |
| Architectural review (session 2026-07-18) | NLM / reservation revisions accepted into v3 |

---

## 11. One-sentence summary

This is a **feature** that adds a durable NFSv3 transcription queue and a single **NLM-locked** worker, with permanent source reservations and idempotent publication — an **execution-model rework**, not a pure refactor and not a from-scratch rewrite of transcription itself.

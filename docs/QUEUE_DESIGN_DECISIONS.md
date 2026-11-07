# Transcription Queue — Design Decision Summary

**Project:** `local-transcribe`  
**Related:** `ref-cli`  
**Source of truth (detailed):** [`SPEC-queue.md`](../SPEC-queue.md) (v3)  
**Shipped in:** `0.5.0` (merged to `main`)  
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

---

## 12. Queue list reporting corrections (2026-11-03)

Task 037 review follow-up. Two edge cases were corrected in `lt queue list`:

| Finding | Decision |
|---------|----------|
| A UUID selection plus a nonzero `--offset` could silently hide the selected record. | **Reject** the combination with an actionable error. UUID selection is a single-record lookup, so nonzero offsets are incompatible; normal status/all-record pagination is unchanged. |
| `--origin` was accepted but ignored for UUID selection. | **Apply** the origin filter to the selected record. A mismatch produces an explicit empty result rather than silently returning the record. |

The governing rule is that filtering and pagination options must never be silently ignored. Implementation is in `src/local_transcribe/cli_queue.py`; regression coverage is in `tests/test_cli_queue.py`.

---

## 13. Deno is probed only on download paths (task 038, 2026-11-04)

**Decision.** The root Typer callback no longer runs any startup probe. Deno
availability is checked where a YouTube download actually happens:
`lt transcribe <URL>` (queue or `--direct`) and the legacy `--direct` batch
pipeline, which keep the prerequisite warning. Local-file transcription, all
`lt queue *` reporting/management commands, `lt worker *`, `lt cookies *`,
`lt version`, and help output never touch the probe.

**Why.** The callback-level check ran for every subcommand, so operators on a
host without Deno saw an install-guidance block before queue statistics, list
output, or version strings. That noise implied the queue depended on Deno, which
it does not: producers only write reservation/execution JSON and the worker owns
downloading. A missing Deno must never interfere with inspecting or managing
queue records.

**Preserved.** `lt doctor` still reports "Deno Runtime (YouTube)" as an optional
diagnostic check; `lt update` still fails clearly when Deno is not on PATH. No
package installation or system configuration was added to any code path. The
module-level `_startup_warnings_shown` flag was removed with the startup probe it
guarded.

**Verification.** `tests/test_deno_startup_warnings.py` simulates a host without
Deno and asserts (a) queue reporting/version/help output carries no Deno warning
and invokes no probe, (b) YouTube paths still warn, (c) local audio enqueue does
not probe, (d) `lt doctor` keeps the diagnostic. The suite fails on `origin/main`
(2 failures reproducing the bug) and passes here.

---

## 14. Producer paths never probe Deno, including `--no-wait` (task 039, 2026-11-04)

Within `lt transcribe <URL>`, the probe now
runs only when this session actually depends on a YouTube download: it moved from
the command entry to the queue **wait** path (`--no-wait` returns before it) and to
the `--direct` URL branch. Consequence: `lt transcribe <URL> --no-wait` is quiet, so
every producer-only invocation (queue add, batch enqueue, local files, `--no-wait`)
never probes Deno; a waiting or in-process YouTube transcription still warns.
Coverage: `test_transcribe_url_no_wait_does_not_probe_deno`,
`test_transcribe_url_wait_warns_without_deno`, `test_transcribe_direct_url_warns_without_deno`.
Version synchronized to **0.8.0** across `pyproject.toml`,
`src/local_transcribe/__init__.py`, `project_spec.md`, `CHANGELOG.md`, and
`docs/WHATS_NEW.md`; the release decision (merge/tag) remains with review.

---

## 15. Queue clients never probe Deno; queue-list tests isolate XDG (task 039 review corrections, 2026-11-05)

**Decision 1 — queue mode is Deno-free end to end.** The client Deno probe was
removed from the queue **wait** path as well as the producer paths. In queue
mode the remote worker performs the download, so `lt transcribe <URL>` (waiting),
`lt transcribe <URL> --no-wait`, `lt batch`, `lt batch --wait`, local files, and
all `lt queue *` commands never probe Deno. The probe now runs only where this
process downloads YouTube audio itself: `lt transcribe <URL> --direct` and
`lt batch --direct`. `lt doctor` keeps its optional diagnostic and `lt update`
keeps its hard requirement.

**Why.** Probing on the client while waiting was still wrong: the client only
observes queue state and may be a different host from the worker. A missing
client Deno install cannot affect a remote worker's download, and warning there
reintroduced the misleading implication that the queue depends on Deno.

**Actionability.** The wait outcome continues to print the worker's failure as
`category: detail` (for example `temporary_forbidden: ERROR: HTTP Error 403`),
and `lt queue show <UUID>` retains the stored error. Operators diagnose the
worker host with `lt doctor` / `lt queue doctor`; no worker execution behaviour
changed.

**Decision 2 — queue-list tests own their XDG configuration.** The task-037
queue-list tests now install an autouse fixture that points `XDG_CONFIG_HOME`
and `XDG_STATE_HOME` at the test's temporary directory. Application queue
identity validation is unchanged: an explicitly configured `expected_uuid` is
still enforced. The fixture prevents an operator's production config from
rejecting temporary queues with a UUID mismatch, and the tests pass under both
empty and production-shaped external configuration.

Coverage: `test_transcribe_url_wait_does_not_probe_deno`,
`test_transcribe_url_wait_surfaces_worker_failure`,
`test_batch_queue_wait_does_not_probe_deno`, plus the existing direct-path
warning tests and the queue-list suite under the autouse XDG fixture.
---

## 16. Ruff verification gate restored (task 040, 2026-11-06)

**Decision.** Correct all 413 pre-existing Ruff findings on `main` without
changing the enabled rule set, adding blanket suppressions, or weakening
`pyproject.toml`.

**Why.** Ruff 0.17's default rule set reports the repository's existing style and
typing debt (413 findings at `25b89f1`), so a zero-finding gate cannot be
restored by configuration changes without hiding real findings. The cleanup
therefore fixes the code and keeps the gate meaningful.

**Typer options.** Path-typed options now use `typing.Annotated`, with the
`typer.Option`/`typer.Argument` call inside the annotation and the actual default
on the parameter. This removes the `B008` false positive without suppressing it
and preserves every option name, default, and required flag.

**Exception handling.** Blind handlers were narrowed to the exceptions each call
can actually raise, and two best-effort probes use `contextlib.suppress`. Two
intentional boundaries — the `lt doctor` command and the per-video batch loop —
keep a targeted, justified `# noqa: BLE001` so a single failure still cannot
abort diagnostics or the batch. No blanket `noqa` was introduced; four
pre-existing broad-catch suppressions were removed.

**Small semantic corrections.** Invalid config root/`queue` values now raise
`TypeError` (the producer fallback in `queue_api` catches it as well); missing
auth profiles still raise `ValueError`. `WorkerLock.__enter__` annotates `Self`
imported under `TYPE_CHECKING`, which keeps Python 3.10 imports working without a
new runtime dependency. The duplicate CLI callback was renamed `app_callback`;
the console-script entry point remains `main`.

**Verification.** `uvx ruff check --config pyproject.toml src tests` → **0
findings** (baseline 413). Full suite: 298 passed, 11 skipped; the only baseline
difference was the documented concurrency timing flake. Version remains `0.7.0`;
no dependency or rule-config change.

---

## 17. Final v0.8.0 integration (task 041, 2026-11-06)

**Decision.** Combine tasks 039 and 040 on
`chore/0.8-final-integration` from `origin/main` @ `25b89f1` using two
`--no-ff` merge commits. No squash, rebase, or history rewrite is used, so both
reviewed lines remain individually auditable.

**Conflict resolution principle.** Conflicts are resolved by combining both
sides rather than preferring either branch:

- `__init__.py` keeps `__version__ = "0.8.0"` **and** the
  `contextlib.suppress` ROCm preload from task 040.
- `cli.py` keeps the Deno-free queue/producer paths from task 039 **and** the
  `app_callback` rename from task 040; the console-script entry point stays
  `main`.
- `cli_queue.py` keeps 039 positional `STATUS|UUID` filtering with default
  `limit=60`, `offset=0`, `sort=oldest` **and** 040 `Annotated` Typer options.
- `queue_store.py` keeps 039 `query_executions` filtering/sorting/pagination
  under 040's PEP 604 typing.
- `HANDOFF.md` retains both task histories.
- The Decision Journal is append-only: 039 §12–§15 are unchanged and the task
  040 Ruff section is renumbered from §12 to §16 so section numbers stay unique.

**Markdown hygiene.** Two lines introduced by task 039 used Markdown two-space
hard breaks, which `git diff --check` flags as trailing whitespace. They were
converted to CommonMark backslash hard breaks on the integration branch so the
diff gate is clean without changing rendering.

**Verification.** On the combined branch: targeted queue/Deno suite **99
passed**; full suite **342 passed, 11 skipped** under isolated XDG; Ruff **0
findings**; `uv build` produced **0.8.0** wheel and sdist; an isolated pipx
install reported **0.8.0**; disposable-queue operations verified positional
status filtering, UUID selection, stable pagination, and defaults
`limit=60/offset=0/oldest-first`. The host has no Deno, yet queue
inspection/enqueue stayed quiet while `lt transcribe <URL> --direct` still
emitted the Deno warning. No tests or Ruff configuration were weakened.

---

## 18. Three-host upgrade preflight is read-only and artifact-first (task 043, 2026-11-07)

**Decision.** Treat the 0.8.0 rollout to `nomnom`, `minion-26`, and `aether` as
a separate, explicitly authorized task. Task 043 only collects evidence and
proposes commands; it performs no install, service action, queue mutation, or
configuration change. Rollback artifacts must exist **before** any host is
upgraded.

**Why.** The three hosts are not interchangeable. They differ in OS, Python,
mount version, installed provenance, service role, and hardware. `aether` is the
sole NLM lock holder and is actively processing a live queue; `minion-26` runs
`ref-api` but no worker and mounts the queue over NFSv4.2; `nomnom` has no
`local-transcribe` install at all. A single generic upgrade command would
silently break at least one of them.

**Provenance is a prerequisite, not a detail.** `aether`'s 0.7.0 install points
at the deleted `file:///tmp/lt-branch`, so there is no on-host rollback wheel.
`minion-26`'s 0.7.0 install points at commit `25b89f1`, so a reproducible 0.7.0
wheel can be rebuilt from that commit. The preflight therefore requires an
installed-tree tarball on `aether` and a rebuilt wheel on both hosts before
cutover.

**ROCm injection is state, not dependencies.** `aether` carries injected
`ctranslate2 4.8.3`, `rocm-sdk-core`, and `rocm-sdk-libraries-gfx1151` packages
plus a preserved wheel cache. Because 0.8.0 has no dependency changes versus
0.7.0, the upgrade installs only the main package with `--no-deps` into the
existing venv and verifies the injected set afterwards. Recreating the venv
without re-injecting would silently downgrade the GPU path to CPU.

**Mount version gates roles.** `local-transcribe` expects NFSv3. `minion-26`
mounts `vers=4.2`, so it cannot be a worker and `queue doctor` will reject it.
Producer-only enqueue on NFSv4.2 is not validated by the tooling and is not
approved here. Remounting is a privileged, separately approved change.

**Ordering.** Rollback artifacts → `minion-26` (no worker, lowest risk) →
`aether` (idle window, sole lock holder) → `nomnom` (fresh install only if
needed). `ref` needs no upgrade: both `ref-api` and the pipx CLI already run
`1.6.12b1.dev129+g80c6e3791` from the audited wheel recorded at `5159878`.

**Explicit non-goals.** No worker is created on `nomnom` or `minion-26`. No
queue counters are reset. No archive files are edited. No `lt update`. No
release, tag, merge, or branch cleanup.

---

## 19. Review correction: task 043 gate failed; rollback is artifact-first (2026-11-07)

**Correction.** Section 18's conclusion that the required gate passed, and the
move of task 043 to `review/`, were wrong. The full suite fails on the branch
**and** on untouched `origin/main`. Task 043 is back in `wip/` as blocked, with
a local-only WIP marker, until the required checks pass honestly.

**Gate is failed, not passed.** In an isolated venv (Python 3.12.3, pytest
9.0.3) with isolated `XDG_*`/`HOME`, `python -m pytest -q` gives **7 failed,
335 passed, 11 skipped** in the default environment on ext4, on both branch and
baseline; with `NO_COLOR=1` on ext4 it gives **1 failed, 341 passed, 11
skipped**, again on both. A tmpfs run (1 failure on branch, 0 on baseline) is
diagnostic only and must not be cited as a pass. Three distinct classes:
six ANSI-output failures from `Console(force_terminal=True)`; one ext4
foreground-wait race in `test_wait_completed_via_foreground` caused by
`atomic_state_transition()` renaming before writing metadata; and one
nondeterministic concurrency flake. Baseline reproduction explains the failures
but does not make the gate pass. Fixing them is a separately authorized code
task; this documentation task changes no source or tests.

**Artifacts before upgrade.** Aether's rollback baseline is the captured
installed bytes (82 files / 876,760 bytes, source hashes, dist-info, shims,
interpreter, pipx metadata, injected ROCm packages, ROCm cache), not a rebuilt
0.7.0 wheel. A rebuilt wheel is not equivalent because its version matches;
equivalence must be proven by comparing source hashes and dist-info separately
from generated caches and metadata. Restoration uses a clean fresh venv rather
than extraction over upgraded site-packages, and must re-inject the pinned ROCm
packages. Restoration rehearsal is required future staging work and was not
performed.

**One version-aware upgrade method.** Clean replace with the exact pinned wheel:
`pipx uninstall local-transcribe` → `pipx install <0.8.0 wheel> --python
<pinned>` → re-inject pinned ROCm on Aether. All preservation checks precede
any production restart; explicit abort conditions apply; versions alone are not
a gate. The worker is stopped first, then the lock and `processing/` are
checked, to avoid the race between observing an idle worker and stopping it.
`worker.lock` contents and heartbeat are advisory only; `lslocks` proves lock
ownership. Historical ABRT/OOM events are separated from current failures and
are bounded evidence, not a new troubleshooting task.

**Host roles.** Minion's `ref-api`/`ref-cli` already match the candidate and stay
unchanged; `--queue-dir` does not override `expected_uuid`, so the `lt` fallback
cannot reach the production queue until the config is explicitly corrected, and
NFSv4.2 producer compatibility remains unresolved (no worker, no remount).
Nomnom's reported config disappearance is unsubstantiated (the listing belonged
to `~/.config/ref/`), the directory was not recreated, and a fresh CLI install
is a separate decision. Aether's queue progress is proven by full execution IDs
plus transcript files and hashes, not by lock-file contents.

**Status.** Blocked. A pushed blocked checkpoint is not approval; the task moves
to `review/` only when all required checks and correction criteria pass.

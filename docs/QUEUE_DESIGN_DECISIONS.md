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
## 20. Pre-deploy gate remediation: atomic terminal metadata and generation claims (task 044, 2026-11-07)

**Numbering.** Sections 18 and 19 are reserved by the task-043 preflight
correction (`b6d504f`, not merged here). This section starts at 20 so the two
branches can be merged without renumbering.

**Terminal metadata is published with the state entry, not after it.**
`atomic_state_transition()` previously renamed `src` to the destination and then
wrote the final metadata. A concurrent reader could see the destination with
stale `processing` metadata and report `completed` with no `output_path` (the
ext4 foreground-wait failure). The primitive now writes the final document to a
unique temp, fsyncs it, and hard-links it to the destination in one step, then
unlinks the source. Directory membership remains the authority, collisions still
fail closed, and a crash between link and unlink leaves both names for recovery.
`locate_execution()`/`wait_for_execution()` additionally refuse to trust a
terminal directory entry whose embedded status does not match that directory,
which protects against legacy or interrupted entries.

**Recovery validates the proposed successor, then deletes.** When a crash leaves
two names for one execution, recovery must not choose a winner from filename
existence alone. `_recover_duplicates()` parses every same-named record, checks
JSON/schema, execution id against the filename, embedded status against the
directory, source identity, generation, `max_attempts`, and attempt counts, and
only then selects the unique reachable transition sink. A valid chain must have
exactly one sink and every record must reach it; malformed, conflicting, cyclic,
or unreachable copies are left intact and logged as
`Duplicate recovery left execution … intact`. `recover_processing()` and
`promote_retries()` skip unresolved ids so repeated recovery cannot lose records
or create duplicate execution. Valid transitions keep the earlier generation
safety: `pending → processing` increments attempts, `processing → retry/failed`
requires an error, cancellation preserves the pre-claim attempt count, and a
retry promotion preserves the retry's attempts.

**`keys/<source>.json` remains the sole reservation authority.** The earlier
`.advance` claim ledger is withdrawn: it created a second durable authority that
existing direct key-file readers and the ref reconciler do not consult.
`read_reservation()` now reads only `keys/<source-key>.json`, as SPEC §5.1 and
§6.4 require. Concurrent generation advances and repairs are serialized with a
short-lived advisory lock (`fcntl.lockf`) plus a per-path in-process thread lock
under `.locks/`. Lock files are coordination only, contain no queue state, and
are never removed; removing a lock file after release would let a waiter on the
old inode race a new opener on a new inode. A writer that bypasses the lock (an
older client) simply leaves its document in the keys file, and both the
application and direct readers observe that same document. Compare-and-swap
helpers re-read after writing and report a lost race instead of overwriting a
newer pointer.

**Claim and cancellation share one execution lock.** `worker.claim_execution()`
and `QueueStore.cancel_pending()` both take
`.locks/executions/<execution-id>.lock`. The first transition wins. If the claim
publishes `processing/` first, cancellation fails closed with a
processing-specific `QueueStoreError` and never publishes a contradictory
`cancelled/` record. If cancellation publishes `cancelled/` first, the claim
sees no pending source and returns `None`, so the worker does not execute the
job. An interrupted cancel that left both `pending/` and `cancelled/` is
resolved only when the cancelled successor validates against the pending record;
otherwise it raises `AtomicFileError` and leaves both files for inspection. No
sleep-based synchronization is used.

**Incomplete-reservation repair uses the keys authority.** After a forced
generation loses its pending execution record, a normal enqueue repairs the
pointer with `try_update_current_execution()` under the reservation lock. The
replacement becomes the current execution at the same generation, stays
claimable, and is not cancelled by an earlier same-generation claim. The repair
is verified through the real `QueueStore` lifecycle (enqueue → force → delete the
forced pending record → enqueue → claim).

**ANSI tests assert plain text.** Rich's `Console(force_terminal=True)` styles
captured output and can split literal substrings. The six affected tests strip
CSI/SGR escapes before asserting; every behavioral assertion and exit-code
check is unchanged. This is a test correction, not a change to CLI styling.

**Verification and release.** The fix is a patch release: metadata, changelog,
and release notes are synchronized to **0.8.1**. On the corrective tree the
targeted suite passed **134**, the full suite passed **368 passed, 11 skipped,
1 warning** in both default and `NO_COLOR=1` configurations, and the predefined
50-run ext4 concurrency stress passed **50/50**. Ruff, `compileall`, package
build, isolated CLI smoke, and an isolated pipx 0.8.0 → 0.8.1 upgrade all
passed. The new assertions fail **13 of 16** on the rejected source `fe18821`
(3 pass), proving they exercise the defects. Real NFSv3/NLM and GPU checks were
not run (no production queue or GPU test); the NFS tests skipped without
`LT_NFS_QUEUE_DIR`. No tag, publish, merge, deployment, or production
configuration change was performed. Local ext4 passes do not prove multi-host
NFS/NLM behavior.

---

## 21. Unresolved recovery excludes execution across selection and claim (task 044 correction, 2026-11-08)

**Decision.** Recovery's "unresolved" result is no longer only a warning emitted
by `recover_processing()`/`promote_retries()`; it is a live eligibility rule for
the worker lifecycle. An execution id with more than one state record (a
pending plus a retry, processing, or terminal counterpart, including a
malformed or conflicting copy) is ineligible even when the pending JSON itself
parses. Recovery leaves every conflicting copy intact, and both selection and
claim fail closed on it.

- `select_next_execution()` validates each pending document (filename,
  embedded status, schema, identity, generation, attempts) and excludes ids
  with any counterpart **before** priority ordering. A blocked high-priority
  pending record therefore cannot starve a healthy lower-priority job.
- `claim_execution()` re-loads the pending record under the shared execution
  lock, rechecks for any counterpart, and compares the caller's snapshot
  (source key, generation, attempts) with the record it is about to claim. A
  conflicting copy that appears between selection and claim is therefore
  excluded, and a stale snapshot is never claimed.
- A queue containing only blocked executions reaches the normal idle path
  (idle model maintenance, then the configured poll interval) instead of a
  tight select/claim-failure loop. `once=True` returns without processing.

No record is deleted, no attempt count is reset, and no winning state is
invented to make progress. Conflicting evidence remains for inspection.

**Compatibility correction (supersedes the readback wording in §20).** The
advisory lock coordinates cooperating writers only. The post-write readback in
`try_advance_generation()`/`try_update_current_execution()` detects a race that
happens before the readback (including a non-cooperating write that lands
inside the locked section), but it cannot detect a non-cooperating older writer
that writes `keys/<source>.json` after the readback. Section 20's statement
that the helpers "report a lost race instead of overwriting a newer pointer"
must be read with that limit. Direct key-file readers remain compatible with
the application because `keys/<source>.json` is still the sole authority, but
**mixed-version writer operation is an unresolved deployment constraint**:
older writers must not run concurrently with this version. No queue migration,
ref change, or production change is made to resolve it.

**Verification.** `tests/test_worker_unresolved_lifecycle.py` drives the real
`run_worker` lifecycle with a controlled job runner and disposable queues for:
malformed retry, conflicting retry generation/identity, malformed/conflicting
terminal, conflicting processing, a blocked high-priority job beside a healthy
pending job, a queue containing only unresolved jobs (bounded idle wait), and a
conflicting copy appearing between selection and claim. On the reviewed source
`60f774f` all nine cases failed (executed the unresolved job, starved the
healthy job, or entered a 201-selection tight loop). Details and full gate
results are recorded in task 044 and `HANDOFF.md`.

---

## 22. Task 044 approved and merged (2026-11-08)

**Approval.** ChatGPT approved feature head
`084d212364cc1e2c8cdbb8de9fd89baa08cde328` for the task-044 merge/cleanup
phase. The implementation was not redone.

**Final pre-merge gate.** On the approved feature tree (ext4 worktree,
unprivileged `nomnom`, isolated `HOME`/`XDG_*`/`TMPDIR`): canonical Ruff **All
checks passed**; full pytest **377 passed, 11 skipped, 1 warning**; `git diff
--check origin/main...HEAD` clean; `0.8.1` consistent across `pyproject.toml`,
`src/local_transcribe/__init__.py`, `project_spec.md`, `CHANGELOG.md`, and
`docs/WHATS_NEW.md`; secret scan of the branch delta **no matches**. Evidence:
`~/.local/share/local-transcribe/evidence/20261108T-gate044-merge/`.

**Merge intent.** Merge the complete feature branch into `main` with a normal
non-squash `--no-ff` merge. No history is rewritten or squashed.
`bff5ca6` (atomic terminal metadata), `e62b383` (claim/cancel serialization and
validated recovery), `6aff283` (unresolved-recovery exclusion), and the task
closure remain ancestors of `main`. Task 044 moves from `review/` to `done/`.

**Deployment is still not authorized.** The merge does not authorize a tag,
release, package publication, deployment, service action, or production change.
Production remains blocked by task 043 prerequisites (artifact staging, rollback
rehearsal, Minion config/identity, NFSv4.2 producer decision) and by the
mixed-version writer constraint in §21: older producers must not write
`keys/<source>.json` concurrently with 0.8.1.

## 23. Task 043 refresh: 0.8.1 artifact-first rollback and mixed-version cutover rule (2026-11-08)

**Candidate moved to 0.8.1.** The task-043 candidate is now `local_transcribe`
**0.8.1** at `f147340f9c6e4f36da10618c7abb947bd491fccb`. The task branch was
reconciled by a **normal merge** of `origin/main` into the branch (no rebase, no
force-push), preserving both histories; the head is
`1343b3badef6c199840a7451930ef892ed6fd95f`. The earlier 0.8.0 / `abdedbe`
candidate and its failed-gate conclusions are retained as history and marked
superseded, not deleted.

**Artifact built from the exact release commit.** The deploy artifact was built
from a detached worktree at `f147340`, not from the docs branch, so the shipped
bytes correspond to the release commit. Wheel
`local_transcribe-0.8.1-py3-none-any.whl` SHA256
`3b05384388bad18a1d2bdb3eadd2838c83256bda047eaf3fee04b76f4a16ff3d`; sdist
`b79124cf0b236e970c362bca6e4c6b5118f2c36438a65983e8da563fd9844004`. A later
verification build from the reconciled tree is content-identical (48/48 wheel
members) but has a different archive hash because wheel files embed timestamps;
the **staged** wheel remains the deployment artifact.

**Rollback is captured-bytes-first, not rebuilt-wheel-first.** A rebuilt 0.7.0
wheel (`bc6eae40...`) is used only as a representative rehearsal baseline and is
not claimed to equal installed production bytes. The authoritative rollback
source is the captured installed 0.7.0 tree (Aether
`62d4a74e...`, Minion `28e1e495...`) plus, on Aether, the preserved CTranslate2
ROCm wheel (`f4790b7c...`) and the ROCm SDK installed-trees tarball
(`12a1c8be...`, 890,062,815 bytes, 3,359 files). Both hosts rehearsed restoring
those captured bytes and then upgrading to 0.8.1 in disposable environments.

**Two rehearsal corrections are now required procedure.** First, `pipx inject`
**must use `--force`** when replacing the PyPI CPU `ctranslate2` with the ROCm
wheel; without it pipx reports "already injected" and leaves the CPU build in
place. Second, the HF `faster-whisper-tiny` cache is Xet-backed (`model.bin` is a
symlink to an out-of-directory blob), so offline rehearsals must copy a **flat**
model directory; copying only the model directory yields
`IncompleteSnapshotError`. The Aether ROCm rehearsal passed all four stages
(0.7.0 → 0.8.1 → 0.7.0 → 0.8.1) with `gpu_backend=amd-rocm`, one GPU, and a
non-empty transcript each time; production was verified untouched afterward.

**Mixed-version writer rule.** `keys/<source-key>.json` is the sole reservation
authority and is written only through `QueueStore.enqueue`/force/repair and the
`source_reservations` helpers. The worker claim path only reads reservations.
On the current hosts the only `keys/` writer is the Minion `lt` (manual and via
ref-api's external-CLI fallback); Aether's worker is a reader because
`watch_legacy_pending: false`; Nomnom has no producer. Therefore the cutover is:
**upgrade the Aether worker first** (it reads 0.7.0 records fine — a 0.8.1 scan
of 8,290 keys and 6,481 state records found 0 errors), then **quiesce Minion
producers, replace Minion `lt`, and restart `ref-api`**. A 0.7.0 producer must
never write concurrently with a 0.8.1 producer; do not add a second producer
host until it is 0.8.1, and do not run admin writes (`repair`/`import`/`force`)
during the window.

**Minion NFSv4.2 is acceptable for the producer role only.** `queue add` resolves
the path/UUID and publishes atomically; it does not call the worker's NFSv3/NLM
mount gate. NFSv3/NLM remains required for the worker role. Minion keeps
`expected_nfs_version: 3` as a deliberate worker-role guard even after fixing the
stale `queue.path`/`expected_uuid`, because producers ignore that field and the
guard prevents accidentally starting a worker on NFSv4.2. No production producer
write was made during the read-only phase; the post-cutover test URL is required.

**Gates and authorization.** The refreshed tree passed canonical Ruff, full
pytest (377 passed, 11 skipped in both color modes on ext4), diff check, version
consistency, build verification, and secret scan. Task 043 moves to `review/`.
Production cutover — service stop/start, pipx replacement, live config edit, or
a production test enqueue — remains **not authorized** and requires the explicit
window described in the task record.

## 24. Task 043 approved and merged as a preflight record (2026-11-08)

**Approval.** ChatGPT approved the task-043 preflight merge at remote feature
head `1758e4b9a4df9753a3d4a5b248dadb425f9120e4`, based on `origin/main`
`f147340f9c6e4f36da10618c7abb947bd491fccb`. The approval covers only the final
pre-merge gate, the `review` → `done` transition, a normal non-squash merge into
`main`, pushing `main`, branch/worktree cleanup, and lifecycle documentation. It
does **not** authorize production deployment.

**Final pre-merge gate (fresh, not reused).** On `nomnom`, unprivileged, in an
ext4 worktree with a fresh venv: canonical Ruff **All checks passed**; full
pytest **377 passed, 11 skipped, 1 warning**; `git diff --check` and
`git diff --check origin/main...HEAD` clean; version **0.8.1** consistent across
`pyproject.toml`, `src/local_transcribe/__init__.py`, `project_spec.md`,
`CHANGELOG.md`, and `docs/WHATS_NEW.md`; branch-delta secret scan reviewed with
no credential-shape match. Evidence:
`~/.local/share/local-transcribe/evidence/20261108T-gate043-merge/`.

**What is being merged is evidence, not a deployment.** The deploy artifact is
the 0.8.1 wheel with SHA256
`3b05384388bad18a1d2bdb3eadd2838c83256bda047eaf3fee04b76f4a16ff3d`. The Minion
rehearsal passed from both the representative wheel and the captured installed
0.7.0 tree, including upgrade, rollback, and re-upgrade on disposable queues.
The Aether ROCm rehearsal passed 0.7.0 → 0.8.1 → 0.7.0 → 0.8.1 plus a
captured-bytes restore, with `gpu_backend=amd-rocm`, one GPU, and a non-empty
transcript each stage. Production was verified untouched afterward.

**Remaining production limitations are unchanged.** Minion's live config is
still stale (path/UUID); Minion `lt` and the Aether worker remain 0.7.0; no
producer write was exercised against the production NFSv4.2 queue; and the
mixed-version writer rule still requires a quiesced, authorized cutover window.
This merge does not stop or start the Aether worker, change either host's pipx
environment, edit the Minion config, restart ref-api, enqueue into production,
change NFS/ROCm, tag, publish, release, or install anything on Nomnom.

**Merge and cleanup.** The complete feature branch was merged into `main` with a
normal non-squash, non-rewriting merge. After verifying the remote `main`, the
`done/` task location, task 044 still under `done/`, the 0.8.1 source version,
and the preflight ancestry, the feature worktree was removed, the local and
remote feature branch deleted, and the WIP marker removed. Durable
deployment/rollback artifacts and evidence were retained for the production
window.

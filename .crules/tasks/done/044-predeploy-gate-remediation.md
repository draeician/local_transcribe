# Task 044 — Pre-deploy queue gate remediation

| Field | Value |
|-------|-------|
| **Status** | `done` — approved, final gate passed, merged into `main`; production deployment not authorized |
| **Branch** | `fix/predeploy-queue-gate` |
| **Base** | `origin/main` @ `abdedbe88b8b44d24582520264f463bebcc83109` |
| **Type** | `fix` (patch release 0.8.0 → 0.8.1) |
| **Owner** | OpenCode |
| **Host** | `nomnom`, user `draeician`, unprivileged |
| **Worktree** | `/tmp/opencode/local_transcribe-gate-remediation` (ext4) |
| **Depends on** | 043 preflight correction (`b6d504f`) |
| **Blocks** | Production upgrade approval for 0.8.x |
| **Implementation commit** | `bff5ca6` (`fix(queue): publish terminal metadata atomically`) |
| **Correction commit** | `e62b383cffff24e61eeb9dbae67e0024e15d18d0` (`fix(queue): serialize claim/cancel and validate recovery`) |
| **Revision 2 commit** | `6aff2837fb86eb17c4c250301783e16dbaa460d1` (`fix(queue): exclude unresolved executions from claim`) |
| **Superseded revision** | `fe18821c0d5eb63c21a52a138d75c726860d8f31` — historical rejected revision; its gate evidence is not reused |
| **Correction status** | Approved at `084d212364cc1e2c8cdbb8de9fd89baa08cde328`; final pre-merge gate passed; merged into `main` with a non-squash merge |

## Ownership

Claimed by OpenCode at `2026-11-07T19:09Z` on branch
`fix/predeploy-queue-gate`, created from updated `origin/main` in a dedicated
ext4 worktree. Task 043 and its worktree
(`/tmp/opencode/local_transcribe-preflight`, `chore/three-host-upgrade-preflight`
@ `b6d504f`) are preserved and are **not** merged or modified here.

## Scope

1. Fix premature terminal-result reporting across `atomic_state_transition`,
   `locate_execution`, and `wait_for_execution` while preserving queue
   ownership, collision handling, crash recovery, directory-state authority,
   and NFS safety.
2. Correct the six ANSI-sensitive tests without weakening behavioral
   assertions or suppressing errors.
3. Investigate and fix the concurrent-force generation failure, distinguishing
   application defect from test synchronization defect, and preserve the
   single-current-generation invariant.

Non-goals: heartbeat, download deadlines, deployment tooling, new workers,
unrelated refactors, tag/publish, merge, production/Minion/Aether changes.

## Changes

### 1. Premature terminal results

| File | Change |
|------|--------|
| `src/local_transcribe/services/atomic_files.py` | `atomic_state_transition()` now writes the final metadata to a unique temp file, fsyncs it, and `link()`-publishes it to the destination **before** unlinking the source. The destination is never visible with stale pre-transition metadata. Collision still fails closed and leaves the source untouched. A failed source unlink raises `AtomicFileError` and leaves both names for recovery. |
| `src/local_transcribe/services/queue_wait.py` | `locate_execution()` only returns a terminal directory entry when the embedded `status` matches that directory; a mismatch is treated as transient. `wait_for_execution()` keeps the same guard so it cannot surface `completed` with a missing `output_path` or `failed` without an error record. |
| `src/local_transcribe/services/worker.py` | Recovery resolves interrupted-publication duplicates in favour of the advanced state: remove `pending` when `processing` exists; remove `processing` when `retry`/terminal exists; remove `retry` when `pending`/terminal exists. Normal retry promotion is unchanged. |

Directory membership remains the state authority. The new transition eliminates
the interval between destination publication and final metadata publication for
new writes; the waiter guard also protects against legacy/interrupted entries.

### 2. ANSI-sensitive tests

`tests/conftest.py` adds a `plain` fixture that strips ANSI CSI/SGR escapes from
captured CLI output. The six recorded tests use it while keeping every original
substring and exit-code assertion. No assertion was removed, relaxed, or
skipped.

Affected tests:

- `tests/test_cli_queue.py::test_queue_retry_all_failed`
- `tests/test_deno_startup_warnings.py::test_transcribe_url_no_wait_does_not_probe_deno`
- `tests/test_deno_startup_warnings.py::test_transcribe_url_wait_surfaces_worker_failure`
- `tests/test_update.py::test_cli_update_default_includes_pre`
- `tests/test_update.py::test_cli_update_dry_run_does_not_modify_environment`
- `tests/test_update.py::test_cli_update_pip_failure_shows_command_and_stderr`

### 3. Concurrent-force generation safety (corrected)

| File | Change |
|------|--------|
| `src/local_transcribe/services/queue_locks.py` | New advisory-lock helper. `queue_lock()` uses `fcntl.lockf` (NLM-backed on NFSv3) plus a per-path `threading.Lock` because POSIX record locks are per-process. Lock files live under `.locks/`, contain no state, and are never removed. |
| `src/local_transcribe/services/source_reservations.py` | `keys/<source-key>.json` is the **sole** authority again; the rejected `.advance` ledger is removed. `try_advance_generation()` and `try_update_current_execution()` perform locked read-modify-write with CAS semantics and re-read after writing to detect a lost race. `read_reservation()` validates source key, execution id, generation, and source type. |
| `src/local_transcribe/services/queue_store.py` | Force advances through the locked CAS helper. The force path attaches losers to the winner and does not cancel the winner's pending. `repair_source()` skips pending executions newer than the reservation. `cancel_pending()` takes the shared execution lock and fails closed on processing. |
| `src/local_transcribe/services/worker.py` | `claim_execution()` takes the same execution lock. Duplicate recovery validates every record and selects the unique reachable transition sink, leaving ambiguous/malformed copies intact. |
| `tests/test_queue_concurrency.py` | Real `QueueStore` lifecycle test: enqueue → force → delete the forced pending record (supported missing-current-record condition) → normal enqueue repairs the pointer → claim succeeds. |
| `tests/test_source_reservations.py` | Keys-authority contract tests: direct key-file readers agree with the application after normal updates, force, repair, and an interrupted write; concurrent advances have exactly one winner. |

The original failure was an **application defect**, not merely test timing:
multiple producers could each write generation N+1 with different execution ids
and then `repair_source()` could cancel the execution that the final pointer
named, leaving zero winners. The advisory lock makes each read-modify-write a
single-winner operation while keeping `keys/<source>.json` authoritative for
clients that never upgrade.

## Regression coverage added

- `test_destination_never_exposed_with_stale_metadata` — observes the destination
  immediately after `link()` and asserts final metadata is already present.
- `test_fault_after_publish_before_source_unlink_keeps_final_metadata` — crash
  window after publication; destination has final metadata, source remains for
  recovery.
- `test_fault_before_publish_leaves_source_and_no_partial_dest` — no partial
  destination is exposed.
- `test_wait_does_not_report_completed_before_final_metadata` — deterministic
  stale-terminal interval; waiter keeps polling until `output_path` exists.
- `test_wait_does_not_report_failed_before_error_metadata` — waiter keeps
  polling until the failure category/message exists.
- `test_concurrent_force_single_current_generation` — synchronized concurrent
  producers; exactly one current generation, no heal.
- `test_repair_source_keeps_newer_generation_mid_advance` — repair guard.
- `test_forced_generation_incomplete_repair_is_authoritative` — real
  `QueueStore` lifecycle: enqueue, force, delete the forced pending record, then
  a normal enqueue repairs the pointer; the replacement stays claimable.
- `test_worker_claims_first_cancel_fails_closed` and
  `test_cancel_wins_first_worker_does_not_execute` — both deterministic
  interleavings.
- `test_claim_paused_after_publish_blocks_cancel_until_claim_finishes` —
  controlled interleaving; cancel cannot complete while the claim holds the
  shared lock.
- `test_interrupted_cancel_is_idempotent_and_removes_pending` — interrupted
  cancel recovery.
- `test_concurrent_claim_and_cancel_never_create_both_states` — repeated
  two-thread race; never both `processing/` and `cancelled/`.
- `test_keys_file_is_authority_for_direct_reader`,
  `test_missing_keys_file_means_no_reservation`,
  `test_interrupted_reservation_write_leaves_previous_keys_authority`,
  `test_update_helpers_after_generation_advance` — keys-file contract,
  interrupted writes, and repair helpers after a generation advance.
- `test_concurrent_generation_advance_has_one_winner` — locked CAS has exactly
  one winner.
- `test_malformed_advanced_copy_is_left_intact`,
  `test_wrong_id_advanced_copy_is_left_intact`,
  `test_wrong_generation_advanced_copy_is_left_intact`,
  `test_pending_cancelled_duplicate_keeps_cancelled`,
  `test_processing_cancelled_duplicate_keeps_cancelled`,
  `test_conflicting_processing_cancelled_attempts_are_left_intact`,
  `test_retry_pending_processing_across_attempts_keeps_most_advanced` —
  fail-closed duplicate recovery, repeated without record loss.

## Verification environment

| Item | Value |
|------|-------|
| Host | `nomnom` (Linux Mint 22.3) |
| Python | 3.12.3 |
| Isolated venv | `/tmp/opencode/predeploy-gate-venv` |
| pytest / ruff / build / pipx | 9.2.0 / 0.17.4 / 1.6.1 / 1.4.3 |
| Worktree filesystem | ext4 (`/dev/sdb2`) |
| Isolation | `HOME`, `XDG_CONFIG_HOME`, `XDG_STATE_HOME`, `XDG_CACHE_HOME`, `TMPDIR` under `/tmp/opencode/...` |
| Production config/queue | not loaded or modified |

## Verification evidence — corrective tree

> These results apply to the corrective tree at `e62b383cffff24e61eeb9dbae67e0024e15d18d0`, not to
> `fe18821`.

| Check | Command | Result | Exit |
|-------|---------|--------|------|
| Targeted regressions | `python -m pytest -q` on claim/cancel, recovery validation, queue concurrency, source reservations, worker transitions, CLI queue/transcribe/batch, Deno warnings, update | **134 passed** | 0 |
| Full suite (default) | `python -m pytest -q` on ext4, default color | **368 passed, 11 skipped, 1 warning** | 0 |
| Full suite (`NO_COLOR=1`) | `python -m pytest -q` on ext4 | **368 passed, 11 skipped, 1 warning** | 0 |
| Concurrency stress (ext4) | predefined 50 × mixed force/claim-cancel/reservation selection | **50/50 passed** | 0 |
| Ruff canonical | `python -m ruff check --config pyproject.toml src tests` | **All checks passed** | 0 |
| Syntax | `python -m compileall -q src tests` | clean | 0 |
| Package build | `python -m build --outdir /tmp/opencode/gate044-impl-build/dist` | `local_transcribe-0.8.1` wheel + sdist | 0 |
| Wheel hashes | `sha256sum` | wheel `4837c88d5406ccf0ae5c076111450484117f5ba61e9366be7b7e11ebd6a4238b`; sdist `5a7c750e548305a4630c57c6b25f9096d47e888dc5545108919dfc3506c69d27` | 0 |
| Wheel metadata | inspect `METADATA` / `entry_points.txt` | `Version: 0.8.1`; `lt = local_transcribe.cli:main`; `queue_locks.py` and `queue_listing.py` present | 0 |
| Isolated CLI smoke | `lt version`, `--help`, `queue init/add/list/stats`, `transcribe --no-wait` | version **0.8.1**; 3 pending executions; no worker/download | 0 |
| Isolated pipx upgrade | `pipx install` 0.8.0 wheel, then `pipx install --force` 0.8.1 wheel | **0.8.0 → 0.8.1**; `lt version`, module, metadata, `pip show` all **0.8.1** | 0 |

### Baseline reproduction on rejected source `fe18821`

The new/changed tests were run against the rejected source tree at
`/tmp/opencode/lt-fe18821` with the corrective tests:

| Check | Baseline `fe18821` result |
|-------|---------------------------|
| Claim/cancel, recovery validation, forced repair, keys-authority, interrupted-write, repair-helper tests | **13 failed, 3 passed** (exit 1) |

The three passes are the basic claim-first/cancel-first paths that the rejected
tree already handled; the interleavings and fail-closed cases fail. This proves
the new assertions exercise the defects rather than passing by construction.

### Evidence path

Durable, sanitized evidence:
`~/.local/share/local-transcribe/evidence/20261107T-gate044-corrections-impl/`

Contains `baseline-fe18821-new-tests.log`, `targeted.log`, `full-default.log`,
`full-nocolor.log`, `stress-50.log`, `ruff.log`, `compileall.log`, `build.log`,
`wheel-sha256.txt`, `cli-smoke.log`, `pipx-install.log`,
`pipx-upgrade-080-081.log`, and `baseline-import.txt`.

The earlier evidence at
`~/.local/share/local-transcribe/evidence/20261107T-gate044/` and
`.../20261107T-gate044-corrections/` remains historical evidence for `fe18821`
and the delivery check; it is not reused here.

### Version and release

- `pyproject.toml`, `src/local_transcribe/__init__.py`, `project_spec.md`,
  `CHANGELOG.md`, and `docs/WHATS_NEW.md` are synchronized to **0.8.1**.
- No tag, publish, merge, deployment, service restart, or production
  configuration change was performed.

## Unavailable checks (disclosed)

- Real NFSv3/NLM multi-client tests were **not run**: the 10
  `tests/test_nfs_integration.py` tests skipped because `LT_NFS_QUEUE_DIR` was
  not set. No production queue was mounted or touched. Local ext4 passes do not
  prove multi-host NFS/NLM behavior.
- GPU/ROCm transcription was **not run** (out of scope; no production GPU
  test).
- The opt-in real pipx-install pytest (`LT_RUN_PIPX_INSTALL_TESTS`) was skipped;
  an equivalent isolated pipx install/upgrade was performed manually and
  recorded above.

## Review corrections implemented

| Correction | Implementation |
|------------|----------------|
| Exclusive worker-claim versus cancellation | `worker.claim_execution()` and `QueueStore.cancel_pending()` share `.locks/executions/<id>.lock`. Claim-first makes cancel fail closed with a processing-specific error; cancel-first makes the claim return `None`. Interrupted cancel is resolved only when the cancelled successor validates against the pending record. No sleep-based synchronization. |
| Incomplete-reservation repair after a forced generation | `try_update_current_execution()` repairs `keys/<source>.json` under the reservation lock. The replacement becomes current at the same generation, is not cancelled by an earlier same-generation claim, and remains claimable. Covered by the real `QueueStore` lifecycle test. |
| Existing reservation contract | `keys/<source-key>.json` is again the sole authority; the `.advance` ledger is removed. Advisory `fcntl.lockf` locks serialize read-modify-write without adding durable state, so older direct key-file readers and the ref reconciler agree with the application. No queue migration or ref change. |
| Fail-closed duplicate recovery | Every same-named record is parsed and validated (JSON/schema, execution id, source identity, generation, status directory, attempts, transition metadata). Recovery keeps the unique reachable transition sink; malformed, conflicting, cyclic, or unreachable records stay intact with an explicit warning. |

### Historical delivery check (superseded)

A delivery check at `fe18821` on 2026-11-07 found none of these corrections
implemented; that finding is retained as history and is superseded by the
implementation above. Its probe evidence remains at
`~/.local/share/local-transcribe/evidence/20261107T-gate044-corrections/`.

## Revision 2 — unresolved-recovery exclusion through the worker lifecycle (2026-11-08)

### Gap

The corrected tree validated duplicates inside `recover_processing()` and
`promote_retries()` but did not carry the unresolved result into eligibility.
`select_next_execution()` accepted any pending JSON that parsed, and
`claim_execution()` checked only the destination, `cancelled/`, and terminal
directories. Consequences on reviewed head `60f774f`:

- A valid pending plus a malformed/conflicting `retry/` copy was selected,
  claimed, and executed; recovery's warning had no lifecycle effect.
- A valid pending plus a malformed/conflicting `processing/` or terminal copy
  was re-selected every loop after the claim refused it, producing a tight
  claim-failure loop (201 selections reached the test budget in under a
  second).
- A blocked highest-priority pending record was selected ahead of a healthy
  lower-priority job, starving it.
- A conflicting copy created between selection and claim did not stop the
  claim, because the earlier selection snapshot was never revalidated.

### Implementation

| File | Change |
|------|--------|
| `src/local_transcribe/services/worker.py` | `select_next_execution()` validates every pending document with `_load_state_record()` (filename, embedded status, schema, identity, generation, attempts) and excludes any execution id with another state record **before** priority ordering. Blocked high-priority records therefore cannot starve healthy jobs. |
| `src/local_transcribe/services/worker.py` | `claim_execution()` re-loads the pending record under the shared execution lock, rechecks for any retry/processing/terminal counterpart, and compares the caller snapshot (source key, generation, attempts) with the record it is about to claim. A conflicting copy appearing between selection and claim, or a stale snapshot, fails closed. |
| `src/local_transcribe/services/worker.py` | Only-blocked queues now return `None` from selection, so `run_worker` takes its existing idle path (idle model maintenance, then `poll_interval_seconds` sleep). `once=True` returns 0 without executing anything. |
| `src/local_transcribe/services/source_reservations.py` | Corrected the readback claim: the post-write readback detects a race that happens before it, but not a non-cooperating older writer that writes after it. Direct key-file readers remain compatible; mixed-version writer operation is recorded as an unresolved deployment constraint. |
| `tests/test_worker_unresolved_lifecycle.py` | Nine lifecycle regressions driving the real `run_worker` with a controlled runner on disposable queues. |

No record is deleted, no attempt count is reset, and no winning state is
invented. Conflicting copies are asserted byte-for-byte unchanged.

### Regression coverage

| Scenario | Test |
|----------|------|
| Valid pending + malformed retry | `test_pending_with_malformed_retry_is_not_executed` |
| Valid pending + conflicting retry generation | `test_pending_with_conflicting_retry_is_not_executed[generation]` |
| Valid pending + conflicting retry identity | `test_pending_with_conflicting_retry_is_not_executed[identity]` |
| Valid pending + malformed terminal | `test_pending_with_unresolved_terminal_is_not_executed[malformed]` |
| Valid pending + conflicting terminal | `test_pending_with_unresolved_terminal_is_not_executed[conflicting]` |
| Valid pending + conflicting processing | `test_pending_with_conflicting_processing_is_not_executed` |
| Blocked highest-priority beside healthy pending | `test_blocked_high_priority_does_not_starve_healthy_pending` |
| Queue containing only unresolved jobs | `test_queue_with_only_unresolved_jobs_uses_bounded_idle_wait` |
| Conflict created between selection and claim | `test_conflicting_copy_between_selection_and_claim_is_not_executed` |

The tests use deterministic synchronization: a bounded selection wrapper turns
a tight loop into a failure instead of a hang, and the idle test replaces
`time.sleep` with a sentinel that stops the loop after the first real idle wait.
No sleep-based race synchronization and no weakened assertions.

### Baseline reproduction on reviewed head `60f774f`

`tests/test_worker_unresolved_lifecycle.py` → **9 failed** (exit 1):

- malformed/conflicting retry, only-unresolved queue, and the selection/claim
  race executed the unresolved job (`assert 1 == 0`, runner called);
- malformed/conflicting terminal and conflicting processing hit the 201-
  selection budget (`_BudgetExceeded`) instead of idling;
- the blocked high-priority job was the only runner call while the healthy job
  waited (`assert ['blocked-high'] == ['healthy-low']`).

Log: `baseline-reviewed-60f774f-new-tests.log`.

### Verification on the revision-2 tree

Environment unchanged: isolated venv `/tmp/opencode/predeploy-gate-venv`,
Python 3.12.3, pytest 9.2.0, ruff 0.17.4, build 1.6.1, pipx 1.4.3; ext4
worktree; isolated `HOME`/`XDG_*`/`TMPDIR`. Production config, queue, archives,
services, and installed pipx environments were not touched.

| Check | Command | Result | Exit |
|-------|---------|--------|------|
| New lifecycle regressions | `python -m pytest -q tests/test_worker_unresolved_lifecycle.py` | **9 passed** | 0 |
| Existing task-044 + queue/CLI/Deno/update regressions | `python -m pytest -q` on the 15 targeted files | **165 passed** | 0 |
| Full suite, ext4, default color | `python -m pytest -q` | **377 passed, 11 skipped, 1 warning** | 0 |
| Full suite, ext4, `NO_COLOR=1` | `NO_COLOR=1 python -m pytest -q` | **377 passed, 11 skipped, 1 warning** | 0 |
| Predefined 50-run ext4 concurrency stress | 50 × `pytest -q tests/test_queue_concurrency.py -k "concurrent or multiprocess or forced_generation or cancel_pending"` | **50/50 passed** (6 tests each) | 0 |
| Canonical Ruff | `python -m ruff check --config pyproject.toml src tests` | **All checks passed** | 0 |
| Syntax | `python -m compileall -q src tests` | clean | 0 |
| Package build | `python -m build --outdir /tmp/opencode/gate044-unresolved-build/dist` | `local_transcribe-0.8.1` wheel + sdist | 0 |
| Wheel hashes | `sha256sum` | wheel `09291bdef875a8b609a9303eec7990ba8f9fbd8312ad34692e6760ac63d5b5e9`; sdist `43c05a36cfc4aff05f0025cd746ac34f9350c6d74a162e5c82b7a6dca0ad6a61` | 0 |
| Wheel metadata | inspect `METADATA` / `entry_points.txt` | `Version: 0.8.1`; `lt = local_transcribe.cli:main`; `worker.py`, `queue_locks.py`, `queue_listing.py` present | 0 |
| Isolated CLI smoke | `lt version`, `--help`, `queue init/add/list/stats`, `transcribe --no-wait` | version **0.8.1**; 3 pending; no worker/download | 0 |
| Isolated pipx upgrade | `pipx install` 0.8.0 wheel, then `pipx install --force` 0.8.1 wheel | **0.8.0 → 0.8.1**; `lt version`, module, metadata, `pip show` all **0.8.1** | 0 |

### Evidence path (revision 2)

`~/.local/share/local-transcribe/evidence/20261107T-gate044-unresolved-lifecycle/`
— `baseline-reviewed-60f774f-new-tests.log`, `targeted.log`,
`full-default.log`, `full-nocolor.log`, `stress-50.log`, `ruff.log`,
`compileall.log`, `build.log`, `wheel-sha256.txt`, `cli-smoke.log`, and
`pipx-upgrade-080-081.log`. Earlier evidence directories remain historical for
`fe18821`/`e62b383` and are not reused.

### Compatibility correction

The previous wording that the post-write readback "reports a lost race instead
of overwriting a newer pointer" was too strong. The readback detects a race
that occurs before it (including a non-cooperating write inside the locked
section). It cannot detect a non-cooperating older writer that writes after the
readback. `keys/<source>.json` remains the sole authority, so **direct key-file
readers remain compatible**, but **mixed-version writer operation is an
unresolved deployment constraint**: older writers must not run concurrently
with this version. No queue migration, ref change, or production change was
made to resolve it. Journal correction appended as §21 (history preserved).

## Closure — approved merge (2026-11-08)

**Approval.** ChatGPT approved the feature head
`084d212364cc1e2c8cdbb8de9fd89baa08cde328` for the task-044 merge/cleanup phase.
The implementation was not redone.

**Final pre-merge gate on the approved feature tree.** Host `nomnom`, user
`draeician`, unprivileged, ext4 worktree, isolated `HOME`/`XDG_*`/`TMPDIR`,
development venv `/tmp/opencode/predeploy-gate-venv`.

| Check | Result | Exit |
|-------|--------|------|
| Canonical Ruff (`python -m ruff check --config pyproject.toml src tests`) | **All checks passed** | 0 |
| Full pytest on ext4 (`python -m pytest -q`) | **377 passed, 11 skipped, 1 warning** | 0 |
| Diff hygiene (`git diff --check origin/main...HEAD`) | clean | 0 |
| Version consistency (`pyproject.toml`, `__init__.py`, `project_spec.md`, `CHANGELOG.md`, `WHATS_NEW.md`) | **0.8.1** everywhere | 0 |
| Secret scan of `origin/main...HEAD` delta | no matches | 0 |

Evidence: `~/.local/share/local-transcribe/evidence/20261108T-gate044-merge/`
(`ruff.log`, `full-pytest.log`, `diff-check.log`, `version-consistency.log`,
`secret-scan.log`).

**Merge intent and result.** Merge the complete feature branch into `main` with
a normal non-squash `--no-ff` merge. The implementation history
(`bff5ca6`, `e62b383`, `6aff283`, and this closure commit) is preserved as
ancestry; no history is rewritten or squashed.

**Production deployment remains NOT authorized.** Task 043 prerequisites
(artifact staging, rollback rehearsal, Minion config/identity, NFSv4.2 producer
decision) and the mixed-version writer constraint are unchanged. No tag,
release, package publication, deployment, service action, or production change
is authorized by this closure.

## Blockers

Production deployment remains blocked by the separate task 043 prerequisites
(artifact staging, rollback rehearsal, Minion config/identity, NFSv4.2 producer
decision), plus the unresolved mixed-version writer constraint above: older
producers must not write `keys/<source>.json` concurrently with 0.8.1. These do
not block the approved merge into `main`.

## Next action

None for task 044. The approved feature is merged into `main`; deployment
approval remains a separate task-043 gate.

## Coder notes

- The advisory lock files under `.locks/` are coordination only and are never
  removed. Removing them after release would break exclusion for a waiter that
  already opened the old inode.
- `advance_generation()` remains an unconditional admin/test helper and is not
  used by the queue producer path.
- The ANSI fix is test-only. `Console(force_terminal=True)` is unchanged; the
  application still styles output when a terminal is forced.
- Eligibility is deliberately live rather than a cached recovery set: the
  predicate is "more than one state record for this execution id". Recovery
  leaves unresolved ids in that shape, so selection and claim fail closed even
  if recovery ran in a different process or the copy appeared later.

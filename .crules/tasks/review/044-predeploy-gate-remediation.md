# Task 044 — Pre-deploy queue gate remediation

| Field | Value |
|-------|-------|
| **Status** | `review` — implementation and required verification complete |
| **Branch** | `fix/predeploy-queue-gate` |
| **Base** | `origin/main` @ `abdedbe88b8b44d24582520264f463bebcc83109` |
| **Type** | `fix` (patch release 0.8.0 → 0.8.1) |
| **Owner** | OpenCode |
| **Host** | `nomnom`, user `draeician`, unprivileged |
| **Worktree** | `/tmp/opencode/local_transcribe-gate-remediation` (ext4) |
| **Depends on** | 043 preflight correction (`b6d504f`) |
| **Blocks** | Production upgrade approval for 0.8.x |
| **Implementation commit** | `bff5ca6` (`fix(queue): publish terminal metadata atomically`) |

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

### 3. Concurrent-force generation safety

| File | Change |
|------|--------|
| `src/local_transcribe/services/source_reservations.py` | Generation advances are immutable create-if-absent claims under `keys/.advance/`. `read_reservation()` returns the highest claim as the authority, with `keys/<source>.json` as a materialized cache. A slow writer can no longer move readers backwards, and a producer that dies after claiming remains recoverable. Claims are intentionally never deleted. |
| `src/local_transcribe/services/queue_store.py` | The force path treats the claim result as the winner, attaches losers to the claim's execution, and does not cancel the winner's pending. `repair_source()` skips pending executions whose generation is newer than the reservation, so an older generation's repair cannot cancel a producer that is mid-advance. |
| `tests/test_queue_concurrency.py` | Replaced the repair/heal fallback with a deterministic `threading.Barrier(6)` at the generation claim. All six producers read generation 1 and publish their pending before any advance; the test asserts exactly one `enqueued`, five attachments to that same winner, one pending, and reservation generation 2. Added a test that an older generation's repair preserves a newer mid-advance pending. |
| `tests/test_source_reservations.py` | Added tests that the highest claim wins over a stale materialized write and that a claim remains visible if the materialized reservation is missing. |

The failure was an **application defect**, not merely test timing: multiple
producers could each write generation N+1 with different execution ids and then
`repair_source()` could cancel the execution that the final pointer named,
leaving zero winners. The immutable claim ledger makes each generation advance a
single-winner operation.

## Regression coverage added

- `test_destination_never_exposed_with_stale_metadata` — observes the destination
  immediately after `link()` and asserts final metadata is already present.
- `test_fault_after_publish_before_source_unlink_keeps_final_metadata` — crash
  window after publication; destination has final metadata, source remains for
  recovery.
- `test_fault_before_publish_leaves_source_and_no_partial_dest` — no partial
  destination is exposed.
- `test_interrupted_publish_recovery_prefers_terminal_copy` — terminal copy wins.
- `test_interrupted_claim_publication_recovery_removes_pending_duplicate` —
  `pending` + `processing` resolves to `processing`.
- `test_interrupted_retry_publication_recovery_removes_processing_duplicate` —
  `processing` + `retry` resolves to `retry`.
- `test_interrupted_promotion_recovery_removes_retry_duplicate` —
  `retry` + `pending` resolves to `pending`.
- `test_wait_does_not_report_completed_before_final_metadata` — deterministic
  stale-terminal interval; waiter keeps polling until `output_path` exists.
- `test_wait_does_not_report_failed_before_error_metadata` — waiter keeps
  polling until the failure category/message exists.
- `test_concurrent_force_single_current_generation` — synchronized concurrent
  producers; exactly one current generation, no heal.
- `test_repair_source_keeps_newer_generation_mid_advance` — repair guard.
- `test_generation_claims_are_authoritative_over_stale_materialized_write` and
  `test_claim_is_visible_without_materialized_reservation` — ledger authority.

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

## Required verification evidence

| Check | Command | Result | Exit |
|-------|---------|--------|------|
| Targeted regressions | `python -m pytest -q` on the six ANSI tests, two waiter tests, foreground wait, concurrency, worker transitions, source reservations | **43 passed** | 0 |
| Full suite (default) | `python -m pytest -q` on ext4, default color | **352 passed, 11 skipped, 1 warning** | 0 |
| Full suite (`NO_COLOR=1`) | `python -m pytest -q` on ext4 | **352 passed, 11 skipped, 1 warning** | 0 |
| Concurrency stress (tmpfs) | 100 × `python -m pytest -q tests/test_queue_concurrency.py` | **100/100 passed** | 0 |
| Concurrency stress (ext4) | 50 × `python -m pytest -q tests/test_queue_concurrency.py` | **50/50 passed** | 0 |
| Ruff canonical | `python -m ruff check --config pyproject.toml src tests` | **All checks passed** | 0 |
| Syntax | `python -m compileall -q src tests` | clean | 0 |
| Package build | `python -m build --outdir /tmp/opencode/gate044-build/dist` | `local_transcribe-0.8.1` wheel + sdist | 0 |
| Wheel metadata | inspect `METADATA` / `entry_points.txt` | `Version: 0.8.1`; `lt = local_transcribe.cli:main`; `queue_listing.py` present | 0 |
| Isolated CLI smoke | `lt version`, `--help`, `queue init/add/list/stats`, `transcribe --no-wait` | version **0.8.1**; 2 pending executions; no worker/download | 0 |
| Isolated pipx upgrade | `pipx install` 0.8.0 wheel, then `pipx install --force` 0.8.1 wheel | **0.8.0 → 0.8.1**; `lt version`, module, metadata, `pip show` all **0.8.1** | 0 |
| Diff hygiene | `git diff --check` | clean | 0 |
| Secret scan (added lines) | regex scan of 924 added lines | **0 hits** | 0 |

### Baseline reproduction (proves the tests exercise the defects)

| Check | Baseline `abdedbe` result |
|-------|---------------------------|
| Six ANSI tests | **6 failed** (ANSI escapes split expected substrings) |
| Two new waiter tests | **2 failed** (`polls["n"] == 0`; terminal returned before metadata) |
| Deterministic concurrency test, 10 tmpfs runs | **8 failed** (`len(enqueued) == 1` saw 0 or >1) |

### Evidence path

Durable, sanitized evidence:
`~/.local/share/local-transcribe/evidence/20261107T-gate044/`

Contains full pytest logs, targeted regressions, stress summaries, build logs,
wheel hashes, pipx upgrade transcript, CLI smoke transcript, Ruff/compileall,
diff check, secret scans, baseline reproduction, and NFS skip reasons.

### Version and release

- `pyproject.toml`, `src/local_transcribe/__init__.py`, `project_spec.md`,
  `CHANGELOG.md`, and `docs/WHATS_NEW.md` are synchronized to **0.8.1**.
- No tag, publish, merge, deployment, service restart, or production
  configuration change was performed.

## Unavailable checks (disclosed)

- Real NFSv3/NLM multi-client tests were **not run**: the 10
  `tests/test_nfs_integration.py` tests skipped because `LT_NFS_QUEUE_DIR` was
  not set. No production queue was mounted or touched.
- GPU/ROCm transcription was **not run** (out of scope; no production GPU
  test).
- The opt-in real pipx-install pytest (`LT_RUN_PIPX_INSTALL_TESTS`) was skipped;
  an equivalent isolated pipx install/upgrade was performed manually and
  recorded above.

## Blockers

None for this remediation. Production deployment remains blocked by the
separate task 043 prerequisites (artifact staging, rollback rehearsal, Minion
config/identity, NFSv4.2 producer decision).

## Next action

Review the branch. If approved, merge `fix/predeploy-queue-gate` into `main` and
resume the task 043 deployment preflight with the now-green gate. Do not tag or
publish until that review authorizes it.

## Coder notes

- The immutable claim ledger adds one small JSON file per force generation under
  `keys/.advance/`. It is never pruned; this is intentional so the highest claim
  remains authoritative after a crash. Force is rare; the storage cost is
  negligible.
- `advance_generation()` (unconditional admin/test helper) still writes the
  materialized reservation directly. It is not used by the queue producer path;
  a later claim still wins because the ledger is authoritative.
- The ANSI fix is test-only. `Console(force_terminal=True)` is unchanged; the
  application still styles output when a terminal is forced.

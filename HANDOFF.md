# HANDOFF — local_transcribe

This file is the mandatory coordination channel between the ChatGPT project coordinator and coding agents (OpenCode or pi).

## Mandatory agent protocol

All source-code changes must be performed by an OpenCode or pi coding agent. The ChatGPT coordinator does not implement code changes directly.

Every coding-agent prompt issued for this repository MUST explicitly tell the agent to:

1. Read `HANDOFF.md` before doing any work.
2. Treat the current state and constraints in `HANDOFF.md` as required project context, alongside `AGENTS.md`, `project_spec.md`, and any task/spec files relevant to the work.
3. Before finishing, update `HANDOFF.md` with:
   - what was investigated or changed;
   - files changed;
   - tests/verification run and results;
   - unresolved issues or blockers;
   - recommended next step.
4. Preserve useful prior handoff history. Do not erase earlier findings just to write the current session.
5. Do not claim completion if verification has not passed.

The coordinator will use this file to pass state between separate OpenCode/pi sessions.

## Coding-agent prompt standard

Prompts for OpenCode/pi must be operational instructions, not vague requests. The prompt must tell the agent both **what to accomplish** and **how to execute the work**.

Unless a task genuinely requires something different, every coding-agent prompt should include:

1. **Branch**
   - State the exact branch to work on.
   - Pull the latest matching remote branch before implementation.
   - Never push directly to `main` unless the prompt explicitly authorizes it.

2. **First local repository action / WIP marker**
   - Tell the agent to create a repository-root, local-only WIP marker immediately.
   - Include task id, branch, and UTC start time.
   - The marker must never be staged, committed, or pushed.
   - Keep it present while work is incomplete or blocked.
   - Recreate it after context compaction/restart if missing.
   - Remove it only after all completion criteria and remote verification are green.

3. **Canonical context**
   - Read `AGENTS.md`, `project_spec.md`, `HANDOFF.md`, and any task/spec/status file named in the prompt before editing code.
   - Re-read those files after context compaction/restart before continuing.
   - Inspect repository and git state before resuming.

4. **Exact scope**
   - Name the exact bug/task to close.
   - Explicitly state what must not be rebuilt, broadened, or changed.
   - Identify authoritative files/specs when applicable.
   - Do not ask the user whether to continue when the prompt already defines the work.

5. **Execution instructions**
   - Give concrete implementation expectations and required behavior.
   - Require use of the real application lifecycle when validating behavior; do not fabricate state to bypass the path under test.
   - Call out important invariants, compatibility requirements, and forbidden shortcuts.

6. **Verification**
   - List the exact targeted tests/checks the agent must add or run.
   - Include regression tests for the bug.
   - Include broader project checks appropriate to the scope.
   - Require truthful reporting of commands and pass/fail results.
   - Do not claim completion with skipped required verification.

7. **Handoff update**
   - Before finishing, update `HANDOFF.md` with the implementation, files changed, verification evidence, blockers, and next step.
   - Preserve earlier useful handoff history.

8. **Commit and push**
   - State whether completed work must be committed and pushed.
   - When required, use conventional commits.
   - Push every completed commit to the named branch.
   - Verify the remote branch contains the final code/tests/docs/handoff.

9. **Definition of done**
   - End the prompt with explicit hard completion criteria.
   - The task is not complete until every required behavior/test is green, the remote branch is verified when pushing is required, and the local WIP marker is removed only after that verification.
   - Verify the WIP marker was never tracked, staged, committed, or pushed.

Future prompts should be detailed enough that an agent can execute the full task without needing the coordinator to fill in obvious operational steps mid-session.

## Repository snapshot

- Repository: `draeician/local_transcribe`
- Primary language: Python
- CLI: `lt` (Typer)
- Runtime model: `faster-whisper`
- Current architecture: queue-first NFSv3 workflow with one NLM-locked worker.
- Main worker path: `src/local_transcribe/services/worker.py`
- Production job runner: `src/local_transcribe/services/job_runner.py`
- Whisper cache: `src/local_transcribe/services/model_cache.py`
- Queue specification: `SPEC-queue.md`
- Agent/project guidance: `AGENTS.md`, `project_spec.md`

## Current investigation — idle Whisper model unload

Date: 2026-10-07

The repository contains an idle-unload mechanism, but the current implementation does not appear to schedule or invoke it.

Observed on both `main` and `feat/queue-creation`:

- `src/local_transcribe/services/model_cache.py`
  - `ModelCache(..., idle_unload_seconds=1800.0)` defaults to 1800 seconds (30 minutes).
  - `maybe_unload_idle()` drops the cached model when `last_used` exceeds that threshold.
- Repository-wide search found no caller of `maybe_unload_idle()`.
- `SPEC-queue.md` specifies `model_idle_unload_seconds: 1800`.
- `tests/test_model_cache.py` verifies cache reuse, reload-on-config-change, CUDA fallback, and worker job-runner reuse, but contains no idle-unload integration test.
- No 300-second / five-minute model idle-unload implementation or clearly documented five-minute bug-fix was found on `main` or `feat/queue-creation`.

Working conclusion: the cache has an idle-unload primitive, but the worker currently appears not to call it, so idle unloading is effectively not active. If the intended behavior is to unload Whisper after five idle minutes, an agent should first verify the desired 300-second policy and then implement/test the wiring rather than merely changing the existing 1800-second default.

## Implementation — worker idle Whisper unload (MODEL-IDLE-300)

Date: 2026-10-07
Agent: pi
Branch: `fix/model-idle-unload`, based on `origin/main` at `7234d97`.

### Root cause (independently re-verified before editing)

1. `ModelCache.maybe_unload_idle()` was dead code. `git grep maybe_unload_idle` over the
   whole repo returned only its own definition plus `HANDOFF.md` prose — no caller in
   `src/`, `tests/`, or `docs/`. `worker.py` never referenced a model or the cache at all;
   it only invoked `job_runner(claimed, scratch)`. So idle unloading never ran.
2. The threshold that would have applied was wrong anyway: `ModelCache.__init__` defaulted
   to `idle_unload_seconds=1800.0` (30 minutes), and `SPEC-queue.md` §22 documented
   `model_idle_unload_seconds: 1800`. Nothing passed the argument, so a production cache
   would have used 1800s even if something had called the primitive.

Fixing only the constant would therefore have changed nothing observable — both defects
were fixed.

### Implementation approach (smallest architecture-consistent design)

* `services/model_cache.py`
  * `DEFAULT_IDLE_UNLOAD_SECONDS = 300.0` is now the single authoritative default and the
    `ModelCache.__init__` default.
  * Added a read-only `idle_unload_seconds` property (so the shipped default is assertable
    without touching privates) and a richer unload log line including measured idle time.
  * Added the narrow optional interface `SupportsIdleMaintenance` (Protocol) plus
    `resolve_idle_maintenance(candidate)`, a duck-typed lookup of the `maybe_unload_idle`
    attribute. Plain functions/lambdas and runners without the hook resolve to `None`.
  * `maybe_unload_idle()` logic is unchanged apart from the log line: idle time is still
    measured against `CachedModel.last_used`, which `get()` refreshes on cache hits as well
    as loads. No second timer, no background thread.
* `services/job_runner.py` — `ProductionJobRunner.maybe_unload_idle()` delegates to the
  cache it already owns. Model ownership stays entirely in the runner; nothing was
  duplicated into `worker.py`.
* `services/worker.py` — `run_worker()` resolves the hook once
  (`resolve_idle_maintenance(job_runner)`) and calls it from `_run_idle_maintenance()` on
  the existing loop's **"nothing claimable"** path only — after the `_done_for_target()` /
  `once` / `max_jobs` break checks and before the poll sleep (so it also covers the
  `until_execution_id` foreground-fallback idle path). Because the worker is single-threaded
  and that branch runs only when no execution was claimed, a cached model can never be
  released mid-transcription. Hook failures are logged and swallowed so idle maintenance
  cannot kill the queue loop.

Unloading semantics: only the cache entry (and therefore its model reference) is dropped.
`faster-whisper`/CTranslate2 objects in this codebase are ordinary Python objects created by
`default_model_loader` → `load_whisper_model` with no registry, global table, or manual
free path (verified: no `empty_cache`, `gc.collect`, or CT2 handle bookkeeping anywhere in
`transcriber.py`), so normal reference counting frees them once the worker drops the last
reference. **No CUDA/CT2 global reset or other GPU-wide management was added** — none is
required here, and adding one would risk unrelated state in a long-lived daemon.

Runtime configurability: the config model (`services/config.py`, `QueueConfig`) has never
contained this value, and no code reads the spec's `worker:` block from `config.yaml` at all
(`poll_interval_seconds`, `standby_retry_seconds`, … are all compiled defaults). Per the task
constraint, no configuration subsystem was invented; `SPEC-queue.md` §22 now states
explicitly that the `worker:` sample documents compiled defaults, with
`model_idle_unload_seconds: 300` shipped as `DEFAULT_IDLE_UNLOAD_SECONDS`.

### Files changed

| File | Change |
|------|--------|
| `src/local_transcribe/services/model_cache.py` | 300s authoritative default, `idle_unload_seconds` property, `SupportsIdleMaintenance` + `resolve_idle_maintenance()`, docs/log line |
| `src/local_transcribe/services/job_runner.py` | `ProductionJobRunner.maybe_unload_idle()` hook delegating to its own `ModelCache` |
| `src/local_transcribe/services/worker.py` | resolve hook once; `_run_idle_maintenance()`; call it on the idle branch of the polling loop (best-effort) |
| `tests/test_model_cache.py` | +12 idle-unload tests (fake clock, no sleeps) |
| `tests/test_worker_model_idle.py` | new — 7 worker-lifecycle tests (fake clock + bounded poll budget) |
| `SPEC-queue.md` | §9.2 loop step, §20 idle-unload rules (default 300), §22 sample `model_idle_unload_seconds: 300` + compiled-default note, §25.4 test requirement |
| `CHANGELOG.md` | `[0.5.1] - 2026-10-07` Fixed entry |
| `pyproject.toml`, `src/local_transcribe/__init__.py` | 0.5.0 → 0.5.1 (`.crules/modes/GIT_POLICY.md`: `fix` ⇒ patch bump, cross-file consistency) |

Deliberately untouched: queue layout/store, reservations, NLM lock semantics, retry and
download-admission behavior, transcript publication, `--direct` paths, CLI surface.
`pipeline.py`'s `timeout_seconds: int = 1800  # 30 minutes` is the legacy `--direct` batch
per-job timeout and is **not** a model idle timeout — intentionally left alone.

### Behavior now guaranteed

* Worker-scoped cache releases the model after **300 s of genuine model inactivity**.
* Reuse inside the window refreshes `last_used` → back-to-back jobs reuse one instance.
* Nothing is released while a job runs; eviction happens only between jobs.
* The next job after eviction reloads through the normal cache path.
* Model/device/compute change still causes the existing cache-miss reload.
* Arbitrary injected `job_runner` callables (plain functions, custom classes) still work and
  are never required to expose model-cache internals.

### Tests added

`tests/test_model_cache.py`: default is 300 (constant + instance + production runner), no
unload at 299.5 s, unload at 300 s (idempotent second call), reuse refreshes the idle clock
(200 s reuse then 299 s / 300 s), reload after eviction, unload actually releases the model
reference (weakref + `gc.collect()`), `idle_unload_seconds=None` disables, explicit override,
config-change reload unaffected, hook resolution finds/ignores candidates.

`tests/test_worker_model_idle.py`: full worker lifecycle through the real `run_worker` loop
and real `ProductionJobRunner`/`ModelCache` (only Whisper/`faster-whisper` execution and the
`model_cache` clock are stubbed) — load → idle 60/120/180/240 s keeps it cached → 300 s tick
releases it → later job reloads; jobs inside the window reuse one model; a transcription
longer than the threshold keeps its model and the hook is never seen active; the worker calls
the hook while nothing is claimable for a non-`ProductionJobRunner` callable; a raising hook
does not kill the loop; a plain-function runner still works with no model cache at all.

Regression value was verified by mutation, not assumption:

* Deleting the `_run_idle_maintenance()` call site → 5 of the 7 worker tests fail in ~2.5 s
  (`WorkerStalled: worker polled 200 times without progress`).
* Restoring `DEFAULT_IDLE_UNLOAD_SECONDS = 1800.0` → 3 tests fail (`assert 1800.0 == 300.0`).

### Commands run (project venv `.venv`; bare `python3` on this host has no pytest/ruff)

| Command | Result |
|---------|--------|
| `.venv/bin/python -m pytest -q tests/test_model_cache.py` | **16 passed** |
| `.venv/bin/python -m pytest -q tests/test_worker_model_idle.py` | **7 passed** |
| `.venv/bin/python -m pytest -q tests/test_worker_model_idle.py tests/test_model_cache.py` | **23 passed in ~1.3 s** (no real 5-minute sleeps) |
| worker/CLI regression set (`test_worker_loop`, `test_worker_job_runner`, `test_worker_lock`, `test_worker_error_handling`, `test_worker_atomic_transitions`, `test_cli_worker`, `test_cli_transcribe_queue` + the two files above) | **86 passed** |
| `.venv/bin/python -m pytest -q` (full suite) | **12 failed, 198 passed, 10 skipped** |
| baseline full suite on stashed `origin/main` state | **12 failed, 179 passed, 10 skipped** — same 12 test ids |
| `.venv/bin/python -m ruff check .` | **42 errors — identical count before and after this change (0 new)**. All 3 findings inside touched files are pre-existing and were left alone as out of scope: `job_runner.py` unused `shutil`/`Optional` imports, `worker.py` unused `interactive` variable. `model_cache.py`, `test_model_cache.py`, `test_worker_model_idle.py` are clean. |
| `git diff --check` | clean (no whitespace errors) |
| `.venv/bin/lt version` | `local-transcribe version 0.5.1` (matches `pyproject.toml` and `__init__.py`) |

### Pre-existing failures (not caused by this task)

The same 12 ids fail on a clean `origin/main` checkout in this environment:

* 9 × `tests/test_cli_batch_queue.py`, `tests/test_cli_queue.py`,
  `tests/test_queue_concurrency.py::test_enqueue_youtube_safe_*` — the operator's live
  `~/.config/local-transcribe/config.yaml` sets `queue.path` + `expected_uuid: 8c6fc098…`,
  which these tmp-path tests inherit, so queue identity checks fail.
* 3 × `tests/test_update.py` — Typer/Rich emits ANSI color codes into `result.output`, so the
  plain-text substring assertions fail in this terminal configuration.
* `tests/test_queue_concurrency.py::test_concurrent_force_single_current_generation` is
  additionally **flaky** in the multiprocess full-suite run (observed intermittently at
  baseline *and* with these changes; it passes in isolation).

### Remaining caveats

1. `model_idle_unload_seconds` remains a specification/compiled default, not a `config.yaml`
   knob; the whole `worker:` block behaves the same way today. Wiring worker tuning into
   config would be a separate, larger change.
2. Effective eviction granularity is one poll interval: with the default
   `poll_interval_seconds = 5.0` the model is released between 300 and ~305 s after last use.
   That is intentional (no new thread/timer) and matches "no model use for 300 seconds".
3. Idle is measured from the last `get()`; a very long transcription can therefore be
   eligible on the first idle tick after it finishes. That is the specified semantics
   ("no model use for 300 seconds") and is verified to never unload *during* a job.
4. `0.5.1` is a version bump only — no git tag was pushed, so per
   `.crules/modes/GIT_POLICY.md` Release Protocol it is not an official release until tagged.
5. The `.venv` used for verification carries stale editable-install metadata
   (`local_transcribe-0.4.0.dist-info`), so `importlib.metadata.version()` still reports
   `0.4.0` even though `lt version` and the source report `0.5.1`. Reinstalling
   (`pipx install . --force`) refreshes it; nothing in this change depends on it.
6. `lt transcribe`/`--direct` are untouched: direct mode never used the worker-scoped cache,
   so the five-minute timeout does not apply there.

### Recommended next step

Review/merge `fix/model-idle-unload`, then on the real worker host: `pipx install . --force`,
restart the systemd user worker, submit one job, and confirm in
`~/.local/state/local-transcribe/logs/worker.log` that ~5 minutes of idleness logs
`Unloading idle model ('<model>', '<device>', '<compute>') (idle 300s >= 300s)` and that GPU
memory drops (`nvidia-smi`) while the worker stays resident. Afterwards, decide whether the
pre-existing config-pollution test failures (item above) deserve their own fix branch, since
they make the whole suite look red on operator machines.

## Correction — idle clock must start at end of model use (MODEL-IDLE-300-CORRECTION)

Date: 2026-10-07
Agent: pi
Branch: `fix/model-idle-unload`, correction on top of `fe71870` (not amended, not rebuilt).

### Semantic defect found in architecture review of `fe71870`

`fe71870` wired idle eviction correctly but measured the window from the wrong instant.
`CachedModel.last_used` was written only by `ModelCache.get()`, i.e. at **acquisition**,
before transcription starts. Because the worker calls idle maintenance right after a job
finishes, any transcription longer than the threshold arrived at its first idle tick already
looking expired:

```text
get model at T -> transcribe 600s -> job ends -> worker idle branch
-> now - last_used == 600 >= 300 -> model released immediately
```

Acquisition time is not equivalent to end-of-use time: the hour spent inside
`transcribe_with_model()` **is** model use, so it must not be billed as idle time. The
practical effect was that the most expensive model load was thrown away precisely when the
worker was most likely to need it again (busy queue = long jobs = eviction after every job),
and the previous handoff even documented that wrong behavior as intended (caveat 3 above).
The 300-second threshold itself was never in question and is unchanged.

### Implementation correction

* `services/model_cache.py` — new narrow API `ModelCache.mark_used(model, device,
  compute_type) -> bool`: records the **end of an actual use** by moving the entry's
  `last_used` to now. It is **key-aware** — it refreshes only the entry whose key matches and
  returns `False` without touching anything if the entry was replaced (different
  model/device/compute) or already evicted, so a late completion can never keep an unrelated
  entry alive or resurrect an evicted one. `get()` keeps seeding `last_used` at acquisition,
  now documented purely as a *floor* (start of use), never as end-of-use. Module/`get()`/
  `maybe_unload_idle()`/`CachedModel.last_used` docstrings corrected.
* `services/job_runner.py` — the single model-use site in `ProductionJobRunner.run()` is now
  wrapped:

  ```python
  model = self.model_cache.get(opts.model, effective_device, effective_compute)
  try:
      text = transcribe_with_model(model, audio_path, language=opts.language)
  finally:
      self.model_cache.mark_used(opts.model, effective_device, effective_compute)
  ```

  `finally` is deliberate: a transcription that raises has still finished using the model, so
  the clock restarts at the failure rather than leaving an ancient pre-transcription stamp.
  The exception is **not** caught, converted, or swallowed — it still propagates to the worker
  and the normal retry path.
* `services/worker.py` — **unchanged by this correction.** No thread, no timer, no polling
  change, no transcription timing in the worker, no duplicated cache state; idle maintenance
  still runs only on the "nothing claimable" branch, and cache ownership stays in
  `ProductionJobRunner`. `ProductionJobRunner` is the only consumer of `ModelCache` in `src/`
  (verified by grep), so there is no other use site that could report stale completions.

Resulting invariant: `last_used` == end of the most recent actual use, and the model is
released only after 300 further seconds with no use.

### Files changed (this correction)

| File | Change |
|------|--------|
| `src/local_transcribe/services/model_cache.py` | key-aware `mark_used()`; `last_used` redefined as end-of-use; docstrings corrected |
| `src/local_transcribe/services/job_runner.py` | `try/finally` around `transcribe_with_model()` calling `mark_used()`; hook docstring |
| `tests/test_model_cache.py` | +6 tests; the old `get()`-based reuse test reframed as acquisition-floor behavior |
| `tests/test_worker_model_idle.py` | wrong long-transcription test replaced by two correct ones; `ProbeRunner` gained an exact per-tick clock schedule |
| `SPEC-queue.md` | §20 idle-unload rules now state end-of-use semantics, the `T+900 → evict at T+1200` example, and `mark_used()` key-awareness; §25.4 test requirement extended |
| `CHANGELOG.md` | the 0.5.1 wording that equated `get()` age with "last actual use" corrected |

No production behavior other than the timestamp semantics changed; queue layout/store,
reservations, NLM lock, retry, download admission, transcript publication, CLI, `--direct`,
worker concurrency, and configuration architecture were not touched.

### Tests changed / added

`tests/test_model_cache.py` (23 tests now):

* `test_mark_used_restarts_idle_clock_at_end_of_use` — 600s use then a full further 300s
  window, then release, then normal reload.
* `test_reuse_then_completion_restarts_idle_clock` — completion → reuse inside the window →
  its own completion restarts the window; one load throughout.
* `test_mark_used_is_key_aware_and_never_touches_a_replacement`,
  `test_mark_used_without_cached_entry_is_noop` — cache-identity safety.
* `test_finished_transcription_starts_idle_clock_at_completion`,
  `test_back_to_back_jobs_reuse_one_model_and_reset_the_window`,
  `test_failed_transcription_still_records_end_of_use` — through the real
  `ProductionJobRunner.run()` (only the Whisper call is stubbed); the last one asserts the
  `RuntimeError` still propagates while the stamp is refreshed.

`tests/test_worker_model_idle.py` (8 tests now):

* Removed `test_worker_never_unloads_model_during_active_transcription`, whose final
  assertion demanded eviction on the first idle tick after a long job — the wrong semantics.
* `test_worker_keeps_model_after_long_transcription_when_next_job_is_soon` — 900s job, next
  idle tick 60s later must not unload, and the job that then arrives reuses **one** model.
* `test_worker_unloads_exactly_300s_after_long_transcription_completes` — through the real
  `run_worker` loop with a pinned per-tick fake clock: acquire at `T`, transcribe to `T+900`,
  maintenance at `T+901` (keep), `T+1199.5` (keep), `T+1200` (release), reload only afterwards
  (`loads[1].loaded_at == completed_at + 300`).
* Unchanged and still passing: idle window 60/120/180/240 → release at 300, reuse inside the
  window, hook never observed while a job is active (`hook_calls_while_active == 0`), hook
  called for a non-`ProductionJobRunner` callable, failing hook does not kill the loop,
  plain-function runner still works, production default threshold is 300.

**Proof the regression tests actually catch `fe71870`:** with the new tests kept and only the
two production files restored to `fe71870`, the run is **9 failed, 22 passed**; restoring the
correction gives **31 passed**. Five of those nine fail on behavioral assertions rather than
on the missing `mark_used` API (including both worker-loop tests, e.g. `AssertionError` on
`[False, False, True]` vs immediate eviction), so the guard is about timing semantics, not
internals.

### Verification

All commands via the project venv (`.venv/bin/python`); bare `python3` on this host has no
pytest/ruff.

| Check | Result |
|-------|--------|
| `pytest -q tests/test_model_cache.py` | **23 passed** |
| `pytest -q tests/test_worker_model_idle.py` | **8 passed** |
| `pytest -q tests/test_model_cache.py tests/test_worker_model_idle.py` | **31 passed in ~1.3 s** (fake clock; no real five-minute waits) |
| worker/CLI regression set (`test_worker_loop`, `test_worker_job_runner`, `test_worker_lock`, `test_worker_error_handling`, `test_worker_atomic_transitions`, `test_cli_worker`, `test_cli_transcribe_queue`, `test_model_cache`, `test_worker_model_idle`) | **94 passed** |
| branch full suite `pytest -q` | **12 failed, 206 passed, 10 skipped** (identical over 3 consecutive runs; 12–13 over 6 runs because of the known flaky multiprocess test) |
| freshly verified `origin/main` baseline (`git worktree add /tmp/base-main origin/main` @ `7234d97`, same venv, `PYTHONPATH=/tmp/base-main/src`, import path confirmed to resolve to the worktree) | **12–14 failed, 179 passed, 10 skipped** over 6 runs |
| failing-test-ID comparison (6 runs each, union of IDs) | baseline union = 13 IDs, branch union = **the same 13 IDs**; `comm`/`diff` show **no branch-only failure and no baseline-only failure** → no previously passing test was broken and nothing new was added to the red set |
| flaky IDs | `tests/test_queue_concurrency.py::test_concurrent_force_single_current_generation` and `::test_multiprocess_force_no_dual_current` appear intermittently in **both** baseline and branch full runs and pass in isolation |
| `.venv/bin/python -m ruff check .` (branch) vs `ruff check .` (origin/main worktree) | **42 errors on both**; `--output-format concise` rule+file sets diffed with `comm` → **empty delta in both directions**, i.e. zero new findings (and none removed). Pre-existing findings inside touched files (`job_runner.py` unused `shutil`/`Optional`, `worker.py` unused `interactive`) were deliberately left alone as unrelated cleanup. |
| `git diff --check` | clean |

### Remaining caveats

1. `get()` still refreshes `last_used` at acquisition as a documented floor. A hypothetical
   future caller that uses a model without ever calling `mark_used()` would fall back to
   acquisition-based aging (conservative: keeps the model longer, never evicts mid-use).
   Today `ProductionJobRunner` is the only `ModelCache` consumer.
2. Eviction granularity is still one poll interval, so release happens between 300 and ~305 s
   after the end of use (no new thread/timer, by design).
3. If a transcription never returns (hang), no completion is reported — but eviction only runs
   on the worker's no-claim path, so a hung job still cannot lose its model; the process would
   need the usual operator intervention.
4. Version policy choice, flagged for the coordinator: this commit does **not** bump to 0.5.2.
   `GIT_POLICY.md` maps `fix` → patch bump, but the code being corrected shipped only inside
   `0.5.1`, which was bumped in `fe71870` on this same branch and has never been tagged or
   installed (Release Protocol: a release is official only when tagged). Bumping again for a fix
   to unreleased code would create version noise, so the `0.5.1` CHANGELOG wording was corrected
   in place instead, keeping `pyproject.toml` / `__init__.py` / CHANGELOG consistent at 0.5.1.
   Say the word and a follow-up `chore(release): prepare 0.5.2` + tag is trivial.
5. `model_idle_unload_seconds` is still a spec/compiled default, not a `config.yaml` knob
   (unchanged from the previous entry, caveat 1 there).
6. Verification environment note from the previous entry still applies: `.venv` carries stale
   editable-install metadata (`local_transcribe-0.4.0.dist-info`), unrelated to this change.

### Recommended next step

Review/merge `fix/model-idle-unload` (now `fe71870` + this correction). On the worker host:
`pipx install . --force`, restart the systemd user worker, run one long job (>5 min), and
confirm in `~/.local/state/local-transcribe/logs/worker.log` that **no** `Unloading idle
model` line appears right after the job completes, and that the line first appears about five
minutes after the last job finished — the previous build would have logged it immediately.
Separately, the persistent 12-failure baseline (operator `~/.config/local-transcribe/config.yaml`
polluting tmp-path queue tests, plus Rich/ANSI assertions in `tests/test_update.py`) and the two
flaky multiprocess concurrency tests deserve their own fix branch; they are unrelated to model
idleness and were intentionally not touched here.

## Standing constraint

Do not make source-code changes from the coordinator session. For any implementation, debugging fix, refactor, or test change, prepare an OpenCode/pi prompt that includes the mandatory HANDOFF protocol and coding-agent prompt standard above.

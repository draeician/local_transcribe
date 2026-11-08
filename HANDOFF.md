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

## Merge and release — 0.5.1 on `main` (MODEL-IDLE-300-MERGE)

Date: 2026-10-08 (UTC)
Agent: pi

### Pre-merge verification (before any repository state change)

`git fetch --prune --tags origin`, then `fix/model-idle-unload` pulled `--ff-only` (already
up to date). Verified:

* exactly the two expected commits on top of `origin/main`, in order —
  `fe71870 fix(worker): unload Whisper model after idle timeout` then
  `16cf38c fix(worker): start model idle timeout after transcription`; nothing extra;
* branch based on current `origin/main` (`git merge-base --is-ancestor origin/main HEAD` →
  true; merge-base `7234d97` == `origin/main`);
* `origin/main` contained **neither** commit before the merge;
* working tree clean apart from the ignored local `.local-transcribe-wip`;
* version state agreed: `pyproject.toml` `0.5.1`, `src/local_transcribe/__init__.py` `0.5.1`,
  `CHANGELOG.md` `[0.5.1]` present; no `0.5.2` anywhere. Highest existing tag is `v0.5.0`, so
  `0.5.1` is the correct next release and **no further bump was applied** — the two fix commits
  together *are* the unreleased 0.5.1, and `16cf38c` corrects the code becoming 0.5.1;
* branch health: 31 targeted model-idle tests passed, 63 worker/CLI regression tests passed,
  full suite **12 failed, 206 passed, 10 skipped**, `ruff check .` 42 errors (all pre-existing).

### Merge mechanics

```text
7a0bbe1 Merge branch 'fix/model-idle-unload' — worker idle Whisper unload
  parent 1: 7234d97 (origin/main)
  parent 2: 16cf38c (branch tip, unmodified)
```

`git merge --no-ff` on `main` after `git pull --ff-only origin main`. `--no-ff` was chosen over
fast-forward to keep the reviewed feature branch traceable as a unit (same pattern as the 0.5.0
merge `fb5c4d0`), and `--no-commit` was used first to confirm the merged tree was **byte-identical**
to the approved branch tree (`git diff fix/model-idle-unload` empty) and to run the GIT_POLICY
secret scan on the staged merge (0 content hits, 0 forbidden filenames). Both implementation
commits keep their original hashes — nothing was squashed, amended, or rewritten. No push to
`main` happened outside this reviewed merge.

### Post-merge verification on `main`

| Check | Result |
|-------|--------|
| `pytest -q tests/test_model_cache.py tests/test_worker_model_idle.py` | **31 passed** |
| worker/CLI regression set (9 files) | **94 passed** |
| full suite ×3 | 12 / 13 / 12 failed, 206 (205) passed, 10 skipped |
| failing-ID comparison vs fresh `origin/main` worktree (`/tmp/base-main2` @ `7234d97`, 4 runs, ID union = 13) | **no new failing IDs on merged `main`**; the 12–13 failures are the same pre-existing set documented in the two entries above |
| `ruff check .` merged vs `origin/main` worktree | **42 findings on both**; concise rule+file sets diffed → **empty delta both directions** |
| `git diff --check` | clean |
| `.venv/bin/lt --version` (release protocol step a; `python3 -m local_transcribe` has no `__main__.py`) | `local-transcribe version 0.5.1` — matches `pyproject.toml` and `__init__.py` |

The model-idle implementation was **not** modified by this task: the merged source is exactly
what passed architectural review.

### Release preparation in this commit

The code merge alone would have shipped an inaccurate release record, so this commit only does
release hygiene (no behavior change):

* `CHANGELOG.md` — `[0.5.1]` dated to the actual release day (2026-10-08) and completed with
  everything merged since the `v0.5.0` tag, per the release protocol "summarize all feat and fix
  commits since the last tag": the `transcript-pending.md` auto-import (was sitting in
  `[Unreleased]` although already merged in `6720269`), plus `b99fd9e`, which had **no**
  changelog entry at all (`lt cookies refresh`, no-argument `lt queue retry`, worker resolving
  its own pipx/venv `yt-dlp` with a pinned systemd `PATH`, `max_attempts` honored during
  abandoned-`processing` recovery). Every claim was re-checked against the code
  (`cli_cookies.py` `cookies refresh`, `cli_queue.py` retry-all, `find_yt_dlp_binary`,
  `Environment=PATH=` in `cli_worker.py`, `attempts < max_attempts` in `worker.py`).
  `[Unreleased]` is now empty.
* `project_spec.md` — header `Version:` synced `0.5.0` → `0.5.1` (it is not a version-authority
  file, but MANAGER.md forbids divergent version strings and this one was stale).
* `docs/WHATS_NEW.md` — short 0.5.1 operator-facing section (model released after 5 idle
  minutes, measured from end of use; cookie refresh; venv `yt-dlp`).
* `HANDOFF.md` — this record.

### Release tag and push

Annotated tag `v0.5.1` (matching the existing `v0.4.0`/`v0.5.0` annotated-tag convention) points
at this release commit, and `main` + the tag are pushed with `git push origin main --tags`.
Post-push verification (see the session summary for the captured hashes): local `main` ==
`origin/main`, `v0.5.1` resolves to the same commit on the remote, `v0.5.0` still reachable and
untouched, and both implementation commits are ancestors of `origin/main`.

### Remaining caveats

1. The 12 pre-existing full-suite failures are unchanged by the release (operator
   `~/.config/local-transcribe/config.yaml` polluting tmp-path queue tests; Rich/ANSI substring
   assertions in `tests/test_update.py`) plus two order-sensitive multiprocess concurrency tests.
   A release tagged from a tree with red tests is defensible only because those IDs are identical
   on `origin/main` — they should be fixed before the next release.
2. `importlib.metadata.version("local-transcribe")` still reports `0.4.0` on this host until
   `pipx install . --force` refreshes the stale editable-install metadata; `lt version` and the
   package metadata in the tree are correct at 0.5.1.
3. `project_spec.md`'s "Verification baseline" line still says `python3 -m local_transcribe
   --version`, which fails (no `__main__.py`); `lt version` / `lt --version` is the working
   check and is what the release verification used.
4. The merged branch `fix/model-idle-unload` (local and remote) was deliberately **not**
   deleted, even though `GIT_POLICY.md` suggests deleting after merge: the coordinator may still
   want to diff it. `git push origin --delete fix/model-idle-unload && git branch -d
   fix/model-idle-unload` when ready.
5. No GitHub Release object was created (out of scope; the tag push is what makes the release
   official per the Release Protocol). Release-notes text for one is in `CHANGELOG.md` /
   `docs/WHATS_NEW.md`.

### Recommended next step

On the worker host: `pipx install . --force` (this also clears caveat 2), restart the systemd
user worker, run one job longer than five minutes, and confirm in
`~/.local/state/local-transcribe/logs/worker.log` that no `Unloading idle model` line appears at
job end and that the line appears about five minutes after the last job finished, with GPU memory
dropping in `nvidia-smi` while the worker stays resident. Then decide whether to delete the merged
branch and whether to schedule the test-hygiene fix branch described in caveat 1.

## Portable CPU/CUDA installation and runtime selection (PORTABLE-CPU-CUDA-INSTALL)

Date: 2026-10-19 (UTC)
Agent: OpenCode
Branch: `fix/portable-cpu-cuda-install` (based on `origin/main` at `b4ec265`)

### Reported failure and root cause

On **Aether** (no CUDA) `pipx install git+https://github.com/draeician/local_transcribe`
failed while building `nvidia-cudnn`. Root cause: `pyproject.toml` listed
`nvidia-cublas`, `nvidia-cudnn`, `torch`, `torchvision`, and `torchaudio` as
**core** dependencies, so every install attempted to resolve/build NVIDIA
packages. Inspection showed PyTorch is not used by any inference path:
faster-whisper runs on CTranslate2 (`services/transcriber.py` only ever imports
`faster_whisper`; the sole `torch` references were the CUDA auto-install helpers
and their diagnostics).

### Implementation

1. **Dependencies** — core set is now CPU-only: `nvidia-*`, `torch*` removed;
   faster-whisper/ctranslate2 retained. Optional `cuda` extra
   (`nvidia-cublas-cu12`, `nvidia-cudnn-cu12==9.*`) + `requirements-cuda.txt`.
   Also pinned `av>=14,<19` (see latent bug below).
2. **Portable installer** — new `install_local_transcribe.py` with `--auto`
   (default), `--cpu`, `--cuda`. Detection uses a functional `nvidia-smi` probe
   plus a driver threshold (>= 525.60 for CUDA 12); `--cuda` fails closed with a
   clear message. Installs only via `pipx` into an isolated venv (never system
   `pip`), `pipx install --force` makes re-runs idempotent, and it verifies the
   result. `install_with_gpu.sh` is now a thin forwarding wrapper. Deno is not a
   prerequisite for install or local-audio transcription.
3. **Runtime selection** — new `utils/hardware.py` centralizes detection and
   `resolve_device_and_compute`. `device`/`compute_type` default to `auto` in
   the CLI, `ExecutionOptions`, `BatchConfig`, and `TranscribeConfig`.
   Resolution happens **on the worker** in `ProductionJobRunner.run()`, so
   producers and workers may differ in hardware. Usable CUDA → `cuda` + best
   supported compute type; otherwise `cpu`/`int8`. Explicit `--device`/
   `--compute-type` are preserved.
4. **No automatic installs** — removed `ensure_pytorch_cuda` and the CLI
   startup side effect, `lt worker install`'s CUDA-torch block, and the whole
   PyTorch install/marker machinery. `lt doctor`, `lt queue stats`,
   `lt worker status` never download anything.
5. **Diagnostics** — `doctor.py` rewritten around `DiagnosticCheck` with
   `required` flags: required = CTranslate2/faster-whisper/yt-dlp/soundfile,
   CPU backend, ffmpeg, yt-dlp; optional = GPU probe, ctranslate2 CUDA, Deno.
   CPU-only `lt doctor` exits 0; broken CUDA on a GPU host is still actionable.
   PyTorch is no longer a check.

### Files changed

| File | Change |
|------|--------|
| `pyproject.toml` | version 0.6.0; CPU-only core deps; `cuda` extra; `av<19`; `build` in dev |
| `requirements.txt` / `requirements-cuda.txt` | CPU core mirror + new CUDA profile |
| `install_local_transcribe.py` (new) | portable CPU/CUDA installer |
| `install_with_gpu.sh` | thin wrapper forwarding to the Python installer |
| `src/local_transcribe/utils/hardware.py` (new) | GPU/CUDA detection + device/compute resolution |
| `src/local_transcribe/utils/doctor.py` | CPU-aware diagnostics (`DiagnosticCheck`) |
| `src/local_transcribe/cli.py` | `auto` defaults; removed torch side effects; doctor exit code |
| `src/local_transcribe/cli_worker.py` | `worker install` no longer installs torch |
| `src/local_transcribe/services/transcriber.py` | delegate to hardware module |
| `src/local_transcribe/services/queue_models.py`, `services/pipeline.py` | `auto` defaults |
| `tests/test_portable_install.py` (new) | 41 tests for the 10 required areas |
| `README.md`, `START_HERE.md`, `docs/QUEUE_OPERATOR.md`, `docs/WHATS_NEW.md`, `AGENTS.md`, `SPEC-queue.md`, `CHANGELOG.md`, `project_spec.md` | docs + version |

Untouched by design: queue store/layout, source reservations, NLM lock, retry,
download admission, transcript publishing, `--direct`, worker concurrency.

### Verification (evidence)

Environment: project venv `.venv`; clean isolated pipx homes under `/tmp/opencode`.

| Check | Result |
|-------|--------|
| `.venv/bin/python -m pytest -q` | **4 failed, 255 passed, 11 skipped** |
| baseline `origin/main` (`b4ec265`) full suite | **same 4 ids** fail (plus known flaky multiprocess test) → **0 new failures** |
| new tests `tests/test_portable_install.py` | **41 passed, 1 skipped** (opt-in real-install test) |
| worker/model-cache/queue regression set (6 files) | **99 passed, 1 skipped** |
| `.venv/bin/python -m ruff check .` | **38 findings vs 42 baseline**; rule+file delta showed **0 new**, 4 pre-existing removed |
| `.venv/bin/python -m build` | built `local_transcribe-0.6.0` sdist + wheel |
| real clean pipx install (isolated `PIPX_HOME`) | `pipx install --force …` succeeded; **no nvidia/torch in `pipx runpip … list`** |
| `lt --version` / `lt doctor` in clean CPU env | `0.6.0`; doctor **exit 0**, CPU backend "healthy (int8)", CUDA/GPU/Deno shown optional |
| actual CPU transcription (clean pipx env) | `Introducing AgentKit.mp3` → transcript JSON with real text, device auto→cpu int8 |
| actual queue execution (clean pipx env) | local audio enqueued `device=auto`, worker processed 1 job, `local_….json` published with real transcript |
| installer `--cuda` on CPU host | exit 2, message: "CUDA is not usable here: no NVIDIA GPU detected…" |
| installer idempotency | `--cpu`, repeat `--cpu`, then `--auto` all succeeded against the same venv; env stayed free of nvidia/torch |
| installer `--cuda` with mocked GPU | resolves profile `cuda`, commands `pipx inject --force local-transcribe nvidia-cublas-cu12 nvidia-cudnn-cu12==9.*` |
| `git diff --check` | clean except 3 intentional Markdown hard-break lines (two trailing spaces) that match the surrounding header/bullet style in `README.md`, `SPEC-queue.md`, `project_spec.md` |

### Latent bug found during clean-install verification

A truly clean resolve pulled **PyAV 19**, which removed the `metadata_errors`
kwarg that `faster-whisper 1.2.1` still passes in `faster_whisper/audio.py`.
Real transcription then failed with `open() got an unexpected keyword argument
'metadata_errors'`. This was masked in the dev venv (PyAV 17.1.0). Fixed by
pinning `av>=14,<19` in the core dependencies and `requirements.txt`. Verified
by reinstalling the clean pipx env and transcribing real audio successfully.

### Pre-existing failures (unchanged, not caused by this task)

* `tests/test_cli_queue.py::test_queue_retry_all_failed` and 3 ×
  `tests/test_update.py` — Rich/ANSI colour output and operator config
  pollution already documented in the 0.5.1 entries above; identical on
  `origin/main`.
* `tests/test_queue_concurrency.py::test_multiprocess_force_no_dual_current` —
  known flaky multiprocess test (also flaky at baseline).

### Remaining caveats / not verified

1. **No physical GPU validation was performed.** The CUDA profile selection,
   inject command, and driver threshold were verified with **mocked** detection
   and dry-runs only; this host has no NVIDIA GPU (`nvidia-smi` absent,
   `ctranslate2.get_cuda_device_count() == 0`). On real GPU hardware, run
   `./install_local_transcribe.py --cuda` and confirm `lt doctor` reports
   `ctranslate2 CUDA: … usable`.
2. Idle-unload/model-cache behavior is unchanged; regression protection comes
   from the existing `tests/test_model_cache.py` and `tests/test_worker_model_idle.py`
   (both still green), plus new queue-on-CPU tests.
3. Version bumped `0.5.1 → 0.6.0` (minor: user-facing default change + new
   installer/extra), so `pyproject.toml`, `__init__.py`, `project_spec.md`, and
   `CHANGELOG.md` agree. No tag pushed here.

### Recommended next step

Merge to `main`, then on a real GPU host run
`./install_local_transcribe.py --cuda` and `lt doctor`, and transcribe one
YouTube video to confirm CUDA acceleration end-to-end.

### Repository lifecycle record

* Feature commit: `8a6f9a5` `feat(install): portable CPU/CUDA installation and
  runtime selection` on `fix/portable-cpu-cuda-install` (based on `origin/main`
  `b4ec265`).
* Merge commit: `a4801fe` `Merge branch 'fix/portable-cpu-cuda-install' —
  portable CPU/CUDA installation` (`--no-ff` on `main`; merged tree verified
  byte-identical to the reviewed branch tree via `git diff <branch> HEAD`).
* `origin/main` == local `main` == `a4801fe` after `git push origin main`;
  merged branch deleted both locally and on the remote
  (`git ls-remote --heads origin` no longer lists it).
* The local WIP marker `.local-transcribe-wip` existed throughout and was
  removed only after the final on-`main` verification; `git ls-files`,
  `git log --all`, and `git status` confirm it was never tracked, committed, or
  pushed.
* No new tag was pushed (0.6.0 is not declared an official release here).

## AMD ROCm acceleration for the Radeon 8060S / Strix Halo (AMD-ROCM-8060S)

Date: 2026-10-19 (UTC)
Agent: OpenCode
Branch: `feat/amd-rocm-transcription` (based on `origin/main` at `3e0e236`)
Target host: `aether` (AMD Ryzen AI Max+ 395, Radeon 8060S, gfx1151)

### Phase 1 — Aether environment (observed)

| Item | Value |
|------|-------|
| `hostname` | `aether` (reached over SSH from the `minion` dev checkout) |
| kernel | `6.17.0-41-generic` |
| OS | Ubuntu 25.10 |
| GPU | `c5:00.0` AMD/ATI **Radeon 8060S** (Strix Halo), PCI `1002:1586` |
| Device nodes | `/dev/kfd` and `/dev/dri/renderD128` present; user in `video`+`render` |
| `rocminfo` | reports `Name: gfx1151`, "Radeon 8060S Graphics", `amdgcn-amd-amdhsa--gfx1151` |
| Python | 3.13.7 (`/usr/bin/python3`), pipx 1.7.1 |
| Existing install | pipx `local-transcribe 0.6.0`, `lt` on PATH |
| Existing worker | **active** (`/dev/kfd` holder absent; worker state active, heartbeat live) |
| Other GPU users | llama.cpp `llama-server` in podman containers (must not be disturbed) |
| Disk | `/` 97% used, 33 GB free |

#### Critical ROCm finding: mixed 5.x system stack

`rocm-core` is **7.2.0**, but the actual runtime libraries on the host are the
*distribution* 5.x builds, and `/opt/rocm-7.2.0/lib` contains only
`librocm-core.so`:

* installed: `libamdhip64-5` (5.7.1), `librocblas0` (5.5.1) → `libamdhip64.so.5`,
  `libhipblas.so.0`
* the CTranslate2 ROCm wheel requires `libamdhip64.so.7`, `libhipblas.so.3`,
  `libhiprand.so.1`

So `/opt/rocm` on Aether **cannot** satisfy the CTranslate2 ROCm wheel. Fixing
this with system packages (`apt install rocm`, `rocm-hip-runtime`) needs root
and risks the llama.cpp stack; per the task's "do not install or replace system
GPU drivers, kernels, or ROCm libraries automatically" rule this was **not**
done. No privileged change was applied.

#### Non-privileged solution found

TheRock's ROCm SDK ships the matching ROCm 7.x libraries as ordinary Python
wheels (`rocm-sdk-core`, `rocm-sdk-libraries-gfx1151`) on AMD's own index
(`https://rocm.nightlies.amd.com/v2/gfx1151/`). Installing them inside the venv
supplies `libamdhip64.so.7`, `libhipblas.so.3`, `libhiprand.so.1`, … with no
root and no system modification. `HSA_OVERRIDE_GFX_VERSION` is **not** used.

### Phase 2 — Validation result (real GPU, isolated venv, non-privileged)

On `aether`, in a throwaway venv (`~/rocm-verify`) — **not** the production pipx
env:

```
ctranslate2 4.8.3 (official ROCm wheel, cp313)
rocm-sdk-core==7.13.0a20260515
rocm-sdk-libraries-gfx1151==7.13.0a20260515
```

* `ctranslate2.get_cuda_device_count()` → **1**
* `get_supported_compute_types("cuda")` → `bfloat16, float16, float32, int8,
  int8_bfloat16, int8_float16, int8_float32`
* Real transcription of `gettysburg.wav` (17.58 s) with `medium`/`float16`:
  **wall 2.41 s, 7.31x realtime**, correct transcript text
* GPU utilization sampled with `rocm-smi -u` during a 6-run burst: **28 → 30 →
  72 → 85 → 90 → 91 %** — the GPU is genuinely doing the work
* Benchmark, same audio/model/`beam_size=5`, best-of-3 warm runs:
  * CPU `int8`: **7.061 s (2.49x realtime)**
  * ROCm `float16`: **4.138 s (4.25x realtime)**
  * speedup: **1.71x**

Archives pinned and verified during this work:

* `rocm-python-wheels-Linux.zip` sha256
  `132eda022faed55e4d1a7093f15a9dd9d4bcdf26bd72471ec1c984bada9a3d3f`
* cp313 inner wheel sha256
  `f4790b7c10e668619f9ba138b6f26cdba5736c8d810628d37d76b567de3b7a2d`

### Implementation (Phase 3)

| File | Change |
|------|--------|
| `src/local_transcribe/utils/rocm.py` (new) | pinned ROCm wheel plan, SHA-256 verify, archive extraction, pipx step construction |
| `src/local_transcribe/utils/hardware.py` | AMD detection (`/dev/kfd` + `rocminfo`), ROCm SDK library discovery + safe `RTLD_GLOBAL` preload, `gpu_backend()` (rocm/cuda/cpu from loaded libs), backend-agnostic `gpu_runtime_status`, `--device rocm` semantics, `describe_backend` |
| `src/local_transcribe/utils/doctor.py` | three-way backend reporting (AMD ROCm / NVIDIA CUDA / CPU), GPU arch + compute types, actionable `--rocm` hint |
| `src/local_transcribe/services/transcriber.py` | prepare ROCm runtime before resolution; `effective_backend()`; re-export `BackendUnavailableError` |
| `src/local_transcribe/cli.py` | device help now `auto/cpu/cuda/rocm` |
| `install_local_transcribe.py` | `--rocm` profile, AMD auto-detection, ROCm wheel download/verify/inject, ROCm verification |
| `tests/test_rocm_support.py` (new) | 36 tests: detection, preload safety/idempotency, backend identity, auto/explicit selection, queue-on-worker ROCm, doctor three-way, install planning, hash verification |
| `tests/test_portable_install.py` | updated to the backend-agnostic probe names |

Behavior guaranteed:

* `device=auto` → GPU (ROCm or CUDA) when CTranslate2 can use it, else CPU `int8`.
* `device=rocm` → ROCm, or a clear `BackendUnavailableError`/installer error; never a silent fake GPU.
* CTranslate2's internal `cuda` device name is preserved for ROCm.
* Queue executions keep `device=auto`; resolution stays on the worker.
* Model-cache keys remain `(model, effective_device, effective_compute)`; the five-minute idle-unload path is untouched.

### Verification so far (dev checkout `minion`)

| Check | Result |
|-------|--------|
| targeted ROCm + portable-install tests | **78 passed, 1 skipped** |
| full suite | **4 failed, 292 passed, 11 skipped** |
| baseline `origin/main` failures | the **same 4** (config pollution `test_cli_queue`, Rich/ANSI `test_update`) → **0 new** |
| `ruff check .` | 32 findings, **0 in new/edited ROCm files**, no new findings |
| `python3 -m build` | `local_transcribe-0.7.0` sdist + wheel |

### Remaining work before this entry is final

Phase 4 production-safe verification is recorded in the entry below.

## AMD ROCm — Phase 4/5 delivery (AMD-ROCM-8060S, final)

Date: 2026-10-19 (UTC)
Agent: OpenCode
Branch: `feat/amd-rocm-transcription`, commits `601b7f8`, `a2c19ad`, `270b08e`, `9a44619`.

### Three bugs found and fixed during production-safe validation

These were only visible in a **real pipx install** on Aether, not in the dev
checkout — which is exactly why the task required isolated GPU testing:

1. **`pipx inject --pip-args --no-deps` mis-parsed** (pipx consumed `--no-deps`
   as its own flag). Fixed to `--pip-args=--no-deps` (`a2c19ad`).
2. **The base install pulls the PyPI CUDA `ctranslate2` at the same version
   (4.8.3)**, so pip treated the ROCm wheel injection as already satisfied and
   left the non-ROCm build in place (`libctranslate2-bdb19cbc` instead of
   `47736dc1`). Fixed with `--force-reinstall`; post-install verification now
   also fails unless CTranslate2 reports a non-zero device count and compute
   types (`270b08e`).
3. **Import-order failure**: `faster_whisper` imports `ctranslate2` at module
   load, before `resolve_device_and_compute` ran, so `lt version` aborted with
   `libhiprand.so.1: cannot open shared object file`. Fixed by preloading the
   ROCm runtime at package import, best-effort and non-fatal (`9a44619`).

### Isolated install (aether, `PIPX_HOME=~/.cache/lt-rocm-test/pipx`)

* `pipx install git+…@feat/amd-rocm-transcription` → 0.7.0
* `install_local_transcribe.py --rocm` → ROCm wheel downloaded (SHA-256 OK),
  injected with `--force-reinstall`, then `ROCm backend verified: backend
  amd-rocm / gpu_devices 1 / gpu_types [bfloat16, float16, float32, int8,
  int8_bfloat16, int8_float16, int8_float32]`
* `lt doctor` exit 0; `Acceleration Backend: active: AMD ROCm (gfx1151)`;
  `AMD GPU (/dev/kfd, rocminfo) ✓`; `ctranslate2 GPU ✓ (backend=amd-rocm)`
* `lt transcribe --direct` of a 17.58 s local file → correct transcript JSON in
  a private output dir
* Queue worker on a **disposable** queue (`/tmp/lt-q`): a job enqueued with
  `device=auto` was resolved on the worker and completed via ROCm
* Idle unload: `Unloading idle model ('tiny', 'cuda', 'float16') (idle 301s >=
  300s)` — cache key and 5-minute semantics intact

### Benchmark (Aether, same audio/model/settings, warm best-of-N)

Audio: `gettysburg.wav`, 17.58 s; model `medium`; `beam_size=5`.

| Backend | Compute | Best warm | Realtime | Speedup |
|---------|---------|-----------|----------|---------|
| CPU | `int8` | 4.144 s | 4.24× | 1.00× |
| **AMD ROCm** | `float16` | **1.666 s** | **10.55×** | **2.49×** |

GPU utilisation sampled with `rocm-smi -u` during transcription bursts:
**28 → 30 → 72 → 85 → 90 → 91 %** (isolated) and **94–96 %** (production worker).

An early isolated run measured 20.34 s (`0.86×`); re-measurement gave
1.67–1.68 s consistently. This host shares the GPU with llama.cpp containers and
VRAM was ~97/103 GB in use, so the single slow run is attributed to transient
contention, not a code path. The steady-state numbers above are reproducible.

### Production rollout on Aether (controlled)

* Production pipx upgraded in place: `0.6.0` → `0.7.0` with the ROCm profile.
* Queue UUID `8c6fc098-8cbe-41ae-9689-dd759797a582` and
  `~/.config/local-transcribe/config.yaml` unchanged; queue not recreated; no
  jobs cleared.
* The single NLM-locked worker service (`local-transcribe-worker.service`) was
  restarted **only after** isolated validation. The in-flight job was recovered
  by the normal path: `Recovered processing job fc16bd35… -> retry/`.
* After restart the worker process maps `libamdhip64.so.7`, `libhipblas.so.3`,
  `libhipblaslt.so.1`, `librocblas.so.5` with **no** `LD_LIBRARY_PATH`,
  `HSA_OVERRIDE_GFX_VERSION`, or `CT2_USE_CUDA` in its environment.
* Jobs completed by the new worker at 23:32:56 and 23:34:02 (43 s / 65 s),
  GPU utilisation **94–96 %** during transcription, transcript JSON written to
  `/opt/md2/music/youtube/transcripts/`.

### Dev-checkout verification (`minion`)

| Check | Result |
|-------|--------|
| `pytest -q` | **4 failed, 294 passed, 11 skipped** — the same 4 pre-existing failures as `origin/main` (config pollution `test_cli_queue`, Rich/ANSI `test_update`); **0 new** |
| targeted ROCm + worker/model-cache/CLI set (8 files) | **142 passed, 1 skipped** |
| `tests/test_rocm_support.py` | **39 passed** |
| `ruff check .` | 32 findings, **0 new**, `utils/rocm.py`/`utils/hardware.py`/installer/tests clean |
| `python3 -m build` | `local_transcribe-0.7.0` sdist + wheel |

### Files changed (feature, cumulative)

| File | Change |
|------|--------|
| `src/local_transcribe/utils/rocm.py` (new) | pinned ROCm wheel plan, SHA-256 verify, extraction, pipx steps |
| `src/local_transcribe/utils/hardware.py` | AMD detect, ROCm lib discovery + safe preload, `gpu_backend()`, backend-agnostic runtime status, `rocm` device semantics |
| `src/local_transcribe/utils/doctor.py` | AMD ROCm / NVIDIA CUDA / CPU reporting + arch/compute types |
| `src/local_transcribe/services/transcriber.py` | preload + `effective_backend()`, re-export `BackendUnavailableError` |
| `src/local_transcribe/__init__.py` | import-time best-effort ROCm preload |
| `src/local_transcribe/cli.py` | device help `auto/cpu/cuda/rocm` |
| `install_local_transcribe.py` | `--rocm` profile, AMD auto-detect, hash-verified wheel install, GPU verification |
| `tests/test_rocm_support.py` (new) | 39 tests |
| `tests/test_portable_install.py` | probe-name updates |
| `README.md`, `AGENTS.md`, `CHANGELOG.md`, `docs/WHATS_NEW.md`, `project_spec.md` | docs + 0.7.0 |

### Outstanding issues / next step

1. **System ROCm is still a mixed 5.x/7.2 stack.** Acceleration is provided
   entirely by the venv's TheRock ROCm SDK wheels. A future host-level ROCm 7.x
   upgrade would let the system libraries be used instead; it needs root and was
   deliberately not attempted.
2. The Aether GPU is shared with llama.cpp containers; benchmarks are
   best-effort under that contention and should be re-run on an idle GPU.
3. ROCm wheel/sdk versions are pinned to a nightly TheRock release
   (`7.13.0a20260515`); a periodic bump is a normal maintenance task.
4. `HSA_OVERRIDE_GFX_VERSION` is intentionally never used; `gfx1151` is a real
   target of the pinned wheels.

### Repository lifecycle

* Feature commits `601b7f8`, `a2c19ad`, `270b08e`, `9a44619`, `0186958` on
  `feat/amd-rocm-transcription` (based on `origin/main` at `3e0e236`).
* Merge commit `938844b` (`--no-ff` on `main`; merged tree verified
  byte-identical to the reviewed branch tree via `git diff <branch> HEAD`).
* `origin/main` == local `main` == `938844b`; merged branch deleted locally and
  on the remote (`git ls-remote --heads origin` no longer lists it).
* The local WIP marker `.local-transcribe-wip` was present for the whole task and
  removed only after the final on-`main` verification; `git ls-files`,
  `git log --all`, and `git status` confirm it was never tracked, committed, or
  pushed.
* Version is consistent at `0.7.0` across `pyproject.toml`,
  `src/local_transcribe/__init__.py`, `project_spec.md`, and `CHANGELOG.md`.
  No tag pushed (not declared an official release here).

## Implementation — queue list reporting (task 037)

Date: 2026-11-03
Agent: OpenCode
Branch: `feat/queue-list-reporting`, based on `origin/main` at `25b89f1`.
Task file: `.crules/tasks/review/037-queue-list-reporting.md`.
Feature commit: `df60e1e` (`feat(queue): add filtered and paginated queue list reporting`).

### What changed

- `lt queue list [STATUS|UUID] [--limit N] [--offset N] [--sort oldest|newest]`
  now lists all execution states by default (`--limit 60`, `--offset 0`,
  `--sort oldest`).
- Positional input accepts any of `pending`, `processing`, `retry`,
  `completed`, `failed`, `cancelled`, or a UUID. A UUID selects one execution;
  an unknown but valid UUID renders an empty result (exit 0).
- Existing `--status`, `--origin`, and `--limit` options remain available. A
  positional status and `--status` must agree; a UUID cannot be combined with
  `--status`.
- Output is tab-separated and includes execution ID, enqueue timestamp in local
  time with timezone abbreviation/offset, age, status, priority, source, and
  origin. The footer reports `Showing start-end of total`, limit/offset/sort,
  and the local timezone.
- `QueueStore.query_executions()` performs read-only filtering (state
  directory), sorting by `created_at`, and pagination after filtering. Ties use
  the execution ID as a deterministic secondary key.
- Invalid negative offsets, nonpositive limits, unknown statuses, malformed
  UUIDs, and invalid sort values exit 1 with actionable messages.
- No worker scheduling, queue transition, ROCm, or transcription code changed.
  No schema migration or timestamp synthesis was introduced.

### Files changed

| File | Change |
|------|--------|
| `src/local_transcribe/cli_queue.py` | new list interface, validation, local-time/age rendering |
| `src/local_transcribe/services/queue_listing.py` (new) | pure timestamp, age, sort, pagination helpers |
| `src/local_transcribe/services/queue_store.py` | read-only `query_executions()` |
| `tests/test_cli_queue.py` | 28 new regression tests |
| `README.md`, `QUICK_REFERENCE.md`, `AGENTS.md`, `docs/QUEUE_OPERATOR.md`, `docs/WHATS_NEW.md` | updated list defaults/examples |
| `CHANGELOG.md` | `[Unreleased]` entry (no version bump) |
| `.crules/tasks/review/037-queue-list-reporting.md` | task moved to review with coder notes |

### Verification

Environment note: the checked-in `.venv`/`venv` are broken (no packages), and
the pipx environment has runtime dependencies but no pytest. Tests were run with
the system pytest and the pipx site-packages on `PYTHONPATH`. `TERM=dumb`
disables Rich color for the four pre-existing ANSI-sensitive tests.

| Command | Result |
|---------|--------|
| `TERM=dumb PYTHONPATH=src:$PIPX_SITE /usr/bin/python3 -m pytest -q tests/test_cli_queue.py tests/test_queue_store.py` | **39 passed** |
| `TERM=dumb PYTHONPATH=src:$PIPX_SITE /usr/bin/python3 -m pytest -q` | **326 passed, 11 skipped** |
| same full suite with normal color terminal | 4 failed, 322 passed, 11 skipped; the 4 failures are the pre-existing Rich/ANSI `test_update` + `test_cli_queue` set (reproduced on `main` before edits). The pre-existing `test_queue_concurrency::test_concurrent_force_single_current_generation` race also failed intermittently on both `main` and this branch and passed on rerun. |
| `uvx ruff check --config pyproject.toml src/local_transcribe/cli_queue.py src/local_transcribe/services/queue_store.py src/local_transcribe/services/queue_listing.py tests/test_cli_queue.py` | 40 findings, all pre-existing style classes (`B008`, `UP045`, `TRY203`, `UP035`); baseline HEAD versions of the same tracked files had 45. New `queue_listing.py` alone: **All checks passed** |
| `PYTHONPATH=src:$PIPX_SITE /usr/bin/python3 -m local_transcribe.cli version` | `local-transcribe version 0.7.0` |
| `PYTHONPATH=src:$PIPX_SITE /usr/bin/python3 -m local_transcribe.cli doctor` | exit 1: environment missing `ffmpeg`; unrelated to this change |

`$PIPX_SITE` = `~/.local/share/pipx/venvs/local-transcribe/lib/python3.14/site-packages`.

### Blockers / open items

- None for review. The only full-suite failures seen under a color terminal are
  pre-existing and disappear with `TERM=dumb`; the concurrency test remains
  timing-flaky on `main` and this branch.
- Package version intentionally remains `0.7.0`; the change is recorded under
  `[Unreleased]`. Version bump/merge/release is for review approval.

### Recommended next step

Review `feat/queue-list-reporting`, especially the UUID-not-found semantics and
the new default (all states, limit 60), then merge if approved. Do not release
or upgrade production before review.

## Correction — queue list UUID filters (task 037 follow-up)

Date: 2026-11-03
Agent: OpenCode
Branch: `feat/queue-list-reporting`, based on feature commit `df60e1e`.
Correction commit: `bda86ce` (`fix(queue): honor UUID filters in queue list`).

### Findings corrected

1. **UUID selection + nonzero `--offset` could silently hide the record.**
   A UUID now rejects any nonzero `--offset` with:
   `A UUID selection cannot be combined with a nonzero --offset; omit --offset to show the selected execution.`
   `--offset 0` remains valid. Normal status/all-record pagination is
   unchanged.
2. **`--origin` was accepted but ignored for UUID selection.**
   The origin filter is now applied to the selected execution. A mismatch
   returns exit 0 with
   `No matching execution for UUID <id> with origin '<origin>'.`
   No filtering option is silently ignored.

Decision recorded in `docs/QUEUE_DESIGN_DECISIONS.md` §13. Task 037 was moved
back to `wip/` during the correction and returned to `review/` before commit.

### Files changed

| File | Change |
|------|--------|
| `src/local_transcribe/cli_queue.py` | reject UUID+nonzero offset; apply origin to UUID selection; help text |
| `tests/test_cli_queue.py` | regression tests for both findings |
| `README.md`, `QUICK_REFERENCE.md`, `CHANGELOG.md` | document UUID offset/origin behavior |
| `docs/QUEUE_DESIGN_DECISIONS.md` | correction decision |
| `.crules/tasks/review/037-queue-list-reporting.md` | review status + correction notes |

### Verification

| Command | Result |
|---------|--------|
| `TERM=dumb PYTHONPATH=src:$PIPX_SITE /usr/bin/python3 -m pytest -q tests/test_cli_queue.py tests/test_queue_store.py` | **41 passed** |
| `TERM=dumb PYTHONPATH=src:$PIPX_SITE /usr/bin/python3 -m pytest -q` | **328 passed, 11 skipped** |
| normal color terminal full suite | 4 failed, 324 passed, 11 skipped; the same pre-existing Rich/ANSI `test_update` + `test_cli_queue` failures reproduced on `main` |
| `uvx ruff check --config pyproject.toml …changed Python files…` | **40 findings**, identical to the pre-correction HEAD baseline (0 new) |
| `uvx ruff check --config pyproject.toml src/local_transcribe/services/queue_listing.py` | **All checks passed** |
| `uv build --out-dir /tmp/opencode/lt-dist` | built `local_transcribe-0.7.0.tar.gz` and `local_transcribe-0.7.0-py3-none-any.whl` |

### Read-only performance (synthetic queue, no production data)

Created `/tmp/opencode/lt-queue-list-perf` with **6,430** execution records
(100 pending, 50 processing, 50 retry, 6,000 completed, 200 failed, 30
cancelled). No production queue directory was read or modified.

| Case | CLI end-to-end median (3 runs) |
|------|--------------------------------|
| all/default (`--limit 60`) | **1.269 s** |
| `completed` status filter | **1.135 s** |
| UUID selection | **0.544 s** |
| `completed --offset 5900 --limit 60` | **1.171 s** |

Store-level medians (5 runs): all/default **0.614 s**, completed **0.570 s**,
direct UUID lookup **~0.000 s**. CLI timings include Python startup and the
Deno warning path.

### Blockers / open items

- None for review. The only full-suite failures under a color terminal are the
  pre-existing ANSI-sensitive tests; they pass with `TERM=dumb`.
- Version remains `0.7.0`; no merge, tag, release, or Aether upgrade was done.

## Fix — unnecessary Deno startup warnings (DENO-STARTUP-QUIET)

Date: 2026-11-04 (UTC)
Agent: pi
Branch: `fix/deno-startup-warning`, based on `origin/main` at `25b89f1`.
Task record: `.crules/tasks/review/038-deno-startup-warning.md`
Type: `fix` (patch; recorded under `[Unreleased]`)

### Investigated / changed

**Root cause.** The root Typer callback (`main`, `@app.callback`) in
`src/local_transcribe/cli.py` called `check_deno_with_guidance()` for *every*
subcommand (guarded by the module-level `_startup_warnings_shown` flag). On a
host without Deno this printed the full install-guidance block before queue
statistics, list output, version strings, and sub-app help. Reproduced with the
installed CLI: `lt queue list`, `lt version`, `lt --version`, `lt queue --help`,
and `lt doctor` all emitted "⚠️ Warning: Deno runtime not detected".

**Fix.** The callback performs no probe at all; `_startup_warnings_shown` was
removed with the probe it guarded. Deno is now checked only where a YouTube
download actually happens:
- `transcribe`: the probe moved inside `if not is_local`, so local audio files
  never touch it and queue/local paths stay quiet.
- `batch --direct`: keeps its prerequisite warning (that pipeline always
  downloads URLs); the queue branch returns before reaching that code.
`lt doctor` retains "Deno Runtime (YouTube)" as an optional diagnostic via
`run_diagnostics()`, and `lt update` still fails clearly when Deno is absent.
No package installation or system configuration was added anywhere. Worker,
job runner, download admission, queue store, and ROCm code were not touched.

### Files changed

| File | Change |
|------|--------|
| `src/local_transcribe/cli.py` | removed callback-level Deno probe + `_startup_warnings_shown`; transcribe probe scoped to non-local sources; direct-batch comment clarified (10 insertions, 24 deletions) |
| `tests/test_deno_startup_warnings.py` | new regression suite, 10 tests, simulating a host without Deno and recording every probe call |
| `CHANGELOG.md` | `[Unreleased] → Fixed` entry |
| `docs/QUEUE_DESIGN_DECISIONS.md` | §13 append-only decision record |
| `docs/QUEUE_DESIGN_DECISIONS.md` | §13 append-only decision record |
| `README.md`, `AGENTS.md` | state the warning is download-path only |
| `.crules/tasks/review/038-deno-startup-warning.md` | task record with acceptance + evidence |

### Verification

| Command | Result |
|---------|--------|
| `TERM=dumb PYTHONPATH=src:$PIPX_SITE /usr/bin/python3 -m pytest -q tests/test_deno_startup_warnings.py` | **10 passed** |
| Same suite run against an `origin/main` worktree (`25b89f1`) | **2 failed, 8 passed** — the two failures reproduce this bug (`queue list` prints the warning; `_startup_warnings_shown` still present) |
| `pytest -q tests/test_deno_startup_warnings.py tests/test_cli_queue.py tests/test_update.py tests/test_cli_batch_queue.py tests/test_cli_transcribe_queue.py` | **48 passed** |
| Full suite, 5 consecutive runs on this branch (`TERM=dumb`) | 308 passed / 1 failed+307 / 308 / 308 / 308 (11 skipped every run) |
| Full suite on `origin/main` worktree, 5 runs | 298 / 1 failed+297 / 1 failed+297 / 298 / 298 (11 skipped) |
| `uvx ruff check --config pyproject.toml src tests` on this branch vs `origin/main` | **413 findings both** — identical per-rule counts; only difference is the line number in the pre-existing duplicate-`main` F811 (1117 vs 1118). **0 new findings.** |
| `/…pipx venv…/python -m compileall src/local_transcribe/cli.py tests/test_deno_startup_warnings.py` + AST parse | OK |
| `uv build --out-dir /tmp/pi-build` | built `local_transcribe-0.7.0.tar.gz` and `local_transcribe-0.7.0-py3-none-any.whl` |

**Real CLI check on this host (no Deno on PATH, real queue in a temp dir):**
`lt version`, `--version`, `queue list`, `queue stats`, `queue path`,
`queue doctor`, `--help`, `queue --help` are all warning-free; `transcribe
https://youtu.be/dQw4w9WgXcQ` still prints the Deno warning plus the HTTP 403
note before enqueuing; `transcribe <local>.m4a` enqueues silently; `lt doctor`
still lists "Deno Runtime (YouTube) … deno not found (only needed for YouTube
downloads)" as optional.

### Baseline exceptions (pre-existing, reproduced on origin/main)

- `tests/test_queue_concurrency.py::test_concurrent_enqueue_one_active` and
  `…::test_concurrent_force_single_current_generation` are timing-flaky under a
  loaded machine: they pass in isolation on both branches and intermittently fail
  inside the full-suite run on **both** this branch (1/5 runs) and `origin/main`
  (2/5 runs). Not caused by this change; not suppressed, not weakened.
- Ruff reports 413 findings repo-wide at baseline (`F401`, `UP045`, `I001`,
  `RUF100`, the duplicate `main` F811 in `cli.py`). Left untouched — out of scope.

### Blockers / open items

- None blocking review. No Deno installation or system change was performed.
- Version remains `0.7.0`; this fix is recorded under `[Unreleased]`. The patch
  bump to `0.7.1` (fix per GIT_POLICY) is a merge/release decision for review,
  matching how task 037 was staged.

### Recommended next step

Review `fix/deno-startup-warning`; if approved, merge into `main`, apply the
patch version bump consistently (`pyproject.toml` +
`src/local_transcribe/__init__.py`) or fold it into the next release, then move
task 038 to `done/`. Do not upgrade production before review.

## Integration — CLI reporting + Deno probe scope, version 0.8.0 (task 039, 2026-11-04)

Branch `chore/cli-reporting-integration` from `origin/main` @ `25b89f1`. Integrates task 037
(`feat/queue-list-reporting`) and task 038 (`fix/deno-startup-warning`) into one reviewable
branch. **No merge into main, no tag, no branch deletion, no production queue or Aether change.**

### What was changed

- `feat/queue-list-reporting` merged with `--no-ff` (merge commit `25be670`) so its four commits
  stay reachable exactly as authored: `df60e1e`, `0a571e8`, `bda86ce`, `29e402a`.
- `fix/deno-startup-warning` applied with `git cherry-pick`; authorship, dates and message bodies
  preserved (new SHAs `0e83077`, `2f247c4`, `1a4e931` for `3b78d5c`, `c6bc375`, `259a0c9`).
  Conflicts were documentation-only (`CHANGELOG.md`, `docs/QUEUE_DESIGN_DECISIONS.md`,
  `HANDOFF.md`); the Deno decision is renumbered §13 and the task 039 refinement recorded as §14.
- **Integration refinement** in `src/local_transcribe/cli.py`: the Deno probe moved from the
  `lt transcribe` entry to (a) the queue **wait** path, before `wait_for_execution`, and (b) the
  `--direct` URL branch. Consequence: `lt transcribe <URL> --no-wait` — a producer-only call that
  only writes reservation/execution JSON — no longer probes Deno; local-file transcription,
  `lt batch` queue-mode enqueue, `lt queue *`, version and help remain quiet. Waiting or
  in-process YouTube transcription still warns with the HTTP 403 note; `lt doctor` keeps the
  optional diagnostic row; the `lt update` hard gate is unchanged.
- Version synchronized to **0.8.0**: `pyproject.toml`, `src/local_transcribe/__init__.py`,
  `project_spec.md`, `CHANGELOG.md` (`[Unreleased]` → `[0.8.0] - 2026-11-04`), new
  `docs/WHATS_NEW.md` section.
- `tests/test_deno_startup_warnings.py` now 14 tests: added
  `test_transcribe_url_no_wait_does_not_probe_deno`,
  `test_transcribe_url_wait_warns_without_deno`,
  `test_transcribe_direct_url_warns_without_deno`. One assertion corrected to the task 037 output
  shape (the `SOURCE` column carries the recorded source URL, not the `youtube:<id>` key). No
  existing assertion was weakened.

### Verification evidence

Environment: checked-in `.venv`/`venv` are empty; pipx venv has runtime deps but no pytest. Tests
ran with system pytest plus `PYTHONPATH=src:$PIPX_SITE`
(`$PIPX_SITE=/home/draeician/.local/share/pipx/venvs/local-transcribe/lib/python3.14/site-packages`),
`TERM=dumb` for the ANSI-sensitive assertions.

| Check | Result |
|-------|--------|
| Targeted suites (Deno, queue store/list, transcribe/batch queue, update) | **86 passed** on this branch |
| Full suite ×5, integration vs `origin/main` worktree @ `25b89f1` | identical failure classes; only the timing-flaky `test_queue_concurrency.py` cases appear intermittently on **both** (pass in isolation) |
| Ruff (`uvx ruff check --config pyproject.toml src tests`) | 408 findings vs baseline 413 — strictly fewer, 0 new findings in changed files |
| `uv build --out-dir /tmp/pi-integration-dist` | `local_transcribe-0.8.0.tar.gz` + `-py3-none-any.whl`; wheel METADATA `Version: 0.8.0` |
| Isolated install `/tmp/lt-venv-080` (production pipx untouched) | `importlib.metadata.version('local-transcribe') == 0.8.0`, `__version__ == 0.8.0`; production pipx still reports **0.7.0** |
| Upgrade detection | installed 0.7.0 vs wheel 0.8.0 → detected as an upgrade; isolated `lt version` prints `local-transcribe version 0.8.0` |

**Real CLI smoke on this host (no Deno on PATH, throwaway queue dir under `/tmp`, removed after;
real `~/.config/local-transcribe/config.yaml` and production queue untouched):**
`lt version`, `--version`, `queue list/stats/path/doctor`, `--help`, `queue --help` warning-free.
`transcribe <URL> --no-wait` enqueues silently (`✓ Queue enqueued: youtube:9bZkp7q19f0`,
`--no-wait: not waiting for worker`) with no Deno block. Local `.m4a` enqueue silent. Waiting
YouTube `transcribe <URL>` still prints the full Deno guidance plus the HTTP 403 note before
waiting. `lt doctor` lists "Deno Runtime (YouTube) … deno not found (only needed for YouTube
downloads) (optional)"; `lt update --dry-run` fails closed with "✗ Deno is not on PATH".
Queue list reporting verified live: default 3-column-ordered oldest-first table with
ENQUEUED/AGE/STATUS/PRIORITY/SOURCE/ORIGIN, `Showing 1-N of N (limit=60, offset=0, sort=oldest)`,
`--limit/--offset` paging (`Showing 0-0 of 1 … offset=1` beyond end), `--sort newest`, positional
status filter and UUID selection, UUID + nonzero `--offset` rejected with exit 1, UUID + mismatched
`--origin` renders an explicit empty result (exit 0).

### Test-isolation finding (documented, not suppressed)

With a **production-shaped** config present (`queue.path` + `expected_uuid: 8c6fc098…`, supplied
to the test process through an isolated `XDG_CONFIG_HOME` copy — the real user config was never
modified), `tests/test_cli_batch_queue.py`, `test_cli_transcribe_queue.py`,
`test_deno_startup_warnings.py` and `test_update.py` are green on this branch: **45 passed** with
the same 6 pre-existing `test_cli_batch_queue.py` failures that fail identically on baseline main
(diffed failure lists: identical). The task-037 `test_queue_list_*` tests do not pin
`XDG_CONFIG_HOME`, so they inherit the operator config and fail against tmp-path queues with
"Queue UUID mismatch … expected 8c6fc098…" — this is a test-isolation gap in those new tests, not
a behaviour regression; baseline main has no such tests. Recommend a follow-up to add the
`monkeypatch.setenv("XDG_CONFIG_HOME", …)` isolation used by the neighbouring tests.

### Blockers / open items

- None blocking review. No Deno installation, worker/ROCm/job-runner edit, service restart, or
  production data change was performed.
- Version 0.8.0 is staged on this branch only; merge/tag/publish and any Aether upgrade belong to
  review.

### Recommended next step

Review `chore/cli-reporting-integration` (both feature lines plus the probe-scope refinement).
If approved: merge into `main`, tag `v0.8.0`, then upgrade the production pipx install and restart
the NLM-locked worker only after isolated validation, and schedule the test-isolation follow-up
for `test_queue_list_*`.

## Review corrections — task 039 (2026-11-05)

Agent: OpenCode (task reassigned from the cancelled Pi agent). Branch
`chore/cli-reporting-integration`, starting at `23626c0`, verified equal to
`origin/chore/cli-reporting-integration` before any edit. GitHub state at
reassignment: `main` still `25b89f1`, integration branch still `23626c0`, version
0.8.0 staged, no Pi corrections pushed. No Pi process was restarted and no
unfinished Pi work was recovered.

Task 039 was claimed by OpenCode and moved from `review/` back to `wip/` before
coding. Scope was limited to the two outstanding review corrections.

Correction commits on `chore/cli-reporting-integration`:
`25fb167` (test isolation), `6a2078d` (Deno queue-client fix + docs),
`3c64e6d` (task/HANDOFF evidence), `9a57bac` (task scope alignment). Branch head
was verified against `origin` after each push; `main` remained at `25b89f1`.

### Correction 1 — queue-list test isolation

`tests/test_cli_queue.py` now installs an autouse fixture that sets
`XDG_CONFIG_HOME` and `XDG_STATE_HOME` under each test's `tmp_path`. The task-037
queue-list tests previously inherited the operator's real config; when it held a
production `expected_uuid`, temporary queues failed identity validation with a
UUID mismatch. The fixture isolates tests only. Application queue-identity
validation, production configuration, and all assertions are unchanged.

### Correction 2 — queue clients never probe Deno

`src/local_transcribe/cli.py` no longer probes Deno on the queue wait path.
Queue mode is Deno-free for `lt transcribe <URL> --no-wait`, `lt transcribe
<URL>` including waiting, `lt batch` including `--wait`, local files, and all
`lt queue *` commands. `lt transcribe <URL> --direct` and `lt batch --direct`
retain the prerequisite warning; `lt doctor` keeps its optional diagnostic and
`lt update` its hard gate. Worker-side failures still reach the waiting client
as `category: detail`; worker execution behaviour was not changed.

Tests changed in `tests/test_deno_startup_warnings.py`:
`test_transcribe_url_wait_does_not_probe_deno` replaces the old wait-warns
assertion; new `test_transcribe_url_wait_surfaces_worker_failure` and
`test_batch_queue_wait_does_not_probe_deno`. Docs updated: `CHANGELOG.md`,
`docs/WHATS_NEW.md`, `README.md`, `AGENTS.md`, and
`docs/QUEUE_DESIGN_DECISIONS.md` §15.

### Files changed

| File | Change |
|------|--------|
| `tests/test_cli_queue.py` | autouse isolated XDG fixture |
| `src/local_transcribe/cli.py` | removed queue-wait Deno probe; comments |
| `tests/test_deno_startup_warnings.py` | wait/batch-wait no-probe + actionable-failure tests |
| `CHANGELOG.md`, `docs/WHATS_NEW.md`, `README.md`, `AGENTS.md` | corrected Deno scope |
| `docs/QUEUE_DESIGN_DECISIONS.md` | append-only §15 |
| `.crules/tasks/review/039-cli-reporting-integration.md` | review status, corrections, evidence |
| `HANDOFF.md` | this section |

### Verification evidence

Environment: system `pytest` 9.0.2 with
`PYTHONPATH=src:$HOME/.local/share/pipx/venvs/local-transcribe/lib/python3.14/site-packages`
and `TERM=dumb`. The real operator config contains a stale `/tmp` smoke queue
with `expected_uuid: 6a058908-116f-41c9-987f-d5f4ef38667e`; that was used as the
production-shaped external config for the isolation check.

| Check | Command | Result |
|-------|---------|--------|
| Correction-targeted | `pytest -q tests/test_cli_queue.py tests/test_deno_startup_warnings.py tests/test_cli_transcribe_queue.py` | **63 passed** |
| Queue-list, empty external XDG | `XDG_CONFIG_HOME=/tmp/opencode/lt-empty-config pytest -q tests/test_cli_queue.py` | **35 passed** |
| Queue-list, production-shaped external XDG | `XDG_CONFIG_HOME=/tmp/opencode/lt-prod-shaped-config pytest -q tests/test_cli_queue.py` | **35 passed** |
| Identity validation preserved | `pytest -q tests/test_queue_paths.py` | **11 passed** |
| Full suite, default operator config | `pytest -q` | 8 failed, **334 passed, 11 skipped** |
| Baseline full suite (`origin/main` @ `25b89f1`), same env | same command in detached worktree | 7 failed, **291 passed, 11 skipped** |
| Full suite, isolated empty XDG | `XDG_CONFIG_HOME=/tmp/opencode/lt-empty-config pytest -q` | **342 passed, 11 skipped** |
| Concurrency case in isolation | `pytest -q tests/test_queue_concurrency.py::test_enqueue_youtube_safe_success` (empty XDG) | **1 passed** |
| Ruff | `uvx ruff check --config pyproject.toml src tests` | **408** vs baseline **413**; changed `cli.py`+`test_cli_queue.py` **26 both**; new Deno suite **0** |
| Syntax | `compileall` + `ast.parse` changed files | OK |
| Build | `uv build --out-dir /tmp/opencode/lt-dist-039` | 0.8.0 sdist + wheel |
| Isolated install | `PIPX_HOME=/tmp/opencode/pipx-039 PIPX_BIN_DIR=/tmp/opencode/pipx-039/bin pipx install …whl` | **0.8.0**; production pipx still **0.7.0** |
| CLI smoke (no Deno on PATH) | isolated `lt`, throwaway queue/XDG | queue list/stats, `transcribe --no-wait`, waiting `transcribe --timeout 0`, `batch --wait` all **0** Deno warnings; `doctor` lists optional Deno row; `--direct` transcribe and `--direct` batch each warn once |
| Diff hygiene | `git diff --check` | clean |

### Failure / baseline comparison

- `tests/test_cli_batch_queue.py` has 6 pre-existing failures on this host,
  reproduced identically on `origin/main` @ `25b89f1` (same six test names).
- `tests/test_queue_concurrency.py::test_enqueue_youtube_safe_success` fails
  only because the operator's stale config pins a different `expected_uuid`; it
  fails identically on baseline `main` and passes in isolation with an empty
  XDG config. Not caused by these corrections.
- `test_concurrent_force_single_current_generation` is the documented timing
  flake; it failed once in the default-env full run and passed in isolation.
- With an isolated empty XDG config the full suite is **342 passed, 11 skipped**
  with no failures.

### Blockers / open items

- None for the two corrections. No merge, tag, branch deletion, worker restart,
  production queue change, or production install was performed.
- Version remains 0.8.0.

### Recommended next step

Review the pushed correction commits on `chore/cli-reporting-integration`. If
approved, merge into `main`, tag `v0.8.0`, then upgrade the production pipx
install and restart the NLM-locked worker only after isolated validation.

## Recovery verification — task 039 (2026-11-05, OpenCode re-check)

State found before this round: local `HEAD` and
`origin/chore/cli-reporting-integration` were both `615497b`, i.e. five commits
ahead of the `23626c0` review baseline. Both required corrections were already
committed and pushed (`25fb167` test isolation, `6a2078d` Deno queue-client
fix), with follow-up docs in `3c64e6d`, `9a57bac`, `615497b`. No correction was
missing locally, so nothing was reset, discarded, or overwritten; this round
re-verified the pushed content independently and recorded fresh evidence.

Version remains **0.8.0**. No merge, tag, branch deletion, production queue
change, worker restart, or production install was performed.

### Verification evidence (this round)

Environment: system `pytest` 9.0.2; `TERM=dumb`;
`PYTHONPATH=src:/home/draeician/.local/share/pipx/venvs/local-transcribe/lib/python3.14/site-packages`.
External XDG fixtures were created under `/tmp/opencode/verify-039/`: an empty
config dir and a production-shaped config with
`expected_uuid: 6a058908-116f-41c9-987f-d5f4ef38667e`.

| Check | Command | Result |
|-------|---------|--------|
| Correction-targeted, operator real config | `pytest -q tests/test_cli_queue.py tests/test_deno_startup_warnings.py tests/test_cli_transcribe_queue.py` | **63 passed** |
| Queue-list isolation, empty external XDG | `XDG_CONFIG_HOME=…/empty-config pytest -q tests/test_cli_queue.py` | **35 passed** |
| Queue-list isolation, production-shaped external XDG | `XDG_CONFIG_HOME=…/prod-shaped pytest -q tests/test_cli_queue.py` | **35 passed** |
| Identity validation preserved | `pytest -q tests/test_queue_paths.py` | **11 passed** |
| Full suite (one run), isolated empty XDG | `XDG_CONFIG_HOME=…/full-xdg-config pytest -q` | **342 passed, 11 skipped** |
| Ruff, branch | `uvx ruff check --config pyproject.toml src tests` | **408** findings |
| Ruff, baseline `25b89f1` | same command in `/tmp/lt-baseline-main` | **413** findings (no new findings) |
| Ruff, changed files | `uvx ruff check … src/local_transcribe/cli.py tests/test_cli_queue.py tests/test_deno_startup_warnings.py` | **26**, same count documented for `cli.py` + `test_cli_queue.py`; new Deno suite **0** |
| Syntax | `python3 -m compileall -q src` + `ast.parse` on changed files | OK |
| Build | `uv build --out-dir /tmp/opencode/verify-039/dist` | 0.8.0 sdist + wheel; METADATA `Version: 0.8.0`, entry point `lt = local_transcribe.cli:main` |
| Secret scan | regex scan of `git diff 23626c0..HEAD` (AWS/GitHub/OpenAI/Slack/Google/private-key/generic-assignment patterns) | **CLEAN**; no credential files tracked |
| Diff hygiene | `git diff --check` (working tree and `origin..HEAD`) | clean |

The full suite passed in this round, so no isolated re-run of a failed test was
needed. The previously documented operator-config failures (`test_cli_batch_queue`
×6, stale `expected_uuid` in `test_enqueue_youtube_safe_success`, and the
force-generation timing flake) are pre-existing and were avoided by the isolated
XDG configuration, exactly as recorded in the prior round.

## Delivery confirmation — task 039 (2026-11-06, OpenCode)

State found: local `HEAD` and `origin/chore/cli-reporting-integration` were both
`571bf2b`, working tree clean, and the remote already contained both review
corrections. Nothing was local-only, so no implementation was repeated and
nothing was reset or overwritten. The correction commits were re-inspected at
this SHA: `25fb167` (autouse XDG isolation in `tests/test_cli_queue.py`) and
`6a2078d` (queue wait path no longer probes Deno; `--direct` transcribe/batch
retain the warning; `lt doctor` and `lt update` unchanged).

This round re-ran the full required verification instead of relying on the
previous record:

| Check | Command | Result |
|-------|---------|--------|
| Correction-targeted, operator real config | `pytest -q tests/test_cli_queue.py tests/test_deno_startup_warnings.py tests/test_cli_transcribe_queue.py` | **63 passed** |
| Queue-list isolation, empty external XDG | `XDG_CONFIG_HOME=…/empty-config pytest -q tests/test_cli_queue.py` | **35 passed** |
| Queue-list isolation, production-shaped external XDG | `XDG_CONFIG_HOME=…/prod-shaped pytest -q tests/test_cli_queue.py` | **35 passed** |
| Identity validation preserved | `pytest -q tests/test_queue_paths.py` | **11 passed** |
| Full suite (one run), isolated empty XDG | `XDG_CONFIG_HOME=…/full-xdg-config pytest -q` | **342 passed, 11 skipped** |
| Ruff, branch | `uvx ruff check --config pyproject.toml src tests` | **408** findings |
| Ruff, baseline `25b89f1` | same command in `/tmp/lt-baseline-main` | **413** findings (no new findings) |
| Ruff, changed files | `uvx ruff check … cli.py test_cli_queue.py test_deno_startup_warnings.py` | **26**, unchanged |
| Syntax | `python3 -m compileall -q src` + `ast.parse` on changed files | OK |
| Build | `uv build --out-dir /tmp/opencode/verify-039-1106/dist` | 0.8.0 sdist + wheel; METADATA `Version: 0.8.0`, entry point `lt = local_transcribe.cli:main` |
| Secret scan | regex scan of `git diff 23626c0..HEAD` | **CLEAN**; no credential files tracked |
| Diff hygiene | `git diff --check` (working tree and `origin..HEAD`) | clean |

No new Decision Journal entry was required: `docs/QUEUE_DESIGN_DECISIONS.md` §15
already records both review decisions (queue clients never probe Deno; queue-list
tests own their XDG configuration) and matches the code at this SHA. Version
remains **0.8.0**. No merge, tag, branch deletion, production queue change,
worker restart, or production install was performed. No blockers.

---

## Ruff verification gate cleanup (task 040, 2026-11-06, OpenCode)

Branch `chore/ruff-gate-cleanup`, based on `origin/main` @ `25b89f1`. Task 040
was opened in `.crules/tasks/wip/` and moved to `review/` only after the gate and
verification checks passed. Task 039 and `chore/cli-reporting-integration` were
left untouched (that branch remains at `94b40db`).

### Baseline

`uvx ruff check --config pyproject.toml src tests` reported **413 findings**
across 49 files. The largest groups were `UP045` (125), `UP006` (109), `UP035`
(40), `B008` (21), `F401` (21), and `BLE001` (15); the remainder were autofixable
style/import findings plus `F841`, `PLW1510`, `S110`, `S112`, `FURB192`, `TRY004`,
`TRY203`, and one-off `SIM`/`RUF`/`PYI` items.

### Changes

| Commit | Scope |
|--------|-------|
| `41c8dce` | Import sorting (`I001`) |
| `832104b` | PEP 604/585 annotations and `collections.abc` imports (`UP006`, `UP045`, `UP035`, `UP037`, `UP012`) |
| `d5a1a36` | Remaining safe autofixes: `F541`, `PIE790`, `FURB122`, `RUF022`, genuinely unused `RUF100` |
| `628c4c5` | Manual findings: `B008`, `BLE001`, `S110`, `S112`, `PLW1510`, `F841`, `FURB192`, `TRY004`, `TRY203`, `F811`, `PYI034`, `UP022`, `RUF013`, `RUF059`, `RUF007`, `SIM103`, `SIM117` |

Notable decisions:

- **B008** was fixed properly with `typing.Annotated`, not suppressed. Only
  Path-typed options triggered it; option names, defaults, and required flags
  were preserved. `lt queue init --help` still marks `--queue-dir` required, and
  CLI tests pass.
- **BLE001/S110/S112** handlers were narrowed to the exceptions each call can
  raise; best-effort probes use `contextlib.suppress`. Two intentional
  boundaries (CLI `doctor`, per-video batch loop) retain targeted, justified
  `# noqa: BLE001`. Four earlier broad-catch suppressions were removed, so the
  branch has a net reduction in suppressions.
- **TRY004** invalid config root/`queue` values now raise `TypeError`; the
  producer fallback in `queue_api` catches `TypeError` too, preserving fallback.
  Missing auth profiles still raise `ValueError`; malformed profile entries raise
  `TypeError`.
- **PYI034** uses `Self` imported under `TYPE_CHECKING`, so no runtime
  dependency is added and Python 3.10 imports still work.
- **F811** renamed the Typer callback to `app_callback`; the console-script
  entry point `main` is unchanged.
- `pyproject.toml` was not modified: no rule selection, ignore list, or
  dependency changed. Version remains **0.7.0** (this branch is based on
  `main`, which has not taken the 0.8.0 release line).

### Verification evidence

Environment: system `pytest` 9.0.2; `TERM=dumb`;
`PYTHONPATH=src:/home/draeician/.local/share/pipx/venvs/local-transcribe/lib/python3.14/site-packages`.
Queue-related tests used isolated XDG paths under `/tmp/opencode/task040/`.

| Check | Command | Result |
|-------|---------|--------|
| Baseline gate | `uvx ruff check --config pyproject.toml src tests` | **413 findings** |
| Final gate | same command | **0 findings — All checks passed** |
| Targeted regression (CLI/worker/queue, default env) | `pytest -q tests/test_cli_queue.py tests/test_cli_worker.py tests/test_cli_batch_queue.py tests/test_cli_transcribe_queue.py tests/test_cookie_refresh.py tests/test_config_write.py tests/test_worker_lock.py tests/test_worker_model_idle.py tests/test_worker_atomic_transitions.py tests/test_downloader_errors.py tests/test_rocm_support.py tests/test_update.py` | **117 passed, 6 failed** |
| Batch tests, isolated XDG | `XDG_CONFIG_HOME=… pytest -q tests/test_cli_batch_queue.py` | **10 passed** |
| Baseline batch tests (`origin/main` @ `25b89f1`) | same command in `/tmp/lt-baseline-main` | **same 6 failures, 4 passed** |
| Full suite, one run | `XDG_CONFIG_HOME=… XDG_STATE_HOME=… pytest -q` | **298 passed, 11 skipped** |
| Baseline full suite (`origin/main`) | same command in `/tmp/lt-baseline-main` | **1 failed, 297 passed, 11 skipped** — documented timing flake only |
| Syntax | `compileall -q src tests` + import every package module | OK |
| CLI help smoke | `queue init --help`, `queue add --help`, `cookies refresh --help`, `worker run --help` | required/defaults intact |
| Build | `uv build --out-dir /tmp/opencode/task040/dist` | 0.7.0 sdist + wheel; metadata `Version: 0.7.0` |
| Secret scan | high-confidence pattern scan of `git diff` | **CLEAN** |
| Diff hygiene | `git diff --check` | clean |

Branch diff versus `origin/main`: **51 files, +502/−403**.

The six `test_cli_batch_queue` failures are pre-existing on `origin/main` under
the operator's config (stale `expected_uuid`); they pass with isolated XDG. The
only baseline full-suite failure was
`test_queue_concurrency.py::test_concurrent_force_single_current_generation`,
the documented timing flake; the branch run passed it.

### Blockers

None. No merge, tag, release, branch deletion, ROCm change, worker restart,
production queue change, or system configuration change was performed.

## Final integration — v0.8.0 + zero-finding Ruff (task 041, 2026-11-06, OpenCode)

Branch `chore/0.8-final-integration`, based on `origin/main` @ `25b89f1`.
Task 041 was opened in `.crules/tasks/wip/` and moved to `review/` only after the
combined gate passed. Both reviewed lines were merged without squashing or
rewriting history:

| Merge | Parents | Subject |
|-------|---------|---------|
| `bb09499` | `c7e97a1` + `94b40db` | Merge `chore/cli-reporting-integration` |
| `542ad31` | `bb09499` + `7de3789` | Merge `chore/ruff-gate-cleanup` |
| `94a92b0` | — | Markdown hard-break hygiene |

### Conflict resolutions (combined, not preferred)

- `__init__.py`: `__version__ = "0.8.0"` **and** `contextlib.suppress` ROCm
  preload.
- `cli.py`: Deno-free queue paths **and** `app_callback` rename (entry point
  remains `main`).
- `cli_queue.py`: positional `STATUS|UUID` filtering, defaults
  `limit=60/offset=0/sort=oldest` **and** `Annotated` Typer options.
- `queue_store.py`: 039 `query_executions` filtering/sorting/pagination under
  040 typing (auto-merged).
- `HANDOFF.md`: both histories retained.
- `docs/QUEUE_DESIGN_DECISIONS.md`: 039 §12–§15 preserved; 040 Ruff section
  renumbered to §16; task 041 decisions appended as §17.
- `README.md` / `project_spec.md`: two integrated Markdown two-space hard breaks
  converted to backslash hard breaks so `git diff --check` is clean with
  unchanged rendering.

### Verification evidence

Environment: system `pytest` 9.0.2; `TERM=dumb`;
`PYTHONPATH=src:/home/draeician/.local/share/pipx/venvs/local-transcribe/lib/python3.14/site-packages`.
Isolated `XDG_CONFIG_HOME`/`XDG_STATE_HOME`/`XDG_CACHE_HOME` under
`/tmp/opencode/task041/`. This host has no Deno, so missing-Deno behavior was
exercised throughout.

| Check | Command | Result |
|-------|---------|--------|
| Targeted queue/Deno | `pytest -q tests/test_cli_queue.py tests/test_deno_startup_warnings.py tests/test_queue_store.py tests/test_queue_paths.py tests/test_cli_batch_queue.py tests/test_cli_transcribe_queue.py tests/test_cli_worker.py` | **99 passed** |
| Full suite, one run | `pytest -q` | **342 passed, 11 skipped** |
| Ruff gate | `uvx ruff check --config pyproject.toml src tests` | **0 findings — All checks passed** |
| Ruff config | diff vs `origin/main` | unchanged |
| Syntax | `compileall` + import every module | OK; version `0.8.0` |
| Build | `uv build --out-dir /tmp/opencode/task041/dist` | 0.8.0 sdist + wheel |
| Wheel audit | `METADATA`, `entry_points.txt`, contents | `Version: 0.8.0`; `lt = local_transcribe.cli:main`; `queue_listing.py` included |
| Isolated pipx | disposable `PIPX_HOME`/`PIPX_BIN_DIR` | local-transcribe **0.8.0** |
| CLI version | `lt version` | `local-transcribe version 0.8.0` |
| CLI help | `lt --help`, `lt queue list --help` | no Deno warning; positional `[STATUS|UUID]`, `--status`, `--limit` 60, `--offset`, `--sort` |
| Queue init/add | disposable queue | initialized; 3 executions enqueued |
| `queue list pending` | `lt queue list pending` | 3 rows; `limit=60, offset=0, sort=oldest` |
| `queue list failed` | `lt queue list failed` | 0 rows, no error |
| `queue list completed --sort newest` | same | 0 rows; `sort=newest` |
| UUID selection | `lt queue list <UUID>` | exactly 1 row |
| Pagination | `lt queue list pending --offset 1 --limit 1` | 2nd row; `Showing 2-2 of 3` |
| Deno quiet on queue ops | `queue list` + `queue stats` grep | **NO DENO WARNING** |
| Missing Deno, doctor | `lt doctor` without Deno | reports `deno not found ...`; no crash |
| Direct YouTube check | `lt transcribe <URL> --direct --output-dir …` | `⚠️ Warning: Deno runtime not detected` before download |
| Dedicated Deno suite | `pytest -q tests/test_deno_startup_warnings.py` | **14 passed** |
| Test weakening | removed `assert`/`def test_` in diff | none removed; 353 tests collected |
| Secret scan | high-confidence pattern scan of branch diff | **CLEAN** |
| Diff hygiene | `git diff --check origin/main..HEAD` | clean |
| WIP marker | `git ls-files` | never tracked |

Branch diff versus `origin/main`: **68 files, +3009/−462**.

### Blockers

None. No merge to `main`, tag, release, branch deletion, production service
change, worker restart, or system configuration change was performed.

## Approval — task 041 merge (task 042, 2026-11-06)

ChatGPT reviewed and approved task 041 for merging into `main`. Drae
authorized completing the project work and cleaning up the repository so that
only `main` remains. No production deployment, system change, worker restart, or
installed-environment upgrade is authorized.

At approval time the integration head was
`9a3c2c1d39c06687c89473b59776c5cbdf63ac2c` and `origin/main` was
`25b89f1326751a3b0781051331822c50ffde3b19`. Tasks 037–041 are moved from
`review/` to `done/` with their `Status` fields set to `done`; this approval
section is committed on the approved integration branch before the merge.

## Final merge and repository cleanup (task 042, 2026-11-06, OpenCode)

### Merge

| Item | Value |
|------|-------|
| Target | `main` |
| Previous `origin/main` | `25b89f1326751a3b0781051331822c50ffde3b19` |
| Approved integration head | `9a3c2c1` (subsequently `0495376` after task closure) |
| Merge source | `0495376080551805d260f784ffb49f5ba74f04bb` |
| **Final merge commit** | **`e3244ffa37bd470f1e71cc4c9f3f9ae565a41351`** |
| Merge parents | `25b89f1` (main) + `0495376` (integration) |
| Strategy | `git merge --no-ff`; no squash, rebase, or history rewrite |

The merged tree is byte-identical to the approved integration tree
(`git diff --stat chore/0.8-final-integration` was empty at merge time). Both
integration histories (`94b40db`, `7de3789`) are ancestors of the final merge.

### Post-merge verification (on `main`)

Environment: system `pytest` 9.0.2; `TERM=dumb`;
`PYTHONPATH=src:/home/draeician/.local/share/pipx/venvs/local-transcribe/lib/python3.14/site-packages`;
isolated XDG under `/tmp/opencode/task042/`. The host has no Deno.

| Check | Command | Result |
|-------|---------|--------|
| Ruff gate | `uvx ruff check --config pyproject.toml src tests` | **0 findings — All checks passed** |
| Full suite | `pytest -q` | **342 passed, 11 skipped** |
| Targeted queue/Deno (pre-merge) | queue/Deno/worker test files | **99 passed** |
| Version | `pyproject.toml`, `__init__.py` | **0.8.0** |
| Build | `uv build --out-dir /tmp/opencode/task042/dist` | 0.8.0 sdist + wheel |
| Wheel audit | `METADATA`, `entry_points.txt` | `Version: 0.8.0`; `lt = local_transcribe.cli:main`; `queue_listing.py` present |
| CLI version | `python -m local_transcribe.cli version` | `local-transcribe version 0.8.0` |
| Queue list interface | `queue list --help` | `[STATUS|UUID]`, `--status`, `--limit` 60, `--offset`, `--sort` |
| `queue list pending` | disposable queue | worked; defaults `limit=60, offset=0, sort=oldest` |
| `queue list failed` | disposable queue | worked (0 rows) |
| `queue list completed --sort newest` | disposable queue | worked (0 rows, `sort=newest`) |
| Missing Deno | queue list/stats scan | **no Deno warning** |

The documented pre-existing timing flake
`test_queue_concurrency.py::test_concurrent_force_single_current_generation`
was observed intermittently: 1 failure in 5 isolated reruns on `main` and 1 in 5
on the untouched `origin/main` baseline (`25b89f1`). It is unrelated to this
integration; the required full-suite runs passed.

### Task closure

Tasks 037, 038, 039, 040, and 041 were moved from `.crules/tasks/review/` to
`.crules/tasks/done/` and their `Status` fields set to `done` in
`0495376`, before the merge. Histories and verification records are preserved.

### Branch cleanup

All seven obsolete branches were audited before deletion. Six were direct
ancestors of `main`; `fix/deno-startup-warning` was not an ancestor because it
was cherry-picked, so it was verified separately:

- `git cherry` marks its code commit `3b78d5c` as patch-equivalent (`-`).
- Its task 038 record is present under `done/` (only path/status changed).
- Its AGENTS/README/CHANGELOG/HANDOFF/Decision-Journal documentation and Deno
  tests are present in `main` (some lines expanded or renamed by task 039).

Deleted remote branches (normal `git push origin --delete`, no force):

`chore/0.8-final-integration`, `chore/cli-reporting-integration`,
`chore/ruff-gate-cleanup`, `feat/queue-list-reporting`,
`fix/deno-startup-warning`, `feat/queue-creation`, `fix/model-idle-unload`.

Deleted the corresponding local branches (`-d` for ancestors, `-D` only for the
verified patch-equivalent branch). Final state: local and remote contain only
`main`.

### PR and release state

- Open pull requests: **none** (`gh pr list --state open` returned `[]`).
- Version: **0.8.0**.
- Release tags: existing tags are `v0.4.0`, `v0.5.0`, `v0.5.1`. **No `v0.8.0`
  tag was created** and no package was published; 0.8.0 is versioned but not
  released. Production installation remains a separate task.

### Operational safety

No production queue records, NFS configuration, AMD ROCm libraries, worker
services, or installed pipx environments were modified. No deployment, worker
restart, or system configuration change was performed.

## Standing constraint

Do not make source-code changes from the coordinator session. For any implementation, debugging fix, refactor, or test change, prepare an OpenCode/pi prompt that includes the mandatory HANDOFF protocol and coding-agent prompt standard above.

## Pre-deploy gate remediation — task 044 (2026-11-07, OpenCode)

### Context

Task 043's corrected preflight (`b6d504f`) left the required gate red on
untouched `origin/main` `abdedbe`: six ANSI-output test failures, one ext4
foreground-wait race caused by `atomic_state_transition()` renaming before
writing metadata, and an intermittent concurrent-force generation failure. Task
043 and its worktree were preserved and not merged or modified.

### Branch and worktree

| Item | Value |
|------|-------|
| Branch | `fix/predeploy-queue-gate` |
| Base | `origin/main` @ `abdedbe88b8b44d24582520264f463bebcc83109` |
| Worktree | `/tmp/opencode/local_transcribe-gate-remediation` (ext4) |
| Host | `nomnom`, user `draeician`, unprivileged |
| Task record | `.crules/tasks/review/044-predeploy-gate-remediation.md` |
| Implementation commit | `bff5ca6` (`fix(queue): publish terminal metadata atomically`) |
| Correction commit | `e62b383cffff24e61eeb9dbae67e0024e15d18d0` (`fix(queue): serialize claim/cancel and validate recovery`) |
| Superseded revision | `fe18821c0d5eb63c21a52a138d75c726860d8f31` — historical rejected revision; its evidence is not reused |

### Changes

- **Terminal metadata publication** (`atomic_files.py`, `queue_wait.py`):
  transitions write the final JSON to a temp, fsync it, and hard-link it to the
  destination before unlinking the source. A destination can never be observed
  with stale pre-transition metadata. `locate_execution()` and
  `wait_for_execution()` only trust a terminal directory entry when its embedded
  status matches the directory.
- **Interrupted-publication recovery** (`worker.py`): every same-named record is
  parsed and validated (JSON/schema, execution id, source identity, generation,
  status directory, attempts, transition metadata). Recovery keeps the unique
  reachable transition sink; malformed, conflicting, cyclic, or unreachable
  records stay intact with an explicit warning. `recover_processing()` and
  `promote_retries()` skip unresolved ids so repeated recovery cannot lose
  records or duplicate execution.
- **Concurrent force generations** (`source_reservations.py`,
  `queue_store.py`): `keys/<source>.json` is the sole reservation authority
  again. The rejected `keys/.advance/` ledger is removed. Concurrent advances
  and repairs use a short-lived advisory `fcntl.lockf` lock plus a per-path
  thread lock under `.locks/`; lock files are coordination only, contain no
  state, and are never removed. Compare-and-swap helpers re-read after writing
  and report a lost race. `repair_source()` skips pending executions newer than
  the reservation. A repair after a forced generation updates the keys pointer
  and remains claimable.
- **Claim versus cancellation** (`worker.py`, `queue_store.py`): both take the
  same per-execution advisory lock. Claim-first makes cancel fail closed with a
  processing-specific error and never publishes a contradictory cancelled
  record; cancel-first makes the claim return `None`, so the worker does not
  execute the job. Interrupted cancel is resolved only when the cancelled
  successor validates against the pending record. No sleep-based fix.
- **ANSI tests** (`tests/conftest.py` and six tests): strip CSI/SGR escapes from
  captured output; all behavioral assertions and exit-code checks are unchanged.
- **Regression tests**: deterministic claim/cancel interleavings (including a
  paused-claim window), real `QueueStore` forced-generation repair, keys-file
  compatibility for direct readers and interrupted writes, fail-closed
  duplicate recovery, plus the existing stale-terminal and crash-window tests.
- **Version**: patch release **0.8.1** synchronized across `pyproject.toml`,
  `src/local_transcribe/__init__.py`, `project_spec.md`, `CHANGELOG.md`, and
  `docs/WHATS_NEW.md`. No tag or publish.

### Review corrections implemented (2026-11-07)

| Correction | Implementation |
|------------|----------------|
| Exclusive worker-claim versus cancellation | `worker.claim_execution()` and `QueueStore.cancel_pending()` share `.locks/executions/<id>.lock`. Claim-first makes cancel fail closed with a processing-specific error and never publishes a contradictory cancelled record; cancel-first makes the claim return `None`, so the worker does not execute the job. Interrupted cancel is resolved only when the cancelled successor validates against the pending record. No sleep-based synchronization. |
| Incomplete-reservation repair after a forced generation | `try_update_current_execution()` repairs `keys/<source>.json` under the reservation lock. The replacement becomes current at the same generation, is not cancelled by an earlier same-generation claim, and remains claimable. Covered by a real `QueueStore` lifecycle test (enqueue → force → delete forced pending → enqueue → claim). |
| Compatibility with the existing `keys/<source>.json` authority | `keys/<source-key>.json` is the sole authority again; the `.advance` ledger is removed. Advisory `fcntl.lockf` locks serialize read-modify-write without adding durable state, so older direct key-file readers and the ref reconciler agree with the application. No queue migration or ref change. |
| Validated, fail-closed duplicate recovery | Every same-named record is parsed and validated (JSON/schema, execution id, source identity, generation, status directory, attempts, transition metadata). Recovery keeps the unique reachable transition sink; malformed, conflicting, cyclic, or unreachable records stay intact with an explicit warning. |

A prior delivery check at `fe18821` found none of these corrections implemented;
that finding is retained as history and superseded by the implementation above.
Its probe evidence remains at
`~/.local/share/local-transcribe/evidence/20261107T-gate044-corrections/`.

### Verification (exact results — corrective tree)

Environment: isolated venv `/tmp/opencode/predeploy-gate-venv`, Python 3.12.3,
pytest 9.2.0, ruff 0.17.4, build 1.6.1, pipx 1.4.3; ext4 worktree; isolated
`HOME`/`XDG_*`/`TMPDIR`. Production config, queue, archives, and services were
not loaded or touched. Results apply to the corrective commit
`e62b383cffff24e61eeb9dbae67e0024e15d18d0`, not to `fe18821`.

| Check | Result |
|-------|--------|
| Targeted regressions (claim/cancel, recovery validation, concurrency, reservations, worker transitions, CLI queue/transcribe/batch, Deno warnings, update) | **134 passed** |
| Full suite, ext4, default color | **368 passed, 11 skipped, 1 warning**, exit 0 |
| Full suite, ext4, `NO_COLOR=1` | **368 passed, 11 skipped, 1 warning**, exit 0 |
| Concurrency stress, ext4 | **50/50** runs passed (predefined mixed force/claim-cancel/reservation selection) |
| Ruff canonical | **All checks passed** |
| Syntax (`compileall`) | clean |
| Build | `local_transcribe-0.8.1` wheel + sdist; wheel `Version: 0.8.1`, entry point `lt = local_transcribe.cli:main`, `queue_locks.py` present |
| Wheel SHA256 | `4837c88d5406ccf0ae5c076111450484117f5ba61e9366be7b7e11ebd6a4238b` (wheel); `5a7c750e548305a4630c57c6b25f9096d47e888dc5545108919dfc3506c69d27` (sdist) |
| Isolated CLI smoke | `lt version` **0.8.1**; queue init/add/list/stats and `transcribe --no-wait` worked on a disposable queue (3 pending) |
| Isolated pipx upgrade | 0.8.0 wheel installed, then forced 0.8.1 wheel; `lt version`, module, metadata, and `pip show` all **0.8.1** |

Baseline reproduction on rejected source `fe18821`: the new/changed test
selection (claim/cancel, recovery validation, forced repair, keys authority,
interrupted write, repair helpers) produced **13 failed, 3 passed**, exit 1. The
three passes are the basic claim-first/cancel-first paths the rejected tree
already handled. The new assertions therefore exercise the defects rather than
passing by construction.

### Evidence

Durable sanitized evidence:
`~/.local/share/local-transcribe/evidence/20261107T-gate044-corrections-impl/` —
`baseline-fe18821-new-tests.log`, `targeted.log`, `full-default.log`,
`full-nocolor.log`, `stress-50.log`, `ruff.log`, `compileall.log`, `build.log`,
`wheel-sha256.txt`, `cli-smoke.log`, `pipx-install.log`,
`pipx-upgrade-080-081.log`, and `baseline-import.txt`.

Earlier evidence at `~/.local/share/local-transcribe/evidence/20261107T-gate044/`
and `.../20261107T-gate044-corrections/` remains historical for `fe18821` and the
delivery check; it is not reused here.

### Unavailable checks

Real NFSv3/NLM multi-client tests were not run: the 10
`tests/test_nfs_integration.py` tests skipped because `LT_NFS_QUEUE_DIR` was not
set. Local ext4 passes do not prove multi-host NFS/NLM behavior. GPU/ROCm
transcription was not run. The opt-in real pipx-install pytest was skipped; an
equivalent isolated pipx install/upgrade was performed manually.

### Blockers

None for review. Production deployment remains blocked by the separate task 043
prerequisites: artifact staging, rollback rehearsal, Minion config/identity, and
the NFSv4.2 producer decision.

### Next action

Review the corrective commit on `fix/predeploy-queue-gate`. Do not merge, tag,
publish, deploy, or clean up branches from this task.

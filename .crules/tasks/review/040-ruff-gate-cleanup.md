# Task 040 — Restore the Ruff verification gate

Mode: Cleanup agent
Branch: `chore/ruff-gate-cleanup` (based on `origin/main` @ `25b89f1`)
Task created: 2026-11-06 (UTC) — created in `.crules/tasks/wip/`, moved to `review/` after verification.
Owner: OpenCode
Status: `review` — zero-finding Ruff gate, verification complete

## Objective

Restore the repository's Ruff verification gate to zero findings by correcting the
413 pre-existing findings on `origin/main`. This is a lint-only cleanup: no
application behavior, runtime dependency, ROCm path, worker, queue, or system
configuration changes.

## Scope

- Existing Ruff findings only, as reported by the repository command:
  `uvx ruff check --config pyproject.toml src tests`
- No new features, refactors, dependency changes, or version bumps.
- Do not merge, tag, release, or delete branches.

## Baseline (2026-11-06, origin/main @ 25b89f1)

`uvx ruff check --config pyproject.toml src tests` → **413 findings**.

| Rule | Count | Resolution |
|------|------:|------------|
| UP045 non-pep604-annotation-optional | 125 | Ruff autofix |
| UP006 non-pep585-annotation | 109 | Ruff autofix |
| UP035 deprecated-import | 40 | Autofix after typing rewrite |
| B008 function-call-in-default-argument | 21 | Typer `Annotated` migration |
| F401 unused-import | 21 | Ruff autofix |
| BLE001 blind-except | 15 | Narrowed to raised types |
| I001 unsorted-imports | 13 | Ruff autofix |
| RUF100 unused-noqa | 10 | Removed (genuinely unused) |
| F541 f-string-missing-placeholders | 9 | Ruff autofix |
| UP037 quoted-annotation | 8 | Ruff autofix |
| PLW1510 subprocess-run-without-check | 8 | Explicit `check=False` |
| F841 unused-variable | 6 | Removed dead assignments |
| S110 try-except-pass | 4 | `contextlib.suppress` / removed |
| PIE790 unnecessary-placeholder | 4 | Ruff autofix |
| FURB192 sorted-min-max | 3 | `min(...)` |
| S112 try-except-continue | 2 | Narrowed handlers |
| TRY004 type-check-without-type-error | 2 | `TypeError` for type mismatch |
| TRY203 useless-try-except | 2 | Removed re-raise-only wrapper |
| FURB122 for-loop-writes | 1 | Ruff autofix |
| RUF013 implicit-optional | 1 | Explicit `| None` |
| SIM117 multiple-with-statements | 1 | Combined `with` |
| SIM103 needless-bool | 1 | Direct return |
| PYI034 non-self-return-type | 1 | `Self` under `TYPE_CHECKING` |
| F811 redefined-while-unused | 1 | Renamed CLI callback |
| UP022 replace-stdout-stderr | 1 | `capture_output=True` |
| UP012 unnecessary-encode-utf8 | 1 | Ruff autofix |
| RUF022 unsorted-dunder-all | 1 | Ruff autofix |
| RUF059 unused-unpacked-variable | 1 | Renamed to `_cache` |
| RUF007 zip-instead-of-pairwise | 1 | `itertools.pairwise` |

Files affected: 49.

### Baseline findings by file

| File | Findings |
|------|---------:|
| `services/reconcile.py` | 42 |
| `cli_queue.py` | 39 |
| `utils/hardware.py` | 29 |
| `utils/doctor.py` | 27 |
| `cli.py` | 26 |
| `services/pipeline.py` | 20 |
| `services/queue_models.py` | 19 |
| `services/verify_status.py` | 19 |
| `utils/ytdlp_update.py` | 19 |
| `services/downloader.py` | 16 |
| `services/status_store.py` | 15 |
| `utils/rocm.py` | 13 |
| `services/transcriber.py` | 11 |
| `utils/files.py` | 11 |
| `services/config.py` | 9 |
| `cli_cookies.py` | 8 |
| `services/worker.py` | 8 |
| `services/download_admission.py` | 7 |
| `cli_worker.py` | 6 |
| `services/queue_store.py` | 6 |
| `services/rate_limiter.py` | 6 |
| `services/cookie_refresh.py` | 5 |
| `services/job_runner.py` | 4 |
| `services/mount_validation.py` | 4 |
| `services/worker_lock.py` | 4 |
| `services/model_cache.py` | 3 |
| `services/queue_reporting.py` | 3 |
| `services/queue_wait.py` | 3 |
| `tests/test_portable_install.py` | 3 |
| `services/queue_paths.py` | 2 |
| `services/transcript_publish.py` | 2 |
| `services/worker_state.py` | 2 |
| `tests/test_worker_atomic_transitions.py` | 2 |
| `tests/test_worker_error_handling.py` | 2 |
| `tests/test_worker_model_idle.py` | 2 |
| `utils/youtube.py` | 2 |
| `__init__.py` | 1 |
| `logging_setup.py` | 1 |
| `queue_api.py` | 1 |
| `services/atomic_files.py` | 1 |
| `services/source_reservations.py` | 1 |
| `services/worker_errors.py` | 1 |
| `tests/test_atomic_files.py` | 1 |
| `tests/test_cli_batch_queue.py` | 1 |
| `tests/test_cli_transcribe_queue.py` | 1 |
| `tests/test_cookie_refresh.py` | 1 |
| `tests/test_model_cache.py` | 1 |
| `tests/test_rocm_support.py` | 1 |
| `tests/test_transcript_publish.py` | 1 |
| `tests/test_worker_job_runner.py` | 1 |

## Constraints

- Preserve Python 3.10+ compatibility (`target-version = "py310"`).
- Do not disable rules, add blanket `noqa`, weaken `pyproject.toml`, or suppress
  findings just to pass.
- Prefer Ruff's safe autofixes where the transformation is provably
  behavior-preserving.
- Keep unrelated refactoring out of the branch.

## Acceptance criteria

- [x] `uvx ruff check --config pyproject.toml src tests` → **0 findings**.
- [x] No rule/config weakening and no blanket suppressions. Only two justified
      boundary `# noqa: BLE001` remain (CLI doctor, per-video job boundary); four
      pre-existing broad-catch suppressions were removed.
- [x] Relevant regression tests and one full pytest run pass (pre-existing
      baseline failures confirmed identical on `origin/main`).
- [x] Syntax, package build, secret scan, and `git diff --check` clean.
- [x] Version unchanged at **0.7.0** (this branch is based on `main`, which has
      not taken the 0.8.0 release branch).
- [x] Task/HANDOFF/Decision Journal updated; branch pushed; remote SHA verified;
      task moved to `review/`.

## Implementation notes

- Import ordering (`41c8dce`), PEP 604/585 annotations and `collections.abc`
  imports (`832104b`), and the remaining safe autofixes (`d5a1a36`) were applied
  first; Ruff's safe fixer only.
- Manual findings (`628c4c5`):
  - **B008**: Path-typed Typer options use `typing.Annotated` with the option
    call inside the annotation and the real default on the parameter. Option
    names, defaults, and required flags were preserved; `lt … --help` smoke
    checks and CLI tests pass.
  - **BLE001/S110/S112**: handlers narrowed to the exception types each call can
    raise; best-effort probes use `contextlib.suppress`. Two intentional
    boundaries (CLI doctor, per-video job loop) keep a targeted, justified
    `# noqa: BLE001` so one failure cannot abort diagnostics or the batch.
  - **TRY004**: invalid config root/`queue` values now raise `TypeError`; the
    producer fallback in `queue_api` also catches `TypeError` so enqueue
    fallback behavior is preserved. Missing auth profiles still raise
    `ValueError`; wrong profile shape raises `TypeError`.
  - **PYI034**: `WorkerLock.__enter__` annotates `Self` imported under
    `TYPE_CHECKING`; no runtime dependency is added and Python 3.10 imports
    remain clean.
  - **F811**: the Typer callback was renamed `app_callback`; the console-script
    entry point `main` is unchanged.
- No dependency was added. `pyproject.toml` was not modified.

## Verification evidence

Environment: system `pytest` 9.0.2; `TERM=dumb`;
`PYTHONPATH=src:/home/draeician/.local/share/pipx/venvs/local-transcribe/lib/python3.14/site-packages`.
Tests that touch queue config ran with isolated XDG paths under
`/tmp/opencode/task040/`.

| Check | Command | Result |
|-------|---------|--------|
| Baseline gate | `uvx ruff check --config pyproject.toml src tests` | **413 findings** |
| Final gate | same command | **0 findings — All checks passed** |
| Targeted regression (CLI/worker/queue) | `pytest -q tests/test_cli_queue.py tests/test_cli_worker.py tests/test_cli_batch_queue.py tests/test_cli_transcribe_queue.py tests/test_cookie_refresh.py tests/test_config_write.py tests/test_worker_lock.py tests/test_worker_model_idle.py tests/test_worker_atomic_transitions.py tests/test_downloader_errors.py tests/test_rocm_support.py tests/test_update.py` | **117 passed, 6 failed** (the six pre-existing `test_cli_batch_queue` failures) |
| Batch tests with isolated XDG | `XDG_CONFIG_HOME=… pytest -q tests/test_cli_batch_queue.py` | **10 passed** |
| Baseline batch tests (origin/main) | same command in `/tmp/lt-baseline-main` | **same 6 failures, 4 passed** |
| Full suite, one run | `XDG_CONFIG_HOME=… XDG_STATE_HOME=… pytest -q` | **298 passed, 11 skipped** |
| Baseline full suite (origin/main @ 25b89f1) | same command in `/tmp/lt-baseline-main` | **1 failed, 297 passed, 11 skipped** — documented timing flake `test_concurrent_force_single_current_generation` |
| Syntax | `python3 -m compileall -q src tests` + import every package module | OK |
| CLI help smoke | `python -m local_transcribe.cli {--help,queue init --help,queue add --help,cookies refresh --help,worker run --help}` | required/defaults intact |
| Build | `uv build --out-dir /tmp/opencode/task040/dist` | 0.7.0 sdist + wheel; metadata `Version: 0.7.0` |
| Secret scan | high-confidence pattern scan of `git diff` | **CLEAN** |
| Diff hygiene | `git diff --check` | clean |

Branch diff vs `origin/main`: **51 files, +502/−403**.

## Blockers

None. The only non-mechanical changes are documented above: Typer `Annotated`
options, exception-type narrowing, `TypeError` for malformed config types, and
two justified boundary suppressions.

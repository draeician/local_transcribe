# Task 038 — Remove unconditional Deno startup warning

| Field | Value |
|-------|-------|
| **Status** | `review` |
| **Branch** | `fix/deno-startup-warning` |
| **Base** | `origin/main` @ `25b89f1` |
| **Type** | `fix` (patch) |
| **Owner** | pi |
| **Phase** | 5 (CLI/reporting UX) |
| **Depends on** | 022, 024 |
| **Blocks** | — |

## Summary

The root Typer callback in `src/local_transcribe/cli.py` probed Deno for every
subcommand, so queue reporting and other non-download commands printed an
unrelated "Deno runtime not detected" install-guidance block. Deno is a YouTube
download prerequisite only; the probe now runs on download paths alone.

## Required behavior

- `lt queue list/show/stats/path/doctor` produce no Deno warning and never call
  the Deno availability probe.
- `lt version`, `--version`, command help, and other non-download commands stay
  quiet.
- YouTube download paths keep the Deno prerequisite check and warning:
  `lt transcribe <URL>` (queue mode and `--direct`) and legacy `--direct` batch.
- Local audio transcription does not require or probe Deno.
- `lt doctor` keeps "Deno Runtime (YouTube)" as an optional diagnostic.
- Missing Deno must never block inspecting or managing queue records.
- No Deno installation, no system configuration change.

## Acceptance criteria

- [x] Startup callback performs no Deno probe (`_startup_warnings_shown` removed).
- [x] Queue reporting commands are quiet and invoke zero probes (test asserts the
      probe was never called, not merely that output looks clean).
- [x] `lt transcribe <URL>` still warns with the HTTP 403 guidance.
- [x] Local-file `lt transcribe` enqueues without probing Deno.
- [x] Queue-mode batch bulk-enqueue does not probe; `--direct` batch still warns.
- [x] `lt doctor` still lists Deno as an optional diagnostic check.
- [x] Version/help output unaffected (exit 0, no warning).
- [x] Regression tests fail on `origin/main`, pass on this branch.

## Files changed

| File | Change |
|------|--------|
| `src/local_transcribe/cli.py` | removed the callback-level Deno probe + `_startup_warnings_shown`; moved the transcribe probe inside `if not is_local`; clarified direct-batch prerequisite comment |
| `tests/test_deno_startup_warnings.py` | new regression suite (10 tests) simulating a host without Deno |
| `CHANGELOG.md` | `[Unreleased] → Fixed` entry |
| `docs/QUEUE_DESIGN_DECISIONS.md` | §12 decision record |
| `README.md`, `AGENTS.md` | document that the warning is download-path only |
| `.crules/tasks/review/038-deno-startup-warning.md` | this task record |

## Verification (see HANDOFF for full evidence)

| Command | Result |
|---------|--------|
| `pytest -q tests/test_deno_startup_warnings.py` | **10 passed** |
| Same suite copied onto an `origin/main` worktree | **2 failed, 8 passed** (reproduces the bug) |
| `pytest -q tests/test_deno_startup_warnings.py tests/test_cli_queue.py tests/test_update.py tests/test_cli_batch_queue.py tests/test_cli_transcribe_queue.py` | **48 passed** |
| `TERM=dumb … pytest -q` (full suite, fixed host) | **308 passed, 11 skipped**; see Coder Notes for the pre-existing concurrency flake |
| `uvx ruff check --config pyproject.toml src tests` | **413 findings — identical to the `origin/main` baseline** (0 new) |
| `compileall` + AST parse of changed files | OK |
| `uv build` | wheel + sdist built (see HANDOFF) |
| Manual CLI on a host with no Deno on PATH | queue/version/help quiet; URL transcribe warns; local file enqueues silently; doctor lists Deno as optional |

## Coder Notes / deviations

- No behavioral change to the worker, job runner, download admission, or ROCm
  paths. `lt update`'s own Deno gate (`resolve_deno_on_path`) is untouched.
- The legacy `--direct` batch keeps its unconditional probe because that pipeline
  always downloads URLs; the queue path returns before reaching it.
- Baseline exceptions observed on this host, reproduced identically on an
  `origin/main` worktree (not caused by this change):
  - `tests/test_queue_concurrency.py::test_concurrent_enqueue_one_active` and
    `…::test_concurrent_force_single_current_generation` are timing-flaky under a
    loaded machine (pass in isolation; occasionally fail inside the full suite on
    both branches). Not suppressed, not weakened — recorded as pre-existing.
- Version left at `0.7.0`; change recorded under `[Unreleased]`. Bump to 0.7.1 is
  a review/merge decision per GIT_POLICY (fix → patch), consistent with how task
  037 was staged.

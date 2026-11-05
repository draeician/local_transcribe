# Task 039 — CLI reporting integration (queue list + Deno probe scope)

Mode: Integration agent
Branch: `chore/cli-reporting-integration` (based on `origin/main` @ `25b89f1`)
Task created: 2026-11-04 (UTC) — created in `.crules/tasks/wip/`, moved to `review/` after verification.
Owner: OpenCode (reassigned 2026-11-05 from the cancelled Pi agent)
Status: `review` — review corrections complete, pushed for review; re-verified 2026-11-05 at `615497b` (see HANDOFF "Recovery verification")

## Objective

Integrate the two reviewed feature lines into one reviewable branch without merging
into `main`:

1. `feat/queue-list-reporting` (task 037, 4 commits ahead of main) — filtered and
   paginated `lt queue list`.
2. `fix/deno-startup-warning` (task 038, 3 commits ahead of main) — Deno probed only
   on YouTube download paths.

Synchronize the result at **version 0.8.0** with consistent metadata and documentation,
then push for review. No merge into `main`, no tag, no branch deletion, no production
queue change, no Aether upgrade.

## Acceptance criteria

- [x] Both branches integrated with their commits preserved (`df60e1e`, `0a571e8`,
      `bda86ce`, `29e402a` as authored ancestors; Deno fix content of `3b78d5c`,
      `c6bc375`, `259a0c9`).
- [x] Queue-only producers emit **no** Deno warning, including `lt transcribe URL --no-wait`.
- [x] Queue-mode producers **and waiters** emit no Deno warning (`--no-wait`, waiting
      `lt transcribe URL`, `lt batch` and `--wait`); in-process download paths still warn
      (`--direct` transcribe/batch); `lt doctor` keeps the optional diagnostic; `lt update` gate intact.
- [x] `lt queue list [STATUS|UUID]`: default limit 60, offset 0, oldest-first sort, pagination.
- [x] Version `0.8.0` consistent across `pyproject.toml`, `src/local_transcribe/__init__.py`,
      `project_spec.md`, `CHANGELOG.md`, `docs/WHATS_NEW.md`; CLI reports the same value.
- [x] Targeted + full suite pass with baseline failures compared, none weakened.
- [x] Ruff introduces no new findings versus baseline; package build succeeds.
- [x] Isolated pipx installation of 0.8.0 (production install untouched); real CLI smoke tests
      on a host without Deno; upgrade-detection check against the installed 0.7.0.
- [x] Task record, decision journal (`docs/QUEUE_DESIGN_DECISIONS.md` §12–§14), and `HANDOFF.md`
      updated with exact evidence and commit SHAs.
- [x] Branch pushed; remote SHA verified; `.local-transcribe-wip` removed only after that check.

## Coder notes (integration decisions)

- `feat/queue-list-reporting` merged with a merge commit (`git merge --no-ff`) so its four
  commits remain reachable exactly as authored and reviewed:
  `df60e1e`, `0a571e8`, `bda86ce`, `29e402a`. Merge commit `25be670` records the integration.
- The Deno fix was applied with `git cherry-pick`, preserving authorship, dates and message
  bodies for all three commits (new SHAs on this branch: `0e83077`, `2f247c4`, `1a4e931` for
  `3b78d5c`, `c6bc375`, `259a0c9`). Conflicts were documentation-only:
  - `CHANGELOG.md` — both `[Unreleased]` entries kept (Added / Changed / Fixed merged).
  - `docs/QUEUE_DESIGN_DECISIONS.md` — append-only journal: task 037 keeps §12, the Deno
    decision is renumbered **§13** so section numbering stays unique.
  - `HANDOFF.md` — both dated entries kept (task 037 implementation + UUID correction, then
    DENO-STARTUP-QUIET); a duplicated `## Standing constraint` heading was collapsed to one.
  - README/AGENTS queue-list wording resolves to task 037 semantics; Deno scope notes kept.

## Integration refinement (task 039 requirement)

Task 038 left the probe at the entry of `lt transcribe`, so a **producer-only** invocation
(`lt transcribe <URL> --no-wait`) still warned about Deno even though nothing in that session
downloads anything. The probe moved to the paths that actually depend on a YouTube download:

| Path | Probe | Where |
|------|-------|-------|
| `lt queue *`, `lt worker *`, `lt cookies *`, version, help | none | root callback has no probe |
| `lt transcribe <local file>` (queue or `--direct`) | none | local audio never needs Deno |
| `lt batch` queue-mode bulk enqueue and `--wait` | none | producer/watcher path never downloads |
| `lt transcribe <URL> --no-wait` | **none** (was: warned) | enqueue only; returns before the wait block |
| `lt transcribe <URL>` (waits for worker) | **none** (was: warned) | review correction: the worker downloads, not the client |
| `lt transcribe <URL> --direct` | warns | in-process `transcribe_url()` branch |
| `lt batch --input … --direct` | warns | legacy in-process pipeline prerequisites |
| `lt doctor` | optional diagnostic row "Deno Runtime (YouTube)" | unchanged |
| `lt update` | hard gate, exits 1 without Deno on PATH | unchanged |

Coverage in `tests/test_deno_startup_warnings.py` (14 tests):
`test_transcribe_url_no_wait_does_not_probe_deno`,
`test_transcribe_url_wait_does_not_probe_deno` (supersedes the earlier
wait-warns assertion), `test_transcribe_url_wait_surfaces_worker_failure`,
`test_batch_queue_wait_does_not_probe_deno`, and
`test_transcribe_direct_url_warns_without_deno`. The existing producer/reporting and
download-path assertions are unchanged in strength; one assertion was corrected to match the
task 037 output shape (the `SOURCE` column carries the recorded source URL, not the
`youtube:<id>` source key).

## Verification evidence

Environment note: the checked-in `.venv`/`venv` are broken (no packages) and the pipx venv has
runtime deps but no pytest. Tests ran with system pytest + the pipx site-packages on
`PYTHONPATH`; `TERM=dumb` disables Rich color for the ANSI-sensitive tests.

`PIPX_SITE=/home/draeician/.local/share/pipx/venvs/local-transcribe/lib/python3.14/site-packages`

| Check | Command | Result |
|-------|---------|--------|
| Targeted suites | `TERM=dumb PYTHONPATH=src:$PIPX_SITE python3 -m pytest -q tests/test_deno_startup_warnings.py tests/test_cli_queue.py tests/test_queue_store.py tests/test_cli_transcribe_queue.py tests/test_cli_batch_queue.py tests/test_update.py` | **86 passed** |
| Full suite ×5 (integration branch) | `TERM=dumb PYTHONPATH=src:$PIPX_SITE python3 -m pytest -q` | see HANDOFF table; only the pre-existing concurrency flake appears, identical to baseline |
| Baseline full suite ×5 (`origin/main`) | same command in a clean worktree at `25b89f1` | same intermittent `test_queue_concurrency` failures; no new failure class |
| Ruff | `uvx ruff check --config pyproject.toml src tests` vs baseline counts | identical per-rule counts, 0 new findings (details in HANDOFF) |
| Build | `uv build --out-dir /tmp/pi-integration-dist` | `local_transcribe-0.8.0` sdist + wheel |
| Isolated pipx install | `pipx install --venv /tmp/lt-pipx-080 --force /tmp/pi-integration-dist/local_transcribe-0.8.0-py3-none-any.whl` | installs 0.8.0; production `local-transcribe 0.7.0` untouched |
| CLI smoke (no Deno on PATH) | isolated venv `lt queue list`, `version`, `--help`, `transcribe URL --no-wait`, local `.m4a`, YouTube URL + wait | warning-free on producer/reporting paths; at that time the wait path still warned — **superseded by the 2026-11-05 correction** (queue wait is now quiet; only `--direct` warns) |
| Upgrade detection | installed 0.7.0 vs wheel 0.8.0 metadata + `lt version` output | 0.7.0 → 0.8.0 detected as an upgrade; both report their own version consistently with metadata |

## Blockers / notes for review

- No merge into `main`, no tag, no branch deletion, no production queue or Aether change.
- Version bump to 0.8.0 is staged on this branch only; the release decision (merge + publish)
  belongs to review.
- Pre-existing repo-wide Ruff baseline and the timing-flaky concurrency tests are documented,
  not suppressed.

## Review corrections (2026-11-05, OpenCode)

The Pi agent that owned this task was cancelled after an apparent loop. GitHub
showed `main` unchanged at `25b89f1` and this branch unchanged at `23626c0`, so
OpenCode claimed task 039, moved it back to `wip/`, and addressed only the two
outstanding review findings. No Pi work was recovered or restarted.

### Correction 1 — queue-list test isolation

`tests/test_cli_queue.py` now has a module-level autouse fixture that sets
`XDG_CONFIG_HOME` and `XDG_STATE_HOME` under the test's `tmp_path`. The
task-037 queue-list tests previously inherited the operator's real config; when
that config carried a production `expected_uuid`, temporary queues failed
identity validation with a UUID mismatch. Application identity validation is
untouched, production configuration is untouched, and no assertion was weakened.

### Correction 2 — no Deno probe for queue clients

`src/local_transcribe/cli.py` no longer probes Deno on the queue **wait** path.
Queue mode is Deno-free for `lt transcribe <URL> --no-wait`, `lt transcribe
<URL>` (including waiting), `lt batch` (enqueue and `--wait`), local files, and
all `lt queue *` commands. `lt transcribe <URL> --direct` and `lt batch --direct`
retain the prerequisite warning; `lt doctor` retains its optional diagnostic and
`lt update` its hard requirement. Worker-side download failures still surface to
the waiting client as `category: detail`; worker execution was not changed.

Tests: `test_transcribe_url_wait_does_not_probe_deno`,
`test_transcribe_url_wait_surfaces_worker_failure`, and
`test_batch_queue_wait_does_not_probe_deno` replace the old
wait-warns assertion. Docs updated: `CHANGELOG.md`, `docs/WHATS_NEW.md`,
`README.md`, `AGENTS.md`, and `docs/QUEUE_DESIGN_DECISIONS.md` §15.

### Verification evidence (this correction round)

Environment: system `pytest` 9.0.2 with the pipx runtime site-packages on
`PYTHONPATH`; `TERM=dumb`. The operator's real `~/.config/local-transcribe/config.yaml`
points at a stale `/tmp` smoke queue with `expected_uuid`
`6a058908-116f-41c9-987f-d5f4ef38667e`; that is the production-shaped external
config used for the isolation check.

| Check | Command | Result |
|-------|---------|--------|
| Correction-targeted | `pytest -q tests/test_cli_queue.py tests/test_deno_startup_warnings.py tests/test_cli_transcribe_queue.py` | **63 passed** |
| Queue-list isolation, empty external XDG | `XDG_CONFIG_HOME=/tmp/opencode/lt-empty-config pytest -q tests/test_cli_queue.py` | **35 passed** |
| Queue-list isolation, production-shaped external XDG | `XDG_CONFIG_HOME=/tmp/opencode/lt-prod-shaped-config pytest -q tests/test_cli_queue.py` | **35 passed** |
| Identity validation preserved | `pytest -q tests/test_queue_paths.py` | **11 passed** |
| Full suite, default operator config | `pytest -q` | 8 failed, **334 passed, 11 skipped**; 6 pre-existing `test_cli_batch_queue` failures + `test_enqueue_youtube_safe_success` (stale operator `expected_uuid`) + one timing-flaky force-generation case |
| Baseline full suite (`origin/main` @ `25b89f1`), same env | same command in a detached worktree | 7 failed, **291 passed, 11 skipped**; identical failure classes minus the flake |
| Full suite, isolated empty XDG | `XDG_CONFIG_HOME=/tmp/opencode/lt-empty-config pytest -q` | **342 passed, 11 skipped** |
| Concurrency case in isolation | `pytest -q tests/test_queue_concurrency.py::test_enqueue_youtube_safe_success` with empty XDG | **1 passed** (fails with the stale operator config on baseline too) |
| Ruff | `uvx ruff check --config pyproject.toml src tests` | **408 findings** vs baseline **413**; changed `cli.py` + `test_cli_queue.py` **26 both**; new Deno suite **0** |
| Syntax | `python3 -m compileall` + `ast.parse` on changed files | OK |
| Build | `uv build --out-dir /tmp/opencode/lt-dist-039` | 0.8.0 sdist + wheel |
| Isolated install | `PIPX_HOME=/tmp/opencode/pipx-039 pipx install …whl` | **0.8.0**; production pipx still **0.7.0** |
| CLI smoke, no Deno on PATH | isolated `lt` with throwaway queue/XDG | `queue list/stats`, `transcribe --no-wait`, waiting `transcribe --timeout 0`, `batch --wait` → **0** Deno warnings; `doctor` still lists the optional Deno row; `--direct` transcribe and `--direct` batch each warn once |
| Diff hygiene | `git diff --check` | clean |

No production queue record, worker service, ROCm/CUDA path, or production pipx
installation was changed.

# Task 039 — CLI reporting integration (queue list + Deno probe scope)

Mode: Integration agent
Branch: `chore/cli-reporting-integration` (based on `origin/main` @ `25b89f1`)
Task created: 2026-11-04 (UTC) — created in `.crules/tasks/wip/`, moved to `review/` after verification.

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
- [x] YouTube download paths still warn when Deno is missing (waiting `lt transcribe URL`,
      `--direct` transcribe/batch); `lt doctor` keeps the optional diagnostic; `lt update` gate intact.
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
| `lt batch` queue-mode bulk enqueue | none | producer path returns before any download code |
| `lt transcribe <URL> --no-wait` | **none** (was: warned) | enqueue only; returns before the wait block |
| `lt transcribe <URL>` (waits for worker) | warns | before `wait_for_execution` |
| `lt transcribe <URL> --direct` | warns | in-process `transcribe_url()` branch |
| `lt batch --input … --direct` | warns | legacy in-process pipeline prerequisites |
| `lt doctor` | optional diagnostic row "Deno Runtime (YouTube)" | unchanged |
| `lt update` | hard gate, exits 1 without Deno on PATH | unchanged |

New/changed coverage in `tests/test_deno_startup_warnings.py` (14 tests):
`test_transcribe_url_no_wait_does_not_probe_deno`,
`test_transcribe_url_wait_warns_without_deno`,
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
| CLI smoke (no Deno on PATH) | isolated venv `lt queue list`, `version`, `--help`, `transcribe URL --no-wait`, local `.m4a`, YouTube URL + wait | warning-free on producer/reporting paths; warns only where a download is awaited |
| Upgrade detection | installed 0.7.0 vs wheel 0.8.0 metadata + `lt version` output | 0.7.0 → 0.8.0 detected as an upgrade; both report their own version consistently with metadata |

## Blockers / notes for review

- No merge into `main`, no tag, no branch deletion, no production queue or Aether change.
- Version bump to 0.8.0 is staged on this branch only; the release decision (merge + publish)
  belongs to review.
- Pre-existing repo-wide Ruff baseline and the timing-flaky concurrency tests are documented,
  not suppressed.

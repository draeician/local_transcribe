# Task 040 — Restore the Ruff verification gate

Mode: Cleanup agent
Branch: `chore/ruff-gate-cleanup` (based on `origin/main` @ `25b89f1`)
Task created: 2026-11-06 (UTC) — created in `.crules/tasks/wip/`.
Owner: OpenCode
Status: `wip`

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

| Rule | Count | Autofix |
|------|------:|---------|
| UP045 non-pep604-annotation-optional | 125 | yes |
| UP006 non-pep585-annotation | 109 | yes |
| UP035 deprecated-import | 40 | no |
| B008 function-call-in-default-argument | 21 | no |
| F401 unused-import | 21 | yes |
| BLE001 blind-except | 15 | no |
| I001 unsorted-imports | 13 | yes |
| RUF100 unused-noqa | 10 | yes |
| F541 f-string-missing-placeholders | 9 | yes |
| UP037 quoted-annotation | 8 | yes |
| PLW1510 subprocess-run-without-check | 8 | no |
| F841 unused-variable | 6 | no |
| S110 try-except-pass | 4 | no |
| PIE790 unnecessary-placeholder | 4 | yes |
| FURB192 sorted-min-max | 3 | no |
| S112 try-except-continue | 2 | no |
| TRY004 type-check-without-type-error | 2 | no |
| TRY203 useless-try-except | 2 | no |
| FURB122 for-loop-writes | 1 | yes |
| RUF013 implicit-optional | 1 | no |
| SIM117 multiple-with-statements | 1 | no |
| SIM103 needless-bool | 1 | no |
| PYI034 non-self-return-type | 1 | no |
| F811 redefined-while-unused | 1 | no |
| UP022 replace-stdout-stderr | 1 | no |
| UP012 unnecessary-encode-utf8 | 1 | yes |
| RUF022 unsorted-dunder-all | 1 | yes |
| RUF059 unused-unpacked-variable | 1 | no |
| RUF007 zip-instead-of-pairwise | 1 | no |

Files affected: 49 across `src/local_transcribe/` and `tests/`. Largest:
`services/reconcile.py` (42), `cli_queue.py` (39), `utils/hardware.py` (29),
`utils/doctor.py` (27), `cli.py` (26), `services/pipeline.py` (20).

## Constraints

- Preserve Python 3.10+ compatibility (`target-version = "py310"`).
- Do not disable rules, add blanket `noqa`, weaken `pyproject.toml`, or suppress
  findings just to pass.
- Prefer Ruff's safe autofixes where the transformation is provably
  behavior-preserving; review unsafe fixes before applying.
- Where a finding cannot be fixed without a semantic guess, record it as a
  blocker instead of changing behavior.
- Keep unrelated refactoring out of the branch.

## Plan

1. Capture baseline (`--output-format=json` + `--statistics`).
2. Apply autofixes in focused groups (import sorting, typing modernization,
   pyflakes/cleanup) with a commit per group and re-run Ruff between groups.
3. Fix remaining findings manually, one rule family at a time.
4. Confirm zero findings, then run regression tests, full suite once, syntax,
   build, secret scan, and diff hygiene.
5. Update HANDOFF and the Decision Journal; push and move to `review/`.

## Acceptance criteria

- [ ] `uvx ruff check --config pyproject.toml src tests` → **0 findings**.
- [ ] No rule/config weakening and no blanket suppressions.
- [ ] Relevant regression tests and one full pytest run pass (pre-existing
      baseline failures documented if any).
- [ ] Syntax, package build, secret scan, and `git diff --check` clean.
- [ ] Version remains 0.8.0.
- [ ] Task/HANDOFF/Decision Journal updated; branch pushed; remote SHA verified;
      task moved to `review/`.

## Verification log

(To be filled with exact commands and results.)

## Blockers

(To be filled if a finding requires a semantic change.)

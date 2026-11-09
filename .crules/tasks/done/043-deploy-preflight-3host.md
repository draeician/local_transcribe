# Task 043 — Three-host upgrade/rollback preflight (read-only)

| Field | Value |
|-------|-------|
| **Status** | `done` — approved by ChatGPT; final pre-merge gate passed; merged into `main`; production cutover **not authorized and not part of this merge** |
| **Branch** | `chore/three-host-upgrade-preflight` |
| **Reviewed head** | `1343b3badef6c199840a7451930ef892ed6fd95f` (normal merge of `origin/main` `f147340` into the task branch) |
| **Base** | `origin/main` @ `f147340f9c6e4f36da10618c7abb947bd491fccb` |
| **Type** | `chore` (deployment preflight / evidence only) |
| **Owner** | OpenCode |
| **Phase** | Post-0.8.1 production rollout planning |
| **Depends on** | 041, 042, 044 |
| **Blocks** | Actual three-host upgrade (separate authorized task) |
| **Correction window** | 2026-11-07T17:25:35Z – 2026-11-07T17:33Z |
| **Refresh** | 2026-11-08 — 0.8.1 candidate `f147340`; see "Refresh 2026-11-08" below |

> **Supersession note (2026-11-08).** This record now targets **0.8.1**
> (`f147340`) and the reconciled branch head `1343b3b`. Sections above the
> "Refresh 2026-11-08" heading describe the earlier **0.8.0 / `abdedbe`**
> candidate, the failed required gate, and the first host inventory. They are
> **historical and superseded, not deleted**, and are retained to preserve the
> preflight audit trail. Where the old text conflicts with the refresh section,
> the refresh section governs. No shared history was rewritten and no
> force-push was used.

## Execution location (recorded, not assumed)

| Item | Value |
|------|-------|
| Actual host | `nomnom` (Linux Mint 22.3) |
| Requested host | Minion |
| User | `draeician` |
| Start directory | `/home/draeician/git/personal/local_transcribe` |
| Resolved repository | `/opt/md2/git/personal/local_transcribe` (main worktree, `abdedbe`, untouched) |
| Deliverable worktree | `/tmp/opencode/local_transcribe-preflight` (`chore/three-host-upgrade-preflight`) |
| Remote checks | `ssh minion` (`minion-26`) and `ssh -o HostKeyAlias=aether aether` (`aether`), read-only |

**Discrepancy.** The task asked to run on Minion. The harness is on `nomnom`, so
the repository work and gate ran on `nomnom`; Minion and Aether were inspected
over existing SSH. No SSH configuration was changed and no `sudo` was used.

## Why this record supersedes `b9f686d`

The prior record moved the task to `review/` and checked a "gate passed" box.
That was false. The required full suite failed then and still fails now, on the
branch **and** on untouched `origin/main`. This correction:

- replaces the false completion claim with the actual failed runs;
- records exact commands, environment, filesystem, exit codes, and test IDs;
- separates ANSI-output failures, the ext4 foreground-wait race, and the
  concurrency flake;
- states plainly that a baseline reproduction explains the failure but does not
  make the required gate pass;
- keeps the task in `wip/` with the WIP marker and no approval claim.

## Scope and non-actions

Read-only against production. No install, upgrade, staging installation,
service start/stop/restart, queue mutation, counter reset, archive edit, NFS or
driver change, `lt update`, source/test change, release, tag, merge, or branch
cleanup. No worker was created where none exists. No live configuration was
modified.

## Host and role inventory

> **Superseded 2026-11-08.** This is the original 0.8.0 inventory. See
> "Current host inventory (fresh, 2026-11-08)" in the refresh section for the
> authoritative current state.

| Host | OS / kernel | System Python | pipx | `local-transcribe` | Role | Queue mount | `ref` |
|------|-------------|---------------|------|--------------------|------|-------------|-------|
| `nomnom` | Linux Mint 22.3, 7.0.0-42 | 3.12.3 | 1.4.3 | **none** | none | **NFSv3** (`vers=3`) | pipx `ref-cli 1.6.12b1.dev129+g80c6e3791` (candidate) |
| `minion-26` | Ubuntu 26.04.1, 7.0.0-38 | 3.14.4 | 1.8.0 | **0.7.0**, venv Python 3.14.4, VCS `25b89f1326751a3b0781051331822c50ffde3b19` | producer candidate; **no worker**; `ref-api.service` active | **NFSv4.2** (`vers=4.2`) | `ref-cli`/`ref-api` `1.6.12b1.dev129+g80c6e3791` (candidate) |
| `aether` | Ubuntu 25.10, 6.17.0-41 | 3.13.7 | 1.7.1 | **0.7.0**, venv Python 3.13.7, provenance `file:///tmp/lt-branch` (deleted) | **sole NLM worker** | **NFSv3** (`vers=3`) | none |

Shared queue: `/opt/md2/music/youtube/transcripts/transcription-queue`, UUID
`8c6fc098-8cbe-41ae-9689-dd759797a582`, `created_by: nomnom`.

## Evidence table

> **Superseded 2026-11-08.** Original 0.8.0 evidence table. The current artifact
> hashes, rollback readiness, and rehearsal verdicts are in the refresh section.

| Host | Service / role | Environment | Installed version + source | Proposed target | Upgrade needed | Blockers | Rollback readiness |
|------|----------------|-------------|----------------------------|-----------------|----------------|----------|--------------------|
| `nomnom` | none | Mint 22.3; Python 3.12.3; pipx 1.4.3; NFSv3 | no `local-transcribe`; pipx `ref-cli` at candidate | fresh 0.8.0 **only if a CLI is required** | conditional, separate decision | no CLI; no worker; config directory absent (see below) | N/A; `pipx uninstall` if later installed |
| `minion-26` | `local-transcribe` CLI + `ref-api` | Ubuntu 26.04.1; Python 3.14.4; pipx 1.8.0; NFSv4.2 | `local-transcribe 0.7.0` from `25b89f1`; `ref-cli`/`ref-api` at candidate | `local-transcribe` 0.7.0 → 0.8.0; **ref unchanged** | yes (lt only) | config `expected_uuid` is the smoke UUID; `--queue-dir` cannot override it; NFSv4.2 producer compatibility unresolved; no preserved 0.7.0 wheel | **not ready**: capture installed tree + build exact 0.7.0 wheel; rehearse |
| `aether` | `local-transcribe-worker.service` (sole NLM lock holder) | Ubuntu 25.10; Python 3.13.7; pipx 1.7.1; gfx1151; ROCm `/opt/rocm-7.2.0`; NFSv3 | `local-transcribe 0.7.0` from deleted `file:///tmp/lt-branch`; injected `ctranslate2 4.8.3`, `rocm-sdk-core 7.13.0a20260515`, `rocm-sdk-libraries-gfx1151 7.13.0a20260515` | 0.7.0 → 0.8.0 preserving injected packages and cache | yes | provenance gone; no rollback artifact; live queue; 13 historical ABRT/OOM events | **not ready**: capture installed bytes, dist-info, shims, pipx metadata, ROCm cache; rehearse |

## Sanitized read-only evidence

All timestamps UTC. Commands ran as `draeician` in `/home/draeician` unless
noted. Values are sanitized; no raw environment files or secrets are included.

### Aether — worker progress (full IDs, completion, transcripts)

```
host=aether user=draeician date=2026-11-07T17:26:41Z
e8d987f0-7bc9-4bcf-8628-1055e7d3e950
  source_key=youtube:JUQvlLPKyv8 status=completed attempts=1 error=null
  started_at=2026-11-07T16:53:44+00:00 completed_at=2026-11-07T16:56:12+00:00
  output_path=/home/draeician/references/transcripts/JUQvlLPKyv8.json
  transcript size=21144 mtime=2026-11-07 10:51:22 +0000
  sha256=1fb54625cc5ea2f851d273aaa28ba57e7dd3b841cf0262293ad7fe03e1087c69
ba0ed373-bfce-4fe4-acc4-6b067f479262
  source_key=youtube:wYSehRUnpJg status=completed attempts=1 error=null
  started_at=2026-11-07T16:56:12+00:00 completed_at=2026-11-07T16:58:50+00:00
  output_path=/home/draeician/references/transcripts/wYSehRUnpJg.json
  transcript size=26865 mtime=2026-11-07 10:54:00 +0000
  sha256=04be320e098be75e7e0d3c28453dcb75ae44f719ade5e41b55a9d9afa5df0420
```

Lock ownership is **not** inferred from `worker.lock` contents or heartbeat.
Read-only `lslocks` at `2026-11-07T17:26:41Z` shows PID `910154` (`lt`) holding
both:

```
910154 lt /run/user/1000/local-transcribe/worker.lock
910154 lt /opt/md2/music/youtube/transcripts/transcription-queue/worker/worker.lock
```

`state.json` and `worker.lock` contents are recorded only as advisory metadata.
At the snapshot the worker had already moved on to
`cd05610c-f78f-4560-96c5-111d02d88200`; the queue is live and changes between
reads.

### Aether — installed-bytes baseline (`2026-11-07T17:26:50Z`)

```
interpreter=Python 3.13.7 (main, Mar 3 2026) [GCC 15.2.0]
shim=/home/draeician/.local/bin/lt -> .../pipx/venvs/local-transcribe/bin/lt
dist-info=local_transcribe-0.7.0.dist-info
package files=82 bytes=876760
injected=ctranslate2 4.8.3, rocm-sdk-core 7.13.0a20260515,
         rocm-sdk-libraries-gfx1151 7.13.0a20260515
rocm cache:
  289438953  rocm/pycp313/rocm-python-wheels-4.8.3-Linux.zip
            sha256=132eda022faed55e4d1a7093f15a9dd9d4bcdf26bd72471ec1c984bada9a3d3f
   43708399  rocm/pycp313/extracted/ctranslate2-4.8.3-cp313-...whl
            sha256=f4790b7c10e668619f9ba138b6f26cdba5736c8d810628d37d76b567de3b7a2d
```

Source-file hashes were captured for all 39 `*.py` files excluding
`__pycache__` (for example `cli.py`
`ba6cb8479aba2dc166a870910a1d3ead5d667c9fbe7a12cd28f8190fbfcd8b09`,
`worker.py`
`73e03f9182af9c0f20a04f54cc05eeb9ba437a4537a9c323845344233f06d690`).
These are the rollback baseline. A rebuilt 0.7.0 wheel is **not** assumed
equivalent because its version string matches; equivalence must be proven by
comparing these source hashes and the dist-info `RECORD` separately from
generated caches and metadata.

### Aether — historical vs current failures (bounded)

```
NRestarts=13
ActiveEnterTimestamp=Fri 2026-11-06 09:16:36 UTC
core-dump/oom-kill events 2026-10-28 .. 2026-11-07 00:00 = 13
core-dump/oom-kill events since 2026-11-06 09:16:37 = 0
```

The 13 events (ABRT core dumps and OOM kills, latest
`2026-11-06T09:16:26Z`) are **historical**. The current run has no recorded
crash. This is bounded evidence only; no troubleshooting or configuration task
is opened here.

### Minion — `--queue-dir` does not override `expected_uuid`

Read-only, `2026-11-07T17:26:32Z`, exit code `1`:

```
$ ~/.local/bin/lt queue path \
    --queue-dir /opt/md2/music/youtube/transcripts/transcription-queue
Configured path: /tmp/lt-smoke-queue-59553
✗ Queue UUID mismatch for
  /opt/md2/music/youtube/transcripts/transcription-queue:
  found '8c6fc098-8cbe-41ae-9689-dd759797a582',
  expected '6a058908-116f-41c9-987f-d5f4ef38667e'
```

`queue doctor --queue-dir …` fails the same identity check before reaching the
mount check (exit `1`). Source confirms why: `resolve_queue_dir()` verifies
`queue_cfg.expected_uuid` from
`~/.config/local-transcribe/config.yaml` even when a path argument is supplied
(`queue_paths.py`), and `queue_api.enqueue_youtube()` uses the same resolver.

**Consequence for ref-api.** Minion's `ref-api.env` sets
`REF_LOCAL_TRANSCRIBE_LT=/home/draeician/.local/bin/lt` and the production
queue UUID, but `ref-api`'s service venv does not contain
`local_transcribe`, so it falls back to `lt queue add --queue-dir …`. That
fallback currently fails lt's own identity check and falls back to the legacy
pending file. `ref-api` is healthy (`/health` → `{"status":"ok","version":"1.6.12b1.dev129+g80c6e3791"}`,
PID `3521265`, `NRestarts=0`), but its queue producer path is not reaching the
shared queue. This must be corrected by an explicit config edit, not by
`--queue-dir`.

### Nomnom — configuration "disappearance" (corrected)

Read-only at `2026-11-07T17:26:58Z`:

```
~/.config/local-transcribe: No such file or directory
~/.config mtime=2026-11-06 16:50:59 -0600
no local-transcribe entry under ~/.config, ~/.local/share/Trash,
  ~/.local/state, or ~/.cache
```

The earlier claim that the directory "was present, then absent" is **not
substantiated**. The only listing that appeared to show it also contained
`ref-api.env.template`, a ref-only file, so that output is attributable to
`~/.config/ref/`. The correct statement is: no `local-transcribe` configuration
was found on `nomnom`, and there is no read-only evidence it ever existed there.
The directory was not recreated.

## Required gate — actual result (fails)

> **Superseded 2026-11-08.** This documents the **0.8.0 / `abdedbe`** gate
> failure. The refreshed 0.8.1 tree (`1343b3b`) passes the canonical gate; see
> "Fresh verification gates on the refreshed tree" in the refresh section.

Environment: isolated venv `/tmp/opencode/preflight-gate/venv`,
Python `3.12.3`, `pytest 9.0.3`, `ruff 0.17.4`, `typer 0.25.1`,
`rich 15.0.0`, `click 8.4.1`. Isolated `XDG_*` and `HOME` under
`/tmp/opencode/preflight-gate`. No production queue or archive was touched.

Command: `python -m pytest -q` from each worktree. One run per configuration;
no success-hunting reruns.

| Worktree | `NO_COLOR` | `TMPDIR` filesystem | Result | Exit |
|----------|-----------|---------------------|--------|------|
| branch `b9f686d` | unset | `/tmp` (ext4) | **7 failed, 335 passed, 11 skipped** | 1 |
| baseline `abdedbe` | unset | `/tmp` (ext4) | **7 failed, 335 passed, 11 skipped** | 1 |
| branch | `1` | `/tmp` (ext4) | **1 failed, 341 passed, 11 skipped** | 1 |
| baseline | `1` | `/tmp` (ext4) | **1 failed, 341 passed, 11 skipped** | 1 |
| branch | `1` | `/dev/shm` (tmpfs, diagnostic only) | **1 failed, 341 passed, 11 skipped** | 1 |
| baseline | `1` | `/dev/shm` (tmpfs, diagnostic only) | 342 passed, 11 skipped | 0 |

**The required gate does not pass.** The tmpfs run is diagnostic only and is
not used to claim completion. Switching filesystems, weakening assertions,
deselecting tests, or rerunning for a lucky pass is explicitly rejected.

### Failure classes

**1. ANSI-output failures (6, default environment).** `Console(force_terminal=True)`
injects ANSI escapes into captured output, so substring assertions fail. Same
IDs on branch and baseline:

- `tests/test_cli_queue.py::test_queue_retry_all_failed`
- `tests/test_deno_startup_warnings.py::test_transcribe_url_no_wait_does_not_probe_deno`
- `tests/test_deno_startup_warnings.py::test_transcribe_url_wait_surfaces_worker_failure`
- `tests/test_update.py::test_cli_update_default_includes_pre`
- `tests/test_update.py::test_cli_update_dry_run_does_not_modify_environment`
- `tests/test_update.py::test_cli_update_pip_failure_shows_command_and_stderr`

Example (`tests/test_update.py:161`): expected literal
`yt-dlp[default,curl-cffi]`, actual output contains
`yt-dlp\x1b[1m[\x1b[0mdefault,curl-cffi\x1b[1m]\x1b[0m`.

**2. Foreground-wait race on ext4 (1).**
`tests/test_cli_transcribe_queue.py::test_wait_completed_via_foreground`
returns `WaitOutcome(status='completed', output_path=None)` because
`atomic_state_transition()` renames `processing → completed` and then writes
metadata; a concurrent `locate_execution()` can read the destination between
those steps. It passes on tmpfs and fails on ext4, identically on baseline.

**3. Concurrency flake (nondeterministic).**
`tests/test_queue_concurrency.py::test_concurrent_force_single_current_generation`
failed in the branch tmpfs run and passed in the baseline tmpfs run. It is a
known pre-existing flake, not caused by this docs change.

### Other gate checks

| Check | Command | Result | Exit |
|-------|---------|--------|------|
| Ruff (canonical) | `ruff check --config pyproject.toml src tests` | **All checks passed** on branch and baseline | 0 |
| Ruff (whole tree, informational) | `ruff check .` | 52 pre-existing findings in `install_local_transcribe.py` and `export-firefox-cookies.py`, outside the canonical gate | 1 |
| Version | `python -m local_transcribe.cli version` | `local-transcribe version 0.8.0` | 0 |
| Diff hygiene | `git diff --check` | clean | 0 |
| Secret scan | regex scan of changed docs | no matches; no raw `REF_*=` values | 0 |
| Change scope | `git diff --stat origin/main -- . ':!*.md'` | empty (docs-only) | 0 |

## Required follow-up code corrections (separately authorized)

These are **not** fixed here. A green required gate needs a separate code task:

1. Make `atomic_state_transition`/`locate_execution` safe against the
   rename-then-write window, or make the foreground-wait test deterministic
   without weakening its assertion.
2. Make CLI tests independent of `Console(force_terminal=True)` ANSI output.
3. Decide whether the pre-existing concurrency flake needs a code fix or a
   documented quarantine with owner approval.

## Artifact and rollback design (proposed, not executed)

> **Superseded 2026-11-08.** Proposed 0.8.0 design. The executed 0.8.1 artifact,
> staging, and captured-bytes rollback design are in the refresh section.

### Pinned candidates

> **Superseded 2026-11-08.** The pinned candidate is now `0.8.1` /
> `f147340f9c6e4f36da10618c7abb947bd491fccb`.

| Repo | Commit | Use |
|------|--------|-----|
| `local_transcribe` | `abdedbe88b8b44d24582520264f463bebcc83109` | 0.8.0 upgrade candidate |
| `ref` | `80c6e379106a73c047cd517e56207bc62d558d7f` | already deployed; no upgrade |

### Build the pinned 0.8.0 wheel (proposed)

```bash
# HOST: build host (nomnom)  USER: draeician  PRIV: unprivileged
# DIR: /tmp/opencode/lt-build-abdedbe (build only; not a staging install)
git clone https://github.com/draeician/local_transcribe.git /tmp/opencode/lt-build-abdedbe
git -C /tmp/opencode/lt-build-abdedbe checkout abdedbe88b8b44d24582520264f463bebcc83109
git -C /tmp/opencode/lt-build-abdedbe rev-parse HEAD   # must equal the pin
(cd /tmp/opencode/lt-build-abdedbe && uv build --out-dir dist)
sha256sum /tmp/opencode/lt-build-abdedbe/dist/local_transcribe-0.8.0-py3-none-any.whl
```

Record the wheel sha256 and the source commit in a signed-off manifest. A wheel
whose sha256 is not recorded is not a candidate.

### Durable staging and transfer (proposed)

```bash
# HOST: build host  USER: draeician  PRIV: unprivileged
# DIR: ~/.local/share/local-transcribe/deploy-artifacts/<UTC>-v0.8.0
ART="$HOME/.local/share/local-transcribe/deploy-artifacts/20261107T<UTC>-v0.8.0"
install -d -m 700 "$ART"
cp /tmp/opencode/lt-build-abdedbe/dist/local_transcribe-0.8.0-py3-none-any.whl "$ART/"
sha256sum "$ART"/*.whl > "$ART/SHA256SUMS"
```

Transfer to each target host's own durable path, then verify:

```bash
# HOST: source  USER: draeician  PRIV: unprivileged
scp "$ART/local_transcribe-0.8.0-py3-none-any.whl" "$ART/SHA256SUMS" \
    minion-26:~/.local/share/local-transcribe/deploy-artifacts/20261107T<UTC>-v0.8.0/
# HOST: target  USER: draeician  PRIV: unprivileged
cd ~/.local/share/local-transcribe/deploy-artifacts/20261107T<UTC>-v0.8.0
sha256sum -c SHA256SUMS
```

Never stage under `/tmp`, `/var/tmp`, or any path that is not durable and
host-local.

### Required artifacts per host (proposed, not created)

| Artifact | Host | Contents |
|----------|------|----------|
| `local_transcribe-0.8.0-py3-none-any.whl` | minion, aether | pinned candidate + `SHA256SUMS` |
| `local_transcribe-0.7.0-installed-tree.tar.gz` | aether, minion | `local_transcribe/` and `local_transcribe-0.7.0.dist-info/`, excluding `__pycache__` |
| `local_transcribe-0.7.0-src.sha256` | aether, minion | hash manifest of installed `*.py` |
| `local_transcribe-0.7.0-wheel/` | aether, minion | wheel rebuilt from `25b89f1`; **fallback only** |
| `pipx_metadata-before.json` | aether, minion | main version, interpreter, injected keys |
| `shim-before.txt` | aether, minion | `~/.local/bin/lt` target and interpreter |
| `rocm-cache.sha256` | aether | hashes of `rocm-python-wheels-4.8.3-Linux.zip` and `ctranslate2-4.8.3-...whl` |
| `venv-freeze-before.txt` | aether, minion | full `pip list --format=freeze` |

Aether's authoritative rollback baseline is the captured installed bytes, not
the rebuilt wheel. Source files are compared separately from `__pycache__`,
`RECORD`, `direct_url.json`, and other generated metadata.

### Clean restoration design (replaces extraction over upgraded site-packages)

```bash
# HOST: aether or minion  USER: draeician  PRIV: unprivileged
# DIR: ~
# 1. Stop the worker first (Aether) and confirm no processing execution.
# 2. Remove the upgraded environment cleanly.
pipx uninstall local-transcribe
# 3. Create a fresh venv from the pinned interpreter with the preserved 0.7.0 wheel.
pipx install "$ART/local_transcribe-0.7.0-wheel/local_transcribe-0.7.0-py3-none-any.whl" \
  --python /usr/bin/python3.13
# 4. Reconcile against the captured installed-bytes baseline.
#    Compare source hashes; if the rebuilt wheel is not equivalent, overlay the
#    captured local_transcribe/ + dist-info into the FRESH venv (never over 0.8.0)
#    and record the deviation.
# 5. Re-inject the pinned ROCm packages (Aether only) from the preserved cache.
pipx inject local-transcribe \
  "$HOME/.cache/local-transcribe/rocm/pycp313/extracted/ctranslate2-4.8.3-cp313-...whl" \
  --pip-args="--no-deps --force-reinstall"
# 6. Verify shim, interpreter, pipx metadata, source hashes, imports, version.
```

Restoration rehearsal is **required future staging work and was not performed**.

## Upgrade runbook (proposed, not executed)

> **Superseded 2026-11-08.** Proposed 0.8.0 runbook. Note in particular that the
> old guidance to leave `ref-api` running during the Minion shim gap conflicts
> with the current rule: **quiesce Minion producers before replacing `lt`**.
> Use "Final production cutover order" in the refresh section.

### One version-aware method

Use a clean replace with the exact wheel — no ambiguous venv reuse and no
"try it, then repair":

```bash
# HOST: target  USER: draeician  PRIV: unprivileged
# Aether: stop worker first. Minion: ref-api stays running; its enqueue path
# falls back to transcript-pending.md during the short shim gap.
pipx uninstall local-transcribe
pipx install "$ART/local_transcribe-0.8.0-py3-none-any.whl" \
  --python <pinned interpreter for that host>
# Aether only: re-inject the exact pinned packages from the preserved cache.
pipx inject local-transcribe <ctranslate2 wheel> --pip-args="--no-deps --force-reinstall"
pipx inject local-transcribe <rocm-sdk-core wheel> <rocm-sdk-libraries-gfx1151 wheel>
```

Pinned interpreters: Minion `3.14.4` (`/usr/bin/python3.14`); Aether `3.13.7`
(`/usr/bin/python3.13`).

### Preservation checks — all before any production restart

Printing versions alone is not a gate. Require all of:

1. `pipx list --short` and `pipx_metadata.json` show main `0.8.0`.
2. Interpreter matches the pinned version; `~/.local/bin/lt` points at the new
   venv and its shebang uses the pinned interpreter.
3. `local_transcribe` source hashes match the recorded 0.8.0 manifest; dist-info
   `METADATA` says `Version: 0.8.0`.
4. Aether: injected `ctranslate2 4.8.3`, `rocm-sdk-core 7.13.0a20260515`,
   `rocm-sdk-libraries-gfx1151 7.13.0a20260515` present; ROCm cache hashes match.
5. `python -c "import local_transcribe, ctranslate2, faster_whisper"` succeeds
   in the venv.
6. `lt version` → `0.8.0`; `lt queue path` resolves; `lt queue doctor` passes
   NFSv3/identity on Aether.
7. Only after the above: start the worker; confirm `lslocks` shows the new PID
   holding both locks; confirm one controlled execution reaches `completed`
   with a transcript file and hash. `worker.lock` contents and heartbeat alone
   are not proof.

### Idle-window and stop race

Observing an idle worker and then stopping it is racy: a new job can be claimed
between the check and `systemctl --user stop`. Correct order:

1. `systemctl --user stop local-transcribe-worker.service` **first**.
2. Confirm the service is inactive and `lslocks` no longer shows the queue lock.
3. Inspect `processing/`. If a job was claimed during the stop window, restart
   the worker, let that job finish, then stop again.
4. Abort the upgrade if any `processing/*.json` remains, if the lock is still
   held, or if recovery cannot reach a clean idle state.

### Explicit abort conditions

Abort and execute the clean rollback — do not repair in place — if any of:
source-hash mismatch, wrong interpreter, missing injected package, ROCm cache
mismatch, `queue path`/`queue doctor` failure, lock not acquired, a new
core-dump/OOM in the bounded post-start window, or a job fails to complete.
Historical ABRT/OOM events are not an abort trigger by themselves; new events
after the upgrade are.

### Future staging requirements (not performed)

- Isolated upgrade **and** rollback rehearsal on a non-production venv/host.
- Dependency and build-provenance verification for the exact wheel.
- Actual ROCm transcription on the staging host.
- Only then request a production change window. Production GPU testing is out
  of scope for this preflight.

## Host-specific corrections

### Minion

- `ref-api` and `ref-cli` already match the candidate; leave them unchanged.
- Account for `ref-api`'s dependency on the external `lt` executable: its
  fallback `lt queue add --queue-dir …` is subject to lt's own
  `expected_uuid`. Until the lt config is corrected, that fallback cannot reach
  the production queue.
- `--queue-dir` does **not** override `expected_uuid`. Correct validation order
  (without modifying live config): (1) prove the identity mismatch read-only;
  (2) correct `queue.path` and `queue.expected_uuid` by an explicit authorized
  edit; (3) re-run `lt queue path`; (4) then `lt queue doctor` to surface the
  NFS version.
- NFSv4.2 producer compatibility is **unresolved**. `queue_api.enqueue_youtube`
  does not call `validate_queue_mount`, so it may not reject NFSv4.2, but the
  spec requires NFSv3 and there is no evidence that NFSv4.2 producers are
  safe. Do not authorize a worker or a remount.
- Do not create a worker on Minion.

### Nomnom

- A fresh CLI installation is a **separate decision**, not part of this
  preflight. Do not create a worker.
- The reported configuration disappearance is not substantiated; no directory
  was recreated.

## Unperformed and unknown

| Item | State |
|------|-------|
| 0.8.0 wheel build and checksum | not performed |
| Rollback artifacts | not created |
| Upgrade/rollback rehearsal | not performed |
| Actual ROCm transcription after upgrade | not performed (out of scope) |
| Aether 0.7.0 wheel equivalence | **unproven** |
| Minion NFSv4.2 producer safety | **unresolved** |
| Minion ref-api `lt` enqueue to production | **currently failing identity** |
| Source/test fixes for gate failures | not performed (separate task) |

## Blockers

> **Superseded 2026-11-08.** These are the original 0.8.0 blockers. Current
> blockers are in "Remaining blockers" in the refresh section.

1. **Required gate fails** — 7 failures in the default environment and 1 on
   ext4 with `NO_COLOR=1`; identical on untouched `origin/main`. No approval.
2. **Aether rollback artifact absent** — provenance tree deleted; installed
   bytes must be captured and equivalence proven before any upgrade.
3. **Minion config identity mismatch** — smoke UUID blocks the ref-api `lt`
   fallback from reaching the production queue.
4. **Minion NFSv4.2 producer compatibility unresolved** — no worker, no remount.
5. **Rehearsal not performed** — isolated upgrade and rollback rehearsal are
   prerequisites for production approval.
6. **Code/test corrections outstanding** — the ANSI and rename-then-write
   failures need a separately authorized code task.

## Authorization needed for the next phase

> **Superseded 2026-11-08.** The next-phase authorization list is now in
> "Authorization required for production cutover" in the refresh section.

1. Authorization to **build and durably stage** the pinned 0.8.0 wheel and the
   rollback artifacts (no installation, no service action).
2. Authorization to run an **isolated upgrade and rollback rehearsal** on a
   non-production host/venv, including one real ROCm transcription.
3. Authorization for a separate **code task** to fix the ANSI-output test
   fragility and the `atomic_state_transition` race so the required gate can
   pass honestly.
4. A decision on **Minion's local-transcribe config** (`queue.path` +
   `queue.expected_uuid`) and on whether NFSv4.2 producer use is acceptable.
5. A separate decision on whether **Nomnom** needs a CLI at all.
6. Only after 1–5: a production change window for Minion, then Aether.

## Evidence path

- This record: `.crules/tasks/wip/043-deploy-preflight-3host.md`
- Raw gate transcripts (ephemeral, not committed):
  `/tmp/opencode/preflight-3host/review2/`

---

## Refresh 2026-11-08 — 0.8.1 candidate, staging, rollback baselines, isolated rehearsals

**Current candidate.** `local_transcribe` **0.8.1** at
`f147340f9c6e4f36da10618c7abb947bd491fccb`. The task branch is reconciled by a
normal merge onto that commit:
`1343b3badef6c199840a7451930ef892ed6fd95f`. The old 0.8.0 candidate
`abdedbe88b8b44d24582520264f463bebcc83109` and the old failed-gate conclusions
are **superseded, not deleted**; they remain above as history. No shared history
was rewritten and no force-push was used.

**Scope actually executed.** Read-only production inspection; isolated
disposable pipx/venv rehearsals; durable artifact staging; rollback baseline
capture; mixed-version writer analysis; Minion NFSv4.2 producer analysis. No
production service was stopped or started, no production pipx environment was
replaced, no live Minion config was edited, no NFS mount was changed, no
production queue record or counter was mutated, no Nomnom worker was installed,
and no tag/release/publish/merge/deployment was performed.

### Current host inventory (fresh, 2026-11-08)

| Host | Timestamp (UTC) | OS / kernel | Python | pipx | `lt` | Interpreter | Role / service | Queue mount | Queue UUID |
|------|-----------------|-------------|--------|------|------|-------------|----------------|-------------|------------|
| `nomnom` | 2026-11-08T16:17Z | Linux Mint 22.3, 7.0.0-42 | 3.12.3 | 1.4.3 | **none** | — | no worker; no producer | — | — |
| `minion-26` | 2026-11-08T16:17Z | Ubuntu 26.04.1, 7.0.0-43 | 3.14.4 | 1.8.0 | **0.7.0** | venv Python 3.14.4 | `ref-api.service` active (PID 2034); **no worker** | NFSv4.2, `vers=4.2`, `local_lock=none`, `hard`, TCP | `8c6fc098-8cbe-41ae-9689-dd759797a582` |
| `aether` | 2026-11-08T16:19Z | Ubuntu 25.10, 6.17.0-41 | 3.13.7 | 1.7.1 | **0.7.0** | venv Python 3.13.7 | `local-transcribe-worker.service` active (PID 910154, idle, sleeping) | NFSv3, `vers=3`, `local_lock=none`, `hard`, TCP | `8c6fc098-8cbe-41ae-9689-dd759797a582` |

- **Nomnom.** `lt` is absent. `ref-cli` is installed, but
  `local_transcribe` is not importable in its venv and
  `~/.config/ref/config.yaml` has `local_transcribe_lt`,
  `local_transcribe_queue_dir`, and `local_transcribe_queue_uuid` all `null`;
  Nomnom is therefore **not a queue producer**. No crontab or producer timer.
- **Minion.** `ref-cli`/`ref-api` are
  `1.6.12b1.dev129+g80c6e3791` (the desired revision). `ref-api.service`
  `ExecStart` is
  `/home/draeician/.local/share/ref-services/ref-api/bin/ref-api --host 0.0.0.0 --port 8000`;
  its venv has no `local_transcribe`, so the enqueue path is the external `lt`
  CLI. `~/.config/ref/ref-api.env` sets
  `REF_LOCAL_TRANSCRIBE_LT=/home/draeician/.local/bin/lt`,
  `REF_LOCAL_TRANSCRIBE_QUEUE_DIR=/opt/md2/music/youtube/transcripts/transcription-queue`,
  `REF_LOCAL_TRANSCRIBE_QUEUE_UUID=8c6fc098-8cbe-41ae-9689-dd759797a582`,
  `REF_YOUTUBE_TRANSCRIPT_MODE=local_only`, and
  `REF_TRANSCRIPT_RECONCILE_ENABLED=true`. The live process environment matches
  those values. No crontab or producer timer. `refwatch.service` has no
  `local_transcribe`/`ref_cli` import and no queue code; it is not a producer.
- **Aether.** Worker is the sole NLM lock holder. `state.json` shows
  `worker_id=aether-910154`, `pid=910154`, `queue_uuid` matching production;
  the process is sleeping in the idle poll. Injected packages:
  `ctranslate2 4.8.3`, `rocm-sdk-core 7.13.0a20260515`,
  `rocm-sdk-libraries-gfx1151 7.13.0a20260515`, `faster-whisper 1.2.1`.
  GPU: AMD RYZEN AI MAX+ 395 / Radeon 8060S, `gfx1151`. The ROCm installed
  trees are `_rocm_sdk_core` (~1.5G) and `_rocm_sdk_libraries_gfx1151` (~1.8G).
  `~/.config/local-transcribe/config.yaml` has
  `watch_legacy_pending: false`, so the worker does **not** run the legacy
  pending import and does **not** write `keys/`.

### Exact 0.8.1 artifact

| Item | Value |
|------|-------|
| Source commit | `f147340f9c6e4f36da10618c7abb947bd491fccb` |
| Version | `0.8.1` |
| Build host / user | `nomnom` / `draeician` (unprivileged) |
| Build Python / tool | Python 3.12.3 / `build 1.6.1` |
| Wheel | `local_transcribe-0.8.1-py3-none-any.whl` |
| **Wheel SHA256** | **`3b05384388bad18a1d2bdb3eadd2838c83256bda047eaf3fee04b76f4a16ff3d`** |
| Sdist | `local_transcribe-0.8.1.tar.gz` |
| Sdist SHA256 | `b79124cf0b236e970c362bca6e4c6b5118f2c36438a65983e8da563fd9844004` |
| Entry point | `lt = local_transcribe.cli:main` |
| Staged paths | `~/.local/share/local-transcribe/deploy-artifacts/20261108T1624Z-v0.8.1/` on Nomnom, Minion, Aether |

`sha256sum -c SHA256SUMS` passed on all three hosts for the wheel, sdist, and
the representative 0.7.0 rehearsal artifacts.

**Refreshed-tree build verification.** A build from the reconciled tree
`1343b3b` produced a wheel whose **contents are byte-identical** to the staged
wheel: 48/48 member files, zero content differences. The wheel file hash from
that build is `59faee445f2b964f3f490ac819adf44b57b93ce62eba532b023fcf700d303b40`
and the sdist is `b4cd517663fe062d3270f0b33194a523c3a31bf32c630894d845b2cf44d12489`;
the wheel hash differs only because wheel archives embed build timestamps. The
**deployment artifact remains the staged `3b0538...` wheel built from the exact
release commit `f147340`**, not the later verification rebuild.

### Durable rollback inventory

All paths are under
`~/.local/share/local-transcribe/deploy-artifacts/20261108T1624Z-v0.8.1/` on
Nomnom and are mirrored host-local on Minion/Aether as noted.

| Artifact | Path (Nomnom set) | SHA256 / size |
|----------|-------------------|---------------|
| Aether captured 0.7.0 installed tree | `rollback/aether/local_transcribe-0.7.0-installed-tree.tar.gz` | `62d4a74e30b137d23cb556b19deba883550bf9dbbcafad4353d35383ddee6152` |
| Aether ROCm SDK installed trees | `rollback/aether/rocm-sdk-installed-trees-7.13.0a20260515.tar.zst` | `12a1c8be41621ef111d1458f88026b29597e629ca425479ea20b1bba77fb6266`; 890,062,815 bytes; 3,659 archive entries (3,359 regular files) |
| Aether CTranslate2 ROCm wheel | Aether `~/.cache/local-transcribe/rocm/pycp313/extracted/` | `f4790b7c10e668619f9ba138b6f26cdba5736c8d810628d37d76b567de3b7a2d` |
| Aether ROCm wheel archive | Aether `~/.cache/local-transcribe/rocm/pycp313/rocm-python-wheels-4.8.3-Linux.zip` | `132eda022faed55e4d1a7093f15a9dd9d4bcdf26bd72471ec1c984bada9a3d3f` |
| Minion captured 0.7.0 installed tree | `rollback/minion-26/local_transcribe-0.7.0-installed-tree.tar.gz` | `28e1e495f4cdb713f4bb67f5e3092c71309243307ed21704c04831329ecbd14e` |
| Representative 0.7.0 wheel (rehearsal only) | `rehearsal/local_transcribe-0.7.0-py3-none-any.whl` | `bc6eae40d9b45f708d87c7f98d8f2410df7c5ad6330e85d5869dfb7281bc2e31` |

Each host baseline also contains `interpreter.txt`, `pipx_metadata.json`,
`pipx-list.json`, `shim-target.txt`, `shim-shebang.txt`, `venv-freeze.txt`,
`pip-list-freeze.txt`, `pip-show-*`, `local_transcribe-src.sha256`,
`dist-info.sha256`, and `baseline-sha256.txt`. Aether additionally has
`rocm-cache.sha256`, `rocm-cache-list.txt`, and the ROCm installed-tree file
list. The full set is recorded in `artifact-inventory.txt` (130 files) and
`ARTIFACTS-SHA256.txt`.

The rebuilt 0.7.0 wheel is a **representative rehearsal baseline only**; it is
not claimed to equal the installed production bytes. The true rollback source is
the captured installed tree plus the preserved CTranslate2/ROCm artifacts. The
captured trees were restored and hash-verified in the rehearsals below.

### Minion isolated upgrade/rollback rehearsal — passed

Disposable `PIPX_HOME`/`PIPX_BIN_DIR`/`HOME`/`XDG_*` under the durable artifact
area; disposable queue on ext4; production environment untouched.

- **Representative wheel run** (`2026-11-08T17:23:54Z`–`17:26:56Z`):
  0.7.0 install → `lt version` 0.7.0 → `queue init`/`queue add`/`queue stats`
  (pending 1) → 0.8.1 upgrade → `lt version` 0.8.1 → `queue add`/`queue list`
  (2 pending, oldest first) → `queue stats` → 0.7.0 rollback → `lt version`
  0.7.0 → `queue stats` (2 pending) → 0.8.1 re-upgrade → `lt version` 0.8.1 →
  `queue stats` (2 pending). Disposable environment removed.
- **Captured-bytes run** (`2026-11-08T18:09:56Z`–`18:10:39Z`): restored
  `local_transcribe-0.7.0-installed-tree.tar.gz`
  (`28e1e495...`) into a fresh Python 3.14 venv; `local_transcribe-src.sha256`
  and `dist-info.sha256` checks all `OK`; import version 0.7.0; enqueue on a
  disposable queue returned `enqueued` with execution
  `8fabc4bd-56c0-4484-bb97-89bbfd580d85`; upgrade to 0.8.1 over the captured
  bytes; version 0.8.1 and queue stats showed the pending record. Disposable
  environment removed.

**Verdict:** Minion upgrade/rollback is genuinely rehearsed from both the
representative wheel and the captured installed bytes. The CLI/queue commands
were exercised only against disposable queues, never the production queue.

### Aether isolated ROCm upgrade/rollback rehearsal — passed

Disposable `PIPX_HOME`/`PIPX_BIN_DIR`/`HOME`/`XDG_*`; Python 3.13.7; local flat
`faster-whisper-tiny` model copy; local `ffmpeg`/`flite` speech sample; no
production queue, cache, pipx venv, driver, `/opt/rocm`, or worker touched.

- **Representative wheel run** (`2026-11-08T17:14:49Z`–`17:15:44Z`):
  0.7.0 install → inject `ctranslate2 4.8.3` with
  `pipx inject --force ... --pip-args="--no-deps --force-reinstall"` → restore
  the preserved ROCm trees → verify GPU and transcribe → 0.8.1 upgrade →
  re-inject/restore → verify → 0.7.0 rollback → re-inject/restore → verify →
  0.8.1 re-upgrade → re-inject/restore → verify. All four stages exited 0.
  Every stage reported `ctranslate2 4.8.3`, `faster-whisper 1.2.1`,
  `gpu_runtime_ok=true`, `gpu_backend=amd-rocm`, `cuda_device_count=1`,
  `cuda_compute_types` including `float16`, and a non-empty transcript.
- **Captured-bytes run** (`2026-11-08T18:07:15Z`–`18:07:32Z`): restored
  `local_transcribe-0.7.0-installed-tree.tar.gz` (`62d4a74e...`) into a fresh
  Python 3.13 venv; `local_transcribe-src.sha256` and `dist-info.sha256` checks
  all `OK`; restored the ROCm trees (`libhsa` hash
  `97fbbeda0548d6b15b15956056a1fc4b3593acc5d3cbe15874225b0520aff2a5`, matching
  production) and the CTranslate2 ROCm wheel; GPU transcription passed with
  `amd-rocm`; upgrade to 0.8.1 over the captured bytes; GPU transcription
  passed again. Disposable environment removed.
- **Production untouched check:** after the rehearsals, Aether still reported
  `lt 0.7.0`, worker active PID `910154`, the production `libhsa` hash
  unchanged, `ctranslate2 4.8.3`, and the default `PIPX_HOME`.

**Two procedure corrections found by the rehearsal** (both preserved in the
attempt logs):

1. `pipx inject` **must use `--force`** when replacing the PyPI `ctranslate2`
   dependency with the ROCm wheel; without it pipx says "already seems to be
   injected" and leaves the CPU build in place (attempt 1 failed with
   `CUDA driver version is insufficient`).
2. The HF `faster-whisper-tiny` cache is Xet-backed: `model.bin` in the model
   directory is a symlink to a blob outside that directory. Copy a **flat**
   model directory (`config.json`, `model.bin`, `tokenizer.json`,
   `vocabulary.txt`) for an offline rehearsal; copying only the model directory
   yields `IncompleteSnapshotError` (attempt 2).

**Verdict:** rollback is genuinely rehearsed on Aether from the captured
installed bytes plus the preserved ROCm set, not merely from a rebuilt wheel.

### Mixed-version writer analysis and cutover rule

`keys/<source-key>.json` is the sole reservation authority. It is written only
through `QueueStore.enqueue`/force/repair and the `source_reservations` helpers
(`create_reservation_if_absent`, `try_advance_generation`,
`try_update_current_execution`). The worker's claim path only **reads**
reservations; it writes state records (`pending`/`processing`/`retry`/terminal).

| Process | Keys writer? | Evidence |
|---------|--------------|----------|
| Aether worker 0.7.0 | **No** | Claim path calls `read_reservation` only; `watch_legacy_pending: false` disables the legacy import that would call `enqueue`. |
| Minion `lt` 0.7.0 (manual) | **Yes** | `lt queue add`/`lt transcribe`/`lt batch` → `QueueStore.enqueue` → reservation write. |
| Minion `ref-api` fallback | **Yes** | ref-api venv cannot import `local_transcribe`; it invokes `REF_LOCAL_TRANSCRIBE_LT` (`~/.local/bin/lt` 0.7.0) with `lt queue add ... --queue-dir ...`. |
| Minion `refwatch` | **No** | No `local_transcribe`/`ref_cli` import and no queue code in its source/venv. |
| Nomnom `ref-cli` | **No** | No `lt`; `local_transcribe_*` config null; `local_transcribe` not importable. |
| `lt queue list/stats/path`, ref reconcile, ref-api `/health` | **No (readers)** | Read-only commands. |

**Cutover rule (mandatory):**

1. **Upgrade Aether first.** The 0.8.1 worker reads 0.7.0 reservation/state
   records; a read-only scan of all **8,290** production `keys/*.json` records
   and **6,481** state records under 0.8.1 reported **0 invalid, 0 errors**
   (`completed 5607`, `failed 204`, `cancelled 670`). Keep
   `watch_legacy_pending: false` on Aether. If it must ever be enabled, do so
   only after Minion is 0.8.1.
2. **Quiesce Minion producers before replacing `lt`.** Stop `ref-api` (and
   `refwatch` if it ever becomes a producer), confirm no manual `lt` producer is
   running, replace the Minion `lt`/pipx venv with 0.8.1, verify, then restart
   `ref-api`.
3. **Never run a 0.7.0 producer concurrently with a 0.8.1 producer.** Do not
   install a second producer host (Nomnom) until it is 0.8.1. No
   `lt queue repair/import/force` admin writes during the window.
4. Because Minion has exactly one `lt` binary and Aether's worker is a reader,
   quiescing producer invocations during the Minion replacement is sufficient
   to guarantee there is never a 0.7.0 and a 0.8.1 writer on `keys/`
   concurrently.

**Forbidden state:** any 0.7.0 `lt` producer invocation
(`queue add`/`transcribe`/`batch`/`repair`/`import`) while an 0.8.1 `lt` writer
is active on the same queue.

### Minion NFSv4.2 producer conclusion

**Conclusion: NFSv4.2 is safe for producer-only operations under the current
queue contract**, provided the mount remains `hard`/TCP with
`local_lock=none`, the queue path/UUID match, Minion runs **no worker**, and the
producer uses the atomic-write/advisory-lock path. NFSv3/NLM is required only
for the **worker** role (exclusive worker lock plus fail-closed mount
validation).

**Source evidence.** `lt queue add` → `resolve_queue_dir(verify_identity=True)`
checks the path and `queue.id` UUID only; it does **not** call
`validate_queue_mount(validate_nfs=True)`. `QueueStore.enqueue` and
`source_reservations` publish with `atomic_write_json` (unique temp + rename) and
`link_publish_json` (hardlink); 0.8.1 additionally serializes generation
advances with `fcntl.lockf` advisory locks under `.locks/`. Producers never
acquire the worker NLM lock. `queue doctor`, `queue init`, and worker startup
are the commands that enforce the NFSv3/NLM gate.

**Host evidence.**

- Minion's production queue is mounted
  `rw,relatime,vers=4.2,rsize=1048576,wsize=1048576,hard,proto=tcp,local_lock=none`.
- Read-only shared **and** exclusive `fcntl.lockf` probes on the production
  `queue.id` from Minion both succeeded and unlocked cleanly, confirming
  server-side NFSv4.2 advisory locking.
- `lt queue stats` with a disposable config containing the production path/UUID
  read the production queue successfully: `pending 0`, `processing 0`,
  `retry 0`, `completed 5607`, `failed 204`, `cancelled 670`.
- Disposable-queue producer tests passed for both 0.7.0 and 0.8.1.
- Production `keys/` contains 8,290 valid reservations; the newest records are
  from 2026-11-04, around when the Minion config was changed to the smoke queue.

**Limitation.** No producer write was made against the production NFSv4.2
queue during this read-only phase. The post-cutover check must enqueue one known
test URL with `--no-wait`, verify the `pending/` and `keys/` records, and let
the worker process it (or cancel it through the supported command) under the
authorized window.

### Minion config mismatch and exact future edit (not applied)

Current `~/.config/local-transcribe/config.yaml` (`mtime 2026-11-04
23:05:58Z`):

```yaml
queue:
  path: /tmp/lt-smoke-queue-59553
  expected_uuid: 6a058908-116f-41c9-987f-d5f4ef38667e
  expected_nfs_version: 3
```

`/tmp/lt-smoke-queue-59553` does not exist. `lt queue path --queue-dir
/opt/md2/music/youtube/transcripts/transcription-queue` exits 1 with a UUID
mismatch: found `8c6fc098-8cbe-41ae-9689-dd759797a582`, expected
`6a058908-116f-41c9-987f-d5f4ef38667e`. `--queue-dir` does **not** override
`expected_uuid`; the ref-api fallback therefore cannot reach the shared queue
today and falls back to the legacy pending file.

**Minimal future edit (authorized operator action):**

```yaml
queue:
  path: /opt/md2/music/youtube/transcripts/transcription-queue
  expected_uuid: 8c6fc098-8cbe-41ae-9689-dd759797a582
  expected_nfs_version: 3   # keep as a worker-role guard; producers ignore it
```

Backup and rollback:

```bash
cp -a ~/.config/local-transcribe/config.yaml \
  ~/.config/local-transcribe/config.yaml.bak-$(date -u +%Y%m%dT%H%M%SZ)
# apply with vi; then read-only verification:
~/.local/bin/lt queue path --queue-dir /opt/md2/music/youtube/transcripts/transcription-queue
~/.local/bin/lt queue stats --queue-dir /opt/md2/music/youtube/transcripts/transcription-queue
# rollback:
cp -a <backup> ~/.config/local-transcribe/config.yaml
```

Keeping `expected_nfs_version: 3` preserves the fail-closed guard against
accidentally starting a worker on Minion's NFSv4.2 mount; `queue add` does not
consult it. Setting it to `4` would remove that guard and is **not**
recommended for a producer-only host. Do not work around the stale UUID with
`--queue-dir` alone.

### Ref-api dependency chain and post-cutover checks

`ref-api` → external `lt` (`REF_LOCAL_TRANSCRIBE_LT=/home/draeician/.local/bin/lt`)
→ production queue path/UUID. The ref-api venv has no `local_transcribe`, so the
fallback argv is exactly:

```
lt queue add <url> --origin ref --priority 20 --queue-dir <queue>
```

Safe verification on 2026-11-08T17:52Z used a **disposable queue** and the real
0.7.0 `lt`: `add_url_to_pending_file` returned `action='enqueued'`, execution
`f3a17d35-759f-4e26-b07d-b37eb50e5385`, source key `youtube:zzzzzzzzzzz`; the
disposable `pending/` record had `origin=ref`, `priority=20`, and the matching
`keys/` record. No production queue or archive was touched. `/health` returned
`{"status":"ok","version":"1.6.12b1.dev129+g80c6e3791"}`. A temp-archive
reconciliation smoke with a disposable queue returned `candidates=0,
updated=0, errors=0` and left the temp archive unchanged.

**Post-cutover checks (after authorization):**

1. `curl -fsS http://127.0.0.1:8000/health` → 200 and the expected ref version.
2. Confirm the live `ref-api` process environment still points to
   `~/.local/bin/lt` (now 0.8.1) and the production queue UUID.
3. Enqueue one known test URL via ref-api or `ref_cli.add_url_to_pending_file`
   with a test-safe method; verify `pending/` and `keys/` show `origin: ref`,
   `priority: 20`; let the worker process it or cancel it through the supported
   command.
4. `lt queue stats`/`lt queue list` with the fixed config read the production
   queue; `lt version` reports 0.8.1.
5. Reconciliation: confirm the periodic pass reads the queue and does not
   corrupt `references.md`; a temp-archive smoke is acceptable for rehearsal.
6. Check ref-api logs for `lt queue add failed`, UUID mismatch, or
   `transcript-pending.md` fallback; none expected after the config fix.

### Final production cutover order

1. **Pre-window.** Re-verify the wheel SHA256 and rollback hashes on both hosts;
   confirm Aether is idle (pending 0, worker sleeping, PID 910154); capture
   fresh `lt version`, worker state, and queue stats; confirm the rollback
   tarballs are readable and disk space is sufficient.
2. **Aether worker (idle window).** Stop `local-transcribe-worker.service`;
   replace the production pipx venv with the exact 0.8.1 wheel; re-inject
   `ctranslate2 4.8.3` with `--force --no-deps`; restore the ROCm trees from the
   preserved tarball; verify imports, `gpu_runtime_status`, `amd-rocm`, and one
   controlled local transcription; start the worker; verify it holds the lock
   and completes a known job.
3. **Minion producers.** Stop `ref-api` (and `refwatch` if it is a producer);
   confirm no manual `lt` producer; apply the config edit (path + UUID) with a
   backup; replace Minion `lt` with 0.8.1; verify `lt version`, `queue path`,
   `queue stats`, and a disposable-queue `queue add`; restart `ref-api`; run the
   post-cutover checks.
4. **Nomnom.** No action. If a CLI is later desired, install 0.8.1 only; do not
   install a worker.
5. **Monitor.** Keep the rollback artifacts until the window closes; watch the
   Aether worker, Minion ref-api, queue stats, and transcript reconciliation.

**Rollback order if needed:** stop Minion producers → restore the captured
0.7.0 tree + config backup on Minion → verify `lt version`/queue stats → stop
the Aether worker → restore the captured 0.7.0 tree + CTranslate2/ROCm set on
Aether → verify GPU → start the worker. Do not run a 0.7.0 and 0.8.1 producer
concurrently during rollback.

### Explicit abort conditions

- Any artifact or rollback hash mismatch on any host.
- Aether is not idle, or the worker lock is held by an unexpected process.
- Aether GPU probe/transcription fails after upgrade, or `gpu_backend` is not
  `amd-rocm`.
- The restored ROCm `libhsa` hash is not
  `97fbbeda0548d6b15b15956056a1fc4b3593acc5d3cbe15874225b0520aff2a5`.
- Minion config verification fails (path/UUID) or `lt queue stats` cannot read
  the production queue.
- Any 0.7.0 producer is active during the Minion `lt` replacement.
- Any mixed-version writer is detected (two `lt` versions, or a 0.7.0 producer
  on Nomnom/another host).
- The worker fails to start/claim, or queue records become unreadable.
- Any production queue mutation beyond the authorized test URL.
- Ref-api `/health` fails or the enqueue fallback still fails after the change.
- On any failure: stop, restore the captured baseline, do not proceed, and keep
  the blocked evidence.

### Fresh verification gates on the refreshed tree

Tree `1343b3badef6c199840a7451930ef892ed6fd95f` (normal merge of
`origin/main` `f147340` into the task branch), ext4 worktree, fresh venv
Python 3.12.3, pytest 9.2.0, Ruff 0.17.4. No task-044 result was reused.

| Gate | Command | Result |
|------|---------|--------|
| Canonical Ruff | `ruff check --config pyproject.toml src tests` | **All checks passed** (exit 0) |
| Full pytest, `NO_COLOR=1`, ext4 | `python -m pytest -q` | **377 passed, 11 skipped, 1 warning** (87.16s), exit 0 |
| Full pytest, default, ext4 | `python -m pytest -q` | **377 passed, 11 skipped, 1 warning** (72.50s), exit 0 |
| Diff check | `git diff --check` and `git diff --check origin/main...HEAD` | clean (exit 0) |
| Version consistency | `pyproject.toml`, `__init__.py`, `project_spec.md`, `CHANGELOG.md`, `docs/WHATS_NEW.md` | all **0.8.1** |
| Build verification | `python -m build` from the refreshed tree | wheel/sdist built; wheel metadata `0.8.1`, entry point `lt = local_transcribe.cli:main`; wheel contents identical to the staged artifact (48/48 files) |
| Secret scan | branch delta + tracked tree | **no matches** |

`ruff check .` (root scope) still reports **52 pre-existing findings** in root
helper scripts (`export-firefox-cookies.py`, `install_local_transcribe.py`) that
are outside the canonical `src tests` scope and are not introduced by this
branch. The canonical gate is clean.

Evidence:
`~/.local/share/local-transcribe/evidence/20261108T-gate043-refresh/`.

### Remaining blockers

1. **Production cutover is not authorized.** No service action, pipx
   replacement, or live config edit was performed.
2. **Minion live config is still stale.** The ref-api producer path currently
   fails the UUID check and falls back to the legacy pending file; the documented
   path+UUID edit must be applied under authorization before cutover.
3. **Minion `lt` is still 0.7.0; Aether worker is still 0.7.0.** The cutover
   itself is the remaining work.
4. **NFSv4.2 producer write was not exercised against the production mount**
   (read-only phase). The post-cutover test URL is required.
5. **Aether rollback tarball is 890MB.** Ensure ~4.2G free for tarball +
   extraction during a rollback; Aether had 60G free at inspection.
6. **No tag/release/publish/merge/deployment** was performed. Task 043 is in
   `review/`; it is not merged.

### Authorization required for production cutover

1. A production idle window and authorization to stop/start
   `local-transcribe-worker.service` on Aether.
2. Authorization to replace the Aether pipx venv with the exact 0.8.1 wheel,
   re-inject `ctranslate2 4.8.3` from the preserved wheel, and restore the
   preserved ROCm SDK trees.
3. Authorization to back up and edit Minion's
   `~/.config/local-transcribe/config.yaml` (path + `expected_uuid`).
4. Authorization to stop/restart `ref-api.service` and replace Minion's `lt`
   with 0.8.1.
5. Authorization to enqueue one known test URL into the production queue and
   let the worker process it (or cancel it through the supported command) for
   post-cutover verification.
6. Confirmation that Nomnom remains without `lt`/worker.
7. No tag, release, publication, merge, or deployment without a separate
   explicit authorization.

### Evidence paths

- Task record: `.crules/tasks/wip/043-deploy-preflight-3host.md` (moved to
  `review/` on completion).
- Durable artifact set (Nomnom, mirrored on Minion/Aether):
  `~/.local/share/local-transcribe/deploy-artifacts/20261108T1624Z-v0.8.1/`.
- Minion rehearsals:
  `.../20261108T1624Z-rehearsal-minion.log`,
  `.../20261108T1624Z-rehearsal-minion-evidence/`,
  `.../20261108T1624Z-rollback-rehearsal-minion.log`,
  `.../20261108T1624Z-rollback-rehearsal-minion-evidence/`.
- Aether rehearsals:
  `.../20261108T1624Z-rehearsal-aether.log` (plus attempt 1/2 logs),
  `.../20261108T1624Z-rehearsal-aether-evidence/`,
  `.../20261108T1624Z-rollback-rehearsal-aether.log`,
  `.../20261108T1624Z-rollback-rehearsal-aether-evidence/`.
- Fresh gate evidence:
  `~/.local/share/local-transcribe/evidence/20261108T-gate043-refresh/`.

---

## Closure 2026-11-08 — approved preflight merge and cleanup

**Approval.** ChatGPT approved task 043 for the preflight merge and cleanup
phase. Approval covered only: final pre-merge verification, the `review` →
`done` transition, a normal non-squash merge into `main`, pushing `main`,
cleanup of the task-043 branch/worktree, and the required lifecycle
documentation. It did **not** authorize production deployment.

**Approved head and base.** Remote feature head
`1758e4b9a4df9753a3d4a5b248dadb425f9120e4`; expected and verified `origin/main`
`f147340f9c6e4f36da10618c7abb947bd491fccb`. The worktree had no unintended
changes before the gate.

### Final pre-merge verification (fresh, not reused)

Environment: `nomnom`, user `draeician`, unprivileged; worktree
`/tmp/opencode/local_transcribe-preflight` on ext4; venv
`/tmp/opencode/task043-gate-venv` (Python 3.12.3, pytest 9.2.0, Ruff 0.17.4,
editable install of this tree at 0.8.1).

| Check | Command | Result | Exit |
| --- | --- | --- | --- |
| Canonical Ruff | `ruff check --config pyproject.toml src tests` | **All checks passed** | 0 |
| Full pytest (ext4, `NO_COLOR=1`) | `python -m pytest -q` | **377 passed, 11 skipped, 1 warning** (85.07 s) | 0 |
| Working-tree whitespace | `git diff --check` | no output | 0 |
| Branch whitespace | `git diff --check origin/main...HEAD` | no output | 0 |
| Version consistency | `pyproject.toml`, `__init__.py`, `project_spec.md`, `CHANGELOG.md`, `docs/WHATS_NEW.md` | all **0.8.1** | 0 |
| Secret scan (branch delta) | `git diff --unified=0 f147340...HEAD` + `git grep` over 3 changed files | 8 diff / 15 changed-file matches, all policy prose or scan-command text; **0 credential-shape matches** | reviewed |

Evidence: `~/.local/share/local-transcribe/evidence/20261108T-gate043-merge/`.

### Artifact and rehearsal facts carried into the merge

- **0.8.1 wheel SHA256:**
  `3b05384388bad18a1d2bdb3eadd2838c83256bda047eaf3fee04b76f4a16ff3d`
  (sdist `b79124cf0b236e970c362bca6e4c6b5118f2c36438a65983e8da563fd9844004`).
- **Minion rehearsal:** passed from both the representative 0.7.0 wheel and the
  captured installed 0.7.0 tree; upgrade to 0.8.1, rollback, and re-upgrade
  exercised `lt version` and queue commands against disposable queues only.
- **Aether ROCm rehearsal:** passed all four stages (0.7.0 → 0.8.1 → 0.7.0 →
  0.8.1) plus a captured-bytes restore, with `gpu_backend=amd-rocm`, one GPU,
  and a non-empty transcript each stage; production verified untouched
  afterward.
- **Remaining production limitations:** Minion live config is still stale
  (path/UUID); Minion `lt` and the Aether worker remain 0.7.0; no producer write
  was exercised against the production NFSv4.2 queue; the documented cutover
  window and explicit operator authorization are still required.

### Merge and cleanup

Merged the complete feature branch into `main` with a normal non-squash,
non-rewriting merge; pushed `main`; verified the remote `main`, the `done/`
location of this task, task 044 still under `done/`, the 0.8.1 source version,
and the preflight commit ancestry. Then removed the feature worktree and deleted
the local and remote feature branch, and removed the local WIP marker. Durable
deployment/rollback artifacts and evidence were retained for the production
window.

**Production cutover is NOT part of this merge.** No Aether worker stop/start,
Aether pipx change, Minion config edit, Minion pipx change, ref-api restart,
production enqueue, NFS/ROCm change, tag, publication, release, or Nomnom
installation was performed.

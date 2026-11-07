# Task 043 — Three-host upgrade/rollback preflight (read-only)

| Field | Value |
|-------|-------|
| **Status** | `wip` — **blocked**: required gate fails |
| **Branch** | `chore/three-host-upgrade-preflight` |
| **Reviewed head** | `b9f686dc664fc6ff809b1db62c2c1bae6e7f7561` |
| **Base** | `origin/main` @ `abdedbe88b8b44d24582520264f463bebcc83109` |
| **Type** | `chore` (deployment preflight / evidence only) |
| **Owner** | OpenCode |
| **Phase** | Post-0.8.0 production rollout planning |
| **Depends on** | 041, 042 |
| **Blocks** | Actual three-host upgrade (separate authorized task) |
| **Correction window** | 2026-11-07T17:25:35Z – 2026-11-07T17:33Z |

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

| Host | OS / kernel | System Python | pipx | `local-transcribe` | Role | Queue mount | `ref` |
|------|-------------|---------------|------|--------------------|------|-------------|-------|
| `nomnom` | Linux Mint 22.3, 7.0.0-42 | 3.12.3 | 1.4.3 | **none** | none | **NFSv3** (`vers=3`) | pipx `ref-cli 1.6.12b1.dev129+g80c6e3791` (candidate) |
| `minion-26` | Ubuntu 26.04.1, 7.0.0-38 | 3.14.4 | 1.8.0 | **0.7.0**, venv Python 3.14.4, VCS `25b89f1326751a3b0781051331822c50ffde3b19` | producer candidate; **no worker**; `ref-api.service` active | **NFSv4.2** (`vers=4.2`) | `ref-cli`/`ref-api` `1.6.12b1.dev129+g80c6e3791` (candidate) |
| `aether` | Ubuntu 25.10, 6.17.0-41 | 3.13.7 | 1.7.1 | **0.7.0**, venv Python 3.13.7, provenance `file:///tmp/lt-branch` (deleted) | **sole NLM worker** | **NFSv3** (`vers=3`) | none |

Shared queue: `/opt/md2/music/youtube/transcripts/transcription-queue`, UUID
`8c6fc098-8cbe-41ae-9689-dd759797a582`, `created_by: nomnom`.

## Evidence table

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

### Pinned candidates

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

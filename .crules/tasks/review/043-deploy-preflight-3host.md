# Task 043 — Three-host upgrade/rollback preflight (read-only)

| Field | Value |
|-------|-------|
| **Status** | `review` |
| **Branch** | `chore/three-host-upgrade-preflight` |
| **Base** | `origin/main` @ `abdedbe88b8b44d24582520264f463bebcc83109` |
| **Type** | `chore` (deployment preflight / evidence only) |
| **Owner** | OpenCode |
| **Phase** | Post-0.8.0 production rollout planning |
| **Depends on** | 041, 042 |
| **Blocks** | Actual three-host upgrade (separate authorized task) |

## Objective

Produce a verified, host-specific upgrade and rollback preflight for
**nomnom**, **minion-26**, and **aether**, then publish read-only evidence and a
proposed plan. **No deployment, no install, no service action, no queue
mutation.**

## Hard constraints honoured

- Read-only against production. No installs, upgrades, service
  starts/stops/restarts, queue mutations, counter resets, archive edits,
  driver/NFS changes, `lt update`, releases, tags, merges, or branch cleanup.
- No worker created where none exists.
- Actual hostnames, commands, outputs, exit codes, and UTC timestamps recorded.
- No secret values copied or pasted; only key names and redacted presence.
- Candidate SHAs revalidated; no silent selection of a newer `main`.

## Candidate revalidation (2026-11-07T17:01Z)

| Repo | Candidate | `origin/main` at snapshot | Match |
|------|-----------|---------------------------|-------|
| `local_transcribe` | `abdedbe88b8b44d24582520264f463bebcc83109` | `abdedbe88b8b44d24582520264f463bebcc83109` | yes, no advancement |
| `ref` | `80c6e379106a73c047cd517e56207bc62d558d7f` | `80c6e379106a73c047cd517e56207bc62d558d7f` | yes |

Ref review branch `docs/record-url-dump-deploy` at
`515987802ba3b53cde8f3cfdb55ed6993631d7c2` reviewed. It records the authorized
2026-11-02 production deployment of `--url-dump` on `minion-26`: both the pipx
CLI and the dedicated `ref-api` service venv run
`ref-cli 1.6.12b1.dev129+g80c6e3791`, installed from the audited wheel
`ref_cli-1.6.12b1.dev129+g80c6e3791-py3-none-any.whl` (sha256 `03ee2687…`)
built from approved source `main @ 80c6e379…`. Rollback artifacts and
`ROLLBACK.md` are preserved under
`/home/draeician/.local/share/ref-services/deploy-backups/20261102T1416Z-pr26-url-dump/`.

## Evidence table

Snapshot window: **2026-11-07T16:57:46Z – 17:01:46Z**. Queue is live and counts
drift between reads; figures below are a point-in-time snapshot, not a target.

| Host | Service / role | Environment | Installed version + source | Proposed target | Upgrade needed | Blockers | Rollback readiness |
|------|----------------|-------------|----------------------------|-----------------|----------------|----------|--------------------|
| **nomnom** (`nomnom`) | None. No `lt` CLI, no worker, no `ref-api`. Optional producer only. | Linux Mint 22.3 (Zena), kernel 7.0.0-42-generic, x86_64; AMD Ryzen 5 5600X3D; NVIDIA RTX 3060 + RTX 4060 Ti; system Python 3.12.3; pipx 1.4.3; queue mount **NFSv3** (`vers=3`). | `local-transcribe`: **not installed** (no `lt` on PATH, no `~/.local/bin/lt`, not in `pipx list`, no unit, no process). Repo checkouts at `abdedbe`; repo `.venv` metadata reports `0.4.0` while source CLI reports `0.8.0`. `ref-cli 1.6.12b1.dev129+g80c6e3791` via pipx (already candidate). | Fresh **0.8.0** install only if a CLI is required. Do **not** create a worker. | Conditional (nothing installed today). | No installed CLI; `~/.config/local-transcribe` was present in an earlier read-only read and absent in the inventory read (unexplained; not recreated). `~/.config/ref/config.yaml` has `local_transcribe_lt`/`queue_dir`/`queue_uuid` all `None`. | N/A today. If installed fresh: `pipx uninstall local-transcribe` returns to current state. |
| **minion-26** (`minion-26`) | `local-transcribe` CLI (producer candidate); **no worker**; `ref-api.service` (active). | Ubuntu 26.04.1 LTS, kernel 7.0.0-38-generic, x86_64, KVM VM; system Python 3.14.4; pipx 1.8.0; queue mount **NFSv4.2** (`vers=4.2`). | `local-transcribe 0.7.0` via pipx, venv Python 3.14.4, VCS provenance commit `25b89f1326751a3b0781051331822c50ffde3b19` (0.7.0). Deps: `ctranslate2 4.8.3`, `faster-whisper 1.2.1`, `numpy 2.5.4`, `onnxruntime 1.31.0`, `tokenizers 0.23.3`; **no injected packages**. `ref-cli` and `ref-api` both `1.6.12b1.dev129+g80c6e3791` from the audited wheel. | `local-transcribe` **0.7.0 → 0.8.0** from an audited build; correct the config to the production queue **only if** a producer/worker role is intended. `ref-api`/`ref-cli`: **no change**. | **Yes** (local-transcribe only). | `~/.config/local-transcribe/config.yaml` points to `/tmp/lt-smoke-queue-59553` (missing) with UUID `6a058908-116f-41c9-987f-d5f4ef38667e`, **not** production. Queue mount is **NFSv4.2**, which contradicts the tool's NFSv3 requirement and blocks any worker role / `queue doctor`. Deno absent (affects direct YouTube download only). `0.7.0` rollback wheel not preserved on-host. | Partial. `ref` has full artifacts (`ROLLBACK.md`, previous wheel sha256, unit/config copies). `local-transcribe` needs a 0.7.0 wheel built from `25b89f1` **before** upgrade. |
| **minion-26** | `ref-api.service` (user unit) | systemd user unit `~/.config/systemd/user/ref-api.service`; dedicated venv Python 3.13.17; `ExecStartPre=/usr/bin/mountpoint -q /opt/md2`; `Restart=on-failure`. | `ref-cli 1.6.12b1.dev129+g80c6e3791` from the audited wheel in `deploy-backups/20261102T1416Z-pr26-url-dump/new-wheel/`. Active, PID `3521265`, `NRestarts=0`, started 2026-11-02T14:20:03Z. `/health` → `{"status":"ok","version":"1.6.12b1.dev129+g80c6e3791"}`; `/status` → 404. | **No change** (already at candidate). | **No.** | None observed. `~/.config/ref/config.yaml` has `local_transcribe_*` keys `None`, but `ref-api.env` sets them (values redacted) and the queue dir/UUID match production. | **Ready.** `ROLLBACK.md`, previous wheel, unit/config copies, and pre-change freeze/state/journal are preserved. |
| **minion-26** | pipx `ref-cli` (CLI) | pipx venv; Python 3.14.4 (system). | `ref-cli 1.6.12b1.dev129+g80c6e3791`. | **No change.** | **No.** | None. | Covered by the same `ref` deploy-backup artifacts. |
| **aether** (`aether`) | `local-transcribe-worker.service` — **sole NLM lock holder**, active. | Ubuntu 25.10, kernel 6.17.0-41-generic, x86_64; GMKtec NucBox EVO-X2; AMD Ryzen AI MAX+ 395 / Radeon 8060S (**gfx1151**); system Python 3.13.7; pipx 1.7.1; system ROCm `/opt/rocm-7.2.0` (`rocm-core 7.2.0.70200`); queue mount **NFSv3** (`vers=3`). | `local-transcribe 0.7.0`, venv Python 3.13.7, provenance **`file:///tmp/lt-branch`** (deleted; no preserved wheel found). Injected: `ctranslate2 4.8.3` (from cached ROCm wheel, `--no-deps --force-reinstall`), `rocm-sdk-core 7.13.0a20260515`, `rocm-sdk-libraries-gfx1151 7.13.0a20260515`. Also `faster-whisper 1.2.1`, `yt-dlp 2026.8.19`, `av 18.1.0`, `huggingface_hub 2.2.0`. Unit: `ExecStart=/home/draeician/.local/bin/lt worker run --standby`, PID `910154`, `NRestarts=13`, current run since 2026-11-06T09:16:36Z, lock acquired 2026-11-06T09:16:37Z. | `local-transcribe` **0.7.0 → 0.8.0**, explicitly preserving the injected ROCm packages and the wheel cache. | **Yes.** | Provenance tree gone; no on-host rollback artifact; `NRestarts=13` includes prior `ABRT` core-dumps and `OOM` kills (latest 2026-11-06T09:16:26Z); queue is live and must be idle/drained for cutover. | **Not ready.** Must create a rollback artifact (installed-tree tarball + reproducible 0.7.0 wheel from `25b89f1`) and record the injected package set **before** upgrade. `~/.cache/local-transcribe/rocm/pycp313/` must be preserved. |

### Shared queue snapshot (all hosts, read-only)

| Field | Value |
|-------|-------|
| Queue path | `/opt/md2/music/youtube/transcripts/transcription-queue` |
| Queue UUID | `8c6fc098-8cbe-41ae-9689-dd759797a582` |
| Created | 2026-07-30, `created_by: nomnom` |
| Counts (~16:58Z) | pending 43, processing 1, retry 0, completed 5565, failed 202, cancelled 670, keys 8290 |
| Admission | attempts/hour 10, today 351; total 429 = 54, total 403 = 28; consecutive throttle failures 0; last 403 2026-11-07T16:11:45Z; last 429 2026-11-07T13:34:30Z |
| Lock holder | `aether` PID `910154`, acquired 2026-11-06T09:16:37Z |

Aether was observed completing `e8d987f0` and `ba0ed373` (transcripts present on
disk) and starting `377492a3` during the snapshot, confirming the worker is
genuinely active.

## Upgrade ordering recommendation

1. **Create rollback artifacts first** on `aether` and `minion-26` (read-only
   build + tarball; no production change).
2. **Minion** `local-transcribe` 0.7.0 → 0.8.0 and config correction. Lowest
   risk: no worker, ref-api untouched. Verify ref-api `/health` before/after.
3. **Aether** worker 0.7.0 → 0.8.0 in an idle window, preserving injected ROCm
   packages. Highest risk: sole lock holder, live queue, restart/OOM history.
4. **Nomnom** fresh install only if a CLI is required. Never create a worker.
5. **ref** requires no upgrade on any host.

Do not start Aether and Minion cutovers concurrently: Aether is the only worker
and Minion's ref-api is the only producer-side service.

## Proposed commands (NOT EXECUTED)

All commands are labelled with host, user, directory, and privilege. Nothing
below was run. `sudo` is **not** required for any step except the explicitly
marked optional NFS remount on Minion.

### 0. Common staging directory (per host)

```bash
# HOST: aether | minion-26 | nomnom   USER: draeician   PRIV: unprivileged
# DIR: ~  (i.e. /home/draeician)
UTC="$(date -u +%Y%m%dT%H%MZ)"
BK="$HOME/.local/share/local-transcribe/deploy-backups/${UTC}-v0.8.0"
mkdir -p "$BK"
```

### 1. Aether — build rollback artifact (read-only, before any change)

```bash
# HOST: aether   USER: draeician   PRIV: unprivileged
# DIR: /tmp/opencode/lt-0.7.0-build and ~/.local/share/local-transcribe/deploy-backups/<UTC>-v0.8.0
# 1a. Capture the installed tree exactly as it exists now.
tar -C "$HOME/.local/share/pipx/venvs/local-transcribe/lib/python3.13/site-packages" \
    -czf "$BK/local_transcribe-0.7.0-installed-tree.tar.gz" \
    local_transcribe local_transcribe-0.7.0.dist-info
cp "$HOME/.local/share/pipx/venvs/local-transcribe/pipx_metadata.json" \
   "$BK/pipx_metadata-before.json"
"$HOME/.local/share/pipx/venvs/local-transcribe/bin/python" -m pip freeze \
   > "$BK/venv-freeze-before.txt"
cp "$HOME/.config/local-transcribe/config.yaml" "$BK/config-before.yaml"
systemctl --user show local-transcribe-worker.service > "$BK/worker-unit-before.txt"

# 1b. Reproducible 0.7.0 wheel from the known 0.7.0 commit (fallback artifact).
git clone https://github.com/draeician/local_transcribe.git /tmp/opencode/lt-0.7.0-build
git -C /tmp/opencode/lt-0.7.0-build checkout 25b89f1326751a3b0781051331822c50ffde3b19
(cd /tmp/opencode/lt-0.7.0-build && uv build --out-dir dist)
sha256sum /tmp/opencode/lt-0.7.0-build/dist/local_transcribe-0.7.0-py3-none-any.whl \
  | tee "$BK/rollback-wheel.sha256"

# 1c. Prove equivalence (or record the difference) before relying on the wheel.
#     Read-only diff of installed package vs the 25b89f1 checkout.
diff -r \
  /tmp/opencode/lt-0.7.0-build/src/local_transcribe \
  "$HOME/.local/share/pipx/venvs/local-transcribe/lib/python3.13/site-packages/local_transcribe" \
  > "$BK/installed-vs-25b89f1.diff" || true
```

If `installed-vs-25b89f1.diff` is non-empty, the deleted `/tmp/lt-branch` tree
differed from `25b89f1`; the tarball from step 1a is then the authoritative
rollback, and the wheel is only a best-effort fallback. Record this before
cutover.

### 2. Aether — cutover (idle window only)

```bash
# HOST: aether   USER: draeician   PRIV: unprivileged
# DIR: ~   (pipx venv, user unit)
# 2a. Confirm idle: no processing execution, no active download.
cat /opt/md2/music/youtube/transcripts/transcription-queue/worker/state.json
ls -1 /opt/md2/music/youtube/transcripts/transcription-queue/processing/*.json 2>/dev/null

# 2b. Stop the worker (releases the NLM lock on exit).
systemctl --user stop local-transcribe-worker.service
systemctl --user show local-transcribe-worker.service -p ActiveState -p SubState

# 2c. Install 0.8.0 into the EXISTING venv, keeping injected ROCm packages.
#     --no-deps is safe: 0.8.0 has no dependency changes vs 0.7.0.
pipx install /tmp/opencode/local_transcribe-0.8.0-py3-none-any.whl \
  --force --pip-args="--no-deps"

# 2d. Confirm injected packages survived; re-inject from cache only if lost.
"$HOME/.local/share/pipx/venvs/local-transcribe/bin/python" -m pip list --format=freeze \
  | grep -iE '^(local-transcribe|ctranslate2|rocm-sdk-core|rocm-sdk-libraries-gfx1151|faster-whisper)=='

# 2e. Start and verify.
systemctl --user start local-transcribe-worker.service
systemctl --user show local-transcribe-worker.service -p ActiveState -p SubState -p MainPID -p NRestarts
cat /opt/md2/music/youtube/transcripts/transcription-queue/worker/worker.lock
"$HOME/.local/bin/lt" version
"$HOME/.local/bin/lt" queue doctor
```

Expected validation: `lt version` → `0.8.0`; `ctranslate2==4.8.3`,
`rocm-sdk-core==7.13.0a20260515`, `rocm-sdk-libraries-gfx1151==7.13.0a20260515`
still present; worker acquires the lock as `aether`; one job moves
`processing → completed` with a transcript on disk; no new `ABRT`/`OOM` in the
journal.

If pipx recreates the venv instead of reusing it, stop and re-inject before
starting the worker:

```bash
# HOST: aether   USER: draeician   PRIV: unprivileged
# DIR: ~/.cache/local-transcribe/rocm/pycp313/extracted (preserved cache)
V="$HOME/.local/share/pipx/venvs/local-transcribe/bin/python"
"$V" -m pip install --no-deps --force-reinstall \
  "$HOME/.cache/local-transcribe/rocm/pycp313/extracted/ctranslate2-4.8.3-cp313-cp313-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl"
# re-inject rocm-sdk-core / rocm-sdk-libraries-gfx1151 from the preserved cache/zip
```

### 3. Aether — rollback

```bash
# HOST: aether   USER: draeician   PRIV: unprivileged
# DIR: ~
systemctl --user stop local-transcribe-worker.service
# Preferred: restore the captured installed tree.
tar -C "$HOME/.local/share/pipx/venvs/local-transcribe/lib/python3.13/site-packages" \
    -xzf "$BK/local_transcribe-0.7.0-installed-tree.tar.gz"
# Fallback: reinstall the reproducible wheel.
pipx install "$BK/../lt-0.7.0-build/dist/local_transcribe-0.7.0-py3-none-any.whl" \
  --force --pip-args="--no-deps"
"$HOME/.local/bin/lt" version   # expect 0.7.0
systemctl --user start local-transcribe-worker.service
systemctl --user show local-transcribe-worker.service -p ActiveState -p MainPID -p NRestarts
```

### 4. Minion — build rollback artifact (read-only, before any change)

```bash
# HOST: minion-26   USER: draeician   PRIV: unprivileged
# DIR: /tmp/opencode/lt-0.7.0-build and ~/.local/share/local-transcribe/deploy-backups/<UTC>-v0.8.0
cp "$HOME/.config/local-transcribe/config.yaml" "$BK/config-before.yaml"
cp "$HOME/.config/ref/ref-api.env" "$BK/ref-api.env.before"   # secrets: keep mode 600, never commit
chmod 600 "$BK/ref-api.env.before"
"$HOME/.local/share/pipx/venvs/local-transcribe/bin/python" -m pip freeze > "$BK/venv-freeze-before.txt"
systemctl --user show ref-api.service > "$BK/ref-api-unit-before.txt"
curl -sS -m 5 http://127.0.0.1:8000/health > "$BK/ref-api-health-before.json"

git clone https://github.com/draeician/local_transcribe.git /tmp/opencode/lt-0.7.0-build
git -C /tmp/opencode/lt-0.7.0-build checkout 25b89f1326751a3b0781051331822c50ffde3b19
(cd /tmp/opencode/lt-0.7.0-build && uv build --out-dir dist)
sha256sum /tmp/opencode/lt-0.7.0-build/dist/local_transcribe-0.7.0-py3-none-any.whl \
  | tee "$BK/rollback-wheel.sha256"
```

Minion's provenance is exactly `25b89f1`, so this wheel is a faithful rollback
for that host.

### 5. Minion — cutover and validation

```bash
# HOST: minion-26   USER: draeician   PRIV: unprivileged
# DIR: ~   (pipx venv; ref-api user unit untouched)
pipx install /tmp/opencode/local_transcribe-0.8.0-py3-none-any.whl \
  --force --pip-args="--no-deps"

"$HOME/.local/bin/lt" version                 # expect 0.8.0, no Deno warning
"$HOME/.local/bin/lt" queue stats \
  --queue-dir /opt/md2/music/youtube/transcripts/transcription-queue   # read-only
"$HOME/.local/bin/lt" queue doctor \
  --queue-dir /opt/md2/music/youtube/transcripts/transcription-queue   # expect NFSv4.2 rejection
curl -sS -m 5 http://127.0.0.1:8000/health    # expect unchanged ref-api version
```

Config correction — **only if** Minion is to produce into the production queue.
Do this as an explicit edit, not a blind overwrite:

```bash
# HOST: minion-26   USER: draeician   PRIV: unprivileged
# DIR: ~/.config/local-transcribe
# Replace the smoke-queue path/UUID with:
#   path: /opt/md2/music/youtube/transcripts/transcription-queue
#   expected_uuid: 8c6fc098-8cbe-41ae-9689-dd759797a582
#   expected_nfs_version: 3
# Then verify:
"$HOME/.local/bin/lt" queue path
"$HOME/.local/bin/lt" queue doctor
```

**Do not** enable a Minion worker while the mount is NFSv4.2. The optional
remount is privileged and out of scope for this preflight:

```bash
# HOST: minion-26   USER: root   PRIV: sudo (OPTIONAL, NOT PROPOSED HERE)
# Requires changing /etc/fstab or the autofs map to force vers=3, then remount.
# Do not run without a separate approved change.
```

### 6. Minion — rollback

```bash
# HOST: minion-26   USER: draeician   PRIV: unprivileged
# DIR: ~
pipx install /tmp/opencode/lt-0.7.0-build/dist/local_transcribe-0.7.0-py3-none-any.whl \
  --force --pip-args="--no-deps"
cp "$BK/config-before.yaml" "$HOME/.config/local-transcribe/config.yaml"
"$HOME/.local/bin/lt" version   # expect 0.7.0
curl -sS -m 5 http://127.0.0.1:8000/health   # ref-api must remain OK
```

### 7. Nomnom — optional fresh install

```bash
# HOST: nomnom   USER: draeician   PRIV: unprivileged
# DIR: ~   (no worker, no service)
pipx install /tmp/opencode/local_transcribe-0.8.0-py3-none-any.whl
"$HOME/.local/bin/lt" version                 # expect 0.8.0
"$HOME/.local/bin/lt" queue stats \
  --queue-dir /opt/md2/music/youtube/transcripts/transcription-queue   # read-only
```

Do not recreate `~/.config/local-transcribe` until the disappearance noted above
is explained.

### 8. Nomnom — rollback

```bash
# HOST: nomnom   USER: draeician   PRIV: unprivileged
pipx uninstall local-transcribe
```

## Verification performed (this task)

| Check | Result |
|-------|--------|
| Candidate `local_transcribe` SHA vs `origin/main` | identical `abdedbe…` |
| Candidate `ref` SHA vs `origin/main` | identical `80c6e379…` |
| Ref review branch commit present | `5159878…` reviewed |
| Aether worker liveness | active, lock held, jobs completing |
| Minion ref-api liveness | active, `/health` OK |
| Queue identity | matches `8c6fc098…` on all hosts |
| Queue mutation | none |
| Service action | none |
| Install/upgrade | none |
| Change scope | docs-only: `git diff --stat origin/main -- . ':!*.md'` empty |
| Ruff gate | `ruff check --config pyproject.toml src tests` → **All checks passed** |
| Full suite | **341 passed, 11 skipped**, 1 pre-existing flake `test_concurrent_force_single_current_generation` |
| Version | `pyproject.toml` and `__init__.py` both `0.8.0`; CLI reports `0.8.0` |
| Deno-free queue help | `lt queue --help` → 0 occurrences of "deno" |
| `git diff --check` | exit 0 |
| Secret scan | no secret patterns; no raw `REF_*=` values |
| Pre-existing flake evidence | same failure on untouched `origin/main` in 3/3 isolated reruns |

The 7 default-environment pytest failures were traced to
`Console(force_terminal=True)` emitting ANSI codes into captured output; they
reproduce identically on untouched `origin/main`. With `NO_COLOR=1` they clear.
The remaining `test_wait_completed_via_foreground` failure is a pre-existing
rename-then-write race between `atomic_state_transition` and `locate_execution`;
it passes on tmpfs and fails on ext4, and also fails on untouched `origin/main`.
Neither is caused by this docs-only change.

## Files changed

| File | Change |
|------|--------|
| `.crules/tasks/review/043-deploy-preflight-3host.md` | this task record |
| `HANDOFF.md` | preflight evidence, blockers, recommended ordering |
| `docs/QUEUE_DESIGN_DECISIONS.md` | §18 deployment preflight decisions |

## Acceptance criteria

- [x] Read-only evidence collected from all three hosts with timestamps.
- [x] Candidate SHAs revalidated; no advancement silently adopted.
- [x] Ref branch `docs/record-url-dump-deploy` reviewed.
- [x] One evidence table covers host, service, environment, installed
      version/source, target, upgrade need, blockers, rollback readiness.
- [x] Proposed commands labelled by host, user, directory, privilege; not run.
- [x] No secrets copied; only key names and redacted presence.
- [x] No worker created where none existed.
- [x] Repository gate passed (Ruff clean; full suite green except the documented
      pre-existing flake also present on `origin/main`).
- [x] Task moved to `review/`; branch pushed; remote SHA verified; WIP marker
      removed after verification.

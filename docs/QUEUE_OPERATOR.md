# Queue operator guide (NFSv3)

## Overview

`local-transcribe` uses a file-based queue on shared **NFSv3** with one **NLM-locked** worker.

## Mount requirements

Preferred characteristics:

```text
vers=3,proto=tcp,hard,lock,local_lock=none,rw
```

**Rejected / unsafe:**

- `nolock`
- `local_lock=posix` or `local_lock=all`
- `soft` / `softerr`
- non-TCP transports (prefer `proto=tcp`)
- read-only mounts

Ensure `rpc.statd` / lockd are running for NLM recovery after client reboots. Open firewall ports for NFS, rpcbind, mountd, nlockmgr, and statd as needed.

## Configuration

`~/.config/local-transcribe/config.yaml`:

```yaml
queue:
  path: /opt/md2/music/youtube/transcripts/transcription-queue
  expected_uuid: <from lt queue init>
  expected_nfs_version: 3
  expected_server: nas.example.internal
  expected_export: /exports/transcripts
  default_auth_profile: yt
auth_profiles:
  yt:
    cookies_file: ~/.config/local-transcribe/youtube-cookies.txt
```

`default_auth_profile` is applied by the worker when a job has no per-execution `auth_profile`. Cookie *paths* stay local — never put cookie contents into queue JSON.

### Refresh cookies (Brave default)

```bash
lt cookies refresh                 # Brave (Flatpak profile auto-detected when present)
lt cookies refresh --browser chrome
lt cookies refresh --browser firefox
lt cookies refresh --help          # output path, keyring, profile overrides
```

Writes the Netscape cookies file used by the worker (default
`~/.config/local-transcribe/youtube-cookies.txt`) and ensures
`auth_profiles` / `default_auth_profile` point at it.

### yt-dlp binary (worker vs system)

Downloads use the **pipx/venv** `yt-dlp` next to the `lt` runtime (not
`/usr/bin/yt-dlp`). `lt update` refreshes that binary. `lt worker install`
sets systemd `PATH` so the service prefers the same bin directory.

```bash
lt update --dry-run                # prints Downloader yt-dlp binary: ...
lt doctor                          # shows module + binary paths/versions
```

## Bootstrap

```bash
lt queue init --queue-dir /path/to/transcription-queue
# writes ~/.config/local-transcribe/config.yaml (path + UUID + detected NFS identity)
# verify that file, then:
lt queue doctor
lt cookies refresh                 # export browser cookies for the worker
lt worker install   # also ensures CUDA torch when nvidia-smi is present
systemctl --user daemon-reload
systemctl --user enable --now local-transcribe-worker.service
```

Logs live under ``~/.local/state/local-transcribe/logs/`` (override with
``LOCAL_TRANSCRIBE_LOG_DIR``). The systemd worker also logs to the user journal.

## Retry failed jobs

```bash
lt queue list --status failed      # full execution_id column
lt queue retry                     # re-queue all failed (one job per unique source)
lt queue retry <execution_id>      # re-queue one source
lt queue retry --include-cancelled
lt queue retry --help
```

## Two-client NLM lock checklist (lab)

1. Client A: acquire exclusive lock on `worker/worker.lock` via `lt worker run` or Python `WorkerLock.acquire`.
2. Client B: second acquire must fail (`WorkerAlreadyActive`).
3. Kill A without unlock; B eventually acquires after NLM recovery.
4. Repeat after client reboot and server reboot; note grace periods.
5. Confirm firewalls do not block lock/statd traffic.

Automated + scripted lab:

```bash
export LT_NFS_QUEUE_DIR=/opt/md1/git/tmp/local-transcribe-queue-lab
pytest -q -m nfs
bash scripts/nfs_lab_twohost.sh            # nomnom + virindi + localai
bash scripts/nfs_lab_destructive_virindi.sh # RO remount + umount on side mount
```

Full checklist: [QUEUE_NFS_LAB.md](QUEUE_NFS_LAB.md).  
Evidence: [QUEUE_NFS_LAB_EVIDENCE.md](QUEUE_NFS_LAB_EVIDENCE.md).

## Legacy `transcript-pending.md` auto-import

The worker (NLM lock holder) watches the historical ref-cli pending file and
imports new URLs into the durable queue automatically. Ref does **not** need
`local-transcribe` installed — it can keep appending to:

`~/references/transcripts/transcript-pending.md`

Defaults (production worker):

```yaml
queue:
  watch_legacy_pending: true
  # optional override; default is ~/references/transcripts/transcript-pending.md
  # legacy_pending_file: /path/to/transcript-pending.md
```

Idle polls skip unchanged mtime/size. Imported YouTube URLs are removed from
the file; non-URL lines and `requires_force` rows are preserved. Concurrent
appends from ref during an import pass are kept.

Manual one-shot remains available: `lt queue import ~/references/transcripts/transcript-pending.md`.

## Upgrade / rollback

- **Upgrade:** configure queue, run worker (auto-imports pending file), use default `lt transcribe` / `lt batch` enqueue mode. Ref can keep writing `transcript-pending.md`.
- **Rollback:** `lt transcribe --direct` / `lt batch --direct` for emergency in-process behavior; queue files remain on disk.

## Security

- Queue dirs must not be world-writable.
- Never put cookie contents in job JSON; use auth profiles (local paths only).
- Temp media under `~/.cache/local-transcribe/jobs/<execution-id>/`.

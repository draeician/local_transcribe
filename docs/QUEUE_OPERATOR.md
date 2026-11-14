# Queue operator guide (NFSv3)

## Overview

`local-transcribe` uses a file-based queue on shared **NFSv3**. `queue.max_workers` (default 1) is how many workers may run jobs at once across every host. Each active worker holds one NLM slot lock, and each in-flight job holds its own NLM claim lock, so two workers never process the same job.

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
  # How many workers may process jobs at once, across all hosts.
  # Default 1. Set the same number on every host that runs a worker.
  max_workers: 1
  default_auth_profile: yt
worker:
  # How many worker processes this host may start. Default 1.
  concurrency: 1
  # Advisory lease refreshed while a job runs. A held NLM lock is not broken
  # when it expires; kill -9 and host reboot release the lock via NLM.
  lease_seconds: 90
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
lt worker install   # selects CPU/CUDA at runtime; installs no packages
systemctl --user daemon-reload
systemctl --user enable --now local-transcribe-worker.service
```

Logs live under ``~/.local/state/local-transcribe/logs/`` (override with
``LOCAL_TRANSCRIBE_LOG_DIR``). The systemd worker also logs to the user journal.

## Retry failed jobs

```bash
lt queue list failed               # status positional; --status failed also works
lt queue list --sort newest        # newest enqueue first
lt queue list <execution_id>       # one execution, full record via show
lt queue retry                     # re-queue all failed (one job per unique source)
lt queue retry <execution_id>      # re-queue one source
lt queue retry --include-cancelled
lt queue retry --help
```

## Several workers

`lt worker run --standby` (the systemd unit) takes one free slot under `worker/slots/` and holds a shared lock on `worker/worker.lock`. If every slot is taken, it waits. `worker.concurrency` (default 1) is an additional per-host cap, so aether and Nomnom can each run one worker when `queue.max_workers` is 2.

Claiming a job publishes `pending/` → `processing/` with `link()` and holds `worker/claims/<execution-id>.lock` until the job reaches a terminal state. The processing JSON records `worker_host` and `worker_pid`. Recovery will not move a job whose claim lock is still held. After the process dies, NLM releases that lock and the next worker requeues the job. Lock files are never deleted.

`lt queue stats`, `lt queue doctor`, and `lt status` print the cap and each active worker (host, pid, current job). A slot is active only while its lock is held; a leftover JSON file from a dead worker is not treated as a live worker.

YouTube downloads still share one admission budget. Concurrent workers serialize updates to `worker/rate-limit.json` with an NLM lock.

`lt queue repair` takes an exclusive lock on `worker/worker.lock`, so stop the workers first. A waiting `lt transcribe` does not start an extra worker while any 0.9 worker holds that shared lock; the standby workers on the worker hosts fill free slots.

## Upgrade from a single exclusive worker (0.8.x → 0.9)

A pre-0.9 worker holds `worker/worker.lock` **exclusively** for its whole life. A 0.9 worker needs that same file **shared**. Those two locks cannot be held together, so an old worker and a new worker cannot both process jobs. Do not rely on a rolling start.

On aether the user unit is `local-transcribe-worker.service` and its `ExecStart` is `lt worker run --standby`.

1. Stop the old worker and wait until it has exited:
   ```bash
   systemctl --user stop local-transcribe-worker.service
   lt worker status
   ```
   `Legacy worker.lock` must say `free`. If it still says `exclusive`, the old process is alive; do not start 0.9 yet.
2. Upgrade the `lt` install on aether, then on Nomnom (and any other host that will run a worker). Re-apply the CUDA or ROCm inject if `pipx install --force` replaced it.
3. Put the same `queue.max_workers` in `~/.config/local-transcribe/config.yaml` on each worker host. For one worker on aether and one on Nomnom, set `max_workers: 2` and leave `worker.concurrency` at `1`.
4. Start the unit on each worker host:
   ```bash
   systemctl --user daemon-reload
   systemctl --user start local-transcribe-worker.service
   lt queue stats
   ```
   `Active workers` should grow until it matches the cap. Further hosts stay in standby.

If a 0.9 `lt worker run` starts while the old exclusive lock is held, it exits. With `--standby` (the unit) it waits and does not claim jobs. If a pre-0.9 binary is started while a 0.9 worker holds the shared lock, the old process cannot acquire `worker.lock` and will not run jobs either. Stop the old process before treating the new workers as the ones draining the queue.

## Two-client NLM lock checklist (lab)

1. Client A: `lt worker run` holds a shared lock on `worker/worker.lock` plus one slot lock. An exclusive `WorkerLock.acquire` on `worker.lock` must fail (`WorkerAlreadyActive`) while A is running. A second 0.9 worker may share `worker.lock`, and is limited by `queue.max_workers` slot locks instead.
2. Client B: a second exclusive acquire of `worker.lock` must fail while any 0.9 or pre-0.9 worker holds it.
3. Kill A without unlock; B eventually acquires A's slot after NLM recovery, and A's in-flight job is requeued.
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

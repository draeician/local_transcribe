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
```

## Bootstrap

```bash
lt queue init --queue-dir /path/to/transcription-queue
# put path + uuid into config.yaml
lt queue doctor
lt worker install
systemctl --user daemon-reload
systemctl --user enable --now local-transcribe-worker.service
```

## Two-client NLM lock checklist (lab)

1. Client A: acquire exclusive lock on `worker/worker.lock` via `lt worker run` or Python `WorkerLock.acquire`.
2. Client B: second acquire must fail (`WorkerAlreadyActive`).
3. Kill A without unlock; B eventually acquires after NLM recovery.
4. Repeat after client reboot and server reboot; note grace periods.
5. Confirm firewalls do not block lock/statd traffic.

Mark automated tests with `@pytest.mark.nfs` when env is available:

```bash
pytest -q -m nfs
```

## Upgrade / rollback

- **Upgrade:** configure queue, `lt queue import` for `transcript-pending.md`, run worker, use default `lt transcribe` / `lt batch` enqueue mode.
- **Rollback:** `lt transcribe --direct` / `lt batch --direct` for emergency in-process behavior; queue files remain on disk.

## Security

- Queue dirs must not be world-writable.
- Never put cookie contents in job JSON; use auth profiles (local paths only).
- Temp media under `~/.cache/local-transcribe/jobs/<execution-id>/`.

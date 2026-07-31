# NFSv3 lab checklist (task 036)

Automated coverage runs when `LT_NFS_QUEUE_DIR` points at a directory on a
real NFSv3 mount (`local_lock=none`, `vers=3`, `hard`, `proto=tcp`).

```bash
LAB=/opt/md1/git/tmp/local-transcribe-queue-lab
mkdir -p "$LAB"
export LT_NFS_QUEUE_DIR="$LAB"
pytest -q -m nfs
lt queue doctor --queue-dir "$LAB/<subdir-from-init>"
```

Evidence from executed runs: [QUEUE_NFS_LAB_EVIDENCE.md](QUEUE_NFS_LAB_EVIDENCE.md).

## Automated (pytest `-m nfs`)

| Case | Test |
|------|------|
| Mount validation + statd | `test_mount_validation_real_nfs` |
| NLM contention (2 processes) | `test_nlm_contention_two_processes` |
| Lock after normal exit | `test_nlm_lock_after_normal_exit` |
| Lock after kill -9 | `test_nlm_lock_after_kill` |
| Cross-process duplicate enqueue | `test_cross_process_duplicate_enqueue` |
| pending→processing rename | `test_pending_to_processing_rename` |
| processing→completed rename | `test_processing_to_completed_rename` |
| Interrupted / generation-safe publish | `test_interrupted_transcript_publication` |
| Attribute-cache open-to-close wait | `test_attribute_cache_open_to_close` |

## Scripted multi-host

```bash
bash scripts/nfs_lab_twohost.sh            # nomnom + virindi + localai
bash scripts/nfs_lab_destructive_virindi.sh # RO + umount on side mount
```

Note: dual-process locks on one client with `local_lock=none` still use NLM
against the server. Prefer `nfs_lab_twohost.sh` for true multi-host proof.

## Operator — destructive / multi-host (manual)

Do **not** run these against a production queue. Use a dedicated lab path.

### Two-host NLM contention

1. Mount the same export on clients A and B (`vers=3`, `local_lock=none`).
2. A: `WorkerLock.acquire(lab/worker/worker.lock)` or `lt worker run --queue-dir …`
3. B: second acquire must raise `WorkerAlreadyActive`.
4. A: exit cleanly; B acquires.
5. Record hostnames, mount options, timestamps in the evidence file.

### Client reboot

1. A holds the lock; reboot A without unlock.
2. After A returns (and NSM recovery), B acquires within the observed window.
3. Note wait time.

### Server reboot / grace period

1. Hold or contend for the lock; restart NFS server / `lockd`.
2. Expect grace-period delays (`EAGAIN` / brief failures); then locks work again.
3. Record grace duration.

### Mount interrupt / restore

1. During a rename or publish, `umount`/`mount` the export (or block the server).
2. With hard mounts, clients block; after restore, no silent truncation.
3. Verify queue doctor and one enqueue after restore.

### Read-only remount

1. `mount -o remount,ro` the export (lab only).
2. Worker/doctor must fail closed; no partial authoritative writes.
3. Remount `rw` and confirm recovery.

### Firewall / statd / lockd

Confirm on server and both clients:

- `nfs` (2049), `rpcbind` (111), `mountd`, `nlockmgr`, `status` (statd)
- `rpc.statd` active on clients
- `rpcinfo -p <server>` shows `nlockmgr` + `status`

## Production-ready gate

Do **not** claim epic production readiness until:

1. `pytest -q -m nfs` is green on the real mount.
2. Two-host NLM is recorded (`nfs_lab_twohost.sh`).
3. Firewall/statd checklist is filled.
4. Optional but recommended: full client OS reboot + NAS restart grace timings appended to the evidence file.
5. Remaining epic checklist items in `QUEUE_IMPLEMENTATION_STATUS.md` are true.

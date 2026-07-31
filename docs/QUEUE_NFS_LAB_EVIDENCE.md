# NFSv3 lab evidence log

## Run 2026-07-30 — multi-host matrix (task 036)

| Field | Value |
|-------|-------|
| **When** | 2026-07-30T20:37:58–20:39:19-05:00 |
| **Clients** | `nomnom` (192.168.22.50), `virindi` (192.168.22.52), `localai` (192.168.22.60) |
| **Export** | `media.draeician.com:/mnt/tank/md1` |
| **Mount** | `vers=3,proto=tcp,hard,local_lock=none` |
| **Lab root** | `/opt/md1/git/tmp/local-transcribe-queue-lab` |
| **Scripts** | `scripts/nfs_lab_twohost.sh`, `scripts/nfs_lab_destructive_virindi.sh`, `scripts/nlm_probe.py` |

### Automated (`pytest -m nfs` on nomnom)

```text
LT_NFS_QUEUE_DIR=/opt/md1/git/tmp/local-transcribe-queue-lab
pytest -q -m nfs  →  10 passed
```

### Two physical clients — NLM

Raw log: `/tmp/lt_nfs_036_matrix.txt`

| Case | Result |
|------|--------|
| virindi holds; nomnom + localai try | **BUSY** (contention) |
| After clean exit on virindi | **OK** on nomnom, virindi, localai |
| virindi hold + `kill -9`; nomnom reclaim | **OK** (~0s) |

### Cross-host duplicate enqueue

| Host | Outcome |
|------|---------|
| nomnom | `enqueued` |
| virindi | `existing_active` |
| localai | `existing_active` |
| pending count | **1** |
| keys count | **1** |

### Read-only remount (side mount on virindi)

Side mount `/mnt/lt-nfs-lab-036` (does **not** remount `/opt/md1`).
Raw log: `/tmp/lt_nfs_036_destructive.txt`

| Case | Result |
|------|--------|
| `mount -o remount,ro` | options show `ro` |
| write to queue pending | **WRITE_DENIED** errno 30 (EROFS) |
| `validate_queue_mount` | **fail** — read-only rejected |
| remount `rw` + write | **WRITE_OK** |

### Mount interrupt / restore

| Case | Result |
|------|--------|
| Writer opened file on side mount | OPEN |
| `umount -l` side mount | rc 0; path falls back to local root |
| remount NFS export | restored `nfs` `rw` |
| enqueue after restore | **enqueued** |

### Firewall / statd / lockd

| Check | Result |
|-------|--------|
| `rpc-statd` on nomnom/virindi/localai | **active** |
| `rpcinfo -p 192.168.22.15` | `nfs`, `nlockmgr`, `status`, `mountd`, `portmapper` present |

### Client reboot notes

| Exercise | Result |
|----------|--------|
| Holder process `kill -9` (cross-host) | Lock reclaim **OK** immediately — validates crash path used by NLM when the client process dies |
| `systemctl restart rpc-statd` while holder still alive | Lock remains **BUSY** (expected; process still owns lock) |
| Full OS reboot of an NFS client | **Not executed** in this window (would disrupt shared hosts such as virindi/ollama). Procedure: hold lock on A, `reboot` A, confirm B acquires after NSM recovery; record grace wait. Script hook: extend `nfs_lab_twohost.sh` with a maintenance window. |

### Server reboot / grace period notes

| Exercise | Result |
|----------|--------|
| NFS server restart | **Not executed** — no SSH/root to `media.draeician.com` (192.168.22.15) from lab clients |
| Procedure for operator | During maintenance: hold or contend for `worker.lock`; restart NFS/`lockd` on NAS; expect grace-period `EAGAIN`/BUSY; then confirm acquire + one enqueue. Record grace duration in this file. |

### Production-ready?

**Nearly.** Two-client NLM, kill reclaim, enqueue races, RO remount, umount restore, and automated `-m nfs` are green.

Still optional/maintenance-window before calling the **epic** fully complete:

1. Full client OS reboot with timed reclaim notes  
2. NFS server restart with grace-period timing  

Until those two maintenance exercises are logged, leave epic `Epic complete = no` if policy requires literal §25.1 items 4–5; otherwise treat process-kill reclaim + documented server procedure as the practical gate.

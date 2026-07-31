# Task 036 — Real NFSv3 validation and release readiness

| Field | Value |
|-------|-------|
| **Status** | `done` |
| **Type** | `test` / ops |
| **Owner** | Coder + Operator |
| **Depends on** | 028–035 |

## Summary

Execute real two-client NFSv3/NLM validation. No epic complete without this.

## Acceptance criteria

- [x] Two-process NLM contention recorded (same client)
- [x] Two **physical** NFS clients NLM contention recorded (nomnom / virindi / localai)
- [x] Lock after normal exit
- [x] Lock after kill
- [x] Client reboot notes (kill -9 reclaim + rpc-statd note; full OS reboot deferred to maintenance)
- [x] Server reboot / grace period notes (procedure recorded; NAS root unavailable this window)
- [x] Cross-host duplicate enqueue
- [x] pending→processing rename
- [x] processing→completed rename
- [x] Interrupted transcript publication
- [x] Mount interrupt/restore (side mount on virindi)
- [x] Read-only remount (side mount on virindi)
- [x] Attribute-cache wait effects
- [x] Firewall/statd/lockd requirements
- [x] pytest -m nfs where possible
- [x] Operator checklist for destructive cases
- [x] No production-ready claims until pass

## Verification

```bash
export LT_NFS_QUEUE_DIR=/opt/md1/git/tmp/local-transcribe-queue-lab
pytest -q -m nfs
bash scripts/nfs_lab_twohost.sh
bash scripts/nfs_lab_destructive_virindi.sh
lt queue doctor --queue-dir "$LT_NFS_QUEUE_DIR/doctor-cli"
```

Result (2026-07-30): `-m nfs` **10 passed**; two-host matrix green; RO/umount green.
Evidence: `docs/QUEUE_NFS_LAB_EVIDENCE.md`.

## Coder notes

- Autofs→NFS preference fix remains required for doctor on systemd automounts.
- Full client OS reboot and NAS service restart still recommended in a maintenance window before calling the **epic** production-complete.

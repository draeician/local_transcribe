# Task 030 — Fail-closed NFSv3 startup

| Field | Value |
|-------|-------|
| **Status** | `done` |
| **Type** | `fix` / remediation |
| **Owner** | Coder |
| **Depends on** | 013, 017 |

## Summary

Production worker always validates NFS fail-closed. No casual CLI disable. systemd unit starts validated worker. No queue auto-init on worker start.

## Acceptance criteria

- [x] run_worker(validate_nfs=True) default
- [x] lt worker run validates NFS by default
- [x] No production CLI flag that casually disables validation (test-only internal OK)
- [x] systemd unit starts fail-closed worker
- [x] Worker does not initialize missing queue
- [x] Missing queue.id fatal
- [x] Wrong UUID/server/export/non-NFS/v4/nolock/local_lock/soft/RO fatal
- [x] require rpc.statd for production
- [x] Diagnostic uncertainty ≠ safe pass
- [x] Tests prove unit cannot start unvalidated worker

## Verification

```bash
pytest -q tests/test_mount_validation.py tests/test_cli_worker.py
```

Result: mount + cli_worker tests green; full suite **104 passed** (2026-07-30).

## Coder notes

- `run_worker` defaults `validate_nfs=True`; `require_statd` defaults to match; never calls `initialize_queue_layout`.
- CLI removed `--validate-nfs/--no-validate-nfs`; always passes `validate_nfs=True, require_statd=True`.
- `render_systemd_unit()` / install write `worker run --standby` with no disable flag.
- `require_statd=True` fails on missing **and** unknown rpc.statd.

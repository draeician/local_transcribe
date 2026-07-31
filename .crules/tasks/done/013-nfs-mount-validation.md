# Task 013 — NFSv3 mount validation

| Field | Value |
|-------|-------|
| **Status** | `done` |
| **Branch** | `feat/queue-creation` |
| **Type** | `feat` |
| **Owner** | Coder |
| **Phase** | 1–2 (foundation API; worker enforces) |
| **Depends on** | 011 |
| **Blocks** | 017, 022 (`queue doctor`) |

## Summary

Fail-closed checks that the queue path sits on a safe NFSv3 mount with remote locking usable for NLM. Pure diagnostics + validation API; worker will call before lock acquire.

## Spec sections

SPEC §10, §4.5, doctor commands.

## Files

| Action | Path |
|--------|------|
| Create | `src/local_transcribe/services/mount_validation.py` |
| Create | `tests/test_mount_validation.py` |

## Acceptance criteria

- [x] Unsafe options rejected with actionable messages
- [x] Wrong UUID path is separate (011); this focuses on mount
- [x] Local tmpdir “fake queue” can skip NFS validation via test flag / `validate_nfs=False` for unit tests only
- [x] Production worker path will use full validation (wired in 017/018)
- [x] Tests cover option rejection matrix

## Verification

```bash
pytest -q tests/test_mount_validation.py
```

Result: **19 passed** (2026-07-18).

## Coder notes

- API: `validate_queue_mount`, `ensure_queue_mount`, `probe_mount`, `MountInfo`, `MountValidationResult`
- Rejects: `nolock`, `soft`, `softerr`, `local_lock=posix|all|flock`, non-NFS, wrong vers, ro, bad proto
- Optional server/export match via `QueueConfig`
- `validate_nfs=False` for unit-test local fakes only
- `rpc.statd` best-effort via /proc; warning by default
- Raises existing `QueueMountError` from `queue_paths`

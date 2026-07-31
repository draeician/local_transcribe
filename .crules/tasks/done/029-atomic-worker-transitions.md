# Task 029 — Atomic worker transitions

| Field | Value |
|-------|-------|
| **Status** | `done` |
| **Type** | `fix` / remediation |
| **Owner** | Coder |
| **Depends on** | 028 |

## Summary

Replace all write-then-unlink state changes with atomic metadata helpers and same-filesystem rename. Add fault-injection tests.

## Acceptance criteria

- [x] recover_processing, promote_retries, claim_execution, complete_execution, fail_or_retry use atomic helpers + rename
- [x] Producer cancel uses safe transitions
- [x] No direct truncate of authoritative execution files
- [x] No copy+unlink as normal transition
- [x] fsync file + parent dir where supported
- [x] Destination collision fails safely
- [x] Fault-injection tests before/after each boundary

## Verification

```bash
pytest -q tests/test_worker_atomic_transitions.py
```

Result: **13 passed** in this file; related + full suite **93 passed** (2026-07-30).

## Coder notes

- Added `rename_exclusive` (hard `link()` + `unlink(src)`, fails on `EEXIST`) and `atomic_state_transition` (exclusive move then `atomic_write_json`) in `atomic_files.py`.
- Worker transitions and `QueueStore.cancel_pending` use `atomic_state_transition` only — no `write_text` + `unlink` / `os.replace` onto dest.
- Order: rename first (directory = state authority, collision leaves src untouched), then atomic JSON write at dest.

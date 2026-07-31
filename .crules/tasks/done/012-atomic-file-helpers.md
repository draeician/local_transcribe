# Task 012 — Atomic JSON + link() publication helpers

| Field | Value |
|-------|-------|
| **Status** | `done` |
| **Branch** | `feat/queue-creation` |
| **Type** | `feat` |
| **Owner** | Coder |
| **Phase** | 1 |
| **Depends on** | 010 |
| **Blocks** | 014, 015, 016, 021 |

## Summary

Implement NFS-safe atomic file primitives used by all queue authority writes. Do **not** change existing `safe_write_json()` behavior globally.

## Spec sections

SPEC §8.1–8.4, §24.1 (`atomic_files.py`).

## Implementation plan

1. Create `src/local_transcribe/services/atomic_files.py`:
   - `atomic_write_json(path, data)` — unique tmp under parent, write, flush, fsync, parse-validate, `os.replace`, fsync parent dir where supported, cleanup tmp
   - `link_publish(tmp_path, dest_path) -> bool` or raise on failure — `os.link`; handle `EEXIST`; caller unlinks tmp
   - helpers for unique tmp names (`hostname`, `pid`, random)
2. Document that create-if-absent uses `link()`, in-place replacement uses `atomic_write_json` / `os.replace`.
3. Unit tests: concurrent link publish (one winner), crash-safe leave no truncated final (simulate via mid-write tmp only).

## Files

| Action | Path |
|--------|------|
| Create | `src/local_transcribe/services/atomic_files.py` |
| Create | `tests/test_atomic_files.py` |

## Acceptance criteria

- [x] Final JSON never truncated on success path
- [x] `link_publish` fails closed when dest exists (`FileExistsError` / return existing)
- [x] Tmp files cleaned on success and failure paths
- [x] Does not modify or depend on changing `safe_write_json()` semantics for non-queue callers
- [x] Tests pass

## Verification

```bash
pytest -q tests/test_atomic_files.py
```

Result: **10 passed** (2026-07-18).

## Out of scope

Queue store, worker lock file content writes (lock fd writes are special-cased in 016).

## Coder notes

- Public API: `atomic_write_json`, `write_json_tmp`, `link_publish`, `link_publish_json`, `unique_tmp_path`, `fsync_directory`.
- `link_publish` returns `True`/`False` (created vs exists); does not unlink tmp (SPEC caller sequence).
- `link_publish_json` convenience cleans tmp always.
- `safe_write_json` left untouched in `utils/files.py`.

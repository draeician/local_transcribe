# Task 011 — Config load + explicit queue path / UUID

| Field | Value |
|-------|-------|
| **Status** | `done` |
| **Branch** | `feat/queue-creation` |
| **Type** | `feat` |
| **Owner** | Coder |
| **Phase** | 1 — Atomic foundation |
| **Depends on** | 010 |
| **Blocks** | 013, 014, 016, 022 |

## Summary

Add configuration loading and **explicit** queue path resolution. No multi-candidate search. Validate `queue.id` when present; support init writing UUID once.

## Spec sections

SPEC §4, §20, §22 (config keys for `queue.path`, `expected_uuid`, …).

## Implementation plan

1. Config module (e.g. `src/local_transcribe/services/config.py` or `utils/config.py`) loading `~/.config/local-transcribe/config.yaml` with safe defaults / clear error if missing required `queue.path` for queue operations.
2. `src/local_transcribe/services/queue_paths.py`:
   - `resolve_queue_dir(queue_dir: Path | None = None) -> Path`
   - precedence: CLI arg → config `queue.path` → **error** (no fall-through)
   - `verify_queue_identity(queue_dir, expected_uuid=...) -> str`
   - `initialize_queue_layout(queue_dir) -> str` (create dirs + `queue.id` once)
3. Directory layout constants: `keys`, `pending`, `processing`, `retry`, `completed`, `failed`, `cancelled`, `tmp`, `worker`.
4. Unit tests with tmp paths only (no NFS required).

## Files

| Action | Path |
|--------|------|
| Create | `src/local_transcribe/services/queue_paths.py` |
| Create | `src/local_transcribe/services/config.py` |
| Create | `tests/test_queue_paths.py` |
| Modify | `pyproject.toml`, `requirements.txt` — add `PyYAML>=6.0` |

## Acceptance criteria

- [x] Explicit path from CLI overrides config
- [x] Missing path raises clear `QueuePathError`
- [x] Init creates layout + immutable `queue.id` with UUID
- [x] Second init does not rewrite UUID
- [x] `expected_uuid` mismatch raises `QueueIdentityError`
- [x] Missing `queue.id` on resolve is an error
- [x] No candidate list / home-then-opt discovery exists
- [x] Tests cover the above

## Verification

```bash
pytest -q tests/test_queue_paths.py
```

Result: **11 passed** (2026-07-18).

## Out of scope

Mount option parsing (013), enqueue logic (014), CLI commands (022).

## Coder notes

- `load_config()` reads YAML; missing file → empty defaults.
- `resolve_queue_dir(queue_dir=..., config=...)` CLI arg wins over `queue.path`.
- `initialize_queue_layout` uses `O_CREAT|O_EXCL` for one-time `queue.id` write.
- `QueueMountError` reserved for task 013.

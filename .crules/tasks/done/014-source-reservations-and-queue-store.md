# Task 014 — Models, source reservations, queue store

| Field | Value |
|-------|-------|
| **Status** | `wip` |
| **Branch** | `feat/queue-creation` |
| **Type** | `feat` |
| **Owner** | Coder |
| **Phase** | 1 |
| **Depends on** | 011, 012 |
| **Blocks** | 018, 022, 025 |

## Summary

Define execution/reservation schemas and implement durable enqueue: permanent `keys/` reservation + `pending/<execution-id>.json` via atomic link publication. No worker execution yet.

## Spec sections

SPEC §5–7, §6.4–6.6, §8.1, modules `queue_models`, `source_reservations`, `queue_store`.

## Implementation plan

1. `queue_models.py` — dataclasses / TypedDicts for reservation + execution; schema_version=1; validation.
2. Source key normalization:
   - YouTube → `youtube:<video_id>` (reuse `utils/youtube.py`)
   - Local → `local:<hash>` from path+size+mtime
3. Filesystem-safe key filenames for `keys/`.
4. `source_reservations.py` — create-if-absent reservation; read current generation; force new generation API (does not execute).
5. `queue_store.py`:
   - enqueue YouTube / local
   - duplicate rules (active / completed transcript check hook / failed requires force)
   - list by state dir
   - cancel pending (rename) may wait for worker ownership for some ops — for producer cancel of pending only if SPEC allows without lock; otherwise document “pending cancel under producer, processing cancel needs worker”
6. Prefer pure library API; CLI later.

## Files

| Action | Path |
|--------|------|
| Create | `src/local_transcribe/services/queue_models.py` |
| Create | `src/local_transcribe/services/source_reservations.py` |
| Create | `src/local_transcribe/services/queue_store.py` |
| Create | `tests/test_queue_store.py`, `tests/test_source_reservations.py` |

## Acceptance criteria

- [ ] One reservation per source key under concurrent `link()` (one winner)
- [ ] Execution ids are UUIDs (UUIDv7 if available / uuid4 acceptable if documented)
- [ ] `--force` semantics as library API: new generation, same key
- [ ] Alternate YouTube URLs share source key
- [ ] Enqueue does not call yt-dlp or Whisper
- [ ] Valid existing transcript short-circuits to `already_completed` when checker supplied
- [ ] Tests pass on local filesystem

## Verification

```bash
pytest -q tests/test_queue_store.py tests/test_source_reservations.py
```

## Out of scope

Worker claim, rate limit, CLI, host affinity enforcement at claim time (018).

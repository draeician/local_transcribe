# Task 034 — Model cache and transcript publisher integration

| Field | Value |
|-------|-------|
| **Status** | `done` |
| **Type** | `refactor` / remediation |
| **Owner** | Coder |
| **Depends on** | 028, 015, 019 |

## Summary

Refactor transcriber.py fully; worker uses ModelCache; both paths use safe publication; remove dead code.

## Acceptance criteria

- [x] Model construction separate from transcription
- [x] Worker uses ModelCache
- [x] Same config reuses model; change reloads
- [x] CUDA fallback returns effective device/compute
- [x] Direct mode still works
- [x] No plain open(w) to final transcript on normal paths
- [x] Stale generation cannot replace newer
- [x] Dead duplicates removed

## Verification

```bash
pytest -q tests/test_model_cache.py tests/test_transcript_publish.py
```

Result: green; full suite **152 passed** (2026-07-30).

## Coder notes

- `transcribe_url` / `transcribe_local_file` publish via `publish_transcript` (`youtube:` / `file:` keys).
- `transcript_publish.py` rewritten: tmp+fsync+replace for archive + final; no dead `if False` path.
- `ProductionJobRunner` uses `default_model_loader` + `ModelCache`; returns effective device/compute after preflight.

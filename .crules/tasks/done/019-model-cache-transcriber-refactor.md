# Task 019 — Model cache + transcriber refactor

| Field | Value |
|-------|-------|
| **Status** | `wip` |
| **Branch** | `feat/queue-creation` |
| **Type** | `feat` / internal rework |
| **Owner** | Coder |
| **Phase** | 3 |
| **Depends on** | 015 |
| **Blocks** | 018 production path, 024 |

## Summary

Stop constructing `WhisperModel` per call. Provide worker-scoped cache keyed by `(model, effective_device, effective_compute_type)`. Wire atomic transcript publish. Keep existing public behavior for any remaining direct callers until 024.

## Spec sections

SPEC §20, §24.3.

## Implementation plan

1. `model_cache.py` — get/release/idle unload optional
2. Refactor `transcriber.py`:
   - separate load vs transcribe
   - accept model or cache
   - use transcript publisher for final JSON
   - do not use NFS output dir as media scratch
3. Preserve CUDA preflight / CPU fallback
4. Structured result: paths, effective device, duration
5. Tests with mocks if Whisper heavy; smoke optional

## Files

| Action | Path |
|--------|------|
| Create | `src/local_transcribe/services/model_cache.py` |
| Modify | `src/local_transcribe/services/transcriber.py` |
| Create | `tests/test_model_cache.py` |

## Acceptance criteria

- [ ] Same config reuses model instance in cache
- [ ] Config change reloads
- [ ] Transcript write path atomic + validated
- [ ] Existing tests still pass or updated intentionally
- [ ] Direct API still usable for `--direct` later

## Verification

```bash
pytest -q tests/test_model_cache.py
# existing suite if present
```

## Out of scope

Worker selection logic (018); CLI `--direct` flag (024).

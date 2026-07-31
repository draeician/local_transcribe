# Task 015 — Generation-aware transcript publisher

| Field | Value |
|-------|-------|
| **Status** | `wip` |
| **Branch** | `feat/queue-creation` |
| **Type** | `feat` |
| **Owner** | Coder |
| **Phase** | 1 |
| **Depends on** | 012 |
| **Blocks** | 019, 018 (completion path) |

## Summary

Atomic transcript write + validate + optional generation history so stale workers cannot overwrite newer completions.

## Spec sections

SPEC §11–12, §12.1.

## Implementation plan

1. Module e.g. `src/local_transcribe/services/transcript_publish.py`
2. Publish steps: unique tmp in transcript dir → write JSON → fsync → parse/validate (non-empty transcript, expected source id) → generation policy → `os.replace` → fsync dir → reopen validate
3. Generation strategy: `transcripts/.generations/<id>/NNNNNN.json` + current `VIDEO_ID.json`, **or** refuse replace unless generation ≥ current (document choice in code + tests)
4. Function signature should accept generation + source_key + payload
5. Unit tests with tmp transcript root

## Files

| Action | Path |
|--------|------|
| Create | `src/local_transcribe/services/transcript_publish.py` |
| Create | `tests/test_transcript_publish.py` |

## Acceptance criteria

- [ ] Incomplete write never becomes the published file
- [ ] Stale lower generation cannot replace higher generation valid transcript
- [ ] Validation failure leaves no “completed” side effect (publisher returns error only)
- [ ] Existing schema fields preserved
- [ ] Tests pass

## Verification

```bash
pytest -q tests/test_transcript_publish.py
```

## Out of scope

Wiring into `transcriber.py` (019); worker completion (018).

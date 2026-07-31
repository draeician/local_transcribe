# Task 018 — Worker execution loop, claim, recovery, fairness

| Field | Value |
|-------|-------|
| **Status** | `wip` |
| **Branch** | `feat/queue-creation` |
| **Type** | `feat` |
| **Owner** | Coder |
| **Phase** | 3 |
| **Depends on** | 014, 015, 016, 017 |
| **Blocks** | 023, 024 |
| **Integrates** | 019, 020, 021 (can stub then wire) |

## Summary

Implement the single-job worker loop: recover processing, promote retries, select with fairness, claim via rename, generation checks, invoke download/transcribe/publish hooks, terminal state transitions.

## Spec sections

SPEC §9.1–9.2, §9.8, §11, lifecycle §5 of review.

## Implementation plan

1. Expand `worker.py` process loop per SPEC.
2. Recovery of `processing/` on startup under NLM lock.
3. Selection: priority + age bonus **or** max interactive streak (default 5).
4. Host affinity / shared_roots for local files.
5. Claim: `pending → processing` rename.
6. Post-claim: reservation generation current; existing valid transcript short-circuit.
7. Call injection points:
   - admission (021) — stub sleep-none until ready
   - downloader (020)
   - model/transcribe (019)
   - transcript publish (015)
8. On failure: categorize → retry vs failed; set `available_at`.
9. Never interrupt in-flight job for priority.
10. Tests with fake download/transcribe callables on local FS.

## Files

| Action | Path |
|--------|------|
| Modify | `src/local_transcribe/services/worker.py` |
| Create | `tests/test_worker_loop.py` |

## Acceptance criteria

- [ ] One execution processed at a time
- [ ] Abandoned processing recovered deterministically
- [ ] Stale generation does not publish as completed over newer reservation
- [ ] Fairness prevents infinite interactive starvation (streak or age)
- [ ] Local-file wrong host skipped
- [ ] Unit tests use fakes; no real Whisper required

## Verification

```bash
pytest -q tests/test_worker_loop.py
```

## Out of scope

Real yt-dlp classification polish (020), full admission budgets (021), CLI (023/024).

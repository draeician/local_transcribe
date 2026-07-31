# Task 021 — Shared download admission controller

| Field | Value |
|-------|-------|
| **Status** | `wip` |
| **Branch** | `feat/queue-creation` |
| **Type** | `feat` |
| **Owner** | Coder |
| **Phase** | 4 |
| **Depends on** | 012, 016 |
| **Blocks** | 024 (full guarantee) |

## Summary

Replace advisory rate limiting for queue workers with hard admission: interval, hourly/daily budgets, global `blocked_until`, persist attempt **before** yt-dlp. Only called by NLM lock holder.

## Spec sections

SPEC §13, `download_admission.py`.

## Implementation plan

1. New module `download_admission.py` (do not silently reuse old RateLimiter API as authority).
2. State file: `<queue>/worker/rate-limit.json` via `atomic_write_json`.
3. API: `await_admission(lock_held=True) -> None` then `record_attempt()` before download — or single `admit_and_record()` that sleeps then persists.
4. 429 path: set blocked_until + exponential backoff helpers.
5. Local-file jobs never call admission.
6. Optionally deprecate/adapter-wrap old `rate_limiter.py` for non-queue paths until 024 removes dual use.
7. Unit tests with frozen time.

## Files

| Action | Path |
|--------|------|
| Create | `src/local_transcribe/services/download_admission.py` |
| Create | `tests/test_download_admission.py` |
| Note | `rate_limiter.py` — legacy until CLI cutover |

## Acceptance criteria

- [ ] Attempt count increments before download launch in the designed API sequence
- [ ] Interval and budgets delay as configured
- [ ] `blocked_until` prevents admission
- [ ] Atomic state writes only
- [ ] Tests pass

## Verification

```bash
pytest -q tests/test_download_admission.py
```

## Out of scope

Changing BatchPipeline to use this before 024 (optional cleanup).

# Task 032 — Queue-mode batch and authoritative status

| Field | Value |
|-------|-------|
| **Status** | `done` |
| **Type** | `feat` / remediation |
| **Owner** | Coder |
| **Depends on** | 028, 031 |

## Summary

Batch preserves full options; --wait; status/report from queue authority.

## Acceptance criteria

- [x] Batch enqueues with model/device/compute/language/max_attempts/auth/keep_audio/rate options
- [x] --wait for batch queue mode
- [x] lt status / lt report read queue + worker + validated transcripts
- [x] batch_status.json compatibility only
- [x] Tests for summaries, wait, regenerated compat

## Verification

```bash
pytest -q tests/test_cli_batch_queue.py
```

Result: **10 passed**; full suite **128 passed** (2026-07-30).

## Coder notes

- `services/queue_reporting.py`: summarize, failure report, compat `batch_status.json` regeneration.
- Batch queue mode stores full `ExecutionOptions` + `max_attempts`; `--wait` / `--timeout`.
- `lt status` / `lt report` prefer queue authority; legacy store only if queue unresolved.

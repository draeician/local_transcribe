# Task 031 — Queue wait and worker startup

| Field | Value |
|-------|-------|
| **Status** | `done` |
| **Type** | `feat` / remediation |
| **Owner** | Coder |
| **Depends on** | 028, 030 |

## Summary

Default `lt transcribe SOURCE` enqueues, starts worker when needed, waits for completion, returns transcript path.

## Acceptance criteria

- [x] Enqueue or attach to active execution
- [x] Start user systemd worker when appropriate
- [x] Poll specific execution paths across states
- [x] NFS attribute-cache aware polling
- [x] Return transcript path on success
- [x] Nonzero + useful error on fail/cancel
- [x] --timeout, --no-wait
- [x] Foreground queue-worker fallback if no service
- [x] Never silent return saying wait unimplemented
- [x] CLI tests for new/existing/completed/fail/cancel/timeout/no-wait

## Verification

```bash
pytest -q tests/test_cli_transcribe_queue.py
```

Result: **14 passed**; full suite **118 passed** (2026-07-30).

## Coder notes

- New `services/queue_wait.py`: `locate_execution`, `wait_for_execution`, systemd start + foreground fallback.
- `run_worker(..., until_execution_id=)` stops when target is terminal.
- CLI removed stub “wait not automated” message; `--timeout` added.

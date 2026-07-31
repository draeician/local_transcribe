# Task 035 — Producer concurrency and integration

| Field | Value |
|-------|-------|
| **Status** | `done` |
| **Type** | `fix` / remediation |
| **Owner** | Coder |
| **Depends on** | 014, 026 |

## Summary

Fix reservation races; multiprocessing tests; real ref-cli integration; legacy import atomic helpers.

## Acceptance criteria

- [x] Concurrent enqueue one active execution
- [x] Concurrent force no dual current generations
- [x] Repairable reservation/execution ordering
- [x] Failed partial enqueue recoverable
- [x] Pending cancel no contradictory state
- [x] Multiprocessing tests
- [x] ref-cli uses queue_api + pending fallback
- [x] Cross-project tests or documented fixture
- [x] Legacy import uses atomic helpers + fsync

## Verification

```bash
pytest -q tests/test_queue_concurrency.py
```

Result: green; full suite **162 passed** (2026-07-30).

## Coder notes

- `QueueStore.enqueue` is publish-pending-first then reservation claim; losers cancel orphans.
- CAS helpers: `try_advance_generation`, `try_update_current_execution`.
- `enqueue_youtube_safe` + `append_pending_url` in `queue_api.py`.
- `ref_cli.cli.add_url_to_pending_file` tries `enqueue_youtube`, falls back to pending file.
- Fixture docs: `docs/REF_CLI_QUEUE_ADAPTER.md`; in-repo tests cover safe API.
- `legacy_import` rewrites pending via tmp + fsync + replace.

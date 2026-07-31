# Task 035 — Producer concurrency and integration

| Field | Value |
|-------|-------|
| **Status** | `wip` |
| **Type** | `fix` / remediation |
| **Owner** | Coder |
| **Depends on** | 014, 026 |

## Summary

Fix reservation races; multiprocessing tests; real ref-cli integration; legacy import atomic helpers.

## Acceptance criteria

- [ ] Concurrent enqueue one active execution
- [ ] Concurrent force no dual current generations
- [ ] Repairable reservation/execution ordering
- [ ] Failed partial enqueue recoverable
- [ ] Pending cancel no contradictory state
- [ ] Multiprocessing tests
- [ ] ref-cli uses queue_api + pending fallback
- [ ] Cross-project tests or documented fixture
- [ ] Legacy import uses atomic helpers + fsync

## Verification

```bash
pytest -q tests/test_queue_concurrency.py
```

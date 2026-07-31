# Task 025 — Legacy pending import + compatibility artifacts

| Field | Value |
|-------|-------|
| **Status** | `wip` |
| **Branch** | `feat/queue-creation` |
| **Type** | `feat` |
| **Owner** | Coder |
| **Phase** | 6 |
| **Depends on** | 014, 022 |
| **Blocks** | 027 (partial) |

## Summary

Import `transcript-pending.md`, export pending reports, generate/repair `finished.dat` and `batch_status.json` from queue + validated transcripts. Legacy files are never authority.

## Spec sections

SPEC §18–19, §16.x, status_store deprecation as authority.

## Implementation plan

1. `lt queue import` full rules (backup, atomic rewrite, origin=import)
2. `lt queue export-pending` report only
3. Compatibility generators used by reconcile/status
4. Preserve verify-before-append for finished.dat
5. Document migration SOP snippet
6. Tests for idempotent import

## Files

| Action | Path |
|--------|------|
| Modify | queue store / cli queue |
| Modify | `reconcile.py` / `verify_status.py` / `status_store.py` as needed |
| Create | `tests/test_queue_legacy_import.py` |

## Acceptance criteria

- [ ] Import idempotent
- [ ] Unparseable lines preserved in pending file rewrite
- [ ] finished.dat URL without valid transcript ≠ complete
- [ ] batch_status.json regenerable from queue
- [ ] Tests pass

## Verification

```bash
pytest -q tests/test_queue_legacy_import.py
```

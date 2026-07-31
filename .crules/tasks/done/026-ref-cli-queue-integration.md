# Task 026 — `ref-cli` queue adapter + transcript reconcile

| Field | Value |
|-------|-------|
| **Status** | `wip` |
| **Branch** | `feat/queue-creation` (lt) + matching ref-cli branch |
| **Type** | `feat` (cross-repo) |
| **Owner** | Coder |
| **Phase** | 7 |
| **Depends on** | 022, 024 |
| **Blocks** | epic complete |

## Summary

`ref-cli` enqueues blocked YouTube transcripts into local-transcribe without waiting. Fallback to `transcript-pending.md` when queue unavailable. `ref reconcile-transcripts` updates only transcript fields in `references.md`. **local-transcribe never rewrites references.md.**

## Spec sections

SPEC §17.

## Implementation plan

1. Public stable enqueue API in local-transcribe (Python importable) **or** documented `lt queue add` contract
2. ref-cli adapter preference: import → CLI → pending file
3. Failure isolation: ref capture never fails solely because NFS/worker down
4. `ref reconcile-transcripts` dry-run default + `--apply`
5. Cross-project tests if monorepo layout allows; otherwise contract tests on both sides

## Files

| Action | Path |
|--------|------|
| Modify | local-transcribe public enqueue API surface |
| Modify | **ref-cli** repo (separate) |
| Create | tests on both sides as applicable |

## Acceptance criteria

- [ ] Same URL from ref + lt → one reservation
- [ ] ref works offline from queue
- [ ] reconcile only touches transcript fields
- [ ] No cookie data in queue JSON

## Verification

Per ref-cli and lt test suites; manual smoke on NFS lab if available.

## Notes

Coordinate versioning: document minimum `local-transcribe` version for adapter.

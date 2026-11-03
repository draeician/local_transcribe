# Task 037 — Queue list reporting

| Field | Value |
|-------|-------|
| **Status** | `review` |
| **Branch** | `feat/queue-list-reporting` |
| **Type** | `feat` |
| **Owner** | OpenCode |
| **Phase** | 5 (reporting) |
| **Depends on** | 022 |
| **Blocks** | — |

## Summary

Improve `lt queue list` so operators can inspect every execution state with
stable, filtered, sorted, paginated output.

## Required interface

```bash
lt queue list [STATUS|UUID] [--limit N] [--offset N] [--sort oldest|newest]
```

- No positional argument: all execution records.
- Defaults: `--limit 60`, `--offset 0`, `--sort oldest`.
- Statuses: `pending`, `processing`, `retry`, `completed`, `failed`, `cancelled`.
- A UUID positional selects exactly one execution.
- `lt queue show <UUID>` remains the detailed inspector.
- Existing `--status`, `--origin`, and `--limit` options remain available.

## Acceptance criteria

- [x] Shows execution ID, enqueue timestamp, age, status, source, and priority.
- [x] Shows total matching records and the range currently displayed.
- [x] Filters by status and origin, sorts by enqueue time, paginates after sorting.
- [x] Ties are broken deterministically by execution ID.
- [x] Pagination is stable for unchanged queue data.
- [x] Timestamps render in local time with an explicit timezone.
- [x] Rejects negative offsets, nonpositive limits, unknown statuses, invalid UUIDs,
      and invalid sort values with useful messages.
- [x] Empty results and offsets beyond the matching count are handled gracefully.
- [x] Operations remain read-only.
- [x] Regression tests cover defaults, every status filter, UUID selection, both
      sort orders, offset boundaries, pagination consistency, and errors.
- [x] UUID selection cannot be hidden by pagination; nonzero offsets are rejected.
- [x] `--origin` is applied to UUID selection, never silently ignored.

## Files

| Action | Path |
|--------|------|
| Modify | `src/local_transcribe/services/queue_store.py` |
| Create | `src/local_transcribe/services/queue_listing.py` |
| Modify | `src/local_transcribe/cli_queue.py` |
| Modify | `tests/test_cli_queue.py` |
| Modify | `README.md`, `QUICK_REFERENCE.md`, `docs/WHATS_NEW.md`, `AGENTS.md` |
| Modify | `HANDOFF.md` |

## Verification

```bash
pytest -q tests/test_cli_queue.py tests/test_queue_store.py
pytest -q
ruff check .
```

## Out of scope

Worker scheduling, queue state transitions, ROCm, transcription behavior,
destructive schema migrations, and merging/releasing.

## Coder Notes

- The queue is file-backed, so "database-level" filtering is implemented as
  state-directory selection in `QueueStore.list_executions` plus a read-only
  `QueueStore.query_executions` that sorts and paginates after the full
  matching set is known. Sorting cannot be pushed into the filesystem because
  execution IDs are random; the in-memory sort is the practical boundary.
- `created_at` is the original enqueue timestamp and is used for ordering and
  age. `available_at`, `started_at`, and `completed_at` are not substituted.
- A valid UUID with no matching record is treated as an empty list result
  (exit 0) rather than an error; malformed UUIDs are rejected (exit 1).
- `--status`, `--origin`, and `--limit` remain available. A positional status
  and `--status` may be combined only when they agree; a UUID cannot be
  combined with `--status`.
- Default behavior intentionally changes from pending-only/limit 50 to all
  states/limit 60, as required. Docs and `[Unreleased]` changelog were updated;
  the package version was not bumped because this task is not a release.
- Full pytest passes under `TERM=dumb` (Rich color disabled). With a color
  terminal, four pre-existing ANSI-sensitive tests and one pre-existing
  concurrency race fail; both were reproduced on `main` before this branch.
- `ruff check` on the changed files reports fewer findings than the same files
  on `main`; the new `queue_listing.py` is clean under the project config.

### Review corrections (2026-11-03)

- **UUID pagination:** a UUID selection with a nonzero `--offset` is rejected
  with an actionable error instead of bypassing or silently applying
  pagination. `--offset 0` remains valid, and status/all-record pagination is
  unchanged.
- **UUID + `--origin`:** the origin filter is now applied to the selected
  execution. A mismatch returns an explicit empty result
  (`No matching execution for UUID ... with origin ...`) and exit 0.
- Added regression tests for both findings, including a missing UUID with a
  nonzero offset and matching/mismatching UUID origins.
- Decision recorded in `docs/QUEUE_DESIGN_DECISIONS.md` §12.
- Read-only performance measured on a synthetic 6,430-record queue in
  `/tmp/opencode/lt-queue-list-perf`: CLI all/default median **1.269 s**,
  status/completed median **1.135 s**, UUID selection median **0.544 s**;
  direct store query median **0.614 s** (all) and **0.570 s** (completed).
  Production queue data was not read or modified.

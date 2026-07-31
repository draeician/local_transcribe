# Task 022 — CLI: `lt queue *`

| Field | Value |
|-------|-------|
| **Status** | `wip` |
| **Branch** | `feat/queue-creation` |
| **Type** | `feat` |
| **Owner** | Coder |
| **Phase** | 5 |
| **Depends on** | 013, 014 |
| **Blocks** | 024, 025, 026 |

## Summary

Expose queue management Typer subcommands without changing default `transcribe`/`batch` yet.

## Spec sections

SPEC §15.4.

## Implementation plan

1. Typer app group `queue` in `cli.py` (or `cli_queue.py` mounted).
2. Commands: `init`, `path`, `doctor`, `add`, `list`, `show`, `show-source`, `retry`, `cancel`, `import`, `export-pending`, `repair`, `stats`, `purge`
3. `repair` must acquire NLM lock before mutating
4. `purge` requires explicit filters; never deletes transcript JSON
5. `path` / `doctor` show mount + UUID + lock probe
6. Logging via `configure_logging`
7. Help text documents required config `queue.path`

## Files

| Action | Path |
|--------|------|
| Modify | `src/local_transcribe/cli.py` |
| Optional | `src/local_transcribe/cli_queue.py` |
| Create | `tests/test_cli_queue.py` |

## Acceptance criteria

- [ ] `lt queue init --queue-dir PATH` works
- [ ] `lt queue add` enqueues via store
- [ ] `lt queue list` filters by status
- [ ] `lt queue doctor` reports mount/lock issues without crashing
- [ ] Purge refuses without filters
- [ ] Tests use tmp queue

## Verification

```bash
pytest -q tests/test_cli_queue.py
lt queue --help
```

## Out of scope

Changing `lt transcribe` default (024); worker run (023).

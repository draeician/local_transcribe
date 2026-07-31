# Task 024 — CLI migrate: `transcribe` / `batch` / status / report

| Field | Value |
|-------|-------|
| **Status** | `wip` |
| **Branch** | `feat/queue-creation` |
| **Type** | `feat` (user-visible behavior change) |
| **Owner** | Coder |
| **Phase** | 5 |
| **Depends on** | 018, 021, 022, 023 |
| **Blocks** | 026, 027 |

## Summary

**Cutover:** default `lt transcribe` and `lt batch` become queue producers. Keep `--direct` for emergency. Status/report become queue-aware. This is the main behavior change for end users.

## Spec sections

SPEC §15.1–15.2, §15.5, acceptance #2, #24.

## Implementation plan

1. `lt transcribe <source>`:
   - enqueue interactive priority
   - start/wake worker if needed
   - wait unless `--no-wait`
   - options: `--force`, `--retry`, `--priority`, `--timeout`, `--queue-dir`, `--direct`
2. `--direct` preserves old in-process path with loud warning
3. `lt batch`: bulk enqueue + summary; optional `--wait`; `--resume` deprecation alias
4. Foreground fallback: if no worker, take NLM lock and process until requested job done (SPEC §16.2)
5. `lt status` / `lt report`: read queue authority; compat views optional
6. Update CHANGELOG / help strings
7. Integration tests with fake worker or in-process worker `--once`

## Files

| Action | Path |
|--------|------|
| Modify | `src/local_transcribe/cli.py` |
| Modify | possibly `pipeline.py` (delegate or thin wrapper) |
| Create | `tests/test_cli_transcribe_queue.py` |
| Modify | `CHANGELOG.md` when shipping |

## Acceptance criteria

- [ ] Default transcribe does not call download/transcribe without queue (unless `--direct`)
- [ ] Concurrent origins share same reservation for same video id
- [ ] Batch prints enqueue summary counts
- [ ] `--direct` still works for emergencies
- [ ] Status shows queue counts / lock info
- [ ] Tests cover enqueue-and-wait happy path with stubs

## Verification

```bash
pytest -q tests/test_cli_transcribe_queue.py
lt transcribe --help
lt batch --help
```

## Out of scope

ref-cli (026); multi-host NFS lab (027).

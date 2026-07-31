# Task 017 — Worker state, local lock, doctor hooks, standby shell

| Field | Value |
|-------|-------|
| **Status** | `wip` |
| **Branch** | `feat/queue-creation` |
| **Type** | `feat` |
| **Owner** | Coder |
| **Phase** | 2 |
| **Depends on** | 013, 016 |
| **Blocks** | 018, 023 |

## Summary

Diagnostic `worker/state.json`, optional local runtime lock, startup validation orchestration, and standby retry **without** running jobs yet (or with no-op loop). Prepares worker entrypoints for 018.

## Spec sections

SPEC §9.3–9.4, §9.6–9.7, §10, §15.3 doctor.

## Implementation plan

1. `worker_state.py` — atomic write of diagnostic state; mark inactive on clean exit
2. Local runtime lock under `$XDG_RUNTIME_DIR/local-transcribe/worker.lock` (fcntl/flock local only)
3. `worker.py` skeleton:
   - resolve queue → validate identity → mount validation → local lock → NLM lock
   - on `WorkerAlreadyActive`: exit or `--standby` sleep `standby_retry_seconds` and retry NLM only (never decide via state.json)
   - `--once` / run loop placeholders until 018 fills process_one
4. Library API for doctor: return structured lock/mount status
5. Tests with tmp queue on local FS (`validate_nfs=False` in tests)

## Files

| Action | Path |
|--------|------|
| Create | `src/local_transcribe/services/worker_state.py` |
| Create/extend | `src/local_transcribe/services/worker.py` |
| Create | `tests/test_worker_startup.py` |

## Acceptance criteria

- [ ] Standby does not treat `state.json` as ownership
- [ ] Local lock prevents two workers same machine without needing NFS
- [ ] NLM lock still required for ownership claim
- [ ] Clean exit unlocks and marks state inactive
- [ ] Tests pass

## Verification

```bash
pytest -q tests/test_worker_startup.py
```

## Out of scope

Job selection/claim/download (018); systemd unit (023).

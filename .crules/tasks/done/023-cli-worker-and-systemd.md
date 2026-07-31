# Task 023 — CLI: `lt worker *` + systemd user unit

| Field | Value |
|-------|-------|
| **Status** | `wip` |
| **Branch** | `feat/queue-creation` |
| **Type** | `feat` |
| **Owner** | Coder |
| **Phase** | 5 |
| **Depends on** | 017, 018 |
| **Blocks** | 024 |

## Summary

Worker lifecycle CLI and optional user systemd service install. Multi-host safe via `--standby`.

## Spec sections

SPEC §15.3, §16.

## Implementation plan

1. Commands: `run`, `run --once`, `run --standby`, `install`, `start`, `stop`, `restart`, `status`, `logs`, `doctor`
2. `status` distinguishes: lock local / held elsewhere / env unavailable / unsafe mount / state-only unknown
3. `install` writes `~/.config/systemd/user/local-transcribe-worker.service`, daemon-reload, print enable instructions; do not enable linger automatically
4. ExecStart uses resolved `lt` path + `worker run --standby`
5. Tests: unit file content; status parsing with mocks

## Files

| Action | Path |
|--------|------|
| Modify | `src/local_transcribe/cli.py` |
| Optional | `src/local_transcribe/cli_worker.py` |
| Create | `tests/test_cli_worker.py` |

## Acceptance criteria

- [ ] `lt worker run --once` processes ≤1 job when queue has work (integration optional)
- [ ] Status does not claim ownership from state.json alone
- [ ] Install generates valid unit file
- [ ] Help documents NFS/NLM requirements
- [ ] Tests pass

## Verification

```bash
pytest -q tests/test_cli_worker.py
lt worker --help
```

## Out of scope

Migrating default transcribe (024).

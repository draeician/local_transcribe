# Task 016 — NLM-backed POSIX worker lock

| Field | Value |
|-------|-------|
| **Status** | `wip` |
| **Branch** | `feat/queue-creation` |
| **Type** | `feat` |
| **Owner** | Coder |
| **Phase** | 2 |
| **Depends on** | 011, 012 |
| **Blocks** | 017, 018, 021 |

## Summary

Implement authoritative cross-host worker ownership via stable `worker/worker.lock` + `fcntl.lockf(LOCK_EX|LOCK_NB)`. No JSON leases, no rename-to-steal, no lock file replacement.

## Spec sections

SPEC §9.5 (normative `WorkerLock` shape).

## Implementation plan

1. `src/local_transcribe/services/worker_lock.py` per SPEC reference implementation
2. Rules:
   - open `a+`, lock, write diagnostic metadata in place on **same fd**, fsync
   - never rename/unlink lock while held
   - `WorkerAlreadyActive` on EACCES/EAGAIN
   - `release()` unlock + close
3. Context manager optional (`with WorkerLock.acquire(path) as lock:`)
4. Tests:
   - two processes: second fails while first holds (subprocess)
   - after first exits uncleanly, second can acquire (same host; NFS multi-host in 027)
5. Explicitly no `lease_renew_seconds` / steal APIs

## Files

| Action | Path |
|--------|------|
| Create | `src/local_transcribe/services/worker_lock.py` |
| Create | `tests/test_worker_lock.py` |

## Acceptance criteria

- [ ] Uses `fcntl.lockf`, not application timestamp ownership
- [ ] Second exclusive acquire fails while first holds
- [ ] Lock file path identity stable across hold
- [ ] Metadata in lock file is informational only (documented)
- [ ] Tests pass

## Verification

```bash
pytest -q tests/test_worker_lock.py
```

## Out of scope

Standby loop (017), processing recovery (018), live two-client NFS (027).

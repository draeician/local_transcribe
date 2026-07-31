# Task 027 — Multi-host NFSv3 hardening, security, upgrade docs

| Field | Value |
|-------|-------|
| **Status** | `wip` |
| **Branch** | `feat/queue-creation` |
| **Type** | `test` / `docs` / hardening |
| **Owner** | Coder + Operator |
| **Phase** | 8 |
| **Depends on** | 024+ (and 016–018 critical) |

## Summary

Prove the design on the real NFSv3 environment: two-client locks, reboot recovery, race tests, security review, upgrade/rollback documentation. Mark automated tests `@pytest.mark.nfs` when env vars present.

## Spec sections

SPEC §25.1–25.5, §26, §27 Phase 8.

## Implementation plan

1. Script or pytest module for two-client NLM test checklist
2. Document required mount: `vers=3,proto=tcp,hard`, no nolock/local_lock/soft
3. Firewall/rpc.statd checklist in `lt queue doctor` docs
4. Interruption playbook: kill during download/transcribe/publish
5. Security pass: world-writable queue dirs, cookie leakage, path escape
6. Upgrade notes: migrating from batch_status / pending.md; rollback to `--direct`
7. Update README / START_HERE / SOP only as needed for operator cutover

## Files

| Action | Path |
|--------|------|
| Create | `tests/nfs/` or `tests/test_nfs_integration.py` |
| Create | `docs/QUEUE_OPERATOR.md` (or extend existing ops docs) |
| Modify | doctor help text if gaps found |

## Acceptance criteria

- [ ] Two-client lock contention + release after crash documented **and** executed at least once on lab
- [ ] Server/client reboot recovery notes written from actual runs
- [ ] Unsafe mount rejection verified on lab or mocked + one real mount sample
- [ ] Security checklist signed off in task notes
- [ ] Upgrade/rollback doc exists
- [ ] Epic 010 global acceptance can be checked off

## Verification

```bash
# with NFS lab env:
pytest -q -m nfs
lt queue doctor
lt worker doctor
```

## Out of scope

New features beyond SPEC non-goals.

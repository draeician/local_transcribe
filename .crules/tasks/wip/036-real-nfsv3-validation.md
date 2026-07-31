# Task 036 — Real NFSv3 validation and release readiness

| Field | Value |
|-------|-------|
| **Status** | `wip` |
| **Type** | `test` / ops |
| **Owner** | Coder + Operator |
| **Depends on** | 028–035 |

## Summary

Execute real two-client NFSv3/NLM validation. No epic complete without this.

## Acceptance criteria

- [ ] Two-client NLM contention recorded
- [ ] Lock after normal exit
- [ ] Lock after kill
- [ ] Client reboot notes
- [ ] Server reboot / grace period notes
- [ ] Cross-host duplicate enqueue
- [ ] pending→processing rename
- [ ] processing→completed rename
- [ ] Interrupted transcript publication
- [ ] Mount interrupt/restore
- [ ] Read-only remount
- [ ] Attribute-cache wait effects
- [ ] Firewall/statd/lockd requirements
- [ ] pytest -m nfs where possible
- [ ] Operator checklist for destructive cases
- [ ] No production-ready claims until pass

## Verification

```bash
pytest -q -m nfs
lt queue doctor
lt worker doctor
```

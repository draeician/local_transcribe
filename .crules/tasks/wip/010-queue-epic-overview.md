# Task 010 — Epic: Background transcription queue (overview)

| Field | Value |
|-------|-------|
| **Status** | `wip` |
| **Branch** | `feat/queue-creation` |
| **Type** | `feat` (epic / tracking) |
| **Owner** | Manager |
| **Spec** | `SPEC-queue.md` v3 |

## Summary

Track the full implementation of the NFSv3 file-based transcription queue with one NLM-locked worker. This file is the epic index; **do not implement code under this task ID**. Execute child tasks 011–027.

## Architecture (one line)

Producers publish `keys/` + `pending/` via atomic `link()`; exactly one `fcntl` NLM lock holder on stable `worker/worker.lock` runs yt-dlp + Whisper and publishes validated transcripts.

## Child task order

```text
011 config/paths ─┬► 013 mount validation ─┐
012 atomic files ─┼► 014 reservations/store ┼► 018 worker loop ─┬► 023 worker CLI
                  └► 015 transcript pub ───► 019 model cache ──┤
020 downloader (parallel) ─────────────────────────────────────┤
016 NLM lock ─► 017 state/doctor ─────────────────────────────┤
021 admission (after 012+016) ────────────────────────────────┤
022 queue CLI (after 014) ────────────────────────────────────┴► 024 migrate CLI
                                                                      │
025 legacy ◄── 022                                    026 ref-cli ◄──┘
027 hardening (after CLI cutover + real NFS)
```

## Global acceptance (epic done when)

- [ ] All SPEC §26 acceptance criteria satisfied
- [ ] Tasks 011–027 in `done/` or `review/` with verified checklists
- [ ] No JSON lease / rename-to-steal / multi-path discovery in code
- [ ] Default `lt transcribe` uses queue; `--direct` opt-in only
- [ ] `pytest` green; two-client NLM tests documented or automated on NFS lab

## Coder constraints (all child tasks)

1. Do **not** use `safe_write_json()` for queue authority — use `atomic_files` helpers.
2. Do **not** rename or replace `worker.lock` while locked.
3. Do **not** change default CLI behavior before task 024.
4. Type hints + pathlib; new deps only if justified in task (prefer stdlib for locks/files).
5. Tests required per task acceptance; NFS multi-host tests may be marked `@pytest.mark.nfs` and skip without env.

## References

- `SPEC-queue.md`
- `docs/QUEUE_DESIGN_DECISIONS.md`
- `project_spec.md`

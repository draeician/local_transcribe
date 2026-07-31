# Task 020 — Downloader hardening for daemon use

| Field | Value |
|-------|-------|
| **Status** | `wip` |
| **Branch** | `feat/queue-creation` |
| **Type** | `feat` / `fix` |
| **Owner** | Coder |
| **Phase** | 3 |
| **Depends on** | 010 (parallelizable) |
| **Blocks** | 018 production YouTube path |
| **Related** | Task 001 deferred classifier |

## Summary

Make yt-dlp invocation safe for a long-lived worker: per-execution scratch dir, process group, timeout, structured errors, no broad `--ignore-errors`, stream logs, no shared output-dir assumption.

## Spec sections

SPEC §14, §24.4; absorbs deferred 001 classifier goals.

## Implementation plan

1. Accept `scratch_dir: Path` (execution-local under XDG cache).
2. Launch yt-dlp in new process group; kill group on timeout/cancel.
3. Stream stderr/stdout with size caps; do not dump huge `--print-json` into exceptions.
4. Structured result + error categories (SPEC §7.2 / §14).
5. Remove or tightly gate `--ignore-errors`.
6. Classifier messages may still mention `lt update` for stale yt-dlp.
7. Tests with fake subprocess runner.

## Files

| Action | Path |
|--------|------|
| Modify | `src/local_transcribe/services/downloader.py` |
| Create | `tests/test_downloader_errors.py` (and/or harden existing) |

## Acceptance criteria

- [ ] Download media never requires final transcript NFS dir as scratch
- [ ] Timeout kills process group
- [ ] Error categories distinguish 429, auth, unavailable, extractor, network, etc.
- [ ] Exception text not flooded with JSON stdout
- [ ] Tests pass

## Verification

```bash
pytest -q tests/test_downloader_errors.py
```

## Out of scope

Admission controller (021); worker loop wiring details (018).

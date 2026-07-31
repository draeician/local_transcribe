# Project Specification — local-transcribe

**Package:** `local-transcribe` (`lt` CLI)  
**Version:** 0.5.0  
**Status:** Active development  
**Primary language:** Python 3.10+  
**Install:** pipx / editable venv  

## Purpose

Local speech-to-text CLI using faster-whisper, with YouTube download via yt-dlp and batch/resume tooling. Target: Linux (including multi-host NFSv3 transcript storage).

## Current capabilities (main)

- `lt transcribe` — single URL or local file (direct execution today)
- `lt batch` — URL list with resume via `batch_status.json`
- `lt reconcile` / `lt verify` / `lt status` / `lt report`
- `lt update` — refresh yt-dlp in the runtime
- `lt doctor` — environment checks

## Active epic: Background transcription queue (SPEC v3)

**Spec:** [`SPEC-queue.md`](SPEC-queue.md) (revision 3)  
**Decisions:** [`docs/QUEUE_DESIGN_DECISIONS.md`](docs/QUEUE_DESIGN_DECISIONS.md)  
**Branch:** `feat/queue-creation`  
**Type:** Feature + execution-model rework (not a pure refactor)

### Goal

Producers on any host publish durable source reservations and executions on NFSv3. Exactly one NLM lock holder downloads and transcribes, one job at a time, with shared download admission and idempotent recovery.

### Roadmap (task files in `.crules/tasks/wip/`)

| ID | Task | Phase | Depends on |
|----|------|-------|------------|
| 010 | Epic overview & definition of done | — | SPEC v3 |
| 011 | Config load + explicit queue path / UUID | 1 | 010 |
| 012 | Atomic JSON + link() publication helpers | 1 | 010 |
| 013 | NFSv3 mount validation | 1 | 011 |
| 014 | Models, source reservations, queue store | 1 | 011, 012 |
| 015 | Generation-aware transcript publisher | 1 | 012 |
| 016 | NLM POSIX worker lock | 2 | 011, 012 |
| 017 | Worker state, local lock, doctor, standby shell | 2 | 013, 016 |
| 018 | Worker execution loop, claim, recovery, fairness | 3 | 014, 016, 017 |
| 019 | Model cache + transcriber refactor | 3 | 015 |
| 020 | Downloader hardening for daemon use | 3 | — (can parallel after 010) |
| 021 | Download admission controller | 4 | 012, 016 |
| 022 | CLI: `lt queue *` | 5 | 014, 013 |
| 023 | CLI: `lt worker *` + systemd install | 5 | 017, 018 |
| 024 | CLI: migrate `transcribe` / `batch` / status / report | 5 | 018, 021, 022, 023 |
| 025 | Legacy pending import + compatibility artifacts | 6 | 014, 022 |
| 026 | `ref-cli` adapter + reconcile (cross-repo) | 7 | 022, 024 |
| 027 | Multi-host NFS hardening, security, upgrade docs | 8 | 024+ |

### Non-goals (v1 queue)

See SPEC §28: no multi-active workers, no SQLite authority, no JSON leases, no multi-path auto-discovery, no parallel downloads, no web UI.

### Versioning policy for this epic

- Implementation tasks: `feat` → minor bumps when user-facing queue ships (or staged minors per phase if released incrementally).
- Internal foundation-only merges before CLI cutover may ship as minor once queue commands exist; docs-only → patch.
- Default `lt transcribe` behavior change is a **minor** with clear notes; treat as intentional UX change (queue wait), not silent major.

## Related open work

| ID | Location | Notes |
|----|----------|-------|
| 001 | `.crules/tasks/review/` | yt-dlp update done; downloader classifier deferred — partially overlaps task 020 |

## Verification baseline

```bash
python3 -m local_transcribe --version   # or: lt version
pytest -q
lt doctor
```

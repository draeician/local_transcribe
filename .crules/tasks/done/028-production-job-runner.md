# Task 028 — Production job runner

| Field | Value |
|-------|-------|
| **Status** | `done` |
| **Type** | `feat` / remediation |
| **Owner** | Coder |
| **Depends on** | 014–027 foundation (untrusted until verified) |
| **Blocks** | 031, 033, 034 (integration) |

## Summary

Implement the real worker job runner and wire it into `lt worker run`. Never pass `job_runner=None` in normal operation.

## Acceptance criteria

- [x] Execution options resolved from job
- [x] Auth profiles resolved locally (cookies never on NFS)
- [x] Execution-specific local scratch directory
- [x] Hardened downloader for YouTube jobs
- [x] Safe local-file open
- [x] Worker-scoped ModelCache
- [x] Transcribe via refactored API (load vs run)
- [x] Existing transcript JSON schema
- [x] Generation-aware transcript publisher
- [x] Structured result with final output path
- [x] Clean temp media unless keep_audio
- [x] Never write downloaded media to NFS transcript root by default
- [x] `lt worker run` wires production job_runner
- [x] E2E test: pending → processing → completed with mocked DL + Whisper
- [x] Valid transcript atomically published

## Verification

```bash
pytest -q tests/test_worker_job_runner.py
pytest -q tests/test_worker_loop.py
```


## Coder notes (2026-07-18)

- Added `services/job_runner.py` (`ProductionJobRunner`, auth profile resolution, local scratch).
- `transcriber.py`: `load_whisper_model`, `transcribe_with_model`, `resolve_device_and_compute`.
- `run_worker` defaults to `create_default_job_runner()` when job_runner omitted.
- `lt worker run` wires production runner (never `None`).
- Tests: `tests/test_worker_job_runner.py` (E2E pending→completed with mocks).

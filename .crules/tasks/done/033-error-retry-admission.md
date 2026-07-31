# Task 033 — Error classification, retry, and admission integration

| Field | Value |
|-------|-------|
| **Status** | `done` |
| **Type** | `fix` / remediation |
| **Owner** | Coder |
| **Depends on** | 028, 021 |

## Summary

Wire structured downloader/transcription errors into worker retries and admission; NLM lock proof not boolean.

## Acceptance criteria

- [x] Structured categories preserved (not all internal_error)
- [x] 429 updates admission blocked_until + execution available_at
- [x] Temporary 403 retry backoff
- [x] Auth/private/unavailable terminal categories
- [x] Retryable failures set future available_at
- [x] DownloadAdmission requires NLM lock object proof
- [x] Attempt persisted before yt-dlp
- [x] Local-file no download budget
- [x] Retry promotion timestamp-robust
- [x] Tests every category

## Verification

```bash
pytest -q tests/test_worker_error_handling.py tests/test_download_admission.py
```

Result: green; full suite **147 passed** (2026-07-30).

## Coder notes

- `worker_errors.py`: classify exceptions → ExecutionError; retry delay ladder; ISO `available_at` helpers.
- `DownloadAdmission.admit_and_record/record_429/record_403` require held `WorkerLock` (no boolean).
- Worker: YouTube admit before runner; 429/403 update admission + execution `available_at`; local skips admit.
- `PrivateVideoError` separate from temporary `ForbiddenError`; `promote_retries` parses timestamps.

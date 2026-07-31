# Queue Implementation Status

**Epic:** Background transcription queue (SPEC-queue.md v3)  
**Branch:** `feat/queue-creation`  
**Last updated:** 2026-07-30  
**Updated by:** Cursor Grok 4.5 (task 034)  

---

## Agent control block (read/write every session)

| Field | Value |
|-------|--------|
| **Epic status** | `in_progress` |
| **Current task** | `035` |
| **Current task file** | `.crules/tasks/wip/035-producer-concurrency-and-refcli.md` |
| **Next task** | `036` |
| **Next task file** | `.crules/tasks/wip/036-real-nfsv3-validation.md` |
| **Session instruction** | Implement **only** the current remediation task. Prior false epic-complete claims remain invalid. Do not set Epic complete until 028–036 + real NFS lab pass. |
| **Epic complete** | `no` |
| **Stop condition** | Epic complete only after tasks 028–036 all `done`, real two-client NFS evidence recorded, no critical deferred follow-ups, full suite green, and status matches code. |

---

## Status correction (2026-07-18)

Prior agent incorrectly marked the epic complete after unit tests alone while critical paths were unfinished. History retained; epic reopened with remediation 028–036.

---

## Foundation tasks 011–027 (scaffolding — `done_unverified`)

Retained under `.crules/tasks/done/` for history. Production readiness is gated by 028–036.

---

## Remediation sequence (authoritative backlog)

| ID | Title | Status | Notes |
|----|-------|--------|-------|
| 028 | Production job runner | `done` | `job_runner.py` + worker/CLI wire; E2E mock test |
| 029 | Atomic worker transitions | `done` | `rename_exclusive` + `atomic_state_transition`; fault-injection tests |
| 030 | Fail-closed NFSv3 startup | `done` | validate_nfs default True; no CLI disable; no auto-init; require_statd |
| 031 | Queue wait + worker startup | `done` | `queue_wait.py`; CLI enqueue-and-wait; systemd + foreground fallback |
| 032 | Queue batch + status/report | `done` | full batch options + `--wait`; queue-authoritative status/report + compat |
| 033 | Error/retry/admission integration | `done` | structured categories; lock-object admission; available_at backoff |
| 034 | Model cache + publisher integration | `done` | direct+worker use ModelCache/publish; no in-place transcript truncate |
| **035** | Producer concurrency + ref-cli | `ready` | **← current** |
| 036 | Real NFSv3 validation + release | `pending` | blocks epic complete |

---

## Completed work log

| When | Task | Summary | Verification |
|------|------|---------|--------------|
| 2026-07-18 | 011–027 | Scaffolding (prior agent) | unit tests only — not production complete |
| 2026-07-18 | status | Epic reopened; remediation 028–036 created | — |
| 2026-07-18 | 028 | Production job runner + CLI wire + E2E mock | `pytest -q tests/test_worker_job_runner.py tests/test_worker_loop.py` → 7 passed; full suite **80 passed** |
| 2026-07-30 | 029 | Atomic worker transitions + cancel + fault injection | `pytest -q tests/test_worker_atomic_transitions.py` → 13 passed; full suite **93 passed** |
| 2026-07-30 | 030 | Fail-closed NFS worker startup | `pytest -q tests/test_mount_validation.py tests/test_cli_worker.py`; full suite **104 passed** |
| 2026-07-30 | 031 | Transcribe enqueue-and-wait + worker ensure | `pytest -q tests/test_cli_transcribe_queue.py` → 14 passed; full suite **118 passed** |
| 2026-07-30 | 032 | Batch options/wait + queue status/report | `pytest -q tests/test_cli_batch_queue.py` → 10 passed; full suite **128 passed** |
| 2026-07-30 | 033 | Error/retry/admission integration | `pytest -q tests/test_worker_error_handling.py tests/test_download_admission.py`; full suite **147 passed** |
| 2026-07-30 | 034 | Model cache + transcript publisher integration | `pytest -q tests/test_model_cache.py tests/test_transcript_publish.py`; full suite **152 passed** |

---

## Blockers

| Since | Task | Blocker | Unblock by |
|-------|------|---------|------------|
| — | 036 | Real two-host NFSv3 lab | Operator environment |

---

## Session notes (append only)

- 2026-07-18: Prior agent marked epic complete (INVALID).
- 2026-07-18: Senior engineer reopened epic; created remediation tasks 028–036.
- 2026-07-18: Task 028 done — `ProductionJobRunner`, default runner in `run_worker`/`lt worker run`, E2E pending→completed with mocks. Advanced current → 029.
- 2026-07-30: Task 029 done — `rename_exclusive` / `atomic_state_transition`; worker + `cancel_pending` use them; fault-injection tests. Advanced current → 030.
- 2026-07-30: Task 030 done — fail-closed NFS default; removed CLI disable; no worker auto-init; require_statd fails on unknown; systemd unit tested. Advanced current → 031.
- 2026-07-30: Task 031 done — `queue_wait.py` path polling, systemd start, foreground fallback; CLI `--timeout` / real wait; stub message removed. Advanced current → 032.
- 2026-07-30: Task 032 done — batch full options + `--wait`; `queue_reporting.py`; status/report queue-authoritative with compat `batch_status.json`. Advanced current → 033.
- 2026-07-30: Task 033 done — `worker_errors.py`; lock-object admission; 429/403 → blocked_until + available_at; timestamp-robust promote; category tests. Advanced current → 034.
- 2026-07-30: Task 034 done — direct mode uses `publish_transcript`; publisher atomic rewrite; worker ModelCache + effective device; dead publish code removed. Advanced current → 035.

---

## Final completion checklist (epic) — DO NOT CHECK UNTIL TRUE

- [ ] Tasks 028–036 all `done` with acceptance checkboxes checked
- [ ] Installed systemd worker processes a real queued job end-to-end
- [ ] Default `lt transcribe` waits and returns completed transcript path
- [ ] Default `lt batch` preserves options and supports `--wait`
- [ ] Queue-aware status and reporting work
- [ ] ref-cli integration exists in the other repository
- [ ] Real two-client NFSv3/NLM tests executed and recorded (036)
- [ ] Full test suite green including required gates
- [ ] No critical deferred follow-ups
- [ ] Status file accurately matches code
- [ ] **Epic complete** only then set to `yes`

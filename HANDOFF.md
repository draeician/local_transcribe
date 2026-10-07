# HANDOFF — local_transcribe

This file is the mandatory coordination channel between the ChatGPT project coordinator and coding agents (OpenCode or pi).

## Mandatory agent protocol

All source-code changes must be performed by an OpenCode or pi coding agent. The ChatGPT coordinator does not implement code changes directly.

Every coding-agent prompt issued for this repository MUST explicitly tell the agent to:

1. Read `HANDOFF.md` before doing any work.
2. Treat the current state and constraints in `HANDOFF.md` as required project context, alongside `AGENTS.md`, `project_spec.md`, and any task/spec files relevant to the work.
3. Before finishing, update `HANDOFF.md` with:
   - what was investigated or changed;
   - files changed;
   - tests/verification run and results;
   - unresolved issues or blockers;
   - recommended next step.
4. Preserve useful prior handoff history. Do not erase earlier findings just to write the current session.
5. Do not claim completion if verification has not passed.

The coordinator will use this file to pass state between separate OpenCode/pi sessions.

## Coding-agent prompt standard

Prompts for OpenCode/pi must be operational instructions, not vague requests. The prompt must tell the agent both **what to accomplish** and **how to execute the work**.

Unless a task genuinely requires something different, every coding-agent prompt should include:

1. **Branch**
   - State the exact branch to work on.
   - Pull the latest matching remote branch before implementation.
   - Never push directly to `main` unless the prompt explicitly authorizes it.

2. **First local repository action / WIP marker**
   - Tell the agent to create a repository-root, local-only WIP marker immediately.
   - Include task id, branch, and UTC start time.
   - The marker must never be staged, committed, or pushed.
   - Keep it present while work is incomplete or blocked.
   - Recreate it after context compaction/restart if missing.
   - Remove it only after all completion criteria and remote verification are green.

3. **Canonical context**
   - Read `AGENTS.md`, `project_spec.md`, `HANDOFF.md`, and any task/spec/status file named in the prompt before editing code.
   - Re-read those files after context compaction/restart before continuing.
   - Inspect repository and git state before resuming.

4. **Exact scope**
   - Name the exact bug/task to close.
   - Explicitly state what must not be rebuilt, broadened, or changed.
   - Identify authoritative files/specs when applicable.
   - Do not ask the user whether to continue when the prompt already defines the work.

5. **Execution instructions**
   - Give concrete implementation expectations and required behavior.
   - Require use of the real application lifecycle when validating behavior; do not fabricate state to bypass the path under test.
   - Call out important invariants, compatibility requirements, and forbidden shortcuts.

6. **Verification**
   - List the exact targeted tests/checks the agent must add or run.
   - Include regression tests for the bug.
   - Include broader project checks appropriate to the scope.
   - Require truthful reporting of commands and pass/fail results.
   - Do not claim completion with skipped required verification.

7. **Handoff update**
   - Before finishing, update `HANDOFF.md` with the implementation, files changed, verification evidence, blockers, and next step.
   - Preserve earlier useful handoff history.

8. **Commit and push**
   - State whether completed work must be committed and pushed.
   - When required, use conventional commits.
   - Push every completed commit to the named branch.
   - Verify the remote branch contains the final code/tests/docs/handoff.

9. **Definition of done**
   - End the prompt with explicit hard completion criteria.
   - The task is not complete until every required behavior/test is green, the remote branch is verified when pushing is required, and the local WIP marker is removed only after that verification.
   - Verify the WIP marker was never tracked, staged, committed, or pushed.

Future prompts should be detailed enough that an agent can execute the full task without needing the coordinator to fill in obvious operational steps mid-session.

## Repository snapshot

- Repository: `draeician/local_transcribe`
- Primary language: Python
- CLI: `lt` (Typer)
- Runtime model: `faster-whisper`
- Current architecture: queue-first NFSv3 workflow with one NLM-locked worker.
- Main worker path: `src/local_transcribe/services/worker.py`
- Production job runner: `src/local_transcribe/services/job_runner.py`
- Whisper cache: `src/local_transcribe/services/model_cache.py`
- Queue specification: `SPEC-queue.md`
- Agent/project guidance: `AGENTS.md`, `project_spec.md`

## Current investigation — idle Whisper model unload

Date: 2026-10-07

The repository contains an idle-unload mechanism, but the current implementation does not appear to schedule or invoke it.

Observed on both `main` and `feat/queue-creation`:

- `src/local_transcribe/services/model_cache.py`
  - `ModelCache(..., idle_unload_seconds=1800.0)` defaults to 1800 seconds (30 minutes).
  - `maybe_unload_idle()` drops the cached model when `last_used` exceeds that threshold.
- Repository-wide search found no caller of `maybe_unload_idle()`.
- `SPEC-queue.md` specifies `model_idle_unload_seconds: 1800`.
- `tests/test_model_cache.py` verifies cache reuse, reload-on-config-change, CUDA fallback, and worker job-runner reuse, but contains no idle-unload integration test.
- No 300-second / five-minute model idle-unload implementation or clearly documented five-minute bug-fix was found on `main` or `feat/queue-creation`.

Working conclusion: the cache has an idle-unload primitive, but the worker currently appears not to call it, so idle unloading is effectively not active. If the intended behavior is to unload Whisper after five idle minutes, an agent should first verify the desired 300-second policy and then implement/test the wiring rather than merely changing the existing 1800-second default.

## Standing constraint

Do not make source-code changes from the coordinator session. For any implementation, debugging fix, refactor, or test change, prepare an OpenCode/pi prompt that includes the mandatory HANDOFF protocol and coding-agent prompt standard above.

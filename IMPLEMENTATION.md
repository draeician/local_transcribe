# Implementing the transcription queue with an agent

This guide tells **you** (the human operator) how to drive any coding agent (Grok, Claude Code, Cursor, Codex, etc.) through the full queue backlog **one task at a time**, using a **single evolving prompt** and a **shared status file**.

You do **not** rewrite the prompt for each task. You paste the same prompt every session. The agent reads the status file, implements the current task, updates status, and tells you what happened. When the epic is finished, the agent says so explicitly and stops.

---

## What you already have

| Artifact | Role |
|----------|------|
| [`SPEC-queue.md`](SPEC-queue.md) | Full design (v3) — source of truth for behavior |
| [`docs/QUEUE_DESIGN_DECISIONS.md`](docs/QUEUE_DESIGN_DECISIONS.md) | Why decisions were made |
| [`project_spec.md`](project_spec.md) | Project + epic roadmap |
| [`.crules/tasks/wip/010`–`027`](.crules/tasks/wip/) | One markdown task file per unit of work |
| [`QUEUE_IMPLEMENTATION_STATUS.md`](QUEUE_IMPLEMENTATION_STATUS.md) | **Living status** — current task, next task, log |
| This file | Operator + agent protocol |

**Branch:** work should stay on `feat/queue-creation` (or a child branch you create from it).

---

## Mental model

```text
┌─────────────────────────────────────────────────────────────┐
│  YOU                                                        │
│  1. Open a new agent session in this repo                   │
│  2. Paste THE PROMPT (below) unchanged                      │
│  3. Review the agent's report / diff                        │
│  4. If happy: commit (optional) and start another session   │
│     with the SAME prompt until the agent says EPIC COMPLETE │
└───────────────────────────┬─────────────────────────────────┘
                            │ same prompt every time
                            ▼
┌─────────────────────────────────────────────────────────────┐
│  AGENT                                                      │
│  1. Read QUEUE_IMPLEMENTATION_STATUS.md                     │
│  2. If Epic complete = yes → report done and STOP           │
│  3. Else open current task file under .crules/tasks/wip/    │
│  4. Implement ONLY that task (acceptance criteria)          │
│  5. Run verification commands from the task                 │
│  6. Update QUEUE_IMPLEMENTATION_STATUS.md                   │
│  7. Move task file wip/ → done/ when fully accepted         │
│  8. Report: what shipped, tests, next task, epic complete?  │
└─────────────────────────────────────────────────────────────┘
```

One session = **one task** (unless the agent is blocked and must stop early). Do not ask the agent to “do the whole queue” in one go.

---

## How to run a session

### 1. Prerequisites

```bash
cd ~/git/personal/local_transcribe
git checkout feat/queue-creation   # or your implementation branch
git status
```

Prefer a clean or intentional working tree so the agent’s diff is reviewable.

### 2. Start the agent

Open Grok / Claude / Cursor / etc. **with this repository as the workspace**.

### 3. Paste the evolving prompt

Copy everything in [The evolving prompt](#the-evolving-prompt) into the agent **as your first message**.

Do not add “also do 012 and 013” unless you intentionally want multi-task sessions (not recommended).

### 4. Review the result

When the agent finishes a session, check:

1. Does the report match the task acceptance criteria?
2. Did `QUEUE_IMPLEMENTATION_STATUS.md` advance **Current task** / **Next task**?
3. Did tests it claimed to run actually pass?
4. Diff: no forbidden designs (JSON lease, multi-path discovery, etc.)

Optional commit after each green task:

```bash
git add -A
git status
# commit with a message like: feat(queue): task 011 config and path resolution
```

### 5. Repeat

Open a **new** agent session (or continue if your tool preserves context and you prefer), paste **the same prompt again**, and continue until the agent reports:

```text
EPIC COMPLETE
```

That means `QUEUE_IMPLEMENTATION_STATUS.md` has **Epic complete: yes** and task 027 (and checklist) are done.

---

## The evolving prompt

Copy from the line below through the end of the fenced block. Paste as-is every time.

```text
You are the Coder agent implementing the local-transcribe background transcription queue.

## Protocol (mandatory)

1. Read `QUEUE_IMPLEMENTATION_STATUS.md` first (entire control block and current row).
2. If **Epic complete** is `yes`, reply with a short final summary and the words **EPIC COMPLETE**. Do not implement more code.
3. Otherwise, set yourself to the **Current task** only. Read that task file (path in the status file) and `SPEC-queue.md` sections it cites.
4. Implement **only** that one task. Do not start the next task in this session.
5. Follow project rules: type hints, pathlib, logging via `configure_logging` for CLI, no `--break-system-packages`, prefer `python3 -m` / venv / pipx for installs.
6. Forbidden designs (never introduce):
   - JSON heartbeat lease / rename-to-steal / timestamp ownership of the worker
   - Multi-candidate automatic queue path discovery
   - Using `safe_write_json()` for queue authority
   - Claiming absolute exactly-once Whisper execution
   - Changing default `lt transcribe` / `lt batch` behavior before task 024
7. Run the verification commands listed in the task (at least the pytest paths). Fix failures before declaring the task done.
8. When the task’s acceptance criteria are met:
   a. Update the task markdown: mark acceptance checkboxes, set Status to `done`, add a short Coder notes section.
   b. Move the task file from `.crules/tasks/wip/` to `.crules/tasks/done/` (create `done/` if needed).
   c. Update `QUEUE_IMPLEMENTATION_STATUS.md`:
      - Mark this task `done` in the table; append a row to the completed work log.
      - Set **Current task** / **Current task file** to the former **Next task**.
      - Set **Next task** / **Next task file** from the recommended sequence (skip any already `done`).
      - If the new current task is 010-only tracking or none remain after 027, set **Epic complete** to `yes` only when the Final completion checklist is satisfied.
      - Append a line under Session notes with date + what you did.
      - Set **Last updated** and **Updated by** (agent name/model if known).
9. If blocked (missing NFS lab, cross-repo ref-cli, etc.):
   - Do not fake completion.
   - Set task status `blocked` in the status table, fill Blockers, leave **Current task** on the blocked id, and tell the user what they must provide.
10. End your reply with a fixed footer:

## Session result
- Task attempted: <id>
- Outcome: done | blocked | partial
- Tests run: <commands and pass/fail>
- Status file: updated | not updated
- Next task: <id or EPIC COMPLETE>
- EPIC COMPLETE: yes | no

## User message (if epic finished)
If and only if the epic is complete, also say clearly:
"All queue implementation tasks are finished. No further implementation sessions are required for this epic."

## Scope for this session
Implement the single task identified as **Current task** in `QUEUE_IMPLEMENTATION_STATUS.md`. Begin now.
```

---

## What “evolving” means

The **prompt text stays fixed**. What evolves is:

| File | How it evolves |
|------|----------------|
| `QUEUE_IMPLEMENTATION_STATUS.md` | Current/next task, log, blockers, epic flag |
| `.crules/tasks/wip/*.md` → `done/` | Task moves when finished |
| Source tree | Code and tests accumulate |
| Git history | Your commits after each session (recommended) |

You never need a new prompt like “now do task 014” unless you are recovering from a broken status file.

---

## Recovering if something goes wrong

### Agent did two tasks at once

- Prefer to keep the work if tests pass.
- Manually fix `QUEUE_IMPLEMENTATION_STATUS.md` so **Current task** is the true next incomplete id.
- Move both finished task files to `done/`.

### Status file and disk disagree

Trust **code + tests + task file location** over a stale status line. Reconcile the control block to the first incomplete id in the recommended sequence.

### Agent stuck on 027 (NFS lab)

Mark 027 `blocked` or complete non-lab parts (`docs`, unit tests) and leave multi-host checklist open. You can set **Epic complete** only if you explicitly accept lab deferral in Session notes and Manager approval — default is **do not** set complete until 027 is honestly done or skipped with a written skip reason in the status file.

### Want to force a different task

Edit the control block yourself:

```markdown
| **Current task** | `020` |
| **Current task file** | `.crules/tasks/wip/020-downloader-hardening.md` |
| **Next task** | `019` |
...
```

Then paste the evolving prompt again. Only do this when dependencies in the task file are already `done`.

---

## Optional: commit after every green task

Suggested message pattern:

```text
feat(queue): task NNN <short title>

Implements .crules/tasks/done/NNN-....md
```

Or if you use Manager/GIT_POLICY commit flow, run your usual `commit` workflow after reviewing.

Do **not** require the agent to push to origin unless you ask.

---

## Optional: one-shot automation script (human)

If you use a tool that can re-invoke an agent in a loop, the loop is:

```text
while true; do
  # invoke agent with THE PROMPT
  # if agent output contains "EPIC COMPLETE: yes" → break
  # optional: git commit
done
```

Still review intermediate commits; unattended full epic is risky on task 024+ (CLI cutover) and 026 (ref-cli) and 027 (NFS).

---

## Task map (quick reference)

| ID | Outcome in plain language |
|----|---------------------------|
| 011 | Config + explicit queue path |
| 012 | Atomic write + hard-link publish helpers |
| 013 | Reject unsafe NFS mounts |
| 014 | Source keys + pending executions |
| 015 | Safe transcript publish with generations |
| 016 | NLM `fcntl` worker lock |
| 017 | Standby / state / doctor hooks |
| 018 | Real worker loop |
| 019 | Whisper model reuse |
| 020 | Safer yt-dlp for daemons |
| 021 | Hard download rate admission |
| 022 | `lt queue …` commands |
| 023 | `lt worker …` + systemd unit |
| 024 | Default transcribe/batch use the queue |
| 025 | Import old pending files |
| 026 | Wire ref-cli |
| 027 | Prove multi-host NFS + docs |

Full acceptance criteria live in each task file under `.crules/tasks/wip/` (later `done/`).

---

## Definition of “completely implemented”

The agent may only claim full completion when **all** of the following hold:

1. `QUEUE_IMPLEMENTATION_STATUS.md` → **Epic complete** = `yes`
2. Final completion checklist in that file is checked
3. Footer contains `EPIC COMPLETE: yes`
4. User-facing line:  
   `All queue implementation tasks are finished. No further implementation sessions are required for this epic.`

Until then, keep pasting the evolving prompt.

---

## First session (right now)

1. Confirm you are on `feat/queue-creation`.
2. Confirm `QUEUE_IMPLEMENTATION_STATUS.md` shows **Current task: 011**.
3. Paste [The evolving prompt](#the-evolving-prompt) into a new agent session in this repo.
4. After it finishes, review and optionally commit.
5. Repeat with the same prompt until **EPIC COMPLETE**.

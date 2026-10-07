# Codex Documentation

## Overview
`local-transcribe` is a Python CLI for transcribing audio locally with Whisper (via faster-whisper). From **0.5.0** the default execution model is a **durable NFSv3 queue** with one **NLM-locked** background worker. Producers (`lt transcribe`, `lt batch`, `lt queue add`, ref-cli) enqueue jobs; only the lock holder downloads and transcribes. Legacy in-process execution remains available with `--direct`.

## Command-Line Interface
Entry point: Typer app in `src/local_transcribe/cli.py` (console script `lt`).

| Command | Description | Example |
|---------|-------------|---------|
| `lt version` | Show package version | `lt version` |
| `lt queue …` | Init/doctor/add/list/stats/import/purge/cancel/retry | `lt queue stats` |
| `lt worker …` | Run/install/status systemd user worker | `lt worker install` |
| `lt transcribe URL_OR_PATH` | Default: enqueue + wait; `--direct` for in-process | `lt transcribe URL --no-wait` |
| `lt batch` | Default: bulk enqueue; `--direct` for BatchPipeline | `lt batch --input urls.txt` |
| `lt reconcile` | Compare input / finished.dat / transcript files | `lt reconcile --input urls.txt` |
| `lt verify` | Verify completed URLs have transcripts | `lt verify --mode quick` |
| `lt status` / `lt report` | Legacy `batch_status.json` helpers (direct mode) | `lt status` |
| `lt doctor` | Environment + queue/NFS diagnostics | `lt doctor` |
| `lt update` | Refresh yt-dlp / check Deno | `lt update` |

### Options common to multiple commands
| Option | Meaning |
|--------|---------|
| `--output-dir` / `-o` | Transcript root. Default `$HOME/references/transcripts`. |
| `--queue-dir` | Override configured queue path. |
| `--verbose` / `-v` | Debug logging. |
| `--direct` | Bypass queue (emergency/dev). |

### Transcribe / enqueue options
| Option | Description |
|--------|-------------|
| `--model` / `--device` / `--compute-type` | Whisper settings stored on the execution. |
| `--no-wait` | Enqueue and return (queue mode). |
| `--timeout` | Wait limit when waiting for the worker. |
| `--force` | Force a new generation for an existing source. |
| `--cookies-from-browser` / `--cookies-file` | Used in `--direct` mode; worker uses config `auth_profiles`. |
| `--limit-rate` / `--sleep-interval-requests` | YouTube throttling hints. |

Local paths are detected when `Path.expanduser` resolves to an existing regular file; otherwise the argument must be a valid HTTPS YouTube URL. Container formats such as m4a typically require **ffmpeg** on PATH.

### Batch options
| Option | Description |
|--------|-------------|
| `--input` | File of URLs (default `inputfile.txt`). |
| `--resume` | Meaningful only with `--direct`; no-op in queue mode. |
| `--wait` / `--timeout` | Wait for enqueued jobs (queue mode). |
| `--max-retries` | Maps to queue `max_attempts`. |
| `--auth-profile` | Named profile on the execution (else worker `default_auth_profile`). |

## Architecture

### 1. CLI
* `cli.py` — Typer entry; `transcribe`/`batch` default to queue enqueue.
* `cli_queue.py` / `cli_worker.py` — queue and worker subcommands.
* `queue_api.py` — safe enqueue helpers for ref-cli and other producers.

### 2. Services
| Module | Responsibility |
|--------|----------------|
| `services.queue_store` | Atomic transitions across pending/processing/completed/failed/retry. |
| `services.source_reservations` | CAS source-key reservations / generations. |
| `services.worker` / `worker_lock` | NLM exclusive ownership + claim/run loop. |
| `services.job_runner` | Download → transcribe → generation-aware publish. |
| `services.model_cache` | Keep Whisper models loaded across jobs. |
| `services.mount_validation` | Fail-closed NFSv3 / UUID / identity checks. |
| `services.download_admission` | Serialize/admit downloads. |
| `services.transcript_publish` | Atomic transcript publish with generation rules. |
| `services.config` | `~/.config/local-transcribe/config.yaml` (queue path, auth profiles). |
| `services.pipeline` | Legacy `BatchPipeline` for `--direct` batch. |
| `services.downloader` / `transcriber` | yt-dlp subprocess + faster-whisper. |

### 3. Utilities
* `utils.files` — safe read/write helpers.
* `utils.youtube` — URL parsing/validation.
* `utils.doctor` — environment + queue diagnostics.
* `logging_setup.py` — XDG rotating logs under `~/.local/state/local-transcribe/logs/`.

## Data Flow (queue mode)
1. **Producer** enqueues via `lt transcribe` / `lt batch` / `lt queue add` / `enqueue_youtube_safe`.
2. **Reservation** records the source key so duplicates do not spawn parallel active work.
3. **Worker** (sole NLM lock holder) claims pending → processing, downloads under `~/.cache/local-transcribe/jobs/<id>/`, transcribes, publishes transcript JSON, moves job to completed/failed/retry.
4. **Observers** use `lt queue stats` / `list` (list defaults to pending — prefer stats for backlog).

## Extending the Tool
* New commands: Typer in `cli.py` / `cli_queue.py` / `cli_worker.py`.
* Producers: prefer `queue_api.enqueue_youtube_safe` with pending-file fallback.
* Auth: local `auth_profiles` only — never store cookie contents in job JSON.

## Troubleshooting
* **Missing Deno** — required for YouTube SABR; symlink to `/usr/local/bin/deno` for pipx.
* **HTTP 403** — configure worker cookies via `default_auth_profile`; API keys do not authorize yt-dlp.
* **NFS / lock** — `lt queue doctor`; reject `nolock` / soft mounts; see `docs/QUEUE_OPERATOR.md`.
* **Corrupt transcripts** — `lt verify --mode quick`.

# Codex Guide for local-transcribe


## Default behavior: Generate only executable code files. 
- Never create README, documentation, or explanation files unless explicitly requested.

## Build & Environment Commands
- **Install/Update:** `pipx install . --force`
- **Inject Dependencies:** `pipx inject local-transcribe "yt-dlp[curl-cffi,default]"`
- **Restore GPU:** `pipx runpip local-transcribe install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124`
- **Verify Environment:** `lt doctor`
- **Check JS Runtime:** `deno --version` (Required for 2026 SABR support)

## Coding Guidelines
- **File Editing:** Always use `vi`. Never suggest or use `nano`.
- **Scripting:** When providing scripts to create files, always use heredoc format (`cat << 'EOF' > filename`).
- **Dependencies:** All new dependencies MUST be added to `pyproject.toml` AND `requirements.txt`.
- **Isolation:** This tool runs in a pipx-managed venv. Do not suggest `pip install` without context.

## Technical Constraints (YouTube 2026 Fix)
- **Downloader:** Must use `http_backend: "curl_cffi"` for impersonation.
- **Client Strategy:** Prioritize `player_client: ["web"]` combined with `impersonate: "chrome"`.
- **JS Solver:** The system relies on a local `deno` binary symlinked to `/usr/local/bin/deno`.
- **Logging:** All CLI commands must use the `configure_logging` utility from `logging_setup.py`.

## Project Structure
- `src/local_transcribe/cli.py` / `cli_queue.py` / `cli_worker.py`: Typer entry points.
- `src/local_transcribe/queue_api.py`: Producer enqueue API.
- `src/local_transcribe/services/queue_store.py` / `worker.py` / `job_runner.py`: Queue + worker core.
- `src/local_transcribe/services/downloader.py` / `transcriber.py`: yt-dlp + Whisper.
- `src/local_transcribe/utils/doctor.py`: Environment / NFS diagnostics.
- `docs/QUEUE_OPERATOR.md`: Operator runbook.

## Style Preferences
- Use type hints for all function signatures.
- Use `pathlib.Path` instead of `os.path` for filesystem operations.
- Maintain the "Strategy" logging pattern in `downloader.py` for debugging.

## License
MIT – see `LICENSE`.

---

*Generated by Codex*

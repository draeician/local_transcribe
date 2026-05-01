# Claude Code Documentation

## Overview
`local-transcribe` is a Python CLI for transcribing audio locally with the Whisper speech‑to‑text model (via faster-whisper).  It can download and transcribe YouTube videos or transcribe audio files already on disk.  The tool is written in pure Python with a minimal dependency set and is designed to be run on Linux, macOS, and Windows.

## Command‑Line Interface
The entry point is the Typer app defined in `src/local_transcribe/cli.py`.  All commands are available via the `lt` alias (the `local-transcribe` package installs a console script called `lt`).  The following commands are supported:

| Command | Description | Example |
|---------|-------------|---------|
| `lt version` | Show package version | `lt version` |
| `lt transcribe URL_OR_PATH [options]` | Transcribe one YouTube video (HTTPS URL) or one local audio file | `lt transcribe https://youtu.be/dQw4w9WgXcQ --model large --device cuda` · `lt transcribe ~/recordings/talk.m4a -o ./out` |
| `lt batch [options]` | Process a list of URLs from a file, with resume support | `lt batch --input urls.txt --resume` |
| `lt reconcile [options]` | Compare an input file, `finished.dat` and transcript files; generates summary reports | `lt reconcile --input urls.txt` |
| `lt verify [options]` | Verify that every completed URL has an associated transcript | `lt verify --mode quick` |
| `lt status [options]` | Show counts of pending / processing / completed / failed videos from `batch_status.json` | `lt status` |
| `lt report [options]` | Generate a plain‑text report of all failed videos | `lt report --out failed.txt` |
| `lt update` | Check that Deno (used by the yt‑dl downloader) is available and optionally upgrade | `lt update` |

### Options Common to Multiple Commands
| Option | Meaning |
|--------|---------|
| `--output-dir` / `-o` | Directory where transcripts, status, and logs are written.  Default is `$HOME/references/transcripts`.
| `--verbose` / `-v` | Enables debug‑level logging.

### Transcribe Options
| Option | Description |
|--------|-------------|
| `--model` | Whisper model to use (`tiny`, `base`, `small`, `medium`, `large`). Default `medium`.
| `--device` | Target device (`cpu`, `cuda`, `auto`). Default `cuda`.
| `--compute-type` | Precision (`float16`, `int8`).
| `--keep-audio` | Preserve the downloaded audio file after transcription (no‑op when the source is a local file).
| `--cookies-from-browser` / `--cookies-file` | Provide browser cookies to bypass YouTube restrictions (ignored for local files).
| `--limit-rate` | Max download speed, e.g. `200K` or `4.2M` (YouTube only).
| `--sleep-interval-requests` | Pause between yt‑dl requests (YouTube only).

Local paths are detected when `Path.expanduser` resolves to an existing regular file; otherwise the argument must be a valid HTTPS YouTube URL.  Container formats such as m4a typically require **ffmpeg** on PATH for decoding.

### Batch Options
| Option | Description |
|--------|-------------|
| `--input` | File containing URLs (defaults to `inputfile.txt`).
| `--resume` | Resume from an interrupted run using `batch_status.json`.
| `--max-retries` | How many times to retry a failed video.
| `--sleep-interval` | Pause between videos.

## Architecture
The project is split into several loosely coupled modules.

### 1. CLI (`src/local_transcribe/cli.py`)
* Provides the Typer application.
* Parses command‑line arguments and configures logging.
* Delegates to service modules for heavy work.

### 2. Services
| Module | Responsibility |
|--------|----------------|
| `services.pipeline` | Implements `BatchPipeline` – the orchestration layer for batch jobs. Handles resume logic, status tracking, rate limiting, and graceful shutdown.
| `services.transcriber` | Wrapper around faster‑whisper. Performs speech‑to‑text for downloaded or local audio; `transcribe_local_file` writes the same JSON shape as YouTube runs.
| `services.downloader` | Uses `yt-dlp` (executed via `subprocess`) to download audio. Includes custom rate‑limit and error handling logic.
| `services.rate_limiter` | Persists request counts to `rate_limits.json` to avoid exceeding YouTube limits.
| `services.status_store` | Persists per‑video status (`batch_status.json`). Uses a simple dataclass `TranscriptStatus`.
| `services.verify_status` | Verifies that transcripts exist for finished URLs.
| `services.reconcile` | Generates reconciliation reports comparing input, finished, and transcript sets.

### 3. Utilities
* `utils.files` – Safe read/write helpers and cookie handling.
* `utils.youtube` – YouTube URL parsing and validation.

## Data Flow
1. **Input** – `batch` reads URLs from an input file; `transcribe` takes either a YouTube URL or a path to a local audio file.
2. **Preparation** – `BatchPipeline` initializes `TranscriptStatus` objects, checking for existing transcripts.
3. **Processing** – For each pending video (batch), or once for `transcribe`:
   * YouTube: download audio via `services.downloader`; local file: use the path as‑is.
   * Transcribe with `services.transcriber` and write transcript JSON.
   * Batch runs also update `batch_status.json` per video.
4. **Post‑processing** – `verify` or `reconcile` generate reports and update `batch_status.json`.
5. **Reporting** – `status` and `report` provide quick status views.

## Extending the Tool
* **Adding new commands** – Create a Typer command in `cli.py` and delegate to a new service.
* **Custom Whisper models** – Pass any model name supported by Whisper to the `TranscribeConfig`.
* **Different download backends** – Replace `services.downloader` with another downloader while keeping the same interface.

## Troubleshooting
* **Missing Deno** – `lt update` checks for Deno. Install via `brew install deno` (macOS) or `curl -fsSL https://deno.land/x/install/install.sh | sh`.
* **Rate limiting** – The tool will pause automatically. If you hit 429s, increase `--sleep-interval-requests`.
* **Corrupt transcripts** – Use `lt verify --mode quick` to detect and flag.

# Claude Code Guide for local-transcribe


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
- `src/local_transcribe/cli.py`: Typer entry point.
- `src/local_transcribe/services/downloader.py`: Logic for yt-dlp strategies.
- `src/local_transcribe/services/transcriber.py`: Logic for Whisper/Faster-Whisper.
- `src/local_transcribe/utils/doctor.py`: Environment diagnostic checks.

## Style Preferences
- Use type hints for all function signatures.
- Use `pathlib.Path` instead of `os.path` for filesystem operations.
- Maintain the "Strategy" logging pattern in `downloader.py` for debugging.

## License
MIT – see `LICENSE`.

---

*Generated by Claude Code*

# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Worker auto-imports `~/references/transcripts/transcript-pending.md` (configurable) so ref-cli can enqueue without installing `local-transcribe`

## [0.5.1] - 2026-10-07

### Fixed
- The background worker now actually unloads its cached Whisper model after 300 seconds (5 minutes) of model idleness. `ModelCache.maybe_unload_idle()` existed but was never called, and its default was 1800s, so a long-running worker pinned the model (and its VRAM) forever
- Idle time is measured from the **end** of the model's most recent use, not from when it was handed out: `ProductionJobRunner` reports completion of every `transcribe_with_model()` call (success or failure) to `ModelCache.mark_used()`. Time spent transcribing is model use, so a job longer than five minutes no longer looks idle the moment it finishes and still gets a full five minutes of cache; jobs arriving inside that window reuse the loaded model, and eviction still never happens during an active transcription

## [0.5.0] - 2026-07-30

### Added
- NFSv3/NLM background transcription queue with durable pending/processing/completed/failed/retry states
- CLI: `lt queue` (init, enqueue, list, stats, import, purge, wait) and `lt worker` (run, install systemd user unit)
- Config-driven queue path/UUID/NFS identity checks, auth profiles (`default_auth_profile` + cookies), XDG rotating logs
- Producer-safe enqueue/reservations, legacy pending import, download admission, ModelCache-backed worker job runner
- NVIDIA CUDA torch auto-install hooks for doctor/worker setup; real NFSv3 lab scripts and operator docs

### Changed
- Queue init writes `~/.config/local-transcribe/config.yaml`; doctor validates mount identity and NLM locks
- Mount validation prefers real NFS mounts over autofs placeholders when both are present

## [0.4.0] - 2026-04-30

### Added
- `lt transcribe` accepts a path to a local audio file as well as an HTTPS YouTube URL; writes the same JSON transcript format, using a filesystem-safe slug from the file stem for the output filename. Deno is not checked when transcribing local files. Many formats (e.g. m4a) require ffmpeg on PATH for decoding.

## [0.3.9] - 2026-01-25

### Fixed
- Fixed YouTube 2026 "SABR" throttling issue that caused HTTP 403 Forbidden errors
- Simplified download strategies to use stable-web (web client) with Deno support
- Changed default HTTP backend from curl_cffi to requests for improved stability
- Added proper handling for HTTP 403 Forbidden errors with retry logic

### Changed
- Downloader now uses web client strategy as primary method (requires Deno runtime)
- Removed Safari UA header and complex multi-strategy fallbacks
- Updated pipeline to handle 403 errors gracefully with retry suggestions

### Added
- `lt update` command for easy package updates with Deno verification
- Deno runtime check in `lt doctor` diagnostics
- Deno installation requirement in prerequisites and installation guides
- Troubleshooting documentation for YouTube 2026 SABR throttling issue

## [0.1.3] - 2025-11-07

### Added
- `--version` flag to display version information (`lt --version`)
- Help screen now displays when running `lt` with no arguments

### Fixed
- `lt` command with no arguments now properly displays help screen instead of silently exiting

## [0.1.2] - 2025-11-07

### Added
- GPU packages (`nvidia-cublas`, `nvidia-cudnn`) now included as default dependencies
- Installation script `install_with_gpu.sh` for easy pipx installation with GPU support
- Comprehensive installation guide in README.md for pipx and git clone methods
- Support for installing from GitHub via `pipx install git+https://github.com/draeician/local_transcribe.git`

### Fixed
- Fixed subcommand detection in smart callback - `lt doctor`, `lt status`, etc. now work correctly
- Fixed doctor command to properly detect `nvidia.cu13` (CUDA 13 version of cublas)
- Updated entry point to use `main()` function for better pipx compatibility

### Changed
- GPU packages moved from optional dependencies to default dependencies
- Updated README.md with detailed pipx installation instructions
- Updated START_HERE.md with pipx installation options
- Updated requirements.txt to include CUDA packages

## [0.1.1] - 2025-11-07

### Fixed
- Fixed subcommand execution when using smart detection callback
- Subcommands (doctor, status, etc.) now work correctly with `invoke_without_command=True`

## [0.1.0] - 2025-11-07

### Added
- Initial library-first refactoring
- Unified CLI with `lt` command
- Smart detection for URLs vs file paths
- Default values for common options (model, device, compute_type, output-dir)
- Batch processing with resume capability
- Status tracking and reconciliation
- Environment diagnostics (`lt doctor`)


# TASKS.md — Library‑first refactor and unified CLI

> Goal: turn the current collection of scripts into a **library‑first** package with a single **command‑line interface (CLI)**, while keeping backward compatibility and preserving your data model (JavaScript Object Notation (JSON) transcripts, `finished.dat`).
>
> Principle: every capability is importable via an **application programming interface (API)**; the CLI composes those same functions.

---

## Legend

* ✅ DoD — Definition of Done (acceptance criteria)
* 🧩 Files — paths to modify/create
* 🛡️ Safety — migration/compat notes

---

## Phase 0 — Repo hygiene & working branch

1. **Create feature branch**

   * Name: `feat/library-cli-refactor`
   * 🧩 `git checkout -b feat/library-cli-refactor`
   * ✅ DoD: branch exists, pushed to origin.

2. **Pin Python**

   * Add `.python-version` (if using pyenv) and `python_requires` in project.
   * Target >= 3.10.
   * ✅ DoD: local venv created; `python -V` prints >= 3.10.

---

## Phase 1 — Package scaffold (library first)

1. **Create package layout**

   * 🧩 Create directories:

     ```
     src/local_transcribe/
     src/local_transcribe/services/
     src/local_transcribe/utils/
     ```
   * Create empty `__init__.py` files.
   * ✅ DoD: `pip install -e .` succeeds with empty package.

2. **Add build metadata**

   * 🧩 `pyproject.toml` with project metadata and runtime deps.
   * Expose `lt` console script → `local_transcribe.cli:app`.
   * ✅ DoD: `lt --help` executes (temporary stub).

3. **Logging setup module**

   * 🧩 `src/local_transcribe/logging_setup.py`
   * Provide `configure_logging(verbose: bool)` for console + file logs.
   * ✅ DoD: importing and calling does not error.

---

## Phase 2 — Utilities extracted

1. **YouTube helpers**

   * 🧩 `src/local_transcribe/utils/youtube.py`
   * Move `extract_video_id`, URL validators, `pick_channel`.
   * ✅ DoD: unit tests demonstrate ID extraction for `watch`, `shorts`, `live`, `youtu.be`.

2. **Filesystem helpers**

   * 🧩 `src/local_transcribe/utils/files.py`
   * Safe read/write, glob helpers, path checks.
   * ✅ DoD: helper functions imported by services without circular deps.

---

## Phase 3 — Downloader service

1. **Wrap yt‑dlp strategies**

   * 🧩 `src/local_transcribe/services/downloader.py`
   * Public `download_audio_and_metadata(url, outdir, cookies_from_browser, cookies_file) -> (Path, dict)`.
   * Keep your multi‑client fallback order (Safari/TV/Android/18) and headers.
   * ✅ DoD: function returns a file path + info dict across at least one real URL in dev.

🛡️ Safety: Reuse your current retry and fragment settings; preserve cookie options.

---

## Phase 4 — Transcriber service

1. **Port local_transcribe.py**

   * 🧩 `src/local_transcribe/services/transcriber.py`
   * Public API:

     * `TranscribeConfig(model, device, compute_type, output_dir, keep_audio, cookies_from_browser, cookies_file)`
     * `transcribe_url(url: str, cfg: TranscribeConfig) -> Path`
   * Include CUDA preflight (graphics processing unit (GPU))/cuDNN checks and CPU (central processing unit) fallback.
   * ✅ DoD: produces identical JSON schema; respects `--keep-audio`.

---

## Phase 5 — Status store (resume)

1. **Define status model & store**

   * 🧩 `src/local_transcribe/services/status_store.py`
   * `TranscriptStatus` dataclass and `StatusStore` protocol with `JsonStatusStore` implementation for `batch_status.json`.
   * ✅ DoD: load/save round‑trip preserves fields.

---

## Phase 6 — Batch pipeline orchestrator

1. **Port batch_transcribe.py logic**

   * 🧩 `src/local_transcribe/services/pipeline.py`
   * `BatchConfig(input_file, output_dir, model, device, compute_type, max_retries, status_store, cookies_from_browser, cookies_file)`
   * `run_batch(config: BatchConfig, resume: bool) -> BatchSummary`
   * Responsibilities: dedupe inputs, resume, timeout per video, retries, progress logging, update `finished.dat` only on verified success.
   * ✅ DoD: end‑to‑end run updates `batch_status.json`, writes transcripts, and appends to `finished.dat`.

🛡️ Safety: maintain exact `finished.dat` write semantics for compatibility.

---

## Phase 7 — Reconcile as pure service

1. **Refactor reconcile**

   * 🧩 `src/local_transcribe/services/reconcile.py`
   * Pure functions returning `ReconcileReport` (no `print`).
   * Separate writer helpers to emit: `inputfile_clean.txt`, `pending.txt`, `finished_corrected.dat`, `missing_transcripts.txt`, `orphaned_transcripts.txt`.
   * ✅ DoD: outputs match the current script behavior on the same inputs.

---

## Phase 8 — Unified CLI with Typer

1. **Implement CLI entrypoint**

   * 🧩 `src/local_transcribe/cli.py`
   * Subcommands:

     * `lt transcribe URL [--model --device --compute-type --output-dir --keep-audio ...]`
     * `lt batch --input INPUT.txt [--resume --max-retries --device --model --output-dir]`
     * `lt reconcile [--input inputfile.txt --finished finished.dat --out out/]`
     * `lt status [--store batch_status.json]` (optional counts/summary)
     * `lt report [--store ... --out logs/failed_videos.txt]`
     * `lt doctor` (environment diagnostics)
   * Wire `--verbose/-v` to `configure_logging`.
   * ✅ DoD: `lt --help` shows commands; each subcommand executes and delegates to services.

🛡️ Safety: keep `python local_transcribe.py` working via a tiny shim (optional) or print migration hint.

---

## Phase 9 — Doctor (env diagnostics)

1. **Migrate test_cuda_setup.sh into Python**

   * 🧩 `src/local_transcribe/utils/doctor.py`
   * Checks: `ffmpeg`, `yt-dlp`, NVIDIA driver visibility, CUDA toolkit, cuDNN `.so` presence, `ctranslate2` device count, PyTorch CUDA availability, `LD_LIBRARY_PATH` hints.
   * `lt doctor` prints a concise health report and non‑zero exit on critical failures.
   * ✅ DoD: doctor runs without shell scripts and catches the known cuDNN path issue.

---

## Phase 10 — Logging & observability

1. **Structured logs + human logs**

   * File logs under `logs/batch_transcribe_YYYYMMDD_HHMMSS.log`.
   * Console logs concise; file logs include level/timestamp/module.
   * ✅ DoD: logs written for batch runs; failed report generated at end.

---

## Phase 11 — Documentation pass

1. **Docs updates**

   * 🧩 Update `README.md` to show `lt` commands first; keep CUDA/cuDNN notes.
   * 🧩 Add `START_HERE.md` (quick install + two command workflow).
   * 🧩 Update `QUICK_REFERENCE.md` to use `lt` alias.
   * 🧩 Add a short deprecation banner to `run_transcribe.sh` and `batch_transcribe.py` pointing to CLI.
   * ✅ DoD: a new user can install and run a batch in <10 commands (no time estimate displayed in docs).

---

## Phase 12 — Tests

1. **Unit tests**

   * 🧩 `tests/test_youtube.py` for ID extraction & invalids.
   * 🧩 `tests/test_reconcile.py` for set math and file emission (tmpdir).
   * 🧩 `tests/test_cli.py` for `lt --help` and argument parsing smoke.
   * ✅ DoD: `pytest` green locally.

---

## Phase 13 — CI & quality gates (optional but recommended)

1. **Tooling**

   * 🧩 Add `ruff` (lint/format) and `mypy` (types). Optional `pre-commit`.
   * 🧩 GitHub Actions: run `pip install -e .[dev] && pytest`.
   * ✅ DoD: CI passes on branch; PR checks visible.

---

## Phase 14 — Packaging & release

1. **Editable install & version**

   * Ensure `pip install -e .` works on a clean machine.
   * Tag `v0.1.0` when merged.
   * ✅ DoD: `lt` available after install; imports work from other scripts.

---

## Phase 15 — Nice‑to‑have follow‑ups

* SQLite status store for robustness under crashes.
* Parallel **downloads** with serial **transcribe** (GPU memory friendly).
* `lt push --dest /opt/md2/music/youtube/transcripts` (wraps your `rsync`).
* Rich `lt status` (group counts, durations, ETA) without promising times.

---

## Cutover plan

1. Land Phases 1–4 (library core) → smoke test `transcribe_url`.
2. Land Phases 5–7 (resume + reconcile) → verify parity with existing outputs.
3. Land Phase 8 (CLI) → switch docs to `lt`.
4. Keep legacy scripts for one release with deprecation notice.

---

## Quick commands (for Cursor tasks)

* Create scaffolding and install:

  ```bash
  mkdir -p src/local_transcribe/services src/local_transcribe/utils
  printf "" > src/local_transcribe/__init__.py
  printf "" > src/local_transcribe/services/__init__.py
  printf "" > src/local_transcribe/utils/__init__.py
  pip install -e .
  lt --help
  ```
* Run end‑to‑end after Phase 8:

  ```bash
  lt reconcile --input inputfile.txt --finished finished.dat
  lt batch --input inputfile_clean.txt --device cuda --compute-type float16
  lt doctor
  ```

---

## Acceptance summary (must all pass before merge)

* Library API usable from Python scripts (import and call).
* CLI subcommands functionally equivalent to current scripts.
* Reconcile outputs match current behavior on the same inputs.
* Resume works; `finished.dat` only records verified completions.
* Logs and failure report created.
* Minimal tests pass.
* Documentation shows the new path clearly.

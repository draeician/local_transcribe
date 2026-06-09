# Task 001 — yt-dlp update handling and stale-yt-dlp error detection

| Field | Value |
|-------|-------|
| **Status** | `review` (partial — `lt update` complete; downloader classifier pending) |
| **Branch** | `fix/yt-dlp-update-error-classifier` |
| **Type** | `fix` (patch bump) |
| **Owner** | Coder |

## Summary

`lt update` currently upgrades the `local-transcribe` package via pipx/local pip but does **not** refresh the `yt-dlp` dependency inside the running `lt` environment. When YouTube changes break older yt-dlp builds, batch and transcribe failures surface as generic `RuntimeError` blobs that interleave huge `--print-json` stdout with useful stderr. This task makes `lt update` the canonical yt-dlp refresh path and adds a downloader error classifier that routes users to the right remediation.

**Scope guard:** Atomic change only. Do not refactor batch orchestration, CUDA/Whisper paths, status-store schema, or unrelated CLI commands.

## Problem (current behavior)

1. **`lt update`** (`src/local_transcribe/cli.py` ~L640): checks Deno, detects pipx vs local dev, runs `pipx upgrade local-transcribe` or `pip install -e . --upgrade`. It never updates `yt-dlp` in `sys.executable`'s environment.
2. **`downloader.py`**: on non-zero exit, classifies only 403 / 429 / unavailable via substring checks, then raises `RuntimeError` with **both** stdout and stderr. Because `--print-json` is always passed, stdout can be enormous JSON noise that buries the actionable stderr lines in batch reports and `lt report` output.
3. **No tests** exist for update command construction or downloader error classification.

## Implementation plan

### 1. Extract yt-dlp version helper (shared)

Add a small helper (location: `src/local_transcribe/utils/doctor.py` or a new `src/local_transcribe/utils/ytdlp.py` — prefer reusing `check_yt_dlp()` import path in doctor) that returns the installed `yt_dlp.version.__version__` string or `None` if missing.

```python
def get_yt_dlp_version() -> str | None: ...
```

Reuse `import yt_dlp` (already a project dependency); no new third-party packages.

### 2. Rework `lt update` (`src/local_transcribe/cli.py`)

Replace the pipx/local-transcribe self-upgrade focus with **yt-dlp refresh in the current runtime**:

1. **Deno gate** (preserve existing behavior): verify Deno is installed/accessible; on failure print the existing install instructions and exit non-zero.
2. **Print current yt-dlp version** via `get_yt_dlp_version()`.
3. **Upgrade yt-dlp** using exactly:

   ```python
   [sys.executable, "-m", "pip", "install", "-U", "--pre", "yt-dlp[default,curl-cffi]"]
   ```

   - Use `subprocess.run(..., check=False)`; on non-zero exit, print stderr and exit non-zero.
   - **Never** use `--break-system-packages`.
4. **Print new yt-dlp version** after a successful pip run.
5. Optionally keep a lightweight post-check via `run_diagnostics()` for Deno / FFmpeg / yt-dlp (existing Step 4 pattern is fine); do not reintroduce pipx self-upgrade as the primary action.

User-facing output should clearly show: Deno status → old version → pip command (or success line) → new version.

### 3. Downloader error classifier (`src/local_transcribe/services/downloader.py`)

_Addressed in a follow-up slice; not implemented in this review pass._

### 4. Pipeline / report surfacing (minimal touch)

_Pending downloader classifier._

### 5. Tests

| File | Coverage |
|------|----------|
| `tests/test_update.py` | ✅ Done |
| `tests/test_downloader_errors.py` | Pending |

## Files

| Action | Path |
|--------|------|
| Modify | `src/local_transcribe/cli.py` — `update` command ✅ |
| Create | `src/local_transcribe/utils/ytdlp_update.py` ✅ |
| Create | `tests/test_update.py` ✅ |
| Modify | `src/local_transcribe/services/downloader.py` — classifier + exceptions (pending) |
| Modify | `src/local_transcribe/services/pipeline.py` — catch new exception types only (pending) |

## Acceptance criteria

### `lt update` (completed in this pass)

- [x] `lt update` prints the current yt-dlp version, updates yt-dlp with prerelease support, then prints the new version.
- [x] `lt update` uses the current Python executable: `sys.executable -m pip install -U --pre "yt-dlp[default,curl-cffi]"`.
- [x] `lt update` verifies Deno is available and clearly reports when it is missing (`shutil.which("deno")` on PATH).
- [x] `--stable` omits `--pre`; default includes `--pre`.
- [x] `--dry-run` prints the exact pip command without modifying the environment.
- [x] Tests cover update command construction, `--stable`, `--dry-run`, and missing Deno.

### Downloader classifier (deferred)

- [ ] Downloader failures caused by stale/broken yt-dlp produce a message that says to run `lt update`.
- [ ] Downloader failures caused by 403/SABR/missing URL/format 140 unavailable/n-challenge/signature issues are classified separately from generic failures.
- [ ] Batch failure reports preserve useful stderr detail and do not bury it behind huge `--print-json` stdout.
- [ ] Tests cover the downloader error classifier.
- [ ] Existing tests still pass.

## Verification commands

```bash
pip install -e ".[dev]"
pytest -q tests/test_update.py
lt update          # manual: observe version before/after (requires Deno on PATH)
lt update --dry-run
lt update --stable
```

## Coder Notes

**Completed (2026-06-08):** Replaced pipx/local-transcribe self-upgrade with yt-dlp-only refresh in `src/local_transcribe/utils/ytdlp_update.py` and a slim `update` command in `cli.py`.

- Deno gate uses `shutil.which("deno")` only (no legacy path probing).
- Version detection prefers PATH `yt-dlp`/`yt_dlp` binary `--version`, then `sys.executable -m yt_dlp --version`.
- `build_yt_dlp_pip_command()` centralizes argv construction for tests.
- `perform_yt_dlp_update()` handles pip run, dry-run short-circuit, and stderr tail on failure.
- Rich bracket markup in `yt-dlp[default,curl-cffi]` suppressed via `markup=False` on pip command lines.
- Removed pipx upgrade, editable self-upgrade, and post-update `run_diagnostics()` block to keep scope atomic.
- Nine tests in `tests/test_update.py`; all pass in project `.venv`.

**Deferred:** Downloader `classify_yt_dlp_failure`, new exception types, pipeline handlers, and `tests/test_downloader_errors.py` remain for the next wip slice.

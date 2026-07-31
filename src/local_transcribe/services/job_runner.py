"""Production worker job runner (remediation task 028).

Resolves options, downloads into local scratch, transcribes via ModelCache,
publishes generation-aware transcripts. Never stores media on the NFS
transcript root by default.
"""

from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

from local_transcribe.services.config import AppConfig, load_config
from local_transcribe.services.downloader import download_audio_and_metadata
from local_transcribe.services.model_cache import ModelCache
from local_transcribe.services.queue_models import Execution
from local_transcribe.services.transcriber import (
    build_output_json,
    default_model_loader,
    local_file_metadata,
    resolve_device_and_compute,
    transcribe_with_model,
)
from local_transcribe.services.transcript_publish import publish_transcript

logger = logging.getLogger(__name__)

# Injectable hooks for tests
DownloadFn = Callable[..., tuple[Path, dict]]
TranscribeFn = Callable[..., str]


@dataclass
class JobRunnerResult:
    """Structured result returned to the worker loop."""

    output_path: Path
    transcript_payload: dict[str, Any]
    effective_device: str
    effective_compute_type: str
    audio_path: Path | None = None

    def as_worker_payload(self) -> dict[str, Any]:
        payload = dict(self.transcript_payload)
        payload["_output_path"] = str(self.output_path)
        payload["_effective_device"] = self.effective_device
        payload["_effective_compute_type"] = self.effective_compute_type
        return payload


def default_scratch_root() -> Path:
    xdg = os.environ.get("XDG_CACHE_HOME")
    base = Path(xdg) if xdg else Path.home() / ".cache"
    return base / "local-transcribe" / "jobs"


def resolve_transcripts_root(
    *,
    config: AppConfig | None = None,
    override: Path | None = None,
) -> Path:
    if override is not None:
        return Path(override).expanduser()
    cfg = config or load_config()
    raw = cfg.raw.get("transcripts") if isinstance(cfg.raw, dict) else None
    if isinstance(raw, Mapping) and raw.get("root"):
        return Path(str(raw["root"])).expanduser()
    # Fall back to historical default
    return Path.home() / "references" / "transcripts"


def resolve_auth_profile(
    profile_name: str | None,
    *,
    config: AppConfig | None = None,
) -> tuple[str | None, Path | None]:
    """Return (cookies_from_browser, cookies_file) for a local auth profile.

    Cookie *contents* are never loaded into queue metadata — only local paths.
    """
    if not profile_name:
        return None, None
    cfg = config or load_config()
    profiles = cfg.raw.get("auth_profiles") if isinstance(cfg.raw, dict) else None
    if not isinstance(profiles, Mapping):
        logger.warning("auth_profile %r set but no auth_profiles in config", profile_name)
        return None, None
    entry = profiles.get(profile_name)
    if not isinstance(entry, Mapping):
        raise ValueError(f"Unknown auth_profile: {profile_name!r}")
    browser = entry.get("cookies_from_browser")
    cookies_file = entry.get("cookies_file")
    path = Path(str(cookies_file)).expanduser() if cookies_file else None
    if path is not None and not path.is_file():
        raise FileNotFoundError(f"Auth profile cookies_file not found: {path}")
    return (str(browser) if browser else None), path


def _open_local_audio(source: str) -> Path:
    path = Path(source).expanduser().resolve()
    if not path.is_file():
        raise ValueError(f"Local source is not a regular file: {path}")
    if path.is_dir():
        raise ValueError(f"Local source is a directory: {path}")
    return path


class ProductionJobRunner:
    """End-to-end job execution for the NLM lock holder."""

    def __init__(
        self,
        *,
        transcripts_root: Path | None = None,
        config: AppConfig | None = None,
        model_cache: ModelCache | None = None,
        download_fn: DownloadFn | None = None,
        # If set, bypasses model cache and Whisper entirely (tests).
        transcribe_text_fn: TranscribeFn | None = None,
        scratch_root: Path | None = None,
    ) -> None:
        self.config = config if config is not None else load_config()
        self.transcripts_root = resolve_transcripts_root(
            config=self.config, override=transcripts_root
        )
        self.scratch_root = scratch_root or default_scratch_root()
        self.download_fn = download_fn or download_audio_and_metadata
        self.transcribe_text_fn = transcribe_text_fn
        if model_cache is not None:
            self.model_cache = model_cache
        else:
            self.model_cache = ModelCache(loader=default_model_loader)

    def scratch_dir_for(self, execution: Execution) -> Path:
        path = self.scratch_root / execution.execution_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def __call__(self, execution: Execution, scratch_dir: Path) -> dict[str, Any]:
        result = self.run(execution, scratch_dir=scratch_dir)
        return result.as_worker_payload()

    def run(
        self,
        execution: Execution,
        *,
        scratch_dir: Path | None = None,
    ) -> JobRunnerResult:
        opts = execution.options
        scratch = Path(scratch_dir) if scratch_dir is not None else self.scratch_dir_for(execution)
        scratch.mkdir(parents=True, exist_ok=True)

        # Ensure scratch is not under the transcript root (best-effort check).
        try:
            scratch.resolve().relative_to(self.transcripts_root.resolve())
            raise RuntimeError(
                f"Scratch directory {scratch} must not be under transcript root "
                f"{self.transcripts_root}"
            )
        except ValueError:
            pass  # not a subpath — good
        except FileNotFoundError:
            pass

        audio_path: Path | None = None
        meta: dict[str, Any]

        if execution.source_type == "youtube":
            browser, cookies_file = resolve_auth_profile(
                opts.auth_profile, config=self.config
            )
            audio_path, meta = self.download_fn(
                url=execution.source,
                outdir=scratch,
                cookies_from_browser=browser,
                cookies_file=str(cookies_file) if cookies_file else None,
                limit_rate=opts.limit_rate,
                sleep_interval_requests=opts.sleep_interval_requests,
            )
        elif execution.source_type == "local_file":
            audio_path = _open_local_audio(execution.source)
            meta = local_file_metadata(audio_path)
        else:
            raise ValueError(f"Unsupported source_type: {execution.source_type!r}")

        assert audio_path is not None

        effective_device = opts.device
        effective_compute = opts.compute_type

        if self.transcribe_text_fn is not None:
            text = self.transcribe_text_fn(
                audio_path,
                model_name=opts.model,
                language=opts.language,
                device=opts.device,
                compute_type=opts.compute_type,
            )
        else:
            # Resolve device once so cache key matches effective runtime.
            effective_device, effective_compute = resolve_device_and_compute(
                opts.device, opts.compute_type
            )
            model = self.model_cache.get(opts.model, effective_device, effective_compute)
            text = transcribe_with_model(
                model,
                audio_path,
                language=opts.language,
            )

        payload = build_output_json(meta, text)
        published = publish_transcript(
            self.transcripts_root,
            source_key=execution.source_key,
            payload=payload,
            generation=execution.generation,
        )

        # Clean local media for YouTube downloads unless keep_audio
        if (
            execution.source_type == "youtube"
            and not opts.keep_audio
            and audio_path is not None
            and audio_path.is_file()
        ):
            try:
                audio_path.unlink()
            except OSError as exc:
                logger.warning("Failed to remove temp audio %s: %s", audio_path, exc)
            # Remove empty scratch dir if possible
            try:
                if scratch.is_dir() and not any(scratch.iterdir()):
                    scratch.rmdir()
            except OSError:
                pass

        return JobRunnerResult(
            output_path=published.path,
            transcript_payload=payload,
            effective_device=effective_device,
            effective_compute_type=effective_compute,
            audio_path=audio_path if opts.keep_audio else None,
        )


def create_default_job_runner(
    *,
    transcripts_root: Path | None = None,
    config: AppConfig | None = None,
) -> ProductionJobRunner:
    """Factory used by CLI / worker entrypoints."""
    return ProductionJobRunner(transcripts_root=transcripts_root, config=config)

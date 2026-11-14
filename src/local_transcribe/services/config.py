"""Application configuration loader for local-transcribe.

Loads ``~/.config/local-transcribe/config.yaml`` (or an explicit path).
Queue path resolution is explicit only — no multi-candidate discovery.
"""

from __future__ import annotations

import os
from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


def default_config_path() -> Path:
    """Return the default user config file path (XDG-aware)."""
    xdg = os.environ.get("XDG_CONFIG_HOME", "").strip()
    if xdg:
        return Path(xdg).expanduser() / "local-transcribe" / "config.yaml"
    return Path.home() / ".config" / "local-transcribe" / "config.yaml"


@dataclass
class QueueConfig:
    """Queue-related settings from config."""

    path: Path | None = None
    expected_uuid: str | None = None
    expected_nfs_version: int | None = 3
    expected_server: str | None = None
    expected_export: str | None = None
    default_auth_profile: str | None = None
    # Auto-import ref-cli's transcript-pending.md into the durable queue.
    legacy_pending_file: Path | None = None
    watch_legacy_pending: bool = True
    # How many workers may process jobs on this queue at once (all hosts).
    # Default 1 keeps the historical single-worker behavior.
    max_workers: int = 1


@dataclass
class WorkerConfig:
    """Per-host worker settings. The queue-wide cap is ``QueueConfig.max_workers``."""

    concurrency: int = 1
    lease_seconds: float = 90.0


@dataclass
class AppConfig:
    """Top-level application configuration."""

    queue: QueueConfig = field(default_factory=QueueConfig)
    worker: WorkerConfig = field(default_factory=WorkerConfig)
    raw: dict[str, Any] = field(default_factory=dict)


_MAX_WORKER_CAP = 64


def _positive_int(value: Any, name: str, default: int, *, maximum: int) -> int:
    """Parse a config integer in ``1..maximum``. Missing means ``default``."""
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer >= 1, got {value!r}")
    if value < 1 or value > maximum:
        raise ValueError(f"{name} must be between 1 and {maximum}, got {value}")
    return value


def _positive_float(value: Any, name: str, default: float) -> float:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a number >= 1, got {value!r}")
    number = float(value)
    if number < 1:
        raise ValueError(f"{name} must be >= 1, got {value!r}")
    return number


def _expand_path(value: str | Path | None) -> Path | None:
    if value is None:
        return None
    return Path(value).expanduser()


def _queue_from_mapping(data: Mapping[str, Any] | None) -> QueueConfig:
    if not data:
        return QueueConfig()

    nfs_version = data.get("expected_nfs_version", 3)
    if nfs_version is not None and not isinstance(nfs_version, int):
        try:
            nfs_version = int(nfs_version)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"queue.expected_nfs_version must be an integer, got {nfs_version!r}"
            ) from exc

    expected_uuid = data.get("expected_uuid")
    if expected_uuid is not None:
        expected_uuid = str(expected_uuid).strip() or None

    default_auth = data.get("default_auth_profile")
    if default_auth is not None:
        default_auth = str(default_auth).strip() or None

    watch_legacy = data.get("watch_legacy_pending", True)
    if not isinstance(watch_legacy, bool):
        watch_legacy = str(watch_legacy).strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }

    legacy_pending = data.get("legacy_pending_file")
    if legacy_pending is not None:
        legacy_pending = str(legacy_pending).strip() or None

    return QueueConfig(
        path=_expand_path(data.get("path")),
        expected_uuid=expected_uuid,
        expected_nfs_version=nfs_version,
        expected_server=(
            str(data["expected_server"]).strip()
            if data.get("expected_server") is not None
            else None
        ),
        expected_export=(
            str(data["expected_export"]).strip()
            if data.get("expected_export") is not None
            else None
        ),
        default_auth_profile=default_auth,
        legacy_pending_file=_expand_path(legacy_pending),
        watch_legacy_pending=bool(watch_legacy),
        max_workers=_positive_int(
            data.get("max_workers"),
            "queue.max_workers",
            1,
            maximum=_MAX_WORKER_CAP,
        ),
    )


def _worker_from_mapping(data: Mapping[str, Any] | None) -> WorkerConfig:
    if not data:
        return WorkerConfig()
    return WorkerConfig(
        concurrency=_positive_int(
            data.get("concurrency"),
            "worker.concurrency",
            1,
            maximum=_MAX_WORKER_CAP,
        ),
        lease_seconds=_positive_float(
            data.get("lease_seconds"),
            "worker.lease_seconds",
            90.0,
        ),
    )


def load_config(config_path: Path | None = None) -> AppConfig:
    """Load application config from YAML.

    Missing config file yields empty defaults (``queue.path`` is ``None``).
    Invalid YAML raises ``ValueError``.
    """
    path = (config_path or default_config_path()).expanduser()
    if not path.is_file():
        return AppConfig()

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"Unable to read config file {path}: {exc}") from exc

    if not text.strip():
        return AppConfig()

    try:
        loaded = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid YAML in config file {path}: {exc}") from exc

    if loaded is None:
        return AppConfig()
    if not isinstance(loaded, MutableMapping):
        raise TypeError(f"Config root must be a mapping, got {type(loaded).__name__}")

    raw = dict(loaded)
    queue_data = raw.get("queue")
    if queue_data is not None and not isinstance(queue_data, Mapping):
        raise TypeError("config key 'queue' must be a mapping")
    worker_data = raw.get("worker")
    if worker_data is not None and not isinstance(worker_data, Mapping):
        raise TypeError("config key 'worker' must be a mapping")

    return AppConfig(
        queue=_queue_from_mapping(queue_data),
        worker=_worker_from_mapping(worker_data),
        raw=raw,
    )


def write_queue_config(
    *,
    queue_dir: Path,
    queue_uuid: str,
    expected_nfs_version: int | None = 3,
    expected_server: str | None = None,
    expected_export: str | None = None,
    config_path: Path | None = None,
) -> Path:
    """Create or update ``config.yaml`` with queue path + UUID.

    Preserves unrelated top-level keys. Overwrites ``queue.path`` and
    ``queue.expected_uuid`` to match the initialized queue. Fills NFS identity
    fields when provided and not already set (or when updating the same path).

    Refuses to write pytest/temp junk paths into the real user config.
    """
    path = (config_path or default_config_path()).expanduser()
    resolved = Path(queue_dir).expanduser().resolve()
    resolved_s = str(resolved)
    # Never pollute the real ~/.config file with pytest paths (ignore XDG overrides).
    real_user_cfg = (Path.home() / ".config" / "local-transcribe" / "config.yaml").resolve()
    if path.resolve() == real_user_cfg and (
        "pytest" in resolved_s or resolved_s.startswith("/tmp/pytest")
    ):
        raise ValueError(
            f"Refusing to write pytest/temp queue path into config: {resolved}"
        )

    path.parent.mkdir(parents=True, exist_ok=True)

    raw: dict[str, Any] = {}
    if path.is_file():
        try:
            loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            loaded = {}
        if isinstance(loaded, MutableMapping):
            raw = dict(loaded)

    queue_raw = raw.get("queue")
    if not isinstance(queue_raw, MutableMapping):
        queue_raw = {}
    else:
        queue_raw = dict(queue_raw)

    queue_raw["path"] = resolved_s
    queue_raw["expected_uuid"] = str(queue_uuid)
    if expected_nfs_version is not None:
        queue_raw["expected_nfs_version"] = int(expected_nfs_version)
    if expected_server:
        queue_raw["expected_server"] = expected_server
    if expected_export:
        queue_raw["expected_export"] = expected_export

    raw["queue"] = queue_raw
    body = yaml.safe_dump(raw, default_flow_style=False, sort_keys=False)
    path.write_text(body, encoding="utf-8")
    return path


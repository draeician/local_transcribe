"""Application configuration loader for local-transcribe.

Loads ``~/.config/local-transcribe/config.yaml`` (or an explicit path).
Queue path resolution is explicit only — no multi-candidate discovery.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, MutableMapping, Optional

import yaml


def default_config_path() -> Path:
    """Return the default user config file path."""
    return Path.home() / ".config" / "local-transcribe" / "config.yaml"


@dataclass
class QueueConfig:
    """Queue-related settings from config."""

    path: Optional[Path] = None
    expected_uuid: Optional[str] = None
    expected_nfs_version: Optional[int] = 3
    expected_server: Optional[str] = None
    expected_export: Optional[str] = None


@dataclass
class AppConfig:
    """Top-level application configuration."""

    queue: QueueConfig = field(default_factory=QueueConfig)
    raw: dict[str, Any] = field(default_factory=dict)


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
        raise ValueError(f"Config root must be a mapping, got {type(loaded).__name__}")

    raw = dict(loaded)
    queue_data = raw.get("queue")
    if queue_data is not None and not isinstance(queue_data, Mapping):
        raise ValueError("config key 'queue' must be a mapping")

    return AppConfig(queue=_queue_from_mapping(queue_data), raw=raw)

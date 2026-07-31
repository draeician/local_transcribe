"""Queue schema models: source reservations and executions (SPEC §6–7)."""

from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal, Mapping, Optional

from local_transcribe.utils.youtube import extract_video_id, is_valid_youtube_url

SCHEMA_VERSION = 1

SourceType = Literal["youtube", "local_file"]
ExecutionStatus = Literal[
    "pending",
    "processing",
    "retry",
    "completed",
    "failed",
    "cancelled",
]

ACTIVE_STATUSES: frozenset[str] = frozenset({"pending", "processing", "retry"})
TERMINAL_STATUSES: frozenset[str] = frozenset({"completed", "failed", "cancelled"})
STATE_DIRS: tuple[str, ...] = (
    "pending",
    "processing",
    "retry",
    "completed",
    "failed",
    "cancelled",
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def new_execution_id() -> str:
    """Return a new execution id (UUID4; documented alternative to UUIDv7)."""
    return str(uuid.uuid4())


def youtube_source_key(video_id: str) -> str:
    return f"youtube:{video_id}"


def local_source_key(path: Path) -> str:
    """Stable local key from canonical path + size + mtime."""
    resolved = path.expanduser().resolve()
    stat = resolved.stat()
    material = f"{resolved}|{stat.st_size}|{stat.st_mtime_ns}"
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]
    return f"local:{digest}"


def normalize_source(source: str) -> tuple[SourceType, str, str]:
    """Return (source_type, source_key, canonical_source_string)."""
    raw = source.strip()
    if is_valid_youtube_url(raw) or (
        not Path(raw).expanduser().exists()
        and ("youtube.com" in raw or "youtu.be" in raw)
    ):
        if not is_valid_youtube_url(raw) and not raw.startswith("https://"):
            # allow bare video ids only if extract works on constructed URL later
            pass
        video_id = extract_video_id(raw)
        if not video_id or video_id == raw and "http" not in raw:
            # try as video id
            if re.fullmatch(r"[\w-]{11}", raw):
                video_id = raw
            elif video_id == raw and "youtube" not in raw and "youtu.be" not in raw:
                raise ValueError(f"Cannot derive YouTube video id from: {source!r}")
        if not video_id or len(video_id) < 6:
            raise ValueError(f"Cannot derive YouTube video id from: {source!r}")
        return "youtube", youtube_source_key(video_id), raw

    path = Path(raw).expanduser()
    if path.exists() and path.is_file():
        key = local_source_key(path)
        return "local_file", key, str(path.resolve())

    if is_valid_youtube_url(raw):
        video_id = extract_video_id(raw)
        if not video_id:
            raise ValueError(f"Cannot derive YouTube video id from: {source!r}")
        return "youtube", youtube_source_key(video_id), raw

    raise ValueError(
        f"Source is neither a valid YouTube URL nor an existing local file: {source!r}"
    )


def source_key_filename(source_key: str) -> str:
    """Filesystem-safe filename for keys/<source-key>.json."""
    # Encode colon and other unsafe chars.
    safe = (
        source_key.replace(":", "__")
        .replace("/", "_")
        .replace("\\", "_")
        .replace(" ", "_")
    )
    safe = re.sub(r"[^A-Za-z0-9._+-]", "_", safe)
    return f"{safe}.json"


@dataclass
class ExecutionOptions:
    model: str = "medium"
    device: str = "cuda"
    compute_type: str = "float16"
    language: Optional[str] = None
    keep_audio: bool = False
    auth_profile: Optional[str] = None
    limit_rate: Optional[str] = None
    sleep_interval_requests: Optional[float] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> "ExecutionOptions":
        if not data:
            return cls()
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass
class ExecutionError:
    category: str
    message: str
    retryable: bool = False
    occurred_at: Optional[str] = None
    next_retry_at: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> Optional["ExecutionError"]:
        if not data:
            return None
        return cls(
            category=str(data.get("category", "internal_error")),
            message=str(data.get("message", "")),
            retryable=bool(data.get("retryable", False)),
            occurred_at=data.get("occurred_at"),
            next_retry_at=data.get("next_retry_at"),
        )


@dataclass
class SourceReservation:
    source_key: str
    source_type: SourceType
    current_execution_id: str
    generation: int = 1
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)
    schema_version: int = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source_key": self.source_key,
            "source_type": self.source_type,
            "current_execution_id": self.current_execution_id,
            "generation": self.generation,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SourceReservation":
        return cls(
            schema_version=int(data.get("schema_version", SCHEMA_VERSION)),
            source_key=str(data["source_key"]),
            source_type=data["source_type"],  # type: ignore[arg-type]
            current_execution_id=str(data["current_execution_id"]),
            generation=int(data.get("generation", 1)),
            created_at=str(data.get("created_at", utc_now_iso())),
            updated_at=str(data.get("updated_at", utc_now_iso())),
        )


@dataclass
class Execution:
    execution_id: str
    source_key: str
    generation: int
    source: str
    source_type: SourceType
    origin: str = "lt-transcribe"
    priority: int = 50
    status: ExecutionStatus = "pending"
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)
    available_at: str = field(default_factory=utc_now_iso)
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    attempts: int = 0
    max_attempts: int = 3
    worker: Optional[str] = None
    required_host: Optional[str] = None
    output_path: Optional[str] = None
    error: Optional[ExecutionError] = None
    options: ExecutionOptions = field(default_factory=ExecutionOptions)
    schema_version: int = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "execution_id": self.execution_id,
            "source_key": self.source_key,
            "generation": self.generation,
            "source": self.source,
            "source_type": self.source_type,
            "origin": self.origin,
            "priority": self.priority,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "available_at": self.available_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "attempts": self.attempts,
            "max_attempts": self.max_attempts,
            "worker": self.worker,
            "required_host": self.required_host,
            "output_path": self.output_path,
            "error": self.error.to_dict() if self.error else None,
            "options": self.options.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Execution":
        return cls(
            schema_version=int(data.get("schema_version", SCHEMA_VERSION)),
            execution_id=str(data["execution_id"]),
            source_key=str(data["source_key"]),
            generation=int(data.get("generation", 1)),
            source=str(data["source"]),
            source_type=data["source_type"],  # type: ignore[arg-type]
            origin=str(data.get("origin", "lt-transcribe")),
            priority=int(data.get("priority", 50)),
            status=data.get("status", "pending"),  # type: ignore[arg-type]
            created_at=str(data.get("created_at", utc_now_iso())),
            updated_at=str(data.get("updated_at", utc_now_iso())),
            available_at=str(data.get("available_at", utc_now_iso())),
            started_at=data.get("started_at"),
            completed_at=data.get("completed_at"),
            attempts=int(data.get("attempts", 0)),
            max_attempts=int(data.get("max_attempts", 3)),
            worker=data.get("worker"),
            required_host=data.get("required_host"),
            output_path=data.get("output_path"),
            error=ExecutionError.from_dict(data.get("error")),
            options=ExecutionOptions.from_dict(data.get("options")),
        )


# Optional hook type for transcript existence checks
TranscriptChecker = Callable[[str], Optional[Path]]

"""Diagnostic worker state.json (not ownership authority)."""

from __future__ import annotations

import json
import os
import socket
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any, Optional

from local_transcribe.services.atomic_files import atomic_write_json
from local_transcribe.services.queue_models import utc_now_iso


@dataclass
class WorkerState:
    worker_id: str
    hostname: str
    pid: int
    queue_uuid: str
    started_at: str
    heartbeat_at: str
    current_execution_id: Optional[str] = None
    active: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def create(cls, queue_uuid: str, execution_id: str | None = None) -> "WorkerState":
        host = socket.gethostname()
        pid = os.getpid()
        now = utc_now_iso()
        return cls(
            worker_id=f"{host}-{pid}",
            hostname=host,
            pid=pid,
            queue_uuid=queue_uuid,
            started_at=now,
            heartbeat_at=now,
            current_execution_id=execution_id,
            active=True,
        )


def state_path(queue_dir: Path) -> Path:
    return Path(queue_dir) / "worker" / "state.json"


def write_worker_state(queue_dir: Path, state: WorkerState) -> None:
    atomic_write_json(state_path(queue_dir), state.to_dict())


def read_worker_state(queue_dir: Path) -> WorkerState | None:
    """Load diagnostic worker state if present and parseable."""
    path = state_path(queue_dir)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    known = {f.name for f in fields(WorkerState)}
    try:
        return WorkerState(**{k: v for k, v in data.items() if k in known})
    except TypeError:
        return None


def mark_worker_inactive(queue_dir: Path, state: WorkerState) -> None:
    state.active = False
    state.current_execution_id = None
    state.heartbeat_at = utc_now_iso()
    write_worker_state(queue_dir, state)

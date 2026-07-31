"""Permanent source reservations under keys/ (SPEC §6.4–6.5)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from local_transcribe.services.atomic_files import (
    atomic_write_json,
    link_publish_json,
)
from local_transcribe.services.queue_models import (
    SourceReservation,
    SourceType,
    new_execution_id,
    source_key_filename,
    utc_now_iso,
)


class ReservationError(RuntimeError):
    """Reservation operation failed."""


def keys_dir(queue_dir: Path) -> Path:
    return queue_dir / "keys"


def reservation_path(queue_dir: Path, source_key: str) -> Path:
    return keys_dir(queue_dir) / source_key_filename(source_key)


def read_reservation(queue_dir: Path, source_key: str) -> Optional[SourceReservation]:
    path = reservation_path(queue_dir, source_key)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None
    try:
        return SourceReservation.from_dict(data)
    except (KeyError, TypeError, ValueError):
        return None


def create_reservation_if_absent(
    queue_dir: Path,
    *,
    source_key: str,
    source_type: SourceType,
    execution_id: str | None = None,
    generation: int = 1,
) -> tuple[SourceReservation, bool]:
    """Create reservation via link() publish.

    Returns:
        (reservation, created) where created is True if this call published.
    """
    keys_dir(queue_dir).mkdir(parents=True, exist_ok=True)
    exec_id = execution_id or new_execution_id()
    now = utc_now_iso()
    reservation = SourceReservation(
        source_key=source_key,
        source_type=source_type,
        current_execution_id=exec_id,
        generation=generation,
        created_at=now,
        updated_at=now,
    )
    dest = reservation_path(queue_dir, source_key)
    tmp_dir = queue_dir / "tmp"
    created = link_publish_json(dest, reservation.to_dict(), tmp_dir=tmp_dir)
    if created:
        return reservation, True
    existing = read_reservation(queue_dir, source_key)
    if existing is None:
        raise ReservationError(
            f"Reservation destination exists but is unreadable: {dest}"
        )
    return existing, False


def advance_generation(
    queue_dir: Path,
    source_key: str,
    *,
    new_execution_id: str,
) -> SourceReservation:
    """Bump generation and point reservation at a new execution (force)."""
    current = read_reservation(queue_dir, source_key)
    if current is None:
        raise ReservationError(f"No reservation for source_key={source_key!r}")
    updated = SourceReservation(
        source_key=current.source_key,
        source_type=current.source_type,
        current_execution_id=new_execution_id,
        generation=current.generation + 1,
        created_at=current.created_at,
        updated_at=utc_now_iso(),
        schema_version=current.schema_version,
    )
    atomic_write_json(reservation_path(queue_dir, source_key), updated.to_dict())
    return updated


def try_advance_generation(
    queue_dir: Path,
    source_key: str,
    *,
    expected_generation: int,
    new_execution_id: str,
) -> SourceReservation | None:
    """Advance generation only if current generation still matches expectation.

    After the write, re-reads and returns the reservation only when this
    caller's ``new_execution_id`` is the current pointer (won the race).
    """
    current = read_reservation(queue_dir, source_key)
    if current is None:
        return None
    if current.generation != expected_generation:
        return None
    updated = SourceReservation(
        source_key=current.source_key,
        source_type=current.source_type,
        current_execution_id=new_execution_id,
        generation=current.generation + 1,
        created_at=current.created_at,
        updated_at=utc_now_iso(),
        schema_version=current.schema_version,
    )
    atomic_write_json(reservation_path(queue_dir, source_key), updated.to_dict())
    after = read_reservation(queue_dir, source_key)
    if after is None:
        return None
    # Pointer ownership is the win condition. Generation may move again under
    # concurrent force; still treat matching current_execution_id as success.
    if after.current_execution_id == new_execution_id:
        return after
    return None


def update_current_execution(
    queue_dir: Path,
    source_key: str,
    *,
    execution_id: str,
    generation: int | None = None,
) -> SourceReservation:
    """Update current_execution_id (and optionally generation) in place."""
    current = read_reservation(queue_dir, source_key)
    if current is None:
        raise ReservationError(f"No reservation for source_key={source_key!r}")
    updated = SourceReservation(
        source_key=current.source_key,
        source_type=current.source_type,
        current_execution_id=execution_id,
        generation=generation if generation is not None else current.generation,
        created_at=current.created_at,
        updated_at=utc_now_iso(),
        schema_version=current.schema_version,
    )
    atomic_write_json(reservation_path(queue_dir, source_key), updated.to_dict())
    return updated


def try_update_current_execution(
    queue_dir: Path,
    source_key: str,
    *,
    expected_execution_id: str,
    new_execution_id: str,
    expected_generation: int | None = None,
) -> SourceReservation | None:
    """Point reservation at ``new_execution_id`` if still at ``expected_execution_id``.

    Returns the updated reservation on win, else None (lost race / changed).
    """
    current = read_reservation(queue_dir, source_key)
    if current is None:
        return None
    if current.current_execution_id != expected_execution_id:
        return None
    if (
        expected_generation is not None
        and current.generation != expected_generation
    ):
        return None
    updated = SourceReservation(
        source_key=current.source_key,
        source_type=current.source_type,
        current_execution_id=new_execution_id,
        generation=current.generation,
        created_at=current.created_at,
        updated_at=utc_now_iso(),
        schema_version=current.schema_version,
    )
    atomic_write_json(reservation_path(queue_dir, source_key), updated.to_dict())
    after = read_reservation(queue_dir, source_key)
    if after is None or after.current_execution_id != new_execution_id:
        return None
    return after

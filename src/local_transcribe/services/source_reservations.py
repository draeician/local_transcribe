"""Permanent source reservations under keys/ (SPEC §6.4–6.5).

``keys/<source-key>.json`` is the single authoritative reservation document.
Existing local-transcribe clients and the ref reconciler read that file
directly, so this module never introduces a second durable authority.

Concurrent generation advances are serialized with a short-lived advisory lock
under ``.locks/reservations/``. The lock is coordination only; the reservation
document remains the source of truth.

Compatibility limit: the advisory lock coordinates only writers that take it.
An older client that writes ``keys/<source>.json`` directly can still write
after a locked writer's readback. The readback detects a race that happens
before it (including a non-cooperating write that lands inside the locked
section), but it does not detect a non-cooperating write that happens after
it. Mixed-version producer operation is an unresolved deployment constraint;
older writers must not run concurrently with this version.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from local_transcribe.services.atomic_files import (
    atomic_write_json,
    link_publish_json,
)
from local_transcribe.services.queue_locks import (
    queue_lock,
    reservation_lock_path,
)
from local_transcribe.services.queue_models import (
    SourceReservation,
    SourceType,
    new_execution_id,
    source_key_filename,
    utc_now_iso,
)


@dataclass(frozen=True)
class GenerationAdvance:
    """Result of a compare-and-swap generation advance."""

    won: bool
    reservation: SourceReservation | None
    winner_execution_id: str


class ReservationError(RuntimeError):
    """Reservation operation failed."""


def keys_dir(queue_dir: Path) -> Path:
    return queue_dir / "keys"


def reservation_path(queue_dir: Path, source_key: str) -> Path:
    return keys_dir(queue_dir) / source_key_filename(source_key)


def read_reservation(queue_dir: Path, source_key: str) -> SourceReservation | None:
    """Return the authoritative reservation from ``keys/<source>.json``.

    The document is written with ``atomic_write_json`` (unique temp + replace),
    so a reader sees either the previous complete document or the next complete
    document. Direct key-file readers therefore observe the same authority.
    """
    path = reservation_path(queue_dir, source_key)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    try:
        reservation = SourceReservation.from_dict(data)
    except (KeyError, TypeError, ValueError):
        return None
    if reservation.source_key != source_key:
        return None
    if not reservation.current_execution_id:
        return None
    if reservation.generation < 1:
        return None
    if reservation.source_type not in ("youtube", "local_file"):
        return None
    return reservation


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
    existing = read_reservation(queue_dir, source_key)
    if existing is not None:
        return existing, False

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
    with queue_lock(reservation_lock_path(queue_dir, source_key)):
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
        after = read_reservation(queue_dir, source_key)
        if after is None:
            raise ReservationError(
                f"Reservation disappeared after advance for source_key={source_key!r}"
            )
        return after


def try_advance_generation(
    queue_dir: Path,
    source_key: str,
    *,
    expected_generation: int,
    new_execution_id: str,
) -> GenerationAdvance | None:
    """Compare-and-swap the authoritative reservation to ``expected + 1``.

    The advisory lock serializes the read-modify-write sequence, so exactly one
    concurrent cooperating caller can advance a given generation. After writing,
    the document is re-read to confirm this caller still owns the pointer. That
    readback detects a cooperating writer that raced inside the locked section
    and a non-cooperating legacy write that landed before the readback; it
    cannot detect a non-cooperating legacy write that lands after the readback.
    Mixed-version producer operation remains an unresolved deployment
    constraint.

    Returns ``None`` when the reservation is missing or no longer at
    ``expected_generation``. On a lost race, ``winner_execution_id`` names the
    execution the caller should attach to instead of overwriting the pointer.
    """
    with queue_lock(reservation_lock_path(queue_dir, source_key)):
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
            return GenerationAdvance(
                won=False,
                reservation=None,
                winner_execution_id=current.current_execution_id,
            )

        won = (
            after.generation == expected_generation + 1
            and after.current_execution_id == new_execution_id
        )
        return GenerationAdvance(
            won=won,
            reservation=after if won else None,
            winner_execution_id=after.current_execution_id,
        )


def update_current_execution(
    queue_dir: Path,
    source_key: str,
    *,
    execution_id: str,
    generation: int | None = None,
) -> SourceReservation:
    """Update current_execution_id (and optionally generation) in place."""
    with queue_lock(reservation_lock_path(queue_dir, source_key)):
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
        after = read_reservation(queue_dir, source_key)
        if after is None:
            raise ReservationError(
                f"Reservation disappeared after update for source_key={source_key!r}"
            )
        return after


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
    The post-write readback has the same limit as ``try_advance_generation()``:
    it cannot detect a non-cooperating older writer that writes after the
    readback.
    """
    with queue_lock(reservation_lock_path(queue_dir, source_key)):
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
        if (
            expected_generation is not None
            and after.generation != expected_generation
        ):
            return None
        return after

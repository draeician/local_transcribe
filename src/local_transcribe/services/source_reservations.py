"""Permanent source reservations under keys/ (SPEC §6.4–6.5)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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


def _read_materialized_reservation(
    queue_dir: Path, source_key: str
) -> SourceReservation | None:
    """Read the mutable ``keys/<source>.json`` cache (not the authority)."""
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


def read_reservation(queue_dir: Path, source_key: str) -> SourceReservation | None:
    """Return the authoritative reservation for ``source_key``.

    Generation advances are recorded as immutable, create-if-absent claim files
    under ``keys/.advance/``. A claim is the authority for its generation, while
    ``keys/<source>.json`` is a materialized cache that may briefly lag or be
    overwritten by a slow writer. Consulting the highest claim makes the
    reservation monotonic: a stale writer can never move readers back to an
    older generation, and an interrupted producer's claim is still visible.
    """
    materialized = _read_materialized_reservation(queue_dir, source_key)
    claim = _read_highest_advance_claim(queue_dir, source_key)
    if claim is None:
        return materialized

    claim_generation = int(claim["generation"])
    if materialized is not None and materialized.generation > claim_generation:
        return materialized

    source_type = claim.get("source_type")
    if source_type not in ("youtube", "local_file"):
        source_type = materialized.source_type if materialized is not None else None
    if source_type is None:
        return materialized

    claimed_at = str(claim.get("claimed_at", utc_now_iso()))
    return SourceReservation(
        source_key=source_key,
        source_type=source_type,
        current_execution_id=str(claim["new_execution_id"]),
        generation=claim_generation,
        created_at=(
            materialized.created_at if materialized is not None else claimed_at
        ),
        updated_at=claimed_at,
        schema_version=(
            materialized.schema_version if materialized is not None else 1
        ),
    )


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
    # A generation claim can outlive a missing/lagging materialized file; never
    # shadow it with a fresh generation-1 reservation.
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
) -> GenerationAdvance | None:
    """Compare-and-swap the reservation to ``expected_generation + 1``.

    Exactly one concurrent caller can claim a given generation: the claim is an
    immutable file published under ``keys/.advance/`` with hard ``link()``
    (create-if-absent). Claims are never deleted, so the highest claim is the
    durable authority even if a producer dies between claiming and writing the
    materialized reservation cache.

    Returns ``None`` when the reservation is missing or no longer at
    ``expected_generation``. On a lost claim, ``winner_execution_id`` names the
    execution the caller should attach to instead of overwriting the pointer.
    """
    current = read_reservation(queue_dir, source_key)
    if current is None:
        return None
    if current.generation != expected_generation:
        return None

    claim_generation = expected_generation + 1
    claim_path = _advance_claim_path(queue_dir, source_key, claim_generation)
    claim_payload: dict[str, Any] = {
        "schema_version": 1,
        "source_key": source_key,
        "source_type": current.source_type,
        "generation": claim_generation,
        "expected_execution_id": current.current_execution_id,
        "new_execution_id": new_execution_id,
        "claimed_at": utc_now_iso(),
    }
    won_claim = link_publish_json(
        claim_path, claim_payload, tmp_dir=queue_dir / "tmp"
    )

    claim = _read_advance_claim(claim_path)
    if claim is None:
        # The claim vanished or is unreadable; fall back to the current
        # authority rather than inventing a winner.
        after = read_reservation(queue_dir, source_key)
        fallback = (
            after.current_execution_id
            if after is not None
            else current.current_execution_id
        )
        return GenerationAdvance(
            won=False, reservation=None, winner_execution_id=fallback
        )

    winner_id = str(claim["new_execution_id"])

    # Materialize the claim only while it is the highest generation. This file
    # is a cache: a slow writer that lands after a newer claim cannot move
    # readers backwards because read_reservation consults the claim ledger.
    highest = _read_highest_advance_claim(queue_dir, source_key)
    if highest is not None and int(highest["generation"]) == claim_generation:
        materialized = _read_materialized_reservation(queue_dir, source_key)
        if materialized is None or materialized.generation <= claim_generation:
            updated = SourceReservation(
                source_key=source_key,
                source_type=current.source_type,
                current_execution_id=winner_id,
                generation=claim_generation,
                created_at=(
                    materialized.created_at
                    if materialized is not None
                    else current.created_at
                ),
                updated_at=str(claim.get("claimed_at", utc_now_iso())),
                schema_version=(
                    materialized.schema_version
                    if materialized is not None
                    else current.schema_version
                ),
            )
            atomic_write_json(
                reservation_path(queue_dir, source_key), updated.to_dict()
            )

    latest = read_reservation(queue_dir, source_key)
    committed = (
        latest is not None
        and latest.generation == claim_generation
        and latest.current_execution_id == winner_id
    )
    won = won_claim and committed
    return GenerationAdvance(
        won=won,
        reservation=latest if won else None,
        winner_execution_id=winner_id,
    )


def _advance_claims_dir(queue_dir: Path) -> Path:
    return keys_dir(queue_dir) / ".advance"


def _advance_claim_path(
    queue_dir: Path, source_key: str, generation: int
) -> Path:
    return (
        _advance_claims_dir(queue_dir)
        / f"{source_key_filename(source_key)}.gen-{generation}.json"
    )


def _read_advance_claim(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    if not isinstance(data.get("new_execution_id"), str):
        return None
    generation = data.get("generation")
    if not isinstance(generation, int):
        return None
    return data


def _read_highest_advance_claim(
    queue_dir: Path, source_key: str
) -> dict[str, Any] | None:
    """Return the highest-generation immutable claim for ``source_key``."""
    directory = _advance_claims_dir(queue_dir)
    if not directory.is_dir():
        return None
    prefix = f"{source_key_filename(source_key)}.gen-"
    highest: dict[str, Any] | None = None
    highest_generation = -1
    for path in directory.glob(f"{prefix}*.json"):
        claim = _read_advance_claim(path)
        if claim is None:
            continue
        generation = int(claim["generation"])
        if generation > highest_generation:
            highest = claim
            highest_generation = generation
    return highest


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

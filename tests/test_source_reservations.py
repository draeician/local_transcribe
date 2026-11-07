"""Tests for source reservations (task 014)."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from local_transcribe.services.atomic_files import atomic_write_json
from local_transcribe.services.queue_models import SourceReservation
from local_transcribe.services.queue_paths import initialize_queue_layout
from local_transcribe.services.source_reservations import (
    advance_generation,
    create_reservation_if_absent,
    read_reservation,
    reservation_path,
    try_advance_generation,
)


def test_create_reservation_once(tmp_path: Path) -> None:
    queue = tmp_path / "q"
    initialize_queue_layout(queue)
    r1, c1 = create_reservation_if_absent(
        queue, source_key="youtube:abcd1234567", source_type="youtube", execution_id="e1"
    )
    r2, c2 = create_reservation_if_absent(
        queue, source_key="youtube:abcd1234567", source_type="youtube", execution_id="e2"
    )
    assert c1 is True
    assert c2 is False
    assert r1.current_execution_id == "e1"
    assert r2.current_execution_id == "e1"
    assert r2.generation == 1


def test_concurrent_reservation_one_winner(tmp_path: Path) -> None:
    queue = tmp_path / "q"
    initialize_queue_layout(queue)
    key = "youtube:racevideo1"

    def attempt(i: int) -> bool:
        _, created = create_reservation_if_absent(
            queue,
            source_key=key,
            source_type="youtube",
            execution_id=f"exec-{i}",
        )
        return created

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(attempt, range(8)))
    assert sum(1 for r in results if r) == 1
    res = read_reservation(queue, key)
    assert res is not None
    assert res.generation == 1


def test_advance_generation(tmp_path: Path) -> None:
    queue = tmp_path / "q"
    initialize_queue_layout(queue)
    create_reservation_if_absent(
        queue, source_key="youtube:vid", source_type="youtube", execution_id="e1"
    )
    updated = advance_generation(queue, "youtube:vid", new_execution_id="e2")
    assert updated.generation == 2
    assert updated.current_execution_id == "e2"


def test_generation_claims_are_authoritative_over_stale_materialized_write(
    tmp_path: Path,
) -> None:
    """A slow writer cannot move readers back to an older generation."""
    queue = tmp_path / "q"
    initialize_queue_layout(queue)
    key = "youtube:vid"
    create_reservation_if_absent(
        queue, source_key=key, source_type="youtube", execution_id="e1"
    )

    second = try_advance_generation(
        queue, key, expected_generation=1, new_execution_id="e2"
    )
    assert second is not None and second.won
    third = try_advance_generation(
        queue, key, expected_generation=2, new_execution_id="e3"
    )
    assert third is not None and third.won

    # Simulate a delayed writer that materializes generation 2 after generation
    # 3 was claimed. The immutable claim ledger must still win.
    stale = SourceReservation(
        source_key=key,
        source_type="youtube",
        current_execution_id="e2",
        generation=2,
    )
    atomic_write_json(reservation_path(queue, key), stale.to_dict())

    res = read_reservation(queue, key)
    assert res is not None
    assert res.generation == 3
    assert res.current_execution_id == "e3"


def test_claim_is_visible_without_materialized_reservation(tmp_path: Path) -> None:
    """A producer that dies before materializing its claim is still recoverable."""
    queue = tmp_path / "q"
    initialize_queue_layout(queue)
    key = "youtube:vid"
    create_reservation_if_absent(
        queue, source_key=key, source_type="youtube", execution_id="e1"
    )
    advanced = try_advance_generation(
        queue, key, expected_generation=1, new_execution_id="e2"
    )
    assert advanced is not None and advanced.won

    reservation_path(queue, key).unlink()
    res = read_reservation(queue, key)
    assert res is not None
    assert res.generation == 2
    assert res.current_execution_id == "e2"
    assert res.source_type == "youtube"

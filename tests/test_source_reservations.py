"""Tests for source reservations (task 014, task 044 contract)."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from local_transcribe.services import atomic_files
from local_transcribe.services.atomic_files import atomic_write_json
from local_transcribe.services.queue_models import SourceReservation
from local_transcribe.services.queue_paths import initialize_queue_layout
from local_transcribe.services.source_reservations import (
    advance_generation,
    create_reservation_if_absent,
    read_reservation,
    reservation_path,
    try_advance_generation,
    try_update_current_execution,
    update_current_execution,
)


def _direct(path: Path) -> SourceReservation:
    """Read keys/<source>.json exactly as an existing client would."""
    return SourceReservation.from_dict(json.loads(path.read_text(encoding="utf-8")))


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
    assert _direct(reservation_path(queue, "youtube:vid")) == updated


def test_concurrent_generation_advance_has_one_winner(tmp_path: Path) -> None:
    queue = tmp_path / "q"
    initialize_queue_layout(queue)
    key = "youtube:vid"
    create_reservation_if_absent(
        queue, source_key=key, source_type="youtube", execution_id="e1"
    )

    def attempt(i: int):
        return try_advance_generation(
            queue, key, expected_generation=1, new_execution_id=f"e{i}"
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(attempt, range(8)))

    winners = [result for result in results if result is not None and result.won]
    assert len(winners) == 1
    winner = winners[0]
    assert winner.reservation is not None
    assert winner.reservation.generation == 2
    assert winner.reservation.current_execution_id == winner.winner_execution_id

    direct = _direct(reservation_path(queue, key))
    app = read_reservation(queue, key)
    assert direct == app
    assert direct.generation == 2
    assert direct.current_execution_id == winner.winner_execution_id


def test_keys_file_is_authority_for_direct_reader(tmp_path: Path) -> None:
    """A stale unlocked write is what direct readers see; keys is the contract."""
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

    # An old client that does not take the advisory lock writes directly. The
    # application must agree with the direct reader rather than consult a
    # separate ledger.
    stale = SourceReservation(
        source_key=key,
        source_type="youtube",
        current_execution_id="e2",
        generation=2,
    )
    atomic_write_json(reservation_path(queue, key), stale.to_dict())

    direct = _direct(reservation_path(queue, key))
    app = read_reservation(queue, key)
    assert direct == app
    assert app is not None
    assert app.generation == 2
    assert app.current_execution_id == "e2"


def test_missing_keys_file_means_no_reservation(tmp_path: Path) -> None:
    queue = tmp_path / "q"
    initialize_queue_layout(queue)
    key = "youtube:vid"
    create_reservation_if_absent(
        queue, source_key=key, source_type="youtube", execution_id="e1"
    )
    reservation_path(queue, key).unlink()
    assert read_reservation(queue, key) is None


def test_interrupted_reservation_write_leaves_previous_keys_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    queue = tmp_path / "q"
    initialize_queue_layout(queue)
    key = "youtube:vid"
    create_reservation_if_absent(
        queue, source_key=key, source_type="youtube", execution_id="e1"
    )
    before = _direct(reservation_path(queue, key))

    def fail_replace(*_args: object, **_kwargs: object) -> None:
        raise OSError("injected: replace before publish")

    monkeypatch.setattr(atomic_files.os, "replace", fail_replace)
    with pytest.raises(OSError, match="injected"):
        try_advance_generation(
            queue, key, expected_generation=1, new_execution_id="e2"
        )
    monkeypatch.undo()

    direct = _direct(reservation_path(queue, key))
    app = read_reservation(queue, key)
    assert direct == app == before
    assert app is not None
    assert app.generation == 1
    assert app.current_execution_id == "e1"


def test_update_helpers_after_generation_advance(tmp_path: Path) -> None:
    queue = tmp_path / "q"
    initialize_queue_layout(queue)
    key = "youtube:vid"
    path = reservation_path(queue, key)
    create_reservation_if_absent(
        queue, source_key=key, source_type="youtube", execution_id="e1"
    )
    advanced = try_advance_generation(
        queue, key, expected_generation=1, new_execution_id="e2"
    )
    assert advanced is not None and advanced.won
    assert _direct(path).current_execution_id == "e2"

    updated = update_current_execution(queue, key, execution_id="e3")
    assert updated.generation == 2
    assert updated.current_execution_id == "e3"
    assert _direct(path) == updated

    repaired = try_update_current_execution(
        queue,
        key,
        expected_execution_id="e3",
        new_execution_id="e4",
        expected_generation=2,
    )
    assert repaired is not None
    assert repaired.generation == 2
    assert repaired.current_execution_id == "e4"
    assert _direct(path) == repaired

    lost = try_update_current_execution(
        queue,
        key,
        expected_execution_id="wrong-id",
        new_execution_id="e5",
        expected_generation=2,
    )
    assert lost is None
    assert _direct(path).current_execution_id == "e4"

    next_generation = advance_generation(queue, key, new_execution_id="e6")
    assert next_generation.generation == 3
    assert next_generation.current_execution_id == "e6"
    assert _direct(path) == next_generation

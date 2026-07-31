"""Tests for source reservations (task 014)."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from local_transcribe.services.queue_paths import initialize_queue_layout
from local_transcribe.services.source_reservations import (
    advance_generation,
    create_reservation_if_absent,
    read_reservation,
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

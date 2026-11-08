"""Deterministic claim-versus-cancel interleaving tests (task 044)."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from local_transcribe.services import atomic_files
from local_transcribe.services.atomic_files import AtomicFileError
from local_transcribe.services.queue_paths import initialize_queue_layout
from local_transcribe.services.queue_store import QueueStore, QueueStoreError
from local_transcribe.services.worker import claim_execution


def _enqueue(queue: Path, suffix: str = "a"):
    store = QueueStore(queue)
    result = store.enqueue(f"https://youtu.be/{suffix * 11}", priority=100)
    assert result.execution is not None
    return store, result.execution


def test_worker_claims_first_cancel_fails_closed(tmp_path: Path) -> None:
    queue = tmp_path / "q"
    initialize_queue_layout(queue)
    store, execution = _enqueue(queue)
    eid = execution.execution_id

    claimed = claim_execution(queue, execution)
    assert claimed is not None
    assert (queue / "processing" / f"{eid}.json").is_file()

    with pytest.raises(QueueStoreError, match="processing"):
        store.cancel_pending(eid)

    assert (queue / "processing" / f"{eid}.json").is_file()
    assert not (queue / "cancelled" / f"{eid}.json").exists()
    assert not (queue / "pending" / f"{eid}.json").exists()


def test_cancel_wins_first_worker_does_not_execute(tmp_path: Path) -> None:
    queue = tmp_path / "q"
    initialize_queue_layout(queue)
    store, execution = _enqueue(queue, "b")
    eid = execution.execution_id

    cancelled = store.cancel_pending(eid)
    assert cancelled.status == "cancelled"
    assert (queue / "cancelled" / f"{eid}.json").is_file()

    assert claim_execution(queue, execution) is None
    assert not (queue / "processing" / f"{eid}.json").exists()
    assert not (queue / "pending" / f"{eid}.json").exists()
    assert (queue / "cancelled" / f"{eid}.json").is_file()


def test_interrupted_cancel_is_idempotent_and_removes_pending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    queue = tmp_path / "q"
    initialize_queue_layout(queue)
    store, execution = _enqueue(queue, "c")
    eid = execution.execution_id
    pending = queue / "pending" / f"{eid}.json"

    real_unlink = Path.unlink

    def selective_unlink(self: Path, *args: object, **kwargs: object) -> None:
        if self == pending:
            raise OSError("injected: after publish before source unlink")
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", selective_unlink)
    with pytest.raises(AtomicFileError):
        store.cancel_pending(eid)
    monkeypatch.undo()

    assert pending.is_file()
    assert (queue / "cancelled" / f"{eid}.json").is_file()

    recovered = store.cancel_pending(eid)
    assert recovered.status == "cancelled"
    assert not pending.exists()
    assert (queue / "cancelled" / f"{eid}.json").is_file()


def test_claim_paused_after_publish_blocks_cancel_until_claim_finishes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Controlled interleaving: cancel must not complete while claim is paused."""
    queue = tmp_path / "q"
    initialize_queue_layout(queue)
    store, execution = _enqueue(queue, "e")
    eid = execution.execution_id

    real_link = atomic_files.link_publish_json
    paused = threading.Event()
    release = threading.Event()

    def paused_link(dest, data, *, tmp_dir=None):
        published = real_link(dest, data, tmp_dir=tmp_dir)
        if Path(dest).parent.name == "processing":
            paused.set()
            assert release.wait(timeout=10)
        return published

    monkeypatch.setattr(atomic_files, "link_publish_json", paused_link)

    claim_result: list[object] = []

    def do_claim() -> None:
        claim_result.append(claim_execution(queue, execution))

    claim_thread = threading.Thread(target=do_claim)
    claim_thread.start()
    assert paused.wait(timeout=5), "claim did not reach the publish window"

    cancel_started = threading.Event()
    cancel_done = threading.Event()
    cancel_outcome: list[str] = []

    def do_cancel() -> None:
        cancel_started.set()
        try:
            store.cancel_pending(eid)
            cancel_outcome.append("cancelled")
        except QueueStoreError:
            cancel_outcome.append("refused")
        finally:
            cancel_done.set()

    cancel_thread = threading.Thread(target=do_cancel)
    cancel_thread.start()
    assert cancel_started.wait(timeout=5)

    # On the rejected tree, cancel completes while the claim is paused and
    # leaves both processing/ and cancelled/. On the corrected tree it blocks
    # on the shared execution lock and cannot complete early.
    cancel_completed_early = cancel_done.wait(timeout=1.0)
    release.set()
    claim_thread.join(timeout=15)
    cancel_thread.join(timeout=15)

    assert not cancel_completed_early, (
        "cancel completed while the claim was paused: claim/cancel are not exclusive"
    )

    processing = (queue / "processing" / f"{eid}.json").is_file()
    cancelled = (queue / "cancelled" / f"{eid}.json").is_file()
    pending = (queue / "pending" / f"{eid}.json").is_file()
    assert not pending
    assert processing != cancelled
    assert claim_result and claim_result[0] is not None
    assert cancel_outcome == ["refused"]


def _race_once(
    queue: Path,
    store: QueueStore,
    execution,
    execution_id: str,
) -> list[str]:
    barrier = threading.Barrier(2)
    outcomes: list[str] = []

    def do_claim() -> None:
        barrier.wait(timeout=10)
        claimed = claim_execution(queue, execution)
        outcomes.append("claimed" if claimed is not None else "claim_lost")

    def do_cancel() -> None:
        barrier.wait(timeout=10)
        try:
            store.cancel_pending(execution_id)
            outcomes.append("cancelled")
        except QueueStoreError:
            outcomes.append("cancel_refused")

    threads = [
        threading.Thread(target=do_claim),
        threading.Thread(target=do_cancel),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)
    return outcomes


def test_concurrent_claim_and_cancel_never_create_both_states(tmp_path: Path) -> None:
    for index in range(8):
        queue = tmp_path / f"q{index}"
        initialize_queue_layout(queue)
        store, execution = _enqueue(queue, "d")
        eid = execution.execution_id
        outcomes = _race_once(queue, store, execution, eid)

        processing = (queue / "processing" / f"{eid}.json").is_file()
        cancelled = (queue / "cancelled" / f"{eid}.json").is_file()
        pending = (queue / "pending" / f"{eid}.json").is_file()

        assert not pending
        assert processing != cancelled, outcomes
        if processing:
            assert "claimed" in outcomes
            assert "cancel_refused" in outcomes
        else:
            assert "cancelled" in outcomes
            assert "claim_lost" in outcomes

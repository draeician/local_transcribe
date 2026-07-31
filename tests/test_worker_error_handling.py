"""Worker error classification, retry backoff, admission integration (task 033)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from local_transcribe.services.download_admission import DownloadAdmission, load_state
from local_transcribe.services.downloader import (
    AuthenticationRequiredError,
    ForbiddenError,
    PrivateVideoError,
    RateLimitError,
    StaleYtDlpError,
    VideoUnavailableError,
    classify_yt_dlp_failure,
)
from local_transcribe.services.queue_paths import initialize_queue_layout
from local_transcribe.services.queue_store import QueueStore
from local_transcribe.services.worker import fail_or_retry, promote_retries, run_worker
from local_transcribe.services.worker_errors import (
    available_at_elapsed,
    classify_job_exception,
    retry_delay_seconds,
)
def _queue(tmp_path: Path) -> Path:
    q = tmp_path / "q"
    initialize_queue_layout(q)
    return q


@pytest.mark.parametrize(
    "exc_cls,category,retryable",
    [
        (RateLimitError, "rate_limited", True),
        (ForbiddenError, "temporary_forbidden", True),
        (AuthenticationRequiredError, "authentication_required", False),
        (PrivateVideoError, "private_video", False),
        (VideoUnavailableError, "video_unavailable", False),
        (StaleYtDlpError, "extractor_failure", True),
    ],
)
def test_classify_job_exception_categories(
    exc_cls: type, category: str, retryable: bool
) -> None:
    err = classify_job_exception(exc_cls("boom"))
    assert err.category == category
    assert err.retryable is retryable


def test_classifier_covers_downloader_categories() -> None:
    cases = [
        ("HTTP Error 429: Too Many Requests", "rate_limited", True),
        ("HTTP Error 403: Forbidden", "temporary_forbidden", True),
        ("Sign in to confirm you're not a bot", "authentication_required", False),
        ("Private video", "private_video", False),
        ("Video unavailable", "video_unavailable", False),
        ("Requested format is not available", "extractor_failure", True),
        ("Connection timed out", "network_failure", True),
        ("Postprocessing: error", "postprocessing_failure", True),
    ]
    for stderr, category, retryable in cases:
        info = classify_yt_dlp_failure("", stderr, 1)
        assert info.category == category, stderr
        assert info.retryable is retryable, stderr


def test_fail_or_retry_sets_future_available_at(tmp_path: Path) -> None:
    q = _queue(tmp_path)
    store = QueueStore(q)
    enq = store.enqueue("https://youtu.be/dQw4w9WgXcQ")
    assert enq.execution is not None
    from local_transcribe.services.worker import claim_execution

    claimed = claim_execution(q, enq.execution)
    assert claimed is not None
    err = classify_job_exception(RateLimitError("429"))
    fail_or_retry(q, claimed, err)
    retry_files = list((q / "retry").glob("*.json"))
    assert retry_files
    data = json.loads(retry_files[0].read_text(encoding="utf-8"))
    assert data["status"] == "retry"
    assert data["error"]["category"] == "rate_limited"
    assert data["error"]["next_retry_at"]
    assert not available_at_elapsed(data["available_at"], now=0)


def test_terminal_categories_go_to_failed(tmp_path: Path) -> None:
    q = _queue(tmp_path)
    store = QueueStore(q)
    enq = store.enqueue("https://youtu.be/aaaaaaaaaaa")
    assert enq.execution is not None
    from local_transcribe.services.worker import claim_execution

    claimed = claim_execution(q, enq.execution)
    assert claimed is not None
    fail_or_retry(q, claimed, classify_job_exception(PrivateVideoError("private")))
    assert list((q / "failed").glob("*.json"))
    assert not list((q / "retry").glob("*.json"))


def test_promote_retries_timestamp_robust(tmp_path: Path) -> None:
    q = _queue(tmp_path)
    store = QueueStore(q)
    enq = store.enqueue("https://youtu.be/bbbbbbbbbbb")
    assert enq.execution is not None
    from local_transcribe.services.worker import claim_execution

    claimed = claim_execution(q, enq.execution)
    assert claimed is not None
    # Past available_at with explicit offset timezone
    fail_or_retry(
        q,
        claimed,
        classify_job_exception(ForbiddenError("403")),
        available_at="2020-01-01T00:00:00+00:00",
    )
    n = promote_retries(q, now=1_700_000_000.0)
    assert n == 1
    assert list((q / "pending").glob("*.json"))


def test_promote_unparseable_available_at(tmp_path: Path) -> None:
    q = _queue(tmp_path)
    store = QueueStore(q)
    enq = store.enqueue("https://youtu.be/ccccccccccc")
    assert enq.execution is not None
    from local_transcribe.services.worker import claim_execution

    claimed = claim_execution(q, enq.execution)
    assert claimed is not None
    fail_or_retry(
        q,
        claimed,
        classify_job_exception(RateLimitError("429")),
        available_at="not-a-timestamp",
    )
    assert promote_retries(q) == 1


def test_worker_429_updates_admission_and_execution(tmp_path: Path) -> None:
    q = _queue(tmp_path)
    store = QueueStore(q)
    store.enqueue("https://youtu.be/ddddddddddd", priority=100)

    def boom(_ex, _scratch):  # type: ignore[no-untyped-def]
        raise RateLimitError("HTTP 429")

    n = run_worker(
        queue_dir=q,
        once=True,
        validate_nfs=False,
        job_runner=boom,
        poll_interval_seconds=0.01,
    )
    assert n == 0
    state = load_state(q)
    assert state.total_429_errors >= 1
    assert state.blocked_until is not None
    retry = list((q / "retry").glob("*.json"))
    assert retry
    data = json.loads(retry[0].read_text(encoding="utf-8"))
    assert data["error"]["category"] == "rate_limited"
    assert data["available_at"] == state.blocked_until


def test_worker_temporary_403_backoff(tmp_path: Path) -> None:
    q = _queue(tmp_path)
    store = QueueStore(q)
    store.enqueue("https://youtu.be/eeeeeeeeeee", priority=100)

    def boom(_ex, _scratch):  # type: ignore[no-untyped-def]
        raise ForbiddenError("HTTP 403 temporary")

    run_worker(
        queue_dir=q,
        once=True,
        validate_nfs=False,
        job_runner=boom,
        poll_interval_seconds=0.01,
    )
    state = load_state(q)
    assert state.total_403_errors >= 1
    assert state.blocked_until is not None
    data = json.loads(next((q / "retry").glob("*.json")).read_text(encoding="utf-8"))
    assert data["error"]["category"] == "temporary_forbidden"


def test_worker_auth_terminal(tmp_path: Path) -> None:
    q = _queue(tmp_path)
    store = QueueStore(q)
    store.enqueue("https://youtu.be/fffffffffff", priority=100)

    def boom(_ex, _scratch):  # type: ignore[no-untyped-def]
        raise AuthenticationRequiredError("sign in")

    run_worker(
        queue_dir=q,
        once=True,
        validate_nfs=False,
        job_runner=boom,
        poll_interval_seconds=0.01,
    )
    data = json.loads(next((q / "failed").glob("*.json")).read_text(encoding="utf-8"))
    assert data["error"]["category"] == "authentication_required"
    assert data["error"]["retryable"] is False


def test_local_file_skips_download_admission(tmp_path: Path) -> None:
    q = _queue(tmp_path)
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"RIFF....WAVE")
    store = QueueStore(q)
    store.enqueue(str(audio), origin="lt-transcribe", priority=100)

    called = {"admit": 0}
    real_admit = DownloadAdmission.admit_and_record

    def tracking(self, *, worker_lock):  # type: ignore[no-untyped-def]
        called["admit"] += 1
        return real_admit(self, worker_lock=worker_lock)

    def ok(_ex, scratch):  # type: ignore[no-untyped-def]
        out = scratch / "out.json"
        out.write_text("{}\n", encoding="utf-8")
        return {"_output_path": str(out)}

    import local_transcribe.services.worker as worker_mod

    monkey = pytest.MonkeyPatch()
    monkey.setattr(DownloadAdmission, "admit_and_record", tracking)
    try:
        n = run_worker(
            queue_dir=q,
            once=True,
            validate_nfs=False,
            job_runner=ok,
            poll_interval_seconds=0.01,
        )
    finally:
        monkey.undo()
    assert n == 1
    assert called["admit"] == 0
    assert load_state(q).download_attempts_this_hour == 0


def test_admit_persisted_before_download(tmp_path: Path) -> None:
    q = _queue(tmp_path)
    store = QueueStore(q)
    store.enqueue("https://youtu.be/ggggggggggg", priority=100)
    order: list[str] = []

    def runner(_ex, scratch):  # type: ignore[no-untyped-def]
        order.append("download")
        # Attempt must already be on disk
        assert load_state(q).download_attempts_this_hour >= 1
        out = scratch / "out.json"
        out.write_text("{}\n", encoding="utf-8")
        return {"_output_path": str(out)}

    real_admit = DownloadAdmission.admit_and_record

    def admit_wrap(self, *, worker_lock):  # type: ignore[no-untyped-def]
        order.append("admit")
        return real_admit(self, worker_lock=worker_lock)

    monkey = pytest.MonkeyPatch()
    monkey.setattr(DownloadAdmission, "admit_and_record", admit_wrap)
    try:
        run_worker(
            queue_dir=q,
            once=True,
            validate_nfs=False,
            job_runner=runner,
            poll_interval_seconds=0.01,
        )
    finally:
        monkey.undo()
    assert order == ["admit", "download"]


def test_retry_delay_rate_limited_ladder() -> None:
    assert retry_delay_seconds("rate_limited", 1) == 300.0
    assert retry_delay_seconds("rate_limited", 2) == 900.0
    assert retry_delay_seconds("rate_limited", 3) == 3600.0

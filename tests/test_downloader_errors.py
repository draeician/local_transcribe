"""Tests for downloader error classifier (task 020)."""

from __future__ import annotations

from local_transcribe.services.downloader import classify_yt_dlp_failure


def test_classify_429() -> None:
    info = classify_yt_dlp_failure("", "HTTP Error 429: Too Many Requests", 1)
    assert info.category == "rate_limited"
    assert info.retryable


def test_classify_extractor_hints_update() -> None:
    info = classify_yt_dlp_failure("", "Requested format is not available", 1)
    assert info.category == "extractor_failure"
    assert "lt update" in info.message
    assert "venv/pipx" in info.message or "/usr/bin" in info.message


def test_classify_unavailable() -> None:
    info = classify_yt_dlp_failure("", "Video unavailable", 1)
    assert info.category == "video_unavailable"

"""Logging defaults."""

from __future__ import annotations

from pathlib import Path

from local_transcribe.logging_setup import configure_logging, default_log_dir


def test_default_log_dir_under_xdg_state(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("LOCAL_TRANSCRIBE_LOG_DIR", raising=False)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    assert default_log_dir() == tmp_path / "state" / "local-transcribe" / "logs"


def test_configure_logging_uses_rotating_file(tmp_path: Path) -> None:
    logger = configure_logging(
        log_dir=tmp_path,
        log_file_prefix="queue",
        announce=False,
    )
    logger.info("hello")
    log_file = tmp_path / "queue.log"
    assert log_file.is_file()
    assert "hello" in log_file.read_text(encoding="utf-8")

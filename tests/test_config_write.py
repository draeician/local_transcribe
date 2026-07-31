"""Config write helpers (queue init)."""

from __future__ import annotations

from pathlib import Path

from local_transcribe.services.config import load_config, write_queue_config


def test_write_queue_config_creates_file(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.yaml"
    queue = tmp_path / "q"
    queue.mkdir()
    written = write_queue_config(
        queue_dir=queue,
        queue_uuid="abc-123",
        expected_server="nas.example",
        expected_export="/export",
        config_path=cfg_path,
    )
    assert written == cfg_path
    cfg = load_config(cfg_path)
    assert cfg.queue.path == queue.resolve()
    assert cfg.queue.expected_uuid == "abc-123"
    assert cfg.queue.expected_server == "nas.example"
    assert cfg.queue.expected_export == "/export"


def test_write_queue_config_preserves_other_keys(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text("other:\n  keep: true\nqueue:\n  path: /old\n", encoding="utf-8")
    queue = tmp_path / "newq"
    queue.mkdir()
    write_queue_config(
        queue_dir=queue,
        queue_uuid="uuid-9",
        config_path=cfg_path,
    )
    text = cfg_path.read_text(encoding="utf-8")
    assert "keep: true" in text
    cfg = load_config(cfg_path)
    assert cfg.queue.expected_uuid == "uuid-9"
    assert cfg.queue.path == queue.resolve()

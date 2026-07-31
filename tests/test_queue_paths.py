"""Tests for queue path resolution and layout initialization (task 011)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from local_transcribe.services.config import AppConfig, QueueConfig, load_config
from local_transcribe.services.queue_paths import (
    QUEUE_STATE_DIRS,
    QueueIdentityError,
    QueuePathError,
    initialize_queue_layout,
    queue_id_path,
    resolve_queue_dir,
    verify_queue_identity,
)


def test_cli_path_overrides_config(tmp_path: Path) -> None:
    configured = tmp_path / "from-config"
    configured.mkdir()
    initialize_queue_layout(configured)

    cli_dir = tmp_path / "from-cli"
    cli_dir.mkdir()
    initialize_queue_layout(cli_dir)

    cfg = AppConfig(queue=QueueConfig(path=configured))
    resolved = resolve_queue_dir(queue_dir=cli_dir, config=cfg)
    assert resolved == cli_dir.resolve()


def test_missing_path_raises_queue_path_error(tmp_path: Path) -> None:
    cfg = AppConfig(queue=QueueConfig(path=None))
    with pytest.raises(QueuePathError, match="No queue directory configured"):
        resolve_queue_dir(config=cfg)


def test_config_path_used_when_cli_absent(tmp_path: Path) -> None:
    queue = tmp_path / "queue"
    queue.mkdir()
    initialize_queue_layout(queue)
    cfg = AppConfig(queue=QueueConfig(path=queue))
    resolved = resolve_queue_dir(config=cfg)
    assert resolved == queue.resolve()


def test_init_creates_layout_and_immutable_queue_id(tmp_path: Path) -> None:
    queue = tmp_path / "new-queue"
    first = initialize_queue_layout(queue)

    assert queue.is_dir()
    id_path = queue_id_path(queue)
    assert id_path.is_file()
    data = json.loads(id_path.read_text(encoding="utf-8"))
    assert data["queue_uuid"] == first
    assert "created_at" in data
    assert "created_by" in data

    for name in QUEUE_STATE_DIRS:
        assert (queue / name).is_dir()

    # Second init must not rewrite UUID or recreate identity.
    before = id_path.read_text(encoding="utf-8")
    second = initialize_queue_layout(queue)
    after = id_path.read_text(encoding="utf-8")
    assert second == first
    assert after == before


def test_expected_uuid_mismatch_raises(tmp_path: Path) -> None:
    queue = tmp_path / "q"
    queue.mkdir()
    real = initialize_queue_layout(queue)
    with pytest.raises(QueueIdentityError, match="UUID mismatch"):
        verify_queue_identity(queue, expected_uuid="00000000-0000-0000-0000-000000000000")

    # Matching expected UUID succeeds.
    assert verify_queue_identity(queue, expected_uuid=real) == real

    cfg = AppConfig(
        queue=QueueConfig(
            path=queue,
            expected_uuid="11111111-1111-1111-1111-111111111111",
        )
    )
    with pytest.raises(QueueIdentityError, match="UUID mismatch"):
        resolve_queue_dir(config=cfg)


def test_missing_queue_id_on_resolve_is_error(tmp_path: Path) -> None:
    queue = tmp_path / "empty"
    queue.mkdir()
    # State dirs absent and no queue.id
    cfg = AppConfig(queue=QueueConfig(path=queue))
    with pytest.raises(QueueIdentityError, match="identity marker missing"):
        resolve_queue_dir(config=cfg)

    with pytest.raises(QueueIdentityError, match="identity marker missing"):
        verify_queue_identity(queue)


def test_nonexistent_directory_raises(tmp_path: Path) -> None:
    missing = tmp_path / "nope"
    cfg = AppConfig(queue=QueueConfig(path=missing))
    with pytest.raises(QueuePathError, match="does not exist"):
        resolve_queue_dir(config=cfg, verify_identity=False)


def test_no_candidate_discovery_constants() -> None:
    """Guardrail: queue_paths must not expose multi-candidate path lists."""
    import local_transcribe.services.queue_paths as qp

    assert not hasattr(qp, "QUEUE_CANDIDATES")
    assert not hasattr(qp, "CANDIDATE_PATHS")
    source = Path(qp.__file__).read_text(encoding="utf-8")
    assert "QUEUE_CANDIDATES" not in source
    assert "references/transcripts/transcription-queue" not in source
    assert "/opt/md2/music/youtube/transcripts/transcription-queue" not in source


def test_load_config_from_yaml(tmp_path: Path) -> None:
    cfg_file = tmp_path / "config.yaml"
    queue = tmp_path / "q"
    cfg_file.write_text(
        "queue:\n"
        f"  path: {queue}\n"
        "  expected_uuid: abc-123\n"
        "  expected_nfs_version: 3\n"
        "  expected_server: nas.example\n"
        "  expected_export: /exports/transcripts\n",
        encoding="utf-8",
    )
    app = load_config(cfg_file)
    assert app.queue.path == queue
    assert app.queue.expected_uuid == "abc-123"
    assert app.queue.expected_nfs_version == 3
    assert app.queue.expected_server == "nas.example"
    assert app.queue.expected_export == "/exports/transcripts"


def test_load_config_missing_file_returns_defaults(tmp_path: Path) -> None:
    app = load_config(tmp_path / "does-not-exist.yaml")
    assert app.queue.path is None
    assert app.queue.expected_uuid is None


def test_unparseable_queue_id_raises(tmp_path: Path) -> None:
    queue = tmp_path / "q"
    queue.mkdir()
    queue_id_path(queue).write_text("not-json", encoding="utf-8")
    with pytest.raises(QueueIdentityError, match="not valid JSON"):
        verify_queue_identity(queue)

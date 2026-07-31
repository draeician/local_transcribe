"""CLI worker fail-closed startup (task 030)."""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest
from typer.testing import CliRunner

from local_transcribe.cli import app
from local_transcribe.cli_worker import render_systemd_unit, worker_run
from local_transcribe.services.queue_paths import (
    QueueIdentityError,
    initialize_queue_layout,
)
from local_transcribe.services.worker import run_worker


def test_run_worker_validate_nfs_defaults_true() -> None:
    sig = inspect.signature(run_worker)
    assert sig.parameters["validate_nfs"].default is True


def test_cli_worker_run_has_no_validate_nfs_disable_flag() -> None:
    """Production CLI must not expose a casual NFS validation disable."""
    runner = CliRunner()
    result = runner.invoke(app, ["worker", "run", "--help"])
    assert result.exit_code == 0
    help_text = result.output.lower()
    assert "--no-validate-nfs" not in help_text
    assert "--validate-nfs" not in help_text


def test_cli_worker_run_always_passes_validate_nfs_true(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict = {}

    def fake_run_worker(**kwargs):  # type: ignore[no-untyped-def]
        seen.update(kwargs)
        return 0

    monkeypatch.setattr(
        "local_transcribe.cli_worker.resolve_queue_dir",
        lambda **kw: tmp_path,
    )
    monkeypatch.setattr("local_transcribe.cli_worker.run_worker", fake_run_worker)
    monkeypatch.setattr(
        "local_transcribe.services.job_runner.create_default_job_runner",
        lambda **kw: object(),
    )
    initialize_queue_layout(tmp_path)

    runner = CliRunner()
    result = runner.invoke(
        app, ["worker", "run", "--once", "--queue-dir", str(tmp_path)]
    )
    assert result.exit_code == 0, result.output
    assert seen.get("validate_nfs") is True
    assert seen.get("require_statd") is True


def test_systemd_unit_starts_fail_closed_worker() -> None:
    unit = render_systemd_unit("/home/user/.local/bin/lt")
    assert "ExecStart=" in unit
    assert "worker run --standby" in unit
    assert "--no-validate-nfs" not in unit
    assert "--validate-nfs=false" not in unit.lower()
    # Unit relies on CLI default fail-closed validation
    assert "worker run --standby --no-validate" not in unit


def test_worker_install_writes_fail_closed_unit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(
        "local_transcribe.cli_worker.shutil.which",
        lambda _name: str(tmp_path / "lt"),
    )
    monkeypatch.setattr("local_transcribe.cli_worker.Path.home", lambda: home)

    runner = CliRunner()
    result = runner.invoke(app, ["worker", "install"])
    assert result.exit_code == 0, result.output
    unit_path = home / ".config" / "systemd" / "user" / "local-transcribe-worker.service"
    assert unit_path.is_file()
    text = unit_path.read_text(encoding="utf-8")
    assert "worker run --standby" in text
    assert "--no-validate-nfs" not in text


def test_run_worker_does_not_auto_init_missing_queue(tmp_path: Path) -> None:
    missing = tmp_path / "not-a-queue"
    missing.mkdir()
    assert not (missing / "queue.id").exists()
    with pytest.raises(QueueIdentityError, match="identity marker missing"):
        run_worker(
            queue_dir=missing,
            once=True,
            validate_nfs=False,
            job_runner=lambda ex, scratch: {},
        )
    assert not (missing / "queue.id").exists()


def test_run_worker_missing_queue_id_fatal_even_with_nfs_skip(
    tmp_path: Path,
) -> None:
    q = tmp_path / "q"
    q.mkdir()
    with pytest.raises(QueueIdentityError):
        run_worker(
            queue_dir=q,
            once=True,
            validate_nfs=False,
            job_runner=lambda ex, scratch: {},
        )


def test_worker_run_signature_has_no_validate_nfs_param() -> None:
    sig = inspect.signature(worker_run)
    assert "validate_nfs" not in sig.parameters

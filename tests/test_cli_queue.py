"""CLI queue smoke tests (task 022)."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from local_transcribe.cli import app

runner = CliRunner()


def test_queue_init_and_add(tmp_path: Path) -> None:
    q = tmp_path / "queue"
    r = runner.invoke(app, ["queue", "init", "--queue-dir", str(q)])
    assert r.exit_code == 0, r.output
    r2 = runner.invoke(
        app,
        ["queue", "add", "https://youtu.be/dQw4w9WgXcQ", "--queue-dir", str(q)],
    )
    assert r2.exit_code == 0, r2.output
    assert "enqueued" in r2.output
    r3 = runner.invoke(app, ["queue", "stats", "--queue-dir", str(q)])
    assert r3.exit_code == 0
    assert "pending:" in r3.output


def test_purge_requires_filter(tmp_path: Path) -> None:
    q = tmp_path / "queue"
    runner.invoke(app, ["queue", "init", "--queue-dir", str(q)])
    r = runner.invoke(app, ["queue", "purge", "--queue-dir", str(q)])
    assert r.exit_code != 0

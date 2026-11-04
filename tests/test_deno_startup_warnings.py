"""Regression tests: Deno is probed only on YouTube download paths (task 038).

The root Typer callback used to run ``check_deno_with_guidance()`` for every
subcommand, so queue reporting, version and help output printed an unrelated
"Deno runtime not detected" warning. These tests simulate a host without Deno
and assert the probe runs only where YouTube downloading actually occurs.

The CLI imports the probe inside each command body, so patching
``local_transcribe.utils.doctor.check_deno_with_guidance`` intercepts every
call site.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import typer
from typer.testing import CliRunner

import local_transcribe.cli as cli_mod
from local_transcribe.cli import app
from local_transcribe.services.queue_paths import initialize_queue_layout
from local_transcribe.services.queue_store import QueueStore

runner = CliRunner()

DENO_WARNING = "Deno runtime not detected"


def _queue(tmp_path: Path) -> Path:
    q = tmp_path / "q"
    initialize_queue_layout(q)
    return q


def _no_deno(monkeypatch) -> list[bool]:
    """Simulate a host without Deno and record every probe call."""
    calls: list[bool] = []

    def fake_probe() -> tuple[bool, str]:
        calls.append(True)
        return False, "deno not found (only needed for YouTube downloads)"

    monkeypatch.setattr(
        "local_transcribe.utils.doctor.check_deno_with_guidance", fake_probe
    )
    return calls


def test_queue_reporting_is_quiet_without_deno(tmp_path: Path, monkeypatch) -> None:
    """`lt queue list/show/stats/path/doctor` must not warn about Deno."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg-state"))
    q = _queue(tmp_path)
    store = QueueStore(q)
    enq = store.enqueue("https://youtu.be/dQw4w9WgXcQ", origin="lt-transcribe")
    assert enq.execution is not None
    eid = enq.execution.execution_id

    calls = _no_deno(monkeypatch)

    invocations = [
        ["queue", "list", "--queue-dir", str(q)],
        ["queue", "show", eid, "--queue-dir", str(q)],
        ["queue", "stats", "--queue-dir", str(q)],
        ["queue", "path", "--queue-dir", str(q)],
        ["queue", "doctor", "--queue-dir", str(q)],
    ]

    for args in invocations:
        result = runner.invoke(app, args)
        assert DENO_WARNING not in result.output, f"{args}: {result.output}"

    assert calls == [], f"queue reporting invoked the Deno probe: {calls}"


def test_queue_list_reports_records_without_deno(tmp_path: Path, monkeypatch) -> None:
    """Queue inspection stays functional (exit 0 + records) with no Deno."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    q = _queue(tmp_path)
    store = QueueStore(q)
    assert store.enqueue("https://youtu.be/dQw4w9WgXcQ").execution is not None

    calls = _no_deno(monkeypatch)
    result = runner.invoke(app, ["queue", "list", "--queue-dir", str(q)])

    assert result.exit_code == 0, result.output
    assert DENO_WARNING not in result.output
    assert "youtube:dQw4w9WgXcQ" in result.output
    assert calls == []


def test_version_and_help_are_quiet_without_deno(monkeypatch) -> None:
    calls = _no_deno(monkeypatch)

    for args in (
        ["version"],
        ["--version"],
        ["--help"],
        ["queue", "--help"],
        [],
    ):
        result = runner.invoke(app, args)
        assert result.exit_code == 0, f"{args}: {result.output}"
        assert DENO_WARNING not in result.output, f"{args}: {result.output}"

    assert calls == []


def test_transcribe_url_still_warns_without_deno(tmp_path: Path, monkeypatch) -> None:
    """YouTube download path keeps the Deno prerequisite warning."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    q = _queue(tmp_path)

    calls = _no_deno(monkeypatch)
    result = runner.invoke(
        app,
        [
            "transcribe",
            "https://youtu.be/dQw4w9WgXcQ",
            "--no-wait",
            "--queue-dir",
            str(q),
        ],
    )

    assert result.exit_code == 0, result.output
    assert DENO_WARNING in result.output
    assert "HTTP 403" in result.output
    assert calls, "YouTube path must probe Deno"


def test_transcribe_local_file_does_not_probe_deno(tmp_path: Path, monkeypatch) -> None:
    """Local audio transcription never requires Deno."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    q = _queue(tmp_path)
    audio = tmp_path / "audio.m4a"
    audio.write_bytes(b"\x00\x00\x00\x18ftypm4a ")

    calls = _no_deno(monkeypatch)
    result = runner.invoke(
        app,
        ["transcribe", str(audio), "--no-wait", "--queue-dir", str(q)],
    )

    assert result.exit_code == 0, result.output
    assert DENO_WARNING not in result.output
    assert "Queue enqueued: local:" in result.output
    assert calls == []


def test_batch_queue_mode_does_not_probe_deno(tmp_path: Path, monkeypatch) -> None:
    """Bulk enqueue is a producer path; it must not warn about Deno."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    q = _queue(tmp_path)
    urls = tmp_path / "urls.txt"
    urls.write_text("https://youtu.be/dQw4w9WgXcQ\n", encoding="utf-8")

    calls = _no_deno(monkeypatch)
    result = runner.invoke(
        app, ["batch", "--input", str(urls), "--queue-dir", str(q)]
    )

    assert result.exit_code == 0, result.output
    assert DENO_WARNING not in result.output
    assert "Batch enqueue summary" in result.output
    assert calls == []


def test_batch_direct_mode_warns_without_deno(tmp_path: Path, monkeypatch) -> None:
    """The legacy in-process pipeline downloads URLs, so it keeps the warning."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))

    def fake_run(self) -> None:  # stand-in for BatchPipeline.run
        raise typer.Exit(0)

    urls = tmp_path / "urls.txt"
    urls.write_text("https://youtu.be/dQw4w9WgXcQ\n", encoding="utf-8")

    calls = _no_deno(monkeypatch)
    with patch("local_transcribe.services.pipeline.BatchPipeline.run", fake_run):
        result = runner.invoke(app, ["batch", "--input", str(urls), "--direct"])

    assert DENO_WARNING in result.output, result.output
    assert calls, "direct batch must probe Deno"


def test_doctor_keeps_deno_as_diagnostic(monkeypatch) -> None:
    """`lt doctor` still reports Deno availability as an optional check."""
    _no_deno(monkeypatch)
    result = runner.invoke(app, ["doctor"])

    assert "Deno Runtime (YouTube)" in result.output, result.output


def test_startup_callback_does_not_probe_deno(monkeypatch) -> None:
    """The root callback itself must never call the Deno probe."""
    calls = _no_deno(monkeypatch)
    result = runner.invoke(app, ["version"])

    assert DENO_WARNING not in result.output
    assert calls == []


def test_startup_warnings_flag_removed() -> None:
    """The unconditional startup-warning flag is gone from the CLI module."""
    assert not hasattr(cli_mod, "_startup_warnings_shown")

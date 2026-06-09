"""Tests for lt update yt-dlp refresh behavior."""

from __future__ import annotations

import subprocess
import sys
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from local_transcribe.cli import app
from local_transcribe.utils.ytdlp_update import (
    YtDlpUpdateOutcome,
    build_yt_dlp_pip_command,
    perform_yt_dlp_update,
)


@pytest.mark.parametrize(
    ("stable", "expect_pre"),
    [
        (False, True),
        (True, False),
    ],
)
def test_build_yt_dlp_pip_command_pre_flag(stable: bool, expect_pre: bool) -> None:
    cmd = build_yt_dlp_pip_command("/usr/bin/python3", stable=stable)
    assert cmd[:4] == ["/usr/bin/python3", "-m", "pip", "install"]
    assert cmd[4] == "-U"
    if expect_pre:
        assert "--pre" in cmd
    else:
        assert "--pre" not in cmd
    assert cmd[-1] == "yt-dlp[default,curl-cffi]"


def test_perform_yt_dlp_update_dry_run_skips_pip_install() -> None:
    run = MagicMock()
    with patch(
        "local_transcribe.utils.ytdlp_update.get_yt_dlp_version",
        return_value="2025.01.01",
    ):
        outcome = perform_yt_dlp_update(
            stable=False,
            dry_run=True,
            python_executable="/venv/bin/python",
            run=run,
        )

    run.assert_not_called()
    assert outcome.dry_run is True
    assert outcome.success is True
    assert "--pre" in outcome.pip_command


def test_perform_yt_dlp_update_stable_omits_pre() -> None:
    run = MagicMock(
        return_value=subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="",
            stderr="",
        )
    )
    with patch(
        "local_transcribe.utils.ytdlp_update.get_yt_dlp_version",
        side_effect=["2025.01.01", "2025.02.01"],
    ):
        outcome = perform_yt_dlp_update(
            stable=True,
            dry_run=False,
            python_executable="/venv/bin/python",
            run=run,
        )

    assert outcome.success is True
    assert "--pre" not in outcome.pip_command
    run.assert_called_once()
    assert run.call_args.args[0] == [
        "/venv/bin/python",
        "-m",
        "pip",
        "install",
        "-U",
        "yt-dlp[default,curl-cffi]",
    ]


def test_cli_update_default_includes_pre() -> None:
    runner = CliRunner()
    with patch(
        "local_transcribe.utils.ytdlp_update.resolve_deno_on_path",
        return_value=(True, "deno 2.0.0"),
    ), patch(
        "local_transcribe.utils.ytdlp_update.perform_yt_dlp_update",
        return_value=YtDlpUpdateOutcome(
            success=True,
            pip_command=build_yt_dlp_pip_command(sys.executable, stable=False),
            before_version="2025.01.01",
            after_version="2025.02.01",
            dry_run=False,
        ),
    ) as perform_mock:
        result = runner.invoke(app, ["update"])

    assert result.exit_code == 0, result.output
    perform_mock.assert_called_once()
    assert perform_mock.call_args.kwargs["stable"] is False
    assert "Before: 2025.01.01" in result.output
    assert "After: 2025.02.01" in result.output
    assert "Updating yt-dlp in the current lt environment" in result.output


def test_cli_update_stable_omits_pre() -> None:
    runner = CliRunner()
    with patch(
        "local_transcribe.utils.ytdlp_update.resolve_deno_on_path",
        return_value=(True, "deno 2.0.0"),
    ), patch(
        "local_transcribe.utils.ytdlp_update.perform_yt_dlp_update",
        return_value=YtDlpUpdateOutcome(
            success=True,
            pip_command=build_yt_dlp_pip_command(sys.executable, stable=True),
            before_version="2025.01.01",
            after_version="2025.02.01",
            dry_run=False,
        ),
    ) as perform_mock:
        result = runner.invoke(app, ["update", "--stable"])

    assert result.exit_code == 0, result.output
    perform_mock.assert_called_once()
    assert perform_mock.call_args.kwargs["stable"] is True
    assert "--pre" not in perform_mock.return_value.pip_command


def test_cli_update_dry_run_does_not_modify_environment() -> None:
    runner = CliRunner()
    pip_command = build_yt_dlp_pip_command(sys.executable, stable=False)
    with patch(
        "local_transcribe.utils.ytdlp_update.resolve_deno_on_path",
        return_value=(True, "deno 2.0.0"),
    ), patch(
        "local_transcribe.utils.ytdlp_update.perform_yt_dlp_update",
        return_value=YtDlpUpdateOutcome(
            success=True,
            pip_command=pip_command,
            before_version="2025.01.01",
            after_version="2025.01.01",
            dry_run=True,
        ),
    ) as perform_mock:
        result = runner.invoke(app, ["update", "--dry-run"])

    assert result.exit_code == 0, result.output
    perform_mock.assert_called_once()
    assert perform_mock.call_args.kwargs["dry_run"] is True
    assert "Dry run:" in result.output
    assert "--pre" in result.output
    assert "yt-dlp[default,curl-cffi]" in result.output
    assert "After: 2025.01.01" in result.output


def test_cli_update_missing_deno_exits_with_clear_message() -> None:
    runner = CliRunner()
    with patch(
        "local_transcribe.utils.ytdlp_update.resolve_deno_on_path",
        return_value=(False, None),
    ), patch("local_transcribe.utils.ytdlp_update.perform_yt_dlp_update") as perform_mock:
        result = runner.invoke(app, ["update"])

    assert result.exit_code == 1, result.output
    perform_mock.assert_not_called()
    assert "Deno is not on PATH" in result.output


def test_cli_update_pip_failure_shows_command_and_stderr() -> None:
    runner = CliRunner()
    pip_command = build_yt_dlp_pip_command(sys.executable, stable=False)
    with patch(
        "local_transcribe.utils.ytdlp_update.resolve_deno_on_path",
        return_value=(True, "deno 2.0.0"),
    ), patch(
        "local_transcribe.utils.ytdlp_update.perform_yt_dlp_update",
        return_value=YtDlpUpdateOutcome(
            success=False,
            pip_command=pip_command,
            before_version="2025.01.01",
            after_version="2025.01.01",
            dry_run=False,
            pip_returncode=1,
            pip_stderr_tail="ERROR: pip install failed",
        ),
    ):
        result = runner.invoke(app, ["update"])

    assert result.exit_code == 1, result.output
    assert "yt-dlp update failed" in result.output
    assert "Command:" in result.output
    assert "--pre" in result.output
    assert "yt-dlp[default,curl-cffi]" in result.output
    assert "ERROR: pip install failed" in result.output

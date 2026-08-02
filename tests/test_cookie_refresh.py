"""Tests for browser cookie refresh helpers and CLI."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml
from typer.testing import CliRunner

from local_transcribe.cli import app
from local_transcribe.services.cookie_refresh import (
    ensure_auth_profile_cookies,
    refresh_cookies_from_browser,
    resolve_browser_cookie_source,
    resolve_cookies_output_path,
)
from local_transcribe.services.config import AppConfig, QueueConfig, load_config


def test_resolve_brave_flatpak_profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    profile = (
        home
        / ".var"
        / "app"
        / "com.brave.Browser"
        / "config"
        / "BraveSoftware"
        / "Brave-Browser"
        / "Default"
    )
    profile.mkdir(parents=True)
    monkeypatch.setattr(
        "local_transcribe.services.cookie_refresh.Path.home",
        lambda: home,
    )

    source = resolve_browser_cookie_source("brave")
    assert source.profile_path == profile
    assert source.cookies_from_browser.startswith("brave+gnomekeyring:")
    assert str(profile) in source.cookies_from_browser


def test_resolve_firefox_ignores_keyring() -> None:
    source = resolve_browser_cookie_source("firefox", keyring="gnomekeyring")
    assert source.cookies_from_browser == "firefox"


def test_resolve_chrome_with_explicit_profile(tmp_path: Path) -> None:
    profile = tmp_path / "chrome-profile"
    profile.mkdir()
    source = resolve_browser_cookie_source(
        "chrome", profile_path=profile, keyring="none"
    )
    assert source.cookies_from_browser == f"chrome:{profile}"


def test_resolve_explicit_cookies_from_browser_override() -> None:
    source = resolve_browser_cookie_source(
        "brave",
        cookies_from_browser="brave+gnomekeyring:/custom/Default",
    )
    assert source.cookies_from_browser == "brave+gnomekeyring:/custom/Default"


def test_resolve_cookies_output_uses_auth_profile(tmp_path: Path) -> None:
    cookies = tmp_path / "yt.txt"
    cfg = AppConfig(
        queue=QueueConfig(default_auth_profile="yt"),
        raw={
            "queue": {"default_auth_profile": "yt"},
            "auth_profiles": {"yt": {"cookies_file": str(cookies)}},
        },
    )
    path, name = resolve_cookies_output_path(config=cfg)
    assert path == cookies
    assert name == "yt"


def test_ensure_auth_profile_cookies_writes_config(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.yaml"
    cookies = tmp_path / "youtube-cookies.txt"
    cookies.write_text("# Netscape\n", encoding="utf-8")

    written, updated = ensure_auth_profile_cookies(
        cookies_file=cookies,
        profile_name="yt",
        config_path=cfg_path,
    )
    assert written == cfg_path
    assert updated is True
    cfg = load_config(cfg_path)
    assert cfg.queue.default_auth_profile == "yt"
    assert cfg.raw["auth_profiles"]["yt"]["cookies_file"] == str(cookies)


def test_refresh_cookies_from_browser_exports_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = tmp_path / "cookies.txt"
    cfg_path = tmp_path / "config.yaml"

    def fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        # cmd contains --cookies <tmp>
        cookies_idx = cmd.index("--cookies")
        dest = Path(cmd[cookies_idx + 1])
        dest.write_text(
            "# Netscape HTTP Cookie File\n"
            ".youtube.com\tTRUE\t/\tTRUE\t0\tA\tB\n",
            encoding="utf-8",
        )
        # yt-dlp often exits non-zero after keyring warnings while still writing.
        return MagicMock(returncode=1, stdout="", stderr="WARNING: secretstorage ...")

    monkeypatch.setattr(
        "local_transcribe.services.cookie_refresh.yt_dlp_invocation",
        lambda: ["yt-dlp"],
    )
    monkeypatch.setattr(
        "local_transcribe.services.cookie_refresh.subprocess.run",
        fake_run,
    )
    monkeypatch.setattr(
        "local_transcribe.services.cookie_refresh.load_config",
        lambda _p=None: AppConfig(),
    )

    result = refresh_cookies_from_browser(
        browser="firefox",
        output=out,
        profile_name="yt",
        config_path=cfg_path,
        update_config=True,
    )
    assert result.cookies_file == out
    assert out.is_file()
    assert "Netscape" in out.read_text(encoding="utf-8")
    assert result.config_updated is True
    data = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    assert data["auth_profiles"]["yt"]["cookies_file"] == str(out)


def test_cli_cookies_refresh_help() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["cookies", "refresh", "--help"])
    assert result.exit_code == 0
    assert "brave" in result.output.lower()
    assert "chrome" in result.output.lower()
    assert "firefox" in result.output.lower()


def test_cli_cookies_refresh_rejects_bad_browser() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["cookies", "refresh", "--browser", "safari"])
    assert result.exit_code != 0
    assert "brave" in result.output.lower()

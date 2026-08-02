"""Export YouTube cookies from a browser into a Netscape cookies file."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Mapping, MutableMapping, Optional

import yaml

from local_transcribe.services.config import AppConfig, default_config_path, load_config
from local_transcribe.utils.ytdlp_update import yt_dlp_invocation

BrowserName = Literal["brave", "chrome", "firefox"]

DEFAULT_PROBE_URL = "https://www.youtube.com/watch?v=jNQXAC9IVRw"
DEFAULT_PROFILE_NAME = "yt"


@dataclass(frozen=True)
class BrowserCookieSource:
    """Resolved ``--cookies-from-browser`` argument and human label."""

    browser: BrowserName
    cookies_from_browser: str
    profile_path: Optional[Path] = None
    label: str = ""


@dataclass(frozen=True)
class CookieRefreshResult:
    """Outcome of a cookie refresh export."""

    cookies_file: Path
    browser_source: BrowserCookieSource
    profile_name: str
    config_path: Path
    config_updated: bool
    yt_dlp_cmd: list[str]


def default_cookies_file() -> Path:
    """Default Netscape cookies path under XDG config."""
    xdg = os.environ.get("XDG_CONFIG_HOME", "").strip()
    if xdg:
        return Path(xdg).expanduser() / "local-transcribe" / "youtube-cookies.txt"
    return Path.home() / ".config" / "local-transcribe" / "youtube-cookies.txt"


def _brave_profile_candidates() -> list[Path]:
    home = Path.home()
    return [
        home
        / ".var"
        / "app"
        / "com.brave.Browser"
        / "config"
        / "BraveSoftware"
        / "Brave-Browser"
        / "Default",
        home / ".config" / "BraveSoftware" / "Brave-Browser" / "Default",
    ]


def _chrome_profile_candidates() -> list[Path]:
    home = Path.home()
    return [
        home / ".config" / "google-chrome" / "Default",
        home / ".config" / "chromium" / "Default",
        home
        / ".var"
        / "app"
        / "com.google.Chrome"
        / "config"
        / "google-chrome"
        / "Default",
    ]


def _first_existing(paths: list[Path]) -> Optional[Path]:
    for path in paths:
        if path.is_dir():
            return path
    return None


def _with_keyring(browser: str, keyring: Optional[str]) -> str:
    if not keyring or keyring in {"none", "off", "-"}:
        return browser
    return f"{browser}+{keyring}"


def resolve_browser_cookie_source(
    browser: BrowserName,
    *,
    profile_path: Path | None = None,
    keyring: str | None = "gnomekeyring",
    cookies_from_browser: str | None = None,
) -> BrowserCookieSource:
    """
    Build a yt-dlp ``--cookies-from-browser`` value.

    Defaults to Brave (Flatpak profile when present). Chrome/Firefox are
    supported explicitly. ``cookies_from_browser`` bypasses auto-detection.
    """
    if cookies_from_browser:
        return BrowserCookieSource(
            browser=browser,
            cookies_from_browser=cookies_from_browser,
            profile_path=None,
            label=f"explicit ({cookies_from_browser})",
        )

    if browser == "firefox":
        # Firefox profile discovery is handled by yt-dlp; keyring unused.
        return BrowserCookieSource(
            browser="firefox",
            cookies_from_browser="firefox",
            label="firefox (yt-dlp default profile)",
        )

    if browser == "brave":
        profile = Path(profile_path).expanduser() if profile_path else _first_existing(
            _brave_profile_candidates()
        )
        base = _with_keyring("brave", keyring)
        if profile is not None:
            value = f"{base}:{profile}"
            return BrowserCookieSource(
                browser="brave",
                cookies_from_browser=value,
                profile_path=profile,
                label=f"brave ({profile})",
            )
        return BrowserCookieSource(
            browser="brave",
            cookies_from_browser=base,
            label=f"brave ({base})",
        )

    # chrome
    profile = Path(profile_path).expanduser() if profile_path else _first_existing(
        _chrome_profile_candidates()
    )
    base = _with_keyring("chrome", keyring)
    if profile is not None:
        # Chromium profile dir → use chromium browser name when path says so.
        browser_token = "chromium" if "chromium" in str(profile).lower() else "chrome"
        base = _with_keyring(browser_token, keyring)
        value = f"{base}:{profile}"
        return BrowserCookieSource(
            browser="chrome",
            cookies_from_browser=value,
            profile_path=profile,
            label=f"{browser_token} ({profile})",
        )
    return BrowserCookieSource(
        browser="chrome",
        cookies_from_browser=base,
        label=f"chrome ({base})",
    )


def resolve_cookies_output_path(
    *,
    output: Path | None = None,
    config: AppConfig | None = None,
    profile_name: str | None = None,
) -> tuple[Path, str]:
    """
    Choose destination cookies file and auth profile name.

    Preference: explicit ``output``, then configured profile cookies_file,
    then default XDG path.
    """
    cfg = config or load_config()
    name = (
        profile_name
        or cfg.queue.default_auth_profile
        or DEFAULT_PROFILE_NAME
    )
    if output is not None:
        return Path(output).expanduser(), name

    profiles = cfg.raw.get("auth_profiles") if isinstance(cfg.raw, dict) else None
    if isinstance(profiles, Mapping):
        entry = profiles.get(name)
        if isinstance(entry, Mapping) and entry.get("cookies_file"):
            return Path(str(entry["cookies_file"])).expanduser(), name

    return default_cookies_file(), name


def ensure_auth_profile_cookies(
    *,
    cookies_file: Path,
    profile_name: str = DEFAULT_PROFILE_NAME,
    config_path: Path | None = None,
    set_default: bool = True,
) -> tuple[Path, bool]:
    """
    Ensure config maps ``profile_name`` → ``cookies_file``.

    Returns ``(config_path, updated)``.
    """
    path = (config_path or default_config_path()).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)

    raw: dict = {}
    if path.is_file():
        try:
            loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            loaded = {}
        if isinstance(loaded, MutableMapping):
            raw = dict(loaded)

    queue_raw = raw.get("queue")
    if not isinstance(queue_raw, MutableMapping):
        queue_raw = {}
    else:
        queue_raw = dict(queue_raw)

    profiles = raw.get("auth_profiles")
    if not isinstance(profiles, MutableMapping):
        profiles = {}
    else:
        profiles = dict(profiles)

    entry = profiles.get(profile_name)
    if not isinstance(entry, MutableMapping):
        entry = {}
    else:
        entry = dict(entry)

    cookies_s = str(cookies_file.expanduser())
    updated = False
    if entry.get("cookies_file") != cookies_s:
        entry["cookies_file"] = cookies_s
        updated = True
    # Prefer file-based auth for the worker; drop stale browser key if present.
    if "cookies_from_browser" in entry:
        entry.pop("cookies_from_browser", None)
        updated = True

    profiles[profile_name] = entry
    if set_default and queue_raw.get("default_auth_profile") != profile_name:
        queue_raw["default_auth_profile"] = profile_name
        updated = True

    if updated:
        raw["queue"] = queue_raw
        raw["auth_profiles"] = profiles
        body = yaml.safe_dump(raw, default_flow_style=False, sort_keys=False)
        path.write_text(body, encoding="utf-8")

    return path, updated


def refresh_cookies_from_browser(
    *,
    browser: BrowserName = "brave",
    output: Path | None = None,
    profile_name: str | None = None,
    profile_path: Path | None = None,
    keyring: str | None = "gnomekeyring",
    cookies_from_browser: str | None = None,
    probe_url: str = DEFAULT_PROBE_URL,
    config_path: Path | None = None,
    update_config: bool = True,
    timeout_seconds: float = 120.0,
) -> CookieRefreshResult:
    """
    Export browser cookies to a Netscape file via yt-dlp.

    Uses the same yt-dlp binary preference as downloads (venv/pipx first).
    """
    cfg = load_config(config_path)
    dest, resolved_profile = resolve_cookies_output_path(
        output=output,
        config=cfg,
        profile_name=profile_name,
    )
    dest = dest.expanduser()
    dest.parent.mkdir(parents=True, exist_ok=True)

    source = resolve_browser_cookie_source(
        browser,
        profile_path=profile_path,
        keyring=keyring,
        cookies_from_browser=cookies_from_browser,
    )

    # Atomic-ish: write to temp sibling then replace.
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    if tmp.exists():
        tmp.unlink()

    inv = yt_dlp_invocation()

    def _run_export(browser_arg: str) -> tuple[subprocess.CompletedProcess[str], list[str]]:
        cmd = [
            *inv,
            "--cookies-from-browser",
            browser_arg,
            "--cookies",
            str(tmp),
            "--skip-download",
            "--no-warnings",
            probe_url,
        ]
        if tmp.exists():
            tmp.unlink()
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"Cookie refresh timed out after {timeout_seconds}s"
            ) from exc
        return proc, cmd

    def _cookies_file_usable(path: Path) -> bool:
        if not path.is_file() or path.stat().st_size < 32:
            return False
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return False
        # Netscape cookie lines are tab-separated; ignore comments/blank.
        return any(
            (not line.startswith("#")) and ("\t" in line)
            for line in text.splitlines()
        )

    proc, cmd = _run_export(source.cookies_from_browser)
    detail = (proc.stderr or proc.stdout or "")[-2000:]

    # Chromium + gnomekeyring needs secretstorage in the runtime; fall back once.
    if (
        not _cookies_file_usable(tmp)
        and browser in {"brave", "chrome"}
        and keyring
        and keyring not in {"none", "off", "-"}
        and "secretstorage" in detail.lower()
        and not cookies_from_browser
    ):
        fallback = resolve_browser_cookie_source(
            browser,
            profile_path=profile_path or source.profile_path,
            keyring=None,
            cookies_from_browser=None,
        )
        proc, cmd = _run_export(fallback.cookies_from_browser)
        detail = (proc.stderr or proc.stdout or "")[-2000:]
        if _cookies_file_usable(tmp):
            source = fallback

    # yt-dlp may exit non-zero after keyring warnings while still writing cookies.
    if not _cookies_file_usable(tmp):
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
        hint = ""
        if "secretstorage" in detail.lower():
            hint = (
                "\nHint: install secretstorage + jeepney + cryptography in the "
                "lt runtime (`pipx inject local-transcribe secretstorage jeepney "
                "cryptography`) or export with system yt-dlp and copy the file."
            )
        raise RuntimeError(
            "Failed to export cookies from browser via yt-dlp.\n"
            f"Command: {' '.join(cmd)}\n{detail}{hint}"
        )

    os.replace(tmp, dest)
    try:
        os.chmod(dest, 0o600)
    except OSError:
        pass

    config_updated = False
    cfg_path = (config_path or default_config_path()).expanduser()
    if update_config:
        cfg_path, config_updated = ensure_auth_profile_cookies(
            cookies_file=dest,
            profile_name=resolved_profile,
            config_path=cfg_path,
            set_default=True,
        )

    return CookieRefreshResult(
        cookies_file=dest,
        browser_source=source,
        profile_name=resolved_profile,
        config_path=cfg_path,
        config_updated=config_updated,
        yt_dlp_cmd=cmd,
    )

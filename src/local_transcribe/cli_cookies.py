"""Cookie refresh CLI for worker auth profiles."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

from local_transcribe.logging_setup import configure_logging

cookies_app = typer.Typer(
    help=(
        "Manage YouTube cookies for the background worker. "
        "Default export path: ~/.config/local-transcribe/youtube-cookies.txt "
        "(or auth_profiles.<name>.cookies_file). "
        "See also: docs/QUEUE_OPERATOR.md"
    ),
    no_args_is_help=True,
)
console = Console()


@cookies_app.command("refresh")
def cookies_refresh(
    browser: str = typer.Option(
        "brave",
        "--browser",
        "-b",
        help="Browser to export from: brave (default), chrome, or firefox",
    ),
    output: Optional[Path] = typer.Option(
        None,
        "--output",
        "-o",
        help="Destination Netscape cookies file "
        "(defaults to auth profile cookies_file or "
        "~/.config/local-transcribe/youtube-cookies.txt)",
    ),
    profile: Optional[str] = typer.Option(
        None,
        "--profile",
        help="Auth profile name to update (defaults to queue.default_auth_profile)",
    ),
    profile_path: Optional[Path] = typer.Option(
        None,
        "--profile-path",
        help="Browser profile directory (auto-detect Flatpak/native Brave/Chrome)",
    ),
    keyring: str = typer.Option(
        "gnomekeyring",
        "--keyring",
        help="Chromium keyring backend (gnomekeyring, kwallet, basic, none). "
        "Ignored for firefox.",
    ),
    cookies_from_browser: Optional[str] = typer.Option(
        None,
        "--cookies-from-browser",
        help="Raw yt-dlp --cookies-from-browser value (bypasses auto-detect)",
    ),
    probe_url: str = typer.Option(
        "https://www.youtube.com/watch?v=jNQXAC9IVRw",
        "--probe-url",
        help="URL used with --skip-download to trigger cookie export",
    ),
    no_config_update: bool = typer.Option(
        False,
        "--no-config-update",
        help="Do not write auth_profiles / default_auth_profile in config.yaml",
    ),
    verbose: bool = typer.Option(False, "-v", "--verbose"),
) -> None:
    """Export cookies from a browser into the worker's Netscape cookies file.

    Default browser is Brave (Flatpak profile when present). Use
    ``--browser chrome`` or ``--browser firefox`` for other browsers.
    """
    from local_transcribe.services.cookie_refresh import refresh_cookies_from_browser

    logger = configure_logging(verbose=verbose, log_file_prefix="cookies")
    browser_norm = browser.strip().lower()
    if browser_norm not in {"brave", "chrome", "firefox"}:
        console.print(
            "[red]✗[/red] --browser must be one of: brave, chrome, firefox"
        )
        raise typer.Exit(1)

    keyring_norm = keyring.strip().lower()
    if keyring_norm in {"none", "off", "-"}:
        keyring_val: Optional[str] = None
    else:
        keyring_val = keyring_norm

    try:
        result = refresh_cookies_from_browser(
            browser=browser_norm,  # type: ignore[arg-type]
            output=output,
            profile_name=profile,
            profile_path=profile_path,
            keyring=keyring_val,
            cookies_from_browser=cookies_from_browser,
            probe_url=probe_url,
            update_config=not no_config_update,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("Cookie refresh failed: %s", exc, exc_info=verbose)
        console.print(f"[red]✗[/red] {exc}")
        raise typer.Exit(1) from exc

    console.print(f"[green]✓[/green] Cookies exported from {result.browser_source.label}")
    console.print(f"  File: {result.cookies_file}")
    console.print(f"  Auth profile: {result.profile_name}")
    if result.config_updated:
        console.print(f"  Config updated: {result.config_path}")
    elif not no_config_update:
        console.print(f"  Config unchanged: {result.config_path}")
    if verbose:
        console.print(f"  yt-dlp: {' '.join(result.yt_dlp_cmd)}")

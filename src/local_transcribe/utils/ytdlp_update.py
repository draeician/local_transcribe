"""Helpers for refreshing yt-dlp inside the running lt environment."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Sequence

RunFunc = Callable[..., subprocess.CompletedProcess[str]]

PIP_STDERR_TAIL_LINES = 20
PIP_TIMEOUT_SECONDS = 300
SUBPROCESS_TIMEOUT_SECONDS = 10


def _abspath_no_symlink(path: str | Path) -> Path:
    """Absolute path without resolving symlinks.

    pipx exposes ``venv/bin/python -> python3``; ``Path.resolve()`` would
    follow that into ``/usr/bin`` and make us prefer system yt-dlp.
    """
    return Path(os.path.abspath(os.path.expanduser(str(path))))


def runtime_bin_dir(python_executable: Optional[str] = None) -> Path:
    """Return the ``bin`` directory for the current (or given) Python runtime."""
    return _abspath_no_symlink(python_executable or sys.executable).parent


def find_yt_dlp_binary(python_executable: Optional[str] = None) -> str:
    """
    Locate the yt-dlp executable the worker / downloader should use.

    Prefer the sibling of ``sys.executable`` (pipx/venv) so ``lt update`` and
    downloads stay aligned. Fall back to PATH, then ``python -m yt_dlp``.
    """
    bin_dir = runtime_bin_dir(python_executable)
    for name in ("yt-dlp", "yt_dlp"):
        candidate = bin_dir / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)

    path_hit = shutil.which("yt-dlp") or shutil.which("yt_dlp")
    if path_hit:
        return path_hit

    py = python_executable or sys.executable
    # Last resort: invoke via the module (still works if console script missing).
    return py


def yt_dlp_invocation(
    python_executable: Optional[str] = None,
) -> List[str]:
    """Return argv prefix to run yt-dlp (binary or ``python -m yt_dlp``)."""
    binary = find_yt_dlp_binary(python_executable)
    py = _abspath_no_symlink(python_executable or sys.executable)
    if _abspath_no_symlink(binary) == py:
        return [str(py), "-m", "yt_dlp"]
    return [binary]


def build_yt_dlp_pip_command(
    python_executable: str,
    *,
    stable: bool = False,
) -> List[str]:
    """Build the pip command used to upgrade yt-dlp in the current runtime."""
    cmd = [python_executable, "-m", "pip", "install", "-U"]
    if not stable:
        cmd.append("--pre")
    cmd.append("yt-dlp[default,curl-cffi]")
    return cmd


def _parse_version_output(output: str) -> Optional[str]:
    for line in output.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return None


def _version_from_binary(binary: str, run: RunFunc) -> Optional[str]:
    try:
        result = run(
            [binary, "--version"],
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT_SECONDS,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return _parse_version_output(result.stdout or result.stderr or "")


def get_yt_dlp_version(
    python_executable: Optional[str] = None,
    *,
    run: RunFunc = subprocess.run,
) -> Optional[str]:
    """
    Return the installed yt-dlp version.

    Prefers the same resolver as the downloader (venv sibling, then PATH),
    then falls back to ``python -m yt_dlp --version``.
    """
    inv = yt_dlp_invocation(python_executable)
    if len(inv) == 1:
        version = _version_from_binary(inv[0], run)
        if version:
            return version
    else:
        try:
            result = run(
                inv + ["--version"],
                capture_output=True,
                text=True,
                timeout=SUBPROCESS_TIMEOUT_SECONDS,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            result = None
        if result is not None and result.returncode == 0:
            version = _parse_version_output(result.stdout or result.stderr or "")
            if version:
                return version

    py = python_executable or sys.executable
    return _version_via_module(py, run)


def _version_via_module(python_executable: str, run: RunFunc) -> Optional[str]:
    try:
        result = run(
            [python_executable, "-m", "yt_dlp", "--version"],
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT_SECONDS,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return _parse_version_output(result.stdout or result.stderr or "")


def resolve_deno_on_path() -> tuple[bool, Optional[str]]:
    """
    Verify Deno is on PATH and return its version first line.

    Returns:
        Tuple of (available, version_line_or_none).
    """
    deno_path = shutil.which("deno")
    if not deno_path:
        return False, None
    try:
        result = subprocess.run(
            [deno_path, "--version"],
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT_SECONDS,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False, None
    if result.returncode != 0:
        return False, None
    first_line = (result.stdout or "").splitlines()
    if not first_line:
        return True, None
    return True, first_line[0].strip()


def tail_stderr(stderr: str, *, max_lines: int = PIP_STDERR_TAIL_LINES) -> str:
    """Return the last few stderr lines for concise failure output."""
    lines = [line for line in stderr.splitlines() if line.strip()]
    if not lines:
        return ""
    return "\n".join(lines[-max_lines:])


@dataclass
class YtDlpUpdateOutcome:
    """Result of an yt-dlp update attempt."""

    success: bool
    pip_command: List[str]
    before_version: Optional[str]
    after_version: Optional[str]
    dry_run: bool
    pip_returncode: Optional[int] = None
    pip_stderr_tail: str = ""


def perform_yt_dlp_update(
    *,
    stable: bool = False,
    dry_run: bool = False,
    python_executable: Optional[str] = None,
    run: RunFunc = subprocess.run,
) -> YtDlpUpdateOutcome:
    """
    Upgrade yt-dlp in the current Python environment.

    Does not update PyTorch, CUDA, local-transcribe, or system packages.
    """
    py = python_executable or sys.executable
    pip_command = build_yt_dlp_pip_command(py, stable=stable)
    before_version = get_yt_dlp_version(py, run=run)

    if dry_run:
        return YtDlpUpdateOutcome(
            success=True,
            pip_command=pip_command,
            before_version=before_version,
            after_version=before_version,
            dry_run=True,
        )

    try:
        result = run(
            pip_command,
            capture_output=True,
            text=True,
            timeout=PIP_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return YtDlpUpdateOutcome(
            success=False,
            pip_command=pip_command,
            before_version=before_version,
            after_version=before_version,
            dry_run=False,
            pip_returncode=None,
            pip_stderr_tail="pip install timed out",
        )

    if result.returncode != 0:
        return YtDlpUpdateOutcome(
            success=False,
            pip_command=pip_command,
            before_version=before_version,
            after_version=get_yt_dlp_version(py, run=run),
            dry_run=False,
            pip_returncode=result.returncode,
            pip_stderr_tail=tail_stderr(result.stderr or ""),
        )

    after_version = get_yt_dlp_version(py, run=run)
    return YtDlpUpdateOutcome(
        success=True,
        pip_command=pip_command,
        before_version=before_version,
        after_version=after_version,
        dry_run=False,
        pip_returncode=0,
    )


def format_pip_command(cmd: Sequence[str]) -> str:
    """Format a pip argv list for display."""
    return " ".join(cmd)

#!/usr/bin/env python3
"""Portable installer for local-transcribe (CPU, NVIDIA CUDA, or AMD ROCm).

Everything is installed into a pipx-managed virtual environment: this script
never runs ``pip`` against a system interpreter, never uses ``sudo``, and never
installs packages into the environment running the script.

Profiles
--------
* ``--auto`` (default): pick the best available acceleration profile —
  CUDA when a working NVIDIA GPU/driver is detected, else ROCm when an AMD GPU
  and a ROCm runtime are detected, else CPU.
* ``--cpu``: install the CPU profile unconditionally.
* ``--cuda``: install the NVIDIA CUDA profile; fails clearly when no usable
  NVIDIA GPU/driver is present.
* ``--rocm``: install the AMD ROCm profile (official ROCm CTranslate2 wheel +
  TheRock ROCm SDK libraries); fails clearly when AMD ROCm is not usable.

Detection deliberately requires a functioning GPU probe — ``nvidia-smi`` for
NVIDIA, ``/dev/kfd`` + ``rocminfo`` for AMD — rather than the presence of a
compiler (``nvcc``) or a GPU alone. Re-running the script is safe: install
upgrade steps are idempotent, and the ROCm wheel is hash-verified and cached.

Deno is required for YouTube downloads only and is intentionally *not* a
prerequisite for installing this application or for transcribing local audio.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Tuple

# Allow the installer to reuse the in-repo ROCm helpers when run from a
# checkout (before the package is installed into the venv).
_REPO_SRC = Path(__file__).resolve().parent / "src"
if _REPO_SRC.is_dir() and str(_REPO_SRC) not in sys.path:
    sys.path.insert(0, str(_REPO_SRC))

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

DEFAULT_GIT_SOURCE = "git+https://github.com/draeician/local_transcribe.git"
PACKAGE_NAME = "local-transcribe"

PROFILE_CPU = "cpu"
PROFILE_CUDA = "cuda"
PROFILE_ROCM = "rocm"

#: CTranslate2 4.x (faster-whisper backend) links cuBLAS 12 + cuDNN 9.
CUDA_DEPENDENCIES: Tuple[str, ...] = (
    "nvidia-cublas-cu12",
    "nvidia-cudnn-cu12==9.*",
)

#: Lowest NVIDIA driver supporting the CUDA 12 runtime used by the wheels.
MINIMUM_CUDA12_DRIVER: Tuple[int, int] = (525, 60)

#: Default ROCm wheel cache directory (inside the user cache, never system).
DEFAULT_ROCM_WORK_DIR = (
    Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    / "local-transcribe"
    / "rocm"
)


class InstallerError(RuntimeError):
    """Fatal, user-actionable installer failure."""


Runner = Callable[..., "subprocess.CompletedProcess[str]"]


@dataclass(frozen=True)
class GpuInfo:
    name: str
    driver: str


# --------------------------------------------------------------------------- #
# Hardware detection (pure, injectable for tests)
# --------------------------------------------------------------------------- #


def parse_driver_version(version: str) -> Optional[Tuple[int, int]]:
    """Parse ``"535.274.02"`` -> ``(535, 274)``; ``None`` when unparseable."""
    parts = (version or "").strip().split(".")
    if not parts or not parts[0]:
        return None
    try:
        major = int(parts[0])
        minor = int(parts[1]) if len(parts) > 1 and parts[1] else 0
    except ValueError:
        return None
    return major, minor


def driver_supports_cuda12(version: str) -> bool:
    """Whether a driver version meets the CUDA 12 minimum."""
    parsed = parse_driver_version(version)
    if parsed is None:
        return False
    return parsed >= MINIMUM_CUDA12_DRIVER


def parse_nvidia_smi_output(text: str) -> List[GpuInfo]:
    """Parse ``name, driver_version`` CSV rows (one GPU per line)."""
    gpus: List[GpuInfo] = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        name, _, driver = line.partition(",")
        gpus.append(GpuInfo(name=name.strip(), driver=driver.strip()))
    return gpus


def detect_nvidia_gpus(runner: Runner | None = None) -> Tuple[List[GpuInfo], str]:
    """Return ``(gpus, detail)`` from a *functional* ``nvidia-smi`` probe.

    A missing binary, non-zero exit, timeout, or empty output yields ``[]``.
    """
    run = runner if runner is not None else subprocess.run
    try:
        result = run(
            [
                "nvidia-smi",
                "--query-gpu=name,driver_version",
                "--format=csv,noheader",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except FileNotFoundError:
        return [], "nvidia-smi not found"
    except subprocess.TimeoutExpired:
        return [], "nvidia-smi timed out"
    except Exception as exc:  # noqa: BLE001 — probe must never raise
        return [], f"nvidia-smi probe failed: {exc}"

    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        return [], f"nvidia-smi exited {result.returncode}: {detail[:200]}"
    gpus = parse_nvidia_smi_output(result.stdout or "")
    if not gpus:
        return [], "nvidia-smi reported no GPUs"
    return gpus, "; ".join(f"{g.name} (driver {g.driver})" for g in gpus)


def cuda_profile_supported(gpus: Sequence[GpuInfo]) -> Tuple[bool, str]:
    """Whether every detected GPU's driver supports the CUDA 12 profile."""
    if not gpus:
        return False, "no NVIDIA GPU detected"
    unsupported = [g for g in gpus if not driver_supports_cuda12(g.driver)]
    if unsupported:
        drivers = ", ".join(f"{g.driver or 'unknown'}" for g in unsupported)
        return False, (
            f"driver {drivers} is older than the CUDA 12 minimum "
            f"{MINIMUM_CUDA12_DRIVER[0]}.{MINIMUM_CUDA12_DRIVER[1]}"
        )
    return True, "usable NVIDIA GPU/driver detected"


def detect_amd_gpu(runner: Runner | None = None) -> Tuple[bool, str]:
    """Functional AMD GPU/ROCm probe via ``/dev/kfd`` + ``rocminfo``.

    Returns ``(available, detail)``. ``/dev/kfd`` is required for ROCm compute,
    so its absence is decisive; ``rocminfo`` then identifies the architecture.
    """
    if not Path("/dev/kfd").exists():
        return False, "/dev/kfd not present (no AMD GPU compute device)"

    run = runner if runner is not None else subprocess.run
    arch = ""
    for cmd in (["rocminfo"], ["rocm-smi", "--showproductname"]):
        try:
            result = run(cmd, capture_output=True, text=True, timeout=20)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
        except Exception:  # noqa: BLE001 — probe must never raise
            continue
        output = (result.stdout or "") + (result.stderr or "")
        for token in output.replace(",", " ").split():
            if token.startswith("gfx") and token[3:].isdigit():
                arch = token
                break
        if arch:
            break

    details = ["/dev/kfd present"]
    if arch:
        details.append(f"arch {arch}")
    return True, "; ".join(details)


def amd_architecture(runner: Runner | None = None) -> str:
    """Return the AMD GPU architecture (``gfx1151``) or ``""``."""
    ok, detail = detect_amd_gpu(runner=runner)
    if not ok:
        return ""
    for part in detail.split(";"):
        part = part.strip()
        if part.startswith("arch "):
            return part[len("arch "):].strip()
    return ""


def rocm_profile_supported(
    arch: Optional[str] = None, runner: Runner | None = None
) -> Tuple[bool, str]:
    """Whether the pinned ROCm SDK can accelerate transcription here.

    Requires an AMD GPU compute device and a GPU architecture covered by the
    pinned TheRock ROCm SDK library wheels.
    """
    if arch is None:
        arch = amd_architecture(runner=runner)
    if not Path("/dev/kfd").exists():
        return False, "/dev/kfd not present (no AMD GPU compute device)"
    if not arch:
        # /dev/kfd exists but the architecture could not be read; assume the
        # pinned target rather than refusing outright.
        return True, "AMD GPU compute device present (architecture unknown)"
    from local_transcribe.utils.rocm import SUPPORTED_ROCM_SDK_TARGETS

    if arch not in SUPPORTED_ROCM_SDK_TARGETS:
        return False, (
            f"AMD GPU arch {arch} has no pinned ROCm SDK library wheels; "
            f"supported: {', '.join(SUPPORTED_ROCM_SDK_TARGETS)}"
        )
    return True, f"AMD GPU {arch} with ROCm runtime available"


def choose_profile(
    request: str,
    gpus: Sequence[GpuInfo],
    *,
    amd_available: bool = False,
    amd_reason: str = "",
) -> str:
    """Resolve a profile request into ``cpu`` / ``cuda`` / ``rocm``.

    ``gpus``/``amd_available`` come from injected detection so this is testable.
    Raises :class:`InstallerError` only for an explicit profile request that
    cannot be satisfied (failing loudly is correct there).
    """
    request = (request or PROFILE_CPU).strip().lower()
    cuda_supported, cuda_reason = cuda_profile_supported(gpus)

    if request == PROFILE_CUDA:
        if not cuda_supported:
            raise InstallerError(
                f"--cuda requested but CUDA is not usable here: {cuda_reason}. "
                "Install a compatible NVIDIA driver (>= "
                f"{MINIMUM_CUDA12_DRIVER[0]}.{MINIMUM_CUDA12_DRIVER[1]}) or use --cpu / --auto."
            )
        return PROFILE_CUDA

    if request == PROFILE_ROCM:
        if not amd_available:
            raise InstallerError(
                f"--rocm requested but AMD ROCm is not usable here: "
                f"{amd_reason or 'no AMD GPU/ROCm runtime detected'}. Install a ROCm "
                "runtime and ensure /dev/kfd is accessible, or use --cpu / --auto."
            )
        return PROFILE_ROCM

    if request == PROFILE_CPU:
        return PROFILE_CPU

    if request != "auto":
        raise InstallerError(f"unknown profile: {request!r}")

    # auto: prefer CUDA, then ROCm, then CPU.
    if cuda_supported:
        return PROFILE_CUDA
    if amd_available:
        return PROFILE_ROCM
    if gpus:
        print(
            f"note: NVIDIA GPU present but CUDA profile not selected ({cuda_reason}); "
            "installing the CPU profile.",
            file=sys.stderr,
        )
    elif Path("/dev/kfd").exists():
        print(
            f"note: AMD GPU present but ROCm profile not selected "
            f"({amd_reason or 'unsupported'}); installing the CPU profile.",
            file=sys.stderr,
        )
    else:
        print(
            "note: no supported GPU detected; installing the CPU profile.",
            file=sys.stderr,
        )
    return PROFILE_CPU


# --------------------------------------------------------------------------- #
# pipx command construction
# --------------------------------------------------------------------------- #


def resolve_source(source: Optional[str], cwd: Path) -> str:
    """Default to the local checkout when run inside one, else the git URL."""
    if source:
        return source
    if (cwd / "pyproject.toml").is_file():
        return "."
    return DEFAULT_GIT_SOURCE


def build_install_command(source: str) -> List[str]:
    """``pipx install --force`` is safe for first-time *and* existing installs."""
    return ["pipx", "install", "--force", source]


def build_inject_command(profile: str) -> Optional[List[str]]:
    """CUDA extra packages, injected into the pipx venv (idempotent with --force)."""
    if profile != PROFILE_CUDA:
        return None
    return ["pipx", "inject", "--force", PACKAGE_NAME, *CUDA_DEPENDENCIES]


def install(
    *,
    source: str,
    profile: str,
    runner: Optional[Runner] = None,
    dry_run: bool = False,
    verify: bool = True,
    rocm_work_dir: Optional[Path] = None,
    rocm_target: Optional[str] = None,
) -> List[List[str]]:
    """Install/upgrade the package and inject profile-specific acceleration.

    For CUDA the extras are injected directly. For ROCm the pinned official
    CTranslate2 ROCm wheel and the TheRock ROCm SDK wheels are downloaded,
    hash-verified, and injected (``--no-deps`` so pip never substitutes the PyPI
    CUDA ``ctranslate2`` wheel).

    Returns the list of commands that were run (or would be, under ``dry_run``).
    """
    if not dry_run and not pipx_available():
        raise InstallerError(
            "pipx is required but was not found on PATH. Install pipx first "
            "(e.g. `python3 -m pip install --user pipx` on your OS, or your "
            "distribution package), then re-run this script."
        )

    commands: List[List[str]] = []

    def run(command: Sequence[str]) -> None:
        run_command(command, runner=runner, dry_run=dry_run)
        commands.append(list(command))

    run(build_install_command(source))

    if profile == PROFILE_CUDA:
        inject = build_inject_command(profile)
        if inject is not None:
            run(inject)
    elif profile == PROFILE_ROCM:
        commands.extend(_install_rocm(source, runner=runner, dry_run=dry_run, target=rocm_target))

    if verify and not dry_run:
        _verify_installed(profile, runner=runner)

    return commands


def _install_rocm(
    source: str,
    *,
    runner: Optional[Runner] = None,
    dry_run: bool = False,
    target: Optional[str] = None,
) -> List[List[str]]:
    """Install the ROCm CTranslate2 wheel + ROCm SDK into the pipx venv."""
    from local_transcribe.utils import rocm

    arch = target or amd_architecture(runner=runner) or "gfx1151"
    plan = rocm.rocm_wheel_plan(target=arch if arch in rocm.SUPPORTED_ROCM_SDK_TARGETS else "gfx1151")
    if dry_run:
        plan_commands = [["pipx", "inject", "...", "rocm-sdk", *plan.rocm_sdk_requirements]]
        for command in plan_commands:
            run_command(command, runner=runner, dry_run=True)
        return plan_commands

    work_dir = DEFAULT_ROCM_WORK_DIR / f"py{plan.python_tag}"
    print(f"[install] obtaining ROCm CTranslate2 wheel for {plan.python_tag}…")
    try:
        wheel, plan = rocm.prepare_rocm_wheels(work_dir, python_tag=plan.python_tag, target=arch)
    except rocm.RocmInstallError as exc:
        raise InstallerError(str(exc)) from exc
    print(f"[install] verified {wheel.name}")

    commands: List[List[str]] = []
    for command in rocm.build_rocm_install_steps(plan, wheel):
        run_command(command, runner=runner)
        commands.append(command)
    return commands


def build_verify_commands() -> List[List[str]]:
    return [
        ["lt", "version"],
        ["lt", "doctor"],
        ["pipx", "runpip", PACKAGE_NAME, "list"],
    ]


def _pipx_bin_dir() -> Optional[Path]:
    raw = os.environ.get("PIPX_BIN_DIR")
    if raw:
        return Path(raw)
    return Path.home() / ".local" / "bin"


def _venv_bin_dir() -> Optional[Path]:
    python = _venv_python()
    return python.parent if python is not None else None


def resolve_lt_executable() -> str:
    """Locate the installed ``lt`` even when the pipx bin dir is not on PATH."""
    found = shutil.which("lt")
    if found:
        return found
    venv_bin = _venv_bin_dir()
    if venv_bin is not None:
        candidate = venv_bin / "lt"
        if candidate.is_file():
            return str(candidate)
    bin_dir = _pipx_bin_dir()
    if bin_dir is not None:
        candidate = bin_dir / "lt"
        if candidate.is_file():
            return str(candidate)
    return "lt"


def _subprocess_env() -> dict:
    """Environment for install/verify commands: pipx bin dir on PATH."""
    env = dict(os.environ)
    bin_dir = _pipx_bin_dir()
    venv_bin = _venv_bin_dir()
    parts = [str(p) for p in (venv_bin, bin_dir) if p is not None]
    if parts:
        env["PATH"] = os.pathsep.join(parts + [env.get("PATH", "")])
    return env


def runner_or_default(runner: Optional[Runner]) -> Runner:
    return runner if runner is not None else subprocess.run


def run_command(
    command: Sequence[str],
    *,
    runner: Optional[Runner] = None,
    dry_run: bool = False,
    check: bool = True,
) -> int:
    """Run one command, echoing it. Never shells out (no ``shell=True``)."""
    rendered = " ".join(command)
    if dry_run:
        print(f"[dry-run] {rendered}")
        return 0
    print(f"[install] {rendered}")
    if runner is None:
        result = subprocess.run(list(command), text=True, env=_subprocess_env())
    else:
        result = runner(list(command), text=True)
    code = getattr(result, "returncode", 1)
    if check and code != 0:
        raise InstallerError(f"command failed ({code}): {rendered}")
    return code


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #


def pipx_available() -> bool:
    return shutil.which("pipx") is not None


def _venv_python() -> Optional[Path]:
    """Best-effort path to the pipx venv interpreter (no ``pipx runpython``)."""
    home = os.environ.get("PIPX_HOME")
    candidates = []
    if home:
        candidates.append(Path(home) / "venvs" / PACKAGE_NAME / "bin" / "python")
    candidates.append(
        Path.home() / ".local" / "share" / "pipx" / "venvs" / PACKAGE_NAME / "bin" / "python"
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def verify_gpu_in_venv(runner: Optional[Runner] = None) -> Tuple[bool, str]:
    """Confirm CTranslate2 can use a GPU inside the installed venv.

    The probe imports the installed package so the ROCm preload path runs
    exactly as it will for a real job, and it requires a non-zero device count
    *and* supported compute types — not merely a successful process exit.
    """
    python = _venv_python()
    if python is None:
        return False, "pipx venv python not found; run `lt doctor` to check GPU support"
    probe = (
        "from local_transcribe.utils import hardware as h;"
        "h.prepare_gpu_runtime();"
        "print('backend', h.gpu_backend());"
        "print('gpu_devices', __import__('ctranslate2').get_cuda_device_count());"
        "print('gpu_types', sorted(h.supported_compute_types('cuda')))"
    )
    try:
        result = runner_or_default(runner)(
            [str(python), "-c", probe], capture_output=True, text=True, timeout=180
        )
    except Exception as exc:  # noqa: BLE001
        return False, f"GPU verification failed to run: {exc}"
    output = (result.stdout or "").strip()
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        return False, f"GPU not usable in installed venv: {detail[-300:]}"

    devices = _parse_probe_int(output, "gpu_devices")
    types = _parse_probe_list(output, "gpu_types")
    if not devices:
        return False, f"no GPU visible to CTranslate2 in the installed venv ({output})"
    if not types:
        return False, f"no GPU compute types reported by CTranslate2 ({output})"
    return True, output


def _parse_probe_int(output: str, key: str) -> int:
    for line in output.splitlines():
        fields = line.split(None, 1)
        if len(fields) == 2 and fields[0] == key:
            try:
                return int(fields[1].strip())
            except ValueError:
                return 0
    return 0


def _parse_probe_list(output: str, key: str) -> List[str]:
    for line in output.splitlines():
        fields = line.split(None, 1)
        if len(fields) == 2 and fields[0] == key:
            inner = fields[1].strip().strip("[]")
            return [item.strip().strip("'\"") for item in inner.split(",") if item.strip()]
    return []


def verify_cuda_in_venv(runner: Optional[Runner] = None) -> Tuple[bool, str]:
    """Backward-compatible alias for :func:`verify_gpu_in_venv`."""
    return verify_gpu_in_venv(runner=runner)


def _verify_installed(profile: str, runner: Optional[Runner] = None) -> None:
    print("[install] verifying installation…")
    run_command([resolve_lt_executable(), "version"], runner=runner)
    if profile == PROFILE_CUDA:
        ok, detail = verify_gpu_in_venv(runner=runner)
        if ok:
            print(f"[install] CUDA backend verified: {detail}")
        else:
            print(
                f"[install] warning: CUDA packages installed but not usable ({detail}). "
                "Local transcription still works on CPU; run `lt doctor` for details.",
                file=sys.stderr,
            )
    elif profile == PROFILE_ROCM:
        ok, detail = verify_gpu_in_venv(runner=runner)
        if ok and "backend amd-rocm" in detail:
            print(f"[install] ROCm backend verified: {detail}")
        elif ok:
            print(
                f"[install] warning: GPU usable but not detected as AMD ROCm ({detail}). "
                "Run `lt doctor` for details.",
                file=sys.stderr,
            )
        else:
            print(
                f"[install] warning: ROCm packages installed but not usable ({detail}). "
                "Run `lt doctor`; you may need a ROCm runtime visible to the venv.",
                file=sys.stderr,
            )


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="install_local_transcribe.py",
        description="Install local-transcribe (CPU, NVIDIA CUDA, or AMD ROCm) into a pipx venv.",
    )
    parser.add_argument(
        "source",
        nargs="?",
        help="install source: '.' (default in a checkout) or a git URL",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--auto",
        dest="profile",
        action="store_const",
        const="auto",
        help="detect supported NVIDIA/AMD GPU hardware, else install CPU (default)",
    )
    group.add_argument(
        "--cpu",
        dest="profile",
        action="store_const",
        const=PROFILE_CPU,
        help="install CPU-only dependencies",
    )
    group.add_argument(
        "--cuda",
        dest="profile",
        action="store_const",
        const=PROFILE_CUDA,
        help="install the NVIDIA CUDA profile (errors when CUDA is unsupported)",
    )
    group.add_argument(
        "--rocm",
        dest="profile",
        action="store_const",
        const=PROFILE_ROCM,
        help="install the AMD ROCm profile (errors when ROCm is unsupported)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the commands without executing them",
    )
    parser.add_argument(
        "--no-verify",
        action="store_true",
        help="skip the post-install verification step",
    )
    parser.set_defaults(profile="auto")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    cwd = Path.cwd()
    source = resolve_source(args.source, cwd)

    try:
        gpus, nvidia_detail = detect_nvidia_gpus()
        amd_ok, amd_detail = detect_amd_gpu()
        profile = choose_profile(
            args.profile,
            gpus,
            amd_available=amd_ok,
            amd_reason=amd_detail,
        )
    except InstallerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if gpus:
        print(f"[install] NVIDIA GPU probe: {nvidia_detail}")
    print(f"[install] AMD GPU probe: {amd_detail}")
    print(f"[install] profile: {profile}")

    try:
        install(
            source=source,
            profile=profile,
            dry_run=args.dry_run,
            verify=not args.no_verify,
            rocm_target=amd_architecture() or None,
        )
    except InstallerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print("[install] done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

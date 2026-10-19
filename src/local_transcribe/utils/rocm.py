"""ROCm (AMD GPU) support for local-transcribe.

Two independent pieces live here because they run in different environments:

* :func:`rocm_wheel_plan` / :func:`download_rocm_ctranslate2_wheel` are used by
  ``install_local_transcribe.py`` to obtain the official ROCm/HIP CTranslate2
  wheel and the TheRock ROCm SDK wheels, and to install them into a pipx venv.
  They deliberately avoid a hard dependency on any package in this repository so
  the installer can run against a checkout that is not yet installed.
* The runtime pieces (library discovery and preloading) live in
  :mod:`local_transcribe.utils.hardware`, because they must run inside the
  worker process.

Design constraints captured from the Aether investigation:

* The PyPI ``ctranslate2`` wheel is *not* the ROCm build. The official ROCm
  wheels are published only as a GitHub release archive.
* CTranslate2's ROCm wheel links against ROCm 7.x sonames
  (``libamdhip64.so.7``, ``libhipblas.so.3``, ``libhiprand.so.1``) and has no
  RPATH to them. A host whose *distro* ROCm is older (Aether ships 5.7/5.5)
  cannot satisfy them from ``/opt/rocm``. TheRock's ROCm SDK ships the matching
  libraries as ordinary Python wheels (``rocm-sdk-core`` +
  ``rocm-sdk-libraries-gfxNNNN``), which install into the venv without root and
  are preloaded at runtime — so no global library-path change is required.
* The ROCm CTranslate2 build keeps CTranslate2's internal ``cuda`` device name;
  ``device="hip"`` is never used.
"""

from __future__ import annotations

import hashlib
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

#: Pinned official CTranslate2 ROCm release (GitHub).
CTRANSLATE2_ROCM_VERSION = "4.8.3"
CTRANSLATE2_ROCM_RELEASE_URL = (
    "https://github.com/OpenNMT/CTranslate2/releases/download/"
    f"v{CTRANSLATE2_ROCM_VERSION}/rocm-python-wheels-Linux.zip"
)
#: SHA-256 of the release archive, verified during download.
CTRANSLATE2_ROCM_ARCHIVE_SHA256 = (
    "132eda022faed55e4d1a7093f15a9dd9d4bcdf26bd72471ec1c984bada9a3d3f"
)

#: Pinned TheRock ROCm SDK release (AMD index) that provides gfx1151 kernels and
#: the ROCm 7.x sonames the CTranslate2 wheel needs.
ROCM_SDK_VERSION = "7.13.0a20260515"
ROCM_SDK_INDEX_URL = "https://rocm.nightlies.amd.com/v2/gfx1151/"

#: A GPUs whose ROCm SDK library wheels are known to ship gfx115x device code.
SUPPORTED_ROCM_SDK_TARGETS: Tuple[str, ...] = ("gfx1151", "gfx1150")

_ARCHIVE_MEMBER_TEMPLATE = (
    "temp-linux/ctranslate2-{version}-{python_tag}-{python_tag}-"
    "manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl"
)


class RocmInstallError(RuntimeError):
    """User-actionable ROCm installation failure."""


@dataclass(frozen=True)
class RocmWheelPlan:
    """Everything needed to install ROCm CTranslate2 into a venv."""

    ctranslate2_archive_url: str
    ctranslate2_archive_sha256: str
    ctranslate2_wheel_member: str
    rocm_sdk_index_url: str
    rocm_sdk_requirements: Tuple[str, ...]
    python_tag: str


def python_tag_for(version: Optional[Tuple[int, int]] = None) -> str:
    """Return the wheel ABI tag (``cp313``) for a Python version.

    Only the interpreter ABI matters, not the OS, so this is stable across
    platforms for CPython.
    """
    if version is None:
        version = (sys.version_info.major, sys.version_info.minor)
    return f"cp{version[0]}{version[1]}"


def supported_python_tag(python_tag: str) -> bool:
    """Whether the pinned release archive contains a wheel for ``python_tag``."""
    return python_tag in {f"cp{m}{n}" for m in (3,) for n in range(9, 15)}


def rocm_wheel_plan(
    *,
    python_tag: Optional[str] = None,
    target: str = "gfx1151",
) -> RocmWheelPlan:
    """Describe the pinned ROCm installation for this interpreter."""
    tag = python_tag or python_tag_for()
    if not supported_python_tag(tag):
        raise RocmInstallError(
            f"no ROCm CTranslate2 wheel for Python ABI {tag!r}; the pinned "
            f"{CTRANSLATE2_ROCM_VERSION} release ships cp309-cp314."
        )
    if target not in SUPPORTED_ROCM_SDK_TARGETS:
        raise RocmInstallError(
            f"no pinned ROCm SDK libraries for {target!r}; supported targets: "
            f"{', '.join(SUPPORTED_ROCM_SDK_TARGETS)}."
        )
    member = _ARCHIVE_MEMBER_TEMPLATE.format(
        version=CTRANSLATE2_ROCM_VERSION, python_tag=tag
    )
    return RocmWheelPlan(
        ctranslate2_archive_url=CTRANSLATE2_ROCM_RELEASE_URL,
        ctranslate2_archive_sha256=CTRANSLATE2_ROCM_ARCHIVE_SHA256,
        ctranslate2_wheel_member=member,
        rocm_sdk_index_url=ROCM_SDK_INDEX_URL,
        rocm_sdk_requirements=(
            f"rocm-sdk-core=={ROCM_SDK_VERSION}",
            f"rocm-sdk-libraries-{target}=={ROCM_SDK_VERSION}",
        ),
        python_tag=tag,
    )


def sha256_file(path: Path) -> str:
    """SHA-256 of a file, streamed (safe for large archives)."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_archive(path: Path, expected_sha256: str) -> None:
    """Raise :class:`RocmInstallError` when ``path`` does not match the hash."""
    actual = sha256_file(path)
    if actual.lower() != expected_sha256.lower():
        raise RocmInstallError(
            f"integrity check failed for {path.name}: expected sha256 "
            f"{expected_sha256}, got {actual}"
        )


def download_file(url: str, destination: Path, *, timeout: int = 1800) -> Path:
    """Stream ``url`` to ``destination`` (no shell, no global installs)."""
    import urllib.request

    destination.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": "local-transcribe-installer"})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        with open(destination, "wb") as handle:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                handle.write(chunk)
    return destination


def extract_wheel_member(archive: Path, member: str, destination_dir: Path) -> Path:
    """Extract a single archive member into ``destination_dir``."""
    destination_dir.mkdir(parents=True, exist_ok=True)
    target = destination_dir / Path(member).name
    with zipfile.ZipFile(archive) as zf:
        try:
            info = zf.getinfo(member)
        except KeyError as exc:
            available = [n for n in zf.namelist() if n.endswith(".whl")]
            raise RocmInstallError(
                f"{member!r} not found in {archive.name}; available: "
                f"{', '.join(Path(a).name for a in available) or 'none'}"
            ) from exc
        with zf.open(info) as source, open(target, "wb") as out:
            out.write(source.read())
    return target


def prepare_rocm_wheels(
    work_dir: Path,
    *,
    python_tag: Optional[str] = None,
    target: str = "gfx1151",
    download: bool = True,
) -> Tuple[Path, RocmWheelPlan]:
    """Obtain and verify the CTranslate2 ROCm wheel, returning its path.

    The archive is cached in ``work_dir`` so repeated installer runs do not
    re-download ~290 MB.
    """
    plan = rocm_wheel_plan(python_tag=python_tag, target=target)
    work_dir.mkdir(parents=True, exist_ok=True)
    archive = work_dir / f"rocm-python-wheels-{CTRANSLATE2_ROCM_VERSION}-Linux.zip"

    if not archive.is_file():
        if not download:
            raise RocmInstallError(
                f"ROCm wheel archive not present at {archive} and downloads are disabled"
            )
        download_file(plan.ctranslate2_archive_url, archive)

    verify_archive(archive, plan.ctranslate2_archive_sha256)
    wheel = extract_wheel_member(
        archive, plan.ctranslate2_wheel_member, work_dir / "extracted"
    )
    return wheel, plan


def build_rocm_install_steps(plan: RocmWheelPlan, wheel_path: Path) -> List[List[str]]:
    """pipx commands to install the ROCm stack into the pipx venv.

    ``--no-deps`` is used for both injections so pip never re-resolves
    ``ctranslate2`` (which would silently fetch the PyPI CUDA wheel) and never
    pulls unrelated packages into the isolated venv.
    """
    return [
        [
            "pipx",
            "inject",
            "--force",
            "--pip-args",
            "--no-deps",
            "--index-url",
            plan.rocm_sdk_index_url,
            "local-transcribe",
            *plan.rocm_sdk_requirements,
        ],
        [
            "pipx",
            "inject",
            "--force",
            "--pip-args",
            "--no-deps",
            "local-transcribe",
            str(wheel_path),
        ],
    ]

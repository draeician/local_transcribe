"""AMD ROCm support: detection, install planning, runtime selection, diagnostics.

These tests are hermetic: GPU detection is injected, and no ROCm libraries are
required. They pin the behaviour established during the Aether investigation —
notably that CTranslate2 keeps the ``cuda`` device name for its ROCm build, that
an explicit ROCm request never silently falls back to CPU, and that the ROCm
wheel is hash-verified.
"""

from __future__ import annotations

import importlib.util
import sys
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
INSTALLER_PATH = REPO_ROOT / "install_local_transcribe.py"


def _load_installer():
    spec = importlib.util.spec_from_file_location("lt_installer_rocm", INSTALLER_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["lt_installer_rocm"] = module
    spec.loader.exec_module(module)
    return module


installer = _load_installer()

from local_transcribe.utils import hardware, rocm  # noqa: E402


class FakeCompleted:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class FakeRunner:
    def __init__(self, response: FakeCompleted | None = None) -> None:
        self.response = response or FakeCompleted()
        self.calls: list[list[str]] = []

    def __call__(self, command, **kwargs):  # type: ignore[no-untyped-def]
        self.calls.append(list(command))
        return self.response


# --------------------------------------------------------------------------- #
# Detection
# --------------------------------------------------------------------------- #


def test_detect_amd_gpu_reads_architecture(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(Path, "exists", lambda self: str(self) == "/dev/kfd")
    rocminfo = (
        "  Name:                    gfx1151\n"
        "  Marketing Name:          Radeon 8060S Graphics\n"
    )
    ok, detail = hardware.detect_amd_gpu(lambda *a, **k: FakeCompleted(0, rocminfo))
    assert ok
    assert "gfx1151" in detail
    assert "arch gfx1151" in detail


def test_detect_amd_gpu_absent_without_kfd(monkeypatch) -> None:
    monkeypatch.setattr(Path, "exists", lambda self: False)
    ok, detail = hardware.detect_amd_gpu(lambda *a, **k: FakeCompleted(0, "gfx1151"))
    assert ok is False
    assert "/dev/kfd" in detail


def test_amd_architecture_helper(monkeypatch) -> None:
    monkeypatch.setattr(hardware, "detect_amd_gpu", lambda run=None: (True, "/dev/kfd present; arch gfx1151"))
    assert hardware.amd_gpu_architecture() == "gfx1151"
    monkeypatch.setattr(hardware, "detect_amd_gpu", lambda run=None: (False, "none"))
    assert hardware.amd_gpu_architecture() == ""


# --------------------------------------------------------------------------- #
# ROCm library discovery + preload safety
# --------------------------------------------------------------------------- #


def test_rocm_library_dirs_find_sdk_packages(monkeypatch, tmp_path: Path) -> None:
    core = tmp_path / "_rocm_sdk_core" / "lib"
    libs = tmp_path / "_rocm_sdk_libraries_gfx1151" / "lib"
    core.mkdir(parents=True)
    libs.mkdir(parents=True)
    monkeypatch.setattr(sys, "path", [str(tmp_path)])
    dirs = hardware.rocm_sdk_library_dirs()
    assert core in dirs
    assert libs in dirs


def test_preload_skips_non_rocm_libraries(monkeypatch, tmp_path: Path) -> None:
    d = tmp_path / "lib"
    d.mkdir()
    (d / "libasan.so.8").write_bytes(b"not-rocm")
    (d / "libamdhip64.so.7").write_bytes(b"not-a-real-so")
    calls: list[str] = []

    def fake_cdll(path, mode=None):  # type: ignore[no-untyped-def]
        calls.append(path)
        raise OSError("not a library")

    monkeypatch.setattr(hardware.ctypes, "CDLL", fake_cdll)
    hardware.preload_rocm_libraries([d])
    assert all("libasan" not in c for c in calls), calls
    assert any("libamdhip64" in c for c in calls), calls


def test_preload_is_idempotent(monkeypatch, tmp_path: Path) -> None:
    d = tmp_path / "_rocm_sdk_core" / "lib"
    d.mkdir(parents=True)
    (d / "libamdhip64.so.7").write_bytes(b"x")
    loads: list[str] = []

    def fake_cdll(path, mode=None):  # type: ignore[no-untyped-def]
        loads.append(path)
        return object()

    monkeypatch.setattr(hardware.ctypes, "CDLL", fake_cdll)
    hardware._PRELOADED.clear()
    first, _ = hardware.preload_rocm_libraries([d])
    second, _ = hardware.preload_rocm_libraries([d])
    assert first == 1
    assert second == 0
    assert len(loads) == 1


# --------------------------------------------------------------------------- #
# Backend identification (ROCm vs CUDA vs CPU)
# --------------------------------------------------------------------------- #


def test_gpu_backend_detects_rocm(monkeypatch) -> None:
    monkeypatch.setattr(
        hardware,
        "_loaded_gpu_libraries",
        lambda: {"libamdhip64.so.7", "libhipblas.so.3"},
    )
    assert hardware.gpu_backend() == hardware.BACKEND_ROCM


def test_gpu_backend_detects_cuda(monkeypatch) -> None:
    monkeypatch.setattr(
        hardware, "_loaded_gpu_libraries", lambda: {"libcudart.so.12", "libcublas.so.12"}
    )
    assert hardware.gpu_backend() == hardware.BACKEND_CUDA


def test_gpu_backend_cpu_when_nothing_loaded(monkeypatch) -> None:
    monkeypatch.setattr(hardware, "_loaded_gpu_libraries", lambda: set())
    assert hardware.gpu_backend() == hardware.BACKEND_CPU


# --------------------------------------------------------------------------- #
# Runtime selection
# --------------------------------------------------------------------------- #


def test_auto_selects_gpu_when_rocm_usable(monkeypatch) -> None:
    monkeypatch.setattr(hardware, "gpu_runtime_status", lambda: (True, ""))
    monkeypatch.setattr(hardware, "gpu_backend", lambda: hardware.BACKEND_ROCM)
    monkeypatch.setattr(
        hardware, "supported_compute_types", lambda dev: ("float16", "int8")
    )
    device, compute = hardware.resolve_device_and_compute("auto", "auto")
    # CTranslate2 keeps the 'cuda' device name even on its ROCm build.
    assert device == "cuda"
    assert compute == "float16"


def test_explicit_rocm_uses_rocm_backend(monkeypatch) -> None:
    monkeypatch.setattr(hardware, "gpu_runtime_status", lambda: (True, ""))
    monkeypatch.setattr(hardware, "gpu_backend", lambda: hardware.BACKEND_ROCM)
    monkeypatch.setattr(hardware, "supported_compute_types", lambda dev: ("float16",))
    device, compute = hardware.resolve_device_and_compute("rocm", "auto")
    assert (device, compute) == ("cuda", "float16")


def test_explicit_rocm_errors_when_unusable(monkeypatch) -> None:
    monkeypatch.setattr(
        hardware, "gpu_runtime_status", lambda: (False, "no ROCm GPU visible")
    )
    with pytest.raises(hardware.BackendUnavailableError) as exc:
        hardware.resolve_device_and_compute("rocm", "auto")
    assert "ROCm" in str(exc.value)


def test_explicit_rocm_errors_when_wrong_build(monkeypatch) -> None:
    monkeypatch.setattr(hardware, "gpu_runtime_status", lambda: (True, ""))
    monkeypatch.setattr(hardware, "gpu_backend", lambda: hardware.BACKEND_CUDA)
    with pytest.raises(hardware.BackendUnavailableError) as exc:
        hardware.resolve_device_and_compute("rocm", "auto")
    assert "not the ROCm/HIP build" in str(exc.value)


def test_auto_falls_back_to_cpu_when_rocm_unusable(monkeypatch) -> None:
    monkeypatch.setattr(hardware, "gpu_runtime_status", lambda: (False, "no gpu"))
    device, compute = hardware.resolve_device_and_compute("auto", "auto")
    assert (device, compute) == ("cpu", "int8")


def test_describe_backend_labels_rocm(monkeypatch) -> None:
    monkeypatch.setattr(hardware, "gpu_runtime_status", lambda: (True, ""))
    monkeypatch.setattr(hardware, "gpu_backend", lambda: hardware.BACKEND_ROCM)
    monkeypatch.setattr(hardware, "supported_compute_types", lambda dev: ("float16",))
    assert hardware.describe_backend("auto", "auto") == "AMD ROCm (float16)"


def test_transcriber_resolves_rocm_and_sets_effective_backend(monkeypatch) -> None:
    from local_transcribe.services import transcriber

    monkeypatch.setattr(hardware, "gpu_runtime_status", lambda: (True, ""))
    monkeypatch.setattr(hardware, "gpu_backend", lambda: hardware.BACKEND_ROCM)
    monkeypatch.setattr(hardware, "supported_compute_types", lambda dev: ("float16",))
    device, compute = transcriber.resolve_device_and_compute("auto", "auto")
    assert (device, compute) == ("cuda", "float16")
    assert transcriber.effective_backend("auto", "auto") == hardware.BACKEND_ROCM


def test_transcriber_rocm_request_without_gpu_raises(monkeypatch) -> None:
    from local_transcribe.services import transcriber

    monkeypatch.setattr(hardware, "gpu_runtime_status", lambda: (False, "no gpu"))
    with pytest.raises(hardware.BackendUnavailableError):
        transcriber.resolve_device_and_compute("rocm", "auto")


# --------------------------------------------------------------------------- #
# Queue: worker resolves auto without producer modification
# --------------------------------------------------------------------------- #


def test_queue_options_remain_hardware_agnostic() -> None:
    from local_transcribe.services.queue_models import ExecutionOptions

    opts = ExecutionOptions()
    assert (opts.device, opts.compute_type) == ("auto", "auto")
    # Round-trips through JSON unchanged (existing queue records stay valid).
    assert ExecutionOptions.from_dict(opts.to_dict()) == opts


def test_worker_job_runner_resolves_rocm_on_worker(tmp_path: Path, monkeypatch) -> None:
    from local_transcribe.services import job_runner as jr_mod
    from local_transcribe.services.job_runner import ProductionJobRunner
    from local_transcribe.services.model_cache import ModelCache
    from local_transcribe.services.queue_models import Execution, ExecutionOptions

    monkeypatch.setattr(hardware, "gpu_runtime_status", lambda: (True, ""))
    monkeypatch.setattr(hardware, "gpu_backend", lambda: hardware.BACKEND_ROCM)
    monkeypatch.setattr(hardware, "supported_compute_types", lambda dev: ("float16",))
    monkeypatch.setattr(
        jr_mod, "resolve_device_and_compute", hardware.resolve_device_and_compute
    )
    monkeypatch.setattr(jr_mod, "transcribe_with_model", lambda *a, **k: "rocm text")

    loaded: list[tuple[str, str, str]] = []
    cache = ModelCache(lambda m, d, c: (loaded.append((m, d, c)) or object()))
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"RIFF")
    transcripts = tmp_path / "tx"
    transcripts.mkdir()
    runner = ProductionJobRunner(
        transcripts_root=transcripts, model_cache=cache, scratch_root=tmp_path / "s"
    )
    result = runner.run(
        Execution(
            execution_id="e1",
            source_key="local:abc",
            generation=1,
            source=str(audio),
            source_type="local_file",
            options=ExecutionOptions(),  # producer default: auto/auto
        )
    )
    assert result.effective_device == "cuda"
    assert result.effective_compute_type == "float16"
    assert loaded == [("medium", "cuda", "float16")]


# --------------------------------------------------------------------------- #
# Doctor: three-way backend reporting
# --------------------------------------------------------------------------- #


def _stub_doctor(monkeypatch, *, nvidia, amd, gpu_ok, backend):
    from local_transcribe.utils import doctor

    monkeypatch.setattr(doctor, "check_ffmpeg", lambda: (True, "ok"))
    monkeypatch.setattr(doctor, "check_yt_dlp", lambda: (True, "ok"))
    monkeypatch.setattr(doctor, "check_required_modules", lambda: (True, "present"))
    monkeypatch.setattr(doctor, "check_cpu_backend", lambda: (True, "cpu ok"))
    monkeypatch.setattr(doctor, "check_nvidia_smi", lambda: nvidia)
    monkeypatch.setattr(doctor, "check_amd_gpu", lambda: amd)
    monkeypatch.setattr(doctor, "check_rocm_sdk", lambda: (True, "rocm dirs"))
    monkeypatch.setattr(doctor, "check_gpu_runtime", lambda: gpu_ok)
    monkeypatch.setattr(doctor, "check_deno", lambda: (True, "deno"))
    monkeypatch.setattr(hardware, "gpu_backend", lambda: backend)
    monkeypatch.setattr(hardware, "amd_gpu_architecture", lambda run=None: "gfx1151")
    monkeypatch.setattr(hardware, "supported_compute_types", lambda dev: ("float16",))
    return doctor


def test_doctor_reports_rocm_backend(monkeypatch) -> None:
    doctor = _stub_doctor(
        monkeypatch,
        nvidia=(False, "nvidia-smi not found"),
        amd=(True, "/dev/kfd present; arch gfx1151"),
        gpu_ok=(True, "usable"),
        backend=hardware.BACKEND_ROCM,
    )
    checks = {c.name: c for c in doctor.run_diagnostics()}
    assert "AMD ROCm" in checks["Acceleration Backend"].detail
    assert "gfx1151" in checks["Acceleration Backend"].detail
    assert checks["ctranslate2 GPU"].ok is True
    assert checks["AMD GPU (/dev/kfd, rocminfo)"].ok is True


def test_doctor_reports_cuda_backend(monkeypatch) -> None:
    doctor = _stub_doctor(
        monkeypatch,
        nvidia=(True, "RTX 4090"),
        amd=(False, "no kfd"),
        gpu_ok=(True, "usable"),
        backend=hardware.BACKEND_CUDA,
    )
    checks = {c.name: c for c in doctor.run_diagnostics()}
    assert "NVIDIA CUDA" in checks["Acceleration Backend"].detail


def test_doctor_reports_cpu_backend_on_cpu_host(monkeypatch) -> None:
    doctor = _stub_doctor(
        monkeypatch,
        nvidia=(False, "nvidia-smi not found"),
        amd=(False, "/dev/kfd not present"),
        gpu_ok=(False, "no GPU"),
        backend=hardware.BACKEND_CPU,
    )
    checks = {c.name: c for c in doctor.run_diagnostics()}
    assert checks["Acceleration Backend"].detail == "active: CPU"
    required_failures = [c.name for c in checks.values() if c.required and not c.ok]
    assert required_failures == []


def test_doctor_amd_visible_but_unusable_is_actionable(monkeypatch) -> None:
    doctor = _stub_doctor(
        monkeypatch,
        nvidia=(False, "nvidia-smi not found"),
        amd=(True, "/dev/kfd present; arch gfx1151"),
        gpu_ok=(False, "libhipblas.so.3 not found"),
        backend=hardware.BACKEND_CPU,
    )
    checks = {c.name: c for c in doctor.run_diagnostics()}
    detail = checks["ctranslate2 GPU"].detail
    assert "GPU detected but CTranslate2 cannot use it" in detail
    assert "--rocm" in detail


# --------------------------------------------------------------------------- #
# Installer planning (pinned, hash-verified wheels)
# --------------------------------------------------------------------------- #


def test_rocm_wheel_plan_pins_versions_and_target() -> None:
    plan = rocm.rocm_wheel_plan(python_tag="cp313", target="gfx1151")
    assert plan.ctranslate2_archive_url.endswith(
        "v4.8.3/rocm-python-wheels-Linux.zip"
    )
    assert "cp313-cp313" in plan.ctranslate2_wheel_member
    assert "rocm-sdk-core==7.13.0a20260515" in plan.rocm_sdk_requirements
    assert "rocm-sdk-libraries-gfx1151==7.13.0a20260515" in plan.rocm_sdk_requirements


def test_rocm_plan_rejects_unsupported_python() -> None:
    with pytest.raises(rocm.RocmInstallError):
        rocm.rocm_wheel_plan(python_tag="cp38")


def test_rocm_plan_rejects_unsupported_target() -> None:
    with pytest.raises(rocm.RocmInstallError):
        rocm.rocm_wheel_plan(python_tag="cp313", target="gfx9999")


def test_verify_archive_detects_corruption(tmp_path: Path) -> None:
    archive = tmp_path / "a.zip"
    archive.write_bytes(b"hello")
    with pytest.raises(rocm.RocmInstallError) as exc:
        rocm.verify_archive(archive, "0" * 64)
    assert "integrity check failed" in str(exc.value)
    # Correct hash passes.
    import hashlib

    rocm.verify_archive(archive, hashlib.sha256(b"hello").hexdigest())


def test_extract_wheel_member_reports_missing(tmp_path: Path) -> None:
    archive = tmp_path / "a.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("temp-linux/other.whl", b"x")
    with pytest.raises(rocm.RocmInstallError) as exc:
        rocm.extract_wheel_member(archive, "temp-linux/missing.whl", tmp_path / "out")
    assert "not found" in str(exc.value)


def test_extract_wheel_member_roundtrip(tmp_path: Path) -> None:
    archive = tmp_path / "a.zip"
    member = "temp-linux/ctranslate2-4.8.3-cp313-cp313-x.whl"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(member, b"wheelbytes")
    out = rocm.extract_wheel_member(archive, member, tmp_path / "out")
    assert out.read_bytes() == b"wheelbytes"


def test_build_rocm_install_steps_never_resolve_pypi_ctranslate2() -> None:
    plan = rocm.rocm_wheel_plan(python_tag="cp313")
    steps = rocm.build_rocm_install_steps(plan, Path("/tmp/ct2.whl"))
    sdk_step, wheel_step = steps
    assert "--pip-args=--no-deps" in sdk_step
    assert "--pip-args=--no-deps" in wheel_step
    assert "/tmp/ct2.whl" in wheel_step
    # The ROCm SDK index is used so pip finds the ROCm libraries.
    assert plan.rocm_sdk_index_url in sdk_step


def test_installer_auto_selects_rocm_for_amd_only(monkeypatch) -> None:
    assert (
        installer.choose_profile("auto", [], amd_available=True, amd_reason="gfx1151")
        == installer.PROFILE_ROCM
    )


def test_installer_auto_prefers_cuda_over_rocm(monkeypatch) -> None:
    gpus = [installer.GpuInfo("RTX 4090", "550.54.14")]
    assert (
        installer.choose_profile("auto", gpus, amd_available=True)
        == installer.PROFILE_CUDA
    )


def test_installer_explicit_rocm_without_amd_errors() -> None:
    with pytest.raises(installer.InstallerError) as exc:
        installer.choose_profile("rocm", [])
    assert "ROCm is not usable" in str(exc.value)


def test_installer_rocm_dry_run_does_not_download(monkeypatch, capsys) -> None:
    recorded: list[list[str]] = []

    def fake_run_command(command, **kwargs):  # type: ignore[no-untyped-def]
        recorded.append(list(command))
        return 0

    monkeypatch.setattr(installer, "run_command", fake_run_command)
    monkeypatch.setattr(installer, "pipx_available", lambda: True)
    commands = installer.install(source=".", profile=installer.PROFILE_ROCM, dry_run=True)
    assert any("rocm-sdk-core" in " ".join(c) for c in commands)
    # No wheel has been downloaded/extracted during a dry run.
    assert not any("prepare_rocm_wheels" in " ".join(c) for c in commands)


def test_installer_rocm_amd_detection_used_for_auto(monkeypatch) -> None:
    monkeypatch.setattr(installer, "detect_nvidia_gpus", lambda runner=None: ([], "none"))
    monkeypatch.setattr(
        installer, "detect_amd_gpu", lambda runner=None: (True, "/dev/kfd present; arch gfx1151")
    )
    monkeypatch.setattr(installer, "amd_architecture", lambda runner=None: "gfx1151")
    monkeypatch.setattr(installer, "run_command", lambda *a, **k: 0)
    monkeypatch.setattr(installer, "pipx_available", lambda: True)
    code = installer.main(["--dry-run", "--no-verify"])
    assert code == 0


# --------------------------------------------------------------------------- #
# Doc/CLI surface
# --------------------------------------------------------------------------- #


def test_cli_help_mentions_rocm() -> None:
    from typer.testing import CliRunner

    from local_transcribe.cli import app

    result = CliRunner().invoke(app, ["transcribe", "--help"])
    assert result.exit_code == 0
    assert "rocm" in result.output.lower()


def test_installer_documents_rocm_profile() -> None:
    text = INSTALLER_PATH.read_text(encoding="utf-8")
    assert "--rocm" in text
    assert "TheRock" in text or "rocm-sdk" in text

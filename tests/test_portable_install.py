"""Portable CPU/CUDA installation and hardware-aware runtime selection.

Regression coverage for the Aether failure: `pipx install git+…` must never pull
NVIDIA packages, and the runtime/diagnostics must treat CPU-only hosts as a
healthy configuration while preserving CUDA acceleration where it is usable.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
INSTALLER_PATH = REPO_ROOT / "install_local_transcribe.py"


def _load_installer():
    spec = importlib.util.spec_from_file_location("lt_installer", INSTALLER_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["lt_installer"] = module
    spec.loader.exec_module(module)
    return module


installer = _load_installer()


class FakeCompleted:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class FakeRunner:
    """Records invocations and returns canned responses."""

    def __init__(self, response: FakeCompleted | None = None) -> None:
        self.response = response or FakeCompleted()
        self.calls: list[list[str]] = []

    def __call__(self, command, **kwargs):  # type: ignore[no-untyped-def]
        self.calls.append(list(command))
        return self.response


# --------------------------------------------------------------------------- #
# 1. Clean CPU installation resolves no NVIDIA or PyTorch dependencies
# --------------------------------------------------------------------------- #


def test_core_dependencies_exclude_nvidia_and_torch() -> None:
    import tomllib

    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    core = [d.lower() for d in data["project"]["dependencies"]]
    joined = " ".join(core)
    assert "nvidia" not in joined, core
    assert "torch" not in joined, core
    # The CPU inference stack must remain.
    assert any("faster-whisper" in d for d in core)
    assert any(d.startswith("ctranslate2") for d in core)


def test_requirements_and_pyproject_core_agree() -> None:
    import tomllib

    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    core_names = {
        d.split(">")[0].split("=")[0].split("[")[0].strip().lower()
        for d in data["project"]["dependencies"]
    }
    req_lines = [
        line.strip()
        for line in (REPO_ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    req_names = {
        line.split(">")[0].split("=")[0].split("[")[0].strip().lower()
        for line in req_lines
    }
    assert req_names == core_names
    assert not any("nvidia" in n or "torch" in n for n in req_names)


def test_cuda_extra_is_optional_and_versioned() -> None:
    import tomllib

    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    cuda = data["project"]["optional-dependencies"]["cuda"]
    joined = " ".join(cuda)
    assert "nvidia-cublas-cu12" in joined
    assert "nvidia-cudnn-cu12==9.*" in joined


def test_cpu_install_runs_no_nvidia_inject() -> None:
    assert installer.build_inject_command("cpu") is None


# --------------------------------------------------------------------------- #
# 2./3. NVIDIA detection → CUDA path; unavailable → safe CPU fallback
# --------------------------------------------------------------------------- #


def test_detect_nvidia_gpu_uses_functional_nvidia_smi() -> None:
    run = FakeRunner(FakeCompleted(0, "NVIDIA RTX A1000, 535.274.02\n"))
    gpus, detail = installer.detect_nvidia_gpus(run)
    assert [g.name for g in gpus] == ["NVIDIA RTX A1000"]
    assert gpus[0].driver == "535.274.02"
    assert "535.274.02" in detail
    assert run.calls[0][0] == "nvidia-smi"


def test_detect_nvidia_gpu_absent_is_graceful() -> None:
    def boom(*_a, **_k):
        raise FileNotFoundError("nvidia-smi")

    gpus, detail = installer.detect_nvidia_gpus(boom)
    assert gpus == []
    assert "not found" in detail


def test_detect_nvidia_gpu_reports_no_gpus() -> None:
    run = FakeRunner(FakeCompleted(0, "\n"))
    gpus, detail = installer.detect_nvidia_gpus(run)
    assert gpus == []
    assert "no GPUs" in detail


def test_gpu_without_driver_still_requires_functional_smi() -> None:
    # A failing nvidia-smi must not be treated as a usable GPU even if nvcc exists.
    run = FakeRunner(FakeCompleted(1, "", "Failed to initialize NVML"))
    gpus, detail = installer.detect_nvidia_gpus(run)
    assert gpus == []
    assert "exited 1" in detail


def test_driver_threshold_gates_cuda_profile() -> None:
    old = [installer.GpuInfo("GTX 1080", "470.199.02")]
    new = [installer.GpuInfo("RTX 4090", "550.54.14")]
    assert installer.cuda_profile_supported(old)[0] is False
    assert installer.cuda_profile_supported(new)[0] is True


def test_auto_selects_cuda_when_supported() -> None:
    gpus = [installer.GpuInfo("RTX 4090", "550.54.14")]
    assert installer.choose_profile("auto", gpus) == "cuda"
    assert installer.build_inject_command("cuda") == [
        "pipx",
        "inject",
        "--force",
        "local-transcribe",
        "nvidia-cublas-cu12",
        "nvidia-cudnn-cu12==9.*",
    ]


def test_auto_falls_back_to_cpu_without_gpu() -> None:
    assert installer.choose_profile("auto", []) == "cpu"


def test_auto_falls_back_to_cpu_on_old_driver() -> None:
    gpus = [installer.GpuInfo("GTX 1080", "470.199.02")]
    assert installer.choose_profile("auto", gpus) == "cpu"


def test_cpu_request_is_unconditional() -> None:
    gpus = [installer.GpuInfo("RTX 4090", "550.54.14")]
    assert installer.choose_profile("cpu", gpus) == "cpu"


def test_explicit_cuda_without_gpu_is_a_clear_error() -> None:
    with pytest.raises(installer.InstallerError) as exc:
        installer.choose_profile("cuda", [])
    assert "CUDA is not usable" in str(exc.value)


def test_explicit_cuda_with_old_driver_is_a_clear_error() -> None:
    gpus = [installer.GpuInfo("GTX 1080", "470.199.02")]
    with pytest.raises(installer.InstallerError) as exc:
        installer.choose_profile("cuda", gpus)
    assert "older than the CUDA 12 minimum" in str(exc.value)


def test_main_cuda_on_cpu_host_exits_nonzero(capsys) -> None:
    def fake_detect(runner=None):  # noqa: ANN001
        return [], "nvidia-smi not found"

    original = installer.detect_nvidia_gpus
    installer.detect_nvidia_gpus = fake_detect  # type: ignore[assignment]
    try:
        code = installer.main(["--cuda", "--dry-run"])
    finally:
        installer.detect_nvidia_gpus = original  # type: ignore[assignment]

    assert code == 2
    captured = capsys.readouterr()
    assert "CUDA is not usable" in captured.err


# --------------------------------------------------------------------------- #
# 4. Runtime automatic device / compute-type selection
# --------------------------------------------------------------------------- #


def test_runtime_auto_resolves_to_cpu_without_cuda(monkeypatch) -> None:
    from local_transcribe.utils import hardware

    monkeypatch.setattr(
        hardware, "gpu_runtime_status", lambda: (False, "no CUDA devices")
    )
    monkeypatch.delenv("CT2_USE_CUDA", raising=False)
    device, compute = hardware.resolve_device_and_compute("auto", "auto")
    assert (device, compute) == ("cpu", "int8")


def test_runtime_auto_resolves_to_cuda_when_usable(monkeypatch) -> None:
    from local_transcribe.utils import hardware

    monkeypatch.setattr(hardware, "gpu_runtime_status", lambda: (True, ""))
    monkeypatch.setattr(
        hardware, "supported_compute_types", lambda dev: ("float16", "int8", "float32")
    )
    device, compute = hardware.resolve_device_and_compute("auto", "auto")
    assert device == "cuda"
    assert compute == "float16"


def test_runtime_cuda_requested_but_broken_falls_back(monkeypatch) -> None:
    from local_transcribe.utils import hardware

    monkeypatch.setattr(
        hardware,
        "gpu_runtime_status",
        lambda: (False, "CUDA driver version is insufficient"),
    )
    device, compute = hardware.resolve_device_and_compute("cuda", "float16")
    assert (device, compute) == ("cpu", "int8")


def test_backend_resolution_is_not_cached_across_machines(monkeypatch) -> None:
    """The worker resolves per call, so producer/worker hardware can differ."""
    from local_transcribe.utils import hardware

    monkeypatch.setattr(hardware, "gpu_runtime_status", lambda: (False, "no cuda"))
    assert hardware.resolve_device_and_compute("auto", "auto")[0] == "cpu"

    monkeypatch.setattr(hardware, "gpu_runtime_status", lambda: (True, ""))
    monkeypatch.setattr(hardware, "supported_compute_types", lambda dev: ("float16",))
    assert hardware.resolve_device_and_compute("auto", "auto")[0] == "cuda"


def test_transcriber_resolve_matches_hardware_module(monkeypatch) -> None:
    from local_transcribe.services import transcriber
    from local_transcribe.utils import hardware

    monkeypatch.setattr(hardware, "gpu_runtime_status", lambda: (False, "nope"))
    device, compute = transcriber.resolve_device_and_compute("cuda", "float16")
    assert (device, compute) == ("cpu", "int8")


def test_execution_options_default_to_auto() -> None:
    from local_transcribe.services.queue_models import ExecutionOptions

    opts = ExecutionOptions()
    assert opts.device == "auto"
    assert opts.compute_type == "auto"


def test_batch_config_defaults_to_auto() -> None:
    from local_transcribe.services.pipeline import BatchConfig

    cfg = BatchConfig(input_file=Path("in.txt"), output_dir=Path("out"))
    assert cfg.device == "auto"
    assert cfg.compute_type == "auto"


def test_cli_defaults_are_hardware_independent() -> None:
    from local_transcribe import cli

    assert cli.DEFAULT_DEVICE == "auto"
    assert cli.DEFAULT_COMPUTE_TYPE == "auto"


# --------------------------------------------------------------------------- #
# 5. Explicit device overrides
# --------------------------------------------------------------------------- #


def test_explicit_cpu_keeps_requested_compute_type(monkeypatch) -> None:
    from local_transcribe.utils import hardware

    # CUDA probe must not even be consulted for an explicit CPU request.
    def explode():
        raise AssertionError("CUDA probe called for explicit cpu request")

    monkeypatch.setattr(hardware, "gpu_runtime_status", explode)
    device, compute = hardware.resolve_device_and_compute("cpu", "int8")
    assert (device, compute) == ("cpu", "int8")


def test_explicit_cuda_compute_type_is_preserved_when_usable(monkeypatch) -> None:
    from local_transcribe.utils import hardware

    monkeypatch.setattr(hardware, "gpu_runtime_status", lambda: (True, ""))
    device, compute = hardware.resolve_device_and_compute("cuda", "int8_float16")
    assert (device, compute) == ("cuda", "int8_float16")


def test_explicit_cpu_compute_preference(monkeypatch) -> None:
    from local_transcribe.utils import hardware

    monkeypatch.setattr(
        hardware, "supported_compute_types", lambda dev: ("int8", "float32")
    )
    assert hardware.select_compute_type("cpu", "auto") == "int8"
    assert hardware.select_compute_type("cpu", "float32") == "float32"


# --------------------------------------------------------------------------- #
# 6. CPU-only `lt doctor` behavior and exit code
# --------------------------------------------------------------------------- #


def test_doctor_cpu_only_checks_pass_without_nvidia(monkeypatch) -> None:
    from local_transcribe.utils import doctor, hardware

    monkeypatch.setattr(hardware, "gpu_runtime_status", lambda: (False, "no cuda"))
    monkeypatch.setattr(hardware, "detect_nvidia_gpu", lambda run=None: (False, "not found"))
    monkeypatch.setattr(hardware, "supported_compute_types", lambda dev: ("int8", "float32"))
    monkeypatch.setattr(doctor, "check_ffmpeg", lambda: (True, "ffmpeg ok"))
    monkeypatch.setattr(doctor, "check_yt_dlp", lambda: (True, "yt-dlp ok"))
    monkeypatch.setattr(doctor, "check_required_modules", lambda: (True, "present"))
    monkeypatch.setattr(doctor, "check_deno", lambda: (False, "deno not found"))

    checks = {c.name: c for c in doctor.run_diagnostics()}
    required_failures = [c.name for c in checks.values() if c.required and not c.ok]
    assert required_failures == []
    assert checks["ctranslate2 GPU"].ok is False
    assert checks["ctranslate2 GPU"].required is False
    assert checks["Deno Runtime (YouTube)"].required is False


def test_doctor_cli_exit_zero_on_cpu_only(tmp_path, monkeypatch) -> None:
    from typer.testing import CliRunner

    from local_transcribe import cli
    from local_transcribe.utils import doctor, hardware

    monkeypatch.setattr(hardware, "gpu_runtime_status", lambda: (False, "no cuda"))
    monkeypatch.setattr(hardware, "detect_nvidia_gpu", lambda run=None: (False, "not found"))
    monkeypatch.setattr(hardware, "supported_compute_types", lambda dev: ("int8",))
    monkeypatch.setattr(doctor, "check_ffmpeg", lambda: (True, "ffmpeg ok"))
    monkeypatch.setattr(doctor, "check_yt_dlp", lambda: (True, "yt-dlp ok"))
    monkeypatch.setattr(doctor, "check_required_modules", lambda: (True, "present"))
    monkeypatch.setattr(doctor, "check_deno", lambda: (False, "deno not found"))

    result = CliRunner().invoke(cli.app, ["doctor"])
    assert result.exit_code == 0, result.output
    assert "CPU" in result.output


def test_doctor_cli_exit_one_on_missing_required(monkeypatch) -> None:
    from typer.testing import CliRunner

    from local_transcribe import cli
    from local_transcribe.utils import doctor, hardware

    monkeypatch.setattr(hardware, "gpu_runtime_status", lambda: (False, "no cuda"))
    monkeypatch.setattr(hardware, "detect_nvidia_gpu", lambda run=None: (False, "not found"))
    monkeypatch.setattr(hardware, "supported_compute_types", lambda dev: ("int8",))
    monkeypatch.setattr(doctor, "check_ffmpeg", lambda: (True, "ffmpeg ok"))
    monkeypatch.setattr(doctor, "check_yt_dlp", lambda: (True, "yt-dlp ok"))
    monkeypatch.setattr(doctor, "check_required_modules", lambda: (False, "Missing: ctranslate2"))
    monkeypatch.setattr(doctor, "check_deno", lambda: (False, "deno not found"))

    result = CliRunner().invoke(cli.app, ["doctor"])
    assert result.exit_code == 1, result.output


def test_doctor_surfaces_actionable_cuda_problem(monkeypatch) -> None:
    from local_transcribe.utils import doctor, hardware

    monkeypatch.setattr(hardware, "detect_nvidia_gpu", lambda run=None: (True, "RTX 4090"))
    monkeypatch.setattr(
        hardware, "gpu_runtime_status", lambda: (False, "cuDNN .so not found")
    )
    monkeypatch.setattr(hardware, "supported_compute_types", lambda dev: ("int8",))
    monkeypatch.setattr(doctor, "check_ffmpeg", lambda: (True, "ok"))
    monkeypatch.setattr(doctor, "check_yt_dlp", lambda: (True, "ok"))
    monkeypatch.setattr(doctor, "check_required_modules", lambda: (True, "present"))
    monkeypatch.setattr(doctor, "check_deno", lambda: (True, "deno"))

    cuda = next(c for c in doctor.run_diagnostics() if c.name == "ctranslate2 GPU")
    assert cuda.ok is False
    assert "GPU detected but CTranslate2 cannot use it" in cuda.detail
    assert "--cuda" in cuda.detail


# --------------------------------------------------------------------------- #
# 7. No automatic package installation during ordinary CLI operations
# --------------------------------------------------------------------------- #


def test_doctor_module_has_no_installer_side_effect() -> None:
    source = (REPO_ROOT / "src" / "local_transcribe" / "utils" / "doctor.py").read_text(
        encoding="utf-8"
    )
    assert "ensure_pytorch_cuda" not in source
    assert "pip install" not in source
    assert "pipx" not in source or "pipx_environment" in source
    assert "subprocess.run" in source  # probes only


def test_cli_does_not_auto_install_packages(monkeypatch) -> None:
    from typer.testing import CliRunner

    from local_transcribe import cli
    from local_transcribe.utils import doctor

    calls: list[str] = []

    def spy_run(cmd, *a, **k):  # type: ignore[no-untyped-def]
        calls.append(str(cmd))
        return subprocess.CompletedProcess(cmd, 1, "", "")

    monkeypatch.setattr(subprocess, "run", spy_run)
    monkeypatch.setattr(doctor, "check_deno", lambda: (False, "missing"))
    result = CliRunner().invoke(cli.app, ["version"])
    assert result.exit_code == 0
    assert not any("pip" in c for c in calls)


def test_worker_install_never_installs_pytorch(monkeypatch, tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from local_transcribe import cli_worker

    recorded: list[list[str]] = []

    def spy_run(command, **kwargs):  # type: ignore[no-untyped-def]
        recorded.append(list(command))
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(subprocess, "run", spy_run)
    monkeypatch.setenv("HOME", str(tmp_path))

    from local_transcribe.utils import hardware

    monkeypatch.setattr(hardware, "detect_nvidia_gpu", lambda run=None: (True, "RTX"))
    monkeypatch.setattr(hardware, "cuda_usable", lambda: False)

    CliRunner().invoke(cli_worker.worker_app, ["install"])
    flat = " ".join(" ".join(c) for c in recorded)
    assert "torch" not in flat
    assert "nvidia" not in flat
    assert (tmp_path / ".config" / "systemd" / "user" / cli_worker.SERVICE_NAME).is_file()


def test_installer_dry_run_builds_expected_commands(monkeypatch) -> None:
    recorded: list[list[str]] = []

    def fake_run_command(command, **kwargs):  # type: ignore[no-untyped-def]
        recorded.append(list(command))
        return 0

    monkeypatch.setattr(installer, "run_command", fake_run_command)
    monkeypatch.setattr(installer, "pipx_available", lambda: True)
    commands = installer.install(source=".", profile="cuda", dry_run=True)
    flat = [" ".join(c) for c in commands]
    assert any("pipx install --force ." == c for c in flat)
    assert any("nvidia-cublas-cu12" in c for c in flat)


# --------------------------------------------------------------------------- #
# 8. Queue execution on CPU when the producer used different hardware
# --------------------------------------------------------------------------- #


def test_worker_resolves_auto_on_cpu_while_producer_requested_auto(
    tmp_path: Path, monkeypatch
) -> None:
    from local_transcribe.services import job_runner as jr_mod
    from local_transcribe.services.job_runner import ProductionJobRunner
    from local_transcribe.services.queue_models import Execution, ExecutionOptions
    from local_transcribe.utils import hardware

    monkeypatch.setattr(
        hardware, "gpu_runtime_status", lambda: (False, "no CUDA devices")
    )
    monkeypatch.setattr(hardware, "supported_compute_types", lambda dev: ("int8",))
    monkeypatch.setattr(jr_mod, "resolve_device_and_compute", hardware.resolve_device_and_compute)

    audio = tmp_path / "talk.m4a"
    audio.write_bytes(b"audio")
    transcripts = tmp_path / "tx"
    transcripts.mkdir()

    loaded: list[tuple[str, str, str]] = []
    from local_transcribe.services.model_cache import ModelCache

    cache = ModelCache(lambda m, d, c: (loaded.append((m, d, c)) or object()))

    def fake_transcribe(model, audio_path, **kwargs):  # type: ignore[no-untyped-def]
        return "cpu transcript"

    monkeypatch.setattr(jr_mod, "transcribe_with_model", fake_transcribe)

    runner = ProductionJobRunner(
        transcripts_root=transcripts,
        model_cache=cache,
        scratch_root=tmp_path / "scratch",
    )
    execution = Execution(
        execution_id="e1",
        source_key="local:abc",
        generation=1,
        source=str(audio),
        source_type="local_file",
        # Producer used hardware-independent defaults.
        options=ExecutionOptions(model="tiny", device="auto", compute_type="auto"),
    )
    result = runner.run(execution)
    assert result.effective_device == "cpu"
    assert result.effective_compute_type == "int8"
    assert loaded == [("tiny", "cpu", "int8")]


def test_job_runner_resolves_auto_on_the_worker_not_the_producer(
    tmp_path: Path, monkeypatch
) -> None:
    """The producer's stored options never bake in 'cuda'; the worker resolves."""
    from local_transcribe.services import job_runner as jr_mod
    from local_transcribe.services.job_runner import ProductionJobRunner
    from local_transcribe.services.queue_models import Execution, ExecutionOptions
    from local_transcribe.utils import hardware

    seen: dict = {}

    def spy(device, compute):  # type: ignore[no-untyped-def]
        seen["device"] = device
        seen["compute"] = compute
        return hardware.resolve_device_and_compute(device, compute)

    monkeypatch.setattr(hardware, "gpu_runtime_status", lambda: (False, "no cuda"))
    monkeypatch.setattr(hardware, "supported_compute_types", lambda dev: ("int8",))
    monkeypatch.setattr(jr_mod, "resolve_device_and_compute", spy)
    monkeypatch.setattr(jr_mod, "transcribe_with_model", lambda *a, **k: "txt")

    audio = tmp_path / "a.wav"
    audio.write_bytes(b"RIFF")
    transcripts = tmp_path / "tx"
    transcripts.mkdir()
    runner = ProductionJobRunner(transcripts_root=transcripts, scratch_root=tmp_path / "s")
    runner.run(
        Execution(
            execution_id="e1",
            source_key="local:abc",
            generation=1,
            source=str(audio),
            source_type="local_file",
            options=ExecutionOptions(),
        )
    )
    assert seen == {"device": "auto", "compute": "auto"}


# --------------------------------------------------------------------------- #
# 10. Idempotent installer operation
# --------------------------------------------------------------------------- #


def test_installer_uses_force_for_repeatable_installs() -> None:
    assert installer.build_install_command(".") == ["pipx", "install", "--force", "."]


def test_installer_repeated_runs_are_identical(monkeypatch) -> None:
    monkeypatch.setattr(installer, "run_command", lambda *a, **k: 0)
    monkeypatch.setattr(installer, "pipx_available", lambda: True)
    first = installer.install(source=".", profile="cpu", dry_run=True)
    second = installer.install(source=".", profile="cpu", dry_run=True)
    assert first == second
    assert first == [["pipx", "install", "--force", "."]]


def test_install_requires_pipx(monkeypatch) -> None:
    monkeypatch.setattr(installer, "pipx_available", lambda: False)
    with pytest.raises(installer.InstallerError) as exc:
        installer.install(source=".", profile="cpu", dry_run=False)
    assert "pipx is required" in str(exc.value)


def test_resolve_source_prefers_local_checkout(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    assert installer.resolve_source(None, tmp_path) == "."
    assert installer.resolve_source(None, tmp_path / "nope") == installer.DEFAULT_GIT_SOURCE
    assert installer.resolve_source("git+x", tmp_path) == "git+x"


# --------------------------------------------------------------------------- #
# Real clean-environment pipx installation (opt-in; needs network)
# --------------------------------------------------------------------------- #


@pytest.mark.skipif(
    not __import__("os").environ.get("LT_RUN_PIPX_INSTALL_TESTS"),
    reason="set LT_RUN_PIPX_INSTALL_TESTS=1 to run real pipx installation tests",
)
def test_real_clean_pipx_install_resolves_no_nvidia() -> None:
    import os

    pipx = __import__("shutil").which("pipx")
    if not pipx:
        pytest.skip("pipx not installed")

    env = dict(os.environ)
    env["PIPX_HOME"] = env["PIPX_HOME"]  # isolated home set by the test runner
    result = subprocess.run(
        ["pipx", "install", "--force", f"{REPO_ROOT}"],
        capture_output=True,
        text=True,
        timeout=1800,
        env=env,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    listing = subprocess.run(
        ["pipx", "runpip", installer.PACKAGE_NAME, "list"],
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
    )
    lowered = listing.stdout.lower()
    assert "nvidia" not in lowered
    assert "torch" not in lowered
    assert "ctranslate2" in lowered
    assert "faster-whisper" in lowered

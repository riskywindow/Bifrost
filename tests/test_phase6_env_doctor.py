from __future__ import annotations

import importlib
import json
import shutil
import socket
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
for source_path in (
    REPO_ROOT / "bifrost_py",
    REPO_ROOT / "integrations" / "lmcache_bifrost",
):
    text = str(source_path)
    if text not in sys.path:
        sys.path.insert(0, text)

from bifrost_serving import env_doctor
from bifrost_serving.env_doctor import EnvDoctorConfig, run_doctor


def test_doctor_runs_without_vllm_installed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _hide_modules(monkeypatch, {"vllm"})

    report = run_doctor(_config(tmp_path))

    assert report.checks["vllm"].status == "not_ready"
    assert report.readiness["fake_ci_ready"].status == "ready"


def test_doctor_runs_without_lmcache_installed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _hide_modules(monkeypatch, {"lmcache"})

    report = run_doctor(_config(tmp_path))

    assert report.checks["lmcache"].status == "not_ready"
    assert report.readiness["fake_ci_ready"].status == "ready"


def test_doctor_returns_fake_ci_ready_with_core_local_dependencies(tmp_path: Path) -> None:
    report = run_doctor(_config(tmp_path))

    assert report.checks["python"].status == "ready"
    assert report.checks["bifrost_client"].status == "ready"
    assert report.checks["output_directory"].status == "ready"
    assert report.checks["disk_space"].status == "ready"
    assert report.readiness["fake_ci_ready"].status == "ready"


def test_daemon_check_reports_not_ready_when_daemon_absent(tmp_path: Path) -> None:
    endpoint = f"127.0.0.1:{_free_port()}"

    report = run_doctor(_config(tmp_path, endpoint=endpoint))

    assert report.checks["bifrost_daemon"].status == "not_ready"
    assert "not reachable" in report.checks["bifrost_daemon"].reason
    assert report.readiness["lmcache_connector_ready"].status == "not_ready"


def test_json_output_is_parseable(tmp_path: Path) -> None:
    report = run_doctor(_config(tmp_path))

    data = json.loads(report.to_json())

    assert data["readiness"]["fake_ci_ready"]["status"] == "ready"
    assert "checks" in data
    assert "python" in data["checks"]


def test_port_and_disk_checks_are_mockable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        env_doctor,
        "_port_available",
        lambda host, port: port != 9001,
    )
    usage = shutil._ntuple_diskusage(total=1000, used=100, free=900)
    monkeypatch.setattr(shutil, "disk_usage", lambda path: usage)

    report = run_doctor(
        EnvDoctorConfig(
            endpoint=f"127.0.0.1:{_free_port()}",
            output_dir=tmp_path,
            min_free_disk_bytes=500,
            required_ports=(9000, 9001),
        )
    )

    assert report.checks["disk_space"].status == "ready"
    assert report.checks["ports"].status == "not_ready"
    assert report.checks["ports"].details["unavailable"] == [9001]


def test_no_gpu_required_for_fake_ci_ready(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        env_doctor,
        "_check_torch",
        lambda: env_doctor.CheckResult(
            "torch",
            "ready",
            {"cuda_available": False, "cuda_device_count": 0, "gpu_names": []},
        ),
    )

    report = run_doctor(_config(tmp_path))

    assert report.readiness["fake_ci_ready"].status == "ready"
    assert report.readiness["gpu_serving_ready"].status == "not_ready"


def test_cli_writes_parseable_json(tmp_path: Path) -> None:
    output = tmp_path / "doctor.json"

    exit_code = env_doctor.main(
        [
            "--endpoint",
            f"127.0.0.1:{_free_port()}",
            "--output-json",
            str(output),
            "--json",
        ]
    )

    data = json.loads(output.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert data["readiness"]["fake_ci_ready"]["status"] == "ready"


def _hide_modules(monkeypatch: pytest.MonkeyPatch, hidden: set[str]) -> None:
    real_import = importlib.import_module

    def fake_import(name: str, package: str | None = None):
        if name in hidden:
            raise ModuleNotFoundError(name)
        return real_import(name, package)

    for name in hidden:
        monkeypatch.delitem(sys.modules, name, raising=False)
    monkeypatch.setattr(importlib, "import_module", fake_import)


def _config(tmp_path: Path, *, endpoint: str | None = None) -> EnvDoctorConfig:
    return EnvDoctorConfig(
        endpoint=endpoint or f"127.0.0.1:{_free_port()}",
        output_dir=tmp_path,
        required_ports=(_free_port(), _free_port()),
        daemon_timeout_seconds=0.1,
    )


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])

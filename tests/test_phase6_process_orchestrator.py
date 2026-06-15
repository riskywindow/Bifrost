from __future__ import annotations

import json
import socket
import subprocess
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

from bifrost_serving.orchestrator import (
    OrchestratorConfig,
    OrchestratorSafetyError,
    run_orchestration,
)
from bifrost_serving.processes import ManagedProcess, ProcessReadinessTimeout

CLI = REPO_ROOT / "tools" / "bifrost_orchestrate_serving.py"


def test_fake_server_orchestration_works_and_writes_logs(tmp_path: Path) -> None:
    output_dir = tmp_path / "orchestrated"

    result = run_orchestration(
        OrchestratorConfig(
            scenario="fake",
            output_dir=output_dir,
            vllm_port=_free_port(),
            readiness_timeout_seconds=5,
        )
    )

    assert result.started is True
    assert result.manifest_path.exists()
    assert (output_dir / "orchestrator_final_status.json").exists()
    log_path = output_dir / "fake_openai_server.log"
    assert log_path.exists()

    final_status = json.loads(
        (output_dir / "orchestrator_final_status.json").read_text(encoding="utf-8")
    )
    assert final_status["processes"][0]["name"] == "fake_openai_server"
    assert final_status["processes"][0]["running"] is False


def test_dry_run_prints_commands_without_starting(tmp_path: Path) -> None:
    output_dir = tmp_path / "dry-run"

    result = subprocess.run(
        [
            sys.executable,
            str(CLI),
            "--scenario",
            "fake",
            "--output-dir",
            str(output_dir),
            "--vllm-port",
            str(_free_port()),
            "--dry-run",
            "--json",
        ],
        cwd=REPO_ROOT,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["dry_run"] is True
    assert data["started"] is False
    assert data["processes"][0]["name"] == "fake_openai_server"
    assert "bifrost_fake_openai_server.py" in " ".join(data["processes"][0]["command"])
    assert not (output_dir / "fake_openai_server.log").exists()


def test_real_vllm_scenario_refuses_without_opt_in(tmp_path: Path) -> None:
    model_dir = tmp_path / "local-model"
    model_dir.mkdir()

    with pytest.raises(OrchestratorSafetyError, match="refusing to start real vLLM"):
        run_orchestration(
            OrchestratorConfig(
                scenario="vllm-only",
                output_dir=tmp_path / "run",
                model=str(model_dir),
            )
        )


def test_real_vllm_scenario_refuses_model_download_without_explicit_env(
    tmp_path: Path,
) -> None:
    with pytest.raises(OrchestratorSafetyError, match="refusing non-local model"):
        run_orchestration(
            OrchestratorConfig(
                scenario="vllm-only",
                output_dir=tmp_path / "run",
                model="remote/model-id",
                allow_real_vllm=True,
            )
        )


def test_process_cleanup_on_orchestrator_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = ManagedProcess(
        name="started",
        command=[
            sys.executable,
            "-u",
            "-c",
            "import time; print('started', flush=True); time.sleep(60)",
        ],
        log_path=tmp_path / "started.log",
        ready_check=lambda: True,
    )
    stuck = ManagedProcess(
        name="stuck",
        command=[
            sys.executable,
            "-u",
            "-c",
            "import time; print('stuck', flush=True); time.sleep(60)",
        ],
        log_path=tmp_path / "stuck.log",
        ready_check=lambda: False,
    )

    monkeypatch.setattr(
        "bifrost_serving.orchestrator.build_processes",
        lambda config: [started, stuck],
    )

    with pytest.raises(ProcessReadinessTimeout):
        run_orchestration(
            OrchestratorConfig(
                scenario="fake",
                output_dir=tmp_path,
                readiness_timeout_seconds=0.2,
            )
        )

    assert started.status()["running"] is False
    assert stuck.status()["running"] is False
    assert (tmp_path / "started.log").exists()
    assert (tmp_path / "stuck.log").exists()


def test_managed_process_logs_are_written(tmp_path: Path) -> None:
    log_path = tmp_path / "process.log"
    process = ManagedProcess(
        name="logger",
        command=[sys.executable, "-u", "-c", "print('hello from process')"],
        log_path=log_path,
        ready_check=None,
    )

    process.start()
    process.wait_ready(1)
    assert process.process is not None
    process.process.wait(timeout=2)
    process.stop()

    assert "hello from process" in log_path.read_text(encoding="utf-8")
    assert process.status()["running"] is False


def test_readiness_timeout_is_handled(tmp_path: Path) -> None:
    process = ManagedProcess(
        name="never-ready",
        command=[sys.executable, "-u", "-c", "import time; time.sleep(60)"],
        log_path=tmp_path / "never-ready.log",
        ready_check=lambda: False,
    )
    process.start()
    try:
        with pytest.raises(ProcessReadinessTimeout, match="readiness timed out"):
            process.wait_ready(0.2)
    finally:
        process.stop()

    assert process.status()["running"] is False


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])

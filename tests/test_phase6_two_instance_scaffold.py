from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "examples" / "serving_benchmark" / "two_instance_cache_share_demo.py"
CONFIG = REPO_ROOT / "examples" / "serving_benchmark" / "configs" / "two_instance_cache_share_example.yaml"


def test_dry_run_works_without_starting_real_services(tmp_path: Path) -> None:
    result = _run_demo(
        tmp_path,
        "--mode",
        "dry-run",
        "--output-dir",
        str(tmp_path / "out"),
        "--model",
        str(tmp_path / "missing-local-model"),
        "--json",
    )

    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["status"] == "dry_run"
    assert data["bifrost_endpoint"] == "127.0.0.1:7420"
    assert data["expected_ports"]["instance_a_vllm"] == 8010
    assert data["expected_ports"]["instance_b_vllm"] == 8011
    assert "instance_a" in data["commands"]
    assert "instance_b" in data["commands"]
    assert "vllm serve" in _joined_commands(data)
    assert data["workload_paths"]["a"].endswith("instance_a_populate.jsonl")
    assert data["workload_paths"]["b"].endswith("instance_b_probe.jsonl")
    assert not list((tmp_path / "out").glob("**/vllm_server.log"))


def test_missing_vllm_reports_not_ready(tmp_path: Path) -> None:
    result = _run_demo(
        tmp_path,
        "--mode",
        "readiness",
        "--output-dir",
        str(tmp_path / "out"),
        "--model",
        str(tmp_path / "definitely-missing-model"),
        "--json",
    )

    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    checks = json.loads(
        Path(data["environment_readiness_path"]).read_text(encoding="utf-8")
    )["checks"]
    assert data["status"] == "readiness"
    assert data["experiment_run_readiness"]["status"] == "not_ready"
    assert checks["vllm"]["status"] != "ready" or checks["vllm_cli"]["status"] != "ready"
    assert checks["model"]["status"] == "unknown"
    assert "Neither vLLM import nor vLLM CLI is available." in (
        data["experiment_run_readiness"]["reasons"]
    )


def test_run_refuses_without_opt_in_before_starting_real_services(tmp_path: Path) -> None:
    model_dir = tmp_path / "local-model"
    model_dir.mkdir()

    result = _run_demo(
        tmp_path,
        "--mode",
        "run",
        "--output-dir",
        str(tmp_path / "out"),
        "--model",
        str(model_dir),
        "--json",
    )

    assert result.returncode == 2
    assert "refusing run mode without --allow-real-vllm" in result.stderr
    assert not list((tmp_path / "out").glob("**/vllm_server.log"))


def test_config_parses() -> None:
    yaml = pytest.importorskip("yaml")
    parsed = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    assert parsed["demo"]["route"] == (
        "vLLM -> LMCache -> BIFROST remote storage connector -> bifrostd"
    )
    assert parsed["demo"]["raw_vllm_kvtransfer"] is False
    assert parsed["demo"]["phase6_hard_requirement"] is False
    assert parsed["serving"]["multi_gpu_required"] is False
    extra = parsed["lmcache"]["extra_config"]
    assert (
        extra["remote_storage_plugin.bifrost.class_name"]
        == "BifrostConnectorAdapter"
    )
    assert extra["object_type"] == "opaque_engine_blob"


def test_scaffold_is_optional_and_ci_safe() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    assert "BIFROST_RUN_REAL_VLLM" in text
    assert "refusing two-instance real vLLM demo run mode in CI" in text
    assert "KVTransfer connector" not in text
    assert "multi_gpu_required: false" in CONFIG.read_text(encoding="utf-8")


def _run_demo(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.pop("BIFROST_RUN_REAL_VLLM", None)
    env.pop("BIFROST_ALLOW_MODEL_DOWNLOADS", None)
    env["PYTHONPATH"] = (
        f"{REPO_ROOT / 'bifrost_py'}:"
        f"{REPO_ROOT / 'integrations' / 'lmcache_bifrost'}:"
        f"{env.get('PYTHONPATH', '')}"
    )
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=REPO_ROOT,
        env=env,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=30,
    )


def _joined_commands(data: dict) -> str:
    return "\n".join(
        " ".join(command)
        for commands in data["commands"].values()
        for command in commands
    )

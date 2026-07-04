from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "examples" / "serving_benchmark" / "vllm_lmcache_bifrost_demo.py"
CONFIGS = REPO_ROOT / "examples" / "serving_benchmark" / "configs"


def test_dry_run_works_without_starting_vllm(tmp_path: Path) -> None:
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
    assert "vllm-lmcache-bifrost" in data["commands"]
    assert "vllm serve" in _joined_commands(data)
    assert not list((tmp_path / "out").glob("**/vllm_server.log"))


def test_readiness_reports_missing_components_clearly(tmp_path: Path) -> None:
    result = _run_demo(
        tmp_path,
        "--mode",
        "readiness",
        "--output-dir",
        str(tmp_path / "out"),
        "--model",
        str(tmp_path / "definitely-missing-model"),
        "--bifrost-endpoint",
        "127.0.0.1:9",
        "--json",
    )

    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    readiness = data["readiness"]["full_benchmark_ready"]
    checks = json.loads(
        Path(data["environment_readiness_path"]).read_text(encoding="utf-8")
    )["checks"]
    assert data["status"] == "readiness"
    assert readiness["status"] == "not_ready"
    assert data["demo_run_readiness"]["status"] == "not_ready"
    assert checks["model"]["status"] == "unknown"
    assert "does not resolve to a local path" in checks["model"]["reason"]
    assert "bifrost_daemon" in checks
    assert "huggingface_token" in checks


def test_run_refuses_without_opt_in_before_starting_vllm(tmp_path: Path) -> None:
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


def test_yaml_configs_parse() -> None:
    yaml = pytest.importorskip("yaml")
    for name in ("one_gpu_inprocess_example.yaml", "mp_mode_example.yaml"):
        parsed = yaml.safe_load((CONFIGS / name).read_text(encoding="utf-8"))
        assert parsed["demo"]["route"] == (
            "vLLM -> LMCache -> BIFROST remote storage connector -> bifrostd"
        )
        assert parsed["demo"]["raw_vllm_kvtransfer"] is False
        extra = parsed["lmcache"]["extra_config"]
        assert (
            extra["remote_storage_plugin.bifrost.class_name"]
            == "BifrostConnectorAdapter"
        )
        assert extra["object_type"] == "opaque_engine_blob"


def test_scaffold_paths_do_not_enable_ci_real_serving() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    assert "BIFROST_RUN_REAL_VLLM" in text
    assert "refusing real vLLM demo run mode in CI" in text
    assert "KVTransfer connector" not in text


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

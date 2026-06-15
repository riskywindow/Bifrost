from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = REPO_ROOT / "examples" / "lmcache_bifrost"
SCRIPT = EXAMPLES / "vllm_lmcache_smoke.py"
CONFIG = EXAMPLES / "vllm_lmcache_bifrost_config.yaml"
SHELL = EXAMPLES / "run_vllm_lmcache_bifrost_smoke.sh"


def test_vllm_smoke_scaffold_files_exist() -> None:
    assert SCRIPT.exists()
    assert CONFIG.exists()
    assert SHELL.exists()
    assert os.access(SHELL, os.X_OK)


def test_vllm_smoke_config_yaml_parses() -> None:
    yaml = pytest.importorskip("yaml")

    parsed = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))

    assert parsed["remote_storage_plugins"] == ["bifrost"]
    plugin = parsed["remote_storage_plugin"]["bifrost"]
    assert plugin["module_path"] == "lmcache_bifrost.adapter"
    assert plugin["class_name"] == "BifrostConnectorAdapter"
    assert plugin["extra_config"]["endpoint"] == "127.0.0.1:7744"
    assert parsed["bifrost_object_type"] == "opaque_engine_blob"


def test_readiness_check_skips_when_vllm_and_lmcache_are_forced_missing() -> None:
    result = run_script("--json")

    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    assert summary["status"] == "skipped"
    assert summary["vllm_installed"] is False
    assert summary["lmcache_installed"] is False
    assert "vLLM is not installed" in summary["skip_reason"]
    assert "LMCache is not installed" in summary["skip_reason"]


def test_run_requires_explicit_environment_opt_in() -> None:
    result = run_script("--run", "--json")

    assert result.returncode != 0
    summary = json.loads(result.stdout)
    assert summary["status"] == "skipped"
    assert "BIFROST_RUN_VLLM_SMOKE" in summary["skip_reason"]


def test_shell_script_refuses_without_opt_in() -> None:
    env = os.environ.copy()
    env.pop("BIFROST_RUN_VLLM_SMOKE", None)
    result = subprocess.run(
        [str(SHELL)],
        cwd=REPO_ROOT,
        env=env,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    assert result.returncode == 2
    assert "Refusing to run the optional vLLM smoke" in result.stderr
    assert "No private tokens" in result.stderr


def test_scaffold_tests_do_not_start_vllm_or_download_models() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    assert "BIFROST_RUN_VLLM_SMOKE" in text
    assert "download_dir=None" in text
    assert "return path.exists()" in text
    assert "KVTransfer" not in text


def run_script(*args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        [
            str(REPO_ROOT / "bifrost_py"),
            str(REPO_ROOT / "integrations" / "lmcache_bifrost"),
        ]
    )
    env["BIFROST_VLLM_SMOKE_FORCE_MISSING"] = "vllm,lmcache"
    env.pop("BIFROST_RUN_VLLM_SMOKE", None)
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=REPO_ROOT,
        env=env,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

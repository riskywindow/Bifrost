from __future__ import annotations

import json
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

from bifrost_serving.baseline_matrix import (
    BaselineMatrix,
    BaselineMatrixError,
    BaselineMode,
    generate_baseline_matrix_configs,
)

CLI = REPO_ROOT / "tools" / "bifrost_generate_phase6_matrix.py"


def test_all_three_modes_generate_and_parse(tmp_path: Path) -> None:
    bundle = generate_baseline_matrix_configs(_matrix(tmp_path))

    assert bundle.run_plan_yaml.exists()
    assert bundle.comparison_manifest_json.exists()
    assert set(bundle.vllm_command_json) == {
        BaselineMode.VLLM_ONLY,
        BaselineMode.VLLM_LMCACHE_LOCAL_CPU,
        BaselineMode.VLLM_LMCACHE_BIFROST,
    }
    assert set(bundle.lmcache_config_yaml) == {
        BaselineMode.VLLM_LMCACHE_LOCAL_CPU,
        BaselineMode.VLLM_LMCACHE_BIFROST,
    }

    manifest = json.loads(bundle.comparison_manifest_json.read_text(encoding="utf-8"))
    assert manifest["fairness"]["status"] == "validated"
    assert manifest["fairness"]["prefix_caching"] == "explicitly_disabled"

    yaml = pytest.importorskip("yaml")
    assert yaml.safe_load(bundle.run_plan_yaml.read_text(encoding="utf-8"))[
        "schema_version"
    ] == "bifrost.phase6_baseline_matrix_run_plan.v1"
    for path in bundle.lmcache_config_yaml.values():
        assert isinstance(yaml.safe_load(path.read_text(encoding="utf-8")), dict)
    for path in bundle.vllm_command_json.values():
        assert json.loads(path.read_text(encoding="utf-8"))["command"][0:2] == [
            "vllm",
            "serve",
        ]
    assert json.loads(bundle.bifrost_connector_config_json.read_text(encoding="utf-8"))[
        "object_type"
    ] == "opaque_engine_blob"


def test_common_fields_match_and_backends_are_isolated(tmp_path: Path) -> None:
    matrix = _matrix(tmp_path)
    by_mode = matrix.by_mode()

    common = by_mode[BaselineMode.VLLM_ONLY].common_fields()
    assert by_mode[BaselineMode.VLLM_LMCACHE_LOCAL_CPU].common_fields() == common
    assert by_mode[BaselineMode.VLLM_LMCACHE_BIFROST].common_fields() == common

    assert by_mode[BaselineMode.VLLM_ONLY].lmcache_enabled is False
    assert by_mode[BaselineMode.VLLM_ONLY].bifrost_enabled is False
    assert by_mode[BaselineMode.VLLM_LMCACHE_LOCAL_CPU].local_cpu is True
    assert by_mode[BaselineMode.VLLM_LMCACHE_LOCAL_CPU].bifrost_enabled is False
    assert by_mode[BaselineMode.VLLM_LMCACHE_BIFROST].local_cpu is False
    assert by_mode[BaselineMode.VLLM_LMCACHE_BIFROST].bifrost_enabled is True
    assert (
        by_mode[BaselineMode.VLLM_LMCACHE_LOCAL_CPU].lmcache_connector_mode
        == by_mode[BaselineMode.VLLM_LMCACHE_BIFROST].lmcache_connector_mode
    )


def test_lmcache_configs_have_required_backend_flags(tmp_path: Path) -> None:
    bundle = generate_baseline_matrix_configs(_matrix(tmp_path))
    yaml = pytest.importorskip("yaml")

    local = yaml.safe_load(
        bundle.lmcache_config_yaml[BaselineMode.VLLM_LMCACHE_LOCAL_CPU].read_text(
            encoding="utf-8"
        )
    )
    bifrost = yaml.safe_load(
        bundle.lmcache_config_yaml[BaselineMode.VLLM_LMCACHE_BIFROST].read_text(
            encoding="utf-8"
        )
    )

    assert local["chunk_size"] == 256
    assert local["local_cpu"] is True
    assert local["max_local_cpu_size"] == 16
    assert local["remote_storage_plugins"] == []
    assert local["remote_url"] is None
    assert "remote_storage_plugin" not in local

    assert bifrost["chunk_size"] == 256
    assert bifrost["local_cpu"] is False
    assert bifrost["remote_storage_plugins"] == ["bifrost"]
    extra = bifrost["extra_config"]
    assert (
        extra["remote_storage_plugin.bifrost.module_path"]
        == "lmcache_bifrost.adapter"
    )
    assert (
        extra["remote_storage_plugin.bifrost.class_name"]
        == "BifrostConnectorAdapter"
    )
    assert extra["endpoint"] == "127.0.0.1:7791"
    assert "bifrost_lmcache_connector_metrics.jsonl" in extra[
        "metrics_jsonl_path"
    ]


def test_prefix_caching_is_explicit_and_identical(tmp_path: Path) -> None:
    bundle = generate_baseline_matrix_configs(_matrix(tmp_path))

    commands = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in bundle.vllm_command_json.values()
    ]
    for command in commands:
        assert "--no-enable-prefix-caching" in command["command"]
        assert "--enable-prefix-caching" not in command["command"]
        assert "--generation-config" in command["command"]
        assert "vllm" in command["command"]
        assert command["vllm_core_flags"]["enable_prefix_caching"] is False


def test_fairness_rejects_common_field_drift(tmp_path: Path) -> None:
    matrix = _matrix(tmp_path)
    runs = list(matrix.runs)
    runs[1] = type(runs[1])(
        **{
            **{field: getattr(runs[1], field) for field in runs[1].__dataclass_fields__},
            "model": "./different-model",
        }
    )

    with pytest.raises(BaselineMatrixError, match="model differs"):
        BaselineMatrix(tuple(runs), output_dir=tmp_path).validate_fairness()


def test_fairness_rejects_connector_mode_drift(tmp_path: Path) -> None:
    matrix = _matrix(tmp_path)
    runs = list(matrix.runs)
    runs[2] = type(runs[2])(
        **{
            **{field: getattr(runs[2], field) for field in runs[2].__dataclass_fields__},
            "lmcache_connector_mode": "multiprocess",
        }
    )

    with pytest.raises(BaselineMatrixError, match="same connector mode"):
        BaselineMatrix(tuple(runs), output_dir=tmp_path).validate_fairness()


def test_no_secrets_are_generated(tmp_path: Path) -> None:
    bundle = generate_baseline_matrix_configs(_matrix(tmp_path))
    combined = "\n".join(path.read_text(encoding="utf-8") for path in bundle.files.values())

    assert "HF_TOKEN=" not in combined
    assert "HUGGING_FACE_HUB_TOKEN=" not in combined
    assert "hf_" not in combined
    assert "api_key" not in combined.lower()
    assert "authorization" not in combined.lower()


def test_generation_requires_no_real_dependencies(tmp_path: Path) -> None:
    before = set(sys.modules)
    generate_baseline_matrix_configs(_matrix(tmp_path))
    imported = set(sys.modules) - before

    assert "vllm" not in imported
    assert "lmcache" not in imported
    assert "torch" not in imported


def test_cli_dry_run_does_not_write_files(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(CLI),
            "--model",
            "./local-model",
            "--output-dir",
            str(tmp_path / "matrix"),
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
    assert "run_plan_yaml" in data["files"]
    assert not (tmp_path / "matrix").exists()


def _matrix(tmp_path: Path) -> BaselineMatrix:
    return BaselineMatrix.primary_isolation(
        model="./local-model",
        served_model_name="phase6-local",
        dtype="float16",
        max_model_len=2048,
        tensor_parallel_size=1,
        gpu_memory_utilization=0.85,
        enable_chunked_prefill=False,
        output_len=32,
        workload_path=tmp_path / "workload.jsonl",
        concurrency=2,
        request_rate=4.0,
        output_dir=tmp_path / "matrix",
        base_port=8100,
        bifrost_endpoint="127.0.0.1:7791",
        lmcache_connector_mode="inprocess",
        max_local_cpu_size=16,
    )

"""First-class Phase 6 serving baseline matrix generation.

This module is intentionally offline: it renders reproducible command/config
artifacts without importing vLLM, LMCache, torch, or the BIFROST connector.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Sequence

LMCACHE_KV_TRANSFER_CONFIG: dict[str, str] = {
    "kv_connector": "LMCacheConnectorV1",
    "kv_role": "kv_both",
}


class BaselineMatrixError(ValueError):
    """Deterministic Phase 6 baseline matrix configuration failure."""


class BaselineMode(str, Enum):
    VLLM_ONLY = "vllm_only"
    VLLM_LMCACHE_LOCAL_CPU = "vllm_lmcache_local_cpu"
    VLLM_LMCACHE_BIFROST = "vllm_lmcache_bifrost"


PRIMARY_BASELINE_MODES: tuple[BaselineMode, ...] = (
    BaselineMode.VLLM_ONLY,
    BaselineMode.VLLM_LMCACHE_LOCAL_CPU,
    BaselineMode.VLLM_LMCACHE_BIFROST,
)

COMMON_FIELD_NAMES: tuple[str, ...] = (
    "model",
    "served_model_name",
    "dtype",
    "max_model_len",
    "tensor_parallel_size",
    "gpu_memory_utilization",
    "enable_chunked_prefill",
    "enable_prefix_caching",
    "output_len",
    "sampling_settings",
    "workload_path",
    "concurrency",
    "request_rate",
)

VLLM_CORE_FIELD_NAMES: tuple[str, ...] = (
    "model",
    "served_model_name",
    "dtype",
    "max_model_len",
    "tensor_parallel_size",
    "gpu_memory_utilization",
    "enable_chunked_prefill",
    "enable_prefix_caching",
)


@dataclass(frozen=True, slots=True)
class BaselineRunConfig:
    mode: BaselineMode
    model: str
    served_model_name: str
    dtype: str
    max_model_len: int
    tensor_parallel_size: int
    gpu_memory_utilization: float
    enable_chunked_prefill: bool
    enable_prefix_caching: bool
    output_len: int
    sampling_settings: Mapping[str, Any]
    workload_path: Path
    concurrency: int
    request_rate: float
    port: int = 8000
    lmcache_enabled: bool = False
    lmcache_connector_mode: str | None = None
    lmcache_chunk_size: int = 256
    local_cpu: bool = False
    max_local_cpu_size: int = 8
    bifrost_enabled: bool = False
    bifrost_endpoint: str | None = None
    connector_metrics_path: Path | None = None

    def common_fields(self) -> dict[str, Any]:
        return {name: _jsonable(getattr(self, name)) for name in COMMON_FIELD_NAMES}

    def vllm_core_fields(self) -> dict[str, Any]:
        return {name: _jsonable(getattr(self, name)) for name in VLLM_CORE_FIELD_NAMES}

    def mode_specific_fields(self) -> dict[str, Any]:
        return {
            "mode": self.mode.value,
            "port": self.port,
            "lmcache_enabled": self.lmcache_enabled,
            "lmcache_connector_mode": self.lmcache_connector_mode,
            "lmcache_chunk_size": self.lmcache_chunk_size,
            "local_cpu": self.local_cpu,
            "max_local_cpu_size": self.max_local_cpu_size,
            "bifrost_enabled": self.bifrost_enabled,
            "bifrost_endpoint": self.bifrost_endpoint,
            "connector_metrics_path": (
                str(self.connector_metrics_path) if self.connector_metrics_path else None
            ),
        }


@dataclass(frozen=True, slots=True)
class BaselineMatrix:
    runs: tuple[BaselineRunConfig, ...]
    output_dir: Path = Path("examples/serving_configs")
    allow_connector_mode_mismatch: bool = False
    vllm_core_allowlist: frozenset[str] = field(default_factory=frozenset)

    @classmethod
    def primary_isolation(
        cls,
        *,
        model: str,
        served_model_name: str = "bifrost-phase6-model",
        dtype: str = "auto",
        max_model_len: int = 4096,
        tensor_parallel_size: int = 1,
        gpu_memory_utilization: float = 0.90,
        enable_chunked_prefill: bool = False,
        enable_prefix_caching: bool = False,
        output_len: int = 64,
        sampling_settings: Mapping[str, Any] | None = None,
        workload_path: Path = Path("runs/phase6-serving/workload.jsonl"),
        concurrency: int = 1,
        request_rate: float = 1.0,
        output_dir: Path = Path("examples/serving_configs"),
        base_port: int = 8000,
        bifrost_endpoint: str = "127.0.0.1:7744",
        lmcache_connector_mode: str = "inprocess",
        lmcache_chunk_size: int = 256,
        max_local_cpu_size: int = 8,
    ) -> "BaselineMatrix":
        sampling = dict(sampling_settings or {"temperature": 0.0, "top_p": 1.0})
        common = {
            "model": model,
            "served_model_name": served_model_name,
            "dtype": dtype,
            "max_model_len": max_model_len,
            "tensor_parallel_size": tensor_parallel_size,
            "gpu_memory_utilization": gpu_memory_utilization,
            "enable_chunked_prefill": enable_chunked_prefill,
            "enable_prefix_caching": enable_prefix_caching,
            "output_len": output_len,
            "sampling_settings": sampling,
            "workload_path": workload_path,
            "concurrency": concurrency,
            "request_rate": request_rate,
            "lmcache_connector_mode": lmcache_connector_mode,
            "lmcache_chunk_size": lmcache_chunk_size,
            "max_local_cpu_size": max_local_cpu_size,
        }
        runs = (
            BaselineRunConfig(
                mode=BaselineMode.VLLM_ONLY,
                port=base_port,
                lmcache_enabled=False,
                local_cpu=False,
                bifrost_enabled=False,
                bifrost_endpoint=None,
                connector_metrics_path=None,
                **{k: v for k, v in common.items() if k != "lmcache_connector_mode"},
            ),
            BaselineRunConfig(
                mode=BaselineMode.VLLM_LMCACHE_LOCAL_CPU,
                port=base_port + 1,
                lmcache_enabled=True,
                local_cpu=True,
                bifrost_enabled=False,
                bifrost_endpoint=None,
                connector_metrics_path=None,
                **common,
            ),
            BaselineRunConfig(
                mode=BaselineMode.VLLM_LMCACHE_BIFROST,
                port=base_port + 2,
                lmcache_enabled=True,
                local_cpu=False,
                bifrost_enabled=True,
                bifrost_endpoint=bifrost_endpoint,
                connector_metrics_path=output_dir
                / "vllm_lmcache_bifrost"
                / "bifrost_lmcache_connector_metrics.jsonl",
                **common,
            ),
        )
        matrix = cls(runs=runs, output_dir=output_dir)
        matrix.validate_fairness()
        return matrix

    def by_mode(self) -> dict[BaselineMode, BaselineRunConfig]:
        return {run.mode: run for run in self.runs}

    def validate_fairness(self) -> None:
        if tuple(run.mode for run in self.runs) != PRIMARY_BASELINE_MODES:
            raise BaselineMatrixError(
                "primary matrix must contain vllm_only, vllm_lmcache_local_cpu, "
                "and vllm_lmcache_bifrost in order"
            )
        _validate_common_fields(self.runs)
        _validate_core_flags(self.runs, self.vllm_core_allowlist)
        by_mode = self.by_mode()
        only = by_mode[BaselineMode.VLLM_ONLY]
        local = by_mode[BaselineMode.VLLM_LMCACHE_LOCAL_CPU]
        bifrost = by_mode[BaselineMode.VLLM_LMCACHE_BIFROST]
        if only.lmcache_enabled or only.bifrost_enabled:
            raise BaselineMatrixError("vllm_only must disable LMCache and BIFROST")
        if not local.lmcache_enabled or not local.local_cpu or local.bifrost_enabled:
            raise BaselineMatrixError(
                "vllm_lmcache_local_cpu must enable LMCache local CPU and disable BIFROST"
            )
        if not bifrost.lmcache_enabled or bifrost.local_cpu or not bifrost.bifrost_enabled:
            raise BaselineMatrixError(
                "vllm_lmcache_bifrost must enable BIFROST and disable local CPU"
            )
        if (
            local.lmcache_connector_mode != bifrost.lmcache_connector_mode
            and not self.allow_connector_mode_mismatch
        ):
            raise BaselineMatrixError(
                "LMCache local CPU and BIFROST modes must use the same connector mode"
            )
        if any(run.enable_prefix_caching for run in self.runs):
            raise BaselineMatrixError(
                "primary isolation matrix must explicitly disable vLLM prefix caching"
            )
        if bifrost.bifrost_endpoint is None:
            raise BaselineMatrixError("BIFROST mode requires a bifrost_endpoint")
        for run in self.runs:
            _validate_run(run)


@dataclass(frozen=True, slots=True)
class GeneratedConfigBundle:
    output_dir: Path
    run_plan_yaml: Path
    comparison_manifest_json: Path
    vllm_command_json: dict[BaselineMode, Path]
    lmcache_config_yaml: dict[BaselineMode, Path]
    bifrost_connector_config_json: Path
    warnings: tuple[str, ...] = ()

    @property
    def files(self) -> dict[str, Path]:
        files = {
            "run_plan_yaml": self.run_plan_yaml,
            "comparison_manifest_json": self.comparison_manifest_json,
            "bifrost_connector_config_json": self.bifrost_connector_config_json,
        }
        files.update(
            {f"vllm_command_{mode.value}": path for mode, path in self.vllm_command_json.items()}
        )
        files.update(
            {f"lmcache_config_{mode.value}": path for mode, path in self.lmcache_config_yaml.items()}
        )
        return files


def generate_baseline_matrix_configs(
    matrix: BaselineMatrix,
    *,
    dry_run: bool = False,
) -> GeneratedConfigBundle:
    matrix.validate_fairness()
    output_dir = matrix.output_dir
    vllm_paths = {
        run.mode: output_dir / run.mode.value / "vllm_command.json" for run in matrix.runs
    }
    lmcache_paths = {
        run.mode: output_dir / run.mode.value / "lmcache_config.yaml"
        for run in matrix.runs
        if run.lmcache_enabled
    }
    connector_path = (
        output_dir / BaselineMode.VLLM_LMCACHE_BIFROST.value / "bifrost_connector_config.json"
    )
    bundle = GeneratedConfigBundle(
        output_dir=output_dir,
        run_plan_yaml=output_dir / "phase6_matrix.yaml",
        comparison_manifest_json=output_dir / "comparison_manifest.json",
        vllm_command_json=vllm_paths,
        lmcache_config_yaml=lmcache_paths,
        bifrost_connector_config_json=connector_path,
        warnings=_warnings(matrix),
    )
    if dry_run:
        return bundle

    output_dir.mkdir(parents=True, exist_ok=True)
    _write_text(bundle.run_plan_yaml, render_run_plan_yaml(matrix, bundle))
    _write_json(bundle.comparison_manifest_json, build_comparison_manifest(matrix, bundle))
    for run in matrix.runs:
        _write_json(vllm_paths[run.mode], build_vllm_command(run, lmcache_paths.get(run.mode)))
        if run.lmcache_enabled:
            _write_text(lmcache_paths[run.mode], render_lmcache_config_yaml(run))
    bifrost_run = matrix.by_mode()[BaselineMode.VLLM_LMCACHE_BIFROST]
    _write_json(connector_path, build_bifrost_connector_config(bifrost_run))
    _scan_for_secrets(bundle.files.values())
    return bundle


def build_vllm_command(
    run: BaselineRunConfig,
    lmcache_config_path: Path | None = None,
) -> dict[str, Any]:
    argv = [
        "vllm",
        "serve",
        run.model,
        "--host",
        "127.0.0.1",
        "--port",
        str(run.port),
        "--served-model-name",
        run.served_model_name,
        "--dtype",
        run.dtype,
        "--max-model-len",
        str(run.max_model_len),
        "--tensor-parallel-size",
        str(run.tensor_parallel_size),
        "--gpu-memory-utilization",
        f"{run.gpu_memory_utilization:.6g}",
        "--enforce-eager",
        "--generation-config",
        "vllm",
    ]
    cli_boolean_compatibility: dict[str, str] = {}
    if run.enable_chunked_prefill:
        argv.append("--enable-chunked-prefill")
        cli_boolean_compatibility["enable_chunked_prefill"] = "explicit_true"
    else:
        argv.append("--no-enable-chunked-prefill")
        cli_boolean_compatibility["enable_chunked_prefill"] = "explicit_false"
    if run.enable_prefix_caching:
        argv.append("--enable-prefix-caching")
        cli_boolean_compatibility["enable_prefix_caching"] = "explicit_true"
    else:
        argv.append("--no-enable-prefix-caching")
        cli_boolean_compatibility["enable_prefix_caching"] = "explicit_false"
    if run.lmcache_enabled:
        argv.extend(
            (
                "--kv-transfer-config",
                json.dumps(LMCACHE_KV_TRANSFER_CONFIG, separators=(",", ":")),
            )
        )
    env: dict[str, str] = {
        "BIFROST_PHASE6_BASELINE_MODE": run.mode.value,
        "BIFROST_VLLM_MODEL": run.model,
        "BIFROST_VLLM_PORT": str(run.port),
        "PYTHONHASHSEED": "0",
    }
    if lmcache_config_path is not None:
        env["LMCACHE_CONFIG_FILE"] = str(lmcache_config_path)
        env["BIFROST_LMCACHE_CONNECTOR_MODE"] = str(run.lmcache_connector_mode)
    if run.bifrost_enabled and run.bifrost_endpoint:
        env["BIFROST_ENDPOINT"] = run.bifrost_endpoint
    return {
        "schema_version": "bifrost.phase6_vllm_command.v1",
        "mode": run.mode.value,
        "command": argv,
        "env": env,
        "vllm_core_flags": run.vllm_core_fields(),
        "cli_boolean_compatibility": cli_boolean_compatibility,
        "vllm_engine_compatibility": {
            "VLLM_USE_V1": "default",
            "enforce_eager": True,
        },
        "kv_transfer_config": LMCACHE_KV_TRANSFER_CONFIG if run.lmcache_enabled else None,
        "lmcache_enabled": run.lmcache_enabled,
        "bifrost_enabled": run.bifrost_enabled,
        "notes": [
            "Optional real-serving command; do not run unless explicitly opted in.",
            "Model must be available locally; this command must not trigger downloads by default.",
        ],
    }


def render_lmcache_config_yaml(run: BaselineRunConfig) -> str:
    if not run.lmcache_enabled:
        raise BaselineMatrixError(f"{run.mode.value} does not use LMCache")
    header = (
        "# Generated BIFROST Phase 6 LMCache baseline configuration.\n"
        "# Version-sensitive: verify field names against the installed LMCache release.\n"
    )
    common = f"""
mode: {run.lmcache_connector_mode}
object_type: opaque_engine_blob
chunk_size: {run.lmcache_chunk_size}
local_cpu: {_yaml_bool(run.local_cpu)}
max_local_cpu_size: {run.max_local_cpu_size}
lookup_timeout_ms: 60000
blocking_timeout_secs: 60
allow_pickle_fallback: false
"""
    if not run.bifrost_enabled:
        return header + common + """
remote_storage_plugins: []
remote_url: null
"""
    return header + common + f"""
remote_storage_plugins:
  - bifrost

remote_url: bifrost://{run.bifrost_endpoint}

extra_config:
  remote_storage_plugin.bifrost.module_path: lmcache_bifrost.adapter
  remote_storage_plugin.bifrost.class_name: BifrostConnectorAdapter
  endpoint: {run.bifrost_endpoint}
  chunk_size: 1048576
  timeout_seconds: 30.0
  ping_timeout: 120.0
  ping_interval: 300.0
  get_blocking_failed_threshold: 100000
  waiting_time_for_recovery: 5.0
  strict_validation: true
  allow_pickle_fallback: false
  engine_name: lmcache
  integration_name: lmcache_bifrost_remote_storage
  object_type: opaque_engine_blob
  metrics_jsonl_path: {run.connector_metrics_path}
"""


def build_bifrost_connector_config(run: BaselineRunConfig) -> dict[str, Any]:
    if not run.bifrost_enabled or not run.bifrost_endpoint:
        raise BaselineMatrixError("BIFROST connector config requires the BIFROST mode")
    return {
        "schema_version": "bifrost.phase6_lmcache_connector_config.v1",
        "mode": run.mode.value,
        "plugin": "bifrost",
        "module_path": "lmcache_bifrost.adapter",
        "class_name": "BifrostConnectorAdapter",
        "endpoint": run.bifrost_endpoint,
        "chunk_size": 1048576,
        "timeout_seconds": 30.0,
        "ping_timeout": 120.0,
        "ping_interval": 300.0,
        "get_blocking_failed_threshold": 100000,
        "waiting_time_for_recovery": 5.0,
        "strict_validation": True,
        "allow_pickle_fallback": False,
        "engine_name": "lmcache",
        "integration_name": "lmcache_bifrost_remote_storage",
        "object_type": "opaque_engine_blob",
        "metrics_jsonl_path": str(run.connector_metrics_path),
    }


def build_comparison_manifest(
    matrix: BaselineMatrix,
    bundle: GeneratedConfigBundle | None = None,
) -> dict[str, Any]:
    matrix.validate_fairness()
    common = matrix.runs[0].common_fields()
    mode_specific = {run.mode.value: run.mode_specific_fields() for run in matrix.runs}
    artifacts: dict[str, Any] = {}
    if bundle is not None:
        artifacts = {key: str(path) for key, path in bundle.files.items()}
    return {
        "schema_version": "bifrost.phase6_baseline_matrix_manifest.v1",
        "matrix": "primary_isolation",
        "common_fields": common,
        "mode_specific_fields": mode_specific,
        "fairness": {
            "status": "validated",
            "vllm_core_allowlist": sorted(matrix.vllm_core_allowlist),
            "connector_mode_mismatch_allowed": matrix.allow_connector_mode_mismatch,
            "prefix_caching": "explicitly_disabled",
        },
        "artifacts": artifacts,
    }


def render_run_plan_yaml(matrix: BaselineMatrix, bundle: GeneratedConfigBundle) -> str:
    manifest = build_comparison_manifest(matrix, bundle)
    data = {
        "schema_version": "bifrost.phase6_baseline_matrix_run_plan.v1",
        "matrix": "primary_isolation",
        "common_fields": manifest["common_fields"],
        "runs": [
            {
                "mode": run.mode.value,
                **run.mode_specific_fields(),
                "vllm_command_json": str(bundle.vllm_command_json[run.mode]),
                "lmcache_config_yaml": (
                    str(bundle.lmcache_config_yaml[run.mode])
                    if run.mode in bundle.lmcache_config_yaml
                    else None
                ),
            }
            for run in matrix.runs
        ],
        "comparison_manifest_json": str(bundle.comparison_manifest_json),
        "warnings": list(bundle.warnings),
    }
    return _simple_yaml(data)


def _validate_common_fields(runs: tuple[BaselineRunConfig, ...]) -> None:
    expected = runs[0].common_fields()
    for run in runs[1:]:
        actual = run.common_fields()
        for name, value in expected.items():
            if actual[name] != value:
                raise BaselineMatrixError(
                    f"fairness violation: {name} differs between "
                    f"{runs[0].mode.value} and {run.mode.value}"
                )


def _validate_core_flags(
    runs: tuple[BaselineRunConfig, ...],
    allowlist: frozenset[str],
) -> None:
    expected = runs[0].vllm_core_fields()
    for run in runs[1:]:
        actual = run.vllm_core_fields()
        for name, value in expected.items():
            if name in allowlist:
                continue
            if actual[name] != value:
                raise BaselineMatrixError(
                    f"vLLM core flag {name} differs outside the explicit allowlist"
                )


def _validate_run(run: BaselineRunConfig) -> None:
    if not run.model:
        raise BaselineMatrixError("model must be non-empty")
    if not run.served_model_name:
        raise BaselineMatrixError("served_model_name must be non-empty")
    if run.max_model_len <= 0:
        raise BaselineMatrixError("max_model_len must be positive")
    if run.tensor_parallel_size <= 0:
        raise BaselineMatrixError("tensor_parallel_size must be positive")
    if not (0 < run.gpu_memory_utilization <= 1):
        raise BaselineMatrixError("gpu_memory_utilization must be in (0, 1]")
    if run.output_len <= 0:
        raise BaselineMatrixError("output_len must be positive")
    if run.concurrency <= 0:
        raise BaselineMatrixError("concurrency must be positive")
    if run.request_rate <= 0:
        raise BaselineMatrixError("request_rate must be positive")
    if run.port <= 0 or run.port > 65535:
        raise BaselineMatrixError("port must be in 1..65535")
    if run.lmcache_chunk_size <= 0:
        raise BaselineMatrixError("lmcache_chunk_size must be positive")
    if run.max_local_cpu_size <= 0:
        raise BaselineMatrixError("max_local_cpu_size must be positive")


def _warnings(matrix: BaselineMatrix) -> tuple[str, ...]:
    warnings = [
        "Real serving is opt-in and may require GPU hardware, CUDA, vLLM, LMCache, lmcache_bifrost, and local model assets.",
        "vLLM and LMCache flag names are version-sensitive; verify generated commands against installed releases before running.",
        "The primary isolation matrix explicitly emits --no-enable-prefix-caching for every mode.",
    ]
    if not Path(matrix.runs[0].model).expanduser().exists():
        warnings.append(
            "The model value does not resolve to a local path; real-run scripts must refuse downloads unless explicitly allowed."
        )
    return tuple(warnings)


def _scan_for_secrets(paths: Sequence[Path]) -> None:
    forbidden = ("HF_TOKEN=", "HUGGING_FACE_HUB_TOKEN=", "hf_", "api_key", "authorization")
    for path in paths:
        if not path.exists() or path.is_dir():
            continue
        text = path.read_text(encoding="utf-8")
        lowered = text.lower()
        for marker in forbidden:
            haystack = lowered if marker.islower() else text
            if marker in haystack:
                raise BaselineMatrixError(f"generated file appears to contain a secret marker: {path}")


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def _jsonable(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, tuple):
        return [_jsonable(v) for v in value]
    return value


def _yaml_bool(value: bool) -> str:
    return "true" if value else "false"


def _simple_yaml(value: Any, indent: int = 0) -> str:
    lines = _simple_yaml_lines(value, indent)
    return "\n".join(lines) + "\n"


def _simple_yaml_lines(value: Any, indent: int) -> list[str]:
    prefix = " " * indent
    if isinstance(value, Mapping):
        lines: list[str] = []
        for key, item in value.items():
            if isinstance(item, (Mapping, list, tuple)):
                lines.append(f"{prefix}{key}:")
                lines.extend(_simple_yaml_lines(item, indent + 2))
            else:
                lines.append(f"{prefix}{key}: {_yaml_scalar(item)}")
        return lines
    if isinstance(value, (list, tuple)):
        lines = []
        for item in value:
            if isinstance(item, Mapping):
                lines.append(f"{prefix}-")
                lines.extend(_simple_yaml_lines(item, indent + 2))
            elif isinstance(item, (list, tuple)):
                lines.append(f"{prefix}-")
                lines.extend(_simple_yaml_lines(item, indent + 2))
            else:
                lines.append(f"{prefix}- {_yaml_scalar(item)}")
        return lines
    return [f"{prefix}{_yaml_scalar(value)}"]


def _yaml_scalar(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    text = str(value)
    return json.dumps(text)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate the Phase 6 three-baseline matrix")
    parser.add_argument("--model", default="./local-model")
    parser.add_argument("--served-model-name", default="bifrost-phase6-model")
    parser.add_argument("--dtype", default="auto")
    parser.add_argument("--max-model-len", type=int, default=4096)
    parser.add_argument("--tensor-parallel-size", type=int, default=1)
    parser.add_argument("--gpu-memory-utilization", type=float, default=0.90)
    parser.add_argument("--enable-chunked-prefill", action="store_true")
    parser.add_argument("--output-len", type=int, default=64)
    parser.add_argument("--workload-path", type=Path, default=Path("runs/phase6-serving/workload.jsonl"))
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--request-rate", type=float, default=1.0)
    parser.add_argument("--output-dir", type=Path, default=Path("examples/serving_configs"))
    parser.add_argument("--base-port", type=int, default=8000)
    parser.add_argument("--bifrost-endpoint", default="127.0.0.1:7744")
    parser.add_argument("--lmcache-connector-mode", default="inprocess")
    parser.add_argument("--lmcache-chunk-size", type=int, default=256)
    parser.add_argument("--max-local-cpu-size", type=int, default=8)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        matrix = BaselineMatrix.primary_isolation(
            model=args.model,
            served_model_name=args.served_model_name,
            dtype=args.dtype,
            max_model_len=args.max_model_len,
            tensor_parallel_size=args.tensor_parallel_size,
            gpu_memory_utilization=args.gpu_memory_utilization,
            enable_chunked_prefill=args.enable_chunked_prefill,
            output_len=args.output_len,
            workload_path=args.workload_path,
            concurrency=args.concurrency,
            request_rate=args.request_rate,
            output_dir=args.output_dir,
            base_port=args.base_port,
            bifrost_endpoint=args.bifrost_endpoint,
            lmcache_connector_mode=args.lmcache_connector_mode,
            lmcache_chunk_size=args.lmcache_chunk_size,
            max_local_cpu_size=args.max_local_cpu_size,
        )
        bundle = generate_baseline_matrix_configs(matrix, dry_run=args.dry_run)
    except Exception as exc:
        print(f"bifrost phase6 matrix generation failed: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(
            json.dumps(
                {
                    "output_dir": str(bundle.output_dir),
                    "dry_run": args.dry_run,
                    "files": {key: str(path) for key, path in bundle.files.items()},
                    "warnings": list(bundle.warnings),
                },
                indent=2,
                sort_keys=True,
            )
        )
    else:
        verb = "Would write" if args.dry_run else "Wrote"
        print(f"{verb} Phase 6 baseline matrix to {bundle.output_dir}")
        for label, path in sorted(bundle.files.items()):
            print(f"- {label}: {path}")
        for warning in bundle.warnings:
            print(f"warning: {warning}", file=sys.stderr)
    return 0


__all__ = [
    "BaselineMatrix",
    "BaselineMatrixError",
    "BaselineMode",
    "BaselineRunConfig",
    "GeneratedConfigBundle",
    "PRIMARY_BASELINE_MODES",
    "build_bifrost_connector_config",
    "build_comparison_manifest",
    "build_vllm_command",
    "generate_baseline_matrix_configs",
    "main",
    "render_lmcache_config_yaml",
    "render_run_plan_yaml",
]

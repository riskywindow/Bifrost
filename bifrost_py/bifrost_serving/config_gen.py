"""Phase 6 vLLM + LMCache + BIFROST config generation.

The generator is intentionally offline. It writes reproducible config artifacts
and guarded shell scripts, but it does not import LMCache or vLLM and does not
start any serving process.
"""

from __future__ import annotations

import argparse
import stat
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from .baseline_matrix import (
    BaselineMatrix,
    GeneratedConfigBundle,
    generate_baseline_matrix_configs,
)

SUPPORTED_MODES = {
    "fake",
    "lmcache_inprocess",
    "lmcache_mp",
    "bifrost_remote_storage",
    "vllm_bench_serve",
}

_MODE_ALIASES = {
    "lmcache-inprocess": "lmcache_inprocess",
    "lmcache-mp": "lmcache_mp",
    "bifrost-remote-storage": "bifrost_remote_storage",
    "vllm-bench-serve": "vllm_bench_serve",
}


@dataclass(frozen=True, slots=True)
class ServingConfigRequest:
    endpoint: str = "127.0.0.1:7744"
    model: str = "./local-model"
    mode: str = "fake"
    output_dir: Path = Path("examples/serving_configs")
    port: int = 8000
    lmcache_port: int = 9000
    chunk_size: int = 262144
    allow_pickle_fallback: bool = False
    gpu_memory_utilization: float | None = None
    max_model_len: int | None = None
    dry_run: bool = False

    def normalized_mode(self) -> str:
        return normalize_mode(self.mode)


@dataclass(frozen=True, slots=True)
class GeneratedServingConfig:
    output_dir: Path
    files: dict[str, Path] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()


def generate_phase6_baseline_matrix(
    matrix: BaselineMatrix,
    *,
    dry_run: bool = False,
) -> GeneratedConfigBundle:
    """Generate the first-class Phase 6 three-baseline serving matrix."""

    return generate_baseline_matrix_configs(matrix, dry_run=dry_run)


def normalize_mode(mode: str) -> str:
    normalized = _MODE_ALIASES.get(mode, mode).replace("-", "_")
    if normalized not in SUPPORTED_MODES:
        choices = ", ".join(sorted(SUPPORTED_MODES | set(_MODE_ALIASES)))
        raise ValueError(f"unsupported mode {mode!r}; expected one of: {choices}")
    return normalized


def generate_serving_config(request: ServingConfigRequest) -> GeneratedServingConfig:
    mode = request.normalized_mode()
    _validate_request(request, mode)
    output_dir = request.output_dir
    warnings = _warnings(request, mode)

    files = {
        "lmcache_inprocess": output_dir / "bifrost_lmcache_inprocess.yaml",
        "lmcache_mp": output_dir / "bifrost_lmcache_mp.yaml",
        "vllm_serve": output_dir / "vllm_serve_bifrost_lmcache.sh",
        "lmcache_server": output_dir / "lmcache_server_bifrost.sh",
        "env": output_dir / "serving.env",
        "bench": output_dir / "vllm_bench_serve_bifrost_lmcache.sh",
        "readme": output_dir / "README.md",
    }

    if request.dry_run:
        return GeneratedServingConfig(output_dir=output_dir, files=files, warnings=warnings)

    output_dir.mkdir(parents=True, exist_ok=True)
    files["lmcache_inprocess"].write_text(
        _render_lmcache_yaml(request, "lmcache_inprocess"),
        encoding="utf-8",
    )
    files["lmcache_mp"].write_text(
        _render_lmcache_yaml(request, "lmcache_mp"),
        encoding="utf-8",
    )
    files["vllm_serve"].write_text(_render_vllm_serve_script(request, mode), encoding="utf-8")
    files["lmcache_server"].write_text(
        _render_lmcache_server_script(request),
        encoding="utf-8",
    )
    files["env"].write_text(_render_env_file(request, mode), encoding="utf-8")
    files["bench"].write_text(_render_bench_script(request), encoding="utf-8")
    files["readme"].write_text(_render_readme(request, mode, warnings), encoding="utf-8")
    for script in (files["vllm_serve"], files["lmcache_server"], files["bench"]):
        _make_executable(script)
    return GeneratedServingConfig(output_dir=output_dir, files=files, warnings=warnings)


def _validate_request(request: ServingConfigRequest, mode: str) -> None:
    _split_endpoint(request.endpoint)
    if not request.model:
        raise ValueError("model must be non-empty")
    if request.port <= 0 or request.port > 65535:
        raise ValueError("port must be in 1..65535")
    if request.lmcache_port <= 0 or request.lmcache_port > 65535:
        raise ValueError("lmcache-port must be in 1..65535")
    if request.chunk_size <= 0:
        raise ValueError("chunk-size must be positive")
    if request.gpu_memory_utilization is not None and not (
        0 < request.gpu_memory_utilization <= 1
    ):
        raise ValueError("gpu-memory-utilization must be in (0, 1]")
    if request.max_model_len is not None and request.max_model_len <= 0:
        raise ValueError("max-model-len must be positive")
    if mode != "fake" and request.allow_pickle_fallback:
        raise ValueError("allow-pickle-fallback may only be enabled for fake mode")


def _split_endpoint(endpoint: str) -> tuple[str, int]:
    if ":" not in endpoint:
        raise ValueError("endpoint must be HOST:PORT")
    host, port_text = endpoint.rsplit(":", 1)
    if not host:
        raise ValueError("endpoint host must be non-empty")
    try:
        port = int(port_text)
    except ValueError as exc:
        raise ValueError("endpoint port must be an integer") from exc
    if port <= 0 or port > 65535:
        raise ValueError("endpoint port must be in 1..65535")
    return host, port


def _warnings(request: ServingConfigRequest, mode: str) -> tuple[str, ...]:
    warnings = [
        "LMCache plugin field names vary by release; verify remote_storage_plugins, extra_config, and remote_url against the installed LMCache version.",
        "vLLM LMCache enablement flags vary by release; verify the generated vLLM command before an opt-in real run.",
        "Real serving may require GPU hardware, CUDA, local model assets, and compatible vLLM plus LMCache packages.",
    ]
    if mode == "fake":
        warnings.append("Fake mode is for CI artifact generation only and does not prove real vLLM or LMCache behavior.")
    if not Path(request.model).expanduser().exists():
        warnings.append("The model value does not resolve to a local path; generated scripts refuse remote downloads unless BIFROST_ALLOW_MODEL_DOWNLOADS=1 is set.")
    return tuple(warnings)


def _render_lmcache_yaml(request: ServingConfigRequest, lmcache_mode: str) -> str:
    allow_pickle = _yaml_bool(request.allow_pickle_fallback)
    local_cpu = "true" if lmcache_mode == "lmcache_inprocess" else "false"
    multiprocess = "true" if lmcache_mode == "lmcache_mp" else "false"
    return f"""# Generated BIFROST Phase 6 LMCache configuration.
# Version-sensitive: LMCache custom remote storage field names vary by release.
# Verify this shape against the installed LMCache version before a real run.
# BIFROST stores LMCache-owned opaque_engine_blob payloads only.

mode: {lmcache_mode}
object_type: opaque_engine_blob
chunk_size: {request.chunk_size}
local_cpu: {local_cpu}
enable_multiprocess: {multiprocess}
lookup_timeout_ms: 60000
blocking_timeout_secs: 60

remote_storage_plugins:
  - bifrost

remote_url: bifrost://{request.endpoint}

extra_config:
  remote_storage_plugin.bifrost.module_path: lmcache_bifrost.adapter
  remote_storage_plugin.bifrost.class_name: BifrostConnectorAdapter
  endpoint: {request.endpoint}
  chunk_size: {request.chunk_size}
  timeout_seconds: 30.0
  ping_timeout: 120.0
  ping_interval: 300.0
  get_blocking_failed_threshold: 100000
  waiting_time_for_recovery: 5.0
  strict_validation: true
  allow_pickle_fallback: {allow_pickle}
  engine_name: lmcache
  integration_name: lmcache_bifrost_remote_storage
  object_type: opaque_engine_blob
  metrics_jsonl_path: ./bifrost_lmcache_connector_metrics.jsonl

# LMCache 0.3.x loads custom adapters from top-level extra_config keys named
# remote_storage_plugin.<plugin>.module_path/class_name.

multiprocess:
  enabled: {multiprocess}
  host: 127.0.0.1
  port: {request.lmcache_port}
  config_file: bifrost_lmcache_mp.yaml
"""


def _render_env_file(request: ServingConfigRequest, mode: str) -> str:
    gpu = "" if request.gpu_memory_utilization is None else str(request.gpu_memory_utilization)
    max_len = "" if request.max_model_len is None else str(request.max_model_len)
    return f"""# Generated BIFROST Phase 6 serving environment.
# Source this file only for opt-in local experiments. It contains no private
# tokens and intentionally does not set HF_TOKEN or HUGGING_FACE_HUB_TOKEN.

BIFROST_PHASE6_MODE={mode}
BIFROST_ENDPOINT={_env_quote(request.endpoint)}
BIFROST_VLLM_MODEL={_env_quote(request.model)}
BIFROST_VLLM_PORT={request.port}
BIFROST_LMCACHE_PORT={request.lmcache_port}
BIFROST_LMCACHE_CHUNK_SIZE={request.chunk_size}
BIFROST_ALLOW_PICKLE_FALLBACK={_shell_bool(request.allow_pickle_fallback)}
BIFROST_GPU_MEMORY_UTILIZATION={gpu}
BIFROST_MAX_MODEL_LEN={max_len}
LMCACHE_CONFIG_FILE={_env_quote("bifrost_lmcache_inprocess.yaml")}
"""


def _render_vllm_serve_script(request: ServingConfigRequest, mode: str) -> str:
    config_file = "bifrost_lmcache_mp.yaml" if mode == "lmcache_mp" else "bifrost_lmcache_inprocess.yaml"
    gpu_arg = ""
    if request.gpu_memory_utilization is not None:
        gpu_arg = f' \\\n  --gpu-memory-utilization "{request.gpu_memory_utilization}"'
    max_len_arg = ""
    if request.max_model_len is not None:
        max_len_arg = f' \\\n  --max-model-len "{request.max_model_len}"'
    return f"""#!/usr/bin/env bash
set -euo pipefail

# Generated optional vLLM + LMCache + BIFROST serving scaffold.
# This may require GPU hardware, CUDA, vLLM, LMCache, lmcache_bifrost, a running
# bifrostd daemon, and a model already available locally.
# Version-sensitive: exact vLLM LMCache flags may vary by release.

if [[ "${{BIFROST_RUN_VLLM_SERVE:-}}" != "1" ]]; then
  cat >&2 <<'EOF'
Refusing to start vLLM. Set BIFROST_RUN_VLLM_SERVE=1 only after verifying:
- bifrostd is running at the configured BIFROST_ENDPOINT
- vLLM, LMCache, and lmcache_bifrost are installed
- the model path is local, or BIFROST_ALLOW_MODEL_DOWNLOADS=1 is explicitly set
- GPU/CUDA requirements for your vLLM version are satisfied

No private tokens are required or embedded by this script.
EOF
  exit 2
fi

SCRIPT_DIR="$(cd "$(dirname "${{BASH_SOURCE[0]}}")" && pwd)"
DEFAULT_MODEL={_sh_quote(request.model)}
MODEL="${{BIFROST_VLLM_MODEL:-${{DEFAULT_MODEL}}}}"
PORT="${{BIFROST_VLLM_PORT:-{request.port}}}"
export BIFROST_ENDPOINT="${{BIFROST_ENDPOINT:-{request.endpoint}}}"
export LMCACHE_CONFIG_FILE="${{LMCACHE_CONFIG_FILE:-${{SCRIPT_DIR}}/{config_file}}}"

if [[ ! -e "${{MODEL}}" && "${{BIFROST_ALLOW_MODEL_DOWNLOADS:-}}" != "1" ]]; then
  echo "Refusing to pass a non-local model to vLLM without BIFROST_ALLOW_MODEL_DOWNLOADS=1: ${{MODEL}}" >&2
  exit 2
fi

exec vllm serve "${{MODEL}}" \\
  --host 127.0.0.1 \\
  --port "${{PORT}}" \\
  --enable-prefix-caching{gpu_arg}{max_len_arg}
"""


def _render_lmcache_server_script(request: ServingConfigRequest) -> str:
    return f"""#!/usr/bin/env bash
set -euo pipefail

# Generated optional LMCache multiprocess scaffold.
# Version-sensitive: LMCache server/controller command names vary by release.
# This script is documented, guarded, and may need local edits for your version.

if [[ "${{BIFROST_RUN_LMCACHE_SERVER:-}}" != "1" ]]; then
  cat >&2 <<'EOF'
Refusing to start an LMCache server. Set BIFROST_RUN_LMCACHE_SERVER=1 only
after verifying the LMCache multiprocess command for your installed version.
No private tokens are required or embedded by this script.
EOF
  exit 2
fi

SCRIPT_DIR="$(cd "$(dirname "${{BASH_SOURCE[0]}}")" && pwd)"
export BIFROST_ENDPOINT="${{BIFROST_ENDPOINT:-{request.endpoint}}}"
export LMCACHE_CONFIG_FILE="${{LMCACHE_CONFIG_FILE:-${{SCRIPT_DIR}}/bifrost_lmcache_mp.yaml}}"
PORT="${{BIFROST_LMCACHE_PORT:-{request.lmcache_port}}}"

exec python -m lmcache.server \\
  --host 127.0.0.1 \\
  --port "${{PORT}}" \\
  --config "${{LMCACHE_CONFIG_FILE}}"
"""


def _render_bench_script(request: ServingConfigRequest) -> str:
    return f"""#!/usr/bin/env bash
set -euo pipefail

# Generated optional vLLM bench serve scaffold.
# Version-sensitive: `vllm bench serve` arguments vary by vLLM release.

if [[ "${{BIFROST_RUN_VLLM_BENCH:-}}" != "1" ]]; then
  echo "Refusing to run vLLM benchmark without BIFROST_RUN_VLLM_BENCH=1." >&2
  exit 2
fi

DEFAULT_MODEL={_sh_quote(request.model)}
MODEL="${{BIFROST_VLLM_MODEL:-${{DEFAULT_MODEL}}}}"
PORT="${{BIFROST_VLLM_PORT:-{request.port}}}"

if [[ ! -e "${{MODEL}}" && "${{BIFROST_ALLOW_MODEL_DOWNLOADS:-}}" != "1" ]]; then
  echo "Refusing to benchmark a non-local model without BIFROST_ALLOW_MODEL_DOWNLOADS=1: ${{MODEL}}" >&2
  exit 2
fi

exec vllm bench serve \\
  --backend openai-chat \\
  --base-url "http://127.0.0.1:${{PORT}}" \\
  --model "${{MODEL}}" \\
  --num-prompts "${{BIFROST_BENCH_NUM_PROMPTS:-16}}" \\
  --request-rate "${{BIFROST_BENCH_REQUEST_RATE:-1.0}}"
"""


def _render_readme(
    request: ServingConfigRequest,
    mode: str,
    warnings: tuple[str, ...],
) -> str:
    warning_lines = "\n".join(f"- {warning}" for warning in warnings)
    return f"""# BIFROST Phase 6 Serving Configs

Generated for mode `{mode}` with BIFROST endpoint `{request.endpoint}`.

These files are scaffolds for opt-in serving experiments:

- `bifrost_lmcache_inprocess.yaml`: LMCache in-process BIFROST remote storage example.
- `bifrost_lmcache_mp.yaml`: LMCache multiprocess BIFROST remote storage example.
- `vllm_serve_bifrost_lmcache.sh`: guarded vLLM serve command.
- `lmcache_server_bifrost.sh`: guarded LMCache multiprocess server command.
- `vllm_bench_serve_bifrost_lmcache.sh`: guarded benchmark client command.
- `serving.env`: non-secret environment values for local runs.

The generated scripts do not embed Hugging Face tokens or any private token
value. They refuse to run unless explicit opt-in environment variables are set,
and they refuse non-local model values unless `BIFROST_ALLOW_MODEL_DOWNLOADS=1`
is explicitly set by the user.

Version-sensitive fields:

{warning_lines}

BIFROST remains behind LMCache remote storage in these examples. They are not a
raw vLLM KVTransfer connector configuration.
"""


def _make_executable(path: Path) -> None:
    mode = path.stat().st_mode
    path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _yaml_bool(value: bool) -> str:
    return "true" if value else "false"


def _shell_bool(value: bool) -> str:
    return "1" if value else "0"


def _env_quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _sh_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate Phase 6 vLLM + LMCache + BIFROST config artifacts"
    )
    parser.add_argument("--endpoint", default="127.0.0.1:7744", help="BIFROST daemon HOST:PORT")
    parser.add_argument("--model", default="./local-model", help="Local model path or model ID")
    parser.add_argument(
        "--mode",
        default="fake",
        choices=sorted(SUPPORTED_MODES | set(_MODE_ALIASES)),
        help="Config mode to emphasize in generated README and scripts",
    )
    parser.add_argument("--output-dir", default="examples/serving_configs", help="Output directory")
    parser.add_argument("--port", type=int, default=8000, help="vLLM serving port")
    parser.add_argument("--lmcache-port", type=int, default=9000, help="LMCache multiprocess port")
    parser.add_argument("--chunk-size", type=int, default=262144, help="LMCache/BIFROST chunk size")
    parser.add_argument(
        "--allow-pickle-fallback",
        default="false",
        choices=("true", "false"),
        help="Enable unsafe pickle fallback for fake mode only",
    )
    parser.add_argument("--gpu-memory-utilization", type=float, default=None)
    parser.add_argument("--max-model-len", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true", help="Print planned files without writing")
    args = parser.parse_args(argv)

    try:
        result = generate_serving_config(
            ServingConfigRequest(
                endpoint=args.endpoint,
                model=args.model,
                mode=args.mode,
                output_dir=Path(args.output_dir),
                port=args.port,
                lmcache_port=args.lmcache_port,
                chunk_size=args.chunk_size,
                allow_pickle_fallback=args.allow_pickle_fallback == "true",
                gpu_memory_utilization=args.gpu_memory_utilization,
                max_model_len=args.max_model_len,
                dry_run=args.dry_run,
            )
        )
    except Exception as exc:
        print(f"bifrost serving config generation failed: {exc}", file=sys.stderr)
        return 2

    action = "Would write" if args.dry_run else "Wrote"
    print(f"{action} BIFROST Phase 6 serving configs to {result.output_dir}")
    for label, path in sorted(result.files.items()):
        print(f"- {label}: {path}")
    for warning in result.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

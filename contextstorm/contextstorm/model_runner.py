"""Model-facing ContextStorm workloads for Phase 4 correctness checks."""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from math import ceil
from pathlib import Path
from typing import Any

import torch

from .model_metrics import ModelOperationMetrics
from .runner import (
    ContextStormError,
    REPO_ROOT,
    _environment,
    _find_binary,
    _free_port,
    _load_simple_yaml,
    _resolve_contextstorm_path,
    _stop_daemon,
    _wait_for_endpoint,
)

BIFROST_PY = REPO_ROOT / "bifrost_py"
if str(BIFROST_PY) not in sys.path:
    sys.path.insert(0, str(BIFROST_PY))

from bifrost_model import TinyIntTokenizer, TinyTransformer, TinyTransformerConfig
from bifrost_model.kv_cache import resume_generate_greedy
from bifrost_model.kv_page_codec import kv_cache_to_native_pages, native_pages_to_kv_cache
from bifrost_model.manifest_roundtrip import run_manifest_roundtrip
from bifrost_model.store_roundtrip import run_store_roundtrip


MODEL_OPERATIONS = {
    "local_kv_roundtrip",
    "store_kv_roundtrip",
    "manifest_kv_roundtrip",
    "kv_teleport",
}
LOGIT_ATOL = 1e-6


@dataclass(frozen=True)
class ModelScenario:
    name: str
    model: dict[str, Any]
    prompt: str
    decode_tokens: int
    block_size_tokens: int
    operations: tuple[str, ...]
    repetitions: int
    timeout_seconds: int
    workload: str = "model"


def is_model_scenario(path: Path) -> bool:
    data = _load_simple_yaml(path)
    operations = {str(op) for op in data.get("operations", [])}
    return bool(operations & MODEL_OPERATIONS) or str(data.get("workload", "")) == "model"


def load_model_scenario(path: Path) -> ModelScenario:
    data = _load_simple_yaml(path)
    operations = tuple(str(op) for op in data.get("operations", ["local_kv_roundtrip"]))
    unknown = sorted(set(operations) - MODEL_OPERATIONS)
    if unknown:
        raise ContextStormError(f"unsupported model operations in {path}: {unknown}")
    model = _model_config_dict(data.get("model") or {})
    TinyTransformerConfig(**model)
    return ModelScenario(
        name=str(data["name"]),
        workload=str(data.get("workload", "model")),
        model=model,
        prompt=str(data["prompt"]),
        decode_tokens=int(data.get("decode_tokens", 4)),
        block_size_tokens=int(data.get("block_size_tokens", 4)),
        operations=operations,
        repetitions=int(data.get("repetitions", 1)),
        timeout_seconds=int(data.get("timeout_seconds", 60)),
    )


def run_model_scenario(
    scenario_path: Path,
    *,
    runs_root: Path | None = None,
    run_id: str | None = None,
) -> Path:
    scenario_path = _resolve_contextstorm_path(scenario_path)
    scenario = load_model_scenario(scenario_path)

    needs_daemon = any(
        operation in {"store_kv_roundtrip", "manifest_kv_roundtrip", "kv_teleport"}
        for operation in scenario.operations
    )
    daemon_bin = _find_binary("bifrost-daemon") if needs_daemon else None
    xfer_bin = _find_binary("bifrost-xfer") if needs_daemon else None
    store_bin = _find_binary("bifrost-store") if needs_daemon else None
    if needs_daemon:
        missing = [
            name
            for name, value in {
                "bifrost-daemon": daemon_bin,
                "bifrost-xfer": xfer_bin,
                "bifrost-store": store_bin,
            }.items()
            if value is None
        ]
        if missing:
            raise ContextStormError(
                "missing Rust binaries: "
                + ", ".join(missing)
                + ". Build with `cargo build --bins` in bifrostd."
            )

    runs_root = runs_root or REPO_ROOT / "runs"
    run_id = run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = runs_root / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    for name in ("inputs", "outputs", "traces", "commands"):
        (run_dir / name).mkdir()
    shutil.copyfile(scenario_path, run_dir / "scenario.yaml")

    run_record: dict[str, Any] = {
        "schema_version": "contextstorm.run.v1",
        "benchmark_kind": "model",
        "scenario": _scenario_to_dict(scenario),
        "started_at_unix_ms": int(time.time() * 1000),
        "environment": _environment(),
        "operations": [],
        "notes": {
            "cpu_only": True,
            "requires_gpu": False,
            "requires_root": False,
            "external_downloads": False,
            "lmcache": False,
            "vllm": False,
        },
    }

    temp_root = tempfile.TemporaryDirectory(prefix="contextstorm-model-")
    daemon: dict[str, Any] | None = None
    endpoint: str | None = None
    try:
        if needs_daemon:
            assert daemon_bin is not None
            endpoint = f"127.0.0.1:{_free_port()}"
            trace_path = run_dir / "traces" / "daemon_primary.jsonl"
            spool = Path(temp_root.name) / "store_primary"
            command = [
                str(daemon_bin),
                "--listen",
                endpoint,
                "--spool",
                str(spool),
                "--trace-jsonl",
                str(trace_path),
            ]
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            daemon = {
                "path_name": "primary",
                "command": command,
                "trace_jsonl": str(trace_path),
                "process": process,
            }
            _wait_for_endpoint(endpoint, scenario.timeout_seconds)

        for repetition in range(scenario.repetitions):
            for operation in scenario.operations:
                result = _run_model_operation(
                    scenario=scenario,
                    operation=operation,
                    repetition=repetition,
                    run_dir=run_dir,
                    work_root=Path(temp_root.name),
                    endpoint=endpoint,
                    xfer_bin=xfer_bin,
                    store_bin=store_bin,
                )
                run_record["operations"].append(result)
    finally:
        if daemon is not None:
            run_record["daemons"] = [_stop_daemon(daemon, run_dir)]
        else:
            run_record["daemons"] = []
        temp_root.cleanup()
        run_record["finished_at_unix_ms"] = int(time.time() * 1000)
        (run_dir / "run.json").write_text(
            json.dumps(run_record, indent=2, sort_keys=True) + "\n"
        )
        from .model_report import write_model_report

        write_model_report(run_dir)
    return run_dir


def _run_model_operation(
    *,
    scenario: ModelScenario,
    operation: str,
    repetition: int,
    run_dir: Path,
    work_root: Path,
    endpoint: str | None,
    xfer_bin: Path | None,
    store_bin: Path | None,
) -> dict[str, Any]:
    started = time.monotonic()
    work_dir = work_root / f"rep_{repetition:03d}_{operation}"
    work_dir.mkdir(parents=True, exist_ok=True)
    try:
        if operation == "local_kv_roundtrip":
            summary = _run_local_roundtrip(scenario)
        elif operation == "store_kv_roundtrip":
            _require_default_model_config(scenario)
            summary = run_store_roundtrip(
                endpoint=_require_endpoint(endpoint, operation),
                prompt=scenario.prompt,
                decode_tokens=scenario.decode_tokens,
                block_size=scenario.block_size_tokens,
                seed=int(scenario.model["seed"]),
                work_dir=work_dir,
                xfer_bin=xfer_bin,
                store_bin=store_bin,
            )
        elif operation == "manifest_kv_roundtrip":
            _require_default_model_config(scenario)
            summary = run_manifest_roundtrip(
                endpoint=_require_endpoint(endpoint, operation),
                prompt=scenario.prompt,
                decode_tokens=scenario.decode_tokens,
                block_size=scenario.block_size_tokens,
                seed=int(scenario.model["seed"]),
                work_dir=work_dir,
                xfer_bin=xfer_bin,
                store_bin=store_bin,
            )
        elif operation == "kv_teleport":
            _require_default_model_config(scenario)
            summary = _run_kv_teleport(
                endpoint=_require_endpoint(endpoint, operation),
                prompt=scenario.prompt,
                decode_tokens=scenario.decode_tokens,
                block_size=scenario.block_size_tokens,
                seed=int(scenario.model["seed"]),
                work_dir=work_dir,
            )
        else:  # pragma: no cover - guarded by load_model_scenario
            raise ContextStormError(f"unsupported model operation: {operation}")
        metrics = _metrics_from_summary(
            operation=operation,
            repetition=repetition,
            scenario=scenario,
            summary=summary,
            elapsed_ms=_duration_ms(started),
        )
        exit_code = 0 if metrics.success else 1
    except Exception as exc:
        reason = _reason_code(exc)
        summary = {"status": "fail", "error": str(exc)}
        metrics = ModelOperationMetrics(
            operation=operation,
            repetition=repetition,
            success=False,
            reason_code=reason,
            failures=[
                {
                    "operation": operation,
                    "reason_code": reason,
                    "message": str(exc),
                }
            ],
        )
        exit_code = 1

    record = {
        "operation": operation,
        "repetition": repetition,
        "command": [],
        "exit_code": exit_code,
        "stdout": json.dumps(summary, sort_keys=True),
        "stderr": "" if exit_code == 0 else str(summary.get("error", "")),
        "parsed_stdout": summary,
        "metrics": metrics.to_dict(),
    }
    path = run_dir / "commands" / f"{operation}_{repetition:03d}.json"
    path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    return record


def _run_local_roundtrip(scenario: ModelScenario) -> dict[str, Any]:
    config = TinyTransformerConfig(**scenario.model)
    tokenizer = TinyIntTokenizer(vocab_size=config.vocab_size)
    prompt_tokens = tokenizer.encode(scenario.prompt)
    if not prompt_tokens:
        raise ValueError("prompt must contain at least one integer token")
    input_ids = torch.tensor(prompt_tokens, dtype=torch.long)
    model = TinyTransformer(config)
    model.eval()

    with torch.no_grad():
        baseline_tokens = model.generate_greedy(
            input_ids,
            max_new_tokens=scenario.decode_tokens,
        )
        baseline_continuation = baseline_tokens[len(prompt_tokens) :].tolist()

        prefill_started = time.perf_counter()
        prefix_logits, past_key_values = model.prefill(input_ids)
        prefill_ms = (time.perf_counter() - prefill_started) * 1000.0

        serialize_started = time.perf_counter()
        pages = kv_cache_to_native_pages(
            past_key_values,
            model,
            tokenizer,
            config,
            prompt_tokens,
            scenario.block_size_tokens,
        )
        serialize_ms = (time.perf_counter() - serialize_started) * 1000.0

        rehydrate_started = time.perf_counter()
        rehydrated = native_pages_to_kv_cache(pages, config)
        rehydrate_ms = (time.perf_counter() - rehydrate_started) * 1000.0

        next_input_id = int(torch.argmax(prefix_logits[-1]).item())
        baseline_next_logits, _ = model.decode_one(next_input_id, past_key_values)
        decode_started = time.perf_counter()
        rehydrated_next_logits, _ = model.decode_one(next_input_id, rehydrated)
        logit_max_abs_error = float(
            torch.max(torch.abs(baseline_next_logits - rehydrated_next_logits)).item()
        )
        rehydrated_continuation = resume_generate_greedy(
            model,
            next_input_id,
            rehydrated,
            max_new_tokens=scenario.decode_tokens,
        ).tolist()
        decode_resume_ms = (time.perf_counter() - decode_started) * 1000.0

    continuation_match = baseline_continuation == rehydrated_continuation
    page_count = len(pages)
    expected_page_count = config.num_layers * ceil(
        len(prompt_tokens) / scenario.block_size_tokens
    )
    status = (
        "pass"
        if continuation_match
        and logit_max_abs_error <= LOGIT_ATOL
        and page_count == expected_page_count
        else "fail"
    )
    return {
        "status": status,
        "prompt_tokens": prompt_tokens,
        "page_count": page_count,
        "total_payload_bytes": sum(len(page.payload) for page in pages),
        "baseline_continuation": baseline_continuation,
        "rehydrated_continuation": rehydrated_continuation,
        "continuation_match": continuation_match,
        "logit_max_abs_error": logit_max_abs_error,
        "prefill_ms": prefill_ms,
        "kv_page_serialize_ms": serialize_ms,
        "rehydrate_ms": rehydrate_ms,
        "decode_resume_ms": decode_resume_ms,
        "object_ids": [page.metadata["object_id"] for page in pages],
    }


def _metrics_from_summary(
    *,
    operation: str,
    repetition: int,
    scenario: ModelScenario,
    summary: dict[str, Any],
    elapsed_ms: float,
) -> ModelOperationMetrics:
    status = str(summary.get("status", summary.get("result", "fail"))).lower()
    continuation_match = _continuation_match(summary)
    logit_error = _optional_float(summary.get("logit_max_abs_error"))
    manifest_completeness = summary.get("manifest_completeness")
    page_count = int(summary.get("page_count") or 0)
    total_payload_bytes = int(
        summary.get("total_payload_bytes")
        or _estimated_payload_bytes(scenario, summary)
    )
    pages_stored = int(
        summary.get("put_success_count")
        or summary.get("pages_written")
        or (page_count if operation == "local_kv_roundtrip" and status == "pass" else 0)
    )
    pages_rehydrated = int(
        summary.get("get_success_count")
        or summary.get("pages_read")
        or (page_count if operation == "local_kv_roundtrip" and status == "pass" else 0)
    )
    success = (
        status == "pass"
        and continuation_match is True
        and (logit_error is not None and logit_error <= LOGIT_ATOL)
        and (
            manifest_completeness in {None, "complete", 1.0, "1.0", True}
        )
    )
    reason = None if success else "correctness_failed"
    failures = [] if success else [
        {
            "operation": operation,
            "reason_code": reason,
            "message": str(summary.get("error") or "model correctness check failed"),
        }
    ]
    store_put_ms = float(summary.get("total_put_ms") or 0.0)
    store_get_ms = float(summary.get("total_get_ms") or 0.0)
    rehydrate_ms = float(summary.get("rehydrate_ms") or 0.0)
    decode_resume_ms = float(summary.get("decode_resume_ms") or 0.0)
    if operation == "kv_teleport":
        prefill = summary.get("prefill") or {}
        decode = summary.get("decode") or {}
        store_put_ms = float(prefill.get("total_put_ms") or store_put_ms)
        store_get_ms = float(decode.get("total_get_ms") or store_get_ms)
        rehydrate_ms = float(decode.get("rehydrate_ms") or rehydrate_ms)
    return ModelOperationMetrics(
        operation=operation,
        repetition=repetition,
        success=success,
        reason_code=reason,
        prefill_ms=float(summary.get("prefill_ms") or 0.0),
        kv_page_serialize_ms=float(summary.get("kv_page_serialize_ms") or 0.0),
        page_count=page_count,
        total_payload_bytes=total_payload_bytes,
        store_put_ms=store_put_ms,
        store_get_ms=store_get_ms,
        manifest_create_ms=elapsed_ms if operation == "manifest_kv_roundtrip" else 0.0,
        manifest_check_ms=elapsed_ms if operation == "manifest_kv_roundtrip" else 0.0,
        rehydrate_ms=rehydrate_ms,
        decode_resume_ms=decode_resume_ms,
        logit_max_abs_error=logit_error,
        continuation_match=continuation_match,
        manifest_completeness=manifest_completeness,
        pages_stored=pages_stored,
        pages_rehydrated=pages_rehydrated,
        failures=failures,
    )


def _run_kv_teleport(
    *,
    endpoint: str,
    prompt: str,
    decode_tokens: int,
    block_size: int,
    seed: int,
    work_dir: Path,
) -> dict[str, Any]:
    script_path = REPO_ROOT / "examples" / "tiny_transformer" / "kv_teleport_demo.py"
    module = _load_module(script_path, "contextstorm_kv_teleport_demo")
    runner = getattr(module, "run_kv_teleport_demo")
    return runner(
        endpoint=endpoint,
        prompt=prompt,
        decode_tokens=decode_tokens,
        block_size=block_size,
        seed=seed,
        work_dir=work_dir,
    )


def _load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load module from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _model_config_dict(value: dict[str, Any]) -> dict[str, Any]:
    defaults = asdict(TinyTransformerConfig())
    defaults.update(value)
    if "hidden_size" not in value:
        defaults["hidden_size"] = int(defaults["num_heads"]) * int(defaults["head_dim"])
    return defaults


def _require_default_model_config(scenario: ModelScenario) -> None:
    default = _model_config_dict({"seed": scenario.model["seed"]})
    comparable = dict(scenario.model)
    if comparable != default:
        raise ContextStormError(
            "daemon-backed Phase 4 model workloads currently require the default "
            "tiny-transformer shape; refusing to run a mismatched config"
        )


def _require_endpoint(endpoint: str | None, operation: str) -> str:
    if not endpoint:
        raise ContextStormError(f"{operation} requires a local BIFROST daemon")
    return endpoint


def _estimated_payload_bytes(
    scenario: ModelScenario,
    summary: dict[str, Any],
) -> int:
    prompt_tokens = summary.get("prompt_tokens")
    token_count = len(prompt_tokens) if isinstance(prompt_tokens, list) else 0
    if token_count <= 0:
        token_count = len(TinyIntTokenizer(scenario.model["vocab_size"]).encode(scenario.prompt))
    bytes_per_token_layer = (
        2
        * int(scenario.model["num_kv_heads"])
        * int(scenario.model["head_dim"])
        * 4
    )
    return int(scenario.model["num_layers"]) * token_count * bytes_per_token_layer


def _continuation_match(summary: dict[str, Any]) -> bool | None:
    if "continuation_match" in summary:
        return bool(summary["continuation_match"])
    if "greedy_tokens_match" in summary:
        return bool(summary["greedy_tokens_match"])
    baseline = summary.get("baseline_continuation")
    roundtrip = (
        summary.get("rehydrated_continuation")
        if "rehydrated_continuation" in summary
        else summary.get("bifrost_continuation")
    )
    if baseline is None or roundtrip is None:
        return None
    return baseline == roundtrip


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


def _reason_code(exc: Exception) -> str:
    if isinstance(exc, ContextStormError):
        return "model_scenario_error"
    if isinstance(exc, ValueError):
        return "model_validation_error"
    if isinstance(exc, FileNotFoundError):
        return "model_missing_binary"
    return "model_workload_error"


def _duration_ms(started: float) -> float:
    return max(0.001, (time.monotonic() - started) * 1000.0)


def _scenario_to_dict(scenario: ModelScenario) -> dict[str, Any]:
    return {
        "name": scenario.name,
        "workload": scenario.workload,
        "model": dict(scenario.model),
        "prompt": scenario.prompt,
        "decode_tokens": scenario.decode_tokens,
        "block_size_tokens": scenario.block_size_tokens,
        "operations": list(scenario.operations),
        "repetitions": scenario.repetitions,
        "timeout_seconds": scenario.timeout_seconds,
    }

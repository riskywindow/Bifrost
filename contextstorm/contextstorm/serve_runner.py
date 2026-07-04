"""Serving benchmark scenarios for ContextStorm Phase 6."""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .runner import ContextStormError, REPO_ROOT, _environment, _load_simple_yaml, _resolve_contextstorm_path

BIFROST_PY = REPO_ROOT / "bifrost_py"
LMCACHE_INTEGRATION = REPO_ROOT / "integrations" / "lmcache_bifrost"
for path in (BIFROST_PY, LMCACHE_INTEGRATION):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from bifrost_serving.compare import BaselineComparisonConfig, run_baseline_comparison  # noqa: E402
from bifrost_serving.config_gen import ServingConfigRequest, generate_serving_config  # noqa: E402
from bifrost_serving.report import ServingReportConfig, generate_serving_report  # noqa: E402
from bifrost_serving.workloads import WorkloadConfig, generate_workload, parse_prefix_size, write_workload  # noqa: E402


SERVE_OPERATIONS = {
    "fake_serving_baseline_comparison",
    "real_vllm_lmcache_bifrost",
    "two_instance_cache_share",
}


@dataclass(frozen=True)
class ServingScenario:
    name: str
    workload: str = "serve"
    operations: tuple[str, ...] = ("fake_serving_baseline_comparison",)
    generator: str = "fake-ci-small"
    request_count: int = 8
    prefix_repeat_groups: int = 2
    max_tokens: int = 16
    seed: int = 1234
    prefix_size: str = "small"
    concurrency: int = 2
    timeout_seconds: int = 30
    modes: tuple[str, ...] = ("fake_no_cache", "fake_with_cache")
    collect_bifrost_stats: bool = False
    bifrost_endpoint: str | None = None
    model: str | None = None
    opt_in_env: str = "BIFROST_RUN_REAL_VLLM"
    generate_config: bool = False
    config_mode: str = "bifrost_remote_storage"
    notes: str = ""


def is_serving_scenario(path: Path) -> bool:
    data = _load_simple_yaml(path)
    operations = {str(op) for op in data.get("operations", [])}
    return str(data.get("workload", "")) == "serve" or bool(operations & SERVE_OPERATIONS)


def load_serving_scenario(path: Path) -> ServingScenario:
    data = _load_simple_yaml(path)
    operations = tuple(str(op) for op in data.get("operations", ["fake_serving_baseline_comparison"]))
    unknown = sorted(set(operations) - SERVE_OPERATIONS)
    if unknown:
        raise ContextStormError(f"unsupported serving operations in {path}: {unknown}")
    return ServingScenario(
        name=str(data["name"]),
        workload=str(data.get("workload", "serve")),
        operations=operations,
        generator=str(data.get("generator", "fake-ci-small")),
        request_count=int(data.get("request_count", 8)),
        prefix_repeat_groups=int(data.get("prefix_repeat_groups", 2)),
        max_tokens=int(data.get("max_tokens", 16)),
        seed=int(data.get("seed", 1234)),
        prefix_size=str(data.get("prefix_size", "small")),
        concurrency=int(data.get("concurrency", 2)),
        timeout_seconds=int(data.get("timeout_seconds", 30)),
        modes=tuple(str(mode) for mode in data.get("modes", ["fake_no_cache", "fake_with_cache"])),
        collect_bifrost_stats=bool(data.get("collect_bifrost_stats", False)),
        bifrost_endpoint=data.get("bifrost_endpoint"),
        model=data.get("model"),
        opt_in_env=str(data.get("opt_in_env", "BIFROST_RUN_REAL_VLLM")),
        generate_config=bool(data.get("generate_config", False)),
        config_mode=str(data.get("config_mode", "bifrost_remote_storage")),
        notes=str(data.get("notes", "")),
    )


def run_serving_scenario(
    scenario_path: Path,
    *,
    runs_root: Path | None = None,
    run_id: str | None = None,
) -> Path:
    scenario_path = _resolve_contextstorm_path(scenario_path)
    scenario = load_serving_scenario(scenario_path)
    runs_root = runs_root or REPO_ROOT / "runs"
    run_id = run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = runs_root / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    for name in ("inputs", "outputs", "traces", "commands", "phase6"):
        (run_dir / name).mkdir()
    shutil.copyfile(scenario_path, run_dir / "scenario.yaml")

    run_record: dict[str, Any] = {
        "schema_version": "contextstorm.run.v1",
        "benchmark_kind": "serving",
        "scenario": asdict(scenario),
        "started_at_unix_ms": int(time.time() * 1000),
        "environment": _environment(),
        "operations": [],
        "phase6_reports": [],
        "notes": {
            "default_cpu_only": True,
            "requires_real_vllm_by_default": False,
            "requires_real_lmcache_by_default": False,
            "requires_gpu_by_default": False,
            "external_downloads": False,
            "raw_vllm_kvtransfer": False,
        },
    }

    try:
        workload_path = run_dir / "inputs" / "serving_requests.jsonl"
        workload_summary_path = run_dir / "inputs" / "serving_workload_summary.json"
        workload = generate_workload(
            WorkloadConfig(
                workload_name=scenario.generator,
                request_count=scenario.request_count,
                prefix_repeat_groups=scenario.prefix_repeat_groups,
                max_tokens=scenario.max_tokens,
                seed=scenario.seed,
                prefix_length_chars=parse_prefix_size(scenario.prefix_size),
            )
        )
        write_workload(workload, out=workload_path, summary_path=workload_summary_path)

        if scenario.generate_config:
            generated = generate_serving_config(
                ServingConfigRequest(
                    endpoint=scenario.bifrost_endpoint or "127.0.0.1:7420",
                    model=scenario.model or "./local-model",
                    mode=scenario.config_mode,
                    output_dir=run_dir / "phase6" / "generated_config",
                )
            )
            run_record["generated_config"] = {
                key: str(value) for key, value in generated.files.items()
            }
            run_record["generated_config_warnings"] = list(generated.warnings)

        for index, operation in enumerate(scenario.operations):
            if operation == "fake_serving_baseline_comparison":
                result = _run_fake_comparison(scenario, workload_path, run_dir, index)
            elif operation == "real_vllm_lmcache_bifrost":
                result = _run_optional_real_comparison(scenario, workload_path, run_dir, index)
            elif operation == "two_instance_cache_share":
                result = _run_optional_two_instance(scenario, run_dir, index)
            else:
                raise ContextStormError(f"unsupported serving operation: {operation}")
            run_record["operations"].append(result)
            artifacts = result.get("metrics", {}).get("raw_phase6_artifacts")
            if artifacts:
                run_record["phase6_reports"].append(artifacts)
    finally:
        run_record["finished_at_unix_ms"] = int(time.time() * 1000)
        (run_dir / "run.json").write_text(
            json.dumps(run_record, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        from .serve_report import write_serving_report

        write_serving_report(run_dir)
    return run_dir


def _run_fake_comparison(
    scenario: ServingScenario,
    workload_path: Path,
    run_dir: Path,
    repetition: int,
) -> dict[str, Any]:
    try:
        comparison = run_baseline_comparison(
            BaselineComparisonConfig(
                workload_jsonl=workload_path,
                output_dir=run_dir / "phase6" / "fake_comparison",
                modes=scenario.modes,
                concurrency=scenario.concurrency,
                bifrost_endpoint=scenario.bifrost_endpoint,
                timeout_seconds=scenario.timeout_seconds,
            )
        )
        report_artifacts = _generate_report_for_completed_mode(
            comparison.summary,
            comparison.output_dir,
            run_dir / "phase6" / "fake_report",
        )
        metrics = _metrics_from_comparison(
            "fake_serving_baseline_comparison",
            comparison.summary,
            comparison.summary_path,
            comparison.markdown_path,
            report_artifacts,
        )
        return _operation_record("fake_serving_baseline_comparison", repetition, 0, metrics)
    except Exception as exc:
        return _operation_record(
            "fake_serving_baseline_comparison",
            repetition,
            1,
            _failure_metrics("fake_serving_baseline_comparison", exc),
        )


def _run_optional_real_comparison(
    scenario: ServingScenario,
    workload_path: Path,
    run_dir: Path,
    repetition: int,
) -> dict[str, Any]:
    if os.environ.get(scenario.opt_in_env) != "1":
        return _operation_record(
            "real_vllm_lmcache_bifrost",
            repetition,
            0,
            _skip_metrics(
                "real_vllm_lmcache_bifrost",
                f"{scenario.opt_in_env}=1 is required for real vLLM serving",
            ),
        )
    try:
        comparison = run_baseline_comparison(
            BaselineComparisonConfig(
                workload_jsonl=workload_path,
                output_dir=run_dir / "phase6" / "real_comparison",
                modes=scenario.modes,
                concurrency=scenario.concurrency,
                bifrost_endpoint=scenario.bifrost_endpoint,
                model=scenario.model,
                allow_real_vllm=True,
                timeout_seconds=scenario.timeout_seconds,
            )
        )
        report_artifacts = _generate_report_for_completed_mode(
            comparison.summary,
            comparison.output_dir,
            run_dir / "phase6" / "real_report",
        )
        metrics = _metrics_from_comparison(
            "real_vllm_lmcache_bifrost",
            comparison.summary,
            comparison.summary_path,
            comparison.markdown_path,
            report_artifacts,
        )
        return _operation_record("real_vllm_lmcache_bifrost", repetition, 0, metrics)
    except Exception as exc:
        return _operation_record(
            "real_vllm_lmcache_bifrost",
            repetition,
            1,
            _failure_metrics("real_vllm_lmcache_bifrost", exc),
        )


def _run_optional_two_instance(
    scenario: ServingScenario,
    run_dir: Path,
    repetition: int,
) -> dict[str, Any]:
    if os.environ.get(scenario.opt_in_env) != "1":
        return _operation_record(
            "two_instance_cache_share",
            repetition,
            0,
            _skip_metrics(
                "two_instance_cache_share",
                f"{scenario.opt_in_env}=1 is required for the two-instance experiment",
            ),
        )
    marker = run_dir / "phase6" / "two_instance_cache_share.md"
    marker.write_text(
        "Two-instance cache sharing is an opt-in exploratory Phase 6 scenario. "
        "Use the generated serving configs and orchestrator to start two real serving instances.\n",
        encoding="utf-8",
    )
    metrics = _skip_metrics(
        "two_instance_cache_share",
        "two-instance orchestration is advisory in ContextStorm and requires a supported local real-serving environment",
    )
    metrics["raw_phase6_artifacts"] = {"two_instance_notes": str(marker)}
    return _operation_record("two_instance_cache_share", repetition, 0, metrics)


def _generate_report_for_completed_mode(
    comparison_summary: dict[str, Any],
    comparison_dir: Path,
    out_dir: Path,
) -> dict[str, str]:
    completed = [
        item for item in comparison_summary.get("mode_results", [])
        if item.get("status") == "completed" and item.get("summary_path")
    ]
    if not completed:
        return {}
    preferred = next(
        (item for item in completed if item.get("mode") in {"fake_with_cache", "vllm_lmcache_bifrost"}),
        completed[-1],
    )
    run_dir = Path(str(preferred["output_dir"]))
    report = generate_serving_report(
        ServingReportConfig(
            run_dir=run_dir,
            comparison_dir=comparison_dir,
            out=out_dir,
            format="all",
        )
    )
    artifacts: dict[str, str] = {
        "phase6_comparison_summary": str(comparison_dir / "comparison_summary.json"),
        "phase6_comparison_markdown": str(comparison_dir / "comparison_summary.md"),
        "phase6_run_summary": str(run_dir / "summary.json"),
        "phase6_raw_requests": str(run_dir / "raw_requests.jsonl"),
    }
    if report.report_path:
        artifacts["phase6_report_markdown"] = str(report.report_path)
    if report.summary_path:
        artifacts["phase6_report_summary"] = str(report.summary_path)
    return artifacts


def _metrics_from_comparison(
    operation: str,
    comparison_summary: dict[str, Any],
    summary_path: Path,
    markdown_path: Path,
    report_artifacts: dict[str, str],
) -> dict[str, Any]:
    completed = [
        item for item in comparison_summary.get("mode_results", [])
        if item.get("status") == "completed" and isinstance(item.get("summary"), dict)
    ]
    latest_summary = completed[-1]["summary"] if completed else {}
    skipped = [
        f"{item.get('mode')} skipped: {item.get('skip_reason')}"
        for item in comparison_summary.get("mode_results", [])
        if item.get("status") == "skipped"
    ]
    failed = [
        {
            "operation": operation,
            "reason_code": "serving_mode_failed",
            "message": f"{item.get('mode')}: {item.get('error')}",
        }
        for item in comparison_summary.get("mode_results", [])
        if item.get("status") == "failed"
    ]
    artifacts = {
        "phase6_comparison_summary": str(summary_path),
        "phase6_comparison_markdown": str(markdown_path),
        **report_artifacts,
    }
    return {
        "operation": operation,
        "success": not failed,
        "skipped": False,
        "request_count": latest_summary.get("request_count"),
        "success_count": latest_summary.get("success_count"),
        "error_count": latest_summary.get("error_count"),
        "p50_latency_ms": latest_summary.get("p50_latency_ms"),
        "p95_latency_ms": latest_summary.get("p95_latency_ms"),
        "p50_ttft_ms": latest_summary.get("p50_ttft_ms"),
        "p95_ttft_ms": latest_summary.get("p95_ttft_ms"),
        "throughput_rps": latest_summary.get("throughput_rps"),
        "error_rate": latest_summary.get("error_rate"),
        "repeated_prefix_group_count": latest_summary.get("repeated_prefix_group_count"),
        "bifrost_stats_delta": latest_summary.get("bifrost_stats_delta"),
        "correctness_status": "advisory" if completed else "skipped",
        "skipped_components": skipped,
        "raw_phase6_artifacts": artifacts,
        "failures": failed,
    }


def _skip_metrics(operation: str, reason: str) -> dict[str, Any]:
    return {
        "operation": operation,
        "success": True,
        "skipped": True,
        "reason_code": "skipped_optional_component",
        "correctness_status": "skipped",
        "skipped_components": [reason],
        "raw_phase6_artifacts": {},
        "failures": [],
    }


def _failure_metrics(operation: str, exc: Exception) -> dict[str, Any]:
    return {
        "operation": operation,
        "success": False,
        "skipped": False,
        "reason_code": "serving_benchmark_error",
        "correctness_status": "failed",
        "skipped_components": [],
        "raw_phase6_artifacts": {},
        "failures": [
            {
                "operation": operation,
                "reason_code": "serving_benchmark_error",
                "message": str(exc),
            }
        ],
    }


def _operation_record(
    operation: str,
    repetition: int,
    exit_code: int,
    metrics: dict[str, Any],
) -> dict[str, Any]:
    metrics.setdefault("operation", operation)
    metrics.setdefault("repetition", repetition)
    return {
        "operation": operation,
        "repetition": repetition,
        "exit_code": exit_code,
        "metrics": metrics,
    }

#!/usr/bin/env python3
"""Optional two-instance BIFROST-backed LMCache cache-sharing scaffold.

Dry-run and readiness modes are CI-safe. Run mode is for local exploratory
serving only and refuses to start real vLLM unless explicitly opted in.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

REPO_ROOT = Path(__file__).resolve().parents[2]
for source_path in (
    REPO_ROOT / "bifrost_py",
    REPO_ROOT / "integrations" / "lmcache_bifrost",
):
    text = str(source_path)
    if text not in sys.path:
        sys.path.insert(0, text)

from bifrost_serving.collectors import BifrostMetricsCollector, bifrost_snapshot_delta
from bifrost_serving.env_doctor import EnvDoctorConfig, run_doctor
from bifrost_serving.orchestrator import OrchestratorConfig, build_processes
from bifrost_serving.runner import ServingBenchmarkConfig, run_serving_benchmark
from bifrost_serving.workloads import WorkloadConfig, generate_workload, write_workload

MODES = {"dry-run", "readiness", "run"}


@dataclass(frozen=True, slots=True)
class TwoInstanceConfig:
    mode: str
    output_dir: Path
    model: str | None
    bifrost_endpoint: str = "127.0.0.1:7420"
    instance_a_port: int = 8010
    instance_b_port: int = 8011
    instance_a_lmcache_port: int = 9010
    instance_b_lmcache_port: int = 9011
    request_count: int = 8
    concurrency: int = 1
    prefix_repeat_groups: int = 2
    max_tokens: int = 16
    workload_name: str = "repeated_system_prompt"
    allow_real_vllm: bool = False
    allow_model_downloads: bool = False
    readiness_timeout_seconds: float = 120.0
    timeout_seconds: float = 60.0


class TwoInstanceDemoError(RuntimeError):
    """Deterministic optional two-instance experiment failure."""


def run_demo(config: TwoInstanceConfig) -> dict[str, Any]:
    _validate_config(config)
    config.output_dir.mkdir(parents=True, exist_ok=True)

    workload_paths = _write_workloads(config)
    doctor = run_doctor(
        EnvDoctorConfig(
            endpoint=config.bifrost_endpoint,
            model=config.model,
            output_dir=config.output_dir,
            required_ports=(
                config.instance_a_port,
                config.instance_b_port,
                config.instance_a_lmcache_port,
                config.instance_b_lmcache_port,
            ),
        )
    )
    doctor_path = config.output_dir / "environment_readiness.json"
    doctor_path.write_text(doctor.to_json(indent=2) + "\n", encoding="utf-8")

    planned = _planned_commands(config)
    summary: dict[str, Any] = {
        "schema_version": "bifrost.two_instance_cache_share_demo.v1",
        "status": "planned",
        "mode": config.mode,
        "output_dir": str(config.output_dir),
        "bifrost_endpoint": config.bifrost_endpoint,
        "expected_ports": {
            "instance_a_vllm": config.instance_a_port,
            "instance_b_vllm": config.instance_b_port,
            "instance_a_lmcache": config.instance_a_lmcache_port,
            "instance_b_lmcache": config.instance_b_lmcache_port,
        },
        "workload_paths": {name: str(path) for name, path in workload_paths.items()},
        "output_paths": _output_paths(config),
        "commands": planned,
        "environment_readiness_path": str(doctor_path),
        "readiness": doctor.to_dict()["readiness"],
        "experiment_run_readiness": _experiment_run_readiness(
            doctor.to_dict(),
            allow_model_downloads=config.allow_model_downloads,
        ),
        "stage_results": [],
        "bifrost_stats": {},
        "comparison": _empty_comparison(),
        "notes": [
            "Optional two-instance scaffold only; skipped by default.",
            "Both serving instances use LMCache remote storage through BIFROST.",
            "No direct vLLM transfer integration is implemented or used.",
        ],
    }

    if config.mode == "readiness":
        summary["status"] = "readiness"
        _write_summary(config.output_dir, summary)
        return summary
    if config.mode == "dry-run":
        summary["status"] = "dry_run"
        _write_summary(config.output_dir, summary)
        return summary

    _require_opt_in(config)
    _require_not_ci()
    _require_experiment_readiness(
        doctor.to_dict(),
        allow_model_downloads=config.allow_model_downloads,
    )
    summary.update(_run_real_experiment(config, workload_paths))
    _write_summary(config.output_dir, summary)
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Optional two-instance BIFROST-backed LMCache cache-sharing experiment"
    )
    parser.add_argument("--mode", choices=sorted(MODES), default="dry-run")
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--model", default=os.environ.get("BIFROST_VLLM_MODEL"))
    parser.add_argument("--bifrost-endpoint", default="127.0.0.1:7420")
    parser.add_argument("--instance-a-port", type=int, default=8010)
    parser.add_argument("--instance-b-port", type=int, default=8011)
    parser.add_argument("--instance-a-lmcache-port", type=int, default=9010)
    parser.add_argument("--instance-b-lmcache-port", type=int, default=9011)
    parser.add_argument("--request-count", type=int, default=8)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--prefix-repeat-groups", type=int, default=2)
    parser.add_argument("--max-tokens", type=int, default=16)
    parser.add_argument("--workload", default="repeated_system_prompt")
    parser.add_argument("--allow-real-vllm", action="store_true")
    parser.add_argument("--readiness-timeout-seconds", type=float, default=120.0)
    parser.add_argument("--timeout-seconds", type=float, default=60.0)
    parser.add_argument("--json", action="store_true")
    try:
        args = parser.parse_args(argv)
        summary = run_demo(
            TwoInstanceConfig(
                mode=args.mode,
                output_dir=args.output_dir,
                model=args.model,
                bifrost_endpoint=args.bifrost_endpoint,
                instance_a_port=args.instance_a_port,
                instance_b_port=args.instance_b_port,
                instance_a_lmcache_port=args.instance_a_lmcache_port,
                instance_b_lmcache_port=args.instance_b_lmcache_port,
                request_count=args.request_count,
                concurrency=args.concurrency,
                prefix_repeat_groups=args.prefix_repeat_groups,
                max_tokens=args.max_tokens,
                workload_name=args.workload,
                allow_real_vllm=args.allow_real_vllm,
                allow_model_downloads=os.environ.get("BIFROST_ALLOW_MODEL_DOWNLOADS") == "1",
                readiness_timeout_seconds=args.readiness_timeout_seconds,
                timeout_seconds=args.timeout_seconds,
            )
        )
        if args.json:
            print(json.dumps(summary, indent=2, sort_keys=True))
        else:
            _print_human_summary(summary)
        return 0 if summary["status"] in {"dry_run", "readiness", "completed"} else 1
    except SystemExit:
        raise
    except Exception as exc:
        if "--json" in (argv or sys.argv[1:]):
            print(json.dumps({"status": "error", "error": str(exc)}, indent=2), file=sys.stderr)
        else:
            print(f"two-instance cache-sharing demo failed: {exc}", file=sys.stderr)
        return 2


def _run_real_experiment(
    config: TwoInstanceConfig,
    workload_paths: dict[str, Path],
) -> dict[str, Any]:
    collector = BifrostMetricsCollector(endpoint=config.bifrost_endpoint, timeout_seconds=5.0)
    stats_initial = collector.snapshot()
    a_processes = build_processes(_orchestrator_config(config, instance="a"))
    b_processes = [
        process
        for process in build_processes(_orchestrator_config(config, instance="b"))
        if process.name != "bifrost_daemon"
    ]
    daemon = [process for process in a_processes if process.name == "bifrost_daemon"]
    a_services = [process for process in a_processes if process.name != "bifrost_daemon"]
    started: list[Any] = []
    stage_results: list[dict[str, Any]] = []
    stats_after_a: dict[str, Any] | None = None
    stats_before_b: dict[str, Any] | None = None
    stats_after_b: dict[str, Any] | None = None
    try:
        for process in daemon:
            process.start()
            started.append(process)
            process.wait_ready(config.readiness_timeout_seconds)
        for process in a_services:
            process.start()
            started.append(process)
            process.wait_ready(config.readiness_timeout_seconds)
        stage_a = _run_stage(config, "instance_a_populate", config.instance_a_port, workload_paths["a"])
        stage_results.append(stage_a)
        for process in reversed(a_services):
            process.stop()
            if process in started:
                started.remove(process)
        stats_after_a = collector.snapshot()
        stats_before_b = collector.snapshot()
        for process in b_processes:
            process.start()
            started.append(process)
            process.wait_ready(config.readiness_timeout_seconds)
        stage_b = _run_stage(config, "instance_b_probe", config.instance_b_port, workload_paths["b"])
        stage_results.append(stage_b)
        stats_after_b = collector.snapshot()
    except Exception as exc:
        return {
            "status": "failed",
            "stage_results": stage_results,
            "error": str(exc),
            "bifrost_stats": {
                "initial": stats_initial,
                "after_instance_a": stats_after_a,
                "before_instance_b": stats_before_b,
                "after_instance_b": stats_after_b,
            },
            "comparison": _build_comparison(stage_results, stats_initial, stats_after_a, stats_before_b, stats_after_b),
        }
    finally:
        for process in reversed(started):
            process.stop()

    return {
        "status": "completed",
        "stage_results": stage_results,
        "bifrost_stats": {
            "initial": stats_initial,
            "after_instance_a": stats_after_a,
            "before_instance_b": stats_before_b,
            "after_instance_b": stats_after_b,
        },
        "comparison": _build_comparison(
            stage_results,
            stats_initial,
            stats_after_a,
            stats_before_b,
            stats_after_b,
        ),
    }


def _run_stage(
    config: TwoInstanceConfig,
    label: str,
    port: int,
    workload_path: Path,
) -> dict[str, Any]:
    result = run_serving_benchmark(
        ServingBenchmarkConfig(
            workload_jsonl=workload_path,
            base_url=f"http://127.0.0.1:{port}",
            backend="openai-compatible",
            concurrency=config.concurrency,
            timeout_seconds=config.timeout_seconds,
            output_dir=config.output_dir / "runs" / label,
            label=label,
            bifrost_endpoint=config.bifrost_endpoint,
            collect_bifrost_stats=True,
        )
    )
    return {
        "stage": label,
        "status": "completed" if result.summary["error_count"] == 0 else "failed",
        "output_dir": str(result.output_dir),
        "summary_path": str(result.summary_path),
        "summary": result.summary,
    }


def _write_workloads(config: TwoInstanceConfig) -> dict[str, Path]:
    workload_dir = config.output_dir / "workloads"
    workload_a = generate_workload(
        WorkloadConfig(
            workload_name=config.workload_name,
            request_count=config.request_count,
            prefix_repeat_groups=config.prefix_repeat_groups,
            max_tokens=config.max_tokens,
            seed=6061,
        )
    )
    workload_b = generate_workload(
        WorkloadConfig(
            workload_name=config.workload_name,
            request_count=config.request_count,
            prefix_repeat_groups=config.prefix_repeat_groups,
            max_tokens=config.max_tokens,
            seed=6061,
        )
    )
    path_a = workload_dir / "instance_a_populate.jsonl"
    path_b = workload_dir / "instance_b_probe.jsonl"
    write_workload(workload_a, out=path_a, summary_path=workload_dir / "instance_a_summary.json")
    write_workload(workload_b, out=path_b, summary_path=workload_dir / "instance_b_summary.json")
    return {"a": path_a, "b": path_b}


def _planned_commands(config: TwoInstanceConfig) -> dict[str, list[list[str]]]:
    commands: dict[str, list[list[str]]] = {}
    for instance in ("a", "b"):
        proc_config = _orchestrator_config(
            config,
            instance=instance,
            dry_run=True,
            allow_model_downloads=True,
        )
        commands[f"instance_{instance}"] = [
            process.command for process in _build_processes_for_planning(proc_config)
        ]
    return commands


def _build_processes_for_planning(proc_config: OrchestratorConfig) -> list[Any]:
    saved = {name: os.environ.get(name) for name in ("CI", "GITHUB_ACTIONS")}
    try:
        os.environ.pop("CI", None)
        os.environ.pop("GITHUB_ACTIONS", None)
        return build_processes(proc_config)
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def _orchestrator_config(
    config: TwoInstanceConfig,
    *,
    instance: str,
    dry_run: bool = False,
    allow_model_downloads: bool | None = None,
) -> OrchestratorConfig:
    if instance == "a":
        vllm_port = config.instance_a_port
        lmcache_port = config.instance_a_lmcache_port
        output_dir = config.output_dir / "services" / "instance_a"
    elif instance == "b":
        vllm_port = config.instance_b_port
        lmcache_port = config.instance_b_lmcache_port
        output_dir = config.output_dir / "services" / "instance_b"
    else:
        raise TwoInstanceDemoError(f"unknown instance: {instance}")
    return OrchestratorConfig(
        scenario="vllm-lmcache-bifrost",
        output_dir=output_dir,
        model=config.model or "./local-model",
        bifrost_endpoint=config.bifrost_endpoint,
        vllm_port=vllm_port,
        lmcache_port=lmcache_port,
        allow_real_vllm=True,
        allow_model_downloads=(
            config.allow_model_downloads if allow_model_downloads is None else allow_model_downloads
        ),
        dry_run=dry_run,
        readiness_timeout_seconds=config.readiness_timeout_seconds,
        env=os.environ.copy(),
    )


def _build_comparison(
    stage_results: list[dict[str, Any]],
    stats_initial: dict[str, Any] | None,
    stats_after_a: dict[str, Any] | None,
    stats_before_b: dict[str, Any] | None,
    stats_after_b: dict[str, Any] | None,
) -> dict[str, Any]:
    by_stage = {str(item.get("stage")): item for item in stage_results}
    a_summary = _summary_for(by_stage.get("instance_a_populate"))
    b_summary = _summary_for(by_stage.get("instance_b_probe"))
    after_a_delta = bifrost_snapshot_delta(stats_initial, stats_after_a)
    stage_b_delta = bifrost_snapshot_delta(stats_before_b, stats_after_b)
    get_activity = _first_number(stage_b_delta, "get_count", "gets", "bytes_get", "bytes_loaded")
    a_latency = _first_number(a_summary, "p50_latency_ms")
    b_latency = _first_number(b_summary, "p50_latency_ms")
    latency_delta = b_latency - a_latency if a_latency is not None and b_latency is not None else None
    status = "inconclusive"
    if get_activity is not None and get_activity > 0:
        status = "observed_reuse_activity"
    elif b_summary and b_summary.get("error_count") == 0:
        status = "no_bifrost_get_activity_observed"
    return {
        "status": status,
        "object_count_delta_after_instance_a": _first_number(after_a_delta, "object_count"),
        "bifrost_get_activity_during_instance_b": get_activity,
        "instance_a_p50_latency_ms": a_latency,
        "instance_b_p50_latency_ms": b_latency,
        "instance_b_minus_instance_a_p50_latency_ms": latency_delta,
        "stage_b_bifrost_delta": stage_b_delta,
        "interpretation": (
            "Positive BIFROST GET activity during instance B is evidence that LMCache attempted "
            "remote reuse. Latency differences are advisory unless the full real-serving report "
            "states matching hardware, model, workload, and runtime conditions."
        ),
    }


def _empty_comparison() -> dict[str, Any]:
    return {
        "status": "not_run",
        "object_count_delta_after_instance_a": None,
        "bifrost_get_activity_during_instance_b": None,
        "instance_a_p50_latency_ms": None,
        "instance_b_p50_latency_ms": None,
        "instance_b_minus_instance_a_p50_latency_ms": None,
    }


def _require_opt_in(config: TwoInstanceConfig) -> None:
    if config.allow_real_vllm or os.environ.get("BIFROST_RUN_REAL_VLLM") == "1":
        return
    raise TwoInstanceDemoError(
        "refusing run mode without --allow-real-vllm or BIFROST_RUN_REAL_VLLM=1"
    )


def _require_not_ci() -> None:
    if os.environ.get("CI") == "true" or os.environ.get("GITHUB_ACTIONS") == "true":
        raise TwoInstanceDemoError("refusing two-instance real vLLM demo run mode in CI")


def _require_experiment_readiness(
    report: dict[str, Any],
    *,
    allow_model_downloads: bool,
) -> None:
    level = _experiment_run_readiness(report, allow_model_downloads=allow_model_downloads)
    if level["status"] == "ready":
        return
    reasons = "; ".join(level.get("reasons") or ["two-instance readiness is not satisfied"])
    raise TwoInstanceDemoError(f"environment is not ready for two-instance real serving: {reasons}")


def _experiment_run_readiness(
    report: dict[str, Any],
    *,
    allow_model_downloads: bool,
) -> dict[str, Any]:
    checks = report["checks"]
    reasons: list[str] = []
    fixes: list[str] = []
    required = (
        "python",
        "torch",
        "lmcache",
        "lmcache_bifrost",
        "lmcache_bifrost_adapter",
        "lmcache_bifrost_config",
        "bifrostd_binary",
        "ports",
        "output_directory",
        "disk_space",
    )
    for name in required:
        check = checks[name]
        if check["status"] != "ready":
            reasons.append(check.get("reason") or f"{name} is {check['status']}.")
            if check.get("fix"):
                fixes.append(check["fix"])
    if checks["vllm"]["status"] != "ready" and checks["vllm_cli"]["status"] != "ready":
        reasons.append("Neither vLLM import nor vLLM CLI is available.")
        for name in ("vllm", "vllm_cli"):
            if checks[name].get("fix"):
                fixes.append(checks[name]["fix"])
    model = checks["model"]
    if model["status"] != "ready":
        if not (allow_model_downloads and model["status"] == "unknown"):
            reasons.append(model.get("reason") or f"model is {model['status']}.")
            if model.get("fix"):
                fixes.append(model["fix"])
    torch_details = checks["torch"].get("details") or {}
    if checks["torch"]["status"] == "ready" and not torch_details.get("cuda_available"):
        reasons.append("torch reports CUDA is unavailable.")
        fixes.append("Use a machine with a compatible GPU, driver, CUDA runtime, and torch build.")
    if checks["torch"]["status"] == "ready" and int(torch_details.get("cuda_device_count") or 0) < 1:
        reasons.append("No GPU devices are visible through torch.")
        fixes.append("Run real serving on a host with at least one visible GPU.")
    return {
        "status": "ready" if not reasons else "not_ready",
        "reasons": _dedupe(reasons),
        "recommended_fixes": _dedupe(fixes),
    }


def _output_paths(config: TwoInstanceConfig) -> dict[str, str]:
    return {
        "summary": str(config.output_dir / "two_instance_cache_share_summary.json"),
        "environment_readiness": str(config.output_dir / "environment_readiness.json"),
        "instance_a_run": str(config.output_dir / "runs" / "instance_a_populate"),
        "instance_b_run": str(config.output_dir / "runs" / "instance_b_probe"),
        "instance_a_services": str(config.output_dir / "services" / "instance_a"),
        "instance_b_services": str(config.output_dir / "services" / "instance_b"),
    }


def _summary_for(result: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(result, dict):
        return None
    summary = result.get("summary")
    return summary if isinstance(summary, dict) else None


def _first_number(mapping: dict[str, Any] | None, *keys: str) -> float | int | None:
    if not isinstance(mapping, dict):
        return None
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            return value
    return None


def _write_summary(output_dir: Path, summary: dict[str, Any]) -> None:
    (output_dir / "two_instance_cache_share_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _print_human_summary(summary: dict[str, Any]) -> None:
    lines = [
        f"status: {summary['status']}",
        f"BIFROST endpoint: {summary['bifrost_endpoint']}",
        f"expected ports: {json.dumps(summary['expected_ports'], sort_keys=True)}",
        "workload paths:",
    ]
    lines.extend(f"- {name}: {path}" for name, path in summary["workload_paths"].items())
    lines.append("output paths:")
    lines.extend(f"- {name}: {path}" for name, path in summary["output_paths"].items())
    lines.append("commands for instance A:")
    lines.extend(f"- {' '.join(command)}" for command in summary["commands"]["instance_a"])
    lines.append("commands for instance B:")
    lines.extend(f"- {' '.join(command)}" for command in summary["commands"]["instance_b"])
    lines.append(f"experiment run readiness: {summary['experiment_run_readiness']['status']}")
    for reason in summary["experiment_run_readiness"].get("reasons", []):
        lines.append(f"- {reason}")
    lines.append(f"comparison: {json.dumps(summary['comparison'], sort_keys=True)}")
    print("\n".join(lines))


def _validate_config(config: TwoInstanceConfig) -> None:
    if config.mode not in MODES:
        raise TwoInstanceDemoError(f"unsupported mode: {config.mode}")
    ports = (
        config.instance_a_port,
        config.instance_b_port,
        config.instance_a_lmcache_port,
        config.instance_b_lmcache_port,
    )
    if len(set(ports)) != len(ports):
        raise TwoInstanceDemoError("instance vLLM and LMCache ports must be distinct")
    if any(port <= 0 or port > 65535 for port in ports):
        raise TwoInstanceDemoError("ports must be in 1..65535")
    if config.request_count <= 0:
        raise TwoInstanceDemoError("request-count must be positive")
    if config.concurrency <= 0:
        raise TwoInstanceDemoError("concurrency must be positive")
    if config.prefix_repeat_groups <= 0:
        raise TwoInstanceDemoError("prefix-repeat-groups must be positive")
    if config.max_tokens <= 0:
        raise TwoInstanceDemoError("max-tokens must be positive")
    if config.mode == "run" and config.model is None and _opted_in(config):
        raise TwoInstanceDemoError("run mode requires --model or BIFROST_VLLM_MODEL")


def _opted_in(config: TwoInstanceConfig) -> bool:
    return config.allow_real_vllm or os.environ.get("BIFROST_RUN_REAL_VLLM") == "1"


def _dedupe(values: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


if __name__ == "__main__":
    raise SystemExit(main())

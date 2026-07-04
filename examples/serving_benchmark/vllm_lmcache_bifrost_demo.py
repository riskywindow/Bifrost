#!/usr/bin/env python3
"""Optional real vLLM + LMCache + BIFROST Phase 6 demo.

This script is guarded for local exploratory runs. Dry-run and readiness modes
do not start vLLM, LMCache, bifrostd, use GPU hardware, or download models.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
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

from bifrost_serving.compare import build_comparisons
from bifrost_serving.config_gen import ServingConfigRequest, generate_serving_config
from bifrost_serving.env_doctor import EnvDoctorConfig, run_doctor
from bifrost_serving.orchestrator import OrchestratorConfig, build_processes
from bifrost_serving.report import ServingReportConfig, generate_serving_report
from bifrost_serving.workloads import WorkloadConfig, generate_workload, write_workload

MODES = {"dry-run", "readiness", "run"}


@dataclass(frozen=True, slots=True)
class RealVLLMDemoConfig:
    mode: str
    output_dir: Path
    model: str | None
    bifrost_endpoint: str = "127.0.0.1:7420"
    vllm_port: int = 8000
    lmcache_port: int = 9000
    request_count: int = 8
    concurrency: int = 1
    prefix_repeat_groups: int = 2
    max_tokens: int = 16
    workload_name: str = "repeated_system_prompt"
    include_vllm_only_baseline: bool = False
    allow_real_vllm: bool = False
    allow_model_downloads: bool = False
    readiness_timeout_seconds: float = 120.0
    timeout_seconds: float = 60.0


class RealVLLMDemoError(RuntimeError):
    """Deterministic optional real serving demo failure."""


def run_demo(config: RealVLLMDemoConfig) -> dict[str, Any]:
    _validate_config(config)
    config.output_dir.mkdir(parents=True, exist_ok=True)

    workload_path = _write_workload(config)
    generated_config = generate_serving_config(
        ServingConfigRequest(
            endpoint=config.bifrost_endpoint,
            model=config.model or "./local-model",
            mode="lmcache-inprocess",
            output_dir=config.output_dir / "configs",
            port=config.vllm_port,
            lmcache_port=config.lmcache_port,
        )
    )
    doctor = run_doctor(
        EnvDoctorConfig(
            endpoint=config.bifrost_endpoint,
            model=config.model,
            output_dir=config.output_dir,
            required_ports=(config.vllm_port, config.lmcache_port),
        )
    )
    doctor_path = config.output_dir / "environment_readiness.json"
    doctor_path.write_text(doctor.to_json(indent=2) + "\n", encoding="utf-8")

    commands = _planned_commands(config)
    summary: dict[str, Any] = {
        "schema_version": "bifrost.real_vllm_lmcache_bifrost_demo.v1",
        "status": "planned",
        "mode": config.mode,
        "output_dir": str(config.output_dir),
        "workload_path": str(workload_path),
        "config_dir": str(generated_config.output_dir),
        "config_files": {key: str(path) for key, path in generated_config.files.items()},
        "environment_readiness_path": str(doctor_path),
        "readiness": doctor.to_dict()["readiness"],
        "demo_run_readiness": _demo_run_readiness(
            doctor.to_dict(),
            allow_model_downloads=config.allow_model_downloads,
        ),
        "commands": commands,
        "skipped_baselines": [],
        "report_path": None,
        "bifrost_object_count_delta": None,
        "latency_summary": {},
        "note": "Optional real serving demo only; no raw vLLM KVTransfer integration is used.",
    }

    if config.mode == "readiness":
        summary["status"] = "readiness"
        _write_summary(config.output_dir, summary)
        return summary
    if config.mode == "dry-run":
        summary["status"] = "dry_run"
        summary["skipped_baselines"] = _skipped_baselines(config)
        _write_summary(config.output_dir, summary)
        return summary

    _require_opt_in(config)
    _require_demo_readiness(doctor.to_dict(), allow_model_downloads=config.allow_model_downloads)
    run_summary = _run_real_stack(config, workload_path)
    summary.update(run_summary)
    _write_summary(config.output_dir, summary)
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Optional real vLLM + LMCache + BIFROST serving demo"
    )
    parser.add_argument("--mode", choices=sorted(MODES), default="dry-run")
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--model", default=os.environ.get("BIFROST_VLLM_MODEL"))
    parser.add_argument("--bifrost-endpoint", default="127.0.0.1:7420")
    parser.add_argument("--vllm-port", type=int, default=8000)
    parser.add_argument("--lmcache-port", type=int, default=9000)
    parser.add_argument("--request-count", type=int, default=8)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--prefix-repeat-groups", type=int, default=2)
    parser.add_argument("--max-tokens", type=int, default=16)
    parser.add_argument("--workload", default="repeated_system_prompt")
    parser.add_argument("--include-vllm-only-baseline", action="store_true")
    parser.add_argument("--allow-real-vllm", action="store_true")
    parser.add_argument("--readiness-timeout-seconds", type=float, default=120.0)
    parser.add_argument("--timeout-seconds", type=float, default=60.0)
    parser.add_argument("--json", action="store_true")
    try:
        args = parser.parse_args(argv)
        summary = run_demo(
            RealVLLMDemoConfig(
                mode=args.mode,
                output_dir=args.output_dir,
                model=args.model,
                bifrost_endpoint=args.bifrost_endpoint,
                vllm_port=args.vllm_port,
                lmcache_port=args.lmcache_port,
                request_count=args.request_count,
                concurrency=args.concurrency,
                prefix_repeat_groups=args.prefix_repeat_groups,
                max_tokens=args.max_tokens,
                workload_name=args.workload,
                include_vllm_only_baseline=args.include_vllm_only_baseline,
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
            print(f"real vLLM demo failed: {exc}", file=sys.stderr)
        return 2


def _run_real_stack(config: RealVLLMDemoConfig, workload_path: Path) -> dict[str, Any]:
    mode_results: list[dict[str, Any]] = []
    skipped_baselines = _skipped_baselines(config)
    if config.include_vllm_only_baseline:
        mode_results.append(_run_process_backed_mode(config, workload_path, "vllm-only", "vllm_only"))

    candidate = _run_process_backed_mode(
        config,
        workload_path,
        "vllm-lmcache-bifrost",
        "vllm_lmcache_bifrost",
    )
    mode_results.append(candidate)

    comparison_dir = config.output_dir / "comparison"
    comparison_dir.mkdir(parents=True, exist_ok=True)
    comparison_summary = {
        "schema_version": "bifrost.serving_baseline_comparison.v1",
        "started_unix_s": time.time(),
        "ended_unix_s": time.time(),
        "workload_jsonl": str(workload_path),
        "output_dir": str(comparison_dir),
        "modes": [result["mode"] for result in mode_results],
        "concurrency": config.concurrency,
        "request_rate": None,
        "bifrost_endpoint": config.bifrost_endpoint,
        "model": config.model,
        "allow_real_vllm": True,
        "mode_results": mode_results,
        "comparisons": build_comparisons(mode_results),
        "skipped_baselines": skipped_baselines,
        "notes": [
            "Real modes were started by the optional demo and compared from preserved run summaries.",
            "Skipped or failed modes are not speedup evidence.",
        ],
    }
    (comparison_dir / "comparison_summary.json").write_text(
        json.dumps(comparison_summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (comparison_dir / "comparison_summary.md").write_text(
        _render_comparison_markdown(comparison_summary),
        encoding="utf-8",
    )

    report = generate_serving_report(
        ServingReportConfig(
            run_dir=Path(str(candidate["output_dir"])),
            comparison_dir=comparison_dir,
            out=config.output_dir / "report",
            format="all",
        )
    )
    return {
        "status": "completed" if candidate["status"] == "completed" else "failed",
        "comparison_dir": str(comparison_dir),
        "mode_results": mode_results,
        "skipped_baselines": skipped_baselines,
        "report_path": str(report.report_path) if report.report_path else None,
        "bifrost_object_count_delta": _object_count_delta(candidate.get("summary")),
        "latency_summary": _latency_summary(mode_results),
    }


def _run_process_backed_mode(
    config: RealVLLMDemoConfig,
    workload_path: Path,
    scenario: str,
    mode: str,
) -> dict[str, Any]:
    from bifrost_serving.runner import ServingBenchmarkConfig, run_serving_benchmark

    run_dir = config.output_dir / "runs" / mode
    proc_config = OrchestratorConfig(
        scenario=scenario,
        output_dir=config.output_dir / "services" / mode,
        model=config.model,
        bifrost_endpoint=config.bifrost_endpoint,
        vllm_port=config.vllm_port,
        lmcache_port=config.lmcache_port,
        allow_real_vllm=True,
        allow_model_downloads=config.allow_model_downloads,
        readiness_timeout_seconds=config.readiness_timeout_seconds,
        env=os.environ.copy(),
    )
    processes = build_processes(proc_config)
    started = []
    try:
        for process in processes:
            process.start()
            started.append(process)
            process.wait_ready(config.readiness_timeout_seconds)
        result = run_serving_benchmark(
            ServingBenchmarkConfig(
                workload_jsonl=workload_path,
                base_url=f"http://127.0.0.1:{config.vllm_port}",
                backend="openai-compatible",
                concurrency=config.concurrency,
                timeout_seconds=config.timeout_seconds,
                output_dir=run_dir,
                label=mode,
                bifrost_endpoint=config.bifrost_endpoint,
                collect_bifrost_stats=mode == "vllm_lmcache_bifrost",
            )
        )
        return {
            "mode": mode,
            "status": "completed",
            "output_dir": str(run_dir),
            "summary_path": str(result.summary_path),
            "summary": result.summary,
            "processes": [process.status() for process in processes],
        }
    except Exception as exc:
        return {
            "mode": mode,
            "status": "failed",
            "error": str(exc),
            "output_dir": str(run_dir),
            "summary_path": None,
            "processes": [process.status() for process in processes],
        }
    finally:
        for process in reversed(started):
            process.stop()
        final_status = proc_config.output_dir / "orchestrator_final_status.json"
        final_status.parent.mkdir(parents=True, exist_ok=True)
        final_status.write_text(
            json.dumps(
                {
                    "schema_version": "bifrost.real_demo_process_final_status.v1",
                    "scenario": scenario,
                    "processes": [process.status() for process in processes],
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )


def _write_workload(config: RealVLLMDemoConfig) -> Path:
    workload = generate_workload(
        WorkloadConfig(
            workload_name=config.workload_name,
            request_count=config.request_count,
            prefix_repeat_groups=config.prefix_repeat_groups,
            max_tokens=config.max_tokens,
            seed=6060,
        )
    )
    workload_dir = config.output_dir / "workload"
    workload_path = workload_dir / "repeated_prefix_workload.jsonl"
    write_workload(workload, out=workload_path, summary_path=workload_dir / "summary.json")
    return workload_path


def _planned_commands(config: RealVLLMDemoConfig) -> dict[str, list[list[str]]]:
    commands: dict[str, list[list[str]]] = {}
    for scenario in ("vllm-only", "vllm-lmcache-bifrost"):
        if scenario == "vllm-only" and not config.include_vllm_only_baseline:
            continue
        proc_config = OrchestratorConfig(
            scenario=scenario,
            output_dir=config.output_dir / "services" / scenario,
            model=config.model or "./local-model",
            bifrost_endpoint=config.bifrost_endpoint,
            vllm_port=config.vllm_port,
            lmcache_port=config.lmcache_port,
            allow_real_vllm=True,
            allow_model_downloads=True,
            dry_run=True,
            env=os.environ.copy(),
        )
        commands[scenario] = [process.command for process in build_processes(proc_config)]
    return commands


def _require_opt_in(config: RealVLLMDemoConfig) -> None:
    if config.allow_real_vllm or os.environ.get("BIFROST_RUN_REAL_VLLM") == "1":
        return
    raise RealVLLMDemoError(
        "refusing run mode without --allow-real-vllm or BIFROST_RUN_REAL_VLLM=1"
    )


def _require_demo_readiness(report: dict[str, Any], *, allow_model_downloads: bool) -> None:
    level = _demo_run_readiness(report, allow_model_downloads=allow_model_downloads)
    if level["status"] == "ready":
        return
    reasons = "; ".join(level.get("reasons") or ["full benchmark readiness is not satisfied"])
    raise RealVLLMDemoError(f"environment is not ready for real serving: {reasons}")


def _demo_run_readiness(
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


def _dedupe(values: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _skipped_baselines(config: RealVLLMDemoConfig) -> list[dict[str, str]]:
    skipped = [
        {
            "mode": "vllm_lmcache_local_cpu",
            "reason": "LMCache local/CPU baseline is documented but not started by this single-stack demo.",
        }
    ]
    if not config.include_vllm_only_baseline:
        skipped.append(
            {
                "mode": "vllm_only",
                "reason": "vLLM-only baseline requires --include-vllm-only-baseline.",
            }
        )
    return skipped


def _object_count_delta(summary: Any) -> int | float | None:
    if not isinstance(summary, dict):
        return None
    delta = summary.get("bifrost_stats_delta")
    if not isinstance(delta, dict):
        return None
    value = delta.get("object_count") or delta.get("opaque_lmcache_object_count")
    return value if isinstance(value, (int, float)) else None


def _latency_summary(mode_results: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for result in mode_results:
        summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
        out[str(result["mode"])] = {
            "status": result.get("status"),
            "p50_latency_ms": summary.get("p50_latency_ms"),
            "p95_latency_ms": summary.get("p95_latency_ms"),
            "p50_ttft_ms": summary.get("p50_ttft_ms"),
            "throughput_rps": summary.get("throughput_rps"),
            "error_rate": summary.get("error_rate"),
        }
    return out


def _write_summary(output_dir: Path, summary: dict[str, Any]) -> None:
    (output_dir / "vllm_lmcache_bifrost_demo_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _render_comparison_markdown(summary: dict[str, Any]) -> str:
    lines = [
        "# BIFROST Phase 6 Real Demo Baseline Comparison",
        "",
        f"Workload: `{summary['workload_jsonl']}`",
        f"Concurrency: `{summary['concurrency']}`",
        "",
        "## Modes",
        "",
        "| Mode | Status | Summary | Notes |",
        "| --- | --- | --- | --- |",
    ]
    for result in summary["mode_results"]:
        note = result.get("skip_reason") or result.get("error") or ""
        lines.append(
            f"| `{result['mode']}` | `{result['status']}` | "
            f"`{result.get('summary_path') or ''}` | {note} |"
        )
    lines.extend(["", "## Skipped Baselines", ""])
    for item in summary["skipped_baselines"]:
        lines.append(f"- `{item['mode']}`: {item['reason']}")
    return "\n".join(lines) + "\n"


def _print_human_summary(summary: dict[str, Any]) -> None:
    full = summary["readiness"]["full_benchmark_ready"]
    demo = summary["demo_run_readiness"]
    lines = [
        f"status: {summary['status']}",
        f"environment readiness: full_benchmark_ready={full['status']}",
        f"demo run readiness: {demo['status']}",
        f"output directory: {summary['output_dir']}",
        f"workload: {summary['workload_path']}",
        f"config directory: {summary['config_dir']}",
    ]
    if full.get("reasons"):
        lines.append("readiness reasons:")
        lines.extend(f"- {reason}" for reason in full["reasons"])
    if demo.get("reasons"):
        lines.append("demo run readiness reasons:")
        lines.extend(f"- {reason}" for reason in demo["reasons"])
    lines.append("commands:")
    for scenario, commands in summary["commands"].items():
        lines.append(f"- {scenario}:")
        lines.extend(f"  {' '.join(command)}" for command in commands)
    lines.append(f"BIFROST object count delta: {summary['bifrost_object_count_delta']}")
    lines.append(f"latency summary: {json.dumps(summary['latency_summary'], sort_keys=True)}")
    if summary["skipped_baselines"]:
        lines.append("skipped baselines:")
        lines.extend(
            f"- {item['mode']}: {item['reason']}" for item in summary["skipped_baselines"]
        )
    lines.append(f"report path: {summary['report_path']}")
    print("\n".join(lines))


def _validate_config(config: RealVLLMDemoConfig) -> None:
    if config.mode not in MODES:
        raise RealVLLMDemoError(f"unsupported mode: {config.mode}")
    if config.request_count <= 0:
        raise RealVLLMDemoError("request-count must be positive")
    if config.concurrency <= 0:
        raise RealVLLMDemoError("concurrency must be positive")
    if config.prefix_repeat_groups <= 0:
        raise RealVLLMDemoError("prefix-repeat-groups must be positive")
    if config.max_tokens <= 0:
        raise RealVLLMDemoError("max-tokens must be positive")
    if config.mode == "run" and not config.model:
        raise RealVLLMDemoError("run mode requires --model or BIFROST_VLLM_MODEL")
    if os.environ.get("CI") == "true" or os.environ.get("GITHUB_ACTIONS") == "true":
        if config.mode == "run":
            raise RealVLLMDemoError("refusing real vLLM demo run mode in CI")


if __name__ == "__main__":
    raise SystemExit(main())

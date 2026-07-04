"""Guarded Phase 6 real-serving matrix executor.

This module is intentionally import-safe for CI: dry-run planning does not
import vLLM, LMCache, torch, or the BIFROST LMCache connector. Real execution is
guarded by an explicit opt-in and a preflight gate.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, Mapping, Sequence

from .artifacts import (
    capture_versions,
    redact_mapping,
    write_artifact_manifest,
    write_json_artifact,
)
from .baseline_matrix import (
    BaselineMatrix,
    BaselineMode,
    BaselineRunConfig,
    PRIMARY_BASELINE_MODES,
    build_bifrost_connector_config,
    build_comparison_manifest,
    build_vllm_command,
    generate_baseline_matrix_configs,
    render_lmcache_config_yaml,
)
from .collectors import LMCacheMetricsCollector, VLLMMetricsCollector
from .compare import build_comparisons
from .correctness import ResponseComparisonConfig, compare_run_outputs
from .env_doctor import EnvDoctorConfig, run_doctor
from .phases import DEFAULT_PHASE_ORDER, BenchmarkPhase, build_phase_plans, parse_phase_order
from .processes import ManagedProcess, http_ready_check, tcp_ready_check
from .request_schema import read_jsonl
from .runner import ServingBenchmarkConfig, run_serving_benchmark


class RealMatrixError(RuntimeError):
    """Base error for deterministic Phase 6 real matrix failures."""


class RealMatrixSafetyError(RealMatrixError):
    """Raised when a real run is requested without required safety gates."""


@dataclass(frozen=True, slots=True)
class RealMatrixConfig:
    workload_jsonl: Path
    output_dir: Path = Path("runs/phase6-real-matrix")
    model: str = "./local-model"
    served_model_name: str = "bifrost-phase6-model"
    dtype: str = "auto"
    max_model_len: int = 4096
    tensor_parallel_size: int = 1
    gpu_memory_utilization: float = 0.90
    gpu_id: str = "0"
    enable_chunked_prefill: bool = False
    enable_prefix_caching: bool = False
    output_len: int = 64
    sampling_settings: Mapping[str, Any] = field(
        default_factory=lambda: {"temperature": 0.0, "top_p": 1.0}
    )
    concurrency: int = 1
    request_rate: float = 1.0
    repetitions: int = 3
    rotate_mode_order: bool = False
    modes: tuple[BaselineMode, ...] = PRIMARY_BASELINE_MODES
    base_port: int = 8100
    port_stride: int = 20
    bifrost_base_port: int = 7744
    lmcache_connector_mode: str = "inprocess"
    lmcache_chunk_size: int = 256
    max_local_cpu_size: int = 8
    min_free_disk_bytes: int = 2 * 1024 * 1024 * 1024
    allow_real_vllm: bool = False
    allow_model_downloads: bool = False
    dry_run: bool = False
    timeout_seconds: float = 60.0
    readiness_timeout_seconds: float = 120.0
    engine_warmup_requests: int = 1
    population_requests_per_prefix: int = 1
    measured_requests_per_prefix: int | None = None
    phase_timeout_seconds: float | None = None
    phase_order: tuple[BenchmarkPhase, ...] = DEFAULT_PHASE_ORDER
    lmcache_metrics_url: str | None = None
    vllm_metrics_url_template: str = "http://127.0.0.1:{port}/metrics"
    serving_endpoint: str = "/v1/completions"
    cwd: Path | None = None


@dataclass(frozen=True, slots=True)
class RealMatrixResult:
    output_dir: Path
    status: str
    manifest_path: Path
    comparison_report_path: Path
    evidence_bundle_path: Path
    completion_gate_path: Path
    summary: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return dict(self.summary)


def run_real_matrix(config: RealMatrixConfig) -> RealMatrixResult:
    _validate_config(config)
    if not config.dry_run:
        _enforce_real_opt_in(config)

    started = time.time()
    config.output_dir.mkdir(parents=True, exist_ok=True)
    workload_digest = _workload_digest(config.workload_jsonl)
    requested_modes = tuple(_mode(mode) for mode in config.modes)
    plan = _execution_plan(config, requested_modes)
    root_preflight = preflight_real_matrix(config, plan) if not config.dry_run else _dry_preflight(config, plan)
    write_json_artifact(config.output_dir / "preflight.json", root_preflight)

    mode_results: list[dict[str, Any]] = []
    if not config.dry_run and root_preflight["status"] != "ready":
        for item in plan:
            mode_results.append(_skipped_result(item, "preflight failed"))
    else:
        for item in plan:
            mode_results.append(_run_plan_item(config, item, workload_digest))

    comparisons = build_comparisons(mode_results)
    correctness = _run_correctness(mode_results)
    completion_gate = evaluate_completion_gate(
        mode_results=mode_results,
        requested_modes=requested_modes,
        repetitions=config.repetitions,
        dry_run=config.dry_run,
        correctness=correctness,
    )
    ended = time.time()
    status = str(completion_gate["status"])
    summary = {
        "schema_version": "bifrost.phase6_real_matrix.v1",
        "status": status,
        "dry_run": config.dry_run,
        "started_unix_s": started,
        "ended_unix_s": ended,
        "duration_s": ended - started,
        "output_dir": str(config.output_dir),
        "workload": workload_digest,
        "requested_modes": [mode.value for mode in requested_modes],
        "repetitions": config.repetitions,
        "rotate_mode_order": config.rotate_mode_order,
        "preflight": root_preflight,
        "mode_results": mode_results,
        "comparisons": comparisons,
        "correctness": correctness,
        "completion_gate": completion_gate,
    }

    manifest_path = config.output_dir / "artifact_manifest.json"
    comparison_path = config.output_dir / "comparison_report.json"
    comparison_csv_path = config.output_dir / "comparison.csv"
    evidence_path = config.output_dir / "sanitized_evidence_bundle.json"
    gate_path = config.output_dir / "completion_gate.json"
    report_path = config.output_dir / "report.md"
    write_json_artifact(comparison_path, {"comparisons": comparisons, "mode_results": mode_results})
    _write_matrix_comparison_csv(comparison_csv_path, summary)
    write_json_artifact(evidence_path, _sanitized_evidence(summary))
    write_json_artifact(gate_path, completion_gate)
    write_json_artifact(config.output_dir / "summary.json", summary)
    _write_matrix_markdown_report(report_path, summary)
    root_manifest = _root_manifest(
        config.output_dir,
        mode_results,
        comparison_path,
        comparison_csv_path,
        evidence_path,
        gate_path,
        report_path,
    )
    write_json_artifact(manifest_path, root_manifest)
    return RealMatrixResult(
        output_dir=config.output_dir,
        status=status,
        manifest_path=manifest_path,
        comparison_report_path=comparison_path,
        evidence_bundle_path=evidence_path,
        completion_gate_path=gate_path,
        summary=summary,
    )


def preflight_real_matrix(config: RealMatrixConfig, plan: Sequence[dict[str, Any]]) -> dict[str, Any]:
    ports = sorted({int(item["vllm_port"]) for item in plan} | {int(item["bifrost_port"]) for item in plan})
    report = run_doctor(
        EnvDoctorConfig(
            endpoint=f"127.0.0.1:{config.bifrost_base_port}",
            model=config.model,
            output_dir=config.output_dir,
            min_free_disk_bytes=config.min_free_disk_bytes,
            required_ports=tuple(ports),
        )
    ).to_dict()
    failures = _preflight_failures(config, report)
    return {
        "schema_version": "bifrost.phase6_real_matrix_preflight.v1",
        "status": "ready" if not failures else "not_ready",
        "failures": failures,
        "doctor": report,
        "required_ports": ports,
    }


def evaluate_completion_gate(
    *,
    mode_results: Sequence[Mapping[str, Any]],
    requested_modes: Sequence[BaselineMode],
    repetitions: int,
    dry_run: bool,
    correctness: Mapping[str, Any],
) -> dict[str, Any]:
    failures: list[str] = []
    expected = repetitions * len(requested_modes)
    if dry_run:
        failures.append("dry-run does not execute real serving")
    if len(mode_results) != expected:
        failures.append(f"expected {expected} mode results, found {len(mode_results)}")
    if correctness.get("status") == "skipped":
        failures.append("correctness checking did not execute")
    elif correctness.get("status") == "fail":
        failures.append("correctness checking failed")
    for result in mode_results:
        label = f"rep{result.get('repetition')}:{result.get('mode')}"
        if result.get("status") != "completed":
            failures.append(f"{label} did not complete: {result.get('skip_reason') or result.get('error')}")
            continue
        summary = result.get("summary") if isinstance(result.get("summary"), Mapping) else {}
        measured = _measured_section(summary)
        if int(measured.get("request_count") or 0) <= 0:
            failures.append(f"{label} has zero measured samples")
        artifacts = result.get("artifact_manifest")
        if isinstance(artifacts, Mapping) and artifacts.get("missing_required_artifacts"):
            failures.append(f"{label} is missing required artifacts")
        mode = str(result.get("mode"))
        if mode == BaselineMode.VLLM_LMCACHE_LOCAL_CPU.value and not _lmcache_activity(result):
            failures.append(f"{label} did not report LMCache store and retrieve activity")
        if mode == BaselineMode.VLLM_LMCACHE_BIFROST.value:
            if not _bifrost_activity(result):
                failures.append(f"{label} did not report BIFROST connector PUT and GET activity")
            if not _bifrost_store_activity(result):
                failures.append(f"{label} did not report BIFROST store object or byte activity")
            if not _fsck_clean(result):
                failures.append(f"{label} BIFROST fsck was not clean")
    return {
        "schema_version": "bifrost.phase6_real_matrix_completion_gate.v1",
        "status": "pass" if not failures else ("dry_run" if dry_run else "failed"),
        "passed": not failures,
        "failure_count": len(failures),
        "failures": failures,
    }


def mode_order_for_repetition(
    modes: Sequence[BaselineMode],
    repetition: int,
    *,
    rotate: bool,
) -> tuple[BaselineMode, ...]:
    normalized = tuple(_mode(mode) for mode in modes)
    if not rotate or not normalized:
        return normalized
    offset = repetition % len(normalized)
    return normalized[offset:] + normalized[:offset]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run guarded Phase 6 real-serving matrix")
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--workload-jsonl", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--served-model-name", default=None)
    parser.add_argument("--dtype", default=None)
    parser.add_argument("--max-model-len", type=int, default=None)
    parser.add_argument("--gpu-id", default=None)
    parser.add_argument("--base-port", type=int, default=None)
    parser.add_argument("--bifrost-base-port", type=int, default=None)
    parser.add_argument("--concurrency", type=int, default=None)
    parser.add_argument("--request-rate", type=float, default=None)
    parser.add_argument("--repetitions", type=int, default=None)
    parser.add_argument("--rotate-mode-order", action="store_true")
    parser.add_argument("--allow-real-vllm", action="store_true")
    parser.add_argument("--allow-model-downloads", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    try:
        args = parser.parse_args(argv)
        config = config_from_args(args)
        result = run_real_matrix(config)
        if args.json:
            print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
        else:
            print(f"wrote {result.manifest_path} with status {result.status}")
        return 0 if result.summary["completion_gate"]["passed"] else 1
    except SystemExit:
        raise
    except Exception as exc:
        if argv is not None and "--json" in argv:
            print(json.dumps({"status": "error", "error": str(exc)}, indent=2), file=sys.stderr)
        else:
            print(f"bifrost real matrix failed: {exc}", file=sys.stderr)
        return 2


def config_from_args(args: argparse.Namespace) -> RealMatrixConfig:
    data = _load_config(args.config) if args.config else {}
    updates = {
        "workload_jsonl": args.workload_jsonl,
        "output_dir": args.output_dir,
        "model": args.model,
        "served_model_name": args.served_model_name,
        "dtype": args.dtype,
        "max_model_len": args.max_model_len,
        "gpu_id": args.gpu_id,
        "base_port": args.base_port,
        "bifrost_base_port": args.bifrost_base_port,
        "concurrency": args.concurrency,
        "request_rate": args.request_rate,
        "repetitions": args.repetitions,
    }
    for key, value in updates.items():
        if value is not None:
            data[key] = value
    if args.rotate_mode_order:
        data["rotate_mode_order"] = True
    if args.allow_real_vllm:
        data["allow_real_vllm"] = True
    if args.allow_model_downloads:
        data["allow_model_downloads"] = True
    if args.dry_run:
        data["dry_run"] = True
    if "workload_jsonl" not in data:
        raise RealMatrixError("--workload-jsonl or config workload_jsonl is required")
    if "output_dir" in data:
        data["output_dir"] = Path(str(data["output_dir"]))
    data["workload_jsonl"] = Path(str(data["workload_jsonl"]))
    if "phase_order" in data and not isinstance(data["phase_order"], tuple):
        data["phase_order"] = parse_phase_order(data["phase_order"])
    if "modes" in data:
        data["modes"] = tuple(_mode(item) for item in data["modes"])
    allowed = {item.name for item in fields(RealMatrixConfig)}
    data = {key: value for key, value in data.items() if key in allowed}
    return RealMatrixConfig(**data)


def _run_plan_item(
    config: RealMatrixConfig,
    item: Mapping[str, Any],
    workload_digest: Mapping[str, Any],
) -> dict[str, Any]:
    mode = _mode(item["mode"])
    mode_dir = Path(str(item["mode_dir"]))
    mode_dir.mkdir(parents=True, exist_ok=True)
    run = _run_config_for_item(config, item)
    _write_mode_artifacts(config, run, mode_dir, workload_digest, dry_run=config.dry_run)
    if config.dry_run:
        result = {
            "mode": mode.value,
            "repetition": item["repetition"],
            "status": "planned",
            "skip_reason": "dry-run",
            "output_dir": str(mode_dir),
            "summary_path": None,
            "summary": None,
        }
        result["artifact_manifest"] = write_artifact_manifest(mode_dir)
        write_json_artifact(mode_dir / "mode_result.json", result)
        return result

    processes = _processes_for_run(config, run, mode_dir)
    started: list[ManagedProcess] = []
    cleanup_errors: list[str] = []
    try:
        for process in processes:
            process.start()
            started.append(process)
            process.wait_ready(config.readiness_timeout_seconds)
        before = _collect_external_metrics(config, run)
        write_json_artifact(mode_dir / "metrics_before.json", before)
        benchmark = run_serving_benchmark(
            ServingBenchmarkConfig(
                workload_jsonl=config.workload_jsonl,
                base_url=f"http://127.0.0.1:{run.port}",
                endpoint=config.serving_endpoint,
                model=run.served_model_name,
                backend="openai-compatible",
                concurrency=run.concurrency,
                request_rate=run.request_rate,
                timeout_seconds=config.timeout_seconds,
                output_dir=mode_dir,
                label=mode.value,
                bifrost_endpoint=run.bifrost_endpoint,
                collect_bifrost_stats=run.bifrost_enabled,
                collect_bifrost_fsck=run.bifrost_enabled,
                bifrost_fsck_command=_fsck_command(run.bifrost_endpoint) if run.bifrost_enabled else (),
                connector_metrics_jsonl_path=run.connector_metrics_path,
                engine_warmup_requests=config.engine_warmup_requests,
                population_requests_per_prefix=config.population_requests_per_prefix,
                measured_requests_per_prefix=config.measured_requests_per_prefix,
                phase_timeout_seconds=config.phase_timeout_seconds,
                phase_order=config.phase_order,
            )
        )
        after_population = _collect_external_metrics(config, run)
        after_measured = _collect_external_metrics(config, run)
        write_json_artifact(mode_dir / "metrics_after_population.json", after_population)
        write_json_artifact(mode_dir / "metrics_after_measured.json", after_measured)
        summary = dict(benchmark.summary)
        summary["lmcache_activity"] = _activity_delta(before.get("lmcache"), after_measured.get("lmcache"))
        summary["lmcache_log_activity"] = _lmcache_log_activity(mode_dir / "stdout.log")
        summary["vllm_metrics"] = {"before": before.get("vllm"), "after": after_measured.get("vllm")}
        write_json_artifact(mode_dir / "summary.json", summary)
        result = _completed_result(mode.value, int(item["repetition"]), mode_dir, summary)
    except Exception as exc:
        result = _failed_result(mode.value, int(item["repetition"]), mode_dir, exc)
    finally:
        for process in reversed(started):
            try:
                process.stop(timeout=60.0)
            except Exception as exc:  # pragma: no cover - defensive cleanup path.
                cleanup_errors.append(f"{process.name}: {type(exc).__name__}: {exc}")
        _ensure_log_placeholders(mode_dir)
    if cleanup_errors:
        result["cleanup_errors"] = cleanup_errors
        summary = result.get("summary")
        if isinstance(summary, dict):
            summary["cleanup_errors"] = cleanup_errors
    result["processes"] = [process.status() for process in processes]
    result["artifact_manifest"] = write_artifact_manifest(mode_dir)
    write_json_artifact(mode_dir / "mode_result.json", result)
    return result


def _write_mode_artifacts(
    config: RealMatrixConfig,
    run: BaselineRunConfig,
    mode_dir: Path,
    workload_digest: Mapping[str, Any],
    *,
    dry_run: bool,
) -> None:
    generated = generate_baseline_matrix_configs(
        _single_repetition_matrix(
            config,
            run.port - PRIMARY_BASELINE_MODES.index(run.mode),
            _endpoint_for_port(run.bifrost_endpoint),
            mode_dir / "generated_configs",
        ),
        dry_run=False,
    )
    vllm_command = build_vllm_command(run, mode_dir / "generated_lmcache_config.yaml" if run.lmcache_enabled else None)
    write_json_artifact(mode_dir / "generated_vllm_command.json", vllm_command)
    if run.lmcache_enabled:
        (mode_dir / "generated_lmcache_config.yaml").write_text(
            render_lmcache_config_yaml(run),
            encoding="utf-8",
        )
    if run.bifrost_enabled:
        write_json_artifact(mode_dir / "generated_bifrost_connector_config.json", build_bifrost_connector_config(run))
    shutil.copyfile(config.workload_jsonl, mode_dir / "workload.jsonl")
    phase_plan = build_phase_plans(
        read_jsonl(config.workload_jsonl),
        engine_warmup_requests=config.engine_warmup_requests,
        population_requests_per_prefix=config.population_requests_per_prefix,
        measured_requests_per_prefix=config.measured_requests_per_prefix,
        phase_timeout_seconds=config.phase_timeout_seconds,
        phase_order=config.phase_order,
    )
    write_json_artifact(
        mode_dir / "phase_plan.json",
        {
            "phases": [
                {"phase": plan.phase.value, "request_count": plan.request_count}
                for plan in phase_plan
            ]
        },
    )
    (mode_dir / "resolved_run_config.yaml").write_text(_simple_yaml(_resolved_run_config(config, run, workload_digest)), encoding="utf-8")
    write_json_artifact(mode_dir / "environment_doctor.json", _dry_preflight(config, []))
    write_json_artifact(mode_dir / "versions.json", capture_versions(model=run.model, workload_path=config.workload_jsonl, env=os.environ))
    write_json_artifact(
        mode_dir / "command_manifest.json",
        {
            "schema_version": "bifrost.phase6_real_matrix_command_manifest.v1",
            "dry_run": dry_run,
            "mode": run.mode.value,
            "generated_bundle": {key: str(path) for key, path in generated.files.items()},
            "vllm_command": vllm_command,
            "gpu": {"CUDA_VISIBLE_DEVICES": config.gpu_id},
        },
    )
    for name in ("metrics_before.json", "metrics_after_population.json", "metrics_after_measured.json"):
        path = mode_dir / name
        if not path.exists():
            write_json_artifact(path, {"status": "skipped", "reason": "dry-run" if dry_run else "not collected"})
    for name in ("raw_requests.jsonl", "stdout.log", "stderr.log"):
        path = mode_dir / name
        if not path.exists():
            path.write_text("", encoding="utf-8")


def _processes_for_run(
    config: RealMatrixConfig,
    run: BaselineRunConfig,
    mode_dir: Path,
) -> list[ManagedProcess]:
    repo_root = Path(__file__).resolve().parents[2]
    cwd = config.cwd or repo_root
    env = {
        "CUDA_VISIBLE_DEVICES": config.gpu_id,
        "BIFROST_PHASE6_BASELINE_MODE": run.mode.value,
        "BIFROST_VLLM_MODEL": run.model,
        "BIFROST_VLLM_PORT": str(run.port),
    }
    if run.lmcache_enabled:
        env["LMCACHE_CONFIG_FILE"] = str(mode_dir / "generated_lmcache_config.yaml")
        env["BIFROST_LMCACHE_CONNECTOR_MODE"] = str(run.lmcache_connector_mode)
    if run.bifrost_endpoint:
        env["BIFROST_ENDPOINT"] = run.bifrost_endpoint
    processes: list[ManagedProcess] = []
    if run.bifrost_enabled:
        host, port = _split_endpoint(str(run.bifrost_endpoint))
        processes.append(
            ManagedProcess(
                name="bifrost_daemon",
                command=[
                    _bifrost_daemon_command(repo_root),
                    "--listen",
                    str(run.bifrost_endpoint),
                    "--spool",
                    str(mode_dir / "bifrost_store"),
                    "--trace-jsonl",
                    str(mode_dir / "bifrost_daemon_trace.jsonl"),
                ],
                env=env,
                cwd=cwd,
                log_path=mode_dir / "stdout.log",
                ready_check=tcp_ready_check(host, port),
            )
        )
    vllm_command_artifact = build_vllm_command(
        run, mode_dir / "generated_lmcache_config.yaml" if run.lmcache_enabled else None
    )
    vllm_env = dict(env)
    vllm_env.update({str(key): str(value) for key, value in vllm_command_artifact.get("env", {}).items()})
    processes.append(
        ManagedProcess(
            name="vllm_server",
            command=[str(item) for item in vllm_command_artifact["command"]],
            env=vllm_env,
            cwd=cwd,
            log_path=mode_dir / "stdout.log",
            ready_check=http_ready_check(f"http://127.0.0.1:{run.port}/health"),
        )
    )
    return processes


def _execution_plan(config: RealMatrixConfig, modes: Sequence[BaselineMode]) -> list[dict[str, Any]]:
    plan: list[dict[str, Any]] = []
    for repetition in range(config.repetitions):
        for index, mode in enumerate(mode_order_for_repetition(modes, repetition, rotate=config.rotate_mode_order)):
            vllm_port = config.base_port + repetition * config.port_stride + index
            bifrost_port = config.bifrost_base_port + repetition * config.port_stride + index
            plan.append(
                {
                    "repetition": repetition,
                    "mode": mode.value,
                    "vllm_port": vllm_port,
                    "bifrost_port": bifrost_port,
                    "mode_dir": str(config.output_dir / f"rep_{repetition:02d}" / mode.value),
                }
            )
    return plan


def _run_config_for_item(config: RealMatrixConfig, item: Mapping[str, Any]) -> BaselineRunConfig:
    matrix = _single_repetition_matrix(
        config,
        _matrix_base_port_for_item(item),
        f"127.0.0.1:{int(item['bifrost_port'])}",
        Path(str(item["mode_dir"])) / "generated_configs",
    )
    return matrix.by_mode()[_mode(item["mode"])]


def _single_repetition_matrix(
    config: RealMatrixConfig,
    base_port: int,
    bifrost_endpoint: str,
    output_dir: Path,
) -> BaselineMatrix:
    return BaselineMatrix.primary_isolation(
        model=config.model,
        served_model_name=config.served_model_name,
        dtype=config.dtype,
        max_model_len=config.max_model_len,
        tensor_parallel_size=config.tensor_parallel_size,
        gpu_memory_utilization=config.gpu_memory_utilization,
        enable_chunked_prefill=config.enable_chunked_prefill,
        enable_prefix_caching=config.enable_prefix_caching,
        output_len=config.output_len,
        sampling_settings=dict(config.sampling_settings),
        workload_path=config.workload_jsonl,
        concurrency=config.concurrency,
        request_rate=config.request_rate,
        output_dir=output_dir,
        base_port=base_port,
        bifrost_endpoint=bifrost_endpoint,
        lmcache_connector_mode=config.lmcache_connector_mode,
        lmcache_chunk_size=config.lmcache_chunk_size,
        max_local_cpu_size=config.max_local_cpu_size,
    )


def _preflight_failures(config: RealMatrixConfig, report: Mapping[str, Any]) -> list[str]:
    checks = report.get("checks") if isinstance(report.get("checks"), Mapping) else {}
    failures: list[str] = []
    torch = _check(checks, "torch")
    torch_details = torch.get("details") if isinstance(torch.get("details"), Mapping) else {}
    if torch.get("status") != "ready" or not torch_details.get("cuda_available") or int(torch_details.get("cuda_device_count") or 0) <= 0:
        failures.append("GPU is not visible through torch.cuda")
    for name in (
        "vllm",
        "lmcache",
        "lmcache_bifrost",
        "lmcache_bifrost_adapter",
        "vllm_kv_transfer",
        "bifrostd_binary",
        "disk_space",
        "ports",
    ):
        if _check(checks, name).get("status") != "ready":
            failures.append(f"{name} is not ready")
    model = _check(checks, "model")
    if model.get("status") != "ready" and not config.allow_model_downloads:
        failures.append("model is not available locally and downloads are not allowed")
    return failures


def _dry_preflight(config: RealMatrixConfig, plan: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": "bifrost.phase6_real_matrix_preflight.v1",
        "status": "dry_run",
        "failures": [],
        "required_ports": sorted({int(item["vllm_port"]) for item in plan} | {int(item["bifrost_port"]) for item in plan}) if plan else [],
        "doctor": {"status": "skipped", "reason": "dry-run"},
    }


def _run_correctness(mode_results: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    completed = [result for result in mode_results if result.get("status") == "completed"]
    if len(completed) < 2:
        return {
            "schema_version": "bifrost.phase6_real_matrix_correctness.v1",
            "status": "skipped",
            "reason": "fewer than two completed modes",
        }
    reference = _read_raw(Path(str(completed[0]["output_dir"])) / "raw_requests.jsonl")
    comparisons = []
    status = "pass"
    for result in completed[1:]:
        candidate = _read_raw(Path(str(result["output_dir"])) / "raw_requests.jsonl")
        comparison = compare_run_outputs(
            reference,
            candidate,
            ResponseComparisonConfig(mode="token_count_only", reason="Phase 6 real matrix deterministic token-count check"),
        ).to_dict()
        comparisons.append({"mode": result.get("mode"), "repetition": result.get("repetition"), "comparison": comparison})
        if comparison["status"] == "fail":
            status = "fail"
    return {
        "schema_version": "bifrost.phase6_real_matrix_correctness.v1",
        "status": status,
        "mode": "token_count_only",
        "comparisons": comparisons,
    }


def _completed_result(mode: str, repetition: int, mode_dir: Path, summary: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "mode": mode,
        "repetition": repetition,
        "status": "completed",
        "skip_reason": None,
        "output_dir": str(mode_dir),
        "summary_path": str(mode_dir / "summary.json"),
        "summary": dict(summary),
        "artifacts": {
            "summary": str(mode_dir / "summary.json"),
            "raw_requests": str(mode_dir / "raw_requests.jsonl"),
            "config": str(mode_dir / "config.json"),
            "workload": str(mode_dir / "workload.jsonl"),
        },
    }


def _failed_result(mode: str, repetition: int, mode_dir: Path, exc: Exception) -> dict[str, Any]:
    return {
        "mode": mode,
        "repetition": repetition,
        "status": "failed",
        "skip_reason": None,
        "error": str(exc),
        "output_dir": str(mode_dir),
        "summary_path": None,
        "summary": None,
        "artifacts": {},
    }


def _skipped_result(item: Mapping[str, Any], reason: str) -> dict[str, Any]:
    mode_dir = Path(str(item["mode_dir"]))
    mode_dir.mkdir(parents=True, exist_ok=True)
    result = {
        "mode": str(item["mode"]),
        "repetition": int(item["repetition"]),
        "status": "skipped",
        "skip_reason": reason,
        "output_dir": str(mode_dir),
        "summary_path": None,
        "summary": None,
        "artifacts": {},
    }
    write_json_artifact(mode_dir / "mode_result.json", result)
    return result


def _collect_external_metrics(config: RealMatrixConfig, run: BaselineRunConfig) -> dict[str, Any]:
    vllm_url = config.vllm_metrics_url_template.format(port=run.port)
    return {
        "schema_version": "bifrost.phase6_real_matrix_external_metrics.v1",
        "vllm": VLLMMetricsCollector(metrics_url=vllm_url, timeout_seconds=2.0).snapshot(),
        "lmcache": LMCacheMetricsCollector(metrics_url=config.lmcache_metrics_url, timeout_seconds=2.0).snapshot(),
    }


def _activity_delta(before: Any, after: Any) -> dict[str, Any]:
    before_metrics = before.get("metrics") if isinstance(before, Mapping) else {}
    after_metrics = after.get("metrics") if isinstance(after, Mapping) else {}
    return {
        "store_activity": _positive_metric_delta(before_metrics, after_metrics, ("store", "put")),
        "retrieve_activity": _positive_metric_delta(before_metrics, after_metrics, ("retrieve", "get", "hit")),
        "before": before,
        "after": after,
    }


def _lmcache_activity(result: Mapping[str, Any]) -> bool:
    summary = result.get("summary") if isinstance(result.get("summary"), Mapping) else {}
    activity = summary.get("lmcache_activity") if isinstance(summary.get("lmcache_activity"), Mapping) else {}
    if activity.get("store_activity") and activity.get("retrieve_activity"):
        return True
    log_activity = summary.get("lmcache_log_activity") if isinstance(summary.get("lmcache_log_activity"), Mapping) else {}
    return bool(log_activity.get("store_activity") and log_activity.get("retrieve_activity"))


def _lmcache_log_activity(log_path: Path) -> dict[str, Any]:
    if not log_path.exists():
        return {
            "schema_version": "bifrost.phase6_lmcache_log_activity.v1",
            "status": "unavailable",
            "reason": "stdout log does not exist",
            "store_activity": False,
            "retrieve_activity": False,
            "store_event_count": 0,
            "retrieve_event_count": 0,
        }
    text = log_path.read_text(encoding="utf-8", errors="replace")
    store_events = re.findall(
        r"(?:Stored|stored|Storing|store) [^.\n]*(?:tokens|chunk|chunks|cache|KV)",
        text,
    )
    retrieve_events = re.findall(
        r"(?:LMCache hit tokens:\s*[1-9]\d*|Retrieved\s+[1-9]\d*\s+out of\s+[1-9]\d*)",
        text,
    )
    return {
        "schema_version": "bifrost.phase6_lmcache_log_activity.v1",
        "status": "ok",
        "source": "lmcache_stdout_log",
        "path": str(log_path),
        "store_activity": bool(store_events),
        "retrieve_activity": bool(retrieve_events),
        "store_event_count": len(store_events),
        "retrieve_event_count": len(retrieve_events),
    }


def _bifrost_activity(result: Mapping[str, Any]) -> bool:
    summary = result.get("summary") if isinstance(result.get("summary"), Mapping) else {}
    for stats in _bifrost_connector_stat_maps(summary):
        if _connector_put_get_activity(stats):
            return True
    return False


def _bifrost_store_activity(result: Mapping[str, Any]) -> bool:
    summary = result.get("summary") if isinstance(result.get("summary"), Mapping) else {}
    store = _store_counts(summary)
    return bool(
        store["object_delta"] > 0
        or store["bytes_delta"] > 0
        or store["verified_delta"] > 0
        or store["access_delta"] > 0
    )


def _bifrost_connector_stat_maps(summary: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    candidates: list[Mapping[str, Any]] = []
    for source_key in ("connector_metrics_delta", "bifrost_stats_delta"):
        delta = summary.get(source_key)
        if isinstance(delta, Mapping):
            nested = delta.get("connector_metrics_delta")
            if isinstance(nested, Mapping):
                candidates.append(nested)
            candidates.append(delta)
    bifrost_stats = summary.get("bifrost_stats")
    if isinstance(bifrost_stats, Mapping):
        for snapshot_name in ("before", "after"):
            _append_connector_stats(candidates, bifrost_stats.get(snapshot_name))
    phase_sections = summary.get("phase_sections")
    if isinstance(phase_sections, Mapping):
        for section in phase_sections.values():
            if not isinstance(section, Mapping):
                continue
            phase_bifrost = section.get("bifrost_stats")
            if not isinstance(phase_bifrost, Mapping):
                continue
            phase_delta = phase_bifrost.get("delta")
            if isinstance(phase_delta, Mapping):
                nested = phase_delta.get("connector_metrics_delta")
                if isinstance(nested, Mapping):
                    candidates.append(nested)
                candidates.append(phase_delta)
            for snapshot_name in ("before", "after"):
                _append_connector_stats(candidates, phase_bifrost.get(snapshot_name))
    return candidates


def _append_connector_stats(candidates: list[Mapping[str, Any]], snapshot: Any) -> None:
    if not isinstance(snapshot, Mapping):
        return
    connector = snapshot.get("connector_metrics")
    if not isinstance(connector, Mapping):
        return
    stats = connector.get("stats")
    if isinstance(stats, Mapping):
        candidates.append(stats)


def _connector_put_get_activity(stats: Mapping[str, Any]) -> bool:
    put = _first_positive(stats, ("put_count", "connector_put_completed", "put_completed"))
    get = _first_positive(stats, ("get_count", "connector_get_completed", "get_completed"))
    return put > 0 and get > 0


def _first_positive(stats: Mapping[str, Any], keys: Sequence[str]) -> float:
    for key in keys:
        value = stats.get(key)
        if _positive(value):
            return float(value)
    return 0.0


def _fsck_clean(result: Mapping[str, Any]) -> bool:
    summary = result.get("summary") if isinstance(result.get("summary"), Mapping) else {}
    after = summary.get("bifrost_stats", {}).get("after") if isinstance(summary.get("bifrost_stats"), Mapping) else {}
    fsck = after.get("fsck") if isinstance(after, Mapping) else None
    return isinstance(fsck, Mapping) and fsck.get("status") == "ok"


def _measured_section(summary: Mapping[str, Any]) -> Mapping[str, Any]:
    sections = summary.get("phase_sections") if isinstance(summary.get("phase_sections"), Mapping) else {}
    measured = sections.get(BenchmarkPhase.MEASURED.value) if isinstance(sections, Mapping) else None
    return measured if isinstance(measured, Mapping) else {}


def _positive_metric_delta(before: Any, after: Any, needles: Sequence[str]) -> bool:
    if not isinstance(before, Mapping) or not isinstance(after, Mapping):
        return False
    for key, value in after.items():
        lowered = str(key).lower()
        if not any(needle in lowered for needle in needles):
            continue
        prior = before.get(key, 0)
        if _positive(value) and isinstance(prior, (int, float)) and float(value) > float(prior):
            return True
    return False


def _root_manifest(
    output_dir: Path,
    mode_results: Sequence[Mapping[str, Any]],
    comparison_path: Path,
    comparison_csv_path: Path,
    evidence_path: Path,
    gate_path: Path,
    report_path: Path,
) -> dict[str, Any]:
    root_artifacts = [
        _root_artifact_entry(output_dir, path)
        for path in (
            output_dir / "summary.json",
            comparison_path,
            comparison_csv_path,
            evidence_path,
            gate_path,
            report_path,
        )
        if path.exists()
    ]
    mode_artifact_manifests: list[dict[str, Any]] = []
    generated_configs: list[dict[str, Any]] = []
    for result in mode_results:
        mode_dir = Path(str(result.get("output_dir")))
        manifest_path = mode_dir / "artifact_manifest.json"
        if not manifest_path.exists():
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        mode_artifact_manifests.append(
            {
                "mode": result.get("mode"),
                "repetition": result.get("repetition"),
                "path": str(manifest_path),
                "missing_required_artifacts": manifest.get("missing_required_artifacts"),
            }
        )
        for artifact in manifest.get("artifacts", []):
            if not isinstance(artifact, Mapping):
                continue
            relative = artifact.get("relative_path")
            artifact_type = artifact.get("artifact_type")
            if artifact_type != "config" and relative not in CONFIG_ARTIFACT_NAMES:
                continue
            generated_configs.append(
                {
                    "mode": result.get("mode"),
                    "repetition": result.get("repetition"),
                    "mode_dir": str(mode_dir),
                    "relative_path": relative,
                    "sha256": artifact.get("sha256"),
                    "byte_size": artifact.get("byte_size"),
                }
            )
    return {
        "schema_version": "bifrost.phase6_real_matrix_artifact_manifest.v1",
        "output_dir": str(output_dir),
        "comparison_report": str(comparison_path),
        "comparison_csv": str(comparison_csv_path),
        "sanitized_evidence_bundle": str(evidence_path),
        "completion_gate": str(gate_path),
        "report": str(report_path),
        "root_artifacts": root_artifacts,
        "mode_directories": [str(result.get("output_dir")) for result in mode_results],
        "mode_artifact_manifests": mode_artifact_manifests,
        "generated_configs": generated_configs,
    }


CONFIG_ARTIFACT_NAMES = {
    "resolved_run_config.yaml",
    "generated_vllm_command.json",
    "generated_lmcache_config.yaml",
    "generated_bifrost_connector_config.json",
}


def _root_artifact_entry(output_dir: Path, path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    return {
        "relative_path": path.relative_to(output_dir).as_posix(),
        "sha256": hashlib.sha256(data).hexdigest(),
        "byte_size": len(data),
    }


def _write_matrix_comparison_csv(path: Path, summary: Mapping[str, Any]) -> None:
    columns = [
        "repetition",
        "mode",
        "status",
        "request_count",
        "success_count",
        "error_count",
        "error_rate",
        "p50_latency_ms",
        "p95_latency_ms",
        "mean_latency_ms",
        "p50_ttft_ms",
        "p95_ttft_ms",
        "throughput_rps",
        "lmcache_store_activity",
        "lmcache_retrieve_activity",
        "bifrost_put_count",
        "bifrost_get_count",
        "bifrost_bytes_put",
        "bifrost_bytes_get",
        "bifrost_store_object_delta",
        "bifrost_store_bytes_delta",
        "bifrost_fsck_status",
        "workload_sha256",
        "model",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in _matrix_rows(summary):
            writer.writerow({key: row.get(key) for key in columns})


def _write_matrix_markdown_report(path: Path, summary: Mapping[str, Any]) -> None:
    rows = _matrix_rows(summary)
    generated_configs = _generated_config_entries(summary)
    raw_results = _raw_result_entries(summary)
    lines = [
        "# BIFROST Phase 6 Real Evidence Run",
        "",
        f"- Status: {_fmt(summary.get('status'))}",
        f"- Output directory: {_fmt(summary.get('output_dir'))}",
        f"- Model path or identifier: {_fmt(_model_value(summary))}",
        f"- Local model asset: {_fmt(_local_model_statement(summary))}",
        f"- Workload SHA-256: {_fmt(_workload_sha(summary))}",
        f"- Workload path: {_fmt(_nested(summary, 'workload', 'path'))}",
        f"- Repetitions: {_fmt(summary.get('repetitions'))}",
        f"- Rotated order: {_fmt(summary.get('rotate_mode_order'))}",
        f"- Completion gate: {_fmt(_nested(summary, 'completion_gate', 'status'))}",
        "",
        "## Environment",
        "",
        f"- Git commit: {_fmt(_nested(summary, 'preflight', 'doctor', 'checks', 'git', 'details', 'commit'))}",
        f"- Dirty tree: {_fmt(_nested(summary, 'preflight', 'doctor', 'checks', 'git', 'details', 'dirty'))}",
        f"- Repository: {_fmt(_nested(summary, 'preflight', 'doctor', 'checks', 'git', 'details', 'repository'))}",
        f"- Hardware: {_fmt(_hardware_summary(summary))}",
        f"- Python: {_fmt(_nested(summary, 'preflight', 'doctor', 'checks', 'python', 'details', 'version'))}",
        f"- Torch: {_fmt(_nested(summary, 'preflight', 'doctor', 'checks', 'torch', 'details', 'version'))}",
        f"- CUDA: {_fmt(_nested(summary, 'preflight', 'doctor', 'checks', 'torch', 'details', 'cuda_version'))}",
        f"- vLLM: {_fmt(_nested(summary, 'preflight', 'doctor', 'checks', 'vllm', 'details', 'version'))}",
        f"- LMCache: {_fmt(_nested(summary, 'preflight', 'doctor', 'checks', 'lmcache', 'details', 'version'))}",
        f"- LMCache BIFROST connector: {_fmt(_nested(summary, 'preflight', 'doctor', 'checks', 'lmcache_bifrost', 'details', 'version'))}",
        f"- BIFROST Python client: {_fmt(_nested(summary, 'preflight', 'doctor', 'checks', 'bifrost_client', 'details', 'version'))}",
        f"- bifrostd binary: {_fmt(_nested(summary, 'preflight', 'doctor', 'checks', 'bifrostd_binary', 'details', 'path'))}",
        "",
        "## Validation",
        "",
        f"- Correctness: {_fmt(_nested(summary, 'correctness', 'status'))}",
        f"- Completion failures: {_fmt(_nested(summary, 'completion_gate', 'failure_count'))}",
        f"- Environment readiness: {_fmt(_nested(summary, 'preflight', 'status'))}",
        f"- Performance metric sources: {_fmt(', '.join(_unique_mode_values(summary, 'performance_metrics_source')) or None)}",
        f"- Connector metric sources: {_fmt(', '.join(_unique_mode_values(summary, 'connector_metrics_source')) or None)}",
        "",
        "## Mode Results",
        "",
        "| Rep | Mode | Status | Requests | p50 latency ms | p95 latency ms | p50 TTFT ms | Error rate | LMCache store/retrieve | BIFROST PUT/GET | BIFROST store objects/bytes | fsck |",
        "| ---: | --- | --- | ---: | ---: | ---: | ---: | ---: | --- | --- | --- | --- |",
    ]
    if rows:
        for row in rows:
            lines.append(
                "| {rep} | {mode} | {status} | {requests} | {p50} | {p95} | {ttft} | {err} | {lmcache} | {bifrost} | {store} | {fsck} |".format(
                    rep=_fmt(row.get("repetition")),
                    mode=_fmt(row.get("mode")),
                    status=_fmt(row.get("status")),
                    requests=_fmt_num(row.get("request_count")),
                    p50=_fmt_num(row.get("p50_latency_ms")),
                    p95=_fmt_num(row.get("p95_latency_ms")),
                    ttft=_fmt_num(row.get("p50_ttft_ms")),
                    err=_fmt_num(row.get("error_rate")),
                    lmcache=f"{_fmt(row.get('lmcache_store_activity'))}/{_fmt(row.get('lmcache_retrieve_activity'))}",
                    bifrost=f"{_fmt_num(row.get('bifrost_put_count'))}/{_fmt_num(row.get('bifrost_get_count'))}",
                    store=f"{_fmt_num(row.get('bifrost_store_object_delta'))}/{_fmt_num(row.get('bifrost_store_bytes_delta'))}",
                    fsck=_fmt(row.get("bifrost_fsck_status")),
                )
            )
    else:
        lines.append("| unavailable | unavailable | unavailable | unavailable | unavailable | unavailable | unavailable | unavailable | unavailable | unavailable | unavailable | unavailable |")
    lines.extend(
        [
            "",
            "## Correctness",
            "",
            f"- Mode: {_fmt(_nested(summary, 'correctness', 'mode'))}",
            f"- Status: {_fmt(_nested(summary, 'correctness', 'status'))}",
            "",
            "## Notes",
            "",
            "- Warmup and cache-population phases are captured separately from measured aggregates.",
            "- Synthetic fake-server metrics are not accepted as final real-serving evidence.",
            "- BIFROST activity requires nonzero connector PUT/GET counters and nonzero store object or byte deltas.",
        ]
    )
    lines.extend(
        [
            "",
            "## Raw Results",
            "",
            "| Rep | Mode | Raw request path |",
            "| ---: | --- | --- |",
        ]
    )
    if raw_results:
        for item in raw_results:
            lines.append(
                f"| {_fmt(item.get('repetition'))} | {_fmt(item.get('mode'))} | {_fmt(item.get('path'))} |"
            )
    else:
        lines.append("| unavailable | unavailable | unavailable |")
    lines.extend(
        [
            "",
            "## Generated Configs",
            "",
            "| Rep | Mode | Config artifact | SHA-256 | Bytes |",
            "| ---: | --- | --- | --- | ---: |",
        ]
    )
    if generated_configs:
        for item in generated_configs:
            lines.append(
                "| {rep} | {mode} | {path} | {sha} | {bytes} |".format(
                    rep=_fmt(item.get("repetition")),
                    mode=_fmt(item.get("mode")),
                    path=_fmt(item.get("relative_path")),
                    sha=_fmt(item.get("sha256")),
                    bytes=_fmt_num(item.get("byte_size")),
                )
            )
    else:
        lines.append("| unavailable | unavailable | unavailable | unavailable | unavailable |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _model_value(summary: Mapping[str, Any]) -> Any:
    model = _nested(summary, "preflight", "doctor", "checks", "model", "details", "value")
    if model is not None:
        return model
    for result in summary.get("mode_results", []):
        if not isinstance(result, Mapping):
            continue
        run_summary = result.get("summary") if isinstance(result.get("summary"), Mapping) else {}
        if run_summary.get("model"):
            return run_summary.get("model")
    return None


def _local_model_statement(summary: Mapping[str, Any]) -> str | None:
    details = _nested(summary, "preflight", "doctor", "checks", "model", "details")
    if not isinstance(details, Mapping):
        return None
    kind = details.get("kind")
    readable = details.get("readable")
    if kind == "local_path" and readable is True:
        return "local readable path; no model download used"
    if kind == "local_path":
        return "local path configured but not readable"
    if kind:
        return str(kind)
    return None


def _hardware_summary(summary: Mapping[str, Any]) -> str | None:
    platform_details = _nested(summary, "preflight", "doctor", "checks", "platform", "details")
    torch_details = _nested(summary, "preflight", "doctor", "checks", "torch", "details")
    if not isinstance(platform_details, Mapping) and not isinstance(torch_details, Mapping):
        return None
    parts: list[str] = []
    if isinstance(platform_details, Mapping):
        machine = platform_details.get("machine")
        cpu_count = platform_details.get("cpu_count")
        memory = platform_details.get("memory_bytes")
        if machine:
            parts.append(str(machine))
        if cpu_count is not None:
            parts.append(f"{cpu_count} CPUs")
        if isinstance(memory, int):
            parts.append(f"{memory} bytes RAM")
    if isinstance(torch_details, Mapping):
        gpu_names = torch_details.get("gpu_names")
        if isinstance(gpu_names, list) and gpu_names:
            parts.append("GPU(s): " + ", ".join(str(item) for item in gpu_names))
        device_count = torch_details.get("cuda_device_count")
        if device_count is not None:
            parts.append(f"CUDA devices: {device_count}")
    return "; ".join(parts) if parts else None


def _unique_mode_values(summary: Mapping[str, Any], key: str) -> list[str]:
    values: set[str] = set()
    for result in summary.get("mode_results", []):
        if not isinstance(result, Mapping):
            continue
        run_summary = result.get("summary") if isinstance(result.get("summary"), Mapping) else {}
        value = run_summary.get(key)
        if value is not None:
            values.add(str(value))
    return sorted(values)


def _raw_result_entries(summary: Mapping[str, Any]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for result in summary.get("mode_results", []):
        if not isinstance(result, Mapping):
            continue
        artifacts = result.get("artifacts") if isinstance(result.get("artifacts"), Mapping) else {}
        path = artifacts.get("raw_requests")
        if path is None:
            run_summary = result.get("summary") if isinstance(result.get("summary"), Mapping) else {}
            path = run_summary.get("raw_requests_path")
        entries.append(
            {
                "repetition": result.get("repetition"),
                "mode": result.get("mode"),
                "path": path,
            }
        )
    return entries


def _generated_config_entries(summary: Mapping[str, Any]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for result in summary.get("mode_results", []):
        if not isinstance(result, Mapping):
            continue
        manifest = result.get("artifact_manifest") if isinstance(result.get("artifact_manifest"), Mapping) else {}
        for artifact in manifest.get("artifacts", []):
            if not isinstance(artifact, Mapping):
                continue
            relative = artifact.get("relative_path")
            artifact_type = artifact.get("artifact_type")
            if artifact_type != "config" and relative not in CONFIG_ARTIFACT_NAMES:
                continue
            entries.append(
                {
                    "repetition": result.get("repetition"),
                    "mode": result.get("mode"),
                    "relative_path": relative,
                    "sha256": artifact.get("sha256"),
                    "byte_size": artifact.get("byte_size"),
                }
            )
    return entries


def _matrix_rows(summary: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    workload_sha = _workload_sha(summary)
    for result in summary.get("mode_results", []):
        if not isinstance(result, Mapping):
            continue
        run_summary = result.get("summary") if isinstance(result.get("summary"), Mapping) else {}
        connector = _connector_counts(run_summary)
        store = _store_counts(run_summary)
        lmcache = run_summary.get("lmcache_activity") if isinstance(run_summary.get("lmcache_activity"), Mapping) else {}
        lmcache_log = run_summary.get("lmcache_log_activity") if isinstance(run_summary.get("lmcache_log_activity"), Mapping) else {}
        lmcache_store_activity = bool(lmcache.get("store_activity") or lmcache_log.get("store_activity"))
        lmcache_retrieve_activity = bool(lmcache.get("retrieve_activity") or lmcache_log.get("retrieve_activity"))
        rows.append(
            {
                "repetition": result.get("repetition"),
                "mode": result.get("mode"),
                "status": result.get("status"),
                "request_count": run_summary.get("request_count"),
                "success_count": run_summary.get("success_count"),
                "error_count": run_summary.get("error_count"),
                "error_rate": run_summary.get("error_rate"),
                "p50_latency_ms": run_summary.get("p50_latency_ms"),
                "p95_latency_ms": run_summary.get("p95_latency_ms"),
                "mean_latency_ms": run_summary.get("mean_latency_ms"),
                "p50_ttft_ms": run_summary.get("p50_ttft_ms"),
                "p95_ttft_ms": run_summary.get("p95_ttft_ms"),
                "throughput_rps": run_summary.get("throughput_rps"),
                "lmcache_store_activity": lmcache_store_activity,
                "lmcache_retrieve_activity": lmcache_retrieve_activity,
                "bifrost_put_count": connector["put_count"],
                "bifrost_get_count": connector["get_count"],
                "bifrost_bytes_put": connector["bytes_put"],
                "bifrost_bytes_get": connector["bytes_get"],
                "bifrost_store_object_delta": store["object_delta"],
                "bifrost_store_bytes_delta": store["bytes_delta"],
                "bifrost_fsck_status": _bifrost_fsck_status(run_summary),
                "workload_sha256": workload_sha,
                "model": run_summary.get("model"),
            }
        )
    return rows


def _connector_counts(summary: Mapping[str, Any]) -> dict[str, Any]:
    counts = {"put_count": 0, "get_count": 0, "bytes_put": 0, "bytes_get": 0}
    for stats in _bifrost_connector_stat_maps(summary):
        counts["put_count"] = max(counts["put_count"], int(_first_positive(stats, ("put_count", "connector_put_completed", "put_completed"))))
        counts["get_count"] = max(counts["get_count"], int(_first_positive(stats, ("get_count", "connector_get_completed", "get_completed"))))
        counts["bytes_put"] = max(counts["bytes_put"], int(_first_positive(stats, ("bytes_put", "connector_bytes_put"))))
        counts["bytes_get"] = max(counts["bytes_get"], int(_first_positive(stats, ("bytes_get", "connector_bytes_get"))))
    return counts


def _store_counts(summary: Mapping[str, Any]) -> dict[str, int]:
    delta = summary.get("bifrost_stats_delta") if isinstance(summary.get("bifrost_stats_delta"), Mapping) else {}
    return {
        "object_delta": int(_first_positive(delta, ("object_count", "opaque_lmcache_object_count"))),
        "bytes_delta": int(_first_positive(delta, ("bytes_stored", "total_logical_bytes", "total_bytes_on_disk"))),
        "verified_delta": int(_first_positive(delta, ("verified_count", "committed_count"))),
        "access_delta": int(_first_positive(delta, ("total_access_count",))),
    }


def _bifrost_fsck_status(summary: Mapping[str, Any]) -> Any:
    bifrost_stats = summary.get("bifrost_stats")
    after = bifrost_stats.get("after") if isinstance(bifrost_stats, Mapping) else {}
    fsck = after.get("fsck") if isinstance(after, Mapping) else {}
    return fsck.get("status") if isinstance(fsck, Mapping) else None


def _workload_sha(summary: Mapping[str, Any]) -> Any:
    workload = summary.get("workload")
    return workload.get("sha256") if isinstance(workload, Mapping) else None


def _nested(value: Mapping[str, Any], *keys: str) -> Any:
    current: Any = value
    for key in keys:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return current


def _fmt(value: Any) -> str:
    if value is None:
        return "unavailable"
    return str(value).replace("|", "\\|")


def _fmt_num(value: Any) -> str:
    if value is None:
        return "unavailable"
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value)


def _sanitized_evidence(summary: Mapping[str, Any]) -> dict[str, Any]:
    return redact_mapping(
        {
            "schema_version": "bifrost.phase6_real_matrix_evidence_bundle.v1",
            "status": summary.get("status"),
            "dry_run": summary.get("dry_run"),
            "workload": summary.get("workload"),
            "preflight": summary.get("preflight"),
            "mode_results": [
                {
                    "mode": result.get("mode"),
                    "repetition": result.get("repetition"),
                    "status": result.get("status"),
                    "skip_reason": result.get("skip_reason"),
                    "error": result.get("error"),
                    "output_dir": result.get("output_dir"),
                    "summary_path": result.get("summary_path"),
                }
                for result in summary.get("mode_results", [])
                if isinstance(result, Mapping)
            ],
            "completion_gate": summary.get("completion_gate"),
        }
    )


def _validate_config(config: RealMatrixConfig) -> None:
    if config.repetitions <= 0:
        raise RealMatrixError("repetitions must be positive")
    if config.concurrency <= 0:
        raise RealMatrixError("concurrency must be positive")
    if config.request_rate <= 0:
        raise RealMatrixError("request_rate must be positive")
    if config.port_stride < 4:
        raise RealMatrixError("port_stride must leave room for per-repetition ports")
    if not config.workload_jsonl.exists():
        raise RealMatrixError(f"workload JSONL does not exist: {config.workload_jsonl}")
    parse_phase_order(config.phase_order)
    _single_repetition_matrix(config, config.base_port, f"127.0.0.1:{config.bifrost_base_port}", config.output_dir / ".fairness_check").validate_fairness()


def _enforce_real_opt_in(config: RealMatrixConfig) -> None:
    if os.environ.get("CI") == "true" or os.environ.get("GITHUB_ACTIONS") == "true":
        raise RealMatrixSafetyError("refusing real vLLM execution in CI")
    if not (config.allow_real_vllm or os.environ.get("BIFROST_RUN_REAL_VLLM") == "1"):
        raise RealMatrixSafetyError("refusing real execution without --allow-real-vllm or BIFROST_RUN_REAL_VLLM=1")


def _workload_digest(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    return {
        "path": str(path),
        "byte_size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def _resolved_run_config(
    config: RealMatrixConfig,
    run: BaselineRunConfig,
    workload_digest: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": "bifrost.phase6_real_matrix_resolved_run_config.v1",
        "mode": run.mode.value,
        "gpu_id": config.gpu_id,
        "common_fields": run.common_fields(),
        "vllm_core_flags": run.vllm_core_fields(),
        "mode_specific_fields": run.mode_specific_fields(),
        "workload": dict(workload_digest),
        "fairness_manifest": build_comparison_manifest(
            _single_repetition_matrix(
                config,
                run.port - PRIMARY_BASELINE_MODES.index(run.mode),
                _endpoint_for_port(run.bifrost_endpoint),
                Path("unused"),
            )
        ),
    }


def _load_config(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".json":
        return json.loads(text)
    try:
        import yaml  # type: ignore

        data = yaml.safe_load(text)
        return data if isinstance(data, dict) else {}
    except Exception:
        return _parse_simple_yaml(text)


def _parse_simple_yaml(text: str) -> dict[str, Any]:
    data: dict[str, Any] = {}
    parent: str | None = None
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        if not line.startswith(" ") and line.endswith(":"):
            parent = line[:-1].strip()
            data[parent] = {}
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        target = data[parent] if parent and raw.startswith(" ") and isinstance(data.get(parent), dict) else data
        target[key.strip()] = _parse_scalar(value.strip())
    return data


def _parse_scalar(value: str) -> Any:
    if value in {"", "null", "None"}:
        return None
    if value in {"true", "True"}:
        return True
    if value in {"false", "False"}:
        return False
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    return value.strip('"').strip("'")


def _read_raw(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _check(checks: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    value = checks.get(name)
    return value if isinstance(value, Mapping) else {}


def _mode(value: Any) -> BaselineMode:
    return value if isinstance(value, BaselineMode) else BaselineMode(str(value))


def _matrix_base_port_for_item(item: Mapping[str, Any]) -> int:
    mode = _mode(item["mode"])
    return int(item["vllm_port"]) - PRIMARY_BASELINE_MODES.index(mode)


def _positive(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0


def _endpoint_for_port(endpoint: str | None) -> str:
    return endpoint or "127.0.0.1:7744"


def _split_endpoint(endpoint: str) -> tuple[str, int]:
    host, port_text = endpoint.rsplit(":", 1)
    return host, int(port_text)


def _bifrost_daemon_command(repo_root: Path) -> str:
    for candidate in (
        shutil.which("bifrost-daemon"),
        str(repo_root / "bifrostd" / "target" / "debug" / "bifrost-daemon"),
    ):
        if candidate and Path(candidate).exists():
            return str(candidate)
    return "bifrost-daemon"


def _fsck_command(endpoint: str | None) -> tuple[str, ...]:
    repo_root = Path(__file__).resolve().parents[2]
    store = (
        shutil.which("bifrost-store")
        or str(repo_root / "bifrostd" / "target" / "debug" / "bifrost-store")
    )
    return (store, "fsck", "--endpoint", str(endpoint), "--check", "--json")


def _ensure_log_placeholders(mode_dir: Path) -> None:
    for name in ("stdout.log", "stderr.log"):
        path = mode_dir / name
        if not path.exists():
            path.write_text("", encoding="utf-8")


def _simple_yaml(value: Any, indent: int = 0) -> str:
    prefix = " " * indent
    if isinstance(value, Mapping):
        lines = []
        for key, item in value.items():
            if isinstance(item, Mapping):
                lines.append(f"{prefix}{key}:")
                lines.append(_simple_yaml(item, indent + 2).rstrip())
            else:
                lines.append(f"{prefix}{key}: {json.dumps(item) if isinstance(item, str) else item}")
        return "\n".join(lines) + "\n"
    return f"{prefix}{value}\n"


__all__ = [
    "RealMatrixConfig",
    "RealMatrixError",
    "RealMatrixResult",
    "RealMatrixSafetyError",
    "config_from_args",
    "evaluate_completion_gate",
    "main",
    "mode_order_for_repetition",
    "preflight_real_matrix",
    "run_real_matrix",
]

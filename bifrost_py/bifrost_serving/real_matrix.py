"""Guarded Phase 6 real-serving matrix executor.

This module is intentionally import-safe for CI: dry-run planning does not
import vLLM, LMCache, torch, or the BIFROST LMCache connector. Real execution is
guarded by an explicit opt-in and a preflight gate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
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
    evidence_path = config.output_dir / "sanitized_evidence_bundle.json"
    gate_path = config.output_dir / "completion_gate.json"
    write_json_artifact(comparison_path, {"comparisons": comparisons, "mode_results": mode_results})
    write_json_artifact(evidence_path, _sanitized_evidence(summary))
    write_json_artifact(gate_path, completion_gate)
    write_json_artifact(config.output_dir / "summary.json", summary)
    root_manifest = _root_manifest(config.output_dir, mode_results, comparison_path, evidence_path, gate_path)
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
                failures.append(f"{label} did not report BIFROST connector/store activity")
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
        summary["vllm_metrics"] = {"before": before.get("vllm"), "after": after_measured.get("vllm")}
        write_json_artifact(mode_dir / "summary.json", summary)
        result = _completed_result(mode.value, int(item["repetition"]), mode_dir, summary)
    except Exception as exc:
        result = _failed_result(mode.value, int(item["repetition"]), mode_dir, exc)
    finally:
        for process in reversed(started):
            process.stop()
        _ensure_log_placeholders(mode_dir)
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
    command = build_vllm_command(run, mode_dir / "generated_lmcache_config.yaml" if run.lmcache_enabled else None)["command"]
    processes.append(
        ManagedProcess(
            name="vllm_server",
            command=[str(item) for item in command],
            env=env,
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
    return bool(activity.get("store_activity") and activity.get("retrieve_activity"))


def _bifrost_activity(result: Mapping[str, Any]) -> bool:
    summary = result.get("summary") if isinstance(result.get("summary"), Mapping) else {}
    for source_key in ("connector_metrics_delta", "bifrost_stats_delta"):
        delta = summary.get(source_key)
        if isinstance(delta, Mapping) and any(_positive(value) for value in delta.values()):
            return True
    return False


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
    evidence_path: Path,
    gate_path: Path,
) -> dict[str, Any]:
    return {
        "schema_version": "bifrost.phase6_real_matrix_artifact_manifest.v1",
        "output_dir": str(output_dir),
        "comparison_report": str(comparison_path),
        "sanitized_evidence_bundle": str(evidence_path),
        "completion_gate": str(gate_path),
        "mode_directories": [str(result.get("output_dir")) for result in mode_results],
    }


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
    store = shutil.which("bifrost-store") or "bifrost-store"
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

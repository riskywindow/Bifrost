"""Phase 6 serving benchmark runner."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import urljoin

from .collectors import BifrostMetricsCollector
from .env_doctor import EnvDoctorConfig, run_doctor
from .http_client import DEFAULT_COMPLETIONS_ENDPOINT, OpenAIClientConfig, OpenAICompatibleClient
from .metrics import output_token_count, stats_delta, summarize_request_metrics
from .phases import (
    DEFAULT_PHASE_ORDER,
    BenchmarkPhase,
    PhaseResult,
    build_phase_plans,
    metric_input_from_row,
    parse_phase_order,
    phase_metrics,
    validate_measured_aggregate,
)
from .request_schema import ServingRequest, read_jsonl
from .workloads import summarize_workload

BACKENDS = {"openai-compatible", "fake"}


@dataclass(frozen=True, slots=True)
class ServingBenchmarkConfig:
    workload_jsonl: Path
    base_url: str
    endpoint: str = DEFAULT_COMPLETIONS_ENDPOINT
    model: str = "bifrost-fake-model"
    backend: str = "openai-compatible"
    concurrency: int = 1
    request_rate: float | None = None
    timeout_seconds: float = 30.0
    output_dir: Path = Path("runs/phase6-serving")
    label: str = "serving"
    headers: dict[str, str] = field(default_factory=dict)
    bifrost_endpoint: str | None = None
    collect_bifrost_stats: bool = False
    collect_bifrost_fsck: bool = False
    bifrost_fsck_command: tuple[str, ...] = ()
    connector_metrics_jsonl_path: Path | None = None
    engine_warmup_requests: int = 0
    population_requests_per_prefix: int = 0
    measured_requests_per_prefix: int | None = None
    phase_timeout_seconds: float | None = None
    phase_order: tuple[BenchmarkPhase, ...] = DEFAULT_PHASE_ORDER


@dataclass(frozen=True, slots=True)
class ServingBenchmarkResult:
    output_dir: Path
    summary: dict[str, Any]
    raw_requests_path: Path
    summary_path: Path
    config_path: Path
    workload_copy_path: Path


def run_serving_benchmark(config: ServingBenchmarkConfig) -> ServingBenchmarkResult:
    _validate_config(config)
    output_dir = config.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    requests = read_jsonl(config.workload_jsonl)
    workload_copy_path = output_dir / "workload.jsonl"
    shutil.copyfile(config.workload_jsonl, workload_copy_path)

    config_snapshot = _config_snapshot(config)
    config_path = output_dir / "config.json"
    _write_json(config_path, config_snapshot)

    doctor_report = run_doctor(
        EnvDoctorConfig(
            endpoint=config.bifrost_endpoint or "127.0.0.1:7420",
            output_dir=output_dir,
        )
    ).to_dict()

    started_unix_s = time.time()
    plans = build_phase_plans(
        requests,
        engine_warmup_requests=config.engine_warmup_requests,
        population_requests_per_prefix=config.population_requests_per_prefix,
        measured_requests_per_prefix=config.measured_requests_per_prefix,
        phase_timeout_seconds=config.phase_timeout_seconds,
        phase_order=config.phase_order,
    )
    phase_results: list[PhaseResult] = []
    raw_results: list[dict[str, Any]] = []
    initial_bifrost = collect_bifrost_stats(config)
    initial_backend = collect_backend_metrics(config)
    final_bifrost = initial_bifrost
    final_backend = initial_backend
    for plan in plans:
        phase_bifrost_before = collect_bifrost_stats(config)
        phase_backend_before = collect_backend_metrics(config)
        phase_started = time.time()
        phase_rows = _send_requests(
            config,
            list(plan.requests),
            phase=plan.phase,
            timeout_seconds=plan.timeout_seconds,
        )
        drain_result = None
        if plan.phase == BenchmarkPhase.CACHE_POPULATION:
            drain_result = _wait_for_connector_put_drain(config)
        phase_ended = time.time()
        phase_bifrost_after = collect_bifrost_stats(config)
        phase_backend_after = collect_backend_metrics(config)
        final_bifrost = phase_bifrost_after
        final_backend = phase_backend_after
        phase_results.append(
            PhaseResult(
                phase=plan.phase,
                requests=tuple(plan.requests),
                raw_results=tuple(phase_rows),
                started_unix_s=phase_started,
                ended_unix_s=phase_ended,
                metrics=phase_metrics(
                    plan.phase,
                    list(plan.requests),
                    phase_rows,
                    started_unix_s=phase_started,
                    ended_unix_s=phase_ended,
                ),
                bifrost_stats_before=phase_bifrost_before,
                bifrost_stats_after=phase_bifrost_after,
                backend_metrics_before=phase_backend_before,
                backend_metrics_after=_with_population_drain(
                    phase_backend_after,
                    drain_result,
                ),
            )
        )
        raw_results.extend(phase_rows)
    ended_unix_s = time.time()

    raw_requests_path = output_dir / "raw_requests.jsonl"
    _write_raw_requests(raw_requests_path, raw_results)

    measured_result = _required_phase_result(phase_results, BenchmarkPhase.MEASURED)
    measured_rows = list(measured_result.raw_results)
    measured_requests = list(measured_result.requests)
    metric_inputs = [
        metric_input_from_row(row)
        for row in measured_rows
    ]
    summary_metrics = summarize_request_metrics(
        measured_requests,
        metric_inputs,
        started_unix_s=measured_result.started_unix_s,
        ended_unix_s=measured_result.ended_unix_s,
        bifrost_stats_before=_stats_values(initial_bifrost),
        bifrost_stats_after=_stats_values(final_bifrost),
        connector_metrics_before=_connector_stats(initial_bifrost),
        connector_metrics_after=_connector_stats(final_bifrost),
    )
    summary_metrics["phase"] = BenchmarkPhase.MEASURED.value
    validate_measured_aggregate(raw_results, summary_metrics)
    workload_summary = summarize_workload(requests)
    workload_summary["max_tokens_values"] = sorted({request.max_tokens for request in requests})
    summary: dict[str, Any] = {
        **summary_metrics,
        "label": config.label,
        "backend": config.backend,
        "connector_metrics_source": _snapshot_value(
            final_backend,
            "connector_metrics_source",
        ),
        "performance_metrics_source": _snapshot_value(
            final_backend,
            "performance_metrics_source",
        ),
        "base_url": config.base_url,
        "endpoint": config.endpoint,
        "model": config.model,
        "started_unix_s": started_unix_s,
        "ended_unix_s": ended_unix_s,
        "config_path": str(config_path),
        "workload_path": str(workload_copy_path),
        "raw_requests_path": str(raw_requests_path),
        "environment_doctor": doctor_report,
        "workload_summary": workload_summary,
        "phase_order": [phase.value for phase in config.phase_order],
        "phase_timeout_seconds": config.phase_timeout_seconds,
        "phase_sections": {
            result.phase.value: _phase_summary(result)
            for result in phase_results
        },
        "phase_validation": {
            "status": "ok",
            "top_level_metrics_source": BenchmarkPhase.MEASURED.value,
            "non_measured_raw_request_count": len(raw_results) - len(measured_rows),
            "measured_raw_request_count": len(measured_rows),
        },
        "bifrost_stats": {
            "before": initial_bifrost,
            "after": final_bifrost,
            "delta": summary_metrics["bifrost_stats_delta"],
        },
        "backend_metrics": {
            "before": initial_backend,
            "after": final_backend,
            "delta": stats_delta(_stats_values(initial_backend), _stats_values(final_backend)),
        },
    }

    summary_path = output_dir / "summary.json"
    _write_json(summary_path, summary)
    return ServingBenchmarkResult(
        output_dir=output_dir,
        summary=summary,
        raw_requests_path=raw_requests_path,
        summary_path=summary_path,
        config_path=config_path,
        workload_copy_path=workload_copy_path,
    )


def collect_bifrost_stats(config: ServingBenchmarkConfig) -> dict[str, Any]:
    if not config.collect_bifrost_stats:
        return {"status": "skipped", "reason": "collect_bifrost_stats is false"}
    if not config.bifrost_endpoint:
        return {"status": "skipped", "reason": "bifrost_endpoint was not provided"}
    return BifrostMetricsCollector(
        endpoint=config.bifrost_endpoint,
        timeout_seconds=min(config.timeout_seconds, 5.0),
        collect_fsck=config.collect_bifrost_fsck,
        fsck_command=list(config.bifrost_fsck_command) if config.bifrost_fsck_command else None,
        connector_metrics_jsonl_path=config.connector_metrics_jsonl_path,
    ).snapshot()


def collect_backend_metrics(config: ServingBenchmarkConfig) -> dict[str, Any]:
    if config.backend != "fake":
        return {"status": "skipped", "reason": "backend is not fake"}
    url = urljoin(config.base_url.rstrip("/") + "/", "metrics")
    try:
        request = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(request, timeout=min(config.timeout_seconds, 5.0)) as response:  # noqa: S310
            data = json.loads(response.read().decode("utf-8"))
        if not isinstance(data, dict):
            return {"status": "error", "reason": "metrics response was not an object"}
        return {"status": "ok", "endpoint": url, "stats": data}
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        return {"status": "error", "endpoint": url, "reason": str(exc)}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a Phase 6 serving benchmark")
    parser.add_argument("--workload-jsonl", required=True, type=Path)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--endpoint", default=DEFAULT_COMPLETIONS_ENDPOINT)
    parser.add_argument("--backend", choices=sorted(BACKENDS), default="openai-compatible")
    parser.add_argument("--concurrency", type=int, required=True)
    parser.add_argument("--request-rate", type=float, default=None)
    parser.add_argument("--timeout-seconds", type=float, required=True)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--label", required=True)
    parser.add_argument("--headers", action="append", default=[], metavar="KEY=VALUE")
    parser.add_argument("--bifrost-endpoint", default=None)
    parser.add_argument(
        "--collect-bifrost-stats",
        choices=("true", "false"),
        default="false",
    )
    parser.add_argument(
        "--collect-bifrost-fsck",
        choices=("true", "false"),
        default="false",
    )
    parser.add_argument("--bifrost-fsck-command", action="append", default=[])
    parser.add_argument("--connector-metrics-jsonl-path", type=Path, default=None)
    parser.add_argument("--engine-warmup-requests", type=int, default=0)
    parser.add_argument("--population-requests-per-prefix", type=int, default=0)
    parser.add_argument("--measured-requests-per-prefix", type=int, default=None)
    parser.add_argument("--phase-timeout-seconds", type=float, default=None)
    parser.add_argument(
        "--phase-order",
        default="engine_warmup,cache_population,measured",
    )
    parser.add_argument("--json", action="store_true")
    try:
        args = parser.parse_args(argv)
        config = ServingBenchmarkConfig(
            workload_jsonl=args.workload_jsonl,
            base_url=args.base_url,
            endpoint=args.endpoint,
            backend=args.backend,
            concurrency=args.concurrency,
            request_rate=args.request_rate,
            timeout_seconds=args.timeout_seconds,
            output_dir=args.output_dir,
            label=args.label,
            headers=_parse_headers(args.headers),
            bifrost_endpoint=args.bifrost_endpoint,
            collect_bifrost_stats=args.collect_bifrost_stats == "true",
            collect_bifrost_fsck=args.collect_bifrost_fsck == "true",
            bifrost_fsck_command=tuple(args.bifrost_fsck_command),
            connector_metrics_jsonl_path=args.connector_metrics_jsonl_path,
            engine_warmup_requests=args.engine_warmup_requests,
            population_requests_per_prefix=args.population_requests_per_prefix,
            measured_requests_per_prefix=args.measured_requests_per_prefix,
            phase_timeout_seconds=args.phase_timeout_seconds,
            phase_order=parse_phase_order(args.phase_order),
        )
        result = run_serving_benchmark(config)
        if args.json:
            print(json.dumps(result.summary, indent=2, sort_keys=True))
        else:
            print(
                f"wrote {result.summary_path} "
                f"({result.summary['success_count']}/{result.summary['request_count']} ok)"
            )
        return 0 if result.summary["error_count"] == 0 else 1
    except SystemExit:
        raise
    except Exception as exc:
        print(f"bifrost serving benchmark failed: {exc}", file=sys.stderr)
        return 2


def _send_requests(
    config: ServingBenchmarkConfig,
    requests: list[ServingRequest],
    *,
    phase: BenchmarkPhase,
    timeout_seconds: float | None = None,
) -> list[dict[str, Any]]:
    if not requests:
        return []
    client = OpenAICompatibleClient(
        OpenAIClientConfig(
            base_url=config.base_url,
            endpoint=config.endpoint,
            model=config.model,
            timeout_s=timeout_seconds or config.timeout_seconds,
            concurrency=config.concurrency,
            headers=config.headers,
            include_serving_metadata=config.backend == "fake",
        )
    )
    results: list[dict[str, Any] | None] = [None] * len(requests)
    with ThreadPoolExecutor(max_workers=config.concurrency) as executor:
        future_to_index: dict[Future[Any], int] = {}
        future_started_at: dict[Future[Any], float] = {}
        for index, request in enumerate(requests):
            if config.request_rate is not None and index > 0:
                time.sleep(1.0 / config.request_rate)
            future = executor.submit(client.send, request)
            future_to_index[future] = index
            future_started_at[future] = time.perf_counter()
        for future in as_completed(future_to_index):
            index = future_to_index[future]
            request = requests[index]
            try:
                response = future.result()
            except Exception as exc:
                now = time.perf_counter()
                response = None
                row = _request_error_row(
                    request,
                    str(exc),
                    started_at=future_started_at.get(future, now),
                    ended_at=now,
                    phase=phase,
                )
            else:
                row = {
                    "request_id": request.request_id,
                    "phase": phase.value,
                    "metadata": request.metadata.to_dict(),
                    "prefix_id": request.metadata.prefix_id,
                    "repeat_group": request.metadata.repeat_group,
                    "expected_cache_reuse": request.metadata.expected_cache_reuse,
                    "status": response.status_code,
                    "latency_ms": response.latency_s * 1000.0,
                    "ttft_ms": response.ttft_s * 1000.0 if response.ttft_s is not None else None,
                    "output_token_count": output_token_count(
                        response.response_json,
                        response.output_text,
                    ),
                    "error": response.error,
                    "response_json": response.response_json,
                }
                if (
                    row["error"] is None
                    and (response.status_code is None or not 200 <= response.status_code < 300)
                ):
                    row["error"] = f"http_status_{response.status_code}"
            if response is None:
                results[index] = row
            else:
                results[index] = row
    return [row for row in results if row is not None]


def _with_population_drain(
    backend_metrics: dict[str, Any] | None,
    drain_result: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if drain_result is None:
        return backend_metrics
    merged = dict(backend_metrics or {})
    merged["population_connector_put_drain"] = drain_result
    return merged


def _wait_for_connector_put_drain(config: ServingBenchmarkConfig) -> dict[str, Any] | None:
    path = config.connector_metrics_jsonl_path
    if path is None:
        return None
    deadline = time.monotonic() + min(max(config.timeout_seconds, 30.0), 180.0)
    stable_since: float | None = None
    last_counts: tuple[int, int, int] | None = None
    while True:
        counts = _connector_put_counts(path)
        started = counts["connector_put_started"]
        completed = counts["connector_put_completed"]
        errors = counts["connector_error"]
        now = time.monotonic()
        current = (started, completed, errors)
        if started > 0 and completed >= started and errors == 0:
            if stable_since is None or current != last_counts:
                stable_since = now
                last_counts = current
            if now - stable_since >= 2.0:
                return {
                    "status": "drained",
                    "connector_put_started": started,
                    "connector_put_completed": completed,
                    "connector_error": errors,
                }
        else:
            stable_since = None
            last_counts = current
        if now >= deadline:
            return {
                "status": "timeout",
                "connector_put_started": started,
                "connector_put_completed": completed,
                "connector_error": errors,
            }
        time.sleep(1.0)


def _connector_put_counts(path: Path) -> dict[str, int]:
    counts = {
        "connector_put_started": 0,
        "connector_put_completed": 0,
        "connector_error": 0,
    }
    if not path.exists():
        return counts
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    event = json.loads(line).get("event")
                except json.JSONDecodeError:
                    continue
                if event in counts:
                    counts[event] += 1
    except OSError:
        return counts
    return counts


def _request_error_row(
    request: ServingRequest,
    error: str,
    *,
    started_at: float,
    ended_at: float,
    phase: BenchmarkPhase = BenchmarkPhase.MEASURED,
) -> dict[str, Any]:
    return {
        "request_id": request.request_id,
        "phase": phase.value,
        "metadata": request.metadata.to_dict(),
        "prefix_id": request.metadata.prefix_id,
        "repeat_group": request.metadata.repeat_group,
        "expected_cache_reuse": request.metadata.expected_cache_reuse,
        "status": None,
        "latency_ms": max(0.0, (ended_at - started_at) * 1000.0),
        "ttft_ms": None,
        "output_token_count": None,
        "error": error,
        "response_json": None,
    }


def _write_raw_requests(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")))
            handle.write("\n")


def _required_phase_result(
    phase_results: list[PhaseResult],
    phase: BenchmarkPhase,
) -> PhaseResult:
    for result in phase_results:
        if result.phase == phase:
            return result
    raise ValueError(f"benchmark phase did not run: {phase.value}")


def _phase_summary(result: PhaseResult) -> dict[str, Any]:
    summary = result.to_summary_dict()
    summary["bifrost_stats"]["delta"] = stats_delta(
        _stats_values(result.bifrost_stats_before),
        _stats_values(result.bifrost_stats_after),
    )
    summary["backend_metrics"]["delta"] = stats_delta(
        _stats_values(result.backend_metrics_before),
        _stats_values(result.backend_metrics_after),
    )
    return summary


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _config_snapshot(config: ServingBenchmarkConfig) -> dict[str, Any]:
    return {
        "schema_version": "bifrost.serving_runner_config.v1",
        "workload_jsonl": str(config.workload_jsonl),
        "base_url": config.base_url,
        "endpoint": config.endpoint,
        "model": config.model,
        "backend": config.backend,
        "concurrency": config.concurrency,
        "request_rate": config.request_rate,
        "timeout_seconds": config.timeout_seconds,
        "output_dir": str(config.output_dir),
        "label": config.label,
        "headers": _redact_headers(config.headers),
        "bifrost_endpoint": config.bifrost_endpoint,
        "collect_bifrost_stats": config.collect_bifrost_stats,
        "collect_bifrost_fsck": config.collect_bifrost_fsck,
        "bifrost_fsck_command": list(config.bifrost_fsck_command),
        "connector_metrics_jsonl_path": (
            str(config.connector_metrics_jsonl_path)
            if config.connector_metrics_jsonl_path
            else None
        ),
        "engine_warmup_requests": config.engine_warmup_requests,
        "population_requests_per_prefix": config.population_requests_per_prefix,
        "measured_requests_per_prefix": config.measured_requests_per_prefix,
        "phase_timeout_seconds": config.phase_timeout_seconds,
        "phase_order": [phase.value for phase in config.phase_order],
    }


def _stats_values(snapshot: dict[str, Any] | None) -> dict[str, Any] | None:
    if not snapshot or snapshot.get("status") != "ok":
        return None
    stats = snapshot.get("stats")
    return stats if isinstance(stats, dict) else None


def _snapshot_value(snapshot: dict[str, Any] | None, key: str) -> Any:
    if not isinstance(snapshot, dict):
        return None
    stats = snapshot.get("stats")
    if isinstance(stats, dict) and key in stats:
        return stats[key]
    return snapshot.get(key)


def _connector_stats(snapshot: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(snapshot, dict) or snapshot.get("status") != "ok":
        return None
    connector = snapshot.get("connector_metrics")
    if not isinstance(connector, dict) or connector.get("status") != "ok":
        return None
    stats = connector.get("stats")
    return stats if isinstance(stats, dict) else None


def _parse_headers(values: list[str]) -> dict[str, str]:
    headers: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise ValueError(f"header must be KEY=VALUE: {value!r}")
        key, header_value = value.split("=", 1)
        if not key:
            raise ValueError("header key must be non-empty")
        headers[key] = header_value
    return headers


def _redact_headers(headers: dict[str, str]) -> dict[str, str]:
    sensitive_fragments = ("authorization", "token", "api-key", "apikey", "secret", "cookie")
    redacted: dict[str, str] = {}
    for key, value in headers.items():
        lowered = key.lower()
        if any(fragment in lowered for fragment in sensitive_fragments):
            redacted[key] = "<redacted>"
        else:
            redacted[key] = value
    return redacted


def _validate_config(config: ServingBenchmarkConfig) -> None:
    if config.backend not in BACKENDS:
        raise ValueError(f"unsupported backend: {config.backend}")
    if config.concurrency <= 0:
        raise ValueError("concurrency must be positive")
    if config.timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    if config.request_rate is not None and config.request_rate <= 0:
        raise ValueError("request_rate must be positive when provided")
    if not config.workload_jsonl.exists():
        raise ValueError(f"workload JSONL does not exist: {config.workload_jsonl}")
    if config.engine_warmup_requests < 0:
        raise ValueError("engine_warmup_requests must be non-negative")
    if config.population_requests_per_prefix < 0:
        raise ValueError("population_requests_per_prefix must be non-negative")
    if config.measured_requests_per_prefix is not None and config.measured_requests_per_prefix <= 0:
        raise ValueError("measured_requests_per_prefix must be positive when provided")
    if config.phase_timeout_seconds is not None and config.phase_timeout_seconds <= 0:
        raise ValueError("phase_timeout_seconds must be positive when provided")
    parse_phase_order(config.phase_order)


__all__ = [
    "ServingBenchmarkConfig",
    "ServingBenchmarkResult",
    "collect_backend_metrics",
    "collect_bifrost_stats",
    "main",
    "parse_phase_order",
    "run_serving_benchmark",
]

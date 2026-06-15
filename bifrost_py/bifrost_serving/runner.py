"""Phase 6 serving benchmark runner."""

from __future__ import annotations

import argparse
import dataclasses
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

from .env_doctor import EnvDoctorConfig, run_doctor
from .http_client import DEFAULT_COMPLETIONS_ENDPOINT, OpenAIClientConfig, OpenAICompatibleClient
from .metrics import RequestMetricInput, output_token_count, stats_delta, summarize_request_metrics
from .request_schema import ServingRequest, read_jsonl
from .workloads import summarize_workload

BACKENDS = {"openai-compatible", "fake"}


@dataclass(frozen=True, slots=True)
class ServingBenchmarkConfig:
    workload_jsonl: Path
    base_url: str
    endpoint: str = DEFAULT_COMPLETIONS_ENDPOINT
    backend: str = "openai-compatible"
    concurrency: int = 1
    request_rate: float | None = None
    timeout_seconds: float = 30.0
    output_dir: Path = Path("runs/phase6-serving")
    label: str = "serving"
    headers: dict[str, str] = field(default_factory=dict)
    bifrost_endpoint: str | None = None
    collect_bifrost_stats: bool = False


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

    bifrost_before = collect_bifrost_stats(config)
    backend_before = collect_backend_metrics(config)

    started_unix_s = time.time()
    raw_results = _send_requests(config, requests)
    ended_unix_s = time.time()

    bifrost_after = collect_bifrost_stats(config)
    backend_after = collect_backend_metrics(config)

    raw_requests_path = output_dir / "raw_requests.jsonl"
    _write_raw_requests(raw_requests_path, raw_results)

    metric_inputs = [
        RequestMetricInput(
            request_id=str(row["request_id"]),
            status=row["status"],
            latency_ms=float(row["latency_ms"]),
            ttft_ms=row["ttft_ms"],
            output_token_count=row["output_token_count"],
            error=row["error"],
        )
        for row in raw_results
    ]
    summary_metrics = summarize_request_metrics(
        requests,
        metric_inputs,
        started_unix_s=started_unix_s,
        ended_unix_s=ended_unix_s,
        bifrost_stats_before=_stats_values(bifrost_before),
        bifrost_stats_after=_stats_values(bifrost_after),
    )
    workload_summary = summarize_workload(requests)
    workload_summary["max_tokens_values"] = sorted({request.max_tokens for request in requests})
    summary: dict[str, Any] = {
        **summary_metrics,
        "label": config.label,
        "backend": config.backend,
        "base_url": config.base_url,
        "endpoint": config.endpoint,
        "started_unix_s": started_unix_s,
        "ended_unix_s": ended_unix_s,
        "config_path": str(config_path),
        "workload_path": str(workload_copy_path),
        "raw_requests_path": str(raw_requests_path),
        "environment_doctor": doctor_report,
        "workload_summary": workload_summary,
        "bifrost_stats": {
            "before": bifrost_before,
            "after": bifrost_after,
            "delta": summary_metrics["bifrost_stats_delta"],
        },
        "backend_metrics": {
            "before": backend_before,
            "after": backend_after,
            "delta": stats_delta(_stats_values(backend_before), _stats_values(backend_after)),
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
    try:
        from bifrost_client import BifrostClient, BifrostClientConfig

        client = BifrostClient(
            config=BifrostClientConfig(
                endpoint=config.bifrost_endpoint,
                timeout_seconds=min(config.timeout_seconds, 5.0),
            )
        )
        try:
            client.connect()
            return {
                "status": "ok",
                "endpoint": config.bifrost_endpoint,
                "stats": dataclasses.asdict(client.stats()),
            }
        finally:
            client.close()
    except Exception as exc:
        return {
            "status": "error",
            "endpoint": config.bifrost_endpoint,
            "reason": str(exc),
        }


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
) -> list[dict[str, Any]]:
    client = OpenAICompatibleClient(
        OpenAIClientConfig(
            base_url=config.base_url,
            endpoint=config.endpoint,
            timeout_s=config.timeout_seconds,
            concurrency=config.concurrency,
            headers=config.headers,
        )
    )
    results: list[dict[str, Any] | None] = [None] * len(requests)
    with ThreadPoolExecutor(max_workers=config.concurrency) as executor:
        future_to_index: dict[Future[Any], int] = {}
        for index, request in enumerate(requests):
            if config.request_rate is not None and index > 0:
                time.sleep(1.0 / config.request_rate)
            future_to_index[executor.submit(client.send, request)] = index
        for future in as_completed(future_to_index):
            index = future_to_index[future]
            request = requests[index]
            try:
                response = future.result()
            except Exception as exc:
                now = time.perf_counter()
                response = None
                row = _request_error_row(request, str(exc), now)
            else:
                row = {
                    "request_id": request.request_id,
                    "metadata": request.metadata.to_dict(),
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


def _request_error_row(
    request: ServingRequest,
    error: str,
    now: float,
) -> dict[str, Any]:
    del now
    return {
        "request_id": request.request_id,
        "metadata": request.metadata.to_dict(),
        "status": None,
        "latency_ms": 0.0,
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


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _config_snapshot(config: ServingBenchmarkConfig) -> dict[str, Any]:
    return {
        "schema_version": "bifrost.serving_runner_config.v1",
        "workload_jsonl": str(config.workload_jsonl),
        "base_url": config.base_url,
        "endpoint": config.endpoint,
        "backend": config.backend,
        "concurrency": config.concurrency,
        "request_rate": config.request_rate,
        "timeout_seconds": config.timeout_seconds,
        "output_dir": str(config.output_dir),
        "label": config.label,
        "headers": dict(config.headers),
        "bifrost_endpoint": config.bifrost_endpoint,
        "collect_bifrost_stats": config.collect_bifrost_stats,
    }


def _stats_values(snapshot: dict[str, Any] | None) -> dict[str, Any] | None:
    if not snapshot or snapshot.get("status") != "ok":
        return None
    stats = snapshot.get("stats")
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


__all__ = [
    "ServingBenchmarkConfig",
    "ServingBenchmarkResult",
    "collect_backend_metrics",
    "collect_bifrost_stats",
    "main",
    "run_serving_benchmark",
]

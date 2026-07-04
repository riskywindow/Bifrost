"""Baseline comparison runner for Phase 6 serving benchmarks."""

from __future__ import annotations

import argparse
import json
import shutil
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from .fake_server import FakeOpenAIServerConfig, create_server
from .fake_cache_backends import BifrostLMCacheBackend
from .phases import DEFAULT_PHASE_ORDER, BenchmarkPhase, parse_phase_order
from .runner import ServingBenchmarkConfig, run_serving_benchmark

BASELINE_MODES = {
    "fake_no_cache",
    "fake_with_cache",
    "fake_bifrost_lmcache",
    "vllm_only",
    "vllm_lmcache_local",
    "vllm_lmcache_local_cpu",
    "vllm_lmcache_bifrost",
}
REAL_VLLM_MODES = {
    "vllm_only",
    "vllm_lmcache_local",
    "vllm_lmcache_local_cpu",
    "vllm_lmcache_bifrost",
}


@dataclass(frozen=True, slots=True)
class BaselineComparisonConfig:
    workload_jsonl: Path
    output_dir: Path
    modes: tuple[str, ...] = ("fake_no_cache", "fake_with_cache")
    concurrency: int = 1
    request_rate: float | None = None
    bifrost_endpoint: str | None = None
    model: str | None = None
    allow_real_vllm: bool = False
    timeout_seconds: float = 30.0
    engine_warmup_requests: int = 0
    population_requests_per_prefix: int = 0
    measured_requests_per_prefix: int | None = None
    phase_timeout_seconds: float | None = None
    phase_order: tuple[BenchmarkPhase, ...] = DEFAULT_PHASE_ORDER


@dataclass(frozen=True, slots=True)
class BaselineComparisonResult:
    output_dir: Path
    summary: dict[str, Any]
    summary_path: Path
    markdown_path: Path
    mode_results: list[dict[str, Any]] = field(default_factory=list)


class BaselineComparisonError(RuntimeError):
    """Deterministic Phase 6 baseline comparison failure."""


def run_baseline_comparison(config: BaselineComparisonConfig) -> BaselineComparisonResult:
    _validate_config(config)
    config.output_dir.mkdir(parents=True, exist_ok=True)

    started_unix_s = time.time()
    mode_results: list[dict[str, Any]] = []
    for mode in config.modes:
        mode_results.append(_run_or_skip_mode(config, mode))
    ended_unix_s = time.time()

    comparisons = build_comparisons(mode_results)
    summary = {
        "schema_version": "bifrost.serving_baseline_comparison.v1",
        "started_unix_s": started_unix_s,
        "ended_unix_s": ended_unix_s,
        "workload_jsonl": str(config.workload_jsonl),
        "output_dir": str(config.output_dir),
        "modes": list(config.modes),
        "concurrency": config.concurrency,
        "request_rate": config.request_rate,
        "bifrost_endpoint": config.bifrost_endpoint,
        "model": config.model,
        "allow_real_vllm": config.allow_real_vllm,
        "phase_order": [phase.value for phase in config.phase_order],
        "engine_warmup_requests": config.engine_warmup_requests,
        "population_requests_per_prefix": config.population_requests_per_prefix,
        "measured_requests_per_prefix": config.measured_requests_per_prefix,
        "phase_timeout_seconds": config.phase_timeout_seconds,
        "mode_results": mode_results,
        "comparisons": comparisons,
        "notes": _comparison_notes(mode_results),
    }
    summary_path = config.output_dir / "comparison_summary.json"
    markdown_path = config.output_dir / "comparison_summary.md"
    _write_json(summary_path, summary)
    _write_markdown(markdown_path, summary)

    return BaselineComparisonResult(
        output_dir=config.output_dir,
        summary=summary,
        summary_path=summary_path,
        markdown_path=markdown_path,
        mode_results=mode_results,
    )


def build_comparisons(mode_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    completed = [result for result in mode_results if result.get("status") == "completed"]
    if not completed:
        return []
    baseline = completed[0]
    comparisons: list[dict[str, Any]] = []
    for candidate in mode_results:
        comparisons.append(compare_summaries(baseline, candidate))
    return comparisons


def compare_summaries(
    baseline_result: dict[str, Any],
    candidate_result: dict[str, Any],
) -> dict[str, Any]:
    baseline_summary = _summary_from_result(baseline_result)
    candidate_summary = _summary_from_result(candidate_result)
    baseline_mode = str(baseline_result.get("mode"))
    candidate_mode = str(candidate_result.get("mode"))
    notes: list[str] = []

    if candidate_result.get("status") != "completed" or candidate_summary is None:
        reason = candidate_result.get("skip_reason") or candidate_result.get("error") or "not completed"
        return {
            "baseline_mode": baseline_mode,
            "candidate_mode": candidate_mode,
            "status": "skipped",
            "latency_delta_ms": None,
            "latency_delta_pct": None,
            "ttft_delta_ms": None,
            "ttft_delta_pct": None,
            "error_rate_delta": None,
            "bifrost_stats_delta": None,
            "cache_activity_observed": False,
            "notes": [str(reason)],
            "skipped_reason": str(reason),
        }
    if baseline_summary is None:
        notes.append("baseline summary unavailable")

    latency_delta = _numeric_delta(baseline_summary, candidate_summary, "p50_latency_ms")
    latency_pct = _percent_delta(baseline_summary, candidate_summary, "p50_latency_ms")
    ttft_delta = _numeric_delta(baseline_summary, candidate_summary, "p50_ttft_ms")
    ttft_pct = _percent_delta(baseline_summary, candidate_summary, "p50_ttft_ms")
    error_rate_delta = _numeric_delta(baseline_summary, candidate_summary, "error_rate")
    if ttft_delta is None:
        notes.append("TTFT unavailable for one or both modes")
    if baseline_result.get("status") != "completed":
        notes.append("baseline did not complete")

    return {
        "baseline_mode": baseline_mode,
        "candidate_mode": candidate_mode,
        "status": "compared" if baseline_summary is not None else "incomplete",
        "latency_delta_ms": latency_delta,
        "latency_delta_pct": latency_pct,
        "ttft_delta_ms": ttft_delta,
        "ttft_delta_pct": ttft_pct,
        "error_rate_delta": error_rate_delta,
        "bifrost_stats_delta": candidate_summary.get("bifrost_stats_delta"),
        "cache_activity_observed": cache_activity_observed(candidate_summary),
        "notes": notes,
        "skipped_reason": None,
    }


def cache_activity_observed(summary: dict[str, Any] | None) -> bool:
    if not summary:
        return False
    backend_delta = _nested_dict(summary, "backend_metrics", "delta")
    if _positive_number(backend_delta.get("cache_hits")) or _positive_number(
        backend_delta.get("cache_misses")
    ):
        return True
    connector_delta = summary.get("connector_metrics_delta")
    if isinstance(connector_delta, dict):
        for key in ("put_count", "get_count", "exists_count", "list_count", "bytes_put", "bytes_get"):
            if _positive_number(connector_delta.get(key)):
                return True
    bifrost_delta = summary.get("bifrost_stats_delta")
    if isinstance(bifrost_delta, dict):
        for key in ("put_count", "get_count", "exists_count", "list_count", "bytes_stored", "bytes_get"):
            if _positive_number(bifrost_delta.get(key)):
                return True
    return False


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare Phase 6 serving baselines")
    parser.add_argument("--workload-jsonl", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--modes", action="append", default=[], choices=sorted(BASELINE_MODES))
    parser.add_argument("--concurrency", required=True, type=int)
    parser.add_argument("--request-rate", type=float, default=None)
    parser.add_argument("--bifrost-endpoint", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--allow-real-vllm", action="store_true")
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
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
        result = run_baseline_comparison(
            BaselineComparisonConfig(
                workload_jsonl=args.workload_jsonl,
                output_dir=args.output_dir,
                modes=tuple(args.modes or ("fake_no_cache", "fake_with_cache")),
                concurrency=args.concurrency,
                request_rate=args.request_rate,
                bifrost_endpoint=args.bifrost_endpoint,
                model=args.model,
                allow_real_vllm=args.allow_real_vllm,
                timeout_seconds=args.timeout_seconds,
                engine_warmup_requests=args.engine_warmup_requests,
                population_requests_per_prefix=args.population_requests_per_prefix,
                measured_requests_per_prefix=args.measured_requests_per_prefix,
                phase_timeout_seconds=args.phase_timeout_seconds,
                phase_order=parse_phase_order(args.phase_order),
            )
        )
        if args.json:
            print(json.dumps(result.summary, indent=2, sort_keys=True))
        else:
            completed = sum(1 for item in result.mode_results if item["status"] == "completed")
            skipped = sum(1 for item in result.mode_results if item["status"] == "skipped")
            print(
                f"wrote {result.summary_path} "
                f"({completed} completed, {skipped} skipped)"
            )
        failed = [item for item in result.mode_results if item["status"] == "failed"]
        return 1 if failed else 0
    except SystemExit:
        raise
    except Exception as exc:
        print(f"bifrost baseline comparison failed: {exc}", file=sys.stderr)
        return 2


def _run_or_skip_mode(config: BaselineComparisonConfig, mode: str) -> dict[str, Any]:
    mode = _normalize_mode(mode)
    mode_dir = config.output_dir / mode
    if mode in REAL_VLLM_MODES and not config.allow_real_vllm:
        result = {
            "mode": mode,
            "status": "skipped",
            "skip_reason": "real vLLM modes require --allow-real-vllm",
            "output_dir": str(mode_dir),
            "summary_path": None,
            "artifacts": {},
        }
        _write_json(mode_dir / "mode_result.json", result)
        return result
    if mode in REAL_VLLM_MODES:
        return _run_real_mode(config, mode, mode_dir)
    return _run_fake_mode(config, mode, mode_dir)


def _run_fake_mode(
    config: BaselineComparisonConfig,
    mode: str,
    mode_dir: Path,
) -> dict[str, Any]:
    simulate_cache = mode == "fake_with_cache"
    daemon: subprocess.Popen[str] | None = None
    daemon_log = None
    backend = None
    bifrost_endpoint = config.bifrost_endpoint
    connector_metrics_path = mode_dir / "bifrost_lmcache_connector_metrics.jsonl"
    fsck_command: tuple[str, ...] = ()
    if mode == "fake_bifrost_lmcache":
        bifrost_endpoint = bifrost_endpoint or f"127.0.0.1:{_free_port()}"
        daemon_bin = _find_binary("bifrost-daemon")
        store_bin = _find_binary("bifrost-store")
        if daemon_bin is None or store_bin is None:
            missing = [
                name
                for name, value in {
                    "bifrost-daemon": daemon_bin,
                    "bifrost-store": store_bin,
                }.items()
                if value is None
            ]
            result = {
                "mode": mode,
                "status": "skipped",
                "skip_reason": "missing Rust binaries: " + ", ".join(missing),
                "output_dir": str(mode_dir),
                "summary_path": None,
                "artifacts": {},
            }
            _write_json(mode_dir / "mode_result.json", result)
            return result
        mode_dir.mkdir(parents=True, exist_ok=True)
        daemon_log = (mode_dir / "bifrost_daemon.log").open("w", encoding="utf-8")
        daemon = subprocess.Popen(
            [
                str(daemon_bin),
                "--listen",
                bifrost_endpoint,
                "--spool",
                str(mode_dir / "bifrost_store"),
                "--trace-jsonl",
                str(mode_dir / "bifrost_daemon_trace.jsonl"),
            ],
            stdout=daemon_log,
            stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            _wait_for_tcp(bifrost_endpoint, timeout_seconds=min(config.timeout_seconds, 10.0))
        except Exception:
            daemon.terminate()
            daemon.wait(timeout=5)
            daemon_log.close()
            raise
        backend = BifrostLMCacheBackend(
            endpoint=bifrost_endpoint,
            metrics_jsonl_path=connector_metrics_path,
            timeout_seconds=min(config.timeout_seconds, 10.0),
        )
        connector_metrics_path.touch(exist_ok=True)
        fsck_command = (
            str(store_bin),
            "fsck",
            "--endpoint",
            bifrost_endpoint,
            "--check",
            "--json",
        )
    server = create_server(
        FakeOpenAIServerConfig(
            port=0,
            simulate_cache=simulate_cache,
            base_delay_ms=12 if simulate_cache else 4,
            cache_hit_delay_ms=1,
            per_token_delay_ms=0.5,
            cache_backend=backend,
        )
    )
    server.start_in_thread()
    try:
        run = run_serving_benchmark(
            ServingBenchmarkConfig(
                workload_jsonl=config.workload_jsonl,
                base_url=server.base_url,
                backend="fake",
                concurrency=config.concurrency,
                request_rate=config.request_rate,
                timeout_seconds=config.timeout_seconds,
                output_dir=mode_dir,
                label=mode,
                bifrost_endpoint=bifrost_endpoint,
                collect_bifrost_stats=mode == "fake_bifrost_lmcache",
                collect_bifrost_fsck=mode == "fake_bifrost_lmcache",
                bifrost_fsck_command=fsck_command,
                connector_metrics_jsonl_path=(
                    connector_metrics_path if mode == "fake_bifrost_lmcache" else None
                ),
                engine_warmup_requests=config.engine_warmup_requests,
                population_requests_per_prefix=config.population_requests_per_prefix,
                measured_requests_per_prefix=config.measured_requests_per_prefix,
                phase_timeout_seconds=config.phase_timeout_seconds,
                phase_order=config.phase_order,
            )
        )
        result = _completed_mode_result(mode, run.summary, mode_dir, run.summary_path)
    except Exception as exc:
        result = _failed_mode_result(mode, mode_dir, exc)
    finally:
        server.shutdown()
        if daemon is not None:
            daemon.terminate()
            try:
                daemon.wait(timeout=5)
            except subprocess.TimeoutExpired:
                daemon.kill()
                daemon.wait(timeout=5)
        if daemon_log is not None:
            daemon_log.close()
    _write_json(mode_dir / "mode_result.json", result)
    return result


def _run_real_mode(
    config: BaselineComparisonConfig,
    mode: str,
    mode_dir: Path,
) -> dict[str, Any]:
    if not config.model:
        result = {
            "mode": mode,
            "status": "skipped",
            "skip_reason": "real vLLM modes require --model",
            "output_dir": str(mode_dir),
            "summary_path": None,
            "artifacts": {},
        }
        _write_json(mode_dir / "mode_result.json", result)
        return result
    collect_bifrost = mode == "vllm_lmcache_bifrost"
    try:
        run = run_serving_benchmark(
            ServingBenchmarkConfig(
                workload_jsonl=config.workload_jsonl,
                base_url="http://127.0.0.1:8000",
                backend="openai-compatible",
                concurrency=config.concurrency,
                request_rate=config.request_rate,
                timeout_seconds=config.timeout_seconds,
                output_dir=mode_dir,
                label=mode,
                bifrost_endpoint=config.bifrost_endpoint,
                collect_bifrost_stats=collect_bifrost,
                engine_warmup_requests=config.engine_warmup_requests,
                population_requests_per_prefix=config.population_requests_per_prefix,
                measured_requests_per_prefix=config.measured_requests_per_prefix,
                phase_timeout_seconds=config.phase_timeout_seconds,
                phase_order=config.phase_order,
            )
        )
        result = _completed_mode_result(mode, run.summary, mode_dir, run.summary_path)
    except Exception as exc:
        result = _failed_mode_result(mode, mode_dir, exc)
    _write_json(mode_dir / "mode_result.json", result)
    return result


def _completed_mode_result(
    mode: str,
    summary: dict[str, Any],
    mode_dir: Path,
    summary_path: Path,
) -> dict[str, Any]:
    return {
        "mode": mode,
        "status": "completed",
        "skip_reason": None,
        "output_dir": str(mode_dir),
        "summary_path": str(summary_path),
        "summary": summary,
        "artifacts": {
            "summary": str(summary_path),
            "raw_requests": str(mode_dir / "raw_requests.jsonl"),
            "config": str(mode_dir / "config.json"),
            "workload": str(mode_dir / "workload.jsonl"),
        },
    }


def _failed_mode_result(mode: str, mode_dir: Path, exc: Exception) -> dict[str, Any]:
    return {
        "mode": mode,
        "status": "failed",
        "skip_reason": None,
        "error": str(exc),
        "output_dir": str(mode_dir),
        "summary_path": None,
        "artifacts": {},
    }


def _summary_from_result(result: dict[str, Any]) -> dict[str, Any] | None:
    summary = result.get("summary")
    return summary if isinstance(summary, dict) else None


def _numeric_delta(
    baseline_summary: dict[str, Any] | None,
    candidate_summary: dict[str, Any] | None,
    key: str,
) -> float | None:
    if baseline_summary is None or candidate_summary is None:
        return None
    baseline = baseline_summary.get(key)
    candidate = candidate_summary.get(key)
    if not isinstance(baseline, (int, float)) or not isinstance(candidate, (int, float)):
        return None
    return float(candidate) - float(baseline)


def _percent_delta(
    baseline_summary: dict[str, Any] | None,
    candidate_summary: dict[str, Any] | None,
    key: str,
) -> float | None:
    delta = _numeric_delta(baseline_summary, candidate_summary, key)
    if delta is None or baseline_summary is None:
        return None
    baseline = baseline_summary.get(key)
    if not isinstance(baseline, (int, float)) or float(baseline) == 0.0:
        return None
    return (delta / float(baseline)) * 100.0


def _nested_dict(summary: dict[str, Any], key: str, nested_key: str) -> dict[str, Any]:
    value = summary.get(key)
    if not isinstance(value, dict):
        return {}
    nested = value.get(nested_key)
    return nested if isinstance(nested, dict) else {}


def _positive_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0


def _comparison_notes(mode_results: list[dict[str, Any]]) -> list[str]:
    notes: list[str] = []
    skipped = [item for item in mode_results if item.get("status") == "skipped"]
    if skipped:
        notes.append("One or more modes were skipped; skipped modes are not speedup evidence.")
    failed = [item for item in mode_results if item.get("status") == "failed"]
    if failed:
        notes.append("One or more modes failed; failed modes are not speedup evidence.")
    if not any(item.get("mode") in REAL_VLLM_MODES and item.get("status") == "completed" for item in mode_results):
        notes.append("No real vLLM mode completed; results are fake serving harness measurements only.")
    return notes


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_markdown(path: Path, summary: dict[str, Any]) -> None:
    lines = [
        "# BIFROST Phase 6 Baseline Comparison",
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
        summary_path = result.get("summary_path") or ""
        lines.append(f"| `{result['mode']}` | `{result['status']}` | `{summary_path}` | {note} |")
    lines.extend(
        [
            "",
            "## Comparisons",
            "",
            "| Baseline | Candidate | p50 latency delta ms | p50 TTFT delta ms | Error rate delta | Cache activity |",
            "| --- | --- | ---: | ---: | ---: | --- |",
        ]
    )
    for comparison in summary["comparisons"]:
        lines.append(
            "| "
            f"`{comparison['baseline_mode']}` | "
            f"`{comparison['candidate_mode']}` | "
            f"{_markdown_value(comparison['latency_delta_ms'])} | "
            f"{_markdown_value(comparison['ttft_delta_ms'])} | "
            f"{_markdown_value(comparison['error_rate_delta'])} | "
            f"`{comparison['cache_activity_observed']}` |"
        )
    if summary["notes"]:
        lines.extend(["", "## Notes", ""])
        lines.extend(f"- {note}" for note in summary["notes"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _markdown_value(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.3f}"
    if value is None:
        return ""
    return str(value)


def _validate_config(config: BaselineComparisonConfig) -> None:
    if not config.workload_jsonl.exists():
        raise BaselineComparisonError(f"workload JSONL does not exist: {config.workload_jsonl}")
    if config.concurrency <= 0:
        raise BaselineComparisonError("concurrency must be positive")
    if config.timeout_seconds <= 0:
        raise BaselineComparisonError("timeout_seconds must be positive")
    if config.request_rate is not None and config.request_rate <= 0:
        raise BaselineComparisonError("request_rate must be positive when provided")
    if config.engine_warmup_requests < 0:
        raise BaselineComparisonError("engine_warmup_requests must be non-negative")
    if config.population_requests_per_prefix < 0:
        raise BaselineComparisonError("population_requests_per_prefix must be non-negative")
    if config.measured_requests_per_prefix is not None and config.measured_requests_per_prefix <= 0:
        raise BaselineComparisonError("measured_requests_per_prefix must be positive when provided")
    if config.phase_timeout_seconds is not None and config.phase_timeout_seconds <= 0:
        raise BaselineComparisonError("phase_timeout_seconds must be positive when provided")
    parse_phase_order(config.phase_order)
    if not config.modes:
        raise BaselineComparisonError("at least one mode is required")
    unsupported = [mode for mode in config.modes if _normalize_mode(mode) not in BASELINE_MODES]
    if unsupported:
        raise BaselineComparisonError(f"unsupported mode(s): {', '.join(unsupported)}")


def _normalize_mode(mode: str) -> str:
    if mode == "vllm_lmcache_local":
        return "vllm_lmcache_local_cpu"
    return mode


def _find_binary(name: str) -> Path | None:
    candidate = Path(__file__).resolve().parents[2] / "bifrostd" / "target" / "debug" / name
    if candidate.exists():
        return candidate
    found = shutil.which(name)
    return Path(found) if found else None


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_tcp(endpoint: str, *, timeout_seconds: float) -> None:
    host, port_text = endpoint.rsplit(":", 1)
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, int(port_text)), timeout=0.2):
                return
        except OSError as exc:
            last_error = exc
            time.sleep(0.05)
    raise BaselineComparisonError(f"bifrost-daemon readiness timeout: {last_error}")


__all__ = [
    "BASELINE_MODES",
    "REAL_VLLM_MODES",
    "BaselineComparisonConfig",
    "BaselineComparisonError",
    "BaselineComparisonResult",
    "build_comparisons",
    "cache_activity_observed",
    "compare_summaries",
    "main",
    "run_baseline_comparison",
]

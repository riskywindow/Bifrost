#!/usr/bin/env python3
"""One-command Phase 6 fake serving benchmark demo.

This demo exercises the Phase 6 serving harness with local fake servers only.
It does not import or benchmark vLLM, LMCache, GPU runtimes, or model assets.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

REPO_ROOT = Path(__file__).resolve().parents[2]
for path in (
    REPO_ROOT / "bifrost_py",
    REPO_ROOT / "integrations" / "lmcache_bifrost",
):
    text = str(path)
    if text not in sys.path:
        sys.path.insert(0, text)

from bifrost_serving.compare import BaselineComparisonConfig, run_baseline_comparison
from bifrost_serving.report import ServingReportConfig, generate_serving_report
from bifrost_serving.workloads import WorkloadConfig, generate_workload, write_workload


@dataclass(frozen=True, slots=True)
class FakeServingDemoConfig:
    output_dir: Path
    request_count: int = 8
    concurrency: int = 2


@dataclass(frozen=True, slots=True)
class FakeServingDemoResult:
    status: str
    output_dir: Path
    workload_path: Path
    comparison_dir: Path
    baseline_run_dir: Path
    candidate_run_dir: Path
    report_path: Path
    summary: dict[str, Any]


class FakeServingDemoError(RuntimeError):
    """Deterministic fake serving demo failure."""


def run_fake_serving_demo(config: FakeServingDemoConfig) -> FakeServingDemoResult:
    _validate_config(config)
    output_dir = config.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    workload_dir = output_dir / "workload"
    workload_path = workload_dir / "fake_ci_small.jsonl"
    workload_summary_path = workload_dir / "summary.json"
    workload = generate_workload(
        WorkloadConfig(
            workload_name="fake_ci_small",
            request_count=config.request_count,
            prefix_repeat_groups=min(2, config.request_count),
            max_tokens=4,
            seed=606,
        )
    )
    write_workload(workload, out=workload_path, summary_path=workload_summary_path)

    comparison_dir = output_dir / "comparison"
    comparison = run_baseline_comparison(
        BaselineComparisonConfig(
            workload_jsonl=workload_path,
            output_dir=comparison_dir,
            modes=("fake_no_cache", "fake_bifrost_lmcache"),
            concurrency=config.concurrency,
            timeout_seconds=10.0,
            population_requests_per_prefix=1,
            measured_requests_per_prefix=1,
        )
    )

    by_mode = {item["mode"]: item for item in comparison.mode_results}
    baseline = by_mode.get("fake_no_cache")
    candidate = by_mode.get("fake_bifrost_lmcache")
    if baseline is None or candidate is None:
        raise FakeServingDemoError("comparison did not produce both fake modes")
    baseline_summary = _completed_summary(baseline, "fake_no_cache")
    candidate_summary = _completed_summary(candidate, "fake_bifrost_lmcache")

    report_dir = output_dir / "report"
    report = generate_serving_report(
        ServingReportConfig(
            run_dir=Path(str(candidate["output_dir"])),
            comparison_dir=comparison_dir,
            out=report_dir,
            format="all",
        )
    )
    if report.report_path is None:
        raise FakeServingDemoError("report generator did not write report.md")

    cache_effect = _cache_effect(comparison.summary)
    correctness = report.summary.get("correctness", {})
    no_errors = baseline_summary.get("error_count") == 0 and candidate_summary.get("error_count") == 0
    connector = candidate_summary.get("connector_metrics_delta") or {}
    connector_activity = all(
        int(connector.get(name) or 0) > 0
        for name in ("put_count", "exists_count", "get_count")
    )
    status = "PASS" if connector_activity and no_errors and Path(report.report_path).exists() else "FAIL"

    summary = {
        "schema_version": "bifrost.fake_serving_demo.v1",
        "status": status,
        "note": "Fake serving harness demo only; connector activity is real BIFROST LMCache connector activity, but timing is not real vLLM performance evidence.",
        "output_dir": str(output_dir),
        "workload_path": str(workload_path),
        "comparison_dir": str(comparison_dir),
        "baseline_run_dir": str(baseline["output_dir"]),
        "candidate_run_dir": str(candidate["output_dir"]),
        "report_path": str(report.report_path),
        "request_count": config.request_count,
        "concurrency": config.concurrency,
        "baseline": _latency_view(baseline_summary),
        "candidate": _latency_view(candidate_summary),
        "simulated_cache_hit_effect": cache_effect,
        "connector_activity_observed": connector_activity,
        "connector_metrics_source": candidate_summary.get("connector_metrics_source"),
        "performance_metrics_source": candidate_summary.get("performance_metrics_source"),
        "connector_metrics_delta": candidate_summary.get("connector_metrics_delta"),
        "bifrost_stats_delta": candidate_summary.get("bifrost_stats_delta"),
        "correctness_status": correctness.get("status"),
        "correctness_notes": correctness.get("notes", []),
    }
    summary_path = output_dir / "fake_serving_demo_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    summary["summary_path"] = str(summary_path)

    return FakeServingDemoResult(
        status=status,
        output_dir=output_dir,
        workload_path=workload_path,
        comparison_dir=comparison_dir,
        baseline_run_dir=Path(str(baseline["output_dir"])),
        candidate_run_dir=Path(str(candidate["output_dir"])),
        report_path=Path(report.report_path),
        summary=summary,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the Phase 6 fake serving benchmark demo")
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--request-count", type=int, default=8)
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--json", action="store_true")
    try:
        args = parser.parse_args(argv)
        result = run_fake_serving_demo(
            FakeServingDemoConfig(
                output_dir=args.output_dir,
                request_count=args.request_count,
                concurrency=args.concurrency,
            )
        )
        if args.json:
            print(json.dumps(result.summary, indent=2, sort_keys=True))
        else:
            _print_human_summary(result.summary)
        return 0 if result.status == "PASS" else 1
    except SystemExit:
        raise
    except Exception as exc:
        if argv is None:
            print(f"fake serving demo failed: {exc}", file=sys.stderr)
        return 2


def _print_human_summary(summary: dict[str, Any]) -> None:
    baseline = summary["baseline"]
    candidate = summary["candidate"]
    effect = summary["simulated_cache_hit_effect"]
    lines = [
        f"{summary['status']}: Phase 6 fake serving harness demo",
        f"workload: {summary['workload_path']}",
        f"comparison dir: {summary['comparison_dir']}",
        f"baseline run dir: {summary['baseline_run_dir']}",
        f"candidate run dir: {summary['candidate_run_dir']}",
        (
            "baseline latency: "
            f"p50={_fmt_ms(baseline['p50_latency_ms'])}, "
            f"p95={_fmt_ms(baseline['p95_latency_ms'])}"
        ),
        (
            "candidate latency: "
            f"p50={_fmt_ms(candidate['p50_latency_ms'])}, "
            f"p95={_fmt_ms(candidate['p95_latency_ms'])}"
        ),
        (
            "simulated cache hit effect: "
            f"p50_delta={_fmt_ms(effect['p50_latency_delta_ms'])}, "
            f"p50_delta_pct={_fmt_pct(effect['p50_latency_delta_pct'])}, "
            f"cache_hits={effect['cache_hits']}, cache_misses={effect['cache_misses']}"
        ),
        f"correctness: {summary['correctness_status']}",
        f"report: {summary['report_path']}",
        "scope: fake serving harness only; not real vLLM speedup evidence",
    ]
    print("\n".join(lines))


def _completed_summary(result: dict[str, Any], mode: str) -> dict[str, Any]:
    if result.get("status") != "completed":
        raise FakeServingDemoError(f"{mode} did not complete: {result.get('error')}")
    summary = result.get("summary")
    if not isinstance(summary, dict):
        raise FakeServingDemoError(f"{mode} summary is unavailable")
    return summary


def _latency_view(summary: dict[str, Any]) -> dict[str, Any]:
    return {
        "p50_latency_ms": summary.get("p50_latency_ms"),
        "p95_latency_ms": summary.get("p95_latency_ms"),
        "error_count": summary.get("error_count"),
        "request_count": summary.get("request_count"),
    }


def _cache_effect(comparison_summary: dict[str, Any]) -> dict[str, Any]:
    candidate = None
    for item in comparison_summary.get("comparisons", []):
        if item.get("candidate_mode") == "fake_bifrost_lmcache":
            candidate = item
            break
    if not isinstance(candidate, dict):
        raise FakeServingDemoError("fake_bifrost_lmcache comparison was not written")

    cache_hits = 0
    cache_misses = 0
    for result in comparison_summary.get("mode_results", []):
        if result.get("mode") != "fake_bifrost_lmcache":
            continue
        summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
        delta = summary.get("backend_metrics", {}).get("delta", {})
        if isinstance(delta, dict):
            cache_hits = int(delta.get("cache_hits") or 0)
            cache_misses = int(delta.get("cache_misses") or 0)
    return {
        "p50_latency_delta_ms": candidate.get("latency_delta_ms"),
        "p50_latency_delta_pct": candidate.get("latency_delta_pct"),
        "p95_latency_delta_ms": _p95_delta(comparison_summary),
        "cache_activity_observed": candidate.get("cache_activity_observed"),
        "cache_hits": cache_hits,
        "cache_misses": cache_misses,
        "interpretation": "negative latency deltas mean the fake cache-simulating server was faster",
    }


def _p95_delta(comparison_summary: dict[str, Any]) -> float | None:
    baseline = None
    candidate = None
    for result in comparison_summary.get("mode_results", []):
        summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
        if result.get("mode") == "fake_no_cache":
            baseline = summary.get("p95_latency_ms")
        if result.get("mode") == "fake_bifrost_lmcache":
            candidate = summary.get("p95_latency_ms")
    if isinstance(baseline, (int, float)) and isinstance(candidate, (int, float)):
        return float(candidate) - float(baseline)
    return None


def _candidate_faster(effect: dict[str, Any]) -> bool:
    delta = effect.get("p50_latency_delta_ms")
    return isinstance(delta, (int, float)) and float(delta) < 0


def _fmt_ms(value: Any) -> str:
    return f"{float(value):.3f} ms" if isinstance(value, (int, float)) else "unavailable"


def _fmt_pct(value: Any) -> str:
    return f"{float(value):.2f}%" if isinstance(value, (int, float)) else "unavailable"


def _validate_config(config: FakeServingDemoConfig) -> None:
    if config.request_count <= 1:
        raise FakeServingDemoError("request-count must be greater than 1")
    if config.concurrency <= 0:
        raise FakeServingDemoError("concurrency must be positive")


if __name__ == "__main__":
    raise SystemExit(main())

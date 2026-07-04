"""Serving benchmark metrics for ContextStorm Phase 6."""

from __future__ import annotations

from typing import Any, Iterable


SERVE_METRIC_KEYS = (
    "request_count",
    "success_count",
    "error_count",
    "p50_latency_ms",
    "p95_latency_ms",
    "p50_ttft_ms",
    "p95_ttft_ms",
    "throughput_rps",
    "error_rate",
    "repeated_prefix_group_count",
)


def parse_serving_operation_metrics(operation: dict[str, Any]) -> dict[str, Any]:
    """Return a normalized serving metrics mapping from a run operation record."""

    metric = dict(operation.get("metrics") or {})
    metric.setdefault("operation", operation.get("operation"))
    metric.setdefault("repetition", operation.get("repetition", 0))
    metric.setdefault("success", operation.get("exit_code", 1) == 0)
    metric.setdefault("skipped", False)
    metric.setdefault("reason_code", None)
    for key in SERVE_METRIC_KEYS:
        metric.setdefault(key, None)
    metric.setdefault("bifrost_stats_delta", None)
    metric.setdefault("correctness_status", "unknown")
    metric.setdefault("skipped_components", [])
    metric.setdefault("raw_phase6_artifacts", {})
    metric.setdefault("failures", [])
    return metric


def summarize_serving_metrics(metrics: Iterable[dict[str, Any]]) -> dict[str, Any]:
    items = list(metrics)
    completed = [item for item in items if item.get("success") and not item.get("skipped")]
    latest = completed[-1] if completed else {}
    return {
        "operation_count": len(items),
        "success_count": len(completed),
        "failure_count": sum(
            1 for item in items if not item.get("success") and not item.get("skipped")
        ),
        "skip_count": sum(1 for item in items if item.get("skipped")),
        "request_count": sum(int(item.get("request_count") or 0) for item in completed),
        "serving": {
            "p50_latency_ms": latest.get("p50_latency_ms"),
            "p95_latency_ms": latest.get("p95_latency_ms"),
            "p50_ttft_ms": latest.get("p50_ttft_ms"),
            "p95_ttft_ms": latest.get("p95_ttft_ms"),
            "throughput_rps": latest.get("throughput_rps"),
            "error_rate": latest.get("error_rate"),
            "repeated_prefix_group_count": latest.get("repeated_prefix_group_count"),
            "bifrost_stats_delta": latest.get("bifrost_stats_delta"),
            "correctness_status": latest.get("correctness_status", "unknown"),
        },
        "skipped_components": [
            component
            for item in items
            for component in (item.get("skipped_components") or [])
        ],
        "raw_phase6_artifacts": [
            item.get("raw_phase6_artifacts")
            for item in items
            if item.get("raw_phase6_artifacts")
        ],
        "failures": [
            failure
            for item in items
            for failure in (item.get("failures") or [])
        ],
    }

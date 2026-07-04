"""Derived metrics and provenance helpers for Phase 6 serving benchmark runs."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Iterable

from .request_schema import ServingRequest


@dataclass(frozen=True, slots=True)
class RequestMetricInput:
    request_id: str
    status: int | None
    latency_ms: float
    ttft_ms: float | None = None
    output_token_count: int | None = None
    error: str | None = None

    @property
    def success(self) -> bool:
        return self.error is None and self.status is not None and 200 <= self.status < 300


class MetricSource(StrEnum):
    VLLM_BENCH_SERVE = "vllm_bench_serve"
    VLLM_METRICS_ENDPOINT = "vllm_metrics_endpoint"
    LMCACHE_PROMETHEUS = "lmcache_prometheus"
    LMCACHE_INTERNAL_API = "lmcache_internal_api"
    BIFROST_CONNECTOR_METRICS = "bifrost_connector_metrics"
    BIFROST_CONNECTOR_JSONL = "bifrost_connector_jsonl"
    BIFROST_STORE_STATS = "bifrost_store_stats"
    SYNTHETIC_FAKE_SERVER = "synthetic_fake_server"
    UNAVAILABLE = "unavailable"


AUTHORITATIVE_METRIC_SOURCES = tuple(source.value for source in MetricSource)


@dataclass(frozen=True, slots=True)
class ProvenancedMetric:
    name: str
    value: int | float | None
    source: MetricSource
    status: str = "available"
    raw_name: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "value": self.value,
            "source": self.source.value,
            "status": self.status,
            "raw_name": self.raw_name,
        }


def metric_value(
    name: str,
    value: int | float | None,
    source: MetricSource | str,
    *,
    raw_name: str | None = None,
) -> dict[str, Any]:
    metric_source = source if isinstance(source, MetricSource) else MetricSource(str(source))
    status = "available" if value is not None else "unavailable"
    return ProvenancedMetric(
        name=name,
        value=value,
        source=metric_source if value is not None else MetricSource.UNAVAILABLE,
        status=status,
        raw_name=raw_name,
    ).to_dict()


def source_delta(
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
    *,
    source: MetricSource | str,
) -> dict[str, Any]:
    delta = stats_delta(before, after) or {}
    source_value = source.value if isinstance(source, MetricSource) else str(source)
    return {
        key: {
            "value": value,
            "source": source_value,
            "status": "available",
        }
        for key, value in delta.items()
    }


def summarize_request_metrics(
    requests: list[ServingRequest],
    results: Iterable[RequestMetricInput],
    *,
    started_unix_s: float,
    ended_unix_s: float,
    bifrost_stats_before: dict[str, Any] | None = None,
    bifrost_stats_after: dict[str, Any] | None = None,
    connector_metrics_before: dict[str, Any] | None = None,
    connector_metrics_after: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result_list = list(results)
    success = [result for result in result_list if result.success]
    errors = [result for result in result_list if not result.success]
    latencies = [result.latency_ms for result in success]
    ttfts = [result.ttft_ms for result in success if result.ttft_ms is not None]
    duration_s = max(0.0, ended_unix_s - started_unix_s)
    expected_cache = sum(1 for request in requests if request.metadata.expected_cache_reuse)
    repeated_groups = {
        request.metadata.prefix_id
        for request in requests
        if request.metadata.expected_cache_reuse
    }
    output_tokens = [
        result.output_token_count
        for result in success
        if result.output_token_count is not None
    ]

    bifrost_delta = stats_delta(bifrost_stats_before, bifrost_stats_after)
    if bifrost_delta is not None and "total_logical_bytes" in bifrost_delta:
        bifrost_delta.setdefault("bytes_stored", bifrost_delta["total_logical_bytes"])

    return {
        "schema_version": "bifrost.serving_summary.v1",
        "request_count": len(result_list),
        "success_count": len(success),
        "error_count": len(errors),
        "error_rate": (len(errors) / len(result_list)) if result_list else 0.0,
        "p50_latency_ms": percentile(latencies, 50),
        "p95_latency_ms": percentile(latencies, 95),
        "mean_latency_ms": mean(latencies),
        "p50_ttft_ms": percentile(ttfts, 50) if ttfts else None,
        "p95_ttft_ms": percentile(ttfts, 95) if ttfts else None,
        "mean_ttft_ms": mean(ttfts) if ttfts else None,
        "ttft_available_count": len(ttfts),
        "output_token_count": sum(output_tokens) if output_tokens else None,
        "mean_output_tokens": mean(output_tokens) if output_tokens else None,
        "throughput_rps": (len(success) / duration_s) if duration_s > 0 else 0.0,
        "run_duration_s": duration_s,
        "cache_expected_request_count": expected_cache,
        "repeated_prefix_group_count": len(repeated_groups),
        "bifrost_stats_delta": bifrost_delta,
        "connector_metrics_delta": stats_delta(
            connector_metrics_before,
            connector_metrics_after,
        ),
    }


def percentile(values: Iterable[float], percentile_value: float) -> float | None:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return None
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * (percentile_value / 100.0)
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return ordered[int(rank)]
    lower_value = ordered[lower]
    upper_value = ordered[upper]
    return lower_value + (upper_value - lower_value) * (rank - lower)


def mean(values: Iterable[float]) -> float | None:
    collected = [float(value) for value in values]
    if not collected:
        return None
    return sum(collected) / len(collected)


def stats_delta(
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if before is None or after is None:
        return None
    delta: dict[str, Any] = {}
    for key, after_value in after.items():
        before_value = before.get(key)
        if isinstance(before_value, bool) or isinstance(after_value, bool):
            continue
        if isinstance(before_value, (int, float)) and isinstance(after_value, (int, float)):
            delta[key] = after_value - before_value
    return delta


def merge_metric_maps_preserving_source(*maps: dict[str, Any] | None) -> dict[str, list[dict[str, Any]]]:
    """Group metric entries by name without collapsing sources.

    Phase 6 reports may compare synthetic timing counters beside real connector
    counters. This helper intentionally returns a list for every metric name so
    a synthetic value cannot overwrite an authoritative connector/store value.
    """
    merged: dict[str, list[dict[str, Any]]] = {}
    for mapping in maps:
        if not isinstance(mapping, dict):
            continue
        for name, value in mapping.items():
            if isinstance(value, dict) and "source" in value:
                entry = dict(value)
                entry.setdefault("name", name)
            else:
                entry = {
                    "name": name,
                    "value": value,
                    "source": MetricSource.UNAVAILABLE.value,
                    "status": "unavailable" if value is None else "available",
                }
            merged.setdefault(name, []).append(entry)
    return merged


def nested_stats_delta(
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
    *,
    stats_key: str = "stats",
) -> dict[str, Any] | None:
    """Diff the numeric stats payload from two collector snapshots."""
    return stats_delta(_collector_stats(before, stats_key), _collector_stats(after, stats_key))


def _collector_stats(
    snapshot: dict[str, Any] | None,
    stats_key: str,
) -> dict[str, Any] | None:
    if not snapshot or snapshot.get("status") != "ok":
        return None
    stats = snapshot.get(stats_key)
    return stats if isinstance(stats, dict) else None


def output_token_count(response_json: dict[str, Any] | None, output_text: str) -> int | None:
    if isinstance(response_json, dict):
        usage = response_json.get("usage")
        if isinstance(usage, dict):
            for key in ("completion_tokens", "output_tokens", "generated_tokens"):
                value = usage.get(key)
                if isinstance(value, int):
                    return value
                if isinstance(value, float) and value.is_integer():
                    return int(value)
    if output_text:
        return len(output_text.split())
    return None


__all__ = [
    "RequestMetricInput",
    "AUTHORITATIVE_METRIC_SOURCES",
    "MetricSource",
    "ProvenancedMetric",
    "mean",
    "merge_metric_maps_preserving_source",
    "metric_value",
    "output_token_count",
    "percentile",
    "nested_stats_delta",
    "source_delta",
    "stats_delta",
    "summarize_request_metrics",
]

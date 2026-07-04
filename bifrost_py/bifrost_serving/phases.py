"""Explicit Phase 6 benchmark phase planning and summaries."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any, Iterable

from .metrics import RequestMetricInput, summarize_request_metrics
from .request_schema import RequestMetadata, ServingRequest


class BenchmarkPhase(StrEnum):
    ENGINE_WARMUP = "engine_warmup"
    CACHE_POPULATION = "cache_population"
    MEASURED = "measured"


DEFAULT_PHASE_ORDER = (
    BenchmarkPhase.ENGINE_WARMUP,
    BenchmarkPhase.CACHE_POPULATION,
    BenchmarkPhase.MEASURED,
)


@dataclass(frozen=True, slots=True)
class PhasePlan:
    phase: BenchmarkPhase
    requests: tuple[ServingRequest, ...]
    timeout_seconds: float | None = None

    @property
    def request_count(self) -> int:
        return len(self.requests)


@dataclass(frozen=True, slots=True)
class PhaseMetrics:
    phase: BenchmarkPhase
    request_count: int
    success_count: int
    error_count: int
    error_rate: float
    duration_s: float
    throughput_rps: float
    p50_latency_ms: float | None
    p95_latency_ms: float | None
    mean_latency_ms: float | None
    p50_ttft_ms: float | None
    p95_ttft_ms: float | None
    mean_ttft_ms: float | None
    ttft_available_count: int
    cache_expected_request_count: int
    repeated_prefix_group_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase.value,
            "request_count": self.request_count,
            "success_count": self.success_count,
            "error_count": self.error_count,
            "error_rate": self.error_rate,
            "duration_s": self.duration_s,
            "throughput_rps": self.throughput_rps,
            "p50_latency_ms": self.p50_latency_ms,
            "p95_latency_ms": self.p95_latency_ms,
            "mean_latency_ms": self.mean_latency_ms,
            "p50_ttft_ms": self.p50_ttft_ms,
            "p95_ttft_ms": self.p95_ttft_ms,
            "mean_ttft_ms": self.mean_ttft_ms,
            "ttft_available_count": self.ttft_available_count,
            "cache_expected_request_count": self.cache_expected_request_count,
            "repeated_prefix_group_count": self.repeated_prefix_group_count,
        }


@dataclass(frozen=True, slots=True)
class PhaseResult:
    phase: BenchmarkPhase
    requests: tuple[ServingRequest, ...]
    raw_results: tuple[dict[str, Any], ...]
    started_unix_s: float
    ended_unix_s: float
    metrics: PhaseMetrics
    bifrost_stats_before: dict[str, Any] | None = None
    bifrost_stats_after: dict[str, Any] | None = None
    backend_metrics_before: dict[str, Any] | None = None
    backend_metrics_after: dict[str, Any] | None = None

    def to_summary_dict(self) -> dict[str, Any]:
        return {
            **self.metrics.to_dict(),
            "started_unix_s": self.started_unix_s,
            "ended_unix_s": self.ended_unix_s,
            "request_ids": [request.request_id for request in self.requests],
            "bifrost_stats": {
                "before": self.bifrost_stats_before,
                "after": self.bifrost_stats_after,
            },
            "backend_metrics": {
                "before": self.backend_metrics_before,
                "after": self.backend_metrics_after,
            },
        }


def parse_phase_order(value: str | Iterable[str | BenchmarkPhase]) -> tuple[BenchmarkPhase, ...]:
    items = value.split(",") if isinstance(value, str) else list(value)
    phases: list[BenchmarkPhase] = []
    for item in items:
        phase = item if isinstance(item, BenchmarkPhase) else BenchmarkPhase(str(item).strip())
        if phase in phases:
            raise ValueError(f"duplicate benchmark phase: {phase.value}")
        phases.append(phase)
    if not phases:
        raise ValueError("phase order must contain at least one phase")
    if BenchmarkPhase.MEASURED not in phases:
        raise ValueError("phase order must include measured")
    return tuple(phases)


def build_phase_plans(
    requests: list[ServingRequest],
    *,
    engine_warmup_requests: int,
    population_requests_per_prefix: int,
    measured_requests_per_prefix: int | None,
    phase_timeout_seconds: float | None,
    phase_order: tuple[BenchmarkPhase, ...] = DEFAULT_PHASE_ORDER,
) -> list[PhasePlan]:
    if engine_warmup_requests < 0:
        raise ValueError("engine_warmup_requests must be non-negative")
    if population_requests_per_prefix < 0:
        raise ValueError("population_requests_per_prefix must be non-negative")
    if measured_requests_per_prefix is not None and measured_requests_per_prefix <= 0:
        raise ValueError("measured_requests_per_prefix must be positive when provided")

    measured = _select_measured_requests(requests, measured_requests_per_prefix)
    measured_prefixes = {request.metadata.prefix_id for request in measured}
    population = _population_requests(measured, population_requests_per_prefix)
    warmup = _warmup_requests(measured, engine_warmup_requests, measured_prefixes)

    by_phase = {
        BenchmarkPhase.ENGINE_WARMUP: tuple(warmup),
        BenchmarkPhase.CACHE_POPULATION: tuple(population),
        BenchmarkPhase.MEASURED: tuple(_with_phase(request, BenchmarkPhase.MEASURED) for request in measured),
    }
    return [
        PhasePlan(phase=phase, requests=by_phase[phase], timeout_seconds=phase_timeout_seconds)
        for phase in phase_order
    ]


def phase_metrics(
    phase: BenchmarkPhase,
    requests: list[ServingRequest],
    rows: list[dict[str, Any]],
    *,
    started_unix_s: float,
    ended_unix_s: float,
) -> PhaseMetrics:
    metric_inputs = [metric_input_from_row(row) for row in rows]
    summary = summarize_request_metrics(
        requests,
        metric_inputs,
        started_unix_s=started_unix_s,
        ended_unix_s=ended_unix_s,
    )
    return PhaseMetrics(
        phase=phase,
        request_count=int(summary["request_count"]),
        success_count=int(summary["success_count"]),
        error_count=int(summary["error_count"]),
        error_rate=float(summary["error_rate"]),
        duration_s=float(summary["run_duration_s"]),
        throughput_rps=float(summary["throughput_rps"]),
        p50_latency_ms=summary["p50_latency_ms"],
        p95_latency_ms=summary["p95_latency_ms"],
        mean_latency_ms=summary["mean_latency_ms"],
        p50_ttft_ms=summary["p50_ttft_ms"],
        p95_ttft_ms=summary["p95_ttft_ms"],
        mean_ttft_ms=summary["mean_ttft_ms"],
        ttft_available_count=int(summary["ttft_available_count"]),
        cache_expected_request_count=int(summary["cache_expected_request_count"]),
        repeated_prefix_group_count=int(summary["repeated_prefix_group_count"]),
    )


def metric_input_from_row(row: dict[str, Any]) -> RequestMetricInput:
    return RequestMetricInput(
        request_id=str(row["request_id"]),
        status=row["status"],
        latency_ms=float(row["latency_ms"]),
        ttft_ms=row["ttft_ms"],
        output_token_count=row["output_token_count"],
        error=row["error"],
    )


def validate_measured_aggregate(raw_results: list[dict[str, Any]], summary: dict[str, Any]) -> None:
    measured_rows = [
        row
        for row in raw_results
        if str(row.get("phase") or row.get("metadata", {}).get("phase")) == BenchmarkPhase.MEASURED.value
    ]
    non_measured_count = len(raw_results) - len(measured_rows)
    if summary.get("request_count") != len(measured_rows):
        raise ValueError(
            "measured aggregate validation failed: top-level request_count "
            "does not match measured raw row count"
        )
    if non_measured_count and summary.get("phase") != BenchmarkPhase.MEASURED.value:
        raise ValueError("measured aggregate validation failed: summary phase is not measured")


def _select_measured_requests(
    requests: list[ServingRequest],
    measured_requests_per_prefix: int | None,
) -> list[ServingRequest]:
    measured_candidates = [
        request
        for request in requests
        if request.metadata.phase == BenchmarkPhase.MEASURED.value
    ]
    if measured_requests_per_prefix is None:
        return measured_candidates
    counts: dict[str, int] = {}
    selected: list[ServingRequest] = []
    for request in measured_candidates:
        prefix_id = request.metadata.prefix_id
        count = counts.get(prefix_id, 0)
        if count >= measured_requests_per_prefix:
            continue
        counts[prefix_id] = count + 1
        selected.append(request)
    return selected


def _population_requests(
    measured: list[ServingRequest],
    per_prefix: int,
) -> list[ServingRequest]:
    if per_prefix == 0:
        return []
    by_prefix: dict[str, list[ServingRequest]] = {}
    for request in measured:
        by_prefix.setdefault(request.metadata.prefix_id, []).append(request)
    population: list[ServingRequest] = []
    for prefix_id in sorted(by_prefix):
        group_requests = by_prefix[prefix_id]
        for index in range(per_prefix):
            source = group_requests[min(index, len(group_requests) - 1)]
            population.append(
                _clone_request(
                    source,
                    phase=BenchmarkPhase.CACHE_POPULATION,
                    request_id=f"cache_population-{index:02d}-{source.request_id}",
                    expected_cache_reuse=False,
                )
            )
    return population


def _warmup_requests(
    measured: list[ServingRequest],
    count: int,
    measured_prefixes: set[str],
) -> list[ServingRequest]:
    if count == 0:
        return []
    template = measured[0] if measured else None
    requests: list[ServingRequest] = []
    index = 0
    while len(requests) < count:
        prefix_id = f"engine_warmup-prefix-{index:04d}"
        index += 1
        if prefix_id in measured_prefixes:
            continue
        max_tokens = template.max_tokens if template else 1
        temperature = template.temperature if template else 0.0
        top_p = template.top_p if template else 1.0
        prompt = (
            f"BIFROST engine warmup isolated prefix {index}. "
            "This prompt intentionally avoids measured prefix groups."
        )
        requests.append(
            ServingRequest(
                request_id=f"engine_warmup-{index - 1:05d}",
                prompt=prompt,
                max_tokens=max_tokens,
                temperature=temperature,
                top_p=top_p,
                stop=list(template.stop) if template and template.stop is not None else None,
                metadata=RequestMetadata(
                    workload_name=template.metadata.workload_name if template else "phase_warmup",
                    prefix_id=prefix_id,
                    repeat_group=index - 1,
                    expected_cache_reuse=False,
                    prompt_token_estimate=max(1, (len(prompt) + 3) // 4),
                    phase=BenchmarkPhase.ENGINE_WARMUP.value,
                ),
            )
        )
    return requests


def _clone_request(
    request: ServingRequest,
    *,
    phase: BenchmarkPhase,
    request_id: str,
    expected_cache_reuse: bool,
) -> ServingRequest:
    return replace(
        request,
        request_id=request_id,
        metadata=replace(
            request.metadata,
            expected_cache_reuse=expected_cache_reuse,
            phase=phase.value,
        ),
    )


def _with_phase(request: ServingRequest, phase: BenchmarkPhase) -> ServingRequest:
    if request.metadata.phase == phase.value:
        return request
    return replace(request, metadata=replace(request.metadata, phase=phase.value))


__all__ = [
    "BenchmarkPhase",
    "DEFAULT_PHASE_ORDER",
    "PhaseMetrics",
    "PhasePlan",
    "PhaseResult",
    "build_phase_plans",
    "metric_input_from_row",
    "parse_phase_order",
    "phase_metrics",
    "validate_measured_aggregate",
]

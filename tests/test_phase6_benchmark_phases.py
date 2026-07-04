from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
for source_path in (
    REPO_ROOT / "bifrost_py",
    REPO_ROOT / "integrations" / "lmcache_bifrost",
):
    text = str(source_path)
    if text not in sys.path:
        sys.path.insert(0, text)

from bifrost_serving.fake_server import FakeOpenAIServerConfig, create_server
from bifrost_serving.phases import (
    BenchmarkPhase,
    build_phase_plans,
    validate_measured_aggregate,
)
from bifrost_serving.runner import ServingBenchmarkConfig, run_serving_benchmark
from bifrost_serving.workloads import WorkloadConfig, generate_workload, write_workload


def test_phase_plan_populates_repeated_prefix_groups_and_isolates_warmup() -> None:
    workload = generate_workload(
        WorkloadConfig(
            workload_name="fake_ci_small",
            request_count=6,
            prefix_repeat_groups=2,
            seed=600,
        )
    )

    plans = build_phase_plans(
        workload.requests,
        engine_warmup_requests=3,
        population_requests_per_prefix=1,
        measured_requests_per_prefix=2,
        phase_timeout_seconds=5,
    )

    by_phase = {plan.phase: plan.requests for plan in plans}
    measured_prefixes = {
        request.metadata.prefix_id for request in by_phase[BenchmarkPhase.MEASURED]
    }
    warmup_prefixes = {
        request.metadata.prefix_id for request in by_phase[BenchmarkPhase.ENGINE_WARMUP]
    }
    population = by_phase[BenchmarkPhase.CACHE_POPULATION]

    assert len(by_phase[BenchmarkPhase.MEASURED]) == 4
    assert len(population) == 2
    assert {request.metadata.prefix_id for request in population} == measured_prefixes
    assert all(request.metadata.phase == "cache_population" for request in population)
    assert all(not request.metadata.expected_cache_reuse for request in population)
    assert len(warmup_prefixes) == 3
    assert warmup_prefixes.isdisjoint(measured_prefixes)
    assert all(
        request.metadata.phase == "engine_warmup"
        for request in by_phase[BenchmarkPhase.ENGINE_WARMUP]
    )


def test_runner_excludes_warmup_and_population_from_measured_metrics(tmp_path: Path) -> None:
    server = create_server(
        FakeOpenAIServerConfig(
            port=0,
            simulate_cache=True,
            base_delay_ms=1,
            cache_hit_delay_ms=1,
            per_token_delay_ms=0,
        )
    )
    server.start_in_thread()
    try:
        workload = generate_workload(
            WorkloadConfig(
                workload_name="fake_ci_small",
                request_count=6,
                prefix_repeat_groups=2,
                max_tokens=2,
                seed=601,
            )
        )
        workload_path = tmp_path / "workload.jsonl"
        write_workload(workload, out=workload_path)

        result = run_serving_benchmark(
            ServingBenchmarkConfig(
                workload_jsonl=workload_path,
                base_url=server.base_url,
                backend="fake",
                concurrency=2,
                timeout_seconds=5,
                output_dir=tmp_path / "run",
                label="phased",
                engine_warmup_requests=2,
                population_requests_per_prefix=1,
                measured_requests_per_prefix=2,
            )
        )

        rows = [
            json.loads(line)
            for line in result.raw_requests_path.read_text(encoding="utf-8").splitlines()
        ]
        phases = [row["phase"] for row in rows]
        measured_rows = [row for row in rows if row["phase"] == "measured"]
        population_rows = [row for row in rows if row["phase"] == "cache_population"]
        warmup_rows = [row for row in rows if row["phase"] == "engine_warmup"]

        assert len(warmup_rows) == 2
        assert len(population_rows) == 2
        assert len(measured_rows) == 4
        assert phases[:2] == ["engine_warmup", "engine_warmup"]
        assert result.summary["phase"] == "measured"
        assert result.summary["request_count"] == 4
        assert result.summary["success_count"] == 4
        assert result.summary["phase_sections"]["engine_warmup"]["request_count"] == 2
        assert result.summary["phase_sections"]["cache_population"]["request_count"] == 2
        assert result.summary["phase_sections"]["measured"]["request_count"] == 4
        assert result.summary["phase_validation"]["non_measured_raw_request_count"] == 4
        assert result.summary["backend_metrics"]["after"]["stats"]["requests"] == 8
        assert result.summary["backend_metrics"]["delta"]["requests"] == 8
        assert all(row["prefix_id"] for row in measured_rows)
        assert all("expected_cache_reuse" in row for row in measured_rows)
    finally:
        server.shutdown()


def test_measured_aggregate_validation_rejects_phase_leakage() -> None:
    raw = [
        {"request_id": "warm", "phase": "engine_warmup", "metadata": {"phase": "engine_warmup"}},
        {"request_id": "measured", "phase": "measured", "metadata": {"phase": "measured"}},
    ]

    with pytest.raises(ValueError, match="request_count"):
        validate_measured_aggregate(raw, {"phase": "measured", "request_count": 2})


def test_phase_failure_is_visible_in_phase_section(tmp_path: Path) -> None:
    server = create_server(FakeOpenAIServerConfig(port=0))
    server.start_in_thread()
    try:
        workload = generate_workload(
            WorkloadConfig(
                workload_name="fake_ci_small",
                request_count=2,
                prefix_repeat_groups=1,
                max_tokens=2,
                seed=602,
            )
        )
        bad = workload.requests[0]
        workload.requests[0] = type(bad)(
            request_id=bad.request_id,
            prompt="trigger __BIFROST_FAKE_ERROR__",
            max_tokens=bad.max_tokens,
            temperature=bad.temperature,
            top_p=bad.top_p,
            stop=bad.stop,
            metadata=bad.metadata,
        )
        workload_path = tmp_path / "workload.jsonl"
        write_workload(workload, out=workload_path)

        result = run_serving_benchmark(
            ServingBenchmarkConfig(
                workload_jsonl=workload_path,
                base_url=server.base_url,
                backend="fake",
                concurrency=1,
                timeout_seconds=5,
                output_dir=tmp_path / "run",
                label="phase-failure",
                engine_warmup_requests=0,
                population_requests_per_prefix=0,
            )
        )

        assert result.summary["error_count"] == 1
        assert result.summary["phase_sections"]["measured"]["error_count"] == 1
        rows = [
            json.loads(line)
            for line in result.raw_requests_path.read_text(encoding="utf-8").splitlines()
        ]
        assert rows[0]["phase"] == "measured"
        assert rows[0]["error"] is not None
    finally:
        server.shutdown()

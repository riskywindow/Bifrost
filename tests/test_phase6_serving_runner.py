from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
for source_path in (
    REPO_ROOT / "bifrost_py",
    REPO_ROOT / "integrations" / "lmcache_bifrost",
):
    text = str(source_path)
    if text not in sys.path:
        sys.path.insert(0, text)

from bifrost_serving.fake_server import FakeOpenAIServerConfig, create_server
from bifrost_serving.metrics import RequestMetricInput, summarize_request_metrics
from bifrost_serving.request_schema import RequestMetadata, ServingRequest, read_jsonl, write_jsonl
from bifrost_serving.runner import ServingBenchmarkConfig, run_serving_benchmark
from bifrost_serving.workloads import WorkloadConfig, generate_workload, write_workload

CLI = REPO_ROOT / "tools" / "bifrost_run_serving_benchmark.py"


def test_runner_works_against_fake_server_and_writes_artifacts(tmp_path: Path) -> None:
    server = create_server(
        FakeOpenAIServerConfig(port=0, simulate_cache=True, base_delay_ms=2)
    )
    server.start_in_thread()
    try:
        workload = generate_workload(
            WorkloadConfig(
                workload_name="fake_ci_small",
                request_count=6,
                prefix_repeat_groups=2,
                max_tokens=3,
                seed=91,
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
                label="fake-baseline",
            )
        )

        summary = result.summary
        assert summary["request_count"] == 6
        assert summary["success_count"] == 6
        assert summary["error_count"] == 0
        assert summary["cache_expected_request_count"] == 4
        assert summary["repeated_prefix_group_count"] == 2
        assert summary["p50_latency_ms"] is not None
        assert summary["p95_latency_ms"] is not None
        assert summary["mean_latency_ms"] is not None
        assert summary["throughput_rps"] > 0
        assert summary["backend_metrics"]["after"]["stats"]["requests"] == 6
        assert summary["backend_metrics"]["delta"]["requests"] == 6

        assert result.raw_requests_path.exists()
        assert result.summary_path.exists()
        assert result.config_path.exists()
        assert result.workload_copy_path.exists()
        assert len(result.raw_requests_path.read_text(encoding="utf-8").splitlines()) == 6
        assert len(read_jsonl(result.workload_copy_path)) == 6

        raw_first = json.loads(
            result.raw_requests_path.read_text(encoding="utf-8").splitlines()[0]
        )
        assert raw_first["request_id"] == workload.requests[0].request_id
        assert raw_first["metadata"]["prefix_id"] == workload.requests[0].metadata.prefix_id
        assert raw_first["output_token_count"] == 3
    finally:
        server.shutdown()


def test_runner_counts_request_errors(tmp_path: Path) -> None:
    server = create_server(FakeOpenAIServerConfig(port=0))
    server.start_in_thread()
    try:
        requests = [
            _request("ok", "shared", "normal prompt"),
            _request("bad", "shared", "trigger __BIFROST_FAKE_ERROR__"),
        ]
        workload_path = tmp_path / "requests.jsonl"
        write_jsonl(workload_path, requests)

        result = run_serving_benchmark(
            ServingBenchmarkConfig(
                workload_jsonl=workload_path,
                base_url=server.base_url,
                backend="fake",
                concurrency=1,
                timeout_seconds=5,
                output_dir=tmp_path / "run",
                label="error-path",
            )
        )

        assert result.summary["request_count"] == 2
        assert result.summary["success_count"] == 1
        assert result.summary["error_count"] == 1
        rows = [
            json.loads(line)
            for line in result.raw_requests_path.read_text(encoding="utf-8").splitlines()
        ]
        assert rows[1]["status"] == 500
        assert "forced fake server error" in rows[1]["error"]
    finally:
        server.shutdown()


def test_bifrost_stats_collection_skips_or_errors_cleanly(tmp_path: Path) -> None:
    server = create_server(FakeOpenAIServerConfig(port=0))
    server.start_in_thread()
    try:
        workload = generate_workload(WorkloadConfig(request_count=1, seed=5))
        workload_path = tmp_path / "workload.jsonl"
        write_workload(workload, out=workload_path)

        skipped = run_serving_benchmark(
            ServingBenchmarkConfig(
                workload_jsonl=workload_path,
                base_url=server.base_url,
                backend="fake",
                concurrency=1,
                timeout_seconds=1,
                output_dir=tmp_path / "skip",
                label="skip",
                collect_bifrost_stats=True,
            )
        ).summary
        assert skipped["bifrost_stats"]["before"]["status"] == "skipped"
        assert skipped["bifrost_stats_delta"] is None

        absent = run_serving_benchmark(
            ServingBenchmarkConfig(
                workload_jsonl=workload_path,
                base_url=server.base_url,
                backend="fake",
                concurrency=1,
                timeout_seconds=1,
                output_dir=tmp_path / "absent",
                label="absent",
                bifrost_endpoint="127.0.0.1:1",
                collect_bifrost_stats=True,
            )
        ).summary
        assert absent["bifrost_stats"]["before"]["status"] == "error"
        assert absent["request_count"] == 1
    finally:
        server.shutdown()


def test_summary_metrics_are_correct_for_known_values() -> None:
    requests = [
        _request("a", "p0", "a", expected_cache_reuse=False),
        _request("b", "p0", "b", expected_cache_reuse=True),
        _request("c", "p1", "c", expected_cache_reuse=False),
        _request("d", "p1", "d", expected_cache_reuse=True),
    ]
    summary = summarize_request_metrics(
        requests,
        [
            RequestMetricInput("a", 200, 10, 3, 2),
            RequestMetricInput("b", 200, 20, 4, 2),
            RequestMetricInput("c", 200, 30, None, 2),
            RequestMetricInput("d", 500, 100, None, None, "failed"),
        ],
        started_unix_s=100.0,
        ended_unix_s=102.0,
        bifrost_stats_before={"object_count": 2, "total_logical_bytes": 10},
        bifrost_stats_after={"object_count": 5, "total_logical_bytes": 40},
    )

    assert summary["request_count"] == 4
    assert summary["success_count"] == 3
    assert summary["error_count"] == 1
    assert summary["p50_latency_ms"] == 20
    assert summary["p95_latency_ms"] == 29
    assert summary["mean_latency_ms"] == 20
    assert summary["p50_ttft_ms"] == 3.5
    assert summary["p95_ttft_ms"] == 3.95
    assert summary["throughput_rps"] == 1.5
    assert summary["cache_expected_request_count"] == 2
    assert summary["repeated_prefix_group_count"] == 2
    assert summary["bifrost_stats_delta"]["object_count"] == 3
    assert summary["bifrost_stats_delta"]["bytes_stored"] == 30


def test_cli_runs_against_fake_server(tmp_path: Path) -> None:
    server = create_server(FakeOpenAIServerConfig(port=0))
    server.start_in_thread()
    try:
        workload = generate_workload(WorkloadConfig(request_count=2, seed=33))
        workload_path = tmp_path / "workload.jsonl"
        output_dir = tmp_path / "cli-run"
        write_workload(workload, out=workload_path)

        result = subprocess.run(
            [
                sys.executable,
                str(CLI),
                "--workload-jsonl",
                str(workload_path),
                "--base-url",
                server.base_url,
                "--backend",
                "fake",
                "--concurrency",
                "1",
                "--timeout-seconds",
                "5",
                "--output-dir",
                str(output_dir),
                "--label",
                "cli",
                "--headers",
                "X-Test=runner",
                "--collect-bifrost-stats",
                "false",
                "--json",
            ],
            cwd=REPO_ROOT,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        assert result.returncode == 0, result.stderr
        data = json.loads(result.stdout)
        assert data["request_count"] == 2
        assert data["success_count"] == 2
        assert (output_dir / "summary.json").exists()
        assert (output_dir / "raw_requests.jsonl").exists()
    finally:
        server.shutdown()


def _request(
    request_id: str,
    prefix_id: str,
    prompt: str,
    *,
    expected_cache_reuse: bool = False,
) -> ServingRequest:
    return ServingRequest(
        request_id=request_id,
        prompt=prompt,
        max_tokens=2,
        temperature=0.0,
        top_p=1.0,
        metadata=RequestMetadata(
            workload_name="fake_ci_small",
            prefix_id=prefix_id,
            repeat_group=0,
            expected_cache_reuse=expected_cache_reuse,
        ),
    )

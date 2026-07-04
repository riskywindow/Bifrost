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

from bifrost_serving.compare import (
    BaselineComparisonConfig,
    cache_activity_observed,
    compare_summaries,
    run_baseline_comparison,
)
from bifrost_serving.workloads import WorkloadConfig, generate_workload, write_workload

CLI = REPO_ROOT / "tools" / "bifrost_compare_serving_baselines.py"


def test_fake_no_cache_vs_fake_with_cache_comparison_runs(tmp_path: Path) -> None:
    workload_path = _write_small_workload(tmp_path)

    result = run_baseline_comparison(
        BaselineComparisonConfig(
            workload_jsonl=workload_path,
            output_dir=tmp_path / "compare",
            modes=("fake_no_cache", "fake_with_cache"),
            concurrency=2,
            timeout_seconds=5,
        )
    )

    assert result.summary["schema_version"] == "bifrost.serving_baseline_comparison.v1"
    assert [item["status"] for item in result.mode_results] == ["completed", "completed"]
    assert [item["mode"] for item in result.mode_results] == ["fake_no_cache", "fake_with_cache"]
    assert len(result.summary["comparisons"]) == 2

    by_mode = {item["mode"]: item for item in result.mode_results}
    assert by_mode["fake_no_cache"]["summary"]["request_count"] == 6
    assert by_mode["fake_with_cache"]["summary"]["request_count"] == 6
    assert not cache_activity_observed(by_mode["fake_no_cache"]["summary"])
    assert cache_activity_observed(by_mode["fake_with_cache"]["summary"])


def test_comparison_summary_computes_deltas() -> None:
    baseline = {
        "mode": "fake_no_cache",
        "status": "completed",
        "summary": {
            "p50_latency_ms": 20.0,
            "p50_ttft_ms": 5.0,
            "error_rate": 0.25,
            "bifrost_stats_delta": {"object_count": 0},
            "backend_metrics": {"delta": {}},
            "connector_metrics_delta": None,
        },
    }
    candidate = {
        "mode": "fake_with_cache",
        "status": "completed",
        "summary": {
            "p50_latency_ms": 15.0,
            "p50_ttft_ms": 4.0,
            "error_rate": 0.0,
            "bifrost_stats_delta": {"object_count": 3},
            "backend_metrics": {"delta": {"cache_hits": 2}},
            "connector_metrics_delta": None,
        },
    }

    comparison = compare_summaries(baseline, candidate)

    assert comparison["baseline_mode"] == "fake_no_cache"
    assert comparison["candidate_mode"] == "fake_with_cache"
    assert comparison["latency_delta_ms"] == -5.0
    assert comparison["latency_delta_pct"] == -25.0
    assert comparison["ttft_delta_ms"] == -1.0
    assert comparison["ttft_delta_pct"] == -20.0
    assert comparison["error_rate_delta"] == -0.25
    assert comparison["bifrost_stats_delta"] == {"object_count": 3}
    assert comparison["cache_activity_observed"] is True


def test_skipped_real_modes_are_reported_not_failed(tmp_path: Path) -> None:
    workload_path = _write_small_workload(tmp_path)

    result = run_baseline_comparison(
        BaselineComparisonConfig(
            workload_jsonl=workload_path,
            output_dir=tmp_path / "compare",
            modes=(
                "fake_no_cache",
                "vllm_only",
                "vllm_lmcache_local_cpu",
                "vllm_lmcache_bifrost",
            ),
            concurrency=1,
            timeout_seconds=5,
        )
    )

    by_mode = {item["mode"]: item for item in result.mode_results}
    assert by_mode["fake_no_cache"]["status"] == "completed"
    assert by_mode["vllm_only"]["status"] == "skipped"
    assert by_mode["vllm_lmcache_local_cpu"]["status"] == "skipped"
    assert by_mode["vllm_lmcache_bifrost"]["status"] == "skipped"
    assert "allow-real-vllm" in by_mode["vllm_only"]["skip_reason"]
    assert all(item["status"] != "failed" for item in result.mode_results)

    skipped_comparisons = [
        item for item in result.summary["comparisons"] if item["candidate_mode"] == "vllm_only"
    ]
    assert skipped_comparisons
    assert skipped_comparisons[0]["status"] == "skipped"


def test_artifacts_are_written(tmp_path: Path) -> None:
    workload_path = _write_small_workload(tmp_path)
    output_dir = tmp_path / "compare"

    result = run_baseline_comparison(
        BaselineComparisonConfig(
            workload_jsonl=workload_path,
            output_dir=output_dir,
            modes=("fake_no_cache", "fake_with_cache", "vllm_only"),
            concurrency=2,
            timeout_seconds=5,
        )
    )

    assert result.summary_path == output_dir / "comparison_summary.json"
    assert result.markdown_path == output_dir / "comparison_summary.md"
    assert result.summary_path.exists()
    assert result.markdown_path.exists()
    assert (output_dir / "fake_no_cache" / "summary.json").exists()
    assert (output_dir / "fake_no_cache" / "raw_requests.jsonl").exists()
    assert (output_dir / "fake_no_cache" / "mode_result.json").exists()
    assert (output_dir / "fake_with_cache" / "summary.json").exists()
    assert (output_dir / "vllm_only" / "mode_result.json").exists()

    data = json.loads(result.summary_path.read_text(encoding="utf-8"))
    assert data["mode_results"][2]["status"] == "skipped"


def test_cli_runs_fake_comparison(tmp_path: Path) -> None:
    workload_path = _write_small_workload(tmp_path)
    output_dir = tmp_path / "cli-compare"

    result = subprocess.run(
        [
            sys.executable,
            str(CLI),
            "--workload-jsonl",
            str(workload_path),
            "--output-dir",
            str(output_dir),
            "--modes",
            "fake_no_cache",
            "--modes",
            "fake_with_cache",
            "--concurrency",
            "2",
            "--timeout-seconds",
            "5",
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
    assert [item["status"] for item in data["mode_results"]] == ["completed", "completed"]
    assert (output_dir / "comparison_summary.json").exists()
    assert (output_dir / "comparison_summary.md").exists()


def _write_small_workload(tmp_path: Path) -> Path:
    workload = generate_workload(
        WorkloadConfig(
            workload_name="fake_ci_small",
            request_count=6,
            prefix_repeat_groups=2,
            max_tokens=3,
            seed=123,
        )
    )
    workload_path = tmp_path / "workload.jsonl"
    write_workload(workload, out=workload_path)
    return workload_path

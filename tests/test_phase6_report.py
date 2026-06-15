from __future__ import annotations

import csv
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

from bifrost_serving.report import ServingReportConfig, generate_serving_report

CLI = REPO_ROOT / "tools" / "bifrost_report_serving_benchmark.py"


def test_report_generates_from_fake_benchmark_run(tmp_path: Path) -> None:
    run_dir = _write_run(tmp_path / "run", ttft=True, bifrost=True)
    comparison_dir = _write_comparison(tmp_path / "compare", include_skipped=False)
    out_dir = tmp_path / "report"

    result = generate_serving_report(
        ServingReportConfig(
            run_dir=run_dir,
            comparison_dir=comparison_dir,
            out=out_dir,
            format="all",
        )
    )

    assert result.report_path == out_dir / "report.md"
    assert result.summary_path == out_dir / "summary.json"
    assert result.per_request_csv_path == out_dir / "per_request.csv"
    assert result.comparison_csv_path == out_dir / "comparison.csv"
    assert result.report_path.exists()
    assert "BIFROST Phase 6 Serving Benchmark Report" in result.report_path.read_text()
    assert "## BIFROST Activity" in result.report_path.read_text()


def test_report_handles_missing_ttft(tmp_path: Path) -> None:
    run_dir = _write_run(tmp_path / "run", ttft=False, bifrost=True)
    result = generate_serving_report(ServingReportConfig(run_dir=run_dir, out=tmp_path / "out"))

    assert result.summary["latency"]["ttft_status"] == "unavailable"
    text = (tmp_path / "out" / "report.md").read_text(encoding="utf-8")
    assert "TTFT is unavailable" in text
    assert "| 10.000 | 19.000 | 15.000 | unavailable | unavailable |" in text


def test_report_handles_skipped_real_vllm_mode(tmp_path: Path) -> None:
    run_dir = _write_run(tmp_path / "run", ttft=False, bifrost=True)
    comparison_dir = _write_comparison(tmp_path / "compare", include_skipped=True)

    result = generate_serving_report(
        ServingReportConfig(run_dir=run_dir, comparison_dir=comparison_dir, out=tmp_path / "out")
    )

    skipped = "\n".join(result.summary["skipped_components"])
    assert "vllm_only skipped" in skipped
    assert "real vLLM modes require --allow-real-vllm" in skipped
    text = (tmp_path / "out" / "report.md").read_text(encoding="utf-8")
    assert "`vllm_only` | `skipped`" in text


def test_report_handles_unavailable_bifrost_stats(tmp_path: Path) -> None:
    run_dir = _write_run(tmp_path / "run", ttft=True, bifrost=False)
    result = generate_serving_report(ServingReportConfig(run_dir=run_dir, out=tmp_path / "out"))

    activity = result.summary["bifrost_activity"]
    assert activity["status"] == "unavailable"
    assert activity["bytes_stored"] is None
    assert "BIFROST stats are unavailable" in (tmp_path / "out" / "report.md").read_text(
        encoding="utf-8"
    )


def test_report_csv_files_parse(tmp_path: Path) -> None:
    run_dir = _write_run(tmp_path / "run", ttft=True, bifrost=True)
    comparison_dir = _write_comparison(tmp_path / "compare", include_skipped=True)
    generate_serving_report(
        ServingReportConfig(
            run_dir=run_dir,
            comparison_dir=comparison_dir,
            out=tmp_path / "out",
            format="csv",
        )
    )

    with (tmp_path / "out" / "per_request.csv").open(encoding="utf-8", newline="") as handle:
        requests = list(csv.DictReader(handle))
    with (tmp_path / "out" / "comparison.csv").open(encoding="utf-8", newline="") as handle:
        comparisons = list(csv.DictReader(handle))

    assert [row["request_id"] for row in requests] == ["req-0", "req-1"]
    assert comparisons[0]["baseline_mode"] == "fake_no_cache"
    assert comparisons[1]["candidate_mode"] == "vllm_only"


def test_report_json_summary_parses_and_cli_prints_json(tmp_path: Path) -> None:
    run_dir = _write_run(tmp_path / "run", ttft=False, bifrost=False)
    out_dir = tmp_path / "out"

    result = subprocess.run(
        [
            sys.executable,
            str(CLI),
            "--run-dir",
            str(run_dir),
            "--out",
            str(out_dir),
            "--format",
            "all",
            "--json",
        ],
        cwd=REPO_ROOT,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    printed = json.loads(result.stdout)
    written = json.loads((out_dir / "summary.json").read_text(encoding="utf-8"))
    assert printed["schema_version"] == "bifrost.serving_report.v1"
    assert written["schema_version"] == "bifrost.serving_report.v1"
    assert written["latency"]["ttft_status"] == "unavailable"


def _write_run(run_dir: Path, *, ttft: bool, bifrost: bool) -> Path:
    run_dir.mkdir(parents=True, exist_ok=True)
    ttft_values = [2.0, 3.0] if ttft else [None, None]
    raw_rows = [
        _raw_request("req-0", 10.0, ttft_values[0], expected_cache_reuse=False),
        _raw_request("req-1", 20.0, ttft_values[1], expected_cache_reuse=True),
    ]
    (run_dir / "raw_requests.jsonl").write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in raw_rows),
        encoding="utf-8",
    )
    (run_dir / "config.json").write_text('{"backend":"fake"}\n', encoding="utf-8")
    (run_dir / "workload.jsonl").write_text("{}\n", encoding="utf-8")

    bifrost_delta = (
        {
            "put_count": 2,
            "get_count": 1,
            "exists_count": 3,
            "bytes_stored": 128,
            "bytes_get": 64,
            "object_count": 2,
        }
        if bifrost
        else None
    )
    summary = {
        "schema_version": "bifrost.serving_summary.v1",
        "label": "fake_with_cache",
        "backend": "fake",
        "base_url": "http://127.0.0.1:1234",
        "endpoint": "/v1/completions",
        "started_unix_s": 1.0,
        "ended_unix_s": 3.0,
        "request_count": 2,
        "success_count": 2,
        "error_count": 0,
        "error_rate": 0.0,
        "p50_latency_ms": 10.0,
        "p95_latency_ms": 19.0,
        "mean_latency_ms": 15.0,
        "p50_ttft_ms": 2.5 if ttft else None,
        "p95_ttft_ms": 2.95 if ttft else None,
        "mean_ttft_ms": 2.5 if ttft else None,
        "ttft_available_count": 2 if ttft else 0,
        "throughput_rps": 1.0,
        "run_duration_s": 2.0,
        "cache_expected_request_count": 1,
        "repeated_prefix_group_count": 1,
        "workload_path": str(run_dir / "workload.jsonl"),
        "workload_summary": {
            "workload_name": "fake_ci_small",
            "max_tokens_values": [3],
        },
        "bifrost_stats_delta": bifrost_delta,
        "connector_metrics_delta": None,
        "bifrost_stats": {
            "before": {"status": "ok", "stats": {"object_count": 10}} if bifrost else {"status": "skipped", "reason": "collect_bifrost_stats is false"},
            "after": {"status": "ok", "stats": {"object_count": 12}, "fsck_status": "clean"} if bifrost else {"status": "skipped", "reason": "collect_bifrost_stats is false"},
            "delta": bifrost_delta,
        },
        "environment_doctor": {
            "checks": {
                "git": {
                    "status": "ready",
                    "details": {
                        "repository": str(REPO_ROOT),
                        "commit": "abc123",
                        "dirty": False,
                    },
                },
                "python": {"status": "ready", "details": {"version": "3.11.0"}},
                "platform": {
                    "status": "ready",
                    "details": {"platform": "test-platform", "machine": "x86_64"},
                },
                "torch": {
                    "status": "ready",
                    "details": {
                        "version": "2.0",
                        "cuda_available": False,
                        "cuda_version": None,
                        "gpu_names": [],
                    },
                },
                "vllm": {"status": "not_ready", "details": {}},
                "lmcache": {"status": "not_ready", "details": {}},
                "lmcache_bifrost": {"status": "ready", "details": {"version": "0.1"}},
            },
            "readiness": {
                "fake_ci_ready": {"status": "ready", "reasons": [], "recommended_fixes": []},
                "real_serving_ready": {
                    "status": "not_ready",
                    "reasons": ["vLLM missing"],
                    "recommended_fixes": [],
                },
            },
        },
    }
    (run_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return run_dir


def _raw_request(
    request_id: str,
    latency_ms: float,
    ttft_ms: float | None,
    *,
    expected_cache_reuse: bool,
) -> dict[str, object]:
    return {
        "request_id": request_id,
        "status": 200,
        "latency_ms": latency_ms,
        "ttft_ms": ttft_ms,
        "output_token_count": 3,
        "error": None,
        "metadata": {
            "workload_name": "fake_ci_small",
            "prefix_id": "p0",
            "repeat_group": 0,
            "expected_cache_reuse": expected_cache_reuse,
        },
        "response_json": {"choices": [{"text": "ok"}]},
    }


def _write_comparison(comparison_dir: Path, *, include_skipped: bool) -> Path:
    comparison_dir.mkdir(parents=True, exist_ok=True)
    mode_results = [
        {
            "mode": "fake_no_cache",
            "status": "completed",
            "skip_reason": None,
            "summary": {
                "request_count": 2,
                "p50_latency_ms": 12.0,
                "p95_latency_ms": 20.0,
                "p50_ttft_ms": None,
                "error_rate": 0.0,
            },
            "summary_path": str(comparison_dir / "fake_no_cache" / "summary.json"),
        }
    ]
    comparisons = [
        {
            "baseline_mode": "fake_no_cache",
            "candidate_mode": "fake_no_cache",
            "status": "compared",
            "latency_delta_ms": 0.0,
            "latency_delta_pct": 0.0,
            "ttft_delta_ms": None,
            "ttft_delta_pct": None,
            "error_rate_delta": 0.0,
            "cache_activity_observed": False,
            "skipped_reason": None,
            "notes": ["TTFT unavailable for one or both modes"],
        }
    ]
    if include_skipped:
        mode_results.append(
            {
                "mode": "vllm_only",
                "status": "skipped",
                "skip_reason": "real vLLM modes require --allow-real-vllm",
                "summary_path": None,
                "artifacts": {},
            }
        )
        comparisons.append(
            {
                "baseline_mode": "fake_no_cache",
                "candidate_mode": "vllm_only",
                "status": "skipped",
                "latency_delta_ms": None,
                "latency_delta_pct": None,
                "ttft_delta_ms": None,
                "ttft_delta_pct": None,
                "error_rate_delta": None,
                "cache_activity_observed": False,
                "skipped_reason": "real vLLM modes require --allow-real-vllm",
                "notes": ["real vLLM modes require --allow-real-vllm"],
            }
        )
    summary = {
        "schema_version": "bifrost.serving_baseline_comparison.v1",
        "mode_results": mode_results,
        "comparisons": comparisons,
        "notes": ["No real vLLM mode completed; results are fake serving harness measurements only."],
    }
    (comparison_dir / "comparison_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return comparison_dir

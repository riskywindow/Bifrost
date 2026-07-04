from __future__ import annotations

import json
from pathlib import Path

from contextstorm.serve_metrics import (
    parse_serving_operation_metrics,
    summarize_serving_metrics,
)
from contextstorm.serve_report import write_serving_report
from contextstorm.serve_runner import load_serving_scenario, run_serving_scenario


def test_serve_fake_small_ci_loads() -> None:
    scenario = load_serving_scenario(Path("scenarios/serve_fake_small_ci.yaml"))

    assert scenario.name == "serve_fake_small_ci"
    assert scenario.workload == "serve"
    assert scenario.operations == ("fake_serving_baseline_comparison",)
    assert scenario.modes == ("fake_no_cache", "fake_bifrost_lmcache")
    assert scenario.request_count == 8
    assert scenario.prefix_repeat_groups == 2


def test_serving_metrics_parser_and_summary() -> None:
    record = {
        "operation": "fake_serving_baseline_comparison",
        "repetition": 0,
        "exit_code": 0,
        "metrics": {
            "success": True,
            "request_count": 4,
            "p50_latency_ms": 10.0,
            "p95_latency_ms": 19.0,
            "p50_ttft_ms": None,
            "throughput_rps": 2.0,
            "error_rate": 0.0,
            "repeated_prefix_group_count": 2,
            "bifrost_stats_delta": {"bytes_stored": 128},
            "correctness_status": "advisory",
            "raw_phase6_artifacts": {"phase6_report_summary": "/tmp/report.json"},
        },
    }

    metric = parse_serving_operation_metrics(record)
    summary = summarize_serving_metrics([metric])

    assert metric["operation"] == "fake_serving_baseline_comparison"
    assert summary["request_count"] == 4
    assert summary["serving"]["p50_latency_ms"] == 10.0
    assert summary["serving"]["p50_ttft_ms"] is None
    assert summary["serving"]["bifrost_stats_delta"] == {"bytes_stored": 128}
    assert summary["serving"]["correctness_status"] == "advisory"


def test_serve_fake_small_ci_runs_with_local_fake_server(tmp_path: Path) -> None:
    run_dir = run_serving_scenario(
        Path("scenarios/serve_fake_small_ci.yaml"),
        runs_root=tmp_path,
        run_id="serve-fake-small-ci-test",
    )

    assert (run_dir / "run.json").exists()
    assert (run_dir / "summary.json").exists()
    assert (run_dir / "summary.md").exists()
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["benchmark_kind"] == "serving"
    assert summary["failure_count"] == 0
    assert summary["skip_count"] == 0
    assert summary["request_count"] == 8
    assert summary["serving"]["p50_latency_ms"] is not None
    assert summary["serving"]["p95_latency_ms"] is not None
    assert summary["serving"]["throughput_rps"] > 0
    assert summary["serving"]["error_rate"] == 0.0
    assert summary["serving"]["repeated_prefix_group_count"] == 2
    artifacts = summary["raw_phase6_artifacts"][0]
    assert Path(artifacts["phase6_comparison_summary"]).exists()
    assert Path(artifacts["phase6_report_summary"]).exists()


def test_optional_real_serving_scenarios_skip_by_default(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("BIFROST_RUN_REAL_VLLM", raising=False)
    monkeypatch.delenv("BIFROST_RUN_TWO_INSTANCE_CACHE_SHARE", raising=False)

    real_dir = run_serving_scenario(
        Path("scenarios/serve_vllm_lmcache_bifrost_opt_in.yaml"),
        runs_root=tmp_path,
        run_id="serve-real-skip-test",
    )
    two_dir = run_serving_scenario(
        Path("scenarios/serve_two_instance_cache_share_opt_in.yaml"),
        runs_root=tmp_path,
        run_id="serve-two-instance-skip-test",
    )

    real_summary = json.loads((real_dir / "summary.json").read_text(encoding="utf-8"))
    two_summary = json.loads((two_dir / "summary.json").read_text(encoding="utf-8"))
    assert real_summary["skip_count"] == 1
    assert two_summary["skip_count"] == 1
    assert real_summary["failure_count"] == 0
    assert two_summary["failure_count"] == 0
    assert "BIFROST_RUN_REAL_VLLM=1" in "\n".join(real_summary["skipped_components"])
    assert "BIFROST_RUN_TWO_INSTANCE_CACHE_SHARE=1" in "\n".join(
        two_summary["skipped_components"]
    )


def test_serving_report_handles_missing_ttft_and_skipped_real_vllm(tmp_path: Path) -> None:
    run = {
        "benchmark_kind": "serving",
        "scenario": {"name": "serve-report-unit"},
        "environment": {"python_version": "3.x", "platform": "test"},
        "operations": [
            {
                "operation": "fake_serving_baseline_comparison",
                "repetition": 0,
                "exit_code": 0,
                "metrics": {
                    "success": True,
                    "request_count": 2,
                    "success_count": 2,
                    "error_count": 0,
                    "p50_latency_ms": 10.0,
                    "p95_latency_ms": 19.0,
                    "p50_ttft_ms": None,
                    "p95_ttft_ms": None,
                    "throughput_rps": 1.0,
                    "error_rate": 0.0,
                    "repeated_prefix_group_count": 1,
                    "bifrost_stats_delta": None,
                    "correctness_status": "advisory",
                    "skipped_components": [
                        "vllm_only skipped: real vLLM modes require --allow-real-vllm"
                    ],
                    "raw_phase6_artifacts": {
                        "phase6_report_summary": str(tmp_path / "phase6" / "summary.json")
                    },
                },
            }
        ],
    }
    (tmp_path / "run.json").write_text(json.dumps(run), encoding="utf-8")

    summary_json, summary_md = write_serving_report(tmp_path)

    summary = json.loads(summary_json.read_text(encoding="utf-8"))
    assert summary["serving"]["p50_ttft_ms"] is None
    assert "vllm_only skipped" in "\n".join(summary["skipped_components"])
    text = summary_md.read_text(encoding="utf-8")
    assert "unavailable" in text
    assert "Raw Phase 6 Artifacts" in text
    assert "vLLM" in text

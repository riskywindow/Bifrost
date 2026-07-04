from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
for source_path in (
    REPO_ROOT / "bifrost_py",
    REPO_ROOT / "integrations" / "lmcache_bifrost",
    REPO_ROOT / "examples" / "serving_benchmark",
):
    text = str(source_path)
    if text not in sys.path:
        sys.path.insert(0, text)

from fake_serving_demo import FakeServingDemoConfig, run_fake_serving_demo

CLI = REPO_ROOT / "examples" / "serving_benchmark" / "fake_serving_demo.py"


def test_fake_serving_demo_runs_and_writes_report(tmp_path: Path) -> None:
    result = run_fake_serving_demo(
        FakeServingDemoConfig(output_dir=tmp_path / "demo", request_count=6, concurrency=2)
    )

    assert result.status == "PASS"
    assert result.workload_path.exists()
    assert result.report_path.exists()
    assert result.summary["request_count"] == 6
    assert result.summary["baseline"]["request_count"] == 2
    assert result.summary["candidate"]["request_count"] == 2
    assert result.summary["correctness_status"] == "advisory"
    assert result.summary["connector_metrics_source"] == "actual_bifrost_remote_connector"
    assert result.summary["performance_metrics_source"] == "synthetic_fake_server"


def test_fake_serving_demo_json_output_parses(tmp_path: Path) -> None:
    output_dir = tmp_path / "demo-json"
    completed = subprocess.run(
        [
            sys.executable,
            str(CLI),
            "--output-dir",
            str(output_dir),
            "--request-count",
            "6",
            "--concurrency",
            "2",
            "--json",
        ],
        cwd=REPO_ROOT,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    data = json.loads(completed.stdout)
    assert data["schema_version"] == "bifrost.fake_serving_demo.v1"
    assert data["status"] == "PASS"
    assert Path(data["report_path"]).exists()
    assert Path(data["comparison_dir"], "comparison_summary.json").exists()


def test_fake_serving_demo_observes_real_connector_activity(tmp_path: Path) -> None:
    result = run_fake_serving_demo(
        FakeServingDemoConfig(output_dir=tmp_path / "demo-fast", request_count=8, concurrency=2)
    )
    effect = result.summary["simulated_cache_hit_effect"]

    assert effect["cache_activity_observed"] is True
    assert effect["cache_hits"] > 0
    assert effect["cache_misses"] > 0
    assert result.summary["connector_activity_observed"] is True
    connector = result.summary["connector_metrics_delta"]
    assert connector["put_count"] > 0
    assert connector["exists_count"] > 0
    assert connector["get_count"] > 0


def test_fake_serving_demo_requires_no_real_serving_dependencies(tmp_path: Path) -> None:
    result = run_fake_serving_demo(
        FakeServingDemoConfig(output_dir=tmp_path / "demo-local", request_count=4, concurrency=1)
    )
    report_summary = json.loads(
        (result.report_path.parent / "summary.json").read_text(encoding="utf-8")
    )

    skipped = "\n".join(report_summary["skipped_components"])
    assert "Real vLLM serving mode was skipped" in skipped
    assert report_summary["scenario"]["backend"] == "fake"
    assert report_summary["scenario"]["performance_metrics_source"] == "synthetic_fake_server"
    workload = json.loads((result.workload_path.parent / "summary.json").read_text(encoding="utf-8"))
    assert workload["requires_gpu"] is False
    assert workload["requires_internet"] is False
    assert workload["requires_model"] is False
    assert workload["requires_tokenizer"] is False

from __future__ import annotations

import json
import shutil
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

from bifrost_serving.compare import BaselineComparisonConfig, run_baseline_comparison
from bifrost_serving.workloads import WorkloadConfig, generate_workload, write_workload


def test_fake_ci_path_uses_real_bifrost_lmcache_connector(tmp_path: Path) -> None:
    _require_binary("bifrost-daemon")
    _require_binary("bifrost-store")
    workload_path = tmp_path / "workload.jsonl"
    workload = generate_workload(
        WorkloadConfig(
            workload_name="fake_ci_small",
            request_count=6,
            prefix_repeat_groups=2,
            max_tokens=3,
            seed=909,
        )
    )
    write_workload(workload, out=workload_path)

    result = run_baseline_comparison(
        BaselineComparisonConfig(
            workload_jsonl=workload_path,
            output_dir=tmp_path / "comparison",
            modes=("fake_bifrost_lmcache",),
            concurrency=1,
            timeout_seconds=15,
            population_requests_per_prefix=1,
            measured_requests_per_prefix=1,
        )
    )

    mode = result.mode_results[0]
    assert mode["status"] == "completed"
    summary = mode["summary"]
    backend = summary["backend_metrics"]["after"]["stats"]
    connector = summary["bifrost_stats"]["after"]["connector_metrics"]["stats"]
    bifrost_delta = summary["bifrost_stats_delta"]
    fsck = summary["bifrost_stats"]["after"]["fsck"]

    assert summary["backend"] == "fake"
    assert summary["performance_metrics_source"] == "synthetic_fake_server"
    assert summary["connector_metrics_source"] == "actual_bifrost_remote_connector"
    assert backend["put_count"] > 0
    assert backend["exists_count"] > 0
    assert backend["get_count"] > 0
    assert connector["put_count"] > 0
    assert connector["exists_count"] > 0
    assert connector["get_count"] > 0
    assert connector["bytes_put"] > 0
    assert connector["bytes_get"] > 0
    assert bifrost_delta["object_count"] > 0
    assert fsck["status"] == "ok"
    assert str(fsck["raw"]["status"]).lower() == "clean"

    raw_rows = [
        json.loads(line)
        for line in Path(summary["raw_requests_path"]).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    measured_hits = [
        row
        for row in raw_rows
        if row["phase"] == "measured"
        and row["response_json"]["bifrost_fake"]["cache_hit"] is True
    ]
    assert measured_hits
    assert all(
        row["response_json"]["bifrost_fake"]["cache_payload_matches"] is True
        for row in measured_hits
    )

    metrics_jsonl = Path(summary["bifrost_stats"]["after"]["connector_metrics"]["path"])
    assert metrics_jsonl.exists()
    events = metrics_jsonl.read_text(encoding="utf-8")
    assert "connector_put_completed" in events
    assert "connector_get_completed" in events


def test_fake_ci_missing_key_is_connector_miss(tmp_path: Path) -> None:
    _require_binary("bifrost-daemon")
    _require_binary("bifrost-store")
    # Covered through the comparison above: the first population request for
    # each prefix is a miss, then measured requests use actual exists/get.
    # This assertion guards against reintroducing synthesized hit counters.
    workload_path = tmp_path / "workload.jsonl"
    workload = generate_workload(
        WorkloadConfig(
            workload_name="fake_ci_small",
            request_count=2,
            prefix_repeat_groups=2,
            max_tokens=1,
            seed=910,
        )
    )
    write_workload(workload, out=workload_path)

    result = run_baseline_comparison(
        BaselineComparisonConfig(
            workload_jsonl=workload_path,
            output_dir=tmp_path / "comparison",
            modes=("fake_bifrost_lmcache",),
            concurrency=1,
            timeout_seconds=15,
        )
    )

    summary = result.mode_results[0]["summary"]
    stats = summary["backend_metrics"]["after"]["stats"]
    assert stats["cache_misses"] == 2
    assert stats["cache_hits"] == 0
    assert stats["exists_count"] == 2
    assert stats["get_count"] == 0


def _require_binary(name: str) -> None:
    candidate = REPO_ROOT / "bifrostd" / "target" / "debug" / name
    if candidate.exists() or shutil.which(name):
        return
    pytest.skip(f"{name} is not built")

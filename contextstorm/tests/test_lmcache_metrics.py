from __future__ import annotations

import json
import socket
from pathlib import Path

import pytest

from contextstorm.lmcache_metrics import (
    parse_lmcache_operation_metrics,
    summarize_lmcache_metrics,
)
from contextstorm.lmcache_report import write_lmcache_report
from contextstorm.lmcache_runner import load_lmcache_scenario, run_lmcache_scenario


def test_lmcache_scenario_yaml_loads() -> None:
    small = load_lmcache_scenario(Path("scenarios/lmcache_connector_small_ci.yaml"))
    large = load_lmcache_scenario(Path("scenarios/lmcache_connector_fake_large.yaml"))
    opt_in = load_lmcache_scenario(Path("scenarios/lmcache_real_opt_in.yaml"))

    assert small.name == "lmcache_connector_small_ci"
    assert small.object_count == 5
    assert small.payload_size_bytes == 64 * 1024
    assert small.operations == (
        "put",
        "exists",
        "get",
        "list",
        "stats",
        "fsck",
        "fake_lmcache_connector_corrupt_object",
    )
    assert large.operations == (
        "fake_lmcache_connector_roundtrip",
        "fake_lmcache_connector_repeated_get",
        "fake_lmcache_connector_batched_ops",
    )
    assert opt_in.operations == ("real_lmcache_connector_smoke", "vllm_lmcache_smoke")


def test_lmcache_metrics_parser_and_summary() -> None:
    record = {
        "operation": "fake_lmcache_connector_roundtrip",
        "repetition": 0,
        "exit_code": 0,
        "metrics": {
            "success": True,
            "connector_put_ms": 1.5,
            "connector_exists_ms": 2.5,
            "connector_get_ms": 3.5,
            "object_count": 2,
            "bytes_put": 128,
            "bytes_get": 128,
            "roundtrip_match_count": 2,
            "exists_true_after_put": True,
            "missing_returns_none": True,
            "all_fake_roundtrips_match": True,
            "fsck_clean": True,
            "corrupt_object_rejected": True,
            "corrupt_rejection_count": 1,
        },
    }

    metric = parse_lmcache_operation_metrics(record)
    summary = summarize_lmcache_metrics([metric])

    assert metric["operation"] == "fake_lmcache_connector_roundtrip"
    assert summary["object_count"] == 2
    assert summary["bytes_put"] == 128
    assert summary["bytes_get"] == 128
    assert summary["correctness"]["all_fake_roundtrips_match"] is True
    assert summary["correctness"]["exists_true_after_put"] is True
    assert summary["correctness"]["missing_returns_none"] is True
    assert summary["correctness"]["fsck_clean"] is True
    assert summary["correctness"]["corrupt_object_rejected"] is True
    assert summary["corrupt_rejection_count"] == 1


def test_lmcache_report_writes_summary_json_and_markdown(tmp_path: Path) -> None:
    run = {
        "benchmark_kind": "lmcache",
        "scenario": {
            "name": "lmcache-unit",
            "lmcache": {"fake_objects": True},
        },
        "environment": {"python_version": "3.x", "platform": "test"},
        "operations": [
            {
                "operation": "fake_lmcache_connector_roundtrip",
                "repetition": 0,
                "exit_code": 0,
                "metrics": {
                    "operation": "fake_lmcache_connector_roundtrip",
                    "repetition": 0,
                    "success": True,
                    "object_count": 1,
                    "bytes_put": 64,
                    "bytes_get": 64,
                    "roundtrip_match_count": 1,
                    "exists_true_after_put": True,
                    "missing_returns_none": True,
                    "all_fake_roundtrips_match": True,
                    "fsck_clean": True,
                    "fsck_status": "clean",
                },
            }
        ],
    }
    (tmp_path / "run.json").write_text(json.dumps(run))

    summary_json, summary_md = write_lmcache_report(tmp_path)

    assert summary_json.exists()
    assert summary_md.exists()
    summary = json.loads(summary_json.read_text())
    assert summary["benchmark_kind"] == "lmcache"
    assert summary["correctness"]["all_fake_roundtrips_match"] is True
    markdown = summary_md.read_text()
    assert "LMCache Connector Summary" in markdown
    assert "Correctness Checks" in markdown
    assert "Timing Breakdown" in markdown


def test_lmcache_small_ci_runs_when_binaries_are_available(tmp_path: Path) -> None:
    daemon = Path("../bifrostd/target/debug/bifrost-daemon")
    store = Path("../bifrostd/target/debug/bifrost-store")
    if not daemon.exists() or not store.exists():
        pytest.skip("bifrost Rust binaries are not built")
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
    except OSError as exc:
        pytest.skip(f"loopback bind is unavailable in this environment: {exc}")

    run_dir = run_lmcache_scenario(
        Path("scenarios/lmcache_connector_small_ci.yaml"),
        runs_root=tmp_path,
        run_id="lmcache-small-ci-test",
    )

    assert (run_dir / "run.json").exists()
    assert (run_dir / "summary.json").exists()
    assert (run_dir / "summary.md").exists()
    summary = json.loads((run_dir / "summary.json").read_text())
    assert summary["benchmark_kind"] == "lmcache"
    assert summary["failure_count"] == 0
    assert summary["fsck_status"] == "clean"


def test_lmcache_opt_in_scenarios_skip_by_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BIFROST_RUN_REAL_LMCACHE_CONTEXTSTORM", raising=False)
    monkeypatch.delenv("BIFROST_RUN_VLLM_LMCACHE_CONTEXTSTORM", raising=False)

    run_dir = run_lmcache_scenario(
        Path("scenarios/lmcache_real_opt_in.yaml"),
        runs_root=tmp_path,
        run_id="lmcache-real-opt-in-skip-test",
    )

    summary = json.loads((run_dir / "summary.json").read_text())
    assert summary["benchmark_kind"] == "lmcache"
    assert summary["skip_count"] == 2
    assert summary["failure_count"] == 0

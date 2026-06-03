from __future__ import annotations

import json
import socket
from pathlib import Path

import pytest

from contextstorm.store_metrics import (
    parse_store_operation_metrics,
    summarize_store_metrics,
)
from contextstorm.store_report import write_store_report
from contextstorm.store_runner import load_store_scenario, run_store_scenario


def test_store_metrics_parser_normalizes_operation_record() -> None:
    record = {
        "operation": "evict",
        "repetition": 0,
        "exit_code": 0,
        "metrics": {
            "success": True,
            "eviction_duration_ms": 7,
            "objects_evicted": 2,
            "bytes_evicted": 1024,
            "pinned_not_evicted": True,
        },
    }

    metric = parse_store_operation_metrics(record)
    summary = summarize_store_metrics([metric])

    assert metric["operation"] == "evict"
    assert metric["eviction_duration_ms"] == 7
    assert summary["objects_evicted"] == 2
    assert summary["bytes_evicted"] == 1024
    assert summary["correctness"]["pinned_not_evicted"] is True


def test_store_small_ci_scenario_loads() -> None:
    scenario = load_store_scenario(Path("scenarios/store_small_ci.yaml"))

    assert scenario.name == "store_small_ci"
    assert scenario.object_count == 5
    assert scenario.object_size_bytes == 1048576
    assert scenario.chunk_size_bytes == 262144
    assert scenario.operations == (
        "put_objects",
        "list_objects",
        "query_objects",
        "get_objects",
        "fsck",
    )
    assert scenario.memory_tier_bytes == 0


def test_store_report_writes_summary_json_and_markdown(tmp_path: Path) -> None:
    run = {
        "benchmark_kind": "store",
        "scenario": {"name": "store-unit"},
        "environment": {"python_version": "3.x", "platform": "test"},
        "operations": [
            {
                "operation": "put_objects",
                "repetition": 0,
                "exit_code": 0,
                "metrics": {
                    "operation": "put_objects",
                    "repetition": 0,
                    "success": True,
                    "put_duration_ms": 12,
                    "objects_inserted": 1,
                    "bytes_committed": 4096,
                    "payload_roundtrip_match": True,
                },
            }
        ],
    }
    (tmp_path / "run.json").write_text(json.dumps(run))

    summary_json, summary_md = write_store_report(tmp_path)

    assert summary_json.exists()
    assert summary_md.exists()
    summary = json.loads(summary_json.read_text())
    assert summary["benchmark_kind"] == "store"
    assert summary["objects_inserted"] == 1
    assert "Per-Operation Metrics" in summary_md.read_text()


def test_store_small_ci_runs_when_binaries_are_available(tmp_path: Path) -> None:
    daemon = Path("../bifrostd/target/debug/bifrost-daemon")
    xfer = Path("../bifrostd/target/debug/bifrost-xfer")
    store = Path("../bifrostd/target/debug/bifrost-store")
    if not daemon.exists() or not xfer.exists() or not store.exists():
        pytest.skip("bifrost Rust binaries are not built")
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
    except OSError as exc:
        pytest.skip(f"loopback bind is unavailable in this environment: {exc}")

    run_dir = run_store_scenario(
        Path("scenarios/store_small_ci.yaml"),
        runs_root=tmp_path,
        run_id="store-small-ci-test",
    )

    assert (run_dir / "run.json").exists()
    assert (run_dir / "summary.json").exists()
    assert (run_dir / "summary.md").exists()
    summary = json.loads((run_dir / "summary.json").read_text())
    assert summary["benchmark_kind"] == "store"

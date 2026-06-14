from __future__ import annotations

import json
import socket
from pathlib import Path

import pytest

from contextstorm.model_metrics import (
    parse_model_operation_metrics,
    summarize_model_metrics,
)
from contextstorm.model_report import write_model_report
from contextstorm.model_runner import load_model_scenario, run_model_scenario


def test_model_metrics_parser_normalizes_operation_record() -> None:
    record = {
        "operation": "local_kv_roundtrip",
        "repetition": 0,
        "exit_code": 0,
        "metrics": {
            "success": True,
            "prefill_ms": 1.5,
            "kv_page_serialize_ms": 2.5,
            "page_count": 4,
            "total_payload_bytes": 2048,
            "logit_max_abs_error": 0.0,
            "continuation_match": True,
            "pages_rehydrated": 4,
        },
    }

    metric = parse_model_operation_metrics(record)
    summary = summarize_model_metrics([metric])

    assert metric["operation"] == "local_kv_roundtrip"
    assert metric["page_count"] == 4
    assert summary["page_count"] == 4
    assert summary["total_payload_bytes"] == 2048
    assert summary["correctness"]["continuation_match"] is True
    assert summary["correctness"]["logits_within_tolerance"] is True


def test_model_roundtrip_small_ci_scenario_loads() -> None:
    scenario = load_model_scenario(Path("scenarios/model_roundtrip_small_ci.yaml"))

    assert scenario.name == "model_roundtrip_small_ci"
    assert scenario.model["vocab_size"] == 128
    assert scenario.model["num_layers"] == 2
    assert scenario.model["num_heads"] == 2
    assert scenario.model["num_kv_heads"] == 2
    assert scenario.model["head_dim"] == 8
    assert scenario.model["dtype"] == "float32"
    assert scenario.model["seed"] == 1234
    assert scenario.prompt == "1 2 3 4 5 6 7 8"
    assert scenario.decode_tokens == 4
    assert scenario.block_size_tokens == 4
    assert scenario.operations == ("local_kv_roundtrip", "store_kv_roundtrip")


def test_model_report_writes_summary_json_and_markdown(tmp_path: Path) -> None:
    run = {
        "benchmark_kind": "model",
        "scenario": {
            "name": "model-unit",
            "model": {
                "vocab_size": 128,
                "max_seq_len": 128,
                "num_layers": 2,
                "num_heads": 2,
                "num_kv_heads": 2,
                "head_dim": 8,
                "dtype": "float32",
                "seed": 1234,
            },
        },
        "environment": {"python_version": "3.x", "platform": "test"},
        "operations": [
            {
                "operation": "local_kv_roundtrip",
                "repetition": 0,
                "exit_code": 0,
                "metrics": {
                    "operation": "local_kv_roundtrip",
                    "repetition": 0,
                    "success": True,
                    "page_count": 4,
                    "total_payload_bytes": 2048,
                    "logit_max_abs_error": 0.0,
                    "continuation_match": True,
                    "pages_rehydrated": 4,
                },
            }
        ],
    }
    (tmp_path / "run.json").write_text(json.dumps(run))

    summary_json, summary_md = write_model_report(tmp_path)

    assert summary_json.exists()
    assert summary_md.exists()
    summary = json.loads(summary_json.read_text())
    assert summary["benchmark_kind"] == "model"
    assert summary["page_count"] == 4
    markdown = summary_md.read_text()
    assert "Model Summary" in markdown
    assert "Correctness Status" in markdown


def test_model_roundtrip_small_ci_runs_when_binaries_are_available(tmp_path: Path) -> None:
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

    run_dir = run_model_scenario(
        Path("scenarios/model_roundtrip_small_ci.yaml"),
        runs_root=tmp_path,
        run_id="model-roundtrip-small-ci-test",
    )

    assert (run_dir / "run.json").exists()
    assert (run_dir / "summary.json").exists()
    assert (run_dir / "summary.md").exists()
    summary = json.loads((run_dir / "summary.json").read_text())
    assert summary["benchmark_kind"] == "model"
    assert summary["correctness"]["continuation_match"] is True
    assert summary["failure_count"] == 0

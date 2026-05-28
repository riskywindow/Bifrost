from __future__ import annotations

import json
from pathlib import Path

from contextstorm.report import write_report


def test_report_writes_summary_json_and_markdown(tmp_path: Path) -> None:
    run = {
        "scenario": {"name": "unit"},
        "environment": {"python_version": "3.x", "platform": "test"},
        "operations": [
            {
                "operation": "put",
                "repetition": 0,
                "metrics": {
                    "operation": "put",
                    "repetition": 0,
                    "success": True,
                    "transfer_duration_ms": 10,
                    "effective_throughput_mib_s": 1.0,
                    "bytes_sent": 10,
                    "bytes_received": 0,
                    "chunks_sent": 1,
                    "retries": 0,
                    "timeouts": 0,
                    "reason_code": None,
                    "committed_object_verified": True,
                    "get_payload_matches_put_payload": None,
                },
            }
        ],
    }
    (tmp_path / "run.json").write_text(json.dumps(run))

    summary_json, summary_md = write_report(tmp_path)

    assert summary_json.exists()
    assert summary_md.exists()
    summary = json.loads(summary_json.read_text())
    assert summary["success_count"] == 1
    assert "Per-Run Metrics" in summary_md.read_text()

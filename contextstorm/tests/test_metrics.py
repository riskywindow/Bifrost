from __future__ import annotations

from pathlib import Path

from contextstorm.metrics import load_trace_jsonl, summarize_trace_events


def test_metrics_parser_handles_trace_jsonl(tmp_path: Path) -> None:
    trace = tmp_path / "trace.jsonl"
    trace.write_text(
        "\n".join(
            [
                '{"event_type":"chunk_sent","bytes":10,"timestamp_unix_ms":1,"path_name":"primary"}',
                '{"event_type":"chunk_sent","bytes":20,"timestamp_unix_ms":2,"path_name":"primary"}',
                '{"event_type":"put_commit_rejected","reason_code":"payload_hash_mismatch","timestamp_unix_ms":3,"path_name":"primary"}',
            ]
        )
        + "\n"
    )

    summary = summarize_trace_events(load_trace_jsonl(trace))

    assert summary["bytes_sent"] == 30
    assert summary["chunks_sent"] == 2
    assert summary["reason_code"] == "payload_hash_mismatch"

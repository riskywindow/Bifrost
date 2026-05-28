"""Trace and command-output metrics for ContextStorm."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable


@dataclass
class OperationMetrics:
    operation: str
    repetition: int
    transfer_duration_ms: int
    effective_throughput_mib_s: float
    bytes_sent: int
    bytes_received: int
    chunks_sent: int
    retries: int
    timeouts: int
    success: bool
    reason_code: str | None
    committed_object_verified: bool
    get_payload_matches_put_payload: bool | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_trace_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text().splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            value = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{line_number}: invalid JSONL trace event") from exc
        if isinstance(value, dict):
            events.append(value)
    return events


def summarize_trace_events(events: Iterable[dict[str, Any]]) -> dict[str, Any]:
    summary = {
        "bytes_sent": 0,
        "bytes_received": 0,
        "chunks_sent": 0,
        "chunks_received": 0,
        "retries": 0,
        "timeouts": 0,
        "reason_code": None,
        "duration_ms": None,
    }
    durations: list[int] = []
    for event in events:
        event_type = event.get("event_type")
        byte_count = int(event.get("bytes") or 0)
        if event_type in {"chunk_sent", "get_chunk_sent"}:
            summary["bytes_sent"] += byte_count
            summary["chunks_sent"] += 1
        if event_type == "chunk_received":
            summary["bytes_received"] += byte_count
            summary["chunks_received"] += 1
        if event_type in {"chunk_retry", "chunk_retried"}:
            summary["retries"] += 1
        if event_type in {"chunk_timeout", "transfer_timeout"}:
            summary["timeouts"] += 1
        if event.get("reason_code"):
            summary["reason_code"] = event["reason_code"]
        if event.get("duration_ms") is not None:
            durations.append(int(event["duration_ms"]))
    if durations:
        summary["duration_ms"] = max(durations)
    return summary


def metrics_snapshot_totals(snapshot: dict[str, Any] | None) -> dict[str, int]:
    snapshot = snapshot or {}
    return {
        "bytes_sent": int(snapshot.get("bytes_sent_total") or 0),
        "bytes_received": int(snapshot.get("bytes_received_total") or 0),
        "chunks_sent": int(snapshot.get("chunks_sent_total") or 0),
        "retries": int(snapshot.get("chunks_retried_total") or 0),
        "timeouts": int(snapshot.get("chunk_timeouts_total") or 0),
    }


def throughput_mib_s(byte_count: int, duration_ms: int) -> float:
    if duration_ms <= 0:
        return 0.0
    return (byte_count / (1024 * 1024)) / (duration_ms / 1000)

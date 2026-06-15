"""In-process metrics and JSONL logging for the BIFROST LMCache connector."""

from __future__ import annotations

import json
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass
class ConnectorMetricsSnapshot:
    put_count: int = 0
    get_count: int = 0
    exists_count: int = 0
    list_count: int = 0
    close_count: int = 0
    put_error_count: int = 0
    get_error_count: int = 0
    serialization_error_count: int = 0
    validation_error_count: int = 0
    bytes_put: int = 0
    bytes_get: int = 0
    total_put_ms: float = 0.0
    total_get_ms: float = 0.0


class ConnectorMetrics:
    """Small thread-safe counter set for connector-local observability."""

    def __init__(self) -> None:
        self._snapshot = ConnectorMetricsSnapshot()
        self._lock = threading.Lock()

    def increment(self, name: str, amount: int = 1) -> None:
        with self._lock:
            setattr(self._snapshot, name, getattr(self._snapshot, name) + amount)

    def add_duration_ms(self, name: str, duration_ms: float) -> None:
        with self._lock:
            setattr(self._snapshot, name, getattr(self._snapshot, name) + duration_ms)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return asdict(self._snapshot)


class ConnectorJsonlLogger:
    """Append connector events as JSONL when configured."""

    def __init__(self, path: str | Path | None) -> None:
        self.path = Path(path) if path else None
        self._lock = threading.Lock()

    def emit(
        self,
        event_name: str,
        *,
        operation: str,
        opaque_engine_key_hash: str | None = None,
        object_id: str | None = None,
        bytes_count: int | None = None,
        duration_ms: float | None = None,
        reason_code: str | None = None,
    ) -> None:
        if self.path is None:
            return
        event: dict[str, Any] = {
            "event": event_name,
            "timestamp_unix_ms": int(time.time() * 1000),
            "operation": operation,
        }
        if opaque_engine_key_hash is not None:
            event["opaque_engine_key_hash"] = opaque_engine_key_hash
        if object_id is not None:
            event["object_id"] = object_id
        if bytes_count is not None:
            event["bytes"] = bytes_count
        if duration_ms is not None:
            event["duration_ms"] = duration_ms
        if reason_code is not None:
            event["reason_code"] = reason_code
        encoded = json.dumps(event, sort_keys=True, separators=(",", ":"))
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(encoded)
                handle.write("\n")


def monotonic_ms() -> float:
    return time.perf_counter() * 1000.0


__all__ = [
    "ConnectorJsonlLogger",
    "ConnectorMetrics",
    "ConnectorMetricsSnapshot",
    "monotonic_ms",
]

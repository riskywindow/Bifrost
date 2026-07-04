"""In-process metrics and JSONL traces for the BIFROST vLLM connector."""

from __future__ import annotations

import json
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass
class ConnectorMetricsSnapshot:
    init_count: int = 0
    register_kv_caches_count: int = 0
    start_load_kv_count: int = 0
    wait_for_layer_load_count: int = 0
    save_kv_layer_count: int = 0
    wait_for_save_count: int = 0
    get_finished_count: int = 0
    get_block_ids_with_load_errors_count: int = 0
    shutdown_count: int = 0
    get_kv_connector_stats_count: int = 0
    unsupported_operation_count: int = 0
    lifecycle_error_count: int = 0
    save_success_count: int = 0
    save_failure_count: int = 0
    load_hit_count: int = 0
    load_miss_count: int = 0
    load_recompute_count: int = 0
    save_skipped_count: int = 0
    load_skipped_count: int = 0
    save_error_count: int = 0
    load_error_count: int = 0
    daemon_error_count: int = 0
    validation_error_count: int = 0
    serialization_error_count: int = 0
    scheduler_metadata_error_count: int = 0
    store_commit_error_count: int = 0
    bytes_saved: int = 0
    bytes_loaded: int = 0
    objects_saved: int = 0
    total_save_ms: float = 0.0
    total_load_ms: float = 0.0
    last_error_reason: str | None = None


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

    def set_last_error_reason(self, reason: str | None) -> None:
        with self._lock:
            self._snapshot.last_error_reason = reason

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return asdict(self._snapshot)


class ConnectorJsonlLogger:
    """Append connector lifecycle events as JSONL when configured."""

    def __init__(self, path: str | Path | None) -> None:
        self.path = Path(path) if path else None
        self._lock = threading.Lock()

    def emit(
        self,
        event_name: str,
        *,
        operation: str,
        connector_instance_id: str | None = None,
        lifecycle_method: str | None = None,
        reason_code: str | None = None,
        bytes_count: int | None = None,
        duration_ms: float | None = None,
        object_id: str | None = None,
        blob_key_hash: str | None = None,
        request_id: str | None = None,
        layer_name: str | None = None,
        block_ids: list[int] | tuple[int, ...] | None = None,
    ) -> None:
        if self.path is None:
            return
        event: dict[str, Any] = {
            "event": event_name,
            "timestamp_unix_ms": int(time.time() * 1000),
            "operation": operation,
        }
        if connector_instance_id is not None:
            event["connector_instance_id"] = connector_instance_id
        if lifecycle_method is not None:
            event["lifecycle_method"] = lifecycle_method
        if reason_code is not None:
            event["reason_code"] = reason_code
        if bytes_count is not None:
            event["bytes"] = bytes_count
        if duration_ms is not None:
            event["duration_ms"] = duration_ms
        if object_id is not None:
            event["object_id"] = object_id
        if blob_key_hash is not None:
            event["blob_key_hash"] = blob_key_hash
        if request_id is not None:
            event["request_id"] = request_id
        if layer_name is not None:
            event["layer_name"] = layer_name
        if block_ids is not None:
            event["block_ids"] = list(block_ids)
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

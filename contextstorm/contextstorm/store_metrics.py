"""Store benchmark metrics for ContextStorm."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable


STORE_METRIC_KEYS = (
    "put_duration_ms",
    "get_duration_ms",
    "has_latency_ms",
    "list_latency_ms",
    "query_latency_ms",
    "inspect_latency_ms",
    "fsck_duration_ms",
    "eviction_duration_ms",
    "objects_inserted",
    "objects_evicted",
    "objects_pinned",
    "bytes_committed",
    "bytes_evicted",
    "manifest_completeness",
    "store_bytes_before",
    "store_bytes_after",
    "memory_tier_hits",
    "memory_tier_misses",
)


@dataclass
class StoreOperationMetrics:
    operation: str
    repetition: int
    success: bool
    reason_code: str | None = None
    put_duration_ms: int = 0
    get_duration_ms: int = 0
    has_latency_ms: int = 0
    list_latency_ms: int = 0
    query_latency_ms: int = 0
    inspect_latency_ms: int = 0
    fsck_duration_ms: int = 0
    eviction_duration_ms: int = 0
    objects_inserted: int = 0
    objects_evicted: int = 0
    objects_pinned: int = 0
    bytes_committed: int = 0
    bytes_evicted: int = 0
    manifest_completeness: float | None = None
    store_bytes_before: int = 0
    store_bytes_after: int = 0
    memory_tier_hits: int = 0
    memory_tier_misses: int = 0
    payload_roundtrip_match: bool | None = None
    pinned_not_evicted: bool | None = None
    fsck_clean_after_run: bool | None = None
    manifest_completeness_expected: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def summarize_store_metrics(metrics: Iterable[dict[str, Any]]) -> dict[str, Any]:
    items = list(metrics)
    summary: dict[str, Any] = {
        "operation_count": len(items),
        "success_count": sum(1 for item in items if item.get("success")),
        "failure_count": sum(1 for item in items if not item.get("success")),
        "correctness": {
            "payload_roundtrip_match": _all_known_true(
                items, "payload_roundtrip_match"
            ),
            "pinned_not_evicted": _all_known_true(items, "pinned_not_evicted"),
            "fsck_clean_after_run": _all_known_true(items, "fsck_clean_after_run"),
            "manifest_completeness_expected": _all_known_true(
                items, "manifest_completeness_expected"
            ),
        },
    }
    last_value_keys = {
        "store_bytes_before",
        "store_bytes_after",
        "memory_tier_hits",
        "memory_tier_misses",
    }
    for key in STORE_METRIC_KEYS:
        values = [item.get(key) for item in items if item.get(key) is not None]
        if key == "manifest_completeness":
            summary[key] = values[-1] if values else None
        elif key == "store_bytes_before":
            summary[key] = next((int(value or 0) for value in values if value), 0)
        elif key in last_value_keys:
            summary[key] = int(values[-1] or 0) if values else 0
        else:
            summary[key] = sum(int(value or 0) for value in values)
    return summary


def parse_store_operation_metrics(operation: dict[str, Any]) -> dict[str, Any]:
    """Return a normalized store metrics mapping from a run operation record."""

    metric = dict(operation.get("metrics") or {})
    metric.setdefault("operation", operation.get("operation"))
    metric.setdefault("repetition", operation.get("repetition", 0))
    metric.setdefault("success", operation.get("exit_code", 1) == 0)
    metric.setdefault("reason_code", None)
    for key in STORE_METRIC_KEYS:
        metric.setdefault(key, None if key == "manifest_completeness" else 0)
    return metric


def _all_known_true(items: list[dict[str, Any]], key: str) -> bool | None:
    known = [item[key] for item in items if item.get(key) is not None]
    if not known:
        return None
    return all(bool(value) for value in known)

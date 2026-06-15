"""LMCache connector workload metrics for ContextStorm Phase 5."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable


LMCACHE_METRIC_KEYS = (
    "connector_put_ms",
    "connector_exists_ms",
    "connector_get_ms",
    "connector_list_ms",
    "connector_close_ms",
    "serialization_ms",
    "deserialization_ms",
    "object_count",
    "bytes_put",
    "bytes_get",
    "roundtrip_match_count",
    "missing_count",
    "validation_error_count",
    "corrupt_rejection_count",
    "bifrost_store_object_count",
    "batched_put_ms",
    "batched_contains_ms",
    "batched_get_ms",
)


@dataclass
class LMCacheOperationMetrics:
    operation: str
    repetition: int
    success: bool
    reason_code: str | None = None
    skipped: bool = False
    connector_put_ms: float = 0.0
    connector_exists_ms: float = 0.0
    connector_get_ms: float = 0.0
    connector_list_ms: float = 0.0
    connector_close_ms: float = 0.0
    serialization_ms: float = 0.0
    deserialization_ms: float = 0.0
    object_count: int = 0
    bytes_put: int = 0
    bytes_get: int = 0
    roundtrip_match_count: int = 0
    missing_count: int = 0
    validation_error_count: int = 0
    corrupt_rejection_count: int = 0
    bifrost_store_object_count: int = 0
    fsck_status: str | None = None
    exists_true_after_put: bool | None = None
    missing_returns_none: bool | None = None
    all_fake_roundtrips_match: bool | None = None
    fsck_clean: bool | None = None
    batched_put_ms: float = 0.0
    batched_contains_ms: float = 0.0
    batched_get_ms: float = 0.0
    batch_contains_match: bool | None = None
    batch_get_match: bool | None = None
    corrupt_object_rejected: bool | None = None
    failures: list[dict[str, str]] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def parse_lmcache_operation_metrics(operation: dict[str, Any]) -> dict[str, Any]:
    """Return a normalized LMCache metrics mapping from a run operation record."""

    metric = dict(operation.get("metrics") or {})
    metric.setdefault("operation", operation.get("operation"))
    metric.setdefault("repetition", operation.get("repetition", 0))
    metric.setdefault("success", operation.get("exit_code", 1) == 0)
    metric.setdefault("reason_code", None)
    metric.setdefault("skipped", False)
    for key in LMCACHE_METRIC_KEYS:
        metric.setdefault(key, 0)
    metric.setdefault("fsck_status", None)
    metric.setdefault("exists_true_after_put", None)
    metric.setdefault("missing_returns_none", None)
    metric.setdefault("all_fake_roundtrips_match", None)
    metric.setdefault("fsck_clean", None)
    metric.setdefault("batch_contains_match", None)
    metric.setdefault("batch_get_match", None)
    metric.setdefault("corrupt_object_rejected", None)
    metric.setdefault("failures", [])
    return metric


def summarize_lmcache_metrics(metrics: Iterable[dict[str, Any]]) -> dict[str, Any]:
    items = list(metrics)
    summary: dict[str, Any] = {
        "operation_count": len(items),
        "success_count": sum(
            1 for item in items if item.get("success") and not item.get("skipped")
        ),
        "failure_count": sum(
            1 for item in items if not item.get("success") and not item.get("skipped")
        ),
        "skip_count": sum(1 for item in items if item.get("skipped")),
        "correctness": {
            "all_fake_roundtrips_match": _all_known_true(
                items, "all_fake_roundtrips_match"
            ),
            "exists_true_after_put": _all_known_true(items, "exists_true_after_put"),
            "missing_returns_none": _all_known_true(items, "missing_returns_none"),
            "fsck_clean": _all_known_true(items, "fsck_clean"),
            "batch_contains_match": _all_known_true(items, "batch_contains_match"),
            "batch_get_match": _all_known_true(items, "batch_get_match"),
            "corrupt_object_rejected": _all_known_true(items, "corrupt_object_rejected"),
        },
        "failures": [
            failure
            for item in items
            for failure in (item.get("failures") or [])
        ],
    }
    for key in LMCACHE_METRIC_KEYS:
        values = [item.get(key) for item in items if item.get(key) is not None]
        if key in {
            "object_count",
            "bytes_put",
            "bytes_get",
            "roundtrip_match_count",
            "missing_count",
            "validation_error_count",
            "corrupt_rejection_count",
            "bifrost_store_object_count",
        }:
            if key == "bifrost_store_object_count":
                summary[key] = max((int(value or 0) for value in values), default=0)
            else:
                summary[key] = sum(int(value or 0) for value in values)
        else:
            summary[key] = sum(float(value or 0.0) for value in values)
    fsck_values = [item.get("fsck_status") for item in items if item.get("fsck_status")]
    summary["fsck_status"] = fsck_values[-1] if fsck_values else None
    return summary


def _all_known_true(items: list[dict[str, Any]], key: str) -> bool | None:
    known = [item[key] for item in items if item.get(key) is not None]
    if not known:
        return None
    return all(bool(value) for value in known)

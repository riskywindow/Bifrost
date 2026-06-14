"""Model-facing ContextStorm metrics for Phase 4 correctness workloads."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable


MODEL_METRIC_KEYS = (
    "prefill_ms",
    "kv_page_serialize_ms",
    "page_count",
    "total_payload_bytes",
    "store_put_ms",
    "store_get_ms",
    "manifest_create_ms",
    "manifest_check_ms",
    "rehydrate_ms",
    "decode_resume_ms",
    "logit_max_abs_error",
    "pages_stored",
    "pages_rehydrated",
)


@dataclass
class ModelOperationMetrics:
    operation: str
    repetition: int
    success: bool
    reason_code: str | None = None
    prefill_ms: float = 0.0
    kv_page_serialize_ms: float = 0.0
    page_count: int = 0
    total_payload_bytes: int = 0
    store_put_ms: float = 0.0
    store_get_ms: float = 0.0
    manifest_create_ms: float = 0.0
    manifest_check_ms: float = 0.0
    rehydrate_ms: float = 0.0
    decode_resume_ms: float = 0.0
    logit_max_abs_error: float | None = None
    continuation_match: bool | None = None
    manifest_completeness: str | float | None = None
    pages_stored: int = 0
    pages_rehydrated: int = 0
    failures: list[dict[str, str]] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def parse_model_operation_metrics(operation: dict[str, Any]) -> dict[str, Any]:
    """Return a normalized model metrics mapping from a run operation record."""

    metric = dict(operation.get("metrics") or {})
    metric.setdefault("operation", operation.get("operation"))
    metric.setdefault("repetition", operation.get("repetition", 0))
    metric.setdefault("success", operation.get("exit_code", 1) == 0)
    metric.setdefault("reason_code", None)
    for key in MODEL_METRIC_KEYS:
        metric.setdefault(
            key,
            None if key == "logit_max_abs_error" else 0,
        )
    metric.setdefault("continuation_match", None)
    metric.setdefault("manifest_completeness", None)
    metric.setdefault("failures", [])
    return metric


def summarize_model_metrics(metrics: Iterable[dict[str, Any]]) -> dict[str, Any]:
    items = list(metrics)
    summary: dict[str, Any] = {
        "operation_count": len(items),
        "success_count": sum(1 for item in items if item.get("success")),
        "failure_count": sum(1 for item in items if not item.get("success")),
        "correctness": {
            "continuation_match": _all_known_true(items, "continuation_match"),
            "logits_within_tolerance": _all_logits_within_tolerance(items),
            "manifest_complete": _all_manifest_complete(items),
        },
        "failures": [
            failure
            for item in items
            for failure in (item.get("failures") or [])
        ],
    }
    for key in MODEL_METRIC_KEYS:
        values = [item.get(key) for item in items if item.get(key) is not None]
        if key == "logit_max_abs_error":
            numeric = [float(value) for value in values]
            summary[key] = max(numeric) if numeric else None
        else:
            summary[key] = sum(float(value or 0) for value in values)
            if key in {
                "page_count",
                "total_payload_bytes",
                "pages_stored",
                "pages_rehydrated",
            }:
                summary[key] = int(summary[key])
    completeness = [
        item.get("manifest_completeness")
        for item in items
        if item.get("manifest_completeness") is not None
    ]
    summary["manifest_completeness"] = completeness[-1] if completeness else None
    return summary


def _all_known_true(items: list[dict[str, Any]], key: str) -> bool | None:
    known = [item[key] for item in items if item.get(key) is not None]
    if not known:
        return None
    return all(bool(value) for value in known)


def _all_logits_within_tolerance(items: list[dict[str, Any]]) -> bool | None:
    known = [
        float(item["logit_max_abs_error"])
        for item in items
        if item.get("logit_max_abs_error") is not None
    ]
    if not known:
        return None
    return all(value <= 1e-6 for value in known)


def _all_manifest_complete(items: list[dict[str, Any]]) -> bool | None:
    known = [
        item.get("manifest_completeness")
        for item in items
        if item.get("manifest_completeness") is not None
    ]
    if not known:
        return None
    return all(value in {"complete", 1.0, "1.0", True} for value in known)

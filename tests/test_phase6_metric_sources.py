from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
for source_path in (
    REPO_ROOT / "bifrost_py",
    REPO_ROOT / "integrations" / "lmcache_bifrost",
):
    text = str(source_path)
    if text not in sys.path:
        sys.path.insert(0, text)

from bifrost_serving.collectors import (  # noqa: E402
    BifrostMetricsCollector,
    LMCACHE_METRIC_NAMES,
    extract_lmcache_metric_records,
    extract_lmcache_metrics,
)
from bifrost_serving.metrics import (  # noqa: E402
    MetricSource,
    merge_metric_maps_preserving_source,
    metric_value,
)


def test_lmcache_exact_metrics_and_unknown_raw_are_preserved() -> None:
    raw = {
        "lmcache:num_retrieve_requests": 7,
        "lmcache:num_store_requests": 3,
        "lmcache:num_requested_tokens": 100,
        "lmcache:num_hit_tokens": 60,
        "lmcache:lookup_hit_rate": 0.5,
        "lmcache:future_metric": 42,
    }

    metrics = extract_lmcache_metrics(raw)
    records = extract_lmcache_metric_records(raw, source=MetricSource.LMCACHE_PROMETHEUS)

    assert metrics["lmcache:num_retrieve_requests"] == 7
    assert metrics["lmcache:num_store_requests"] == 3
    assert metrics["lmcache:num_lookup_requests"] is None
    assert set(LMCACHE_METRIC_NAMES).issubset(records)
    assert records["lmcache:num_lookup_requests"]["status"] == "unavailable"
    assert records["lmcache:num_lookup_requests"]["source"] == "unavailable"
    assert records["lmcache:num_retrieve_requests"]["source"] == "lmcache_prometheus"


def test_connector_jsonl_source_and_event_counts_are_authoritative(tmp_path: Path) -> None:
    jsonl = tmp_path / "connector.jsonl"
    jsonl.write_text(
        "\n".join(
            [
                json.dumps({"event": "connector_put_completed", "operation": "put", "bytes": 11}),
                json.dumps({"event": "connector_get_completed", "operation": "get", "bytes": 5}),
                json.dumps({"event": "connector_error", "operation": "get", "reason_code": "missing"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    snapshot = BifrostMetricsCollector(
        client_factory=lambda: FakeClient(),
        connector_metrics_jsonl_path=jsonl,
    ).snapshot()

    connector = snapshot["connector_metrics"]
    assert connector["source"] == "bifrost_connector_jsonl"
    assert connector["stats"]["put_count"] == 1
    assert connector["stats"]["get_count"] == 1
    assert connector["stats"]["get_error_count"] == 1
    assert connector["metrics"]["bytes_get"]["source"] == "bifrost_connector_jsonl"


def test_metric_provenance_survives_aggregation_and_synthetic_cannot_masquerade() -> None:
    synthetic = {
        "hit_count": metric_value("hit_count", 9, MetricSource.SYNTHETIC_FAKE_SERVER),
    }
    connector = {
        "hit_count": metric_value("hit_count", 2, MetricSource.BIFROST_CONNECTOR_JSONL),
    }

    merged = merge_metric_maps_preserving_source(synthetic, connector)

    assert [entry["source"] for entry in merged["hit_count"]] == [
        "synthetic_fake_server",
        "bifrost_connector_jsonl",
    ]
    assert merged["hit_count"][0]["source"] != MetricSource.BIFROST_CONNECTOR_JSONL.value


class FakeClient:
    def connect(self) -> None:
        return None

    def close(self) -> None:
        return None

    def stats(self) -> dict[str, int]:
        return {"object_count": 1, "total_logical_bytes": 11}

    def list_objects(self) -> list[dict[str, object]]:
        return [
            {
                "object_id": "o",
                "object_type": "opaque_engine_blob",
                "engine_name": "lmcache",
                "integration_name": "lmcache_bifrost_remote_storage",
            }
        ]

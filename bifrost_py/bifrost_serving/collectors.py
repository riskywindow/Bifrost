"""Best-effort Phase 6 metrics collectors.

Collectors preserve raw source data and return structured unavailable/error
snapshots instead of failing a benchmark run when optional services are absent.
"""

from __future__ import annotations

import dataclasses
import json
import subprocess
import urllib.error
import urllib.request
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .metrics import MetricSource, metric_value, source_delta, stats_delta

ClientFactory = Callable[[], Any]

LMCACHE_METRIC_NAMES: tuple[str, ...] = (
    "lmcache:num_retrieve_requests",
    "lmcache:num_store_requests",
    "lmcache:num_lookup_requests",
    "lmcache:num_requested_tokens",
    "lmcache:num_hit_tokens",
    "lmcache:num_stored_tokens",
    "lmcache:num_lookup_tokens",
    "lmcache:num_lookup_hits",
    "lmcache:retrieve_hit_rate",
    "lmcache:lookup_hit_rate",
    "lmcache:time_to_retrieve",
    "lmcache:time_to_store",
    "lmcache:time_to_lookup",
)


@dataclass(slots=True)
class BifrostMetricsCollector:
    endpoint: str | None = None
    timeout_seconds: float = 5.0
    fsck_timeout_seconds: float = 120.0
    client_factory: ClientFactory | None = None
    collect_fsck: bool = False
    fsck_command: list[str] | None = None
    connector_metrics_jsonl_path: Path | None = None
    connector_metrics_url: str | None = None
    opaque_integration_name: str = "lmcache_bifrost_remote_storage"

    _before: dict[str, Any] | None = None
    _after: dict[str, Any] | None = None

    def snapshot_before(self) -> dict[str, Any]:
        self._before = self.snapshot()
        return self._before

    def snapshot_after(self) -> dict[str, Any]:
        self._after = self.snapshot()
        return self._after

    def delta(self) -> dict[str, Any] | None:
        return bifrost_snapshot_delta(self._before, self._after)

    def snapshot(self) -> dict[str, Any]:
        if self.client_factory is None and not self.endpoint:
            return _unavailable("bifrost", "bifrost_endpoint was not provided")
        try:
            client = self._make_client()
            try:
                _connect_if_available(client)
                raw_stats = _to_plain(client.stats())
                objects = _collect_objects(client)
            finally:
                _close_if_available(client)
        except Exception as exc:
            return {
                "schema_version": "bifrost.collector.bifrost.v1",
                "collector": "bifrost",
                "status": "error",
                "endpoint": self.endpoint,
                "reason": str(exc),
            }

        stats = raw_stats if isinstance(raw_stats, dict) else {}
        opaque_count = _opaque_lmcache_count(objects, self.opaque_integration_name)
        connector_metrics = _collect_connector_metrics(
            jsonl_path=self.connector_metrics_jsonl_path,
            url=self.connector_metrics_url,
            timeout_seconds=self.timeout_seconds,
        )
        snapshot = {
            "schema_version": "bifrost.collector.bifrost.v1",
            "collector": "bifrost",
            "source": MetricSource.BIFROST_STORE_STATS.value,
            "status": "ok",
            "endpoint": self.endpoint,
            "stats": stats,
            "metrics": {
                key: metric_value(key, value, MetricSource.BIFROST_STORE_STATS)
                for key, value in stats.items()
                if _is_number(value)
            },
            "raw": {"stats": raw_stats, "objects": objects},
            "object_count": _first_number(stats, "object_count"),
            "bytes_stored": _first_number(stats, "total_logical_bytes"),
            "opaque_lmcache_object_count": opaque_count,
            "fsck": self._collect_fsck(),
            "connector_metrics": connector_metrics,
        }
        return snapshot

    def _make_client(self) -> Any:
        if self.client_factory is not None:
            return self.client_factory()
        from bifrost_client import BifrostClient, BifrostClientConfig

        return BifrostClient(
            config=BifrostClientConfig(
                endpoint=str(self.endpoint),
                timeout_seconds=self.timeout_seconds,
            )
        )

    def _collect_fsck(self) -> dict[str, Any]:
        if not self.collect_fsck:
            return {"status": "skipped", "reason": "collect_fsck is false"}
        if not self.fsck_command:
            return {"status": "unavailable", "reason": "fsck_command was not provided"}
        try:
            result = subprocess.run(
                self.fsck_command,
                text=True,
                capture_output=True,
                timeout=max(1.0, self.fsck_timeout_seconds),
                check=False,
            )
        except Exception as exc:
            return {"status": "error", "reason": str(exc)}
        raw_stdout = result.stdout.strip()
        parsed: Any = None
        if raw_stdout:
            try:
                parsed = json.loads(raw_stdout)
            except json.JSONDecodeError:
                parsed = None
        status = "ok" if result.returncode == 0 else "error"
        return {
            "status": status,
            "returncode": result.returncode,
            "raw": parsed if parsed is not None else raw_stdout,
            "stderr": result.stderr.strip(),
        }


@dataclass(slots=True)
class LMCacheMetricsCollector:
    metrics_url: str | None = None
    timeout_seconds: float = 2.0
    _before: dict[str, Any] | None = None
    _after: dict[str, Any] | None = None

    def snapshot_before(self) -> dict[str, Any]:
        self._before = self.snapshot()
        return self._before

    def snapshot_after(self) -> dict[str, Any]:
        self._after = self.snapshot()
        return self._after

    def delta(self) -> dict[str, Any] | None:
        return stats_delta(_known_metrics(self._before), _known_metrics(self._after))

    def snapshot(self) -> dict[str, Any]:
        if not self.metrics_url:
            return _unavailable("lmcache", "LMCache metrics endpoint is not configured")
        raw = _http_get_metrics(self.metrics_url, self.timeout_seconds)
        if raw["status"] != "ok":
            return {"collector": "lmcache", **raw}
        source = _metrics_endpoint_source(raw.get("raw"), lmcache=True)
        known = extract_lmcache_metrics(raw.get("raw"))
        return {
            "schema_version": "bifrost.collector.lmcache.v1",
            "collector": "lmcache",
            "source": source.value,
            "status": "ok",
            "endpoint": self.metrics_url,
            "raw": raw["raw"],
            "metrics": known,
            "metric_records": extract_lmcache_metric_records(raw.get("raw"), source=source),
            "unknown_raw_metrics": _unknown_lmcache_metrics(raw.get("raw")),
        }


@dataclass(slots=True)
class VLLMMetricsCollector:
    metrics_url: str | None = None
    timeout_seconds: float = 2.0
    _before: dict[str, Any] | None = None
    _after: dict[str, Any] | None = None

    def snapshot_before(self) -> dict[str, Any]:
        self._before = self.snapshot()
        return self._before

    def snapshot_after(self) -> dict[str, Any]:
        self._after = self.snapshot()
        return self._after

    def delta(self) -> dict[str, Any] | None:
        return stats_delta(_known_metrics(self._before), _known_metrics(self._after))

    def snapshot(self) -> dict[str, Any]:
        if not self.metrics_url:
            return _unavailable("vllm", "vLLM metrics endpoint is not configured")
        raw = _http_get_metrics(self.metrics_url, self.timeout_seconds)
        if raw["status"] != "ok":
            return {"collector": "vllm", **raw}
        known = extract_vllm_metrics(raw.get("raw"))
        return {
            "schema_version": "bifrost.collector.vllm.v1",
            "collector": "vllm",
            "source": MetricSource.VLLM_METRICS_ENDPOINT.value,
            "status": "ok",
            "endpoint": self.metrics_url,
            "raw": raw["raw"],
            "metrics": known,
        }


def bifrost_snapshot_delta(
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not before or not after or before.get("status") != "ok" or after.get("status") != "ok":
        return None
    delta = stats_delta(_dict(before.get("stats")), _dict(after.get("stats"))) or {}
    for key in ("object_count", "bytes_stored", "opaque_lmcache_object_count"):
        before_value = before.get(key)
        after_value = after.get(key)
        if _is_number(before_value) and _is_number(after_value):
            delta[key] = after_value - before_value
    connector_delta = stats_delta(
        _connector_stats(before.get("connector_metrics")),
        _connector_stats(after.get("connector_metrics")),
    )
    if connector_delta:
        delta.update(connector_delta)
        delta["connector_metrics_delta"] = connector_delta
        delta["connector_metrics_delta_by_source"] = source_delta(
            _connector_stats(before.get("connector_metrics")),
            _connector_stats(after.get("connector_metrics")),
            source=after.get("connector_metrics", {}).get("source", MetricSource.UNAVAILABLE.value),
        )
    if "total_logical_bytes" in delta:
        delta.setdefault("bytes_stored", delta["total_logical_bytes"])
    delta["store_delta_by_source"] = source_delta(
        _dict(before.get("stats")),
        _dict(after.get("stats")),
        source=MetricSource.BIFROST_STORE_STATS,
    )
    return delta


def extract_lmcache_metrics(raw: Any) -> dict[str, Any]:
    values = _flatten_numeric(raw)
    exact = _extract_lmcache_exact(values)
    generic = _extract_by_patterns(
        values,
        {
            "hit_count": ("hit",),
            "miss_count": ("miss",),
            "local_storage_hits": ("local", "hit"),
            "remote_storage_hits": ("remote", "hit"),
            "remote_storage_puts": ("remote", "put"),
            "evictions": ("evict",),
        },
    )
    return {**generic, **exact}


def extract_lmcache_metric_records(
    raw: Any,
    *,
    source: MetricSource | str,
) -> dict[str, dict[str, Any]]:
    values = _flatten_numeric(raw)
    matched_names = _lmcache_exact_matches(values)
    source_value = source if isinstance(source, MetricSource) else MetricSource(str(source))
    return {
        name: metric_value(name, values.get(matched_names.get(name, "")), source_value, raw_name=matched_names.get(name))
        for name in LMCACHE_METRIC_NAMES
    }


def extract_vllm_metrics(raw: Any) -> dict[str, Any]:
    values = _flatten_numeric(raw)
    return _extract_by_patterns(
        values,
        {
            "requests": ("request",),
            "running_requests": ("running", "request"),
            "waiting_requests": ("waiting", "request"),
            "gpu_cache_usage": ("gpu", "cache"),
            "prefix_cache_hits": ("prefix", "hit"),
            "prefix_cache_queries": ("prefix", "quer"),
        },
    )


def _collect_connector_metrics(
    *,
    jsonl_path: Path | None,
    url: str | None,
    timeout_seconds: float,
) -> dict[str, Any]:
    if url:
        raw = _http_get_metrics(url, timeout_seconds)
        if raw["status"] == "ok":
            return {
                "status": "ok",
                "source": MetricSource.BIFROST_CONNECTOR_METRICS.value,
                "raw": raw["raw"],
                "stats": _flatten_numeric(raw["raw"]),
            }
        return {
            "status": raw["status"],
            "source": MetricSource.UNAVAILABLE.value,
            "reason": raw.get("reason"),
        }
    if jsonl_path is None:
        return {
            "status": "unavailable",
            "source": MetricSource.UNAVAILABLE.value,
            "reason": "connector metrics source was not configured",
        }
    if not jsonl_path.exists():
        return {
            "status": "unavailable",
            "source": MetricSource.UNAVAILABLE.value,
            "path": str(jsonl_path),
            "reason": "file does not exist",
        }
    stats: Counter[str] = Counter(
        {
            "put_count": 0,
            "get_count": 0,
            "exists_count": 0,
            "list_count": 0,
            "put_error_count": 0,
            "get_error_count": 0,
            "exists_error_count": 0,
            "list_error_count": 0,
            "bytes_put": 0,
            "bytes_get": 0,
            "total_put_ms": 0,
            "total_get_ms": 0,
        }
    )
    events: list[dict[str, Any]] = []
    for line in jsonl_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            stats["parse_error_count"] += 1
            continue
        if not isinstance(event, dict):
            stats["parse_error_count"] += 1
            continue
        events.append(event)
        name = str(event.get("event") or "")
        operation = str(event.get("operation") or "")
        if name == "connector_error":
            stats[f"{operation}_error_count" if operation else "connector_error_count"] += 1
        elif name.endswith("_completed") and operation:
            stats[f"{operation}_count"] += 1
        elif name == "connector_exists":
            stats["exists_count"] += 1
        bytes_value = event.get("bytes")
        if _is_number(bytes_value) and operation:
            stats[f"bytes_{operation}"] += bytes_value
        duration_value = event.get("duration_ms")
        if _is_number(duration_value) and operation:
            stats[f"total_{operation}_ms"] += duration_value
    return {
        "status": "ok",
        "source": MetricSource.BIFROST_CONNECTOR_JSONL.value,
        "path": str(jsonl_path),
        "stats": dict(stats),
        "metrics": {
            key: metric_value(key, value, MetricSource.BIFROST_CONNECTOR_JSONL)
            for key, value in stats.items()
            if _is_number(value)
        },
        "raw": events,
    }


def _http_get_metrics(url: str, timeout_seconds: float) -> dict[str, Any]:
    try:
        request = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310
            body = response.read().decode("utf-8", errors="replace")
            content_type = response.headers.get("content-type", "")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return {"status": "unavailable", "endpoint": url, "reason": str(exc)}
    try:
        raw: Any = json.loads(body)
    except json.JSONDecodeError:
        raw = {
            "content_type": content_type,
            "text": body,
            "prometheus": _parse_prometheus_text(body),
        }
    return {"status": "ok", "endpoint": url, "raw": raw}


def _parse_prometheus_text(text: str) -> dict[str, float]:
    metrics: dict[str, float] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        name_value = stripped.split(None, 1)
        if len(name_value) != 2:
            continue
        name = name_value[0].split("{", 1)[0]
        try:
            metrics[name] = float(name_value[1])
        except ValueError:
            continue
    return metrics


def _metrics_endpoint_source(raw: Any, *, lmcache: bool) -> MetricSource:
    if lmcache:
        if isinstance(raw, dict) and isinstance(raw.get("prometheus"), dict):
            return MetricSource.LMCACHE_PROMETHEUS
        return MetricSource.LMCACHE_INTERNAL_API
    return MetricSource.VLLM_METRICS_ENDPOINT


def _collect_objects(client: Any) -> list[dict[str, Any]]:
    try:
        objects = client.list_objects()
    except Exception:
        return []
    return [_to_plain(item) for item in objects]


def _opaque_lmcache_count(objects: list[dict[str, Any]], integration_name: str) -> int:
    count = 0
    for item in objects:
        if item.get("object_type") != "opaque_engine_blob":
            continue
        integration = item.get("integration_name")
        engine = item.get("engine_name")
        if integration in (None, integration_name) or engine == "lmcache":
            count += 1
    return count


def _flatten_numeric(raw: Any, prefix: str = "") -> dict[str, float]:
    if isinstance(raw, dict) and "prometheus" in raw and isinstance(raw["prometheus"], dict):
        return _flatten_numeric(raw["prometheus"], prefix)
    values: dict[str, float] = {}
    if isinstance(raw, dict):
        for key, value in raw.items():
            child_key = f"{prefix}.{key}" if prefix else str(key)
            values.update(_flatten_numeric(value, child_key))
    elif isinstance(raw, list):
        for index, value in enumerate(raw):
            values.update(_flatten_numeric(value, f"{prefix}.{index}"))
    elif _is_number(raw):
        values[prefix] = float(raw)
    return values


def _extract_by_patterns(values: dict[str, float], patterns: dict[str, tuple[str, ...]]) -> dict[str, Any]:
    extracted: dict[str, Any] = {}
    lowered = {key.lower(): value for key, value in values.items()}
    for stable_name, fragments in patterns.items():
        matches = [value for key, value in lowered.items() if all(fragment in key for fragment in fragments)]
        extracted[stable_name] = sum(matches) if matches else None
    return extracted


def _extract_lmcache_exact(values: dict[str, float]) -> dict[str, Any]:
    matches = _lmcache_exact_matches(values)
    return {name: values[matches[name]] if name in matches else None for name in LMCACHE_METRIC_NAMES}


def _lmcache_exact_matches(values: dict[str, float]) -> dict[str, str]:
    matches: dict[str, str] = {}
    lowered = {key.lower(): key for key in values}
    for name in LMCACHE_METRIC_NAMES:
        candidates = (
            name,
            name.replace(":", "."),
            name.replace(":", "_"),
        )
        for candidate in candidates:
            key = lowered.get(candidate.lower())
            if key is not None:
                matches[name] = key
                break
    return matches


def _unknown_lmcache_metrics(raw: Any) -> dict[str, float]:
    values = _flatten_numeric(raw)
    known_raw_names = set(_lmcache_exact_matches(values).values())
    return {
        key: value
        for key, value in values.items()
        if key not in known_raw_names and key.lower().startswith("lmcache")
    }


def _known_metrics(snapshot: dict[str, Any] | None) -> dict[str, Any] | None:
    if not snapshot or snapshot.get("status") != "ok":
        return None
    metrics = snapshot.get("metrics")
    return metrics if isinstance(metrics, dict) else None


def _connector_stats(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict) or value.get("status") != "ok":
        return None
    stats = value.get("stats")
    return stats if isinstance(stats, dict) else None


def _to_plain(value: Any) -> Any:
    if dataclasses.is_dataclass(value):
        return dataclasses.asdict(value)
    if isinstance(value, dict):
        return {str(key): _to_plain(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_plain(child) for child in value]
    return value


def _first_number(mapping: dict[str, Any], key: str) -> int | float | None:
    value = mapping.get(key)
    return value if _is_number(value) else None


def _is_number(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float))


def _dict(value: Any) -> dict[str, Any] | None:
    return value if isinstance(value, dict) else None


def _connect_if_available(client: Any) -> None:
    connect = getattr(client, "connect", None)
    if callable(connect):
        connect()


def _close_if_available(client: Any) -> None:
    close = getattr(client, "close", None)
    if callable(close):
        close()


def _unavailable(collector: str, reason: str) -> dict[str, Any]:
    return {
        "schema_version": f"bifrost.collector.{collector}.v1",
        "collector": collector,
        "status": "unavailable",
        "reason": reason,
    }


__all__ = [
    "BifrostMetricsCollector",
    "LMCACHE_METRIC_NAMES",
    "LMCacheMetricsCollector",
    "VLLMMetricsCollector",
    "bifrost_snapshot_delta",
    "extract_lmcache_metrics",
    "extract_lmcache_metric_records",
    "extract_vllm_metrics",
]

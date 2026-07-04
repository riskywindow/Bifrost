"""Cache backend implementations for the Phase 6 fake serving harness."""

from __future__ import annotations

import asyncio
import hashlib
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .cache_backend import CacheBackend

REPO_ROOT = Path(__file__).resolve().parents[2]
LMCACHE_INTEGRATION = REPO_ROOT / "integrations" / "lmcache_bifrost"
if LMCACHE_INTEGRATION.exists() and str(LMCACHE_INTEGRATION) not in sys.path:
    sys.path.insert(0, str(LMCACHE_INTEGRATION))


@dataclass(slots=True)
class BackendCounters:
    lookup_count: int = 0
    store_count: int = 0
    exists_count: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    bytes_stored: int = 0
    bytes_loaded: int = 0
    error_count: int = 0
    last_error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "lookup_count": self.lookup_count,
            "store_count": self.store_count,
            "exists_count": self.exists_count,
            "cache_hits": self.cache_hits,
            "cache_misses": self.cache_misses,
            "bytes_stored": self.bytes_stored,
            "bytes_loaded": self.bytes_loaded,
            "error_count": self.error_count,
            "last_error": self.last_error,
        }


class NoCacheBackend(CacheBackend):
    def __init__(self) -> None:
        self._counters = BackendCounters()
        self._lock = threading.Lock()

    async def lookup(self, key: str) -> object | None:
        del key
        return None

    async def store(self, key: str, value: object) -> None:
        del key, value

    async def exists(self, key: str) -> bool:
        del key
        return False

    def metrics_snapshot(self) -> dict[str, Any]:
        with self._lock:
            stats = self._counters.to_dict()
        return {
            "status": "ok",
            "schema_version": "bifrost.fake_cache_backend_metrics.v1",
            "cache_backend": "none",
            "connector_metrics_source": "none",
            "performance_metrics_source": "synthetic_fake_server",
            "stats": stats,
        }

    async def close(self) -> None:
        return None


class LocalMemoryCacheBackend(CacheBackend):
    def __init__(self) -> None:
        self._items: dict[str, object] = {}
        self._counters = BackendCounters()
        self._lock = threading.Lock()

    async def lookup(self, key: str) -> object | None:
        with self._lock:
            self._counters.lookup_count += 1
            value = self._items.get(key)
            if value is None:
                self._counters.cache_misses += 1
            else:
                self._counters.cache_hits += 1
                self._counters.bytes_loaded += _payload_len(value)
            return value

    async def store(self, key: str, value: object) -> None:
        with self._lock:
            self._items[key] = value
            self._counters.store_count += 1
            self._counters.bytes_stored += _payload_len(value)

    async def exists(self, key: str) -> bool:
        with self._lock:
            self._counters.exists_count += 1
            return key in self._items

    def metrics_snapshot(self) -> dict[str, Any]:
        with self._lock:
            stats = self._counters.to_dict()
            stats["object_count"] = len(self._items)
        return {
            "status": "ok",
            "schema_version": "bifrost.fake_cache_backend_metrics.v1",
            "cache_backend": "local_memory",
            "connector_metrics_source": "local_memory_dictionary",
            "performance_metrics_source": "synthetic_fake_server",
            "stats": stats,
        }

    async def close(self) -> None:
        return None


class BifrostLMCacheBackend(CacheBackend):
    """Fake-serving cache backed by the real Phase 5 BIFROST LMCache connector."""

    def __init__(
        self,
        *,
        endpoint: str,
        metrics_jsonl_path: str | Path | None = None,
        model_id: str = "bifrost-fake-serving-model",
        timeout_seconds: float = 5.0,
    ) -> None:
        from lmcache_bifrost.config import BifrostLMCacheConfig
        from lmcache_bifrost.connector import BifrostRemoteConnector
        from tests.fakes import FakeCacheEngineKey, FakeMemoryObj

        self.endpoint = endpoint
        self.metrics_jsonl_path = Path(metrics_jsonl_path) if metrics_jsonl_path else None
        self.model_id = model_id
        self._fake_key_type = FakeCacheEngineKey
        self._fake_memory_type = FakeMemoryObj
        self._connector = BifrostRemoteConnector(
            BifrostLMCacheConfig(
                endpoint=endpoint,
                allow_pickle_fallback=True,
                timeout_seconds=timeout_seconds,
                metrics_jsonl_path=(
                    str(self.metrics_jsonl_path) if self.metrics_jsonl_path else None
                ),
            )
        )
        self._counters = BackendCounters()
        self._lock = threading.Lock()

    async def lookup(self, key: str) -> object | None:
        connector_key = self._connector_key(key)
        with self._lock:
            self._counters.lookup_count += 1
        try:
            present = await self._connector.exists(connector_key)
            with self._lock:
                self._counters.exists_count += 1
            if not present:
                with self._lock:
                    self._counters.cache_misses += 1
                return None
            value = await self._connector.get(connector_key)
            if value is None:
                with self._lock:
                    self._counters.cache_misses += 1
                return None
            with self._lock:
                self._counters.cache_hits += 1
                self._counters.bytes_loaded += _payload_len(value)
            return value
        except Exception as exc:
            self._record_error(exc)
            raise

    async def store(self, key: str, value: object) -> None:
        connector_key = self._connector_key(key)
        memory_obj = self._memory_obj(key, value)
        try:
            await self._connector.put(connector_key, memory_obj)
            with self._lock:
                self._counters.store_count += 1
                self._counters.bytes_stored += _payload_len(memory_obj)
        except Exception as exc:
            self._record_error(exc)
            raise

    async def exists(self, key: str) -> bool:
        try:
            present = await self._connector.exists(self._connector_key(key))
            with self._lock:
                self._counters.exists_count += 1
            return present
        except Exception as exc:
            self._record_error(exc)
            return False

    def metrics_snapshot(self) -> dict[str, Any]:
        connector_stats = self._connector.metrics_snapshot()
        with self._lock:
            backend_stats = self._counters.to_dict()
        stats = dict(connector_stats)
        stats.update(
            {
                "backend_lookup_count": backend_stats["lookup_count"],
                "backend_store_count": backend_stats["store_count"],
                "backend_exists_count": backend_stats["exists_count"],
                "cache_hits": backend_stats["cache_hits"],
                "cache_misses": backend_stats["cache_misses"],
                "backend_error_count": backend_stats["error_count"],
            }
        )
        return {
            "status": "ok",
            "schema_version": "bifrost.fake_cache_backend_metrics.v1",
            "cache_backend": "bifrost_lmcache",
            "connector_metrics_source": "actual_bifrost_remote_connector",
            "performance_metrics_source": "synthetic_fake_server",
            "endpoint": self.endpoint,
            "metrics_jsonl_path": str(self.metrics_jsonl_path) if self.metrics_jsonl_path else None,
            "stats": stats,
            "connector_metrics": connector_stats,
            "backend_counters": backend_stats,
        }

    async def close(self) -> None:
        await self._connector.close()

    def _connector_key(self, key: str) -> object:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        tokens = tuple(key.encode("utf-8")[:32]) or (0,)
        return self._fake_key_type(
            self.model_id,
            f"sha256:{digest}",
            tokens,
            (("phase", "phase6_fake_serving"), ("prefix_id", key)),
        )

    def _memory_obj(self, key: str, value: object) -> object:
        if isinstance(value, self._fake_memory_type):
            return value
        if isinstance(value, (bytes, bytearray, memoryview)):
            payload = bytes(value)
        else:
            payload = f"fake-memory-obj:{key}:{value!r}".encode("utf-8")
        return self._fake_memory_type(payload, dtype="uint8", shape=(len(payload),))

    def _record_error(self, exc: Exception) -> None:
        with self._lock:
            self._counters.error_count += 1
            self._counters.last_error = f"{exc.__class__.__name__}: {exc}"


def run_backend_async(awaitable: Any) -> Any:
    return asyncio.run(awaitable)


def fake_memory_obj_payload(value: object) -> bytes | None:
    payload = getattr(value, "payload", None)
    if isinstance(payload, (bytes, bytearray, memoryview)):
        return bytes(payload)
    return None


def _payload_len(value: object) -> int:
    payload = fake_memory_obj_payload(value)
    if payload is not None:
        return len(payload)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return len(value)
    return 0


__all__ = [
    "BifrostLMCacheBackend",
    "LocalMemoryCacheBackend",
    "NoCacheBackend",
    "fake_memory_obj_payload",
]

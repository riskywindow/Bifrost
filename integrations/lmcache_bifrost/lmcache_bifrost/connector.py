"""BIFROST LMCache remote connector."""

from __future__ import annotations

import inspect
from typing import Any

from bifrost_client import BifrostAsyncClient, BifrostClient, BifrostClientConfig
from bifrost_client.errors import BifrostClientError, BifrostNotFoundError
from bifrost_kv.validate import validate_object

from lmcache_bifrost.blob_codec import (
    build_opaque_metadata,
    build_opaque_target_profile,
    deserialize_memory_obj,
    serialize_memory_obj,
)
from lmcache_bifrost.config import BifrostLMCacheConfig
from lmcache_bifrost.errors import (
    BifrostLMCacheSerializationError,
    BifrostLMCacheStoreError,
    BifrostLMCacheValidationError,
    ConnectorConfigurationError,
)
from lmcache_bifrost.key_codec import opaque_engine_key_hash
from lmcache_bifrost.lmcache_compat import RemoteConnector as LMCacheRemoteConnector
from lmcache_bifrost.metrics import ConnectorJsonlLogger, ConnectorMetrics, monotonic_ms

_BaseRemoteConnector = LMCacheRemoteConnector or object
_SERVEABLE_STATES = frozenset(("verified", "pinned", "evictable"))


class BifrostRemoteConnector(_BaseRemoteConnector):  # type: ignore[misc]
    """LMCache remote connector configured by `BifrostConnectorAdapter`.

    BIFROST stores LMCache-owned `MemoryObj` payloads as opaque bytes. The
    connector validates BIFROST descriptors and payload hashes before exposing
    any object as an LMCache hit.
    """

    def __init__(
        self,
        config: BifrostLMCacheConfig,
        *,
        context: object | None = None,
        client: object | None = None,
    ) -> None:
        self.config = config
        self.context = context
        self.client = client or BifrostAsyncClient(config=_client_config(config))
        self._owns_client = client is None
        self._sync_client: BifrostClient | None = None
        self.closed = False
        self._metrics = ConnectorMetrics()
        self._jsonl_logger = ConnectorJsonlLogger(config.metrics_jsonl_path)

    def support_ping(self) -> bool:
        return True

    async def ping(self) -> bool:
        self._ensure_open()
        ping = getattr(self.client, "ping", None)
        stats = getattr(self.client, "stats", None)
        try:
            if callable(ping):
                return bool(await self._maybe_await(ping()))
            if callable(stats):
                await self._maybe_await(stats())
                return True
        except BifrostClientError as exc:
            raise BifrostLMCacheStoreError(f"ping_failed: {exc}") from exc
        except Exception as exc:  # pragma: no cover - defensive wrapper.
            raise BifrostLMCacheStoreError(f"ping_failed: {exc}") from exc
        raise BifrostLMCacheStoreError("ping_failed: no daemon ping or stats API")

    def support_batched_contains(self) -> bool:
        return True

    async def batched_contains(self, keys: object) -> list[bool]:
        return [await self.exists(key) for key in _iter_keys(keys)]

    def support_batched_get(self) -> bool:
        return True

    async def batched_get(self, keys: object) -> list[object | None]:
        return [await self.get(key) for key in _iter_keys(keys)]

    def support_batched_put(self) -> bool:
        return True

    async def batched_put(self, items: object) -> None:
        for index, key, memory_obj in _iter_items(items):
            try:
                await self.put(key, memory_obj)
            except (
                BifrostLMCacheSerializationError,
                BifrostLMCacheStoreError,
                BifrostLMCacheValidationError,
                ConnectorConfigurationError,
            ) as exc:
                raise type(exc)(f"batched_put_failed:index={index}:reason={exc}") from exc
            except Exception as exc:  # pragma: no cover - defensive wrapper.
                raise BifrostLMCacheStoreError(
                    f"batched_put_failed:index={index}:reason={exc}"
                ) from exc

    async def exists(self, key: object) -> bool:
        self._ensure_open()
        self._metrics.increment("exists_count")
        key_hash: str | None = None
        start_ms = monotonic_ms()
        try:
            key_hash = opaque_engine_key_hash(key)
            candidates = await self._query_by_key_hash(key_hash)
            for summary in candidates:
                if not self._summary_is_servable(summary):
                    continue
                try:
                    stored = await self._get_object(summary.object_id)
                    self._validate_stored_object(stored, key, key_hash)
                    return True
                except BifrostLMCacheValidationError:
                    continue
                except BifrostLMCacheStoreError:
                    continue
            return False
        except BifrostLMCacheStoreError as exc:
            self._emit_error("exists", key_hash, None, 0, start_ms, exc)
            return False
        finally:
            self._emit_event(
                "connector_exists",
                operation="exists",
                opaque_engine_key_hash=key_hash,
                duration_ms=monotonic_ms() - start_ms,
            )

    def exists_sync(self, key: object) -> bool:
        self._ensure_open()
        self._metrics.increment("exists_count")
        try:
            key_hash = opaque_engine_key_hash(key)
            candidates = self._query_by_key_hash_sync(key_hash)
            for summary in candidates:
                if not self._summary_is_servable(summary):
                    continue
                try:
                    stored = self._get_object_sync(summary.object_id)
                    self._validate_stored_object(stored, key, key_hash)
                    return True
                except BifrostLMCacheValidationError:
                    continue
                except BifrostLMCacheStoreError:
                    continue
            return False
        except BifrostLMCacheStoreError:
            return False

    async def get(self, key: object) -> object | None:
        self._ensure_open()
        self._metrics.increment("get_count")
        start_ms = monotonic_ms()
        key_hash: str | None = None
        object_id: str | None = None
        bytes_count = 0
        self._emit_event("connector_get_started", operation="get")
        try:
            key_hash = opaque_engine_key_hash(key)
            candidates = await self._query_by_key_hash(key_hash)
            for summary in candidates:
                if not self._summary_is_servable(summary):
                    continue
                object_id = summary.object_id
                stored = await self._get_object(summary.object_id)
                payload = self._validate_stored_object(stored, key, key_hash)
                bytes_count = len(payload)
                try:
                    memory_obj = deserialize_memory_obj(payload, self.config)
                except BifrostLMCacheSerializationError:
                    self._metrics.increment("serialization_error_count")
                    raise
                except Exception as exc:  # pragma: no cover - defensive wrapper.
                    self._metrics.increment("serialization_error_count")
                    raise BifrostLMCacheSerializationError(
                        f"LMCache MemoryObj deserialization failed: {exc}"
                    ) from exc
                self._metrics.increment("bytes_get", bytes_count)
                return memory_obj
            return None
        except BifrostLMCacheValidationError as exc:
            self._metrics.increment("get_error_count")
            self._metrics.increment("validation_error_count")
            self._emit_error("get", key_hash, object_id, bytes_count, start_ms, exc)
            raise
        except BifrostLMCacheSerializationError as exc:
            self._metrics.increment("get_error_count")
            self._emit_error("get", key_hash, object_id, bytes_count, start_ms, exc)
            raise
        except Exception as exc:
            self._metrics.increment("get_error_count")
            self._emit_error("get", key_hash, object_id, bytes_count, start_ms, exc)
            raise
        finally:
            duration_ms = monotonic_ms() - start_ms
            self._metrics.add_duration_ms("total_get_ms", duration_ms)
            self._emit_event(
                "connector_get_completed",
                operation="get",
                opaque_engine_key_hash=key_hash,
                object_id=object_id,
                bytes_count=bytes_count,
                duration_ms=duration_ms,
            )

    async def put(self, key: object, memory_obj: object) -> None:
        self._ensure_open()
        self._metrics.increment("put_count")
        start_ms = monotonic_ms()
        key_hash: str | None = None
        object_id: str | None = None
        bytes_count = 0
        self._emit_event("connector_put_started", operation="put")
        try:
            key_hash = opaque_engine_key_hash(key)
            payload = serialize_memory_obj(memory_obj, self.config)
            bytes_count = len(payload)
            metadata = build_opaque_metadata(key, memory_obj, payload, self.config)
            object_id = metadata["object_id"]
        except BifrostLMCacheSerializationError as exc:
            self._metrics.increment("put_error_count")
            self._metrics.increment("serialization_error_count")
            self._metrics.add_duration_ms("total_put_ms", monotonic_ms() - start_ms)
            self._emit_error("put", key_hash, object_id, bytes_count, start_ms, exc)
            raise
        except BifrostLMCacheValidationError as exc:
            self._metrics.increment("put_error_count")
            self._metrics.increment("validation_error_count")
            self._metrics.add_duration_ms("total_put_ms", monotonic_ms() - start_ms)
            self._emit_error("put", key_hash, object_id, bytes_count, start_ms, exc)
            raise
        except Exception as exc:  # pragma: no cover - defensive wrapper.
            self._metrics.increment("put_error_count")
            self._metrics.increment("validation_error_count")
            wrapped = BifrostLMCacheValidationError(
                f"failed to build opaque LMCache object: {exc}"
            )
            self._metrics.add_duration_ms("total_put_ms", monotonic_ms() - start_ms)
            self._emit_error("put", key_hash, object_id, bytes_count, start_ms, wrapped)
            raise BifrostLMCacheValidationError(
                f"failed to build opaque LMCache object: {exc}"
            ) from exc

        try:
            self._validate_stored_object(
                _StoredObject(metadata=metadata, payload=payload, object_id=object_id),
                key,
                key_hash,
            )
        except BifrostLMCacheValidationError as exc:
            self._metrics.increment("put_error_count")
            self._metrics.increment("validation_error_count")
            self._metrics.add_duration_ms("total_put_ms", monotonic_ms() - start_ms)
            self._emit_error("put", key_hash, object_id, bytes_count, start_ms, exc)
            raise
        try:
            result = await self._maybe_await(
                self.client.put_object(metadata, payload, self.config.chunk_size)
            )
        except BifrostClientError as exc:
            self._metrics.increment("put_error_count")
            self._metrics.add_duration_ms("total_put_ms", monotonic_ms() - start_ms)
            self._emit_error("put", key_hash, object_id, bytes_count, start_ms, exc)
            raise BifrostLMCacheStoreError(f"BIFROST PUT failed: {exc}") from exc
        except Exception as exc:  # pragma: no cover - defensive wrapper.
            self._metrics.increment("put_error_count")
            self._metrics.add_duration_ms("total_put_ms", monotonic_ms() - start_ms)
            self._emit_error("put", key_hash, object_id, bytes_count, start_ms, exc)
            raise BifrostLMCacheStoreError(f"BIFROST PUT failed: {exc}") from exc

        if not bool(getattr(result, "stored", False)) or not bool(
            getattr(result, "verified", False)
        ):
            reason = getattr(result, "reason", "put_not_verified")
            self._metrics.increment("put_error_count")
            self._metrics.add_duration_ms("total_put_ms", monotonic_ms() - start_ms)
            error = BifrostLMCacheStoreError(f"BIFROST PUT was not verified: {reason}")
            self._emit_error("put", key_hash, object_id, bytes_count, start_ms, error)
            raise error
        if getattr(result, "object_id", object_id) != object_id:
            self._metrics.increment("put_error_count")
            self._metrics.add_duration_ms("total_put_ms", monotonic_ms() - start_ms)
            error = BifrostLMCacheStoreError("BIFROST PUT returned the wrong object_id")
            self._emit_error("put", key_hash, object_id, bytes_count, start_ms, error)
            raise error
        duration_ms = monotonic_ms() - start_ms
        self._metrics.increment("bytes_put", bytes_count)
        self._metrics.add_duration_ms("total_put_ms", duration_ms)
        self._emit_event(
            "connector_put_completed",
            operation="put",
            opaque_engine_key_hash=key_hash,
            object_id=object_id,
            bytes_count=bytes_count,
            duration_ms=duration_ms,
        )

    async def list(self) -> list[str]:
        self._ensure_open()
        self._metrics.increment("list_count")
        start_ms = monotonic_ms()
        try:
            summaries = await self._maybe_await(
                self.client.list_objects(engine_name=self.config.engine_name)
            )
        except BifrostClientError as exc:
            self._emit_error("list", None, None, 0, start_ms, exc)
            raise BifrostLMCacheStoreError(f"BIFROST LIST failed: {exc}") from exc
        except Exception as exc:  # pragma: no cover - defensive wrapper.
            self._emit_error("list", None, None, 0, start_ms, exc)
            raise BifrostLMCacheStoreError(f"BIFROST LIST failed: {exc}") from exc

        entries: set[str] = set()
        for summary in summaries:
            if not self._summary_is_servable(summary):
                continue
            try:
                stored = await self._get_object(summary.object_id)
                key_hash = self._validate_listed_object(stored)
            except (BifrostLMCacheStoreError, BifrostLMCacheValidationError):
                continue
            entries.add(f"lmcache:{key_hash}")
        return sorted(entries)

    async def close(self) -> None:
        if self.closed:
            return
        self._metrics.increment("close_count")
        await self._close_client(self.client)
        if self._sync_client is not None:
            self._sync_client.close()
            self._sync_client = None
        self.closed = True

    def metrics_snapshot(self) -> dict[str, Any]:
        return self._metrics.snapshot()

    def _ensure_open(self) -> None:
        if self.closed:
            raise ConnectorConfigurationError("BIFROST connector is closed")

    async def _query_by_key_hash(self, key_hash: str) -> list[Any]:
        try:
            return list(
                await self._maybe_await(
                    self.client.query_by_opaque_key_hash(
                        self.config.engine_name,
                        self.config.integration_name,
                        key_hash,
                    )
                )
            )
        except BifrostClientError as exc:
            raise BifrostLMCacheStoreError(f"BIFROST query failed: {exc}") from exc
        except Exception as exc:  # pragma: no cover - defensive wrapper.
            raise BifrostLMCacheStoreError(f"BIFROST query failed: {exc}") from exc

    def _query_by_key_hash_sync(self, key_hash: str) -> list[Any]:
        client = self._sync_query_client()
        try:
            return list(
                client.query_by_opaque_key_hash(
                    self.config.engine_name,
                    self.config.integration_name,
                    key_hash,
                )
            )
        except BifrostClientError as exc:
            raise BifrostLMCacheStoreError(f"BIFROST query failed: {exc}") from exc
        except Exception as exc:
            raise BifrostLMCacheStoreError(f"BIFROST query failed: {exc}") from exc

    async def _get_object(self, object_id: str) -> Any:
        try:
            return await self._maybe_await(self.client.get_object(object_id))
        except BifrostNotFoundError as exc:
            raise BifrostLMCacheStoreError(f"BIFROST object missing: {object_id}") from exc
        except BifrostClientError as exc:
            raise BifrostLMCacheStoreError(f"BIFROST GET failed: {exc}") from exc
        except Exception as exc:  # pragma: no cover - defensive wrapper.
            raise BifrostLMCacheStoreError(f"BIFROST GET failed: {exc}") from exc

    def _get_object_sync(self, object_id: str) -> Any:
        client = self._sync_query_client()
        try:
            return client.get_object(object_id)
        except BifrostNotFoundError as exc:
            raise BifrostLMCacheStoreError(f"BIFROST object missing: {object_id}") from exc
        except BifrostClientError as exc:
            raise BifrostLMCacheStoreError(f"BIFROST GET failed: {exc}") from exc
        except Exception as exc:
            raise BifrostLMCacheStoreError(f"BIFROST GET failed: {exc}") from exc

    def _sync_query_client(self) -> Any:
        query = getattr(self.client, "query_by_opaque_key_hash", None)
        if callable(query) and not self._owns_client:
            return self.client
        if self._sync_client is None:
            self._sync_client = BifrostClient(config=_client_config(self.config))
        return self._sync_client

    def _validate_stored_object(
        self,
        stored: Any,
        key: object,
        key_hash: str,
    ) -> bytes:
        metadata = _metadata(stored)
        payload = _payload(stored)
        opaque = metadata.get("opaque_engine_profile")
        if not isinstance(opaque, dict) or opaque.get("engine_key_hash") != key_hash:
            raise BifrostLMCacheValidationError("opaque_engine_key_hash mismatch")
        result = validate_object(
            metadata,
            payload,
            build_opaque_target_profile(key, self.config),
        )
        if result.status != "accepted":
            raise BifrostLMCacheValidationError(
                f"opaque LMCache object failed validation: {result.reason_code}"
            )
        object_id = _object_id(stored)
        if object_id is not None and result.object_id != object_id:
            raise BifrostLMCacheValidationError("object_id mismatch")
        return payload

    def _validate_listed_object(self, stored: Any) -> str:
        metadata = _metadata(stored)
        payload = _payload(stored)
        if metadata.get("object_type") != "opaque_engine_blob":
            raise BifrostLMCacheValidationError("listed object is not opaque_engine_blob")
        engine = metadata.get("engine_profile")
        if not isinstance(engine, dict):
            raise BifrostLMCacheValidationError("listed object missing engine_profile")
        if engine.get("engine_name") != self.config.engine_name:
            raise BifrostLMCacheValidationError("listed object engine_name mismatch")
        if engine.get("integration_name") != self.config.integration_name:
            raise BifrostLMCacheValidationError("listed object integration_name mismatch")
        opaque = metadata.get("opaque_engine_profile")
        if not isinstance(opaque, dict):
            raise BifrostLMCacheValidationError("listed object missing opaque profile")
        key_hash = opaque.get("engine_key_hash")
        if not isinstance(key_hash, str) or not key_hash:
            raise BifrostLMCacheValidationError("listed object missing key hash")
        result = validate_object(metadata, payload, None)
        if result.status != "accepted":
            raise BifrostLMCacheValidationError(
                f"listed object failed validation: {result.reason_code}"
            )
        object_id = _object_id(stored)
        if object_id is not None and result.object_id != object_id:
            raise BifrostLMCacheValidationError("listed object_id mismatch")
        return key_hash

    def _summary_is_servable(self, summary: Any) -> bool:
        if getattr(summary, "object_type", None) != "opaque_engine_blob":
            return False
        if getattr(summary, "engine_name", None) != self.config.engine_name:
            return False
        integration_name = getattr(summary, "integration_name", None)
        if integration_name not in (None, self.config.integration_name):
            return False
        return str(getattr(summary, "state", "")) in _SERVEABLE_STATES

    async def _close_client(self, client: object) -> None:
        close = getattr(client, "close", None)
        if callable(close):
            result = close()
            if inspect.isawaitable(result):
                await result

    async def _maybe_await(self, value: Any) -> Any:
        if inspect.isawaitable(value):
            return await value
        return value

    def _emit_event(
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
        self._jsonl_logger.emit(
            event_name,
            operation=operation,
            opaque_engine_key_hash=opaque_engine_key_hash,
            object_id=object_id,
            bytes_count=bytes_count,
            duration_ms=duration_ms,
            reason_code=reason_code,
        )

    def _emit_error(
        self,
        operation: str,
        opaque_engine_key_hash: str | None,
        object_id: str | None,
        bytes_count: int,
        start_ms: float,
        exc: Exception | None = None,
    ) -> None:
        self._emit_event(
            "connector_error",
            operation=operation,
            opaque_engine_key_hash=opaque_engine_key_hash,
            object_id=object_id,
            bytes_count=bytes_count,
            duration_ms=monotonic_ms() - start_ms,
            reason_code=_reason_code(exc),
        )


class _StoredObject:
    def __init__(self, metadata: dict[str, Any], payload: bytes, object_id: str) -> None:
        self.metadata = metadata
        self.payload = payload
        self.object_id = object_id


def _client_config(config: BifrostLMCacheConfig) -> BifrostClientConfig:
    return BifrostClientConfig(
        endpoint=config.endpoint,
        timeout_seconds=config.timeout_seconds,
        default_chunk_size=config.chunk_size,
    )


def _metadata(stored: Any) -> dict[str, Any]:
    metadata = getattr(stored, "metadata", None)
    if not isinstance(metadata, dict):
        raise BifrostLMCacheValidationError("stored object missing metadata")
    return metadata


def _payload(stored: Any) -> bytes:
    payload = getattr(stored, "payload", None)
    if not isinstance(payload, (bytes, bytearray, memoryview)):
        raise BifrostLMCacheValidationError("stored object missing byte payload")
    return bytes(payload)


def _object_id(stored: Any) -> str | None:
    object_id = getattr(stored, "object_id", None)
    return object_id if isinstance(object_id, str) else None


def _reason_code(exc: Exception | None) -> str:
    if exc is None:
        return "connector_error"
    if isinstance(exc, BifrostLMCacheSerializationError):
        return "lmcache_serialization_error"
    if isinstance(exc, BifrostLMCacheValidationError):
        return "opaque_blob_validation_error"
    if isinstance(exc, BifrostLMCacheStoreError):
        return "store_error"
    if isinstance(exc, ConnectorConfigurationError):
        return "connector_configuration_error"
    if isinstance(exc, BifrostClientError):
        return "store_error"
    return "connector_error"


def _iter_keys(keys: object) -> list[object]:
    if isinstance(keys, (str, bytes, bytearray, memoryview)):
        raise ConnectorConfigurationError("batched keys must be an iterable of keys")
    try:
        return list(keys)  # type: ignore[arg-type]
    except TypeError as exc:
        raise ConnectorConfigurationError("batched keys must be an iterable of keys") from exc


def _iter_items(items: object) -> list[tuple[int, object, object]]:
    if isinstance(items, dict):
        return [
            (index, key, memory_obj)
            for index, (key, memory_obj) in enumerate(items.items())
        ]
    if isinstance(items, (str, bytes, bytearray, memoryview)):
        raise ConnectorConfigurationError(
            "batched put items must be an iterable of (key, memory_obj) pairs"
        )
    result: list[tuple[int, object, object]] = []
    try:
        iterator = iter(items)  # type: ignore[arg-type]
    except TypeError as exc:
        raise ConnectorConfigurationError(
            "batched put items must be an iterable of (key, memory_obj) pairs"
        ) from exc
    for index, item in enumerate(iterator):
        try:
            key, memory_obj = item  # type: ignore[misc]
        except (TypeError, ValueError) as exc:
            raise ConnectorConfigurationError(
                f"batched_put_failed:index={index}:reason=invalid_item"
            ) from exc
        result.append((index, key, memory_obj))
    return result


__all__ = ["BifrostRemoteConnector"]

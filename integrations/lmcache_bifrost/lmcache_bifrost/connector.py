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

    async def exists(self, key: object) -> bool:
        self._ensure_open()
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
        except BifrostLMCacheStoreError:
            return False

    def exists_sync(self, key: object) -> bool:
        self._ensure_open()
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
        key_hash = opaque_engine_key_hash(key)
        candidates = await self._query_by_key_hash(key_hash)
        for summary in candidates:
            if not self._summary_is_servable(summary):
                continue
            stored = await self._get_object(summary.object_id)
            payload = self._validate_stored_object(stored, key, key_hash)
            try:
                return deserialize_memory_obj(payload, self.config)
            except BifrostLMCacheSerializationError:
                raise
            except Exception as exc:  # pragma: no cover - defensive wrapper.
                raise BifrostLMCacheSerializationError(
                    f"LMCache MemoryObj deserialization failed: {exc}"
                ) from exc
        return None

    async def put(self, key: object, memory_obj: object) -> None:
        self._ensure_open()
        try:
            payload = serialize_memory_obj(memory_obj, self.config)
            metadata = build_opaque_metadata(key, memory_obj, payload, self.config)
        except BifrostLMCacheSerializationError:
            raise
        except BifrostLMCacheValidationError:
            raise
        except Exception as exc:  # pragma: no cover - defensive wrapper.
            raise BifrostLMCacheValidationError(
                f"failed to build opaque LMCache object: {exc}"
            ) from exc

        self._validate_stored_object(
            _StoredObject(metadata=metadata, payload=payload, object_id=metadata["object_id"]),
            key,
            opaque_engine_key_hash(key),
        )
        try:
            result = await self._maybe_await(
                self.client.put_object(metadata, payload, self.config.chunk_size)
            )
        except BifrostClientError as exc:
            raise BifrostLMCacheStoreError(f"BIFROST PUT failed: {exc}") from exc
        except Exception as exc:  # pragma: no cover - defensive wrapper.
            raise BifrostLMCacheStoreError(f"BIFROST PUT failed: {exc}") from exc

        if not bool(getattr(result, "stored", False)) or not bool(
            getattr(result, "verified", False)
        ):
            reason = getattr(result, "reason", "put_not_verified")
            raise BifrostLMCacheStoreError(f"BIFROST PUT was not verified: {reason}")
        if getattr(result, "object_id", metadata["object_id"]) != metadata["object_id"]:
            raise BifrostLMCacheStoreError("BIFROST PUT returned the wrong object_id")

    async def list(self) -> list[str]:
        self._ensure_open()
        try:
            summaries = await self._maybe_await(
                self.client.list_objects(engine_name=self.config.engine_name)
            )
        except BifrostClientError as exc:
            raise BifrostLMCacheStoreError(f"BIFROST LIST failed: {exc}") from exc
        except Exception as exc:  # pragma: no cover - defensive wrapper.
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
        await self._close_client(self.client)
        if self._sync_client is not None:
            self._sync_client.close()
            self._sync_client = None
        self.closed = True

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


__all__ = ["BifrostRemoteConnector"]

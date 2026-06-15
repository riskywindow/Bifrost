from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any

import pytest

from bifrost_client.models import ObjectSummary, PutResult, StoredObject
from lmcache_bifrost.config import BifrostLMCacheConfig
from lmcache_bifrost.connector import BifrostRemoteConnector
from lmcache_bifrost.errors import (
    BifrostLMCacheStoreError,
    BifrostLMCacheValidationError,
    ConnectorConfigurationError,
    MemoryObjSerializationError,
)
from lmcache_bifrost.key_codec import opaque_engine_key_hash
from tests.fakes import FakeCacheEngineKey, FakeMemoryObj


def test_fake_connector_put_exists_get_and_list_roundtrip() -> None:
    async def run() -> None:
        client = FakeBifrostClient()
        connector = _connector(client)
        key = FakeCacheEngineKey("tiny", "abc", (1, 2, 3))
        memory_obj = FakeMemoryObj(b"lmcache-owned-bytes", shape=(2, 4))

        await connector.put(key, memory_obj)

        assert await connector.exists(key) is True
        assert connector.exists_sync(key) is True
        assert await connector.get(key) == memory_obj
        assert await connector.list() == [f"lmcache:{opaque_engine_key_hash(key)}"]

    asyncio.run(run())


def test_fake_connector_missing_get_returns_none_and_exists_false() -> None:
    async def run() -> None:
        connector = _connector(FakeBifrostClient())
        key = FakeCacheEngineKey("tiny", "missing", (9,))

        assert await connector.exists(key) is False
        assert connector.exists_sync(key) is False
        assert await connector.get(key) is None

    asyncio.run(run())


def test_fake_connector_corrupt_stored_payload_raises_validation_error() -> None:
    async def run() -> None:
        client = FakeBifrostClient()
        connector = _connector(client)
        key = FakeCacheEngineKey("tiny", "abc", (1, 2, 3))
        await connector.put(key, FakeMemoryObj(b"payload"))
        client.corrupt_payload_for_key(key)

        assert await connector.exists(key) is False
        with pytest.raises(BifrostLMCacheValidationError):
            await connector.get(key)

    asyncio.run(run())


def test_fake_connector_descriptor_key_hash_mismatch_fails_closed() -> None:
    async def run() -> None:
        client = FakeBifrostClient()
        connector = _connector(client)
        key = FakeCacheEngineKey("tiny", "abc", (1, 2, 3))
        await connector.put(key, FakeMemoryObj(b"payload"))
        client.replace_stored_key_hash(key, "blake3:" + "f" * 64)

        assert await connector.exists(key) is False
        with pytest.raises(BifrostLMCacheValidationError, match="engine_key_hash"):
            await connector.get(key)
        assert await connector.list() == []

    asyncio.run(run())


def test_fake_connector_put_wrong_object_id_result_fails_closed() -> None:
    async def run() -> None:
        client = WrongObjectIdPutClient()
        connector = _connector(client)
        key = FakeCacheEngineKey("tiny", "abc", (1, 2, 3))

        with pytest.raises(BifrostLMCacheStoreError, match="wrong object_id"):
            await connector.put(key, FakeMemoryObj(b"payload"))

        assert await connector.exists(key) is False

    asyncio.run(run())


def test_fake_connector_close_is_idempotent() -> None:
    async def run() -> None:
        client = FakeBifrostClient()
        connector = _connector(client)

        await connector.close()
        await connector.close()

        assert connector.closed is True
        assert client.close_count == 1

    asyncio.run(run())


def test_fake_connector_operations_after_close_fail_deterministically() -> None:
    async def run() -> None:
        connector = _connector(FakeBifrostClient())
        key = FakeCacheEngineKey("tiny", "abc", (1, 2, 3))
        await connector.close()

        with pytest.raises(ConnectorConfigurationError):
            await connector.exists(key)
        with pytest.raises(ConnectorConfigurationError):
            connector.exists_sync(key)
        with pytest.raises(ConnectorConfigurationError):
            await connector.get(key)
        with pytest.raises(ConnectorConfigurationError):
            await connector.put(key, FakeMemoryObj(b"payload"))
        with pytest.raises(ConnectorConfigurationError):
            await connector.list()

    asyncio.run(run())


def test_fake_connector_put_serialization_failure_does_not_create_hit() -> None:
    async def run() -> None:
        client = FakeBifrostClient()
        config = BifrostLMCacheConfig(endpoint="fake", allow_pickle_fallback=False)
        connector = BifrostRemoteConnector(config, client=client)
        key = FakeCacheEngineKey("tiny", "abc", (1, 2, 3))

        with pytest.raises(MemoryObjSerializationError):
            await connector.put(key, FakeMemoryObj(b"payload"))

        assert await connector.exists(key) is False

    asyncio.run(run())


def _connector(client: "FakeBifrostClient") -> BifrostRemoteConnector:
    return BifrostRemoteConnector(
        BifrostLMCacheConfig(endpoint="fake", allow_pickle_fallback=True),
        client=client,
    )


class FakeBifrostClient:
    def __init__(self) -> None:
        self.objects: dict[str, StoredObject] = {}
        self.query_aliases: dict[tuple[str, str, str], str] = {}
        self.close_count = 0
        self.alive = True

    def ping(self) -> bool:
        return self.alive

    def put_object(
        self,
        metadata: dict[str, Any],
        payload: bytes,
        chunk_size: int,
    ) -> PutResult:
        del chunk_size
        object_id = str(metadata["object_id"])
        self.objects[object_id] = StoredObject(
            object_id=object_id,
            metadata=metadata,
            payload=payload,
            payload_hash=metadata["integrity"]["payload_hash"],
            descriptor_hash=metadata["integrity"]["descriptor_hash"],
        )
        return PutResult(
            object_id=object_id,
            payload_hash=metadata["integrity"]["payload_hash"],
            descriptor_hash=metadata["integrity"]["descriptor_hash"],
            stored=True,
            verified=True,
        )

    def query_by_opaque_key_hash(
        self,
        engine_name: str,
        integration_name: str,
        opaque_engine_key_hash: str,
    ) -> list[ObjectSummary]:
        matches: list[ObjectSummary] = []
        for stored in self.objects.values():
            metadata = stored.metadata
            engine = metadata["engine_profile"]
            opaque = metadata["opaque_engine_profile"]
            if engine["engine_name"] != engine_name:
                continue
            if engine["integration_name"] != integration_name:
                continue
            if opaque["engine_key_hash"] != opaque_engine_key_hash:
                continue
            matches.append(_summary(stored))
        alias = self.query_aliases.get((engine_name, integration_name, opaque_engine_key_hash))
        if alias is not None and alias in self.objects:
            matches.append(_summary(self.objects[alias], key_hash=opaque_engine_key_hash))
        return matches

    def get_object(self, object_id: str) -> StoredObject:
        return self.objects[object_id]

    def list_objects(self, **filters: Any) -> list[ObjectSummary]:
        engine_name = filters.get("engine_name")
        summaries = [_summary(stored) for stored in self.objects.values()]
        if engine_name is not None:
            summaries = [
                summary for summary in summaries if summary.engine_name == engine_name
            ]
        return summaries

    def close(self) -> None:
        self.close_count += 1

    def corrupt_payload_for_key(self, key: FakeCacheEngineKey) -> None:
        key_hash = opaque_engine_key_hash(key)
        for object_id, stored in list(self.objects.items()):
            if stored.metadata["opaque_engine_profile"]["engine_key_hash"] == key_hash:
                payload = b"x" + stored.payload[1:]
                self.objects[object_id] = replace(stored, payload=payload)
                return
        raise AssertionError("test key was not stored")

    def replace_stored_key_hash(self, key: FakeCacheEngineKey, key_hash: str) -> None:
        current_hash = opaque_engine_key_hash(key)
        for object_id, stored in list(self.objects.items()):
            if stored.metadata["opaque_engine_profile"]["engine_key_hash"] == current_hash:
                metadata = {
                    **stored.metadata,
                    "opaque_engine_profile": {
                        **stored.metadata["opaque_engine_profile"],
                        "engine_key_hash": key_hash,
                    },
                }
                self.objects[object_id] = replace(stored, metadata=metadata)
                engine = stored.metadata["engine_profile"]
                self.query_aliases[
                    (
                        engine["engine_name"],
                        engine["integration_name"],
                        current_hash,
                    )
                ] = object_id
                return
        raise AssertionError("test key was not stored")


class WrongObjectIdPutClient(FakeBifrostClient):
    def put_object(
        self,
        metadata: dict[str, Any],
        payload: bytes,
        chunk_size: int,
    ) -> PutResult:
        del metadata, payload, chunk_size
        return PutResult(
            object_id="bifrost://object/blake3/" + "f" * 64,
            payload_hash=None,
            descriptor_hash=None,
            stored=True,
            verified=True,
        )


def _summary(stored: StoredObject, *, key_hash: str | None = None) -> ObjectSummary:
    metadata = stored.metadata
    engine = metadata["engine_profile"]
    opaque = metadata["opaque_engine_profile"]
    return ObjectSummary(
        object_id=stored.object_id,
        object_type=metadata["object_type"],
        state="verified",
        byte_length=len(stored.payload),
        engine_name=engine["engine_name"],
        integration_name=engine["integration_name"],
        opaque_engine_key_hash=key_hash or opaque["engine_key_hash"],
    )

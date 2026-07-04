from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from bifrost_kv.validate import validate_object
from vllm_bifrost.blob_codec import tensor_to_payload
from vllm_bifrost.config import ENGINE_NAME, INTEGRATION_NAME, KV_CACHE_FORMAT
from vllm_bifrost.connector import BifrostKVConnector
from vllm_bifrost.errors import DaemonUnavailableError, KeyHashingError
from vllm_bifrost.fakes import (
    FakeAttentionMetadata,
    FakeKVCacheConfig,
    FakeVllmConfig,
    flatten_layer_blocks,
    make_fake_kv_caches,
)


class RecordingBifrostClient:
    def __init__(self) -> None:
        self.put_calls: list[dict[str, Any]] = []
        self.objects: dict[str, tuple[dict[str, Any], bytes]] = {}

    def put_object(
        self,
        metadata: dict[str, Any],
        payload: bytes,
        chunk_size: int,
    ) -> object:
        validation = validate_object(metadata, payload, None)
        assert validation.status == "accepted"
        assert validation.object_id == metadata["object_id"]
        self.put_calls.append(
            {
                "metadata": metadata,
                "payload": payload,
                "chunk_size": chunk_size,
            }
        )
        self.objects[str(metadata["object_id"])] = (metadata, payload)
        return SimpleNamespace(
            object_id=metadata["object_id"],
            payload_hash=metadata["integrity"]["payload_hash"],
            descriptor_hash=metadata["integrity"]["descriptor_hash"],
            stored=True,
            verified=True,
            reason="committed",
        )


def test_save_kv_layer_serializes_validates_and_records_metrics() -> None:
    client = RecordingBifrostClient()
    kv_config = FakeKVCacheConfig(num_layers=1, num_blocks=4, block_size=3)
    connector = BifrostKVConnector(
        FakeVllmConfig(kv_cache_config=kv_config),
        config={
            "connector_instance_id": "fake-save-0",
            "chunk_size": 64,
        },
        client=client,
    )
    caches = make_fake_kv_caches(1, 4, 3, 2, 4, seed=17)
    attn_metadata = FakeAttentionMetadata(
        request_id="request-save-0",
        layer_names=("layer_0",),
        block_ids=(1, 3),
    )

    connector.register_kv_caches(caches)
    connector.save_kv_layer("layer_0", caches["layer_0"], attn_metadata)
    connector.wait_for_save()

    assert len(client.put_calls) == 1
    put_call = client.put_calls[0]
    metadata = put_call["metadata"]
    payload = put_call["payload"]
    expected_payload = tensor_to_payload(
        flatten_layer_blocks(caches["layer_0"], (1, 3))
    )
    assert payload == expected_payload
    assert put_call["chunk_size"] == 64
    assert validate_object(metadata, payload, None).status == "accepted"
    assert metadata["engine_profile"]["engine_name"] == ENGINE_NAME
    assert metadata["engine_profile"]["integration_name"] == INTEGRATION_NAME
    assert metadata["engine_profile"]["kv_cache_format"] == KV_CACHE_FORMAT

    saved = connector.saved_objects
    assert saved == connector.get_kv_connector_stats()["saved_objects"]
    assert saved[0]["request_id"] == "request-save-0"
    assert saved[0]["layer_name"] == "layer_0"
    assert saved[0]["block_ids"] == [1, 3]
    assert saved[0]["object_id"] == metadata["object_id"]
    assert saved[0]["blob_key_hash"] == metadata["opaque_engine_profile"][
        "engine_key_hash"
    ]

    stats = connector.get_kv_connector_stats()
    assert stats["save_kv_layer_count"] == 1
    assert stats["save_success_count"] == 1
    assert stats["save_failure_count"] == 0
    assert stats["bytes_saved"] == len(payload)
    assert stats["objects_saved"] == 1
    assert stats["wait_for_save_count"] == 1
    assert stats["last_error_reason"] is None


def test_daemon_unavailable_causes_save_failure() -> None:
    kv_config = FakeKVCacheConfig(num_layers=1, num_blocks=2, block_size=2)
    connector = BifrostKVConnector(
        FakeVllmConfig(kv_cache_config=kv_config),
        config={
            "endpoint": "127.0.0.1:1",
            "timeout_seconds": 0.2,
            "connector_instance_id": "fake-daemon-unavailable",
        },
    )
    caches = make_fake_kv_caches(1, 2, 2, 1, 2, seed=1)
    attn_metadata = FakeAttentionMetadata(
        request_id="request-unavailable",
        layer_names=("layer_0",),
        block_ids=(0,),
    )
    connector.register_kv_caches(caches)

    with pytest.raises(DaemonUnavailableError):
        connector.save_kv_layer("layer_0", caches["layer_0"], attn_metadata)
    with pytest.raises(DaemonUnavailableError):
        connector.wait_for_save()

    stats = connector.get_kv_connector_stats()
    assert stats["save_failure_count"] == 1
    assert stats["daemon_error_count"] == 1
    assert stats["save_success_count"] == 0
    assert stats["last_error_reason"] == "daemon_unavailable"
    connector.shutdown()


def test_validation_failure_fails_closed_before_put() -> None:
    client = RecordingBifrostClient()
    kv_config = FakeKVCacheConfig(num_layers=1, num_blocks=2, block_size=2)
    connector = BifrostKVConnector(
        FakeVllmConfig(kv_cache_config=kv_config),
        config={"connector_instance_id": "fake-validation-failure"},
        client=client,
    )
    caches = make_fake_kv_caches(1, 2, 2, 1, 2, seed=2)
    attn_metadata = FakeAttentionMetadata(
        request_id="request-0xabcdef",
        layer_names=("layer_0",),
        block_ids=(0,),
    )
    connector.register_kv_caches(caches)

    with pytest.raises(KeyHashingError):
        connector.save_kv_layer("layer_0", caches["layer_0"], attn_metadata)
    with pytest.raises(KeyHashingError):
        connector.wait_for_save()

    assert client.put_calls == []
    stats = connector.get_kv_connector_stats()
    assert stats["save_failure_count"] == 1
    assert stats["validation_error_count"] == 1
    assert stats["objects_saved"] == 0
    assert stats["last_error_reason"] == "key_hashing_error"

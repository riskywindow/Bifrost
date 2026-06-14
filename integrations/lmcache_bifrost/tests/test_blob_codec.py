from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass

import pytest

from bifrost_kv.errors import (
    OPAQUE_WRONG_ENGINE_KEY,
    PAYLOAD_HASH_MISMATCH,
)
from bifrost_kv.hashing import (
    compute_descriptor_hash,
    compute_object_id,
    compute_payload_hash,
)
from bifrost_kv.validate import validate_object
from lmcache_bifrost.blob_codec import (
    build_opaque_metadata,
    build_opaque_target_profile,
    deserialize_memory_obj,
    serialize_memory_obj,
)
from lmcache_bifrost.config import BifrostLMCacheConfig
from lmcache_bifrost.errors import MemoryObjSerializationError


@dataclass(frozen=True)
class FakeCacheEngineKey:
    model_id: str
    block_hash: str
    tokens: tuple[int, ...]


@dataclass(frozen=True)
class FakeMemoryObj:
    payload: bytes
    dtype: str = "float16"


def test_fake_memory_obj_roundtrips_with_pickle_fallback_enabled() -> None:
    config = BifrostLMCacheConfig(allow_pickle_fallback=True)
    memory_obj = FakeMemoryObj(b"lmcache-owned-bytes")

    payload = serialize_memory_obj(memory_obj, config)
    restored = deserialize_memory_obj(payload, config)

    assert restored == memory_obj


def test_pickle_fallback_disabled_rejects_fake_object() -> None:
    config = BifrostLMCacheConfig()

    with pytest.raises(MemoryObjSerializationError):
        serialize_memory_obj(FakeMemoryObj(b"payload"), config)


def test_generated_opaque_metadata_validates() -> None:
    config = BifrostLMCacheConfig(allow_pickle_fallback=True)
    key = FakeCacheEngineKey("tiny", "abc", (1, 2, 3))
    memory_obj = FakeMemoryObj(b"payload")
    payload = serialize_memory_obj(memory_obj, config)

    metadata = build_opaque_metadata(key, memory_obj, payload, config)
    target = build_opaque_target_profile(key, config)
    result = validate_object(metadata, payload, target)

    assert result.status == "accepted"
    assert metadata["object_type"] == "opaque_engine_blob"
    assert metadata["engine_profile"]["engine_name"] == "lmcache"
    assert metadata["engine_profile"]["integration_name"] == (
        "lmcache_bifrost_remote_storage"
    )
    assert metadata["engine_profile"]["kv_layout"] == "opaque"
    assert metadata["engine_profile"]["kv_cache_format"] == (
        "opaque_lmcache_memory_obj"
    )
    assert metadata["opaque_engine_profile"]["engine_key_repr_version"] == (
        "lmcache_key_repr.v1"
    )
    assert metadata["payload_profile"]["byte_length"] == len(payload)


def test_corrupted_payload_fails_validation() -> None:
    config = BifrostLMCacheConfig(allow_pickle_fallback=True)
    key = FakeCacheEngineKey("tiny", "abc", (1, 2, 3))
    memory_obj = FakeMemoryObj(b"payload")
    payload = serialize_memory_obj(memory_obj, config)
    metadata = build_opaque_metadata(key, memory_obj, payload, config)

    corrupted = b"x" + payload[1:]
    result = validate_object(
        metadata,
        corrupted,
        build_opaque_target_profile(key, config),
    )

    assert result.reason_code == PAYLOAD_HASH_MISMATCH


def test_wrong_key_target_profile_fails_validation() -> None:
    config = BifrostLMCacheConfig(allow_pickle_fallback=True)
    key = FakeCacheEngineKey("tiny", "abc", (1, 2, 3))
    wrong_key = FakeCacheEngineKey("tiny", "other", (1, 2, 3))
    memory_obj = FakeMemoryObj(b"payload")
    payload = serialize_memory_obj(memory_obj, config)
    metadata = build_opaque_metadata(key, memory_obj, payload, config)

    result = validate_object(
        metadata,
        payload,
        build_opaque_target_profile(wrong_key, config),
    )

    assert result.reason_code == OPAQUE_WRONG_ENGINE_KEY


def test_object_id_is_stable_for_same_key_and_payload() -> None:
    config = BifrostLMCacheConfig(allow_pickle_fallback=True)
    key = FakeCacheEngineKey("tiny", "abc", (1, 2, 3))
    first_obj = FakeMemoryObj(b"payload")
    second_obj = FakeMemoryObj(b"payload")
    first_payload = serialize_memory_obj(first_obj, config)
    second_payload = serialize_memory_obj(second_obj, config)

    first = build_opaque_metadata(key, first_obj, first_payload, config)
    second = build_opaque_metadata(key, second_obj, second_payload, config)

    assert first_payload == second_payload
    assert first["object_id"] == second["object_id"]


def test_descriptor_mismatch_fails_validation() -> None:
    config = BifrostLMCacheConfig(allow_pickle_fallback=True)
    key = FakeCacheEngineKey("tiny", "abc", (1, 2, 3))
    memory_obj = FakeMemoryObj(b"payload")
    payload = serialize_memory_obj(memory_obj, config)
    metadata = build_opaque_metadata(key, memory_obj, payload, config)
    mutated = deepcopy(metadata)
    mutated["engine_profile"]["integration_version"] = "changed"

    result = validate_object(mutated, payload, build_opaque_target_profile(key, config))

    assert result.reason_code == "descriptor_hash_mismatch"


def test_object_id_recomputed_from_phase1_identity_helpers() -> None:
    config = BifrostLMCacheConfig(allow_pickle_fallback=True)
    key = FakeCacheEngineKey("tiny", "abc", (1, 2, 3))
    memory_obj = FakeMemoryObj(b"payload")
    payload = serialize_memory_obj(memory_obj, config)
    metadata = build_opaque_metadata(key, memory_obj, payload, config)
    payload_hash = compute_payload_hash(payload)
    descriptor_hash = compute_descriptor_hash(metadata, payload_hash)

    assert metadata["integrity"]["payload_hash"] == payload_hash
    assert metadata["integrity"]["descriptor_hash"] == descriptor_hash
    assert metadata["object_id"] == compute_object_id(descriptor_hash, payload_hash)

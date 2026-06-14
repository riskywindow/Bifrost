"""Map LMCache MemoryObj instances to BIFROST opaque_engine_blob objects."""

from __future__ import annotations

import pickle
import socket
from typing import Any

from bifrost_kv.hashing import (
    blake3_hex,
    compute_descriptor_hash,
    compute_object_id,
    compute_payload_hash,
)
from bifrost_kv.validate import validate_object

from lmcache_bifrost.config import BifrostLMCacheConfig
from lmcache_bifrost.errors import (
    MemoryObjDeserializationError,
    MemoryObjSerializationError,
    OpaqueBlobValidationError,
)
from lmcache_bifrost.key_codec import opaque_engine_key_hash
from lmcache_bifrost.lmcache_compat import (
    deserialize_with_lmcache_native,
    lmcache_version,
    serialize_with_lmcache_native,
)

SCHEMA_VERSION = "bifrost.kv_object.v1alpha1"
TARGET_SCHEMA_VERSION = "bifrost.target_profile.v1alpha1"
INTEGRATION_VERSION = "0.1.0"
PICKLE_MAGIC = b"bifrost.lmcache.pickle.v1\x00"
UNKNOWN_HASH = blake3_hex(b"bifrost.lmcache.unknown.v1")


def serialize_memory_obj(memory_obj: object, config: BifrostLMCacheConfig) -> bytes:
    """Serialize an LMCache MemoryObj without interpreting tensor semantics."""

    native = serialize_with_lmcache_native(memory_obj)
    if native is not None:
        return native
    if not config.allow_pickle_fallback:
        raise MemoryObjSerializationError(
            "LMCache-native MemoryObj serialization was not found and pickle fallback "
            "is disabled"
        )
    try:
        return PICKLE_MAGIC + pickle.dumps(memory_obj, protocol=pickle.HIGHEST_PROTOCOL)
    except Exception as exc:
        raise MemoryObjSerializationError(f"pickle fallback failed: {exc}") from exc


def deserialize_memory_obj(payload: bytes, config: BifrostLMCacheConfig) -> object:
    """Deserialize payload bytes with LMCache APIs, or test-only pickle fallback."""

    if payload.startswith(PICKLE_MAGIC):
        if not config.allow_pickle_fallback:
            raise MemoryObjDeserializationError("pickle fallback is disabled")
        try:
            return pickle.loads(payload[len(PICKLE_MAGIC) :])
        except Exception as exc:
            raise MemoryObjDeserializationError(f"pickle fallback failed: {exc}") from exc

    native = deserialize_with_lmcache_native(payload)
    if native is not None:
        return native
    raise MemoryObjDeserializationError(
        "LMCache-native MemoryObj deserialization was not found"
    )


def build_opaque_metadata(
    key: object,
    memory_obj: object,
    payload: bytes,
    config: BifrostLMCacheConfig,
) -> dict[str, Any]:
    """Build and optionally validate Phase 1 opaque_engine_blob metadata."""

    payload_hash = compute_payload_hash(payload)
    metadata: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "object_type": "opaque_engine_blob",
        "object_id": "bifrost://object/blake3/" + "0" * 64,
        "created_at_unix_ms": 0,
        "created_by": config.integration_name,
        "model_profile": _opaque_model_profile(),
        "engine_profile": _engine_profile(config),
        "prefix_profile": None,
        "payload_profile": {
            "byte_length": len(payload),
            "compression": "none",
            "payload_encoding": "raw_bytes",
        },
        "native_tensor_profile": None,
        "opaque_engine_profile": {
            "engine_key_hash": opaque_engine_key_hash(key),
            "engine_payload_type": _payload_type_name(memory_obj),
            "engine_key_repr_version": config.key_repr_version,
        },
        "integrity": {
            "descriptor_hash": "blake3:" + "0" * 64,
            "payload_hash": payload_hash,
            "object_id_algorithm": "bifrost.object_id.v1",
            "chunk_size_bytes": config.chunk_size,
            "chunk_hashes": _chunk_hashes(payload, config.chunk_size),
        },
        "provenance": {
            "source": "lmcache_bifrost_remote_storage",
            "notes": "LMCache MemoryObj payload treated as opaque bytes",
            "producer_commit": "unknown",
            "producer_hostname": socket.gethostname() or "localhost",
        },
    }
    descriptor_hash = compute_descriptor_hash(metadata, payload_hash)
    metadata["integrity"]["descriptor_hash"] = descriptor_hash
    metadata["object_id"] = compute_object_id(descriptor_hash, payload_hash)

    if config.strict_validation:
        result = validate_object(metadata, payload, build_opaque_target_profile(key, config))
        if result.status != "accepted":
            raise OpaqueBlobValidationError(
                f"generated opaque blob failed validation: {result.reason_code}"
            )
    return metadata


def build_opaque_target_profile(
    key: object,
    config: BifrostLMCacheConfig,
) -> dict[str, Any]:
    return {
        "schema_version": TARGET_SCHEMA_VERSION,
        "accepts_object_type": "opaque_engine_blob",
        "model_profile": None,
        "engine_profile": _engine_profile(config),
        "prefix_requirements": None,
        "opaque_requirements": {
            "engine_key_hash": opaque_engine_key_hash(key),
            "engine_payload_type": "opaque_lmcache_memory_obj",
            "engine_key_repr_version": config.key_repr_version,
        },
    }


def _engine_profile(config: BifrostLMCacheConfig) -> dict[str, Any]:
    return {
        "engine_name": config.engine_name,
        "engine_version": _lmcache_version(),
        "integration_name": config.integration_name,
        "integration_version": INTEGRATION_VERSION,
        "attention_impl": "engine_owned",
        "kv_layout": "opaque",
        "block_size_tokens": 1,
        "kv_cache_format": "opaque_lmcache_memory_obj",
    }


def _opaque_model_profile() -> dict[str, Any]:
    return {
        "model_id": "lmcache-opaque-unknown",
        "model_revision": "unknown",
        "model_hash": UNKNOWN_HASH,
        "tokenizer_hash": UNKNOWN_HASH,
        "config_hash": UNKNOWN_HASH,
        "rope_config_hash": UNKNOWN_HASH,
        "quantization": "unknown",
        "dtype": "opaque",
        "num_layers": 1,
        "num_attention_heads": 1,
        "num_kv_heads": 1,
        "head_dim": 1,
        "max_position_embeddings": 1,
    }


def _payload_type_name(memory_obj: object) -> str:
    module = memory_obj.__class__.__module__
    name = memory_obj.__class__.__qualname__
    if module == "builtins":
        return name
    if "lmcache" in module.lower():
        return f"{module}.{name}"
    return "opaque_lmcache_memory_obj"


def _chunk_hashes(payload: bytes, chunk_size: int) -> list[str]:
    if not payload:
        return []
    return [
        compute_payload_hash(payload[offset : offset + chunk_size])
        for offset in range(0, len(payload), chunk_size)
    ]


def _lmcache_version() -> str:
    return lmcache_version() or "uninstalled"


__all__ = [
    "PICKLE_MAGIC",
    "build_opaque_metadata",
    "build_opaque_target_profile",
    "deserialize_memory_obj",
    "serialize_memory_obj",
]

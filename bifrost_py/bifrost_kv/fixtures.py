"""Deterministic Phase 1 fixture builders."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Callable

from bifrost_kv import errors
from bifrost_kv.hashing import (
    blake3_hex,
    compute_descriptor_hash,
    compute_object_id,
    compute_payload_hash,
)

SCHEMA_VERSION = "bifrost.kv_object.v1alpha1"
TARGET_SCHEMA_VERSION = "bifrost.target_profile.v1alpha1"
CREATED_AT_UNIX_MS = 1779900000000
PRODUCER_COMMIT = "0" * 40

NATIVE_BYTE_LENGTH = 2 * 256 * 12 * 64 * 2
OPAQUE_BYTE_LENGTH = 4096
LM_CACHE_KEY = "lmcache://tiny-gpt/local/layer/0/block/0/prefix/demo"


def deterministic_payload(byte_length: int) -> bytes:
    return bytes([index % 251 for index in range(byte_length)])


def deterministic_hash(label: str) -> str:
    return blake3_hex(label.encode("utf-8"))


def native_payload() -> bytes:
    return deterministic_payload(NATIVE_BYTE_LENGTH)


def opaque_payload() -> bytes:
    return deterministic_payload(OPAQUE_BYTE_LENGTH)


def fake_lmcache_engine_key_hash() -> str:
    return deterministic_hash(LM_CACHE_KEY)


def finalize_identity(metadata: dict[str, Any], payload: bytes) -> dict[str, Any]:
    finalized = deepcopy(metadata)
    payload_hash = compute_payload_hash(payload)
    finalized["payload_profile"]["byte_length"] = len(payload)
    finalized["integrity"]["payload_hash"] = payload_hash
    finalized["integrity"]["chunk_size_bytes"] = len(payload)
    finalized["integrity"]["chunk_hashes"] = [payload_hash]
    finalized["integrity"]["descriptor_hash"] = compute_descriptor_hash(
        finalized, payload_hash
    )
    finalized["object_id"] = compute_object_id(
        finalized["integrity"]["descriptor_hash"], payload_hash
    )
    return finalized


def native_metadata() -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "object_type": "native_kv_page",
        "object_id": "bifrost://object/blake3/" + "0" * 64,
        "created_at_unix_ms": CREATED_AT_UNIX_MS,
        "created_by": "bifrost-phase1-fixture-generator",
        "model_profile": _native_model_profile(),
        "engine_profile": _native_engine_profile(),
        "prefix_profile": {
            "token_count": 256,
            "token_range": {"start": 0, "end": 256},
            "absolute_position_range": {"start": 0, "end": 256},
            "prefix_hash": deterministic_hash("tiny-gpt:prefix:0:256"),
            "token_hash": deterministic_hash("tiny-gpt:tokens:0:256"),
            "tokenizer_hash": deterministic_hash("tiny-gpt:tokenizer"),
            "rope_config_hash": deterministic_hash("tiny-gpt:rope"),
            "mm_hashes": [],
        },
        "payload_profile": {
            "byte_length": NATIVE_BYTE_LENGTH,
            "compression": "none",
            "payload_encoding": "raw_bytes",
        },
        "native_tensor_profile": {
            "layer_id": 0,
            "kv_block_id": 0,
            "block_size_tokens": 256,
            "block_token_count": 256,
            "token_range": {"start": 0, "end": 256},
            "tensor_role": "kv_pair",
            "tensor_shape": [2, 256, 12, 64],
            "tensor_dtype": "float16",
            "tensor_layout": "kv_token_head_dim",
        },
        "opaque_engine_profile": None,
        "integrity": {
            "descriptor_hash": "blake3:" + "0" * 64,
            "payload_hash": "blake3:" + "0" * 64,
            "object_id_algorithm": "bifrost.object_id.v1",
            "chunk_size_bytes": NATIVE_BYTE_LENGTH,
            "chunk_hashes": [],
        },
        "provenance": _provenance("native_valid"),
    }
    return finalize_identity(metadata, native_payload())


def native_target_profile() -> dict[str, Any]:
    metadata = native_metadata()
    return {
        "schema_version": TARGET_SCHEMA_VERSION,
        "accepts_object_type": "native_kv_page",
        "model_profile": deepcopy(metadata["model_profile"]),
        "engine_profile": deepcopy(metadata["engine_profile"]),
        "prefix_requirements": {
            "prefix_hash": metadata["prefix_profile"]["prefix_hash"],
            "token_hash": metadata["prefix_profile"]["token_hash"],
            "tokenizer_hash": metadata["prefix_profile"]["tokenizer_hash"],
            "rope_config_hash": metadata["prefix_profile"]["rope_config_hash"],
            "token_range": deepcopy(metadata["native_tensor_profile"]["token_range"]),
            "absolute_position_range": deepcopy(
                metadata["prefix_profile"]["absolute_position_range"]
            ),
            "allow_mm_hashes": [],
        },
        "opaque_requirements": None,
    }


def opaque_metadata() -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "object_type": "opaque_engine_blob",
        "object_id": "bifrost://object/blake3/" + "0" * 64,
        "created_at_unix_ms": CREATED_AT_UNIX_MS,
        "created_by": "bifrost-phase1-fixture-generator",
        "model_profile": _native_model_profile(),
        "engine_profile": {
            "engine_name": "lmcache",
            "engine_version": "0.1.0-fixture",
            "integration_name": "lmcache_bifrost_remote_storage",
            "integration_version": "0.1.0-fixture",
            "attention_impl": "engine_owned",
            "kv_layout": "opaque",
            "block_size_tokens": 256,
            "kv_cache_format": "opaque_lmcache_memory_obj",
        },
        "prefix_profile": None,
        "payload_profile": {
            "byte_length": OPAQUE_BYTE_LENGTH,
            "compression": "none",
            "payload_encoding": "raw_bytes",
        },
        "native_tensor_profile": None,
        "opaque_engine_profile": {
            "engine_key_hash": fake_lmcache_engine_key_hash(),
            "engine_payload_type": "lmcache_memory_obj",
            "engine_key_repr_version": "fixture-v1",
        },
        "integrity": {
            "descriptor_hash": "blake3:" + "0" * 64,
            "payload_hash": "blake3:" + "0" * 64,
            "object_id_algorithm": "bifrost.object_id.v1",
            "chunk_size_bytes": OPAQUE_BYTE_LENGTH,
            "chunk_hashes": [],
        },
        "provenance": _provenance("opaque_valid"),
    }
    return finalize_identity(metadata, opaque_payload())


def opaque_target_profile() -> dict[str, Any]:
    metadata = opaque_metadata()
    return {
        "schema_version": TARGET_SCHEMA_VERSION,
        "accepts_object_type": "opaque_engine_blob",
        "model_profile": None,
        "engine_profile": deepcopy(metadata["engine_profile"]),
        "prefix_requirements": None,
        "opaque_requirements": deepcopy(metadata["opaque_engine_profile"]),
    }


@dataclass(frozen=True, slots=True)
class FixtureCase:
    name: str
    metadata: dict[str, Any]
    payload: bytes
    target_profile: dict[str, Any]
    expected_reason: str


def invalid_fixture_cases() -> list[FixtureCase]:
    cases: list[FixtureCase] = []

    def native_case(
        name: str,
        reason: str,
        mutate: Callable[[dict[str, Any], bytes, dict[str, Any]], bytes | None],
        *,
        finalize: bool = True,
    ) -> None:
        metadata = native_metadata()
        payload = native_payload()
        target = native_target_profile()
        changed_payload = mutate(metadata, payload, target)
        if isinstance(changed_payload, bytes):
            payload = changed_payload
        if finalize:
            metadata = finalize_identity(metadata, payload)
        cases.append(FixtureCase(name, metadata, payload, target, reason))

    def opaque_case(
        name: str,
        reason: str,
        mutate: Callable[[dict[str, Any], bytes, dict[str, Any]], bytes | None],
        *,
        finalize: bool = True,
    ) -> None:
        metadata = opaque_metadata()
        payload = opaque_payload()
        target = opaque_target_profile()
        changed_payload = mutate(metadata, payload, target)
        if isinstance(changed_payload, bytes):
            payload = changed_payload
        if finalize:
            metadata = finalize_identity(metadata, payload)
        cases.append(FixtureCase(name, metadata, payload, target, reason))

    _add_native_compatibility_cases(native_case)
    _add_native_semantic_cases(native_case)
    _add_integrity_and_schema_cases(native_case)
    _add_opaque_cases(opaque_case)
    return cases


def _native_model_profile() -> dict[str, Any]:
    return {
        "model_id": "tiny-gpt",
        "model_revision": "local",
        "model_hash": deterministic_hash("tiny-gpt:model"),
        "tokenizer_hash": deterministic_hash("tiny-gpt:tokenizer"),
        "config_hash": deterministic_hash("tiny-gpt:config"),
        "rope_config_hash": deterministic_hash("tiny-gpt:rope"),
        "quantization": "none",
        "dtype": "float16",
        "num_layers": 12,
        "num_attention_heads": 12,
        "num_kv_heads": 12,
        "head_dim": 64,
        "max_position_embeddings": 8192,
    }


def _native_engine_profile() -> dict[str, Any]:
    return {
        "engine_name": "bifrost-reference",
        "engine_version": "0.1.0-fixture",
        "integration_name": "bifrost-python-reference",
        "integration_version": "0.1.0-fixture",
        "attention_impl": "flash_attention_2",
        "kv_layout": "kv_token_head_dim",
        "block_size_tokens": 256,
        "kv_cache_format": "native_kv_pair_v1",
    }


def _provenance(source: str) -> dict[str, str]:
    return {
        "source": source,
        "notes": "Generated by tools/generate_phase1_fixtures.py.",
        "producer_commit": PRODUCER_COMMIT,
        "producer_hostname": "localhost",
    }


def _add_native_compatibility_cases(add: Any) -> None:
    wrong_hash = deterministic_hash("wrong")
    add(
        "wrong_model_hash",
        errors.WRONG_MODEL_HASH,
        lambda _meta, _payload, target: target["model_profile"].__setitem__(
            "model_hash", wrong_hash
        ),
    )
    add(
        "wrong_tokenizer_hash",
        errors.WRONG_TOKENIZER_HASH,
        lambda _meta, _payload, target: target["model_profile"].__setitem__(
            "tokenizer_hash", wrong_hash
        ),
    )
    add(
        "wrong_config_hash",
        errors.WRONG_CONFIG_HASH,
        lambda _meta, _payload, target: target["model_profile"].__setitem__(
            "config_hash", wrong_hash
        ),
    )
    add(
        "wrong_rope_hash",
        errors.WRONG_ROPE_HASH,
        lambda _meta, _payload, target: target["model_profile"].__setitem__(
            "rope_config_hash", wrong_hash
        ),
    )
    add(
        "wrong_dtype",
        errors.WRONG_DTYPE,
        lambda _meta, _payload, target: target["model_profile"].__setitem__(
            "dtype", "bfloat16"
        ),
    )
    add(
        "wrong_engine_name",
        errors.WRONG_ENGINE_NAME,
        lambda _meta, _payload, target: target["engine_profile"].__setitem__(
            "engine_name", "other-engine"
        ),
    )
    add(
        "wrong_engine_version",
        errors.WRONG_ENGINE_VERSION,
        lambda _meta, _payload, target: target["engine_profile"].__setitem__(
            "engine_version", "9.9.9"
        ),
    )
    add(
        "wrong_attention_impl",
        errors.WRONG_ATTENTION_IMPL,
        lambda _meta, _payload, target: target["engine_profile"].__setitem__(
            "attention_impl", "eager"
        ),
    )
    add(
        "wrong_kv_layout",
        errors.WRONG_KV_LAYOUT,
        lambda _meta, _payload, target: target["engine_profile"].__setitem__(
            "kv_layout", "other_layout"
        ),
    )
    add(
        "wrong_block_size_tokens",
        errors.WRONG_BLOCK_SIZE_TOKENS,
        lambda _meta, _payload, target: target["engine_profile"].__setitem__(
            "block_size_tokens", 128
        ),
    )
    add(
        "wrong_kv_cache_format",
        errors.WRONG_KV_CACHE_FORMAT,
        lambda _meta, _payload, target: target["engine_profile"].__setitem__(
            "kv_cache_format", "other_format"
        ),
    )
    add(
        "wrong_prefix_hash",
        errors.WRONG_PREFIX_HASH,
        lambda _meta, _payload, target: target["prefix_requirements"].__setitem__(
            "prefix_hash", wrong_hash
        ),
    )
    add(
        "wrong_token_range",
        errors.WRONG_TOKEN_RANGE,
        lambda _meta, _payload, target: target["prefix_requirements"].__setitem__(
            "token_range", {"start": 1, "end": 257}
        ),
    )
    add(
        "wrong_absolute_position_range",
        errors.WRONG_ABSOLUTE_POSITION_RANGE,
        lambda _meta, _payload, target: target["prefix_requirements"].__setitem__(
            "absolute_position_range", {"start": 1, "end": 257}
        ),
    )


def _add_native_semantic_cases(add: Any) -> None:
    add(
        "invalid_layer_id",
        errors.INVALID_LAYER_ID,
        lambda meta, _payload, _target: meta["native_tensor_profile"].__setitem__(
            "layer_id", 12
        ),
    )
    add(
        "invalid_kv_block_id",
        errors.INVALID_KV_BLOCK_ID,
        lambda meta, _payload, _target: meta["native_tensor_profile"].__setitem__(
            "kv_block_id", 1
        ),
    )
    add(
        "invalid_block_token_count",
        errors.INVALID_BLOCK_TOKEN_COUNT,
        lambda meta, _payload, _target: meta["native_tensor_profile"].__setitem__(
            "block_token_count", 255
        ),
    )
    add(
        "invalid_tensor_shape",
        errors.INVALID_TENSOR_SHAPE,
        lambda meta, _payload, _target: meta["native_tensor_profile"].__setitem__(
            "tensor_shape", [2, 256, 12, 65]
        ),
    )
    add(
        "invalid_tensor_dtype",
        errors.INVALID_TENSOR_DTYPE,
        lambda meta, _payload, _target: meta["native_tensor_profile"].__setitem__(
            "tensor_dtype", "float32"
        ),
    )
    add(
        "invalid_tensor_layout",
        errors.INVALID_TENSOR_LAYOUT,
        lambda meta, _payload, _target: meta["native_tensor_profile"].__setitem__(
            "tensor_layout", "other_layout"
        ),
    )
    add(
        "unsupported_compression",
        errors.UNSUPPORTED_COMPRESSION,
        lambda meta, _payload, _target: meta["payload_profile"].__setitem__(
            "compression", "zstd"
        ),
    )
    add(
        "unsupported_payload_encoding",
        errors.UNSUPPORTED_PAYLOAD_ENCODING,
        lambda meta, _payload, _target: meta["payload_profile"].__setitem__(
            "payload_encoding", "base64"
        ),
    )


def _add_integrity_and_schema_cases(add: Any) -> None:
    add(
        "payload_hash_mismatch",
        errors.PAYLOAD_HASH_MISMATCH,
        lambda _meta, payload, _target: bytes([payload[0] ^ 0xFF]) + payload[1:],
        finalize=False,
    )
    add(
        "descriptor_hash_mismatch",
        errors.DESCRIPTOR_HASH_MISMATCH,
        lambda meta, _payload, _target: meta.__setitem__("created_by", "changed"),
        finalize=False,
    )
    add(
        "object_id_mismatch",
        errors.OBJECT_ID_MISMATCH,
        lambda meta, _payload, _target: meta.__setitem__(
            "object_id", "bifrost://object/blake3/" + "9" * 64
        ),
        finalize=False,
    )
    add(
        "byte_length_mismatch",
        errors.BYTE_LENGTH_MISMATCH,
        lambda meta, _payload, _target: meta["payload_profile"].__setitem__(
            "byte_length", meta["payload_profile"]["byte_length"] + 1
        ),
        finalize=False,
    )
    add(
        "missing_required_field",
        errors.MISSING_REQUIRED_FIELD,
        lambda meta, _payload, _target: meta.pop("created_by"),
        finalize=False,
    )
    add(
        "unknown_schema_version",
        errors.UNKNOWN_SCHEMA_VERSION,
        lambda meta, _payload, _target: meta.__setitem__(
            "schema_version", "bifrost.kv_object.v9"
        ),
        finalize=False,
    )
    add(
        "unknown_object_type",
        errors.UNKNOWN_OBJECT_TYPE,
        lambda meta, _payload, _target: meta.__setitem__(
            "object_type", "future_object"
        ),
        finalize=False,
    )


def _add_opaque_cases(add: Any) -> None:
    wrong_hash = deterministic_hash("opaque:wrong")
    add(
        "opaque_wrong_engine_key",
        errors.OPAQUE_WRONG_ENGINE_KEY,
        lambda _meta, _payload, target: target["opaque_requirements"].__setitem__(
            "engine_key_hash", wrong_hash
        ),
    )
    add(
        "opaque_wrong_engine_name",
        errors.OPAQUE_WRONG_ENGINE_NAME,
        lambda _meta, _payload, target: target["engine_profile"].__setitem__(
            "engine_name", "other-engine"
        ),
    )
    add(
        "opaque_wrong_integration_name",
        errors.OPAQUE_WRONG_INTEGRATION_NAME,
        lambda _meta, _payload, target: target["engine_profile"].__setitem__(
            "integration_name", "other_integration"
        ),
    )
    add(
        "opaque_with_native_tensor_profile",
        errors.OPAQUE_PAYLOAD_NOT_INTERPRETABLE,
        lambda meta, _payload, _target: meta.__setitem__(
            "native_tensor_profile", native_metadata()["native_tensor_profile"]
        ),
    )


__all__ = [
    "FixtureCase",
    "deterministic_payload",
    "fake_lmcache_engine_key_hash",
    "finalize_identity",
    "invalid_fixture_cases",
    "native_metadata",
    "native_payload",
    "native_target_profile",
    "opaque_metadata",
    "opaque_payload",
    "opaque_target_profile",
]

from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

from bifrost_kv import compute_descriptor_hash, compute_object_id, compute_payload_hash
from bifrost_kv.errors import (
    ACCEPTED,
    BYTE_LENGTH_MISMATCH,
    DESCRIPTOR_HASH_MISMATCH,
    SCHEMA_VALIDATION_FAILED,
    INVALID_KV_BLOCK_ID,
    INVALID_LAYER_ID,
    INVALID_TENSOR_SHAPE,
    OBJECT_ID_MISMATCH,
    PAYLOAD_HASH_MISMATCH,
    WRONG_ATTENTION_IMPL,
    WRONG_BLOCK_SIZE_TOKENS,
    WRONG_CONFIG_HASH,
    WRONG_DTYPE,
    WRONG_ENGINE_NAME,
    WRONG_KV_LAYOUT,
    WRONG_MODEL_HASH,
    WRONG_PREFIX_HASH,
    WRONG_ROPE_HASH,
    WRONG_TOKEN_RANGE,
    WRONG_TOKENIZER_HASH,
)
from bifrost_kv.fixtures import native_layer3_block7_metadata
from bifrost_kv.validate import validate_object

HASHES = {
    "model": "blake3:" + "a" * 64,
    "tokenizer": "blake3:" + "b" * 64,
    "config": "blake3:" + "c" * 64,
    "rope": "blake3:" + "d" * 64,
    "prefix": "blake3:" + "e" * 64,
    "token": "blake3:" + "f" * 64,
    "wrong": "blake3:" + "9" * 64,
}


def native_payload() -> bytes:
    return bytes(range(96))


def payload_bytes(byte_length: int) -> bytes:
    return bytes(index % 251 for index in range(byte_length))


def finalize_identity(metadata: dict[str, Any], payload: bytes) -> dict[str, Any]:
    metadata = deepcopy(metadata)
    payload_hash = compute_payload_hash(payload)
    metadata["integrity"]["payload_hash"] = payload_hash
    descriptor_hash = compute_descriptor_hash(metadata, payload_hash)
    metadata["integrity"]["descriptor_hash"] = descriptor_hash
    metadata["object_id"] = compute_object_id(descriptor_hash, payload_hash)
    return metadata


def native_metadata() -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "schema_version": "bifrost.kv_object.v1alpha1",
        "object_type": "native_kv_page",
        "object_id": "bifrost://object/blake3/" + "1" * 64,
        "created_at_unix_ms": 1779900000000,
        "created_by": "test",
        "model_profile": {
            "model_id": "example/model",
            "model_revision": "rev-a",
            "model_hash": HASHES["model"],
            "tokenizer_hash": HASHES["tokenizer"],
            "config_hash": HASHES["config"],
            "rope_config_hash": HASHES["rope"],
            "quantization": "none",
            "dtype": "float16",
            "num_layers": 2,
            "num_attention_heads": 4,
            "num_kv_heads": 2,
            "head_dim": 3,
            "max_position_embeddings": 128,
        },
        "engine_profile": {
            "engine_name": "engine-a",
            "engine_version": "1.0",
            "integration_name": "integration-a",
            "integration_version": "1.0",
            "attention_impl": "flash",
            "kv_layout": "native-layout",
            "block_size_tokens": 4,
            "kv_cache_format": "native-v1",
        },
        "prefix_profile": {
            "token_count": 4,
            "token_range": {"start": 0, "end": 4},
            "absolute_position_range": {"start": 10, "end": 14},
            "prefix_hash": HASHES["prefix"],
            "token_hash": HASHES["token"],
            "tokenizer_hash": HASHES["tokenizer"],
            "rope_config_hash": HASHES["rope"],
            "mm_hashes": [],
        },
        "payload_profile": {
            "byte_length": len(native_payload()),
            "compression": "none",
            "payload_encoding": "raw_bytes",
        },
        "native_tensor_profile": {
            "layer_id": 1,
            "kv_block_id": 0,
            "block_size_tokens": 4,
            "block_token_count": 4,
            "token_range": {"start": 0, "end": 4},
            "tensor_role": "kv_pair",
            "tensor_shape": [2, 4, 2, 3],
            "tensor_dtype": "float16",
            "tensor_layout": "kv_token_head_dim",
        },
        "opaque_engine_profile": None,
        "integrity": {
            "descriptor_hash": "blake3:" + "2" * 64,
            "payload_hash": "blake3:" + "3" * 64,
            "object_id_algorithm": "bifrost.object_id.v1",
            "chunk_size_bytes": len(native_payload()),
            "chunk_hashes": [],
        },
        "provenance": {
            "source": "test",
            "notes": "",
            "producer_commit": "0" * 40,
            "producer_hostname": "localhost",
        },
    }
    return finalize_identity(metadata, native_payload())


def native_target_profile() -> dict[str, Any]:
    metadata = native_metadata()
    return {
        "schema_version": "bifrost.target_profile.v1alpha1",
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


def test_valid_native_accepted() -> None:
    result = validate_object(native_metadata(), native_payload(), native_target_profile())

    assert result.status == "accepted"
    assert result.reason_code == ACCEPTED


@pytest.mark.parametrize(
    ("dtype", "byte_width"),
    [
        ("float16", 2),
        ("bfloat16", 2),
        ("float32", 4),
    ],
)
def test_native_payload_byte_length_matches_dtype_width(
    dtype: str, byte_width: int
) -> None:
    metadata = native_metadata()
    expected_length = 2 * 4 * 2 * 3 * byte_width
    payload = payload_bytes(expected_length)
    metadata["model_profile"]["dtype"] = dtype
    metadata["native_tensor_profile"]["tensor_dtype"] = dtype
    metadata["payload_profile"]["byte_length"] = expected_length
    metadata["integrity"]["chunk_size_bytes"] = expected_length
    metadata = finalize_identity(metadata, payload)

    result = validate_object(metadata, payload)

    assert result.status == "accepted"
    assert result.reason_code == ACCEPTED


def test_native_tensor_shape_must_match_exact_rank_and_dimensions() -> None:
    metadata = native_metadata()
    metadata["native_tensor_profile"]["tensor_shape"] = [2, 4, 2, 3, 1]
    metadata = finalize_identity(metadata, native_payload())

    result = validate_object(metadata, native_payload())

    assert result.reason_code == INVALID_TENSOR_SHAPE


def test_native_kv_block_id_calculation_accepts_nonzero_block() -> None:
    metadata = native_layer3_block7_metadata()
    payload = (
        bytes(index % 251 for index in range(metadata["payload_profile"]["byte_length"]))
    )

    result = validate_object(metadata, payload)

    assert result.status == "accepted"
    assert metadata["native_tensor_profile"]["kv_block_id"] == 7
    assert metadata["native_tensor_profile"]["token_range"]["start"] == 1792


@pytest.mark.parametrize(
    ("path", "value", "reason"),
    [
        (("model_profile", "model_hash"), HASHES["wrong"], WRONG_MODEL_HASH),
        (("model_profile", "tokenizer_hash"), HASHES["wrong"], WRONG_TOKENIZER_HASH),
        (("model_profile", "config_hash"), HASHES["wrong"], WRONG_CONFIG_HASH),
        (("model_profile", "rope_config_hash"), HASHES["wrong"], WRONG_ROPE_HASH),
        (("model_profile", "dtype"), "float32", WRONG_DTYPE),
        (("engine_profile", "engine_name"), "engine-b", WRONG_ENGINE_NAME),
        (("engine_profile", "attention_impl"), "eager", WRONG_ATTENTION_IMPL),
        (("engine_profile", "kv_layout"), "other-layout", WRONG_KV_LAYOUT),
        (("engine_profile", "block_size_tokens"), 8, WRONG_BLOCK_SIZE_TOKENS),
        (("prefix_requirements", "prefix_hash"), HASHES["wrong"], WRONG_PREFIX_HASH),
        (
            ("prefix_requirements", "token_range"),
            {"start": 1, "end": 5},
            WRONG_TOKEN_RANGE,
        ),
    ],
)
def test_native_target_mismatches_rejected(
    path: tuple[str, str], value: Any, reason: str
) -> None:
    target = native_target_profile()
    target[path[0]][path[1]] = value

    assert validate_object(native_metadata(), native_payload(), target).reason_code == reason


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (
            lambda meta: meta["native_tensor_profile"].__setitem__("layer_id", 2),
            INVALID_LAYER_ID,
        ),
        (
            lambda meta: meta["native_tensor_profile"].__setitem__("kv_block_id", 1),
            INVALID_KV_BLOCK_ID,
        ),
        (
            lambda meta: meta["native_tensor_profile"].__setitem__(
                "tensor_shape", [2, 4, 2, 4]
            ),
            INVALID_TENSOR_SHAPE,
        ),
    ],
)
def test_native_semantic_mismatches_rejected(mutate: Any, reason: str) -> None:
    metadata = native_metadata()
    mutate(metadata)
    metadata = finalize_identity(metadata, native_payload())

    assert validate_object(metadata, native_payload()).reason_code == reason


def test_byte_length_mismatch_rejected() -> None:
    metadata = native_metadata()
    metadata["payload_profile"]["byte_length"] += 1

    assert validate_object(metadata, native_payload()).reason_code == BYTE_LENGTH_MISMATCH


def test_payload_hash_mismatch_rejected() -> None:
    metadata = native_metadata()
    metadata["integrity"]["payload_hash"] = HASHES["wrong"]

    assert validate_object(metadata, native_payload()).reason_code == PAYLOAD_HASH_MISMATCH


def test_descriptor_hash_mismatch_rejected() -> None:
    metadata = native_metadata()
    metadata["integrity"]["descriptor_hash"] = HASHES["wrong"]

    assert validate_object(metadata, native_payload()).reason_code == DESCRIPTOR_HASH_MISMATCH


def test_object_id_mismatch_rejected() -> None:
    metadata = native_metadata()
    metadata["object_id"] = "bifrost://object/blake3/" + "9" * 64

    assert validate_object(metadata, native_payload()).reason_code == OBJECT_ID_MISMATCH


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (
            lambda meta: meta["payload_profile"].__setitem__("byte_length", -1),
            SCHEMA_VALIDATION_FAILED,
        ),
        (
            lambda meta: meta["native_tensor_profile"]["token_range"].__setitem__(
                "start", -1
            ),
            SCHEMA_VALIDATION_FAILED,
        ),
        (
            lambda meta: meta["native_tensor_profile"].__setitem__("layer_id", -1),
            SCHEMA_VALIDATION_FAILED,
        ),
        (
            lambda meta: meta["native_tensor_profile"].__setitem__("kv_block_id", -1),
            SCHEMA_VALIDATION_FAILED,
        ),
        (
            lambda meta: meta["native_tensor_profile"].__setitem__(
                "block_token_count", -1
            ),
            SCHEMA_VALIDATION_FAILED,
        ),
    ],
)
def test_negative_native_numbers_fail_closed(mutate: Any, reason: str) -> None:
    metadata = native_metadata()
    mutate(metadata)

    assert validate_object(metadata, native_payload()).reason_code == reason

"""Python reference KV object validator for BIFROST Phase 1."""

from __future__ import annotations

from typing import Any

from bifrost_kv import errors
from bifrost_kv.hashing import (
    compute_descriptor_hash,
    compute_object_id,
    compute_payload_hash,
)
from bifrost_kv.result import ValidationResult
from bifrost_kv.schema import validate_json_schema
from bifrost_kv.target_profile import (
    check_native_compatibility,
    check_opaque_compatibility,
)

KV_OBJECT_SCHEMA = "bifrost_kv_object.v1alpha1.schema.json"
SUPPORTED_SCHEMA_VERSION = "bifrost.kv_object.v1alpha1"
SUPPORTED_OBJECT_TYPES = frozenset(("native_kv_page", "opaque_engine_blob"))
DTYPE_BYTE_WIDTHS = {
    "float16": 2,
    "bfloat16": 2,
    "float32": 4,
}


def validate_object(
    metadata: dict[str, Any],
    payload: bytes,
    target_profile: dict[str, Any] | None = None,
) -> ValidationResult:
    if not isinstance(metadata, dict):
        return ValidationResult.rejected(errors.PARSE_ERROR)

    schema_version = metadata.get("schema_version")
    if schema_version != SUPPORTED_SCHEMA_VERSION:
        if schema_version is None:
            return ValidationResult.rejected(errors.MISSING_REQUIRED_FIELD)
        if not isinstance(schema_version, str):
            return ValidationResult.rejected(errors.INVALID_FIELD_TYPE)
        return ValidationResult.rejected(errors.UNKNOWN_SCHEMA_VERSION)

    object_type = metadata.get("object_type")
    if object_type not in SUPPORTED_OBJECT_TYPES:
        if object_type is None:
            return ValidationResult.rejected(errors.MISSING_REQUIRED_FIELD)
        if not isinstance(object_type, str):
            return ValidationResult.rejected(errors.INVALID_FIELD_TYPE)
        return ValidationResult.rejected(errors.UNKNOWN_OBJECT_TYPE)

    reason = _schema_reason(validate_json_schema(metadata, KV_OBJECT_SCHEMA))
    if reason is not None:
        return ValidationResult.rejected(reason)

    payload_profile = metadata["payload_profile"]
    if payload_profile["compression"] != "none":
        return ValidationResult.rejected(errors.UNSUPPORTED_COMPRESSION)
    if payload_profile["payload_encoding"] != "raw_bytes":
        return ValidationResult.rejected(errors.UNSUPPORTED_PAYLOAD_ENCODING)

    if payload_profile["byte_length"] != len(payload):
        return ValidationResult.rejected(errors.BYTE_LENGTH_MISMATCH)

    payload_hash = compute_payload_hash(payload)
    if metadata["integrity"]["payload_hash"] != payload_hash:
        return ValidationResult.rejected(
            errors.PAYLOAD_HASH_MISMATCH,
            object_id=metadata["object_id"],
            payload_hash=payload_hash,
        )

    descriptor_hash = compute_descriptor_hash(metadata, payload_hash)
    if metadata["integrity"]["descriptor_hash"] != descriptor_hash:
        return ValidationResult.rejected(
            errors.DESCRIPTOR_HASH_MISMATCH,
            object_id=metadata["object_id"],
            payload_hash=payload_hash,
            descriptor_hash=descriptor_hash,
        )

    object_id = compute_object_id(descriptor_hash, payload_hash)
    if metadata["object_id"] != object_id:
        return ValidationResult.rejected(
            errors.OBJECT_ID_MISMATCH,
            object_id=object_id,
            payload_hash=payload_hash,
            descriptor_hash=descriptor_hash,
        )

    if target_profile is not None:
        reason = _check_target_compatibility(metadata, target_profile)
        if reason is not None:
            return ValidationResult.rejected(
                reason,
                object_id=object_id,
                payload_hash=payload_hash,
                descriptor_hash=descriptor_hash,
            )

    reason = _check_native_semantics(metadata) if object_type == "native_kv_page" else (
        _check_opaque_semantics(metadata)
    )
    if reason is not None:
        return ValidationResult.rejected(
            reason,
            object_id=object_id,
            payload_hash=payload_hash,
            descriptor_hash=descriptor_hash,
        )

    return ValidationResult.accepted(
        object_id=object_id,
        payload_hash=payload_hash,
        descriptor_hash=descriptor_hash,
    )


def _check_target_compatibility(
    metadata: dict[str, Any], target_profile: dict[str, Any]
) -> str | None:
    if metadata["object_type"] == "native_kv_page":
        return check_native_compatibility(metadata, target_profile)
    return check_opaque_compatibility(metadata, target_profile)


def _check_native_semantics(metadata: dict[str, Any]) -> str | None:
    model = metadata["model_profile"]
    engine = metadata["engine_profile"]
    prefix = metadata["prefix_profile"]
    native = metadata["native_tensor_profile"]
    payload_profile = metadata["payload_profile"]

    if native is None or prefix is None or metadata["opaque_engine_profile"] is not None:
        return errors.SCHEMA_VALIDATION_FAILED

    if model["dtype"] != native["tensor_dtype"]:
        return errors.INVALID_TENSOR_DTYPE
    if native["tensor_dtype"] not in DTYPE_BYTE_WIDTHS:
        return errors.INVALID_TENSOR_DTYPE
    if native["tensor_layout"] != "kv_token_head_dim":
        return errors.INVALID_TENSOR_LAYOUT
    if native["tensor_role"] != "kv_pair":
        return errors.INVALID_TENSOR_SHAPE

    reason = _validate_non_empty_range(native["token_range"], errors.WRONG_TOKEN_RANGE)
    if reason is not None:
        return reason
    reason = _validate_non_empty_range(prefix["token_range"], errors.WRONG_TOKEN_RANGE)
    if reason is not None:
        return reason
    reason = _validate_non_empty_range(
        prefix["absolute_position_range"], errors.WRONG_ABSOLUTE_POSITION_RANGE
    )
    if reason is not None:
        return reason

    block_token_count = native["token_range"]["end"] - native["token_range"]["start"]
    if native["block_token_count"] != block_token_count:
        return errors.INVALID_BLOCK_TOKEN_COUNT
    if block_token_count <= 0 or block_token_count > native["block_size_tokens"]:
        return errors.INVALID_BLOCK_TOKEN_COUNT
    if native["block_size_tokens"] != engine["block_size_tokens"]:
        return errors.WRONG_BLOCK_SIZE_TOKENS

    if native["kv_block_id"] != native["token_range"]["start"] // native["block_size_tokens"]:
        return errors.INVALID_KV_BLOCK_ID
    if not 0 <= native["layer_id"] < model["num_layers"]:
        return errors.INVALID_LAYER_ID

    expected_shape = [
        2,
        block_token_count,
        model["num_kv_heads"],
        model["head_dim"],
    ]
    if native["tensor_shape"] != expected_shape:
        return errors.INVALID_TENSOR_SHAPE

    expected_byte_length = (
        2
        * block_token_count
        * model["num_kv_heads"]
        * model["head_dim"]
        * DTYPE_BYTE_WIDTHS[native["tensor_dtype"]]
    )
    if payload_profile["byte_length"] != expected_byte_length:
        return errors.BYTE_LENGTH_MISMATCH

    if prefix["tokenizer_hash"] != model["tokenizer_hash"]:
        return errors.WRONG_TOKENIZER_HASH
    if prefix["rope_config_hash"] != model["rope_config_hash"]:
        return errors.WRONG_ROPE_HASH
    return None


def _check_opaque_semantics(metadata: dict[str, Any]) -> str | None:
    if metadata["opaque_engine_profile"] is None:
        return errors.SCHEMA_VALIDATION_FAILED
    if metadata["native_tensor_profile"] is not None:
        return errors.OPAQUE_PAYLOAD_NOT_INTERPRETABLE
    return None


def _validate_non_empty_range(range_value: dict[str, int], reason: str) -> str | None:
    if range_value["end"] <= range_value["start"]:
        return reason
    return None


def _schema_reason(messages: list[str]) -> str | None:
    if not messages:
        return None

    first_location = messages[0].split(":", maxsplit=1)[0]
    location_messages = [
        message
        for message in messages
        if message.split(":", maxsplit=1)[0] == first_location
    ]
    if any("is a required property" in message for message in location_messages):
        return errors.MISSING_REQUIRED_FIELD
    if any(
        "Additional properties are not allowed" in message
        for message in location_messages
    ):
        return errors.EXTRA_FIELD_REJECTED
    if any("is not of type" in message for message in location_messages):
        return errors.INVALID_FIELD_TYPE
    return errors.SCHEMA_VALIDATION_FAILED


__all__ = [
    "DTYPE_BYTE_WIDTHS",
    "KV_OBJECT_SCHEMA",
    "SUPPORTED_OBJECT_TYPES",
    "SUPPORTED_SCHEMA_VERSION",
    "validate_object",
]

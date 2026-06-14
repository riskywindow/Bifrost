"""Target profile validation and compatibility checks for Phase 1."""

from __future__ import annotations

from typing import Any

from bifrost_kv import errors
from bifrost_kv.schema import validate_json_schema

TARGET_PROFILE_SCHEMA = "bifrost_target_profile.v1alpha1.schema.json"
SUPPORTED_TARGET_SCHEMA_VERSION = "bifrost.target_profile.v1alpha1"


def validate_target_profile_schema(target_profile: dict[str, Any]) -> str | None:
    if not isinstance(target_profile, dict):
        return errors.SCHEMA_VALIDATION_FAILED
    if target_profile.get("schema_version") != SUPPORTED_TARGET_SCHEMA_VERSION:
        return errors.UNKNOWN_SCHEMA_VERSION
    return _schema_reason(validate_json_schema(target_profile, TARGET_PROFILE_SCHEMA))


def check_native_compatibility(
    metadata: dict[str, Any], target_profile: dict[str, Any]
) -> str | None:
    reason = validate_target_profile_schema(target_profile)
    if reason is not None:
        return reason

    if target_profile["accepts_object_type"] != "native_kv_page":
        return errors.UNKNOWN_OBJECT_TYPE

    object_model = metadata["model_profile"]
    target_model = target_profile["model_profile"]
    object_engine = metadata["engine_profile"]
    target_engine = target_profile["engine_profile"]
    object_prefix = metadata["prefix_profile"]
    target_prefix = target_profile["prefix_requirements"]
    native = metadata["native_tensor_profile"]
    if object_prefix is None or native is None:
        return errors.SCHEMA_VALIDATION_FAILED

    comparisons = (
        (object_model["model_hash"], target_model["model_hash"], errors.WRONG_MODEL_HASH),
        (
            object_model["tokenizer_hash"],
            target_model["tokenizer_hash"],
            errors.WRONG_TOKENIZER_HASH,
        ),
        (object_model["config_hash"], target_model["config_hash"], errors.WRONG_CONFIG_HASH),
        (
            object_model["rope_config_hash"],
            target_model["rope_config_hash"],
            errors.WRONG_ROPE_HASH,
        ),
        (object_model["dtype"], target_model["dtype"], errors.WRONG_DTYPE),
        (
            object_model["num_layers"],
            target_model["num_layers"],
            errors.WRONG_NUM_LAYERS,
        ),
        (
            object_model["num_kv_heads"],
            target_model["num_kv_heads"],
            errors.WRONG_NUM_KV_HEADS,
        ),
        (object_model["head_dim"], target_model["head_dim"], errors.WRONG_HEAD_DIM),
        (
            object_engine["engine_name"],
            target_engine["engine_name"],
            errors.WRONG_ENGINE_NAME,
        ),
        (
            object_engine["engine_version"],
            target_engine["engine_version"],
            errors.WRONG_ENGINE_VERSION,
        ),
        (
            object_engine["attention_impl"],
            target_engine["attention_impl"],
            errors.WRONG_ATTENTION_IMPL,
        ),
        (object_engine["kv_layout"], target_engine["kv_layout"], errors.WRONG_KV_LAYOUT),
        (
            object_engine["block_size_tokens"],
            target_engine["block_size_tokens"],
            errors.WRONG_BLOCK_SIZE_TOKENS,
        ),
        (
            object_engine["kv_cache_format"],
            target_engine["kv_cache_format"],
            errors.WRONG_KV_CACHE_FORMAT,
        ),
        (
            object_prefix["prefix_hash"],
            target_prefix["prefix_hash"],
            errors.WRONG_PREFIX_HASH,
        ),
        (
            object_prefix["token_hash"],
            target_prefix["token_hash"],
            errors.WRONG_PREFIX_HASH,
        ),
        (
            object_prefix["tokenizer_hash"],
            target_prefix["tokenizer_hash"],
            errors.WRONG_TOKENIZER_HASH,
        ),
        (
            object_prefix["rope_config_hash"],
            target_prefix["rope_config_hash"],
            errors.WRONG_ROPE_HASH,
        ),
        (
            object_prefix["mm_hashes"],
            target_prefix["allow_mm_hashes"],
            errors.WRONG_PREFIX_HASH,
        ),
        (
            native["token_range"],
            target_prefix["token_range"],
            errors.WRONG_TOKEN_RANGE,
        ),
        (
            object_prefix["absolute_position_range"],
            target_prefix["absolute_position_range"],
            errors.WRONG_ABSOLUTE_POSITION_RANGE,
        ),
    )
    for observed, expected, reason_code in comparisons:
        if observed != expected:
            return reason_code
    return None


def check_opaque_compatibility(
    metadata: dict[str, Any], target_profile: dict[str, Any]
) -> str | None:
    reason = validate_target_profile_schema(target_profile)
    if reason is not None:
        return reason

    if target_profile["accepts_object_type"] != "opaque_engine_blob":
        return errors.UNKNOWN_OBJECT_TYPE

    object_engine = metadata["engine_profile"]
    target_engine = target_profile["engine_profile"]
    opaque = metadata["opaque_engine_profile"]
    opaque_requirements = target_profile["opaque_requirements"]
    if opaque is None:
        return errors.SCHEMA_VALIDATION_FAILED

    comparisons = (
        (
            object_engine["engine_name"],
            target_engine["engine_name"],
            errors.OPAQUE_WRONG_ENGINE_NAME,
        ),
        (
            object_engine["integration_name"],
            target_engine["integration_name"],
            errors.OPAQUE_WRONG_INTEGRATION_NAME,
        ),
        (
            object_engine["kv_cache_format"],
            target_engine["kv_cache_format"],
            errors.WRONG_KV_CACHE_FORMAT,
        ),
        (
            opaque["engine_key_hash"],
            opaque_requirements["engine_key_hash"],
            errors.OPAQUE_WRONG_ENGINE_KEY,
        ),
    )
    for observed, expected, reason_code in comparisons:
        if observed != expected:
            return reason_code
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
    "SUPPORTED_TARGET_SCHEMA_VERSION",
    "TARGET_PROFILE_SCHEMA",
    "check_native_compatibility",
    "check_opaque_compatibility",
    "validate_target_profile_schema",
]

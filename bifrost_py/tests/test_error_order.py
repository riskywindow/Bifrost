from __future__ import annotations

from bifrost_kv.errors import (
    BYTE_LENGTH_MISMATCH,
    DESCRIPTOR_HASH_MISMATCH,
    EXTRA_FIELD_REJECTED,
    MISSING_REQUIRED_FIELD,
    OBJECT_ID_MISMATCH,
    PAYLOAD_HASH_MISMATCH,
    SCHEMA_VALIDATION_FAILED,
    UNKNOWN_SCHEMA_VERSION,
    UNSUPPORTED_COMPRESSION,
    UNSUPPORTED_PAYLOAD_ENCODING,
    WRONG_TOKENIZER_HASH,
)
from bifrost_kv.hashing import compute_descriptor_hash, compute_object_id, compute_payload_hash
from bifrost_kv.validate import validate_object
from test_native_validation import (
    HASHES,
    finalize_identity,
    native_metadata,
    native_payload,
    native_target_profile,
)


def test_schema_version_is_checked_before_object_type() -> None:
    metadata = native_metadata()
    metadata["schema_version"] = "bifrost.kv_object.future"
    metadata["object_type"] = "future_object"

    result = validate_object(metadata, native_payload())

    assert result.reason_code == UNKNOWN_SCHEMA_VERSION


def test_payload_length_is_checked_before_payload_hash() -> None:
    metadata = native_metadata()
    metadata["payload_profile"]["byte_length"] += 1
    metadata["integrity"]["payload_hash"] = HASHES["wrong"]

    result = validate_object(metadata, native_payload())

    assert result.reason_code == BYTE_LENGTH_MISMATCH


def test_payload_hash_is_checked_before_target_compatibility() -> None:
    metadata = native_metadata()
    target = native_target_profile()
    metadata["integrity"]["payload_hash"] = HASHES["wrong"]
    target["model_profile"]["tokenizer_hash"] = HASHES["wrong"]

    result = validate_object(metadata, native_payload(), target)

    assert result.reason_code == PAYLOAD_HASH_MISMATCH
    assert result.reason_code != WRONG_TOKENIZER_HASH


def test_descriptor_hash_is_checked_before_object_id() -> None:
    metadata = native_metadata()
    metadata["integrity"]["descriptor_hash"] = HASHES["wrong"]
    metadata["object_id"] = "bifrost://object/blake3/" + "9" * 64

    result = validate_object(metadata, native_payload())

    assert result.reason_code == DESCRIPTOR_HASH_MISMATCH


def test_descriptor_hash_is_checked_before_object_id_when_both_are_wrong() -> None:
    metadata = native_metadata()
    metadata["created_by"] = "changed"
    metadata["integrity"]["descriptor_hash"] = HASHES["wrong"]
    metadata["object_id"] = "bifrost://object/blake3/" + "9" * 64

    result = validate_object(metadata, native_payload())

    assert result.reason_code == DESCRIPTOR_HASH_MISMATCH


def test_object_id_binds_descriptor_metadata() -> None:
    metadata = native_metadata()
    original_object_id = metadata["object_id"]
    payload_hash = metadata["integrity"]["payload_hash"]
    metadata["created_by"] = "changed"
    metadata["integrity"]["descriptor_hash"] = compute_descriptor_hash(
        metadata, payload_hash
    )
    assert metadata["object_id"] == original_object_id

    result = validate_object(metadata, native_payload())

    assert result.reason_code == OBJECT_ID_MISMATCH


def test_object_id_binds_payload_hash() -> None:
    metadata = native_metadata()
    original_object_id = metadata["object_id"]
    payload = b"x" + native_payload()[1:]
    payload_hash = compute_payload_hash(payload)
    metadata["payload_profile"]["byte_length"] = len(payload)
    metadata["integrity"]["payload_hash"] = payload_hash
    metadata["integrity"]["descriptor_hash"] = compute_descriptor_hash(
        metadata, payload_hash
    )
    assert compute_object_id(metadata["integrity"]["descriptor_hash"], payload_hash) != (
        original_object_id
    )
    assert metadata["object_id"] == original_object_id

    result = validate_object(metadata, payload)

    assert result.reason_code == OBJECT_ID_MISMATCH


def test_unsupported_compression_is_checked_before_payload_length() -> None:
    metadata = native_metadata()
    metadata["payload_profile"]["compression"] = "zstd"
    metadata["payload_profile"]["byte_length"] += 1

    result = validate_object(metadata, native_payload())

    assert result.reason_code == UNSUPPORTED_COMPRESSION


def test_unsupported_payload_encoding_is_checked_before_payload_length() -> None:
    metadata = native_metadata()
    metadata["payload_profile"]["payload_encoding"] = "base64"
    metadata["payload_profile"]["byte_length"] += 1

    result = validate_object(metadata, native_payload())

    assert result.reason_code == UNSUPPORTED_PAYLOAD_ENCODING


def test_missing_field_is_checked_before_extra_field() -> None:
    metadata = native_metadata()
    metadata.pop("created_by")
    metadata["local_tier"] = "ram"

    result = validate_object(metadata, native_payload())

    assert result.reason_code == MISSING_REQUIRED_FIELD


def test_extra_field_is_checked_before_field_type() -> None:
    metadata = native_metadata()
    metadata["local_tier"] = "ram"
    metadata["created_at_unix_ms"] = "not-an-integer"

    result = validate_object(metadata, native_payload())

    assert result.reason_code == EXTRA_FIELD_REJECTED


def test_schema_order_uses_first_failing_path_for_nested_errors() -> None:
    metadata = native_metadata()
    metadata["model_profile"]["local_tier"] = "ram"
    metadata["engine_profile"].pop("engine_name")

    result = validate_object(metadata, native_payload())

    assert result.reason_code == MISSING_REQUIRED_FIELD


def test_malformed_hash_prefix_is_checked_before_identity_recomputation() -> None:
    metadata = native_metadata()
    metadata["integrity"]["payload_hash"] = "sha256:" + "1" * 64
    metadata["object_id"] = "not-a-bifrost-object-id"

    result = validate_object(metadata, native_payload())

    assert result.reason_code == SCHEMA_VALIDATION_FAILED


def test_semantic_order_returns_first_native_reason() -> None:
    metadata = native_metadata()
    metadata["native_tensor_profile"]["kv_block_id"] = 1
    metadata["native_tensor_profile"]["tensor_shape"] = [2, 4, 2, 4]
    metadata = finalize_identity(metadata, native_payload())

    result = validate_object(metadata, native_payload())

    assert result.reason_code == "invalid_kv_block_id"

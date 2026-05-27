from __future__ import annotations

from bifrost_kv.errors import (
    BYTE_LENGTH_MISMATCH,
    DESCRIPTOR_HASH_MISMATCH,
    PAYLOAD_HASH_MISMATCH,
    UNKNOWN_SCHEMA_VERSION,
    UNSUPPORTED_COMPRESSION,
    WRONG_TOKENIZER_HASH,
)
from bifrost_kv.validate import validate_object
from test_native_validation import HASHES, native_metadata, native_payload, native_target_profile


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


def test_unsupported_compression_is_checked_before_payload_length() -> None:
    metadata = native_metadata()
    metadata["payload_profile"]["compression"] = "zstd"
    metadata["payload_profile"]["byte_length"] += 1

    result = validate_object(metadata, native_payload())

    assert result.reason_code == UNSUPPORTED_COMPRESSION

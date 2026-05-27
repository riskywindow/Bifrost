from __future__ import annotations

from copy import deepcopy
from typing import Any

from bifrost_kv.errors import (
    ACCEPTED,
    OPAQUE_PAYLOAD_NOT_INTERPRETABLE,
    OPAQUE_WRONG_ENGINE_KEY,
)
from bifrost_kv.validate import validate_object
from test_native_validation import HASHES, finalize_identity, native_metadata

OPAQUE_ENGINE_KEY = "blake3:" + "8" * 64


def opaque_payload() -> bytes:
    return b"opaque bytes"


def opaque_metadata() -> dict[str, Any]:
    native = native_metadata()
    metadata: dict[str, Any] = {
        "schema_version": "bifrost.kv_object.v1alpha1",
        "object_type": "opaque_engine_blob",
        "object_id": "bifrost://object/blake3/" + "2" * 64,
        "created_at_unix_ms": 1779900000000,
        "created_by": "test",
        "model_profile": deepcopy(native["model_profile"]),
        "engine_profile": {
            "engine_name": "engine-a",
            "engine_version": "1.0",
            "integration_name": "integration-a",
            "integration_version": "1.0",
            "attention_impl": "engine-owned",
            "kv_layout": "opaque",
            "block_size_tokens": 4,
            "kv_cache_format": "opaque-v1",
        },
        "prefix_profile": None,
        "payload_profile": {
            "byte_length": len(opaque_payload()),
            "compression": "none",
            "payload_encoding": "raw_bytes",
        },
        "native_tensor_profile": None,
        "opaque_engine_profile": {
            "engine_key_hash": OPAQUE_ENGINE_KEY,
            "engine_payload_type": "opaque-kv",
            "engine_key_repr_version": "v1",
        },
        "integrity": {
            "descriptor_hash": "blake3:" + "2" * 64,
            "payload_hash": "blake3:" + "3" * 64,
            "object_id_algorithm": "bifrost.object_id.v1",
            "chunk_size_bytes": len(opaque_payload()),
            "chunk_hashes": [],
        },
        "provenance": {
            "source": "test",
            "notes": "",
            "producer_commit": "0" * 40,
            "producer_hostname": "localhost",
        },
    }
    return finalize_identity(metadata, opaque_payload())


def opaque_target_profile() -> dict[str, Any]:
    metadata = opaque_metadata()
    return {
        "schema_version": "bifrost.target_profile.v1alpha1",
        "accepts_object_type": "opaque_engine_blob",
        "model_profile": None,
        "engine_profile": deepcopy(metadata["engine_profile"]),
        "prefix_requirements": None,
        "opaque_requirements": deepcopy(metadata["opaque_engine_profile"]),
    }


def test_valid_opaque_accepted() -> None:
    result = validate_object(opaque_metadata(), opaque_payload(), opaque_target_profile())

    assert result.status == "accepted"
    assert result.reason_code == ACCEPTED


def test_opaque_wrong_engine_key_rejected() -> None:
    target = opaque_target_profile()
    target["opaque_requirements"]["engine_key_hash"] = HASHES["wrong"]

    result = validate_object(opaque_metadata(), opaque_payload(), target)

    assert result.reason_code == OPAQUE_WRONG_ENGINE_KEY


def test_opaque_object_with_native_tensor_profile_rejected() -> None:
    metadata = opaque_metadata()
    metadata["native_tensor_profile"] = deepcopy(native_metadata()["native_tensor_profile"])
    metadata = finalize_identity(metadata, opaque_payload())

    result = validate_object(metadata, opaque_payload())

    assert result.reason_code == OPAQUE_PAYLOAD_NOT_INTERPRETABLE

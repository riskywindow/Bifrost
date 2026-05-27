from copy import deepcopy

from bifrost_kv import (
    compute_descriptor_hash,
    compute_object_id,
    compute_object_identity,
    compute_payload_hash,
)


def small_meta() -> dict[str, object]:
    return {
        "schema_version": "bifrost.kv_object.v1alpha1",
        "object_type": "native_kv_page",
        "object_id": "bifrost://object/blake3/" + "1" * 64,
        "payload_profile": {
            "byte_length": 4,
            "compression": "none",
            "payload_encoding": "raw_bytes",
        },
        "model_profile": {
            "model_id": "example/model",
            "model_revision": "rev-a",
            "model_hash": "blake3:" + "a" * 64,
        },
        "integrity": {
            "descriptor_hash": "blake3:" + "2" * 64,
            "payload_hash": "blake3:" + "3" * 64,
            "object_id_algorithm": "bifrost.object_id.v1",
        },
    }


def test_same_object_with_different_key_order_has_same_descriptor_hash() -> None:
    meta = small_meta()
    reordered = {
        "integrity": {
            "object_id_algorithm": "bifrost.object_id.v1",
            "payload_hash": "blake3:" + "3" * 64,
            "descriptor_hash": "blake3:" + "2" * 64,
        },
        "model_profile": {
            "model_hash": "blake3:" + "a" * 64,
            "model_revision": "rev-a",
            "model_id": "example/model",
        },
        "payload_profile": {
            "payload_encoding": "raw_bytes",
            "compression": "none",
            "byte_length": 4,
        },
        "object_id": "bifrost://object/blake3/" + "1" * 64,
        "object_type": "native_kv_page",
        "schema_version": "bifrost.kv_object.v1alpha1",
    }
    payload_hash = compute_payload_hash(b"abcd")

    assert compute_descriptor_hash(meta, payload_hash) == compute_descriptor_hash(
        reordered, payload_hash
    )


def test_changing_payload_changes_payload_hash_and_object_id() -> None:
    meta = small_meta()

    left = compute_object_identity(meta, b"abcd")
    right = compute_object_identity(meta, b"abce")

    assert left.payload_hash != right.payload_hash
    assert left.object_id != right.object_id


def test_changing_immutable_metadata_changes_descriptor_hash_and_object_id() -> None:
    original = small_meta()
    changed = deepcopy(original)
    changed["model_profile"]["model_revision"] = "rev-b"  # type: ignore[index]

    left = compute_object_identity(original, b"abcd")
    right = compute_object_identity(changed, b"abcd")

    assert left.payload_hash == right.payload_hash
    assert left.descriptor_hash != right.descriptor_hash
    assert left.object_id != right.object_id


def test_compute_object_identity_fills_expected_values_for_small_fixture() -> None:
    identity = compute_object_identity(small_meta(), b"abcd")

    assert identity.payload_hash == (
        "blake3:8c9c9881805d1a847102d7a42e58b990d088dd88a84f7314d71c838107571f2b"
    )
    assert identity.descriptor_hash == (
        "blake3:7b19556f2f512abbfb1eae2bced00db5af08c8d706db7eeb620cb9894a922a31"
    )
    assert identity.object_id == (
        "bifrost://object/blake3/d54def21dd241b34c1ae512851a56989bbb976ad385f768"
        "a0ab3396c56e5c031"
    )


def test_compute_object_id_uses_descriptor_and_payload_hashes() -> None:
    descriptor_hash = "blake3:" + "a" * 64
    payload_hash = "blake3:" + "b" * 64

    assert compute_object_id(descriptor_hash, payload_hash) == (
        "bifrost://object/blake3/b2234a9b233637a55cd4670421bce6beee67806252aa"
        "acebc8a93f0b4b7937e7"
    )


def test_object_identity_ignores_mutable_record_fields_outside_descriptor() -> None:
    descriptor = small_meta()
    record_a = {
        "descriptor": descriptor,
        "tier": "ram",
        "pinned": False,
        "local_path": "/tmp/a",
        "last_accessed_unix_ms": 1,
    }
    record_b = {
        "descriptor": descriptor,
        "tier": "disk",
        "pinned": True,
        "local_path": "/tmp/b",
        "last_accessed_unix_ms": 2,
    }

    identity_a = compute_object_identity(record_a["descriptor"], b"abcd")
    identity_b = compute_object_identity(record_b["descriptor"], b"abcd")

    assert identity_a == identity_b

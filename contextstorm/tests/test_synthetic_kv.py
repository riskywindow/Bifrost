from __future__ import annotations

from contextstorm.synthetic_kv import generate_synthetic_object


def test_synthetic_payload_generation_is_deterministic() -> None:
    first = generate_synthetic_object(object_size_bytes=1024)
    second = generate_synthetic_object(object_size_bytes=1024)

    assert first.payload == second.payload
    assert first.metadata == second.metadata
    assert first.object_id == second.object_id
    assert first.metadata["payload_profile"]["byte_length"] == 1024


def test_native_synthetic_shape_matches_payload_size() -> None:
    obj = generate_synthetic_object(
        object_size_bytes=1024,
        object_type="native_kv_page",
    )

    assert obj.metadata["object_type"] == "native_kv_page"
    assert obj.metadata["native_tensor_profile"]["tensor_shape"] == [2, 256, 1, 1]
    assert obj.metadata["payload_profile"]["byte_length"] == len(obj.payload)

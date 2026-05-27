import pytest

from bifrost_kv import canonical_encode


def test_same_object_with_different_key_order_has_same_canonical_bytes() -> None:
    left = {
        "object_type": "native_kv_page",
        "integrity": {"payload_hash": None, "descriptor_hash": None},
        "values": [3, {"z": True, "a": None}],
    }
    right = {
        "values": [3, {"a": None, "z": True}],
        "integrity": {"descriptor_hash": None, "payload_hash": None},
        "object_type": "native_kv_page",
    }

    expected = (
        b'{"integrity":{"descriptor_hash":null,"payload_hash":null},'
        b'"object_type":"native_kv_page","values":[3,{"a":null,"z":true}]}'
    )
    assert canonical_encode(left) == expected
    assert canonical_encode(right) == expected


@pytest.mark.parametrize(
    "obj",
    [
        {"value": 1.25},
        {"nested": [{"value": float("nan")}]},
    ],
)
def test_canonical_encode_rejects_floats(obj: dict[str, object]) -> None:
    with pytest.raises(TypeError, match="floats are not supported"):
        canonical_encode(obj)


def test_canonical_encode_rejects_non_string_dict_keys() -> None:
    with pytest.raises(TypeError, match="object keys must be strings"):
        canonical_encode({"nested": {1: "not allowed"}})

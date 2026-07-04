from __future__ import annotations

import re

import pytest

from vllm_bifrost.errors import KeyHashingError
from vllm_bifrost.fakes import FakeKVCacheConfig
from vllm_bifrost.keying import (
    stable_kv_cache_config_hash,
    stable_layout_fingerprint,
    stable_vllm_blob_key,
    vllm_blob_key_hash,
)

_ADDRESS_RE = re.compile(r"0x[0-9a-fA-F]{6,}")


def _key_kwargs() -> dict[str, object]:
    kv_config_hash = stable_kv_cache_config_hash(FakeKVCacheConfig())
    layout_fingerprint = stable_layout_fingerprint(
        kv_cache_config_hash=kv_config_hash,
        model_fingerprint="model:fake-vllm",
        vllm_version="0.9.0",
        connector_api_version="fake-kvtransfer-v1",
        tensor_dtype="float32",
        tensor_shape=(2, 8, 2, 2, 4),
    )
    return {
        "connector_instance_id": "connector-a",
        "request_id": "request-1",
        "model_fingerprint": "model:fake-vllm",
        "kv_cache_config_hash": kv_config_hash,
        "layer_name": "layer_0",
        "block_ids": (0, 2),
        "role": "kv_both",
        "vllm_version": "0.9.0",
        "layout_fingerprint": layout_fingerprint,
    }


def test_key_hash_is_stable() -> None:
    kwargs = _key_kwargs()

    first = vllm_blob_key_hash(**kwargs)  # type: ignore[arg-type]
    second = vllm_blob_key_hash(**kwargs)  # type: ignore[arg-type]

    assert first == second
    assert first.startswith("blake3:")


def test_key_hash_changes_when_request_id_changes() -> None:
    kwargs = _key_kwargs()
    changed = dict(kwargs, request_id="request-2")

    original_hash = vllm_blob_key_hash(**kwargs)  # type: ignore[arg-type]
    changed_hash = vllm_blob_key_hash(**changed)  # type: ignore[arg-type]

    assert original_hash != changed_hash


def test_key_hash_changes_when_layer_name_changes() -> None:
    kwargs = _key_kwargs()
    changed = dict(kwargs, layer_name="layer_1")

    original_hash = vllm_blob_key_hash(**kwargs)  # type: ignore[arg-type]
    changed_hash = vllm_blob_key_hash(**changed)  # type: ignore[arg-type]

    assert original_hash != changed_hash


def test_key_hash_changes_when_block_ids_change() -> None:
    kwargs = _key_kwargs()
    changed = dict(kwargs, block_ids=(0, 3))

    original_hash = vllm_blob_key_hash(**kwargs)  # type: ignore[arg-type]
    changed_hash = vllm_blob_key_hash(**changed)  # type: ignore[arg-type]

    assert original_hash != changed_hash


def test_stable_key_material_has_no_memory_address_strings() -> None:
    kwargs = _key_kwargs()

    stable_key = stable_vllm_blob_key(**kwargs)  # type: ignore[arg-type]

    assert _ADDRESS_RE.search(stable_key) is None


def test_memory_address_strings_reject() -> None:
    kwargs = dict(_key_kwargs(), connector_instance_id="connector at 0x12345678")

    with pytest.raises(KeyHashingError, match="memory address"):
        stable_vllm_blob_key(**kwargs)  # type: ignore[arg-type]


def test_kv_cache_config_hash_is_stable() -> None:
    config = FakeKVCacheConfig(num_layers=3, num_blocks=5, block_size=7)

    assert stable_kv_cache_config_hash(config) == stable_kv_cache_config_hash(config)


def test_kv_cache_config_hash_rejects_mutable_local_fields() -> None:
    with pytest.raises(KeyHashingError, match="mutable local field"):
        stable_kv_cache_config_hash({"num_layers": 2, "server_port": 7420})


def test_layout_fingerprint_is_stable_and_changes_with_layout_inputs() -> None:
    kv_config_hash = stable_kv_cache_config_hash(FakeKVCacheConfig())
    first = stable_layout_fingerprint(
        kv_cache_config_hash=kv_config_hash,
        model_fingerprint="model:fake-vllm",
        tensor_dtype="float32",
        tensor_shape=(2, 8, 2, 2, 4),
    )
    second = stable_layout_fingerprint(
        kv_cache_config_hash=kv_config_hash,
        model_fingerprint="model:fake-vllm",
        tensor_dtype="float32",
        tensor_shape=(2, 8, 2, 2, 4),
    )
    changed = stable_layout_fingerprint(
        kv_cache_config_hash=kv_config_hash,
        model_fingerprint="model:fake-vllm",
        tensor_dtype="float16",
        tensor_shape=(2, 8, 2, 2, 4),
    )

    assert first == second
    assert first != changed

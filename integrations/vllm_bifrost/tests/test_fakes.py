from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import torch

from vllm_bifrost.fakes import (
    FakeAttentionMetadata,
    FakeConnectorMetadata,
    FakeConnectorRole,
    FakeForwardContext,
    FakeKVCacheConfig,
    FakeKVTransferConfig,
    FakeRequest,
    FakeSchedulerOutput,
    FakeVllmConfig,
    clone_fake_kv_caches,
    compare_fake_kv_caches,
    corrupt_one_block,
    flatten_layer_blocks,
    make_fake_kv_caches,
    write_layer_blocks,
    zero_fake_kv_caches,
)


def test_package_import_does_not_import_optional_runtime_modules() -> None:
    package_root = Path(__file__).resolve().parents[1]
    code = """
import json
import sys
import vllm_bifrost

print(json.dumps({
    "version": vllm_bifrost.__version__,
    "has_vllm": "vllm" in sys.modules,
    "has_lmcache": "lmcache" in sys.modules,
    "has_lmcache_bifrost": "lmcache_bifrost" in sys.modules,
    "has_torch": "torch" in sys.modules,
}))
"""
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=package_root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )

    result = json.loads(completed.stdout)

    assert result == {
        "version": "0.1.0",
        "has_vllm": False,
        "has_lmcache": False,
        "has_lmcache_bifrost": False,
        "has_torch": False,
    }


def test_fake_configs_instantiate() -> None:
    kv_transfer_config = FakeKVTransferConfig(
        kv_role=FakeConnectorRole.kv_producer,
        kv_connector_extra_config={"endpoint": "http://127.0.0.1:8765"},
    )
    kv_cache_config = FakeKVCacheConfig(
        num_layers=3,
        num_blocks=5,
        block_size=7,
        num_heads=2,
        head_dim=4,
    )
    vllm_config = FakeVllmConfig(
        kv_transfer_config=kv_transfer_config,
        kv_cache_config=kv_cache_config,
    )
    request = FakeRequest(
        request_id="req-1",
        prompt_token_ids=(10, 11, 12),
        prefix_hash="prefix:abc",
    )
    attention_metadata = FakeAttentionMetadata(
        request_id="req-1",
        layer_names=("layer_0", "layer_2"),
        block_ids=(0, 4),
    )
    forward_context = FakeForwardContext(
        request=request,
        attention_metadata=attention_metadata,
        operation="save",
    )
    scheduler_output = FakeSchedulerOutput(
        requests=(request,),
        attention_metadata=attention_metadata,
    )

    assert kv_transfer_config.to_dict()["kv_role"] == "kv_producer"
    assert vllm_config.kv_cache_config.layer_names == (
        "layer_0",
        "layer_1",
        "layer_2",
    )
    assert vllm_config.kv_cache_config.layer_shape == (5, 7, 2, 2, 4)
    assert forward_context.to_connector_metadata(
        connector_instance_id="connector-a"
    ).request_id == "req-1"
    assert scheduler_output.build_connector_metadata(
        operation="load",
        connector_instance_id="connector-a",
    )[0].operation == "load"


def test_fake_kv_caches_are_deterministic_by_seed() -> None:
    a = make_fake_kv_caches(
        num_layers=2,
        blocks=3,
        block_size=4,
        heads=2,
        head_dim=5,
        seed=123,
    )
    b = make_fake_kv_caches(
        num_layers=2,
        blocks=3,
        block_size=4,
        heads=2,
        head_dim=5,
        seed=123,
    )
    c = make_fake_kv_caches(
        num_layers=2,
        blocks=3,
        block_size=4,
        heads=2,
        head_dim=5,
        seed=124,
    )

    assert compare_fake_kv_caches(a, b) is True
    assert compare_fake_kv_caches(a, c) is False
    assert a["layer_0"].shape == (3, 4, 2, 2, 5)
    assert all(kv_layer.device.type == "cpu" for kv_layer in a.values())


def test_clone_fake_kv_caches_does_not_alias_original() -> None:
    original = make_fake_kv_caches(2, 3, 4, 2, 5, seed=10)
    cloned = clone_fake_kv_caches(original)

    assert compare_fake_kv_caches(original, cloned) is True
    for layer_name, original_layer in original.items():
        assert original_layer.data_ptr() != cloned[layer_name].data_ptr()

    corrupt_one_block(cloned, "layer_0", 1)

    assert compare_fake_kv_caches(original, cloned) is False
    assert compare_fake_kv_caches(
        original,
        make_fake_kv_caches(2, 3, 4, 2, 5, seed=10),
    )


def test_zero_then_write_restores_selected_blocks() -> None:
    source = make_fake_kv_caches(2, 4, 3, 2, 5, seed=99)
    target = clone_fake_kv_caches(source)
    block_ids = (0, 3)
    payload = flatten_layer_blocks(source["layer_1"], block_ids)

    zero_fake_kv_caches(target)
    assert compare_fake_kv_caches(source, target) is False

    write_layer_blocks(target["layer_1"], block_ids, payload)

    assert torch.equal(flatten_layer_blocks(target["layer_1"], block_ids), payload)
    assert torch.equal(
        flatten_layer_blocks(target["layer_1"], (1,)),
        torch.zeros_like(flatten_layer_blocks(source["layer_1"], (1,))),
    )


def test_compare_detects_equality() -> None:
    a = make_fake_kv_caches(1, 2, 3, 1, 4, seed=7)
    b = clone_fake_kv_caches(a)

    assert compare_fake_kv_caches(a, b) is True


def test_compare_detects_corruption() -> None:
    a = make_fake_kv_caches(1, 2, 3, 1, 4, seed=7)
    b = clone_fake_kv_caches(a)

    corrupt_one_block(b, "layer_0", 0)

    assert compare_fake_kv_caches(a, b) is False


def test_fake_metadata_serializes_to_dict_and_back() -> None:
    metadata = FakeConnectorMetadata(
        request_id="req-2",
        layer_names=("layer_0", "layer_1"),
        block_ids=(2, 3),
        prompt_token_ids=(101, 102),
        prefix_hash="prefix:xyz",
        operation="load",
        connector_instance_id="connector-b",
    )

    data = metadata.to_dict()

    assert data == {
        "request_id": "req-2",
        "layer_names": ["layer_0", "layer_1"],
        "block_ids": [2, 3],
        "prompt_token_ids": [101, 102],
        "prefix_hash": "prefix:xyz",
        "operation": "load",
        "connector_instance_id": "connector-b",
    }
    assert FakeConnectorMetadata.from_dict(data) == metadata

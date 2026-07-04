from __future__ import annotations

import json
from copy import deepcopy

import pytest
import torch

from bifrost_kv.errors import OPAQUE_WRONG_ENGINE_KEY, PAYLOAD_HASH_MISMATCH
from bifrost_kv.target_profile import validate_target_profile_schema
from bifrost_kv.validate import validate_object
from vllm_bifrost.blob_codec import (
    build_vllm_opaque_metadata,
    build_vllm_opaque_target_profile,
    payload_to_tensor,
    tensor_to_payload,
)
from vllm_bifrost.config import ENGINE_NAME, INTEGRATION_NAME, KV_CACHE_FORMAT
from vllm_bifrost.errors import CPUStagingSerializationError
from vllm_bifrost.fakes import FakeKVCacheConfig
from vllm_bifrost.keying import (
    KEY_REPR_VERSION,
    stable_kv_cache_config_hash,
    stable_layout_fingerprint,
)


def _tensor() -> torch.Tensor:
    return torch.arange(0, 2 * 3 * 2 * 2, dtype=torch.float32).reshape(2, 3, 2, 2)


def _blob_kwargs(tensor: torch.Tensor) -> dict[str, object]:
    kv_config_hash = stable_kv_cache_config_hash(FakeKVCacheConfig())
    layout_fingerprint = stable_layout_fingerprint(
        kv_cache_config_hash=kv_config_hash,
        model_fingerprint="model:fake-vllm",
        vllm_version="0.9.0",
        connector_api_version="fake-kvtransfer-v1",
        tensor_dtype=tensor.dtype,
        tensor_shape=tuple(tensor.shape),
    )
    return {
        "connector_instance_id": "connector-a",
        "request_id": "request-1",
        "model_fingerprint": "model:fake-vllm",
        "kv_cache_config_hash": kv_config_hash,
        "layer_name": "layer_0",
        "block_ids": (0, 1),
        "role": "kv_both",
        "vllm_version": "0.9.0",
        "connector_api_version": "fake-kvtransfer-v1",
        "layout_fingerprint": layout_fingerprint,
        "tensor_shape": tuple(tensor.shape),
        "tensor_dtype": tensor.dtype,
        "device_origin": tensor.device.type,
    }


def _metadata_and_payload() -> tuple[dict[str, object], bytes, dict[str, object]]:
    tensor = _tensor()
    payload = tensor_to_payload(tensor)
    kwargs = _blob_kwargs(tensor)
    metadata = build_vllm_opaque_metadata(payload=payload, **kwargs)
    target_kwargs = {
        key: kwargs[key]
        for key in (
            "connector_instance_id",
            "request_id",
            "model_fingerprint",
            "kv_cache_config_hash",
            "layer_name",
            "block_ids",
            "role",
            "vllm_version",
            "layout_fingerprint",
        )
    }
    target = build_vllm_opaque_target_profile(**target_kwargs)
    return metadata, payload, target


def test_cpu_tensor_payload_roundtrips_exactly() -> None:
    tensor = _tensor()

    payload = tensor_to_payload(tensor)
    restored = payload_to_tensor(payload, tensor.dtype, tensor.shape)

    assert restored.device.type == "cpu"
    assert restored.dtype == tensor.dtype
    assert tuple(restored.shape) == tuple(tensor.shape)
    assert torch.equal(restored, tensor)


def test_dtype_and_shape_are_preserved_in_metadata() -> None:
    tensor = _tensor()
    payload = tensor_to_payload(tensor)
    metadata = build_vllm_opaque_metadata(payload=payload, **_blob_kwargs(tensor))
    notes = json.loads(metadata["provenance"]["notes"])["vllm_blob_provenance"]

    assert notes["tensor_dtype"] == "float32"
    assert notes["tensor_shape"] == list(tensor.shape)
    assert notes["device_origin"] == "cpu"
    assert notes["layout_fingerprint"].startswith("blake3:")


def test_generated_metadata_validates() -> None:
    metadata, payload, target = _metadata_and_payload()
    result = validate_object(metadata, payload, target)

    assert result.status == "accepted"
    assert metadata["object_type"] == "opaque_engine_blob"
    assert metadata["engine_profile"]["engine_name"] == ENGINE_NAME
    assert metadata["engine_profile"]["integration_name"] == INTEGRATION_NAME
    assert metadata["engine_profile"]["kv_layout"] == "opaque"
    assert metadata["engine_profile"]["kv_cache_format"] == KV_CACHE_FORMAT
    assert metadata["opaque_engine_profile"]["engine_key_repr_version"] == (
        KEY_REPR_VERSION
    )
    assert metadata["payload_profile"]["byte_length"] == len(payload)
    assert metadata["native_tensor_profile"] is None
    assert metadata["prefix_profile"] is None


def test_target_profile_validates() -> None:
    _, _, target = _metadata_and_payload()

    assert validate_target_profile_schema(target) is None


def test_wrong_target_rejects() -> None:
    metadata, payload, target = _metadata_and_payload()
    wrong_target = deepcopy(target)
    tensor = _tensor()
    kwargs = _blob_kwargs(tensor)
    target_kwargs = {
        key: kwargs[key]
        for key in (
            "connector_instance_id",
            "request_id",
            "model_fingerprint",
            "kv_cache_config_hash",
            "layer_name",
            "block_ids",
            "role",
            "vllm_version",
            "layout_fingerprint",
        )
    }
    target_kwargs["layer_name"] = "layer_1"
    wrong_target["opaque_requirements"] = build_vllm_opaque_target_profile(
        **target_kwargs
    )["opaque_requirements"]

    result = validate_object(metadata, payload, wrong_target)

    assert result.reason_code == OPAQUE_WRONG_ENGINE_KEY


def test_corrupted_payload_rejects() -> None:
    metadata, payload, target = _metadata_and_payload()
    corrupted = b"x" + payload[1:]

    result = validate_object(metadata, corrupted, target)

    assert result.reason_code == PAYLOAD_HASH_MISMATCH


def test_object_id_stable_for_same_key_and_payload() -> None:
    tensor = _tensor()
    payload = tensor_to_payload(tensor)
    kwargs = _blob_kwargs(tensor)

    first = build_vllm_opaque_metadata(payload=payload, **kwargs)
    second = build_vllm_opaque_metadata(payload=payload, **kwargs)

    assert first["object_id"] == second["object_id"]


def test_payload_to_tensor_rejects_wrong_byte_length() -> None:
    tensor = _tensor()
    payload = tensor_to_payload(tensor)

    with pytest.raises(CPUStagingSerializationError, match="byte length"):
        payload_to_tensor(payload[:-1], tensor.dtype, tensor.shape)


@pytest.mark.skipif(not torch.cuda.is_available(), reason="CUDA is not available")
def test_cuda_tensor_payload_copies_to_cpu_when_allowed() -> None:
    tensor = _tensor().to("cuda")

    payload = tensor_to_payload(tensor, allow_cpu_staging=True)
    restored = payload_to_tensor(payload, tensor.dtype, tensor.shape)

    assert restored.device.type == "cpu"
    assert torch.equal(restored, tensor.cpu())


@pytest.mark.skipif(not torch.cuda.is_available(), reason="CUDA is not available")
def test_cuda_tensor_payload_rejects_when_cpu_staging_disabled() -> None:
    tensor = _tensor().to("cuda")

    with pytest.raises(CPUStagingSerializationError, match="allow_cpu_staging"):
        tensor_to_payload(tensor, allow_cpu_staging=False)

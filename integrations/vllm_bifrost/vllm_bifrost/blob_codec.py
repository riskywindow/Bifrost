"""vLLM opaque tensor payload and Phase 1 metadata codec."""

from __future__ import annotations

from collections.abc import Iterable
import math
import re
from typing import Any

import torch

from bifrost_kv.canonical import canonical_encode
from bifrost_kv.hashing import (
    blake3_hex,
    compute_descriptor_hash,
    compute_object_id,
    compute_payload_hash,
)
from bifrost_kv.validate import validate_object

from .config import BifrostVLLMConnectorConfig
from .errors import CPUStagingSerializationError, OpaqueBlobValidationError
from .keying import (
    CPU_STAGING_FORMAT_VERSION,
    KEY_REPR_VERSION,
    vllm_blob_key_hash,
)

SCHEMA_VERSION = "bifrost.kv_object.v1alpha1"
TARGET_SCHEMA_VERSION = "bifrost.target_profile.v1alpha1"
INTEGRATION_VERSION = "0.1.0"
ENGINE_PAYLOAD_TYPE = "opaque_vllm_kv_blob"
UNKNOWN_HASH = blake3_hex(b"bifrost.vllm.opaque.unknown.v1")

_ADDRESS_RE = re.compile(r"0x[0-9a-fA-F]{6,}")
_HASH_RE = re.compile(r"^blake3:[0-9a-f]{64}$")


def tensor_to_payload(tensor: object, allow_cpu_staging: bool = True) -> bytes:
    """Serialize a dense vLLM-owned tensor as raw CPU-staged bytes."""

    if not torch.is_tensor(tensor):
        raise CPUStagingSerializationError("tensor_to_payload requires a torch.Tensor")
    if tensor.device.type == "cpu":
        cpu_tensor = tensor.detach().contiguous()
    elif tensor.device.type == "cuda":
        if not allow_cpu_staging:
            raise CPUStagingSerializationError(
                "CUDA tensor serialization requires allow_cpu_staging=true"
            )
        cpu_tensor = tensor.detach().to(device="cpu", copy=True).contiguous()
    else:
        raise CPUStagingSerializationError(
            f"unsupported tensor device for CPU staging: {tensor.device.type}"
        )

    if cpu_tensor.numel() == 0:
        return b""
    try:
        return memoryview(cpu_tensor.view(torch.uint8).numpy()).tobytes()
    except Exception as exc:
        raise CPUStagingSerializationError(
            f"failed to serialize tensor through CPU staging: {exc}"
        ) from exc


def payload_to_tensor(
    payload: bytes | bytearray | memoryview,
    dtype: object,
    shape: Iterable[int],
    device: str | torch.device = "cpu",
) -> torch.Tensor:
    """Rebuild a tensor from raw payload bytes, dtype, and shape metadata."""

    payload_bytes = bytes(payload)
    dtype_obj = _coerce_torch_dtype(dtype)
    shape_tuple = _shape_tuple(shape)
    expected_bytes = (
        math.prod(shape_tuple) * torch.empty((), dtype=dtype_obj).element_size()
    )
    if len(payload_bytes) != expected_bytes:
        raise CPUStagingSerializationError(
            f"payload byte length {len(payload_bytes)} does not match dtype "
            f"{_dtype_name(dtype_obj)} and shape {list(shape_tuple)} "
            f"({expected_bytes} bytes)"
        )
    try:
        if expected_bytes == 0:
            tensor = torch.empty(shape_tuple, dtype=dtype_obj, device="cpu")
        else:
            tensor = (
                torch.frombuffer(bytearray(payload_bytes), dtype=dtype_obj)
                .clone()
                .reshape(shape_tuple)
            )
        target_device = torch.device(device)
        if target_device.type != "cpu":
            tensor = tensor.to(target_device)
        return tensor
    except Exception as exc:
        raise CPUStagingSerializationError(
            f"failed to deserialize tensor payload: {exc}"
        ) from exc


def build_vllm_opaque_metadata(
    *,
    payload: bytes | bytearray | memoryview,
    connector_instance_id: str,
    request_id: str,
    model_fingerprint: str,
    kv_cache_config_hash: str,
    layer_name: str,
    block_ids: Iterable[int],
    role: str,
    layout_fingerprint: str,
    tensor_shape: Iterable[int],
    tensor_dtype: object,
    device_origin: str,
    vllm_version: str | None = None,
    connector_api_version: str | None = None,
    config: BifrostVLLMConnectorConfig | None = None,
) -> dict[str, Any]:
    """Build and optionally validate Phase 1 vLLM opaque blob metadata."""

    config = config or BifrostVLLMConnectorConfig()
    payload_bytes = bytes(payload)
    tensor_shape_list = list(_shape_tuple(tensor_shape))
    tensor_dtype_name = _dtype_name(tensor_dtype)
    device_origin_text = _required_str("device_origin", device_origin)
    block_id_list = _block_id_list(block_ids)
    key_hash = vllm_blob_key_hash(
        connector_instance_id=connector_instance_id,
        request_id=request_id,
        model_fingerprint=model_fingerprint,
        kv_cache_config_hash=kv_cache_config_hash,
        layer_name=layer_name,
        block_ids=block_id_list,
        role=role,
        vllm_version=vllm_version,
        layout_fingerprint=layout_fingerprint,
    )
    payload_hash = compute_payload_hash(payload_bytes)
    metadata: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "object_type": "opaque_engine_blob",
        "object_id": "bifrost://object/blake3/" + "0" * 64,
        "created_at_unix_ms": 0,
        "created_by": config.integration_name,
        "model_profile": _opaque_model_profile(
            model_fingerprint=model_fingerprint,
            kv_cache_config_hash=kv_cache_config_hash,
            tensor_dtype=tensor_dtype_name,
        ),
        "engine_profile": _engine_profile(config, vllm_version=vllm_version),
        "prefix_profile": None,
        "payload_profile": {
            "byte_length": len(payload_bytes),
            "compression": "none",
            "payload_encoding": "raw_bytes",
        },
        "native_tensor_profile": None,
        "opaque_engine_profile": {
            "engine_key_hash": key_hash,
            "engine_payload_type": ENGINE_PAYLOAD_TYPE,
            "engine_key_repr_version": KEY_REPR_VERSION,
        },
        "integrity": {
            "descriptor_hash": "blake3:" + "0" * 64,
            "payload_hash": payload_hash,
            "object_id_algorithm": "bifrost.object_id.v1",
            "chunk_size_bytes": config.chunk_size,
            "chunk_hashes": _chunk_hashes(payload_bytes, config.chunk_size),
        },
        "provenance": {
            "source": config.integration_name,
            "notes": _provenance_notes(
                vllm_version=vllm_version,
                connector_api_version=connector_api_version,
                layer_name=layer_name,
                block_ids=block_id_list,
                tensor_shape=tensor_shape_list,
                tensor_dtype=tensor_dtype_name,
                device_origin=device_origin_text,
                layout_fingerprint=layout_fingerprint,
            ),
            "producer_commit": "unknown",
            "producer_hostname": "localhost",
        },
    }
    descriptor_hash = compute_descriptor_hash(metadata, payload_hash)
    metadata["integrity"]["descriptor_hash"] = descriptor_hash
    metadata["object_id"] = compute_object_id(descriptor_hash, payload_hash)

    if config.strict_validation:
        target = build_vllm_opaque_target_profile(
            connector_instance_id=connector_instance_id,
            request_id=request_id,
            model_fingerprint=model_fingerprint,
            kv_cache_config_hash=kv_cache_config_hash,
            layer_name=layer_name,
            block_ids=block_id_list,
            role=role,
            vllm_version=vllm_version,
            layout_fingerprint=layout_fingerprint,
            config=config,
        )
        result = validate_object(metadata, payload_bytes, target)
        if result.status != "accepted":
            raise OpaqueBlobValidationError(
                f"generated vLLM opaque blob failed validation: {result.reason_code}"
            )
    return metadata


def build_vllm_opaque_target_profile(
    *,
    connector_instance_id: str,
    request_id: str,
    model_fingerprint: str,
    kv_cache_config_hash: str,
    layer_name: str,
    block_ids: Iterable[int],
    role: str,
    layout_fingerprint: str,
    vllm_version: str | None = None,
    config: BifrostVLLMConnectorConfig | None = None,
) -> dict[str, Any]:
    """Build a Phase 1 target profile for a single vLLM opaque blob key."""

    config = config or BifrostVLLMConnectorConfig()
    block_id_list = _block_id_list(block_ids)
    return {
        "schema_version": TARGET_SCHEMA_VERSION,
        "accepts_object_type": "opaque_engine_blob",
        "model_profile": None,
        "engine_profile": _engine_profile(config, vllm_version=vllm_version),
        "prefix_requirements": None,
        "opaque_requirements": {
            "engine_key_hash": vllm_blob_key_hash(
                connector_instance_id=connector_instance_id,
                request_id=request_id,
                model_fingerprint=model_fingerprint,
                kv_cache_config_hash=kv_cache_config_hash,
                layer_name=layer_name,
                block_ids=block_id_list,
                role=role,
                vllm_version=vllm_version,
                layout_fingerprint=layout_fingerprint,
            ),
            "engine_payload_type": ENGINE_PAYLOAD_TYPE,
            "engine_key_repr_version": KEY_REPR_VERSION,
        },
    }


def _engine_profile(
    config: BifrostVLLMConnectorConfig,
    *,
    vllm_version: str | None,
) -> dict[str, Any]:
    return {
        "engine_name": config.engine_name,
        "engine_version": (
            _optional_non_empty("vllm_version", vllm_version) or "unknown"
        ),
        "integration_name": config.integration_name,
        "integration_version": INTEGRATION_VERSION,
        "attention_impl": "engine_owned",
        "kv_layout": "opaque",
        "block_size_tokens": 1,
        "kv_cache_format": config.kv_cache_format,
    }


def _opaque_model_profile(
    *,
    model_fingerprint: str,
    kv_cache_config_hash: str,
    tensor_dtype: str,
) -> dict[str, Any]:
    model_hash = _commitment_hash("model_fingerprint", model_fingerprint)
    return {
        "model_id": f"vllm-opaque-{model_hash[-12:]}",
        "model_revision": "unknown",
        "model_hash": model_hash,
        "tokenizer_hash": UNKNOWN_HASH,
        "config_hash": _commitment_hash("kv_cache_config_hash", kv_cache_config_hash),
        "rope_config_hash": UNKNOWN_HASH,
        "quantization": "opaque",
        "dtype": tensor_dtype,
        "num_layers": 1,
        "num_attention_heads": 1,
        "num_kv_heads": 1,
        "head_dim": 1,
        "max_position_embeddings": 1,
    }


def _provenance_notes(
    *,
    vllm_version: str | None,
    connector_api_version: str | None,
    layer_name: str,
    block_ids: list[int],
    tensor_shape: list[int],
    tensor_dtype: str,
    device_origin: str,
    layout_fingerprint: str,
) -> str:
    material = {
        "vllm_version": _optional_non_empty("vllm_version", vllm_version) or "unknown",
        "connector_api_version": (
            _optional_non_empty("connector_api_version", connector_api_version)
            or "unknown"
        ),
        "layer_name": _required_str("layer_name", layer_name),
        "block_ids": block_ids,
        "tensor_shape": tensor_shape,
        "tensor_dtype": _required_str("tensor_dtype", tensor_dtype),
        "device_origin": _required_str("device_origin", device_origin),
        "layout_fingerprint": _required_str(
            "layout_fingerprint", layout_fingerprint
        ),
        "cpu_staging_format_version": CPU_STAGING_FORMAT_VERSION,
    }
    return canonical_encode({"vllm_blob_provenance": material}).decode("utf-8")


def _chunk_hashes(payload: bytes, chunk_size: int) -> list[str]:
    if not payload:
        return []
    return [
        compute_payload_hash(payload[offset : offset + chunk_size])
        for offset in range(0, len(payload), chunk_size)
    ]


def _commitment_hash(name: str, value: str) -> str:
    text = _required_str(name, value)
    if _HASH_RE.fullmatch(text):
        return text
    return blake3_hex(f"bifrost.vllm.{name}.v1\x00{text}".encode("utf-8"))


def _coerce_torch_dtype(dtype: object) -> torch.dtype:
    if isinstance(dtype, torch.dtype):
        return dtype
    if isinstance(dtype, str):
        name = dtype.removeprefix("torch.")
        candidate = getattr(torch, name, None)
        if isinstance(candidate, torch.dtype):
            return candidate
    raise CPUStagingSerializationError(f"unsupported tensor dtype: {dtype!r}")


def _dtype_name(dtype: object) -> str:
    return str(_coerce_torch_dtype(dtype)).removeprefix("torch.")


def _shape_tuple(shape: Iterable[int]) -> tuple[int, ...]:
    try:
        result = tuple(_non_negative_int("tensor_shape", dim) for dim in shape)
    except TypeError as exc:
        raise CPUStagingSerializationError(
            "tensor shape must be an iterable of integers"
        ) from exc
    return result


def _block_id_list(block_ids: Iterable[int]) -> list[int]:
    try:
        result = [_non_negative_int("block_ids", block_id) for block_id in block_ids]
    except TypeError as exc:
        raise OpaqueBlobValidationError(
            "block_ids must be an iterable of integers"
        ) from exc
    if not result:
        raise OpaqueBlobValidationError("block_ids must be non-empty")
    return result


def _non_negative_int(name: str, value: int) -> int:
    if isinstance(value, bool):
        raise CPUStagingSerializationError(f"{name} entries must be integers")
    coerced = int(value)
    if coerced < 0:
        raise CPUStagingSerializationError(f"{name} entries must be non-negative")
    return coerced


def _required_str(name: str, value: str) -> str:
    if not isinstance(value, str):
        raise OpaqueBlobValidationError(f"{name} must be a string")
    normalized = value.strip()
    if not normalized:
        raise OpaqueBlobValidationError(f"{name} must be non-empty")
    if _ADDRESS_RE.search(normalized):
        raise OpaqueBlobValidationError(f"{name} contains a memory address")
    return normalized


def _optional_non_empty(name: str, value: str | None) -> str | None:
    if value is None:
        return None
    return _required_str(name, str(value))


__all__ = [
    "ENGINE_PAYLOAD_TYPE",
    "INTEGRATION_VERSION",
    "SCHEMA_VERSION",
    "TARGET_SCHEMA_VERSION",
    "build_vllm_opaque_metadata",
    "build_vllm_opaque_target_profile",
    "payload_to_tensor",
    "tensor_to_payload",
]

"""Deterministic keying for BIFROST vLLM opaque KV blobs."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import fields, is_dataclass
from enum import Enum
import inspect
import re
from typing import Any

from bifrost_kv.canonical import canonical_encode
from bifrost_kv.hashing import blake3_hex

from .config import ENGINE_NAME, INTEGRATION_NAME, KV_CACHE_FORMAT
from .errors import KeyHashingError

KEY_HASH_DOMAIN = b"bifrost.vllm.kv_blob.v1\x00"
KV_CACHE_CONFIG_HASH_DOMAIN = b"bifrost.vllm.kv_cache_config.v1\x00"
LAYOUT_FINGERPRINT_DOMAIN = b"bifrost.vllm.layout_fingerprint.v1\x00"
KEY_REPR_VERSION = "vllm_kv_blob_key.v1"
CONNECTOR_BLOB_FORMAT_VERSION = "bifrost.vllm.opaque_blob.v1"
CPU_STAGING_FORMAT_VERSION = "bifrost.vllm.tensor_payload.raw_bytes.v1"

_ADDRESS_RE = re.compile(r"0x[0-9a-fA-F]{6,}")
_FORBIDDEN_FIELD_NAMES = frozenset(
    {
        "benchmark_run_id",
        "cache_location",
        "committed_path",
        "eviction_score",
        "last_access_time",
        "last_accessed_unix_ms",
        "local_store_path",
        "endpoint",
        "kv_ip",
        "kv_port",
        "peer_address",
        "pid",
        "port",
        "process_id",
        "retry_count",
        "run_id",
        "server_port",
        "staging_path",
        "store_path",
        "transfer_state",
        "write_state",
    }
)


def stable_vllm_blob_key(
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
) -> str:
    """Return canonical JSON key material for one opaque vLLM KV blob."""

    material = {
        "key_repr_version": KEY_REPR_VERSION,
        "engine_name": ENGINE_NAME,
        "integration_name": INTEGRATION_NAME,
        "kv_cache_format": KV_CACHE_FORMAT,
        "connector_instance_id": _required_str(
            "connector_instance_id", connector_instance_id
        ),
        "request_id": _required_str("request_id", request_id),
        "model_fingerprint": _required_str("model_fingerprint", model_fingerprint),
        "kv_cache_config_hash": _required_str(
            "kv_cache_config_hash", kv_cache_config_hash
        ),
        "layer_name": _required_str("layer_name", layer_name),
        "block_ids": _block_id_list(block_ids),
        "role": _required_str("role", role),
        "vllm_version": _optional_str("vllm_version", vllm_version),
        "layout_fingerprint": _required_str("layout_fingerprint", layout_fingerprint),
    }
    try:
        return canonical_encode({"vllm_kv_blob_key": material}).decode("utf-8")
    except Exception as exc:  # pragma: no cover - defensive fail-closed wrapper.
        raise KeyHashingError(f"failed to canonicalize vLLM blob key: {exc}") from exc


def vllm_blob_key_hash(
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
) -> str:
    """Return the Phase 1 opaque engine key hash for a vLLM KV blob."""

    stable_key = stable_vllm_blob_key(
        connector_instance_id=connector_instance_id,
        request_id=request_id,
        model_fingerprint=model_fingerprint,
        kv_cache_config_hash=kv_cache_config_hash,
        layer_name=layer_name,
        block_ids=block_ids,
        role=role,
        vllm_version=vllm_version,
        layout_fingerprint=layout_fingerprint,
    )
    return blake3_hex(KEY_HASH_DOMAIN + stable_key.encode("utf-8"))


def stable_kv_cache_config_hash(kv_cache_config: object) -> str:
    """Return a stable hash for vLLM KV-cache configuration material."""

    try:
        material = _stable_value(kv_cache_config, path="$")
        encoded = canonical_encode({"vllm_kv_cache_config": material})
        return blake3_hex(KV_CACHE_CONFIG_HASH_DOMAIN + encoded)
    except KeyHashingError:
        raise
    except Exception as exc:  # pragma: no cover - defensive fail-closed wrapper.
        raise KeyHashingError(
            f"failed to hash vLLM KV-cache config: {exc}"
        ) from exc


def stable_layout_fingerprint(
    *,
    kv_cache_config_hash: str | None = None,
    kv_cache_config: object | None = None,
    model_fingerprint: str | None = None,
    vllm_version: str | None = None,
    connector_api_version: str | None = None,
    tensor_dtype: object | None = None,
    tensor_shape: Iterable[int] | None = None,
    attention_impl: str | None = None,
    quantization: str | None = None,
    block_size_tokens: int | None = None,
    num_attention_heads: int | None = None,
    num_kv_heads: int | None = None,
    head_dim: int | None = None,
    num_layers: int | None = None,
    extra: Mapping[str, Any] | None = None,
) -> str:
    """Return a deterministic compatibility fingerprint for opaque vLLM layout."""

    if kv_cache_config_hash is None and kv_cache_config is not None:
        kv_cache_config_hash = stable_kv_cache_config_hash(kv_cache_config)
    material = {
        "connector_blob_format_version": CONNECTOR_BLOB_FORMAT_VERSION,
        "cpu_staging_format_version": CPU_STAGING_FORMAT_VERSION,
        "kv_cache_config_hash": _optional_str(
            "kv_cache_config_hash", kv_cache_config_hash
        ),
        "model_fingerprint": _optional_str("model_fingerprint", model_fingerprint),
        "vllm_version": _optional_str("vllm_version", vllm_version),
        "connector_api_version": _optional_str(
            "connector_api_version", connector_api_version
        ),
        "tensor_dtype": (
            None
            if tensor_dtype is None
            else _stable_value(tensor_dtype, path="$.tensor_dtype")
        ),
        "tensor_shape": None if tensor_shape is None else _shape_list(tensor_shape),
        "attention_impl": _optional_str("attention_impl", attention_impl),
        "quantization": _optional_str("quantization", quantization),
        "block_size_tokens": _optional_positive_int(
            "block_size_tokens", block_size_tokens
        ),
        "num_attention_heads": _optional_positive_int(
            "num_attention_heads", num_attention_heads
        ),
        "num_kv_heads": _optional_positive_int("num_kv_heads", num_kv_heads),
        "head_dim": _optional_positive_int("head_dim", head_dim),
        "num_layers": _optional_positive_int("num_layers", num_layers),
        "extra": None if extra is None else _stable_value(extra, path="$.extra"),
    }
    try:
        encoded = canonical_encode({"vllm_layout_fingerprint": material})
        return blake3_hex(LAYOUT_FINGERPRINT_DOMAIN + encoded)
    except KeyHashingError:
        raise
    except Exception as exc:  # pragma: no cover - defensive fail-closed wrapper.
        raise KeyHashingError(
            f"failed to hash vLLM layout fingerprint: {exc}"
        ) from exc


def _stable_value(value: Any, *, path: str) -> Any:
    if value is None or isinstance(value, bool) or isinstance(value, int):
        return value
    if isinstance(value, str):
        _reject_memory_address(path, value)
        return value
    if isinstance(value, float):
        raise KeyHashingError(f"{path}: float values are not supported")
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"__bytes_hex__": bytes(value).hex()}
    if isinstance(value, Enum):
        return _stable_value(value.value, path=f"{path}.value")
    if _looks_like_torch_dtype(value):
        return str(value).removeprefix("torch.")
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in sorted(value.items(), key=lambda pair: str(pair[0])):
            key_text = _field_name(path, key)
            result[key_text] = _stable_value(item, path=f"{path}.{key_text}")
        return result
    if isinstance(value, tuple) and hasattr(value, "_fields"):
        return {
            "__module__": value.__class__.__module__,
            "__type__": value.__class__.__qualname__,
            "fields": {
                name: _stable_value(getattr(value, name), path=f"{path}.{name}")
                for name in sorted(value._fields)
                if _field_name_allowed(path, name)
            },
        }
    if is_dataclass(value) and not isinstance(value, type):
        return {
            "__module__": value.__class__.__module__,
            "__type__": value.__class__.__qualname__,
            "fields": {
                field.name: _stable_value(
                    getattr(value, field.name), path=f"{path}.{field.name}"
                )
                for field in sorted(fields(value), key=lambda item: item.name)
                if not field.name.startswith("_")
                and _field_name_allowed(path, field.name)
            },
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [
            _stable_value(item, path=f"{path}[{index}]")
            for index, item in enumerate(value)
        ]

    public_fields = _public_field_values(value, path=path)
    if public_fields:
        return {
            "__module__": value.__class__.__module__,
            "__type__": value.__class__.__qualname__,
            "fields": public_fields,
        }

    raise KeyHashingError(
        f"{value.__class__.__module__}.{value.__class__.__qualname__} does not "
        "expose stable public fields for vLLM keying"
    )


def _public_field_values(value: Any, *, path: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    annotations = getattr(value.__class__, "__annotations__", {})
    for name in sorted(annotations):
        if (
            not name.startswith("_")
            and hasattr(value, name)
            and _field_name_allowed(path, name)
        ):
            result[name] = _stable_value(getattr(value, name), path=f"{path}.{name}")

    if hasattr(value, "__dict__"):
        for name, item in sorted(vars(value).items()):
            if (
                not name.startswith("_")
                and not inspect.ismethod(item)
                and _field_name_allowed(path, name)
            ):
                result.setdefault(name, _stable_value(item, path=f"{path}.{name}"))
    return result


def _required_str(name: str, value: str) -> str:
    if not isinstance(value, str):
        raise KeyHashingError(f"{name} must be a string")
    normalized = value.strip()
    if not normalized:
        raise KeyHashingError(f"{name} must be non-empty")
    _reject_memory_address(name, normalized)
    return normalized


def _optional_str(name: str, value: str | None) -> str | None:
    if value is None:
        return None
    return _required_str(name, str(value))


def _block_id_list(block_ids: Iterable[int]) -> list[int]:
    try:
        result = [_non_negative_int("block_ids", block_id) for block_id in block_ids]
    except TypeError as exc:
        raise KeyHashingError("block_ids must be an iterable of integers") from exc
    if not result:
        raise KeyHashingError("block_ids must be non-empty")
    return result


def _shape_list(shape: Iterable[int]) -> list[int]:
    try:
        return [_non_negative_int("tensor_shape", dim) for dim in shape]
    except TypeError as exc:
        raise KeyHashingError("tensor_shape must be an iterable of integers") from exc


def _optional_positive_int(name: str, value: int | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise KeyHashingError(f"{name} must be a positive integer")
    coerced = int(value)
    if coerced <= 0:
        raise KeyHashingError(f"{name} must be positive")
    return coerced


def _non_negative_int(name: str, value: int) -> int:
    if isinstance(value, bool):
        raise KeyHashingError(f"{name} entries must be integers")
    coerced = int(value)
    if coerced < 0:
        raise KeyHashingError(f"{name} entries must be non-negative")
    return coerced


def _field_name(path: str, key: object) -> str:
    if isinstance(key, str):
        name = key
    elif isinstance(key, int) and not isinstance(key, bool):
        name = str(key)
    else:
        raise KeyHashingError(f"{path}: mapping keys must be strings or integers")
    return name if _field_name_allowed(path, name) else name


def _field_name_allowed(path: str, name: str) -> bool:
    if name.lower() in _FORBIDDEN_FIELD_NAMES:
        raise KeyHashingError(f"{path}.{name}: mutable local field is not key material")
    _reject_memory_address(f"{path}.{name}", name)
    return True


def _reject_memory_address(path: str, value: str) -> None:
    if _ADDRESS_RE.search(value):
        raise KeyHashingError(f"{path} contains a memory address")


def _looks_like_torch_dtype(value: object) -> bool:
    value_type = type(value)
    return (
        value_type.__module__.startswith("torch")
        and value_type.__qualname__ == "dtype"
    )


__all__ = [
    "CONNECTOR_BLOB_FORMAT_VERSION",
    "CPU_STAGING_FORMAT_VERSION",
    "KEY_HASH_DOMAIN",
    "KEY_REPR_VERSION",
    "KV_CACHE_CONFIG_HASH_DOMAIN",
    "LAYOUT_FINGERPRINT_DOMAIN",
    "stable_kv_cache_config_hash",
    "stable_layout_fingerprint",
    "stable_vllm_blob_key",
    "vllm_blob_key_hash",
]

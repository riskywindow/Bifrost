from __future__ import annotations

from dataclasses import asdict
from typing import Any, Iterable

import torch

from bifrost_kv.canonical import canonical_encode
from bifrost_kv.hashing import blake3_hex
from bifrost_kv.target_profile import SUPPORTED_TARGET_SCHEMA_VERSION
from bifrost_model.config import TinyTransformerConfig
from bifrost_model.tokenizer import TinyIntTokenizer

MODEL_ID = "bifrost_tiny_transformer"
MODEL_REVISION = "phase4.v1"
ENGINE_NAME = "bifrost_tiny_transformer"
ENGINE_VERSION = "phase4.v1"
INTEGRATION_NAME = "bifrost_phase4_tiny_harness"
INTEGRATION_VERSION = "phase4.v1"
ATTENTION_IMPL = "tiny_eager_attention"
KV_LAYOUT = "layer_block_kv_head_dim"
KV_CACHE_FORMAT = "bifrost_native_v1"
DEFAULT_BLOCK_SIZE_TOKENS = 4

TOKEN_HASH_DOMAIN = b"bifrost.phase4.tokens.v1\x00"
PREFIX_HASH_DOMAIN = b"bifrost.prefix.v1\x00"
PROFILE_HASH_DOMAIN = b"bifrost.phase4.profile.v1\x00"
TENSOR_HASH_DOMAIN = b"bifrost.phase4.tensor.v1\x00"


def config_hash(config: TinyTransformerConfig) -> str:
    return _canonical_hash("tiny_transformer_config", _config_dict(config))


def tokenizer_hash(tokenizer: TinyIntTokenizer) -> str:
    return _canonical_hash("tiny_int_tokenizer_config", tokenizer.to_config())


def rope_config_hash(config: TinyTransformerConfig) -> str:
    """Hash the tiny harness positional encoding config.

    The Phase 1 schema field is named ``rope_config_hash``. This harness uses
    learned absolute position embeddings, so the value is the deterministic
    positional-encoding config hash carried in that compatibility field.
    """

    return _canonical_hash(
        "tiny_transformer_position_config",
        {
            "position_encoding": "learned_absolute",
            "max_position_embeddings": config.max_seq_len,
            "hidden_size": config.hidden_size,
        },
    )


def model_hash(model: torch.nn.Module, config: TinyTransformerConfig) -> str:
    parameter_hashes: list[dict[str, Any]] = []
    for name, tensor in sorted(model.state_dict().items()):
        parameter_hashes.append(
            {
                "name": name,
                "dtype": _tensor_dtype_name(tensor),
                "shape": list(tensor.shape),
                "tensor_hash": _tensor_hash(tensor),
            }
        )

    return _canonical_hash(
        "tiny_transformer_model",
        {
            "config": _config_dict(config),
            "parameter_hashes": parameter_hashes,
        },
    )


def token_hash(tokens: Iterable[int], tokenizer: TinyIntTokenizer) -> str:
    token_ids = _validate_tokens(tokens, tokenizer)
    material = bytearray(TOKEN_HASH_DOMAIN)
    material.extend(len(token_ids).to_bytes(4, byteorder="little", signed=False))
    for token_id in token_ids:
        material.extend(token_id.to_bytes(4, byteorder="little", signed=False))
    return blake3_hex(bytes(material))


def build_model_profile(
    model: torch.nn.Module,
    tokenizer: TinyIntTokenizer,
    config: TinyTransformerConfig,
) -> dict[str, Any]:
    return {
        "model_id": MODEL_ID,
        "model_revision": MODEL_REVISION,
        "model_hash": model_hash(model, config),
        "tokenizer_hash": tokenizer_hash(tokenizer),
        "config_hash": config_hash(config),
        "rope_config_hash": rope_config_hash(config),
        "quantization": "none",
        "dtype": config.dtype,
        "num_layers": config.num_layers,
        "num_attention_heads": config.num_heads,
        "num_kv_heads": config.num_kv_heads,
        "head_dim": config.head_dim,
        "max_position_embeddings": config.max_seq_len,
    }


def build_engine_profile(
    config: TinyTransformerConfig,
    *,
    block_size_tokens: int = DEFAULT_BLOCK_SIZE_TOKENS,
) -> dict[str, Any]:
    if block_size_tokens <= 0:
        raise ValueError("block_size_tokens must be positive")
    return {
        "engine_name": ENGINE_NAME,
        "engine_version": ENGINE_VERSION,
        "integration_name": INTEGRATION_NAME,
        "integration_version": INTEGRATION_VERSION,
        "attention_impl": ATTENTION_IMPL,
        "kv_layout": KV_LAYOUT,
        "block_size_tokens": block_size_tokens,
        "kv_cache_format": KV_CACHE_FORMAT,
    }


def build_prefix_profile(
    tokens: Iterable[int],
    tokenizer: TinyIntTokenizer,
    config: TinyTransformerConfig,
    token_range: dict[str, int] | None = None,
) -> dict[str, Any]:
    token_ids = _validate_tokens(tokens, tokenizer)
    if token_range is None:
        start = 0
        end = len(token_ids)
    else:
        start = _validate_range_value(token_range, "start")
        end = _validate_range_value(token_range, "end")
        if start > end:
            raise ValueError("token_range start must be <= end")
        if end > len(token_ids):
            raise ValueError("token_range end exceeds token count")

    selected_tokens = token_ids[start:end]
    current_token_hash = token_hash(selected_tokens, tokenizer)
    current_tokenizer_hash = tokenizer_hash(tokenizer)
    current_rope_hash = rope_config_hash(config)
    prefix_material = bytearray(PREFIX_HASH_DOMAIN)
    prefix_material.extend(current_tokenizer_hash.encode("utf-8"))
    prefix_material.extend(b"\x00")
    prefix_material.extend(current_rope_hash.encode("utf-8"))
    prefix_material.extend(b"\x00")
    prefix_material.extend(current_token_hash.encode("utf-8"))
    prefix_material.extend(start.to_bytes(8, byteorder="little", signed=False))
    prefix_material.extend(end.to_bytes(8, byteorder="little", signed=False))
    prefix_material.extend(canonical_encode({"mm_hashes": []}))

    range_obj = {"start": start, "end": end}
    return {
        "prefix_hash": blake3_hex(bytes(prefix_material)),
        "token_hash": current_token_hash,
        "tokenizer_hash": current_tokenizer_hash,
        "rope_config_hash": current_rope_hash,
        "token_count": len(selected_tokens),
        "token_range": dict(range_obj),
        "absolute_position_range": dict(range_obj),
        "mm_hashes": [],
    }


def build_native_target_profile(
    model: torch.nn.Module,
    tokenizer: TinyIntTokenizer,
    config: TinyTransformerConfig,
    tokens: Iterable[int],
    token_range: dict[str, int] | None = None,
    *,
    block_size_tokens: int = DEFAULT_BLOCK_SIZE_TOKENS,
) -> dict[str, Any]:
    token_ids = _validate_tokens(tokens, tokenizer)
    prefix_profile = build_prefix_profile(token_ids, tokenizer, config, token_range)
    full_prefix_profile = build_prefix_profile(token_ids, tokenizer, config)
    return {
        "schema_version": SUPPORTED_TARGET_SCHEMA_VERSION,
        "accepts_object_type": "native_kv_page",
        "model_profile": build_model_profile(model, tokenizer, config),
        "engine_profile": build_engine_profile(
            config, block_size_tokens=block_size_tokens
        ),
        "prefix_requirements": {
            "prefix_hash": full_prefix_profile["prefix_hash"],
            "token_hash": full_prefix_profile["token_hash"],
            "tokenizer_hash": prefix_profile["tokenizer_hash"],
            "rope_config_hash": prefix_profile["rope_config_hash"],
            "token_range": prefix_profile["token_range"],
            "absolute_position_range": prefix_profile["absolute_position_range"],
            "allow_mm_hashes": list(prefix_profile["mm_hashes"]),
        },
        "opaque_requirements": None,
    }


def _canonical_hash(kind: str, value: dict[str, Any]) -> str:
    return blake3_hex(
        PROFILE_HASH_DOMAIN + kind.encode("utf-8") + b"\x00" + canonical_encode(value)
    )


def _config_dict(config: TinyTransformerConfig) -> dict[str, Any]:
    return asdict(config)


def _tensor_hash(tensor: torch.Tensor) -> str:
    detached = tensor.detach().cpu().contiguous()
    material = bytearray(TENSOR_HASH_DOMAIN)
    material.extend(_tensor_dtype_name(detached).encode("utf-8"))
    material.extend(b"\x00")
    material.extend(canonical_encode({"shape": list(detached.shape)}))
    material.extend(b"\x00")
    material.extend(detached.numpy().tobytes(order="C"))
    return blake3_hex(bytes(material))


def _tensor_dtype_name(tensor: torch.Tensor) -> str:
    if tensor.dtype == torch.float32:
        return "float32"
    return str(tensor.dtype).removeprefix("torch.")


def _validate_tokens(tokens: Iterable[int], tokenizer: TinyIntTokenizer) -> list[int]:
    token_ids = list(tokens)
    for token_id in token_ids:
        tokenizer._validate_token_id(token_id)
    return token_ids


def _validate_range_value(token_range: dict[str, int], key: str) -> int:
    value = token_range.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"token_range {key} must be a non-negative integer")
    return value


__all__ = [
    "build_engine_profile",
    "build_model_profile",
    "build_native_target_profile",
    "build_prefix_profile",
    "config_hash",
    "model_hash",
    "rope_config_hash",
    "token_hash",
    "tokenizer_hash",
]

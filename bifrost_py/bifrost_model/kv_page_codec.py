from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

import numpy as np
import torch

from bifrost_kv.hashing import (
    compute_descriptor_hash,
    compute_object_id,
    compute_payload_hash,
)
from bifrost_kv.validate import SUPPORTED_SCHEMA_VERSION, validate_object
from bifrost_model.config import TinyTransformerConfig
from bifrost_model.kv_cache import (
    KVCacheBlock,
    merge_kv_cache_blocks,
    split_kv_cache_into_blocks,
    validate_past_key_values,
)
from bifrost_model.profile import (
    build_engine_profile,
    build_model_profile,
    build_native_target_profile,
    build_prefix_profile,
)
from bifrost_model.tiny_transformer import PastKeyValues
from bifrost_model.tokenizer import TinyIntTokenizer


@dataclass(frozen=True)
class NativePage:
    metadata: dict[str, Any]
    payload: bytes
    target_profile: dict[str, Any]


def kv_block_to_native_page(
    block: KVCacheBlock,
    model: torch.nn.Module,
    tokenizer: TinyIntTokenizer,
    config: TinyTransformerConfig,
    full_tokens: Iterable[int],
    block_size_tokens: int,
) -> tuple[dict[str, Any], bytes]:
    token_ids = _normalize_tokens(full_tokens)
    _validate_block(block, config, block_size_tokens, len(token_ids))

    payload = _block_payload_bytes(block)
    start, end = block.token_range
    token_range = {"start": start, "end": end}
    page_prefix_profile = build_prefix_profile(
        token_ids,
        tokenizer,
        config,
        token_range=token_range,
    )
    full_prefix_profile = build_prefix_profile(token_ids, tokenizer, config)
    page_prefix_profile["prefix_hash"] = full_prefix_profile["prefix_hash"]
    page_prefix_profile["token_hash"] = full_prefix_profile["token_hash"]
    metadata: dict[str, Any] = {
        "schema_version": SUPPORTED_SCHEMA_VERSION,
        "object_type": "native_kv_page",
        "object_id": "bifrost://object/blake3/" + "0" * 64,
        "created_at_unix_ms": 0,
        "created_by": "bifrost_phase4_tiny_harness",
        "model_profile": build_model_profile(model, tokenizer, config),
        "engine_profile": build_engine_profile(
            config, block_size_tokens=block_size_tokens
        ),
        "prefix_profile": page_prefix_profile,
        "payload_profile": {
            "byte_length": len(payload),
            "compression": "none",
            "payload_encoding": "raw_bytes",
        },
        "native_tensor_profile": {
            "layer_id": block.layer_id,
            "kv_block_id": block.kv_block_id,
            "block_size_tokens": block_size_tokens,
            "block_token_count": end - start,
            "token_range": dict(token_range),
            "tensor_role": "kv_pair",
            "tensor_shape": [
                2,
                end - start,
                config.num_kv_heads,
                config.head_dim,
            ],
            "tensor_dtype": config.dtype,
            "tensor_layout": "kv_token_head_dim",
        },
        "opaque_engine_profile": None,
        "integrity": {
            "descriptor_hash": "blake3:" + "0" * 64,
            "payload_hash": "blake3:" + "0" * 64,
            "object_id_algorithm": "bifrost.object_id.v1",
            "chunk_size_bytes": len(payload),
            "chunk_hashes": [],
        },
        "provenance": {
            "source": "bifrost_phase4_tiny_harness",
            "notes": "tiny-transformer KV page serialization",
            "producer_commit": "unknown",
            "producer_hostname": "localhost",
        },
    }
    payload_hash = compute_payload_hash(payload)
    metadata["integrity"]["payload_hash"] = payload_hash
    metadata["integrity"]["chunk_hashes"] = [payload_hash]
    descriptor_hash = compute_descriptor_hash(metadata, payload_hash)
    metadata["integrity"]["descriptor_hash"] = descriptor_hash
    metadata["object_id"] = compute_object_id(descriptor_hash, payload_hash)
    return metadata, payload


def kv_cache_to_native_pages(
    past_key_values: PastKeyValues,
    model: torch.nn.Module,
    tokenizer: TinyIntTokenizer,
    config: TinyTransformerConfig,
    full_tokens: Iterable[int],
    block_size_tokens: int,
) -> list[NativePage]:
    token_ids = _normalize_tokens(full_tokens)
    validate_past_key_values(past_key_values, config)

    pages: list[NativePage] = []
    for block in split_kv_cache_into_blocks(past_key_values, block_size_tokens):
        metadata, payload = kv_block_to_native_page(
            block,
            model,
            tokenizer,
            config,
            token_ids,
            block_size_tokens,
        )
        target_profile = build_native_target_profile(
            model,
            tokenizer,
            config,
            token_ids,
            token_range={
                "start": block.token_range[0],
                "end": block.token_range[1],
            },
            block_size_tokens=block_size_tokens,
        )
        full_prefix_profile = build_prefix_profile(token_ids, tokenizer, config)
        target_profile["prefix_requirements"]["prefix_hash"] = full_prefix_profile[
            "prefix_hash"
        ]
        target_profile["prefix_requirements"]["token_hash"] = full_prefix_profile[
            "token_hash"
        ]
        _raise_if_rejected(metadata, payload, target_profile)
        pages.append(
            NativePage(
                metadata=deepcopy(metadata),
                payload=bytes(payload),
                target_profile=deepcopy(target_profile),
            )
        )
    return pages


def native_page_to_kv_block(
    metadata: dict[str, Any],
    payload: bytes,
    target_profile: dict[str, Any],
) -> KVCacheBlock:
    _raise_if_rejected(metadata, payload, target_profile)

    native = metadata["native_tensor_profile"]
    model_profile = metadata["model_profile"]
    tensor = _payload_to_tensor(payload, native, model_profile)
    key = tensor[0].detach().clone().contiguous()
    value = tensor[1].detach().clone().contiguous()
    token_range = native["token_range"]
    return KVCacheBlock(
        layer_id=native["layer_id"],
        kv_block_id=native["kv_block_id"],
        token_range=(token_range["start"], token_range["end"]),
        key=key,
        value=value,
    )


def native_pages_to_kv_cache(
    pages: Sequence[NativePage],
    config: TinyTransformerConfig,
) -> PastKeyValues:
    if not pages:
        raise ValueError("pages must contain at least one native KV page")

    seen: set[tuple[int, int]] = set()
    blocks: list[KVCacheBlock] = []
    for page in pages:
        block = native_page_to_kv_block(
            page.metadata,
            page.payload,
            page.target_profile,
        )
        coordinate = (block.layer_id, block.kv_block_id)
        if coordinate in seen:
            raise ValueError(
                f"duplicate native KV page for layer {block.layer_id} block "
                f"{block.kv_block_id}"
            )
        seen.add(coordinate)
        blocks.append(block)

    return merge_kv_cache_blocks(blocks, config)


def _block_payload_bytes(block: KVCacheBlock) -> bytes:
    stacked = torch.stack(
        (
            block.key.detach().cpu().contiguous(),
            block.value.detach().cpu().contiguous(),
        ),
        dim=0,
    )
    array = stacked.numpy().astype("<f4", copy=False)
    return array.tobytes(order="C")


def _payload_to_tensor(
    payload: bytes,
    native_tensor_profile: dict[str, Any],
    model_profile: dict[str, Any],
) -> torch.Tensor:
    if native_tensor_profile["tensor_dtype"] != "float32":
        raise ValueError("unsupported native tensor dtype for tiny harness")
    if model_profile["dtype"] != "float32":
        raise ValueError("unsupported model dtype for tiny harness")

    shape = tuple(native_tensor_profile["tensor_shape"])
    expected_elements = int(np.prod(shape))
    array = np.frombuffer(payload, dtype="<f4")
    if array.size != expected_elements:
        raise ValueError("payload element count does not match tensor shape")
    return torch.from_numpy(array.copy()).reshape(shape).to(dtype=torch.float32)


def _raise_if_rejected(
    metadata: dict[str, Any],
    payload: bytes,
    target_profile: dict[str, Any],
) -> None:
    result = validate_object(metadata, payload, target_profile)
    if result.status != "accepted":
        raise ValueError(f"native KV page rejected: {result.reason_code}")


def _normalize_tokens(tokens: Iterable[int]) -> list[int]:
    token_ids = list(tokens)
    for token_id in token_ids:
        if isinstance(token_id, bool) or not isinstance(token_id, int):
            raise ValueError("full_tokens must contain integer token IDs")
    if not token_ids:
        raise ValueError("full_tokens must contain at least one token")
    return token_ids


def _validate_block(
    block: KVCacheBlock,
    config: TinyTransformerConfig,
    block_size_tokens: int,
    token_count: int,
) -> None:
    if block_size_tokens <= 0:
        raise ValueError("block_size_tokens must be positive")
    if block.layer_id < 0 or block.layer_id >= config.num_layers:
        raise ValueError("block layer_id is out of range")
    start, end = block.token_range
    if start < 0 or end <= start or end > token_count:
        raise ValueError("block token_range is outside full_tokens")
    if block.kv_block_id != start // block_size_tokens:
        raise ValueError("block kv_block_id does not match token_range")
    if end - start > block_size_tokens:
        raise ValueError("block token_range exceeds block_size_tokens")

    expected_shape = (end - start, config.num_kv_heads, config.head_dim)
    for tensor, role in ((block.key, "key"), (block.value, "value")):
        if not torch.is_tensor(tensor):
            raise ValueError(f"block {role} must be a tensor")
        if tuple(tensor.shape) != expected_shape:
            raise ValueError(f"block {role} has wrong shape")
        if tensor.dtype != config.torch_dtype:
            raise ValueError(f"block {role} has wrong dtype")
        if tensor.device.type != "cpu":
            raise ValueError(f"block {role} must be on CPU")


__all__ = [
    "NativePage",
    "kv_block_to_native_page",
    "kv_cache_to_native_pages",
    "native_page_to_kv_block",
    "native_pages_to_kv_cache",
]

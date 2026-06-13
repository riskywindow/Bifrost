from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import torch

from bifrost_model.config import TinyTransformerConfig
from bifrost_model.tiny_transformer import PastKeyValues


DEFAULT_KV_RTOL = 1e-5
DEFAULT_KV_ATOL = 1e-6


@dataclass(frozen=True)
class KVCacheBlock:
    layer_id: int
    kv_block_id: int
    token_range: tuple[int, int]
    key: torch.Tensor
    value: torch.Tensor


def validate_past_key_values(
    past_key_values: PastKeyValues,
    config: TinyTransformerConfig,
) -> None:
    if not isinstance(past_key_values, list):
        raise ValueError("past_key_values must be a list")
    if len(past_key_values) != config.num_layers:
        raise ValueError("past_key_values must contain one KV tuple per layer")
    if not past_key_values:
        raise ValueError("past_key_values must contain at least one layer")

    expected_seq_len: int | None = None
    for layer_id, layer_kv in enumerate(past_key_values):
        if not isinstance(layer_kv, tuple) or len(layer_kv) != 2:
            raise ValueError(f"layer {layer_id} cache must be a key/value tuple")

        key, value = layer_kv
        _validate_tensor(key, config, layer_id, "key")
        _validate_tensor(value, config, layer_id, "value")

        if key.shape != value.shape:
            raise ValueError(f"layer {layer_id} key and value shapes differ")
        layer_seq_len = key.shape[0]
        if expected_seq_len is None:
            expected_seq_len = layer_seq_len
        elif layer_seq_len != expected_seq_len:
            raise ValueError(f"layer {layer_id} cache has inconsistent token count")


def clone_past_key_values(past_key_values: PastKeyValues) -> PastKeyValues:
    return [
        (key.detach().clone(), value.detach().clone())
        for key, value in past_key_values
    ]


def kv_cache_token_count(past_key_values: PastKeyValues) -> int:
    if not past_key_values:
        raise ValueError("past_key_values must contain at least one layer")
    return int(past_key_values[0][0].shape[0])


def split_kv_cache_into_blocks(
    past_key_values: PastKeyValues,
    block_size_tokens: int,
) -> list[KVCacheBlock]:
    if block_size_tokens <= 0:
        raise ValueError("block_size_tokens must be positive")

    token_count = kv_cache_token_count(past_key_values)
    blocks: list[KVCacheBlock] = []
    for layer_id, (key, value) in enumerate(past_key_values):
        if key.shape[0] != token_count or value.shape[0] != token_count:
            raise ValueError(f"layer {layer_id} cache has inconsistent token count")
        for start in range(0, token_count, block_size_tokens):
            end = min(start + block_size_tokens, token_count)
            blocks.append(
                KVCacheBlock(
                    layer_id=layer_id,
                    kv_block_id=start // block_size_tokens,
                    token_range=(start, end),
                    key=key[start:end].detach().clone().contiguous(),
                    value=value[start:end].detach().clone().contiguous(),
                )
            )
    return blocks


def merge_kv_cache_blocks(
    blocks: Iterable[KVCacheBlock],
    config: TinyTransformerConfig,
) -> PastKeyValues:
    block_list = list(blocks)
    if not block_list:
        raise ValueError("blocks must contain at least one KV cache block")

    by_layer: list[list[KVCacheBlock]] = [[] for _ in range(config.num_layers)]
    for block in block_list:
        _validate_block_metadata(block, config)
        by_layer[block.layer_id].append(block)

    merged: PastKeyValues = []
    expected_token_count: int | None = None
    expected_block_count: int | None = None
    for layer_id, layer_blocks in enumerate(by_layer):
        if not layer_blocks:
            raise ValueError(f"layer {layer_id} has no KV cache blocks")

        sorted_blocks = sorted(layer_blocks, key=lambda block: block.token_range)
        if expected_block_count is None:
            expected_block_count = len(sorted_blocks)
        elif len(sorted_blocks) != expected_block_count:
            raise ValueError(f"layer {layer_id} has wrong block count")

        cursor = 0
        keys: list[torch.Tensor] = []
        values: list[torch.Tensor] = []
        for expected_index, block in enumerate(sorted_blocks):
            start, end = block.token_range
            if start != cursor:
                raise ValueError(f"layer {layer_id} has a missing or overlapping block")
            if block.kv_block_id != expected_index:
                raise ValueError(f"layer {layer_id} has wrong kv_block_id ordering")
            block_tokens = end - start
            expected_shape = (block_tokens, config.num_kv_heads, config.head_dim)
            if tuple(block.key.shape) != expected_shape:
                raise ValueError(f"layer {layer_id} block key has wrong shape")
            if tuple(block.value.shape) != expected_shape:
                raise ValueError(f"layer {layer_id} block value has wrong shape")
            keys.append(block.key.detach().clone().contiguous())
            values.append(block.value.detach().clone().contiguous())
            cursor = end

        if expected_token_count is None:
            expected_token_count = cursor
        elif cursor != expected_token_count:
            raise ValueError(f"layer {layer_id} has wrong token count")

        merged.append((torch.cat(keys, dim=0), torch.cat(values, dim=0)))

    validate_past_key_values(merged, config)
    return merged


def compare_kv_caches(
    a: PastKeyValues,
    b: PastKeyValues,
    atol: float = DEFAULT_KV_ATOL,
    rtol: float = DEFAULT_KV_RTOL,
) -> bool:
    if len(a) != len(b):
        return False
    for (a_key, a_value), (b_key, b_value) in zip(a, b, strict=True):
        if a_key.shape != b_key.shape or a_value.shape != b_value.shape:
            return False
        if a_key.dtype != b_key.dtype or a_value.dtype != b_value.dtype:
            return False
        if not torch.allclose(a_key, b_key, atol=atol, rtol=rtol):
            return False
        if not torch.allclose(a_value, b_value, atol=atol, rtol=rtol):
            return False
    return True


@torch.no_grad()
def resume_generate_greedy(
    model: torch.nn.Module,
    next_input_id: int | torch.Tensor,
    past_key_values: PastKeyValues,
    max_new_tokens: int,
) -> torch.Tensor:
    if max_new_tokens < 0:
        raise ValueError("max_new_tokens must be non-negative")
    if max_new_tokens == 0:
        return torch.empty((0,), dtype=torch.long)

    current_id = _normalize_single_token(next_input_id)
    generated: list[int] = []
    current_cache = past_key_values
    for token_index in range(max_new_tokens):
        generated.append(current_id)
        if token_index == max_new_tokens - 1:
            break
        logits, current_cache = model.decode_one(current_id, current_cache)
        current_id = int(torch.argmax(logits).item())

    return torch.tensor(generated, dtype=torch.long)


def _validate_tensor(
    tensor: torch.Tensor,
    config: TinyTransformerConfig,
    layer_id: int,
    role: str,
) -> None:
    if not torch.is_tensor(tensor):
        raise ValueError(f"layer {layer_id} {role} must be a tensor")
    expected_tail = (config.num_kv_heads, config.head_dim)
    if tensor.dim() != 3 or tuple(tensor.shape[1:]) != expected_tail:
        raise ValueError(f"layer {layer_id} {role} has wrong shape")
    if tensor.dtype != config.torch_dtype:
        raise ValueError(f"layer {layer_id} {role} has wrong dtype")
    if tensor.device.type != "cpu":
        raise ValueError(f"layer {layer_id} {role} must be on CPU")


def _validate_block_metadata(
    block: KVCacheBlock,
    config: TinyTransformerConfig,
) -> None:
    if block.layer_id < 0 or block.layer_id >= config.num_layers:
        raise ValueError("block layer_id is out of range")
    if block.kv_block_id < 0:
        raise ValueError("block kv_block_id must be non-negative")
    start, end = block.token_range
    if start < 0 or end <= start:
        raise ValueError("block token_range must be non-empty and half-open")
    for tensor, role in ((block.key, "key"), (block.value, "value")):
        _validate_tensor(tensor, config, block.layer_id, role)


def _normalize_single_token(input_id: int | torch.Tensor) -> int:
    if torch.is_tensor(input_id):
        input_id = input_id.detach().cpu()
        if input_id.numel() != 1:
            raise ValueError("next_input_id must contain exactly one token")
        return int(input_id.item())
    return int(input_id)

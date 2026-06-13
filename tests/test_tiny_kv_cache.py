from __future__ import annotations

import sys
from pathlib import Path

import pytest
import torch

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "bifrost_py"))

from bifrost_model import TinyTransformer, TinyTransformerConfig
from bifrost_model.kv_cache import (
    clone_past_key_values,
    compare_kv_caches,
    kv_cache_token_count,
    merge_kv_cache_blocks,
    resume_generate_greedy,
    split_kv_cache_into_blocks,
    validate_past_key_values,
)


TOKENS = torch.tensor([3, 7, 11, 5, 2], dtype=torch.long)


def build_model(seed: int = 1234) -> TinyTransformer:
    model = TinyTransformer(TinyTransformerConfig(seed=seed))
    model.eval()
    return model


def test_validate_past_key_values_accepts_model_output() -> None:
    model = build_model()
    _, past_key_values = model.prefill(TOKENS)

    validate_past_key_values(past_key_values, model.config)


def test_validate_past_key_values_rejects_wrong_layer_count() -> None:
    model = build_model()
    _, past_key_values = model.prefill(TOKENS)

    with pytest.raises(ValueError, match="one KV tuple per layer"):
        validate_past_key_values(past_key_values[:-1], model.config)


def test_validate_past_key_values_rejects_wrong_shape() -> None:
    model = build_model()
    _, past_key_values = model.prefill(TOKENS)
    bad_cache = clone_past_key_values(past_key_values)
    bad_cache[0] = (bad_cache[0][0][:, :, :-1], bad_cache[0][1])

    with pytest.raises(ValueError, match="wrong shape"):
        validate_past_key_values(bad_cache, model.config)


def test_clone_does_not_alias_original_tensors() -> None:
    model = build_model()
    _, past_key_values = model.prefill(TOKENS)

    cloned = clone_past_key_values(past_key_values)
    cloned[0][0][0, 0, 0] += 1.0

    assert not torch.equal(cloned[0][0], past_key_values[0][0])
    assert torch.equal(cloned[0][1], past_key_values[0][1])


def test_split_into_blocks_and_merge_returns_equivalent_kv_cache() -> None:
    model = build_model()
    _, past_key_values = model.prefill(TOKENS)

    blocks = split_kv_cache_into_blocks(past_key_values, block_size_tokens=2)
    merged = merge_kv_cache_blocks(blocks, model.config)

    assert compare_kv_caches(past_key_values, merged)


def test_non_divisible_sequence_length_creates_smaller_final_block() -> None:
    model = build_model()
    _, past_key_values = model.prefill(TOKENS)

    blocks = split_kv_cache_into_blocks(past_key_values, block_size_tokens=4)

    assert kv_cache_token_count(past_key_values) == 5
    layer_zero_blocks = [block for block in blocks if block.layer_id == 0]
    assert [block.token_range for block in layer_zero_blocks] == [(0, 4), (4, 5)]
    assert [block.kv_block_id for block in layer_zero_blocks] == [0, 1]
    assert layer_zero_blocks[-1].key.shape[0] == 1
    assert layer_zero_blocks[-1].value.shape[0] == 1


def test_decode_from_cloned_kv_matches_decode_from_original_kv() -> None:
    model = build_model()
    _, past_key_values = model.prefill(TOKENS)
    cloned = clone_past_key_values(past_key_values)

    original_logits, original_cache = model.decode_one(13, past_key_values)
    cloned_logits, cloned_cache = model.decode_one(13, cloned)

    torch.testing.assert_close(original_logits, cloned_logits, rtol=1e-5, atol=1e-6)
    assert compare_kv_caches(original_cache, cloned_cache)


def test_resume_generate_greedy_from_kv_matches_uninterrupted_generation() -> None:
    model = build_model()
    max_new_tokens = 4

    baseline = model.generate_greedy(TOKENS, max_new_tokens=max_new_tokens)
    prefix_logits, past_key_values = model.prefill(TOKENS)
    next_input_id = int(torch.argmax(prefix_logits[-1]).item())
    resumed = resume_generate_greedy(
        model,
        next_input_id,
        clone_past_key_values(past_key_values),
        max_new_tokens=max_new_tokens,
    )

    assert torch.equal(resumed, baseline[len(TOKENS) :])


def test_corrupting_one_kv_tensor_changes_comparison_result() -> None:
    model = build_model()
    _, past_key_values = model.prefill(TOKENS)
    corrupted = clone_past_key_values(past_key_values)

    corrupted[0][0][0, 0, 0] += 1.0

    assert not compare_kv_caches(past_key_values, corrupted)

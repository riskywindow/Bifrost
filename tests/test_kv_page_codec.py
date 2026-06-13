from __future__ import annotations

from copy import deepcopy
import sys
from pathlib import Path

import pytest
import torch

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "bifrost_py"))

from bifrost_kv.errors import ACCEPTED
from bifrost_kv.validate import validate_object
from bifrost_model import TinyIntTokenizer, TinyTransformer, TinyTransformerConfig
from bifrost_model.kv_cache import compare_kv_caches, split_kv_cache_into_blocks
from bifrost_model.kv_page_codec import (
    NativePage,
    kv_block_to_native_page,
    kv_cache_to_native_pages,
    native_page_to_kv_block,
    native_pages_to_kv_cache,
)
from bifrost_model.profile import build_native_target_profile


TOKENS = [3, 7, 11, 5, 2]
BLOCK_SIZE = 4


def build_harness() -> tuple[TinyTransformer, TinyIntTokenizer, TinyTransformerConfig]:
    config = TinyTransformerConfig()
    model = TinyTransformer(config)
    model.eval()
    tokenizer = TinyIntTokenizer(vocab_size=config.vocab_size)
    return model, tokenizer, config


def prefill(
    model: TinyTransformer,
    tokens: list[int] = TOKENS,
) -> tuple[torch.Tensor, list[tuple[torch.Tensor, torch.Tensor]]]:
    return model.prefill(torch.tensor(tokens, dtype=torch.long))


def test_one_kv_block_serializes_to_valid_native_page() -> None:
    model, tokenizer, config = build_harness()
    _, past_key_values = prefill(model)
    block = split_kv_cache_into_blocks(past_key_values, BLOCK_SIZE)[0]

    metadata, payload = kv_block_to_native_page(
        block,
        model,
        tokenizer,
        config,
        TOKENS,
        BLOCK_SIZE,
    )
    target = build_native_target_profile(
        model,
        tokenizer,
        config,
        TOKENS,
        token_range={"start": 0, "end": 4},
        block_size_tokens=BLOCK_SIZE,
    )

    result = validate_object(metadata, payload, target)

    assert result.reason_code == ACCEPTED
    assert metadata["native_tensor_profile"]["tensor_shape"] == [2, 4, 2, 8]
    assert metadata["payload_profile"]["byte_length"] == len(payload)


def test_all_pages_from_prefill_validate() -> None:
    model, tokenizer, config = build_harness()
    _, past_key_values = prefill(model)

    pages = kv_cache_to_native_pages(
        past_key_values,
        model,
        tokenizer,
        config,
        TOKENS,
        BLOCK_SIZE,
    )

    assert len(pages) == config.num_layers * 2
    for page in pages:
        assert (
            validate_object(page.metadata, page.payload, page.target_profile).reason_code
            == ACCEPTED
        )


def test_serialization_then_deserialization_gives_equivalent_kv_cache() -> None:
    model, tokenizer, config = build_harness()
    _, past_key_values = prefill(model)

    pages = kv_cache_to_native_pages(
        past_key_values,
        model,
        tokenizer,
        config,
        TOKENS,
        BLOCK_SIZE,
    )
    rehydrated = native_pages_to_kv_cache(pages, config)

    assert compare_kv_caches(past_key_values, rehydrated)


def test_logits_after_rehydrated_kv_match_logits_after_original_kv() -> None:
    model, tokenizer, config = build_harness()
    _, past_key_values = prefill(model)
    pages = kv_cache_to_native_pages(
        past_key_values,
        model,
        tokenizer,
        config,
        TOKENS,
        BLOCK_SIZE,
    )
    rehydrated = native_pages_to_kv_cache(pages, config)

    original_logits, _ = model.decode_one(13, past_key_values)
    rehydrated_logits, _ = model.decode_one(13, rehydrated)

    torch.testing.assert_close(
        rehydrated_logits,
        original_logits,
        rtol=1e-5,
        atol=1e-6,
    )


def test_wrong_tokenizer_hash_rejects() -> None:
    page = first_page()
    target = deepcopy(page.target_profile)
    target["model_profile"]["tokenizer_hash"] = "blake3:" + "9" * 64

    with pytest.raises(ValueError, match="wrong_tokenizer_hash"):
        native_page_to_kv_block(page.metadata, page.payload, target)


def test_wrong_model_hash_rejects() -> None:
    page = first_page()
    target = deepcopy(page.target_profile)
    target["model_profile"]["model_hash"] = "blake3:" + "9" * 64

    with pytest.raises(ValueError, match="wrong_model_hash"):
        native_page_to_kv_block(page.metadata, page.payload, target)


def test_wrong_prefix_hash_rejects() -> None:
    page = first_page()
    target = deepcopy(page.target_profile)
    target["prefix_requirements"]["prefix_hash"] = "blake3:" + "9" * 64

    with pytest.raises(ValueError, match="wrong_prefix_hash"):
        native_page_to_kv_block(page.metadata, page.payload, target)


def test_corrupted_payload_rejects() -> None:
    page = first_page()
    payload = bytearray(page.payload)
    payload[0] ^= 0x01

    with pytest.raises(ValueError, match="payload_hash_mismatch"):
        native_page_to_kv_block(page.metadata, bytes(payload), page.target_profile)


def test_missing_page_prevents_full_cache_assembly() -> None:
    model, tokenizer, config = build_harness()
    _, past_key_values = prefill(model)
    pages = kv_cache_to_native_pages(
        past_key_values,
        model,
        tokenizer,
        config,
        TOKENS,
        BLOCK_SIZE,
    )

    with pytest.raises(ValueError, match="wrong block count|missing|overlapping"):
        native_pages_to_kv_cache(pages[:-1], config)


def test_duplicate_conflicting_page_rejects() -> None:
    model, tokenizer, config = build_harness()
    _, past_key_values = prefill(model)
    pages = kv_cache_to_native_pages(
        past_key_values,
        model,
        tokenizer,
        config,
        TOKENS,
        BLOCK_SIZE,
    )
    duplicate = NativePage(
        metadata=deepcopy(pages[0].metadata),
        payload=bytes(pages[0].payload),
        target_profile=deepcopy(pages[0].target_profile),
    )

    with pytest.raises(ValueError, match="duplicate native KV page"):
        native_pages_to_kv_cache([*pages, duplicate], config)


def test_non_divisible_final_block_serializes_and_deserializes_correctly() -> None:
    model, tokenizer, config = build_harness()
    _, past_key_values = prefill(model, TOKENS)
    pages = kv_cache_to_native_pages(
        past_key_values,
        model,
        tokenizer,
        config,
        TOKENS,
        BLOCK_SIZE,
    )

    final_pages = [
        page
        for page in pages
        if page.metadata["native_tensor_profile"]["token_range"] == {"start": 4, "end": 5}
    ]
    rehydrated = native_pages_to_kv_cache(pages, config)

    assert len(final_pages) == config.num_layers
    for page in final_pages:
        assert page.metadata["native_tensor_profile"]["tensor_shape"] == [2, 1, 2, 8]
    assert compare_kv_caches(past_key_values, rehydrated)


def first_page() -> NativePage:
    model, tokenizer, config = build_harness()
    _, past_key_values = prefill(model)
    return kv_cache_to_native_pages(
        past_key_values,
        model,
        tokenizer,
        config,
        TOKENS,
        BLOCK_SIZE,
    )[0]

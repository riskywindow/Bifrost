from __future__ import annotations

import sys
from pathlib import Path

import torch

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "bifrost_py"))

from bifrost_kv.target_profile import validate_target_profile_schema
from bifrost_model import TinyIntTokenizer, TinyTransformer, TinyTransformerConfig
from bifrost_model.profile import (
    build_model_profile,
    build_native_target_profile,
    build_prefix_profile,
    config_hash,
    model_hash,
    tokenizer_hash,
)


def build_model(seed: int = 1234) -> TinyTransformer:
    model = TinyTransformer(TinyTransformerConfig(seed=seed))
    model.eval()
    return model


def test_tokenizer_encode_decode_roundtrip() -> None:
    tokenizer = TinyIntTokenizer(vocab_size=16)

    tokens = tokenizer.encode("1 2 3")

    assert tokens == [1, 2, 3]
    assert tokenizer.decode(tokens) == "1 2 3"


def test_tokenizer_rejects_invalid_tokens() -> None:
    tokenizer = TinyIntTokenizer(vocab_size=4)

    invalid_texts = ["1 x", "-1", "4", "1.0"]
    for text in invalid_texts:
        try:
            tokenizer.encode(text)
        except ValueError:
            pass
        else:
            raise AssertionError(f"expected {text!r} to be rejected")

    for tokens in ([True], [-1], [4], [1.0]):
        try:
            tokenizer.decode(tokens)  # type: ignore[arg-type]
        except ValueError:
            pass
        else:
            raise AssertionError(f"expected {tokens!r} to be rejected")


def test_config_hash_is_stable() -> None:
    config = TinyTransformerConfig(seed=1234)

    assert config_hash(config) == config_hash(TinyTransformerConfig(seed=1234))


def test_tokenizer_hash_is_stable() -> None:
    tokenizer = TinyIntTokenizer(vocab_size=128)

    assert tokenizer_hash(tokenizer) == tokenizer_hash(TinyIntTokenizer(vocab_size=128))


def test_model_hash_is_stable_for_same_seed_and_config() -> None:
    config = TinyTransformerConfig(seed=1234)
    model_a = TinyTransformer(config)
    model_b = TinyTransformer(config)

    assert model_hash(model_a, config) == model_hash(model_b, config)


def test_model_hash_changes_when_seed_changes() -> None:
    config_a = TinyTransformerConfig(seed=1234)
    config_b = TinyTransformerConfig(seed=4321)

    assert model_hash(TinyTransformer(config_a), config_a) != model_hash(
        TinyTransformer(config_b), config_b
    )


def test_model_hash_changes_when_weights_change() -> None:
    config = TinyTransformerConfig(seed=1234)
    model = TinyTransformer(config)
    before = model_hash(model, config)

    with torch.no_grad():
        model.token_embeddings.weight[0, 0] += 1.0

    assert model_hash(model, config) != before


def test_prefix_hash_changes_when_token_order_changes() -> None:
    config = TinyTransformerConfig()
    tokenizer = TinyIntTokenizer(vocab_size=config.vocab_size)

    first = build_prefix_profile([1, 2, 3], tokenizer, config)
    second = build_prefix_profile([3, 2, 1], tokenizer, config)

    assert first["prefix_hash"] != second["prefix_hash"]
    assert first["token_hash"] != second["token_hash"]


def test_prefix_hash_changes_when_tokenizer_hash_changes() -> None:
    config = TinyTransformerConfig()
    tokenizer_a = TinyIntTokenizer(vocab_size=config.vocab_size)
    tokenizer_b = TinyIntTokenizer(
        vocab_size=config.vocab_size,
        tokenizer_version="phase4.v2",
    )

    first = build_prefix_profile([1, 2, 3], tokenizer_a, config)
    second = build_prefix_profile([1, 2, 3], tokenizer_b, config)

    assert first["prefix_hash"] != second["prefix_hash"]
    assert first["tokenizer_hash"] != second["tokenizer_hash"]


def test_generated_model_profile_includes_phase1_required_fields() -> None:
    config = TinyTransformerConfig()
    tokenizer = TinyIntTokenizer(vocab_size=config.vocab_size)
    model = TinyTransformer(config)

    profile = build_model_profile(model, tokenizer, config)

    assert set(profile) == {
        "model_id",
        "model_revision",
        "model_hash",
        "tokenizer_hash",
        "config_hash",
        "rope_config_hash",
        "quantization",
        "dtype",
        "num_layers",
        "num_attention_heads",
        "num_kv_heads",
        "head_dim",
        "max_position_embeddings",
    }
    assert profile["model_id"] == "bifrost_tiny_transformer"
    assert profile["dtype"] == "float32"


def test_generated_target_profile_validates_against_phase1_schema() -> None:
    config = TinyTransformerConfig()
    tokenizer = TinyIntTokenizer(vocab_size=config.vocab_size)
    model = TinyTransformer(config)

    target_profile = build_native_target_profile(
        model,
        tokenizer,
        config,
        [1, 2, 3, 4],
    )

    assert validate_target_profile_schema(target_profile) is None

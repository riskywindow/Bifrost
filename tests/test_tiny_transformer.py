from __future__ import annotations

import sys
from pathlib import Path

import torch

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "bifrost_py"))

from bifrost_model import TinyTransformer, TinyTransformerConfig


TOKENS = torch.tensor([3, 7, 11, 5, 2], dtype=torch.long)


def build_model(seed: int = 1234) -> TinyTransformer:
    model = TinyTransformer(TinyTransformerConfig(seed=seed))
    model.eval()
    return model


def test_model_initializes_deterministically() -> None:
    model_a = build_model(seed=1234)
    model_b = build_model(seed=1234)

    for param_a, param_b in zip(model_a.parameters(), model_b.parameters(), strict=True):
        assert torch.equal(param_a, param_b)


def test_same_seed_produces_same_logits() -> None:
    model_a = build_model(seed=1234)
    model_b = build_model(seed=1234)

    logits_a, _ = model_a(TOKENS)
    logits_b, _ = model_b(TOKENS)

    assert torch.equal(logits_a, logits_b)


def test_different_seed_changes_logits() -> None:
    model_a = build_model(seed=1234)
    model_b = build_model(seed=4321)

    logits_a, _ = model_a(TOKENS)
    logits_b, _ = model_b(TOKENS)

    assert not torch.allclose(logits_a, logits_b)


def test_prefill_returns_kv_for_each_layer_with_expected_shapes() -> None:
    model = build_model()

    _, past_key_values = model.prefill(TOKENS)

    assert len(past_key_values) == model.config.num_layers
    for key, value in past_key_values:
        assert key.shape == (
            len(TOKENS),
            model.config.num_kv_heads,
            model.config.head_dim,
        )
        assert value.shape == (
            len(TOKENS),
            model.config.num_kv_heads,
            model.config.head_dim,
        )
        assert key.dtype == torch.float32
        assert value.dtype == torch.float32


def test_decode_one_with_cache_extends_kv() -> None:
    model = build_model()
    _, past_key_values = model.prefill(TOKENS)

    logits, updated = model.decode_one(13, past_key_values)

    assert logits.shape == (model.config.vocab_size,)
    for key, value in updated:
        assert key.shape[0] == len(TOKENS) + 1
        assert value.shape[0] == len(TOKENS) + 1


def test_generate_greedy_is_deterministic() -> None:
    model_a = build_model(seed=1234)
    model_b = build_model(seed=1234)

    generated_a = model_a.generate_greedy(TOKENS, max_new_tokens=4)
    generated_b = model_b.generate_greedy(TOKENS, max_new_tokens=4)

    assert torch.equal(generated_a, generated_b)
    assert generated_a.shape == (len(TOKENS) + 4,)


def test_full_forward_and_prefill_decode_one_agree_for_next_token_step() -> None:
    model = build_model()
    next_token = torch.tensor([13], dtype=torch.long)

    full_logits, _ = model(torch.cat((TOKENS, next_token)))
    _, past_key_values = model.prefill(TOKENS)
    decoded_logits, _ = model.decode_one(int(next_token.item()), past_key_values)

    torch.testing.assert_close(
        decoded_logits,
        full_logits[-1],
        rtol=1e-5,
        atol=1e-6,
    )


def test_grouped_query_attention_is_explicitly_rejected() -> None:
    try:
        TinyTransformerConfig(num_heads=4, num_kv_heads=2, hidden_size=32)
    except ValueError as exc:
        assert "grouped-query attention is out of scope" in str(exc)
    else:
        raise AssertionError("expected grouped-query attention to be rejected")

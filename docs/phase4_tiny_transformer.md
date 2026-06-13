# Phase 4 Tiny Transformer Harness

Last verified: 2026-06-02

## Purpose

Phase 4 proves that BIFROST can handle real transformer KV cache tensors without
turning the project into a production model integration. The harness uses a
tiny locally defined transformer so tests can extract, serialize, store,
retrieve, rehydrate, and resume from actual `past_key_values` while staying
deterministic and CPU-friendly.

The goal is correctness, not model quality. The model only needs enough
attention structure to produce meaningful key/value tensors and logits that can
be compared against an uninterrupted baseline.

## Why a tiny local transformer

A tiny local transformer keeps Phase 4 focused on BIFROST's native KV page
contract:

1. It creates real key and value tensors rather than synthetic byte payloads.
2. It avoids model downloads, tokenizer downloads, cloud credentials, and
   internet-dependent tests.
3. It keeps CI CPU-only and fast.
4. It gives the project a controlled architecture where tensor shapes, dtypes,
   layer counts, RoPE or positional encoding, and token IDs are known exactly.
5. It makes failures debuggable because every weight, token, and expected logit
   is local and deterministic.

The harness is not intended to represent Hugging Face, LMCache, vLLM, or any
production serving stack.

## Model architecture

The reference Phase 4 model should be a small decoder-only transformer with:

1. Learned token embeddings.
2. Learned or deterministic positional handling.
3. Causal self-attention.
4. Explicit key/value cache output in `past_key_values`.
5. A small MLP block.
6. Layer normalization or another deterministic normalization used consistently
   by both baseline and rehydrated paths.
7. A final projection to logits.

Recommended default shape:

```text
vocab_size: 64
num_layers: 2
num_attention_heads: 2
num_kv_heads: 2
head_dim: 8
hidden_size: 16
mlp_hidden_size: 32
max_positions: 128
block_size_tokens: 4 or 8
dtype: float32 for required tests
```

The model should expose a stable interface:

```text
forward(input_ids, past_key_values=None, use_cache=True)
  -> logits, past_key_values
```

`past_key_values` must be explicit enough for BIFROST to map layer/block pages
without relying on framework-specific hidden state.

## Determinism requirements

All required Phase 4 tests must be deterministic:

1. Set a fixed random seed before weight initialization.
2. Prefer deterministic tensor initialization over random initialization when
   practical.
3. Run required tests on CPU.
4. Use `eval()` mode.
5. Disable dropout and any nondeterministic sampling.
6. Use integer token IDs supplied by fixtures.
7. Use greedy decoding for continuation comparisons.
8. Record exact model configuration and hashing inputs in test fixtures.

Floating-point comparisons must account for normal CPU math behavior but should
be strict enough to catch wrong KV state. The required path is `float32`.
Optional `float16` coverage may exist, but it must be skipped when the CPU or
runtime does not support it reliably.

## CPU-only CI constraints

Default tests and demos must run without:

1. GPU hardware.
2. CUDA or custom kernels.
3. Network access.
4. External model or tokenizer downloads.
5. Docker, Kubernetes, or cloud credentials.
6. Root permissions.

The tiny model should keep tensor sizes small enough that full extract, store,
retrieve, rehydrate, and compare tests can run as unit or small integration
tests. ContextStorm model scenarios should remain local smoke tests unless a
maintainer explicitly opts into larger exploratory runs.

## Out of scope

Phase 4 must not implement:

1. LMCache integration.
2. vLLM integration.
3. Hugging Face model downloads.
4. External tokenizer integration.
5. Production model support.
6. GPU-required inference.
7. Custom CUDA.
8. Compression.
9. QUIC.
10. RDMA.
11. Dashboards.
12. Scheduler logic.

GPU demos may be added only as optional local experiments. They must be skipped
by default and must not be part of CI or correctness acceptance.

# Phase 4 KV Layout

Last verified: 2026-06-14

## Purpose

This document defines how the Phase 4 tiny transformer maps internal
`past_key_values` tensors to Phase 1 `native_kv_page` objects. The mapping is
part of the correctness harness. It does not redefine BIFROST object identity,
store state, or production engine behavior.

## Internal past_key_values layout

The tiny transformer represents cached KV state as an ordered list with one
entry per decoder layer:

```text
past_key_values[layer_id] = (key, value)
key.shape   = [token_count, num_kv_heads, head_dim]
value.shape = [token_count, num_kv_heads, head_dim]
```

Required Phase 4 tests are batch-free at the KV-cache boundary. Model input may
be 1-D token IDs or batch size 1, but cached tensors must use the strict
`[token_count, num_kv_heads, head_dim]` layout. If later tests add larger batch
sizes, the batch dimension must become an explicit compatibility field and
must not be silently flattened into object identity.

Layer order is canonical by ascending `layer_id`. Token positions are absolute
decoder positions, starting at zero for the first token in the deterministic
input fixture.

## Native BIFROST page layout

Every KV page generated from the model must be a Phase 1 `native_kv_page`.

The payload bytes for a tiny-transformer native page should store one layer and
one token block with key and value tensors stacked in this canonical order:

```text
payload tensor shape: [2, block_tokens, num_kv_heads, head_dim]
payload[0] = key[token_start:token_end, :, :]
payload[1] = value[token_start:token_end, :, :]
tensor_layout = "kv_token_head_dim"
tensor_role = "kv_pair"
```

This produces the descriptor tensor shape:

```text
[2, block_tokens, num_kv_heads, head_dim]
```

`block_tokens` is normally `block_size_tokens`. The final block may be shorter
only if the descriptor records the exact `token_range`, exact tensor shape, and
exact byte length. Tests should include at least one non-empty final block case.

## Layer and block mapping

The mapping from model cache to BIFROST object coordinates is:

```text
layer_id:
  decoder layer index, 0-based

kv_block_id:
  floor(token_start / block_size_tokens)

token_range:
  [token_start, token_end)

absolute_position_range:
  [token_start, token_end)
```

For a full prefix rehydration, the required object set is:

```text
required = {
  (layer_id, kv_block_id)
  for layer_id in 0..num_layers-1
  for kv_block_id in blocks_covering(prefix_token_count)
}
```

The harness must not rehydrate unless all required pages are verified and
compatible, or unless a smaller valid prefix boundary is explicitly requested
and tested.

## Dtype handling

Required Phase 4 correctness tests use `float32`.

Descriptor fields must record:

```text
model_profile.dtype
tensor_profile.tensor_dtype
tensor_profile.byte_length
tensor_profile.tensor_shape
```

Payload bytes must be encoded in a deterministic byte order. The required
encoding is little-endian contiguous tensor bytes for the descriptor dtype.

Optional `float16` tests may be added as opt-in coverage. They must:

1. Use explicit `float16` descriptor fields.
2. Avoid implicit upcast or downcast during serialization.
3. Use looser logit tolerances than `float32`.
4. Be skipped by default when CPU support is unreliable.

Dtype mismatch must fail closed. A `float32` target profile must not accept a
`float16` page, and a `float16` page must not be loaded as `float32` without a
new object descriptor and validation path.

## Prefix hash and token hash construction

Phase 4 uses integer token IDs, not external tokenizers. The tokenizer identity
is still explicit so native KV pages bind to the exact token interpretation used
by the harness.

Recommended tokenizer fields:

```text
tokenizer_name = "bifrost_integer_tokens"
tokenizer_version = "phase4.v1"
vocab_size = 64
tokenizer_hash = sha256(canonical tokenizer config)
```

Token IDs are encoded canonically as little-endian unsigned 32-bit integers.
The token hash commits to the exact token sequence represented by a descriptor:

```text
token_hash = blake3(
  "bifrost.phase4.tokens.v1" ||
  u32_le(token_count) ||
  u32_le(token_id[0]) ||
  ... ||
  u32_le(token_id[n-1])
)
```

The prefix hash commits to token identity, tokenizer identity, positional
identity, and the exact absolute position range:

```text
prefix_hash = blake3(
  "bifrost.prefix.v1" ||
  tokenizer_hash ||
  rope_config_hash ||
  token_hash ||
  u64_le(absolute_position_start) ||
  u64_le(absolute_position_end) ||
  canonical_mm_hashes
)
```

For the tiny transformer, `canonical_mm_hashes` is an empty list commitment.
If the model uses learned absolute position embeddings rather than RoPE, the
`rope_config_hash` field should still carry a deterministic positional config
hash with a name such as `position_config_hash`; the descriptor schema field
remains `rope_config_hash` until a schema migration explicitly changes it.

## Target profile generation

The Phase 4 target profile describes the exact tiny model and harness engine
that may consume generated pages:

```json
{
  "schema_version": "bifrost.target_profile.v1alpha1",
  "object_type": "native_kv_page",
  "model_profile": {
    "model_id": "bifrost_tiny_transformer",
    "model_revision": "phase4.v1",
    "model_hash": "blake3:...",
    "tokenizer_hash": "sha256:...",
    "config_hash": "sha256:...",
    "rope_config_hash": "sha256:...",
    "quantization": "none",
    "dtype": "float32",
    "num_layers": 2,
    "num_attention_heads": 2,
    "num_kv_heads": 2,
    "head_dim": 8,
    "max_position_embeddings": 128
  },
  "engine_profile": {
    "engine_name": "tiny_transformer",
    "engine_version": "phase4.v1",
    "integration_name": "bifrost_tiny_harness",
    "integration_version": "phase4.v1",
    "attention_impl": "eager_causal_attention",
    "kv_layout": "kv_token_head_dim",
    "block_size_tokens": 4,
    "kv_cache_format": "bifrost_native_v1"
  }
}
```

The target profile is compatibility input, not mutable store state. It must be
generated deterministically from local model, tokenizer, positional, dtype, and
layout configuration.

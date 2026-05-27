# Validation Errors

Last verified: 2026-05-27

## Purpose

Phase 1 validators return stable reason codes. These codes are part of the fixture and CLI contract, so they should remain stable after introduction.

Validators should fail closed. If multiple problems exist, return the first deterministic, most specific reason code according to the validator's documented order.

## Stable reason codes

```text
accepted
parse_error
schema_validation_failed
unknown_schema_version
unknown_object_type
missing_required_field
extra_field_rejected
invalid_field_type
byte_length_mismatch
payload_hash_mismatch
descriptor_hash_mismatch
object_id_mismatch
wrong_model_hash
wrong_tokenizer_hash
wrong_config_hash
wrong_rope_hash
wrong_dtype
wrong_num_layers
wrong_num_kv_heads
wrong_head_dim
wrong_engine_name
wrong_engine_version
wrong_attention_impl
wrong_kv_layout
wrong_block_size_tokens
wrong_kv_cache_format
wrong_prefix_hash
wrong_token_range
wrong_absolute_position_range
invalid_layer_id
invalid_kv_block_id
invalid_block_token_count
invalid_tensor_shape
invalid_tensor_dtype
invalid_tensor_layout
opaque_wrong_engine_key
opaque_wrong_engine_name
opaque_wrong_integration_name
opaque_payload_not_interpretable
unsupported_compression
unsupported_payload_encoding
```

## Code meanings

`accepted`: descriptor, payload, identity, and requested compatibility profile all validated.

`parse_error`: descriptor bytes could not be parsed as valid JSON.

`schema_validation_failed`: descriptor parsed, but failed a general schema rule not covered by a more specific reason code.

`unknown_schema_version`: `schema_version` is absent from the supported version set.

`unknown_object_type`: `object_type` is absent from the supported object type set.

`missing_required_field`: a required field is absent.

`extra_field_rejected`: a field is present but not allowed by the schema.

`invalid_field_type`: a field has the wrong JSON type.

`byte_length_mismatch`: descriptor byte length does not match payload length.

`payload_hash_mismatch`: recomputed payload hash does not match the descriptor.

`descriptor_hash_mismatch`: recomputed descriptor hash does not match the descriptor.

`object_id_mismatch`: recomputed object ID does not match the descriptor.

`wrong_model_hash`: object model hash differs from the target compatibility profile.

`wrong_tokenizer_hash`: object tokenizer hash differs from the target compatibility profile.

`wrong_config_hash`: object model config hash differs from the target compatibility profile.

`wrong_rope_hash`: object RoPE configuration hash differs from the target compatibility profile.

`wrong_dtype`: object dtype differs from the target compatibility profile.

`wrong_num_layers`: object layer count differs from the target compatibility profile.

`wrong_num_kv_heads`: object KV head count differs from the target compatibility profile.

`wrong_head_dim`: object head dimension differs from the target compatibility profile.

`wrong_engine_name`: object engine name differs from the target compatibility profile.

`wrong_engine_version`: object engine version differs from the target compatibility profile.

`wrong_attention_impl`: object attention implementation differs from the target compatibility profile.

`wrong_kv_layout`: object KV layout differs from the target compatibility profile.

`wrong_block_size_tokens`: object block size differs from the target compatibility profile.

`wrong_kv_cache_format`: object KV cache format differs from the target compatibility profile.

`wrong_prefix_hash`: object prefix hash differs from the requested prefix.

`wrong_token_range`: object token range is malformed or differs from the requested range.

`wrong_absolute_position_range`: object absolute position range is malformed or differs from the requested range.

`invalid_layer_id`: layer id is outside the valid model layer range.

`invalid_kv_block_id`: KV block id is malformed or outside accepted bounds.

`invalid_block_token_count`: block token count is invalid for the token range or block size.

`invalid_tensor_shape`: tensor shape is malformed or inconsistent with descriptor fields.

`invalid_tensor_dtype`: tensor dtype is unsupported or inconsistent with descriptor fields.

`invalid_tensor_layout`: tensor layout is unsupported or inconsistent with descriptor fields.

`opaque_wrong_engine_key`: opaque object engine key commitment differs from the requested engine key.

`opaque_wrong_engine_name`: opaque object engine name differs from the target compatibility profile.

`opaque_wrong_integration_name`: opaque object integration name differs from the target compatibility profile.

`opaque_payload_not_interpretable`: validator was asked to interpret opaque bytes as native tensor data.

`unsupported_compression`: descriptor requests a compression mode unsupported by Phase 1.

`unsupported_payload_encoding`: descriptor requests a payload encoding unsupported by Phase 1.

## Stability rules

Do not rename reason codes casually. If a code must be replaced, update:

1. This document.
2. Python constants.
3. Rust mirror constants.
4. Fixtures.
5. CLI output tests.
6. Cross-language parity tests.

Adding a new code is allowed when it makes rejection more specific and existing fixture expectations are updated intentionally.

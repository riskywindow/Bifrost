# BIFROST Correctness Contract

Last verified: 2026-05-27

## Purpose

This document defines the non-negotiable correctness rules for BIFROST. Performance is only meaningful if BIFROST never injects incompatible, corrupted, incomplete, stale, or misidentified KV-cache state into an inference engine.

The core rule is simple:

> BIFROST may miss a valid cache opportunity, but it must never serve an invalid KV object as valid.

## Threat model for correctness

BIFROST is not Phase 0 scoped as a security boundary against malicious operators. It is, however, scoped to defend against normal distributed-systems and integration failures:

1. Partial writes.
2. Torn objects after daemon crash.
3. Corrupted chunks in transfer or storage.
4. Wrong model or tokenizer metadata.
5. Wrong RoPE or positional encoding metadata.
6. Wrong dtype or tensor layout.
7. Missing KV layers or missing token blocks.
8. Prefix mismatch.
9. Stale metadata index entries.
10. Transfer replay of an object under the wrong key.
11. Version mismatch between integration layers.

## Terminology

### KV object

A BIFROST-managed serialized unit of KV-cache state. A KV object may represent one KV page, one layer/block pair, or an engine-owned memory object. The first release supports both:

```text
native_kv_page:
  BIFROST understands the model compatibility metadata and tensor layout.

opaque_engine_blob:
  BIFROST stores bytes supplied by an integration such as LMCache and wraps them with integrity and provenance metadata.
```

### Engine profile

The compatibility description of the inference engine and model configuration that intends to consume the KV object.

### Prefix hash

A hash commitment to the exact token prefix represented by the KV object, including tokenizer identity and position information.

### Verified object

An object whose metadata and payload hashes have been checked and whose compatibility fields match the target engine profile.

## Invariant 1: incompatible metadata must fail closed

A KV object must be rejected if any required compatibility field is missing or mismatched.

Required compatibility fields for native KV pages:

```text
schema_version
object_type
model_id
model_revision_or_hash
tokenizer_hash
rope_config_hash
attention_impl
kv_layout
dtype
num_layers
num_kv_heads
head_dim
block_size_tokens
layer_id
kv_block_id
token_range
prefix_hash
tensor_shape
tensor_hash
```

If the engine cannot provide a field, BIFROST must mark the object as opaque or unsupported. It must not invent compatibility.

## Invariant 2: object identity binds metadata and payload

Object identity must include both metadata and payload commitments.

The canonical object ID is:

```text
object_id = blake3(canonical_metadata_without_object_id || tensor_hash)
```

A suggested user-facing key is:

```text
kv://bifrost/v1/{model_hash}/{prefix_hash}/{layout_hash}/{layer_id}/{kv_block_id}/{tensor_hash}
```

The key alone is not trusted. The metadata and payload must still verify.

## Invariant 3: no partial object may be served

All writes use a two-phase local commit:

```text
PUT_STARTED:
  write to temporary path
  record intent in index

PUT_COMMITTED:
  fsync payload
  fsync metadata
  atomically rename into object path
  record committed state

VERIFIED:
  re-read metadata and payload hash
  mark object as servable
```

A daemon restart must scan temporary paths and remove or quarantine incomplete writes.

Objects in these states are not servable:

```text
PUT_STARTED
PUT_ABORTED
PARTIAL
CORRUPT
UNKNOWN_VERSION
QUARANTINED
```

Only these states may be served:

```text
VERIFIED
PINNED
```

## Invariant 4: hash verification is mandatory before load

BIFROST must verify:

1. Chunk hash during transfer.
2. Full payload hash after reassembly.
3. Metadata hash or canonical object ID before index commit.
4. Optional hash tree root for large objects.

Hash mismatch behavior:

```text
transfer chunk mismatch:
  drop chunk and retry

full object mismatch:
  mark object CORRUPT
  reject get
  optionally request recompute

metadata mismatch:
  reject object
  remove index entry if stale
```

## Invariant 5: prefix binding is mandatory for native KV reuse

A native KV object may only be used for a request if its prefix hash corresponds to the same token sequence, tokenizer, positional scheme, and prefix length.

Prefix hash input:

```text
prefix_hash = blake3(
  tokenizer_hash ||
  rope_config_hash ||
  token_ids_bytes ||
  absolute_position_start ||
  absolute_position_end ||
  optional_mm_hashes
)
```

If token IDs are truncated or normalized, the exact transformation must be included in the hash input.

## Invariant 6: layer completeness must be proven before session rehydration

For a full rehydration event, BIFROST must prove that all required KV blocks are present for all required layers.

Required set:

```text
required = {
  (layer_id, kv_block_id)
  for layer_id in 0..num_layers-1
  for kv_block_id in needed_blocks
}
```

Servable only if:

```text
required subset actual_verified_objects
```

If the set is incomplete, the integration must either recompute missing blocks or report a cache miss. It must not inject a partial session unless the engine explicitly supports partial prefix fill and BIFROST can identify the exact valid prefix boundary.

## Invariant 7: opaque engine blobs are never reinterpreted by BIFROST

When LMCache or another engine supplies opaque bytes, BIFROST may store, transfer, hash, and return them. BIFROST must not reinterpret their internal tensor layout unless the integration explicitly exposes that layout and tests cover it.

Opaque object compatibility fields:

```text
object_type: opaque_engine_blob
engine_name
engine_version
integration_name
integration_version
engine_key_hash
payload_hash
created_by
```

Opaque objects can still be protected against corruption and partial writes.

## Invariant 8: API semantics are deterministic

For a given object key and target profile:

```text
HAS returns true only if object exists and is verified.
GET returns bytes only if HAS would return true under the same profile.
PUT returns success only after the object is committed or queued according to explicit durability mode.
LIST is informational and not sufficient for correctness.
```

Suggested API behavior:

```text
HAS(key, profile) -> true | false | incompatible(reason)
GET(key, profile) -> object | miss | incompatible(reason) | corrupt(reason)
PUT(key, bytes, metadata) -> committed | rejected(reason)
```

## Invariant 9: all side effects are observable

Every object state transition emits an event:

```text
object_put_started
object_put_committed
object_verified
object_get_started
object_get_served
object_get_rejected
object_corrupt_detected
object_evicted
object_pinned
path_degraded
path_dead
transfer_retried
```

Events must include:

```text
timestamp
object_id
request_id
session_id if available
path_id if applicable
state_before
state_after
reason
```

## Invariant 10: benchmark claims must report correctness outcomes

Every benchmark report must include:

```text
incorrect_cache_reuse_count
corrupt_object_detected_count
compatibility_rejection_count
partial_write_recovered_count
hash_mismatch_count
```

A performance run with unreported correctness metrics is invalid.

## Compatibility algorithm

Pseudocode:

```python
def compatible(obj_meta, target_profile):
    required = [
        "schema_version",
        "object_type",
        "model_hash",
        "tokenizer_hash",
        "rope_config_hash",
        "attention_impl",
        "kv_layout",
        "dtype",
        "num_layers",
        "num_kv_heads",
        "head_dim",
        "block_size_tokens",
    ]

    for field in required:
        if field not in obj_meta:
            return Reject(f"missing {field}")
        if obj_meta[field] != target_profile[field]:
            return Reject(f"mismatch {field}")

    if not valid_token_range(obj_meta):
        return Reject("invalid token range")

    if not verify_metadata_hash(obj_meta):
        return Reject("metadata hash mismatch")

    if not verify_payload_hash(obj_meta):
        return Reject("payload hash mismatch")

    return Accept()
```

## Tiny-transformer correctness criteria

The tiny-transformer harness must include three modes.

### Exact mode

Use deterministic settings and compare logits after KV roundtrip.

Success:

```text
max_abs_error <= 1e-5 for fp32
or documented tighter/looser threshold based on dtype
```

### Tolerance mode

Use fp16 or bf16 and compare distributions.

Success:

```text
KL(next_token_distribution_baseline, next_token_distribution_bifrost) <= configured_threshold
```

### Greedy behavior mode

Use greedy decoding.

Success:

```text
generated token IDs are identical for N continuation tokens
```

## Failure behavior table

| Failure | Expected BIFROST behavior |
|---|---|
| Missing metadata field | Reject as incompatible |
| Wrong tokenizer hash | Reject as incompatible |
| Wrong RoPE config | Reject as incompatible |
| Wrong model hash | Reject as incompatible |
| Payload hash mismatch | Mark corrupt and reject |
| Chunk hash mismatch | Retry or reconstruct if parity available |
| Daemon crash during PUT | Remove or quarantine partial object on restart |
| Index says object exists but file missing | Repair index and return miss |
| File exists but metadata missing | Quarantine and return miss |
| Partial layer set for full rehydration | Return miss or partial-prefix response only if supported |
| Unknown schema version | Reject unless explicit compatibility handler exists |

## Minimum test suite

```text
test_valid_object_served
test_missing_required_field_rejected
test_wrong_model_hash_rejected
test_wrong_tokenizer_hash_rejected
test_wrong_rope_hash_rejected
test_wrong_dtype_rejected
test_hash_mismatch_rejected
test_partial_write_not_served
test_restart_cleans_tmp_put
test_index_file_mismatch_repaired
test_full_layer_set_required_for_rehydrate
test_opaque_blob_roundtrip_hash_verified
test_tiny_transformer_logits_match_after_roundtrip
```

## Acceptance criteria

Phase 1 coding may begin only when this document is treated as binding.

The first implementation milestone passes only when:

```text
[ ] validator rejects every incompatible object fixture
[ ] validator accepts a known-good object fixture
[ ] object IDs are reproducible across runs
[ ] partial write simulation never serves an object
[ ] corruption simulation is detected
[ ] benchmark output includes correctness counters
```

## Sources reviewed

- LMCache remote storage plugin interface: https://docs.lmcache.ai/developer_guide/extending_lmcache/remote_storage_plugins.html
- LMCache integration guide: https://docs.lmcache.ai/developer_guide/integration.html
- vLLM Production Stack KV cache sharing: https://docs.vllm.ai/projects/production-stack/en/latest/use_cases/sharing-kv-cache.html

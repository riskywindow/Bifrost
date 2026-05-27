# BIFROST KV Object Format

Last verified: 2026-05-27

## Purpose

This document specifies the BIFROST KV object format for Phase 1 and later implementation. The format is designed to make KV-cache state portable without making cache reuse unsafe.

A BIFROST KV object has two parts:

```text
metadata envelope:
  structured JSON, MessagePack, or CBOR metadata

payload:
  serialized KV tensor bytes or opaque engine-owned bytes
```

The object is valid only if the metadata and payload hashes verify and the metadata is compatible with the target engine profile.

## Object classes

BIFROST supports two object classes in the initial design.

### native_kv_page

A KV page whose tensor layout and compatibility fields are known to BIFROST.

Use this for:

```text
- tiny-transformer correctness harness
- future direct vLLM connector experiments
- any integration that exposes tensor layout explicitly
```

### opaque_engine_blob

A byte object supplied by an integration, such as an LMCache MemoryObj, whose internal layout BIFROST does not interpret.

Use this for:

```text
- first LMCache remote storage plugin
- engine-specific KV chunks
- compatibility-preserving byte storage
```

BIFROST can still verify, store, transfer, cache, and reject corrupted opaque objects.

## Metadata envelope

The metadata envelope should be encoded in a canonical form before hashing. JSON is acceptable for v0 if canonicalization is enforced. MessagePack or CBOR is preferable later.

### Top-level fields

```json
{
  "schema_version": "bifrost.kv_object.v1alpha1",
  "object_type": "native_kv_page",
  "object_id": "blake3:...",
  "created_at_unix_ms": 1770000000000,
  "created_by": "bifrostd/0.1.0",
  "model_profile": {},
  "engine_profile": {},
  "prefix_profile": {},
  "tensor_profile": {},
  "storage_profile": {},
  "integrity": {},
  "provenance": {}
}
```

## model_profile

```json
{
  "model_id": "Qwen/Qwen2.5-7B-Instruct-AWQ",
  "model_revision": "main",
  "model_hash": "sha256:...",
  "tokenizer_hash": "sha256:...",
  "config_hash": "sha256:...",
  "rope_config_hash": "sha256:...",
  "quantization": "awq",
  "dtype": "float16",
  "num_layers": 32,
  "num_attention_heads": 32,
  "num_kv_heads": 32,
  "head_dim": 128,
  "max_position_embeddings": 131072
}
```

### Required for native KV pages

```text
model_hash
tokenizer_hash
rope_config_hash
dtype
num_layers
num_kv_heads
head_dim
```

### Optional for opaque blobs

Opaque blobs should still include model and engine information when available, but BIFROST does not treat missing tensor layout fields as native compatibility.

## engine_profile

```json
{
  "engine_name": "tiny_transformer",
  "engine_version": "0.1.0",
  "integration_name": "bifrost_tiny_harness",
  "integration_version": "0.1.0",
  "attention_impl": "eager_attention",
  "kv_layout": "layer_block_kv_head_dim",
  "block_size_tokens": 256,
  "kv_cache_format": "bifrost_native_v1"
}
```

For LMCache opaque storage:

```json
{
  "engine_name": "lmcache",
  "engine_version": "pinned_version_here",
  "integration_name": "lmcache_bifrost_remote_storage",
  "integration_version": "0.1.0",
  "kv_cache_format": "opaque_lmcache_memory_obj"
}
```

## prefix_profile

```json
{
  "token_count": 8192,
  "token_range": [0, 8192],
  "absolute_position_range": [0, 8192],
  "prefix_hash": "blake3:...",
  "tokenizer_hash": "sha256:...",
  "token_hash": "blake3:...",
  "mm_hashes": [],
  "system_prompt_hash": "blake3:optional"
}
```

### Prefix hash construction

Native prefix hash:

```text
prefix_hash = blake3(
  "bifrost.prefix.v1" ||
  tokenizer_hash ||
  rope_config_hash ||
  canonical_token_ids ||
  absolute_position_range ||
  canonical_mm_hashes
)
```

For opaque LMCache objects, BIFROST may use the LMCache key hash as the engine key commitment:

```text
engine_key_hash = blake3(canonical_lmcache_key_repr)
```

## tensor_profile

Native KV page example:

```json
{
  "layer_id": 17,
  "kv_block_id": 4921,
  "block_size_tokens": 256,
  "token_range": [8192, 8448],
  "tensor_role": "kv_pair",
  "tensor_shape": [2, 256, 32, 128],
  "tensor_dtype": "float16",
  "tensor_layout": "kv_token_head_dim",
  "byte_length": 16777216,
  "compression": "none"
}
```

Opaque engine blob example:

```json
{
  "engine_key_hash": "blake3:...",
  "byte_length": 52428800,
  "compression": "none",
  "engine_payload_type": "lmcache.MemoryObj"
}
```

## storage_profile

```json
{
  "tier": "disk",
  "cache_policy": "size_aware_lru",
  "pinned": false,
  "expires_at_unix_ms": null,
  "last_accessed_unix_ms": 1770000001000,
  "write_state": "VERIFIED"
}
```

Valid write states:

```text
PUT_STARTED
PUT_COMMITTED
VERIFIED
PINNED
EVICTABLE
EVICTED
CORRUPT
QUARANTINED
```

Only VERIFIED and PINNED are servable.

## integrity

```json
{
  "metadata_hash": "blake3:...",
  "payload_hash": "blake3:...",
  "object_id_algorithm": "blake3(canonical_metadata_without_object_id || payload_hash)",
  "chunk_size_bytes": 262144,
  "chunk_hashes": [
    "blake3:...",
    "blake3:..."
  ],
  "hash_tree_root": "blake3:optional",
  "fec": {
    "enabled": false,
    "scheme": null,
    "data_chunks": null,
    "parity_chunks": null
  }
}
```

## provenance

```json
{
  "source_worker_id": "gpu-worker-a",
  "source_session_id": "session_42",
  "source_request_id": "req_abc123",
  "source_engine": "vllm+lmcache",
  "source_commit": "gitsha:optional",
  "benchmark_run_id": "contextstorm_run_001",
  "notes": []
}
```

## Canonical object ID

Object ID generation:

```python
def canonical_object_id(metadata_without_object_id, payload_hash):
    canonical_meta = canonical_encode(metadata_without_object_id)
    return "blake3:" + blake3(canonical_meta + payload_hash.encode()).hexdigest()
```

The object ID is a commitment to both metadata and payload. A path or key that does not match the recomputed ID is not trusted.

## Recommended object key formats

Native KV page:

```text
kv://bifrost/v1/native/{model_hash}/{prefix_hash}/{layer_id}/{kv_block_id}/{payload_hash}
```

Opaque engine blob:

```text
kv://bifrost/v1/opaque/{engine_name}/{engine_key_hash}/{payload_hash}
```

Short storage path:

```text
objects/
  ab/
    cd/
      abcdef...meta.json
      abcdef...payload.bin
```

where `abcdef...` is derived from object_id.

## Compatibility validation

Native validation requires exact matches on:

```text
schema_version
object_type
model_hash
tokenizer_hash
rope_config_hash
dtype
engine_name if engine-specific
attention_impl
kv_layout
block_size_tokens
num_layers
num_kv_heads
head_dim
```

Then it checks:

```text
payload_hash matches payload
tensor_shape matches metadata
token_range is consistent with block id and block size
layer_id is within range
prefix_hash matches request prefix
```

Opaque validation requires:

```text
schema_version accepted
object_type == opaque_engine_blob
payload_hash matches payload
engine_key_hash matches requested key
engine_name and integration_name are accepted by caller
```

## Serialization choices

### Phase 1

Use:

```text
metadata: canonical JSON
payload: raw bytes
hash: BLAKE3
index: SQLite
```

Reason: simple, readable, fast enough, easy to test.

### Phase 2 and beyond

Consider:

```text
metadata: MessagePack or CBOR
payload: safetensors for native tensors
hash tree for large objects
optional zstd compression
optional FEC metadata
```

## Example native metadata

```json
{
  "schema_version": "bifrost.kv_object.v1alpha1",
  "object_type": "native_kv_page",
  "object_id": "blake3:OBJECT_ID_PLACEHOLDER",
  "created_at_unix_ms": 1770000000000,
  "created_by": "bifrostd/0.1.0",
  "model_profile": {
    "model_id": "tiny-gpt",
    "model_revision": "local",
    "model_hash": "sha256:MODEL_HASH",
    "tokenizer_hash": "sha256:TOKENIZER_HASH",
    "config_hash": "sha256:CONFIG_HASH",
    "rope_config_hash": "sha256:ROPE_HASH",
    "quantization": "none",
    "dtype": "float16",
    "num_layers": 12,
    "num_attention_heads": 12,
    "num_kv_heads": 12,
    "head_dim": 64,
    "max_position_embeddings": 8192
  },
  "engine_profile": {
    "engine_name": "tiny_transformer",
    "engine_version": "0.1.0",
    "integration_name": "bifrost_tiny_harness",
    "integration_version": "0.1.0",
    "attention_impl": "eager_attention",
    "kv_layout": "layer_block_kv_head_dim",
    "block_size_tokens": 256,
    "kv_cache_format": "bifrost_native_v1"
  },
  "prefix_profile": {
    "token_count": 8192,
    "token_range": [0, 8192],
    "absolute_position_range": [0, 8192],
    "prefix_hash": "blake3:PREFIX_HASH",
    "tokenizer_hash": "sha256:TOKENIZER_HASH",
    "token_hash": "blake3:TOKEN_HASH",
    "mm_hashes": []
  },
  "tensor_profile": {
    "layer_id": 0,
    "kv_block_id": 0,
    "block_size_tokens": 256,
    "token_range": [0, 256],
    "tensor_role": "kv_pair",
    "tensor_shape": [2, 256, 12, 64],
    "tensor_dtype": "float16",
    "tensor_layout": "kv_token_head_dim",
    "byte_length": 786432,
    "compression": "none"
  },
  "storage_profile": {
    "tier": "disk",
    "cache_policy": "size_aware_lru",
    "pinned": false,
    "expires_at_unix_ms": null,
    "last_accessed_unix_ms": 1770000001000,
    "write_state": "VERIFIED"
  },
  "integrity": {
    "metadata_hash": "blake3:METADATA_HASH",
    "payload_hash": "blake3:PAYLOAD_HASH",
    "object_id_algorithm": "blake3(canonical_metadata_without_object_id || payload_hash)",
    "chunk_size_bytes": 262144,
    "chunk_hashes": [],
    "hash_tree_root": null,
    "fec": {
      "enabled": false,
      "scheme": null,
      "data_chunks": null,
      "parity_chunks": null
    }
  },
  "provenance": {
    "source_worker_id": "gpu-worker-a",
    "source_session_id": "session_42",
    "source_request_id": "req_abc123",
    "source_engine": "tiny_transformer",
    "source_commit": null,
    "benchmark_run_id": null,
    "notes": []
  }
}
```

## Example opaque LMCache metadata

```json
{
  "schema_version": "bifrost.kv_object.v1alpha1",
  "object_type": "opaque_engine_blob",
  "object_id": "blake3:OBJECT_ID_PLACEHOLDER",
  "created_at_unix_ms": 1770000000000,
  "created_by": "bifrostd/0.1.0",
  "model_profile": {
    "model_id": "provided_by_lmcache_or_unknown",
    "model_revision": null,
    "model_hash": null,
    "tokenizer_hash": null,
    "config_hash": null,
    "rope_config_hash": null,
    "quantization": null,
    "dtype": null,
    "num_layers": null,
    "num_attention_heads": null,
    "num_kv_heads": null,
    "head_dim": null,
    "max_position_embeddings": null
  },
  "engine_profile": {
    "engine_name": "lmcache",
    "engine_version": "PINNED_LMCACHE_VERSION",
    "integration_name": "lmcache_bifrost_remote_storage",
    "integration_version": "0.1.0",
    "attention_impl": null,
    "kv_layout": "opaque",
    "block_size_tokens": null,
    "kv_cache_format": "opaque_lmcache_memory_obj"
  },
  "prefix_profile": {
    "token_count": null,
    "token_range": null,
    "absolute_position_range": null,
    "prefix_hash": null,
    "tokenizer_hash": null,
    "token_hash": null,
    "mm_hashes": [],
    "engine_key_hash": "blake3:ENGINE_KEY_HASH"
  },
  "tensor_profile": {
    "engine_key_hash": "blake3:ENGINE_KEY_HASH",
    "byte_length": 52428800,
    "compression": "none",
    "engine_payload_type": "lmcache.MemoryObj"
  },
  "storage_profile": {
    "tier": "disk",
    "cache_policy": "size_aware_lru",
    "pinned": false,
    "expires_at_unix_ms": null,
    "last_accessed_unix_ms": 1770000001000,
    "write_state": "VERIFIED"
  },
  "integrity": {
    "metadata_hash": "blake3:METADATA_HASH",
    "payload_hash": "blake3:PAYLOAD_HASH",
    "object_id_algorithm": "blake3(canonical_metadata_without_object_id || payload_hash)",
    "chunk_size_bytes": 262144,
    "chunk_hashes": [],
    "hash_tree_root": null,
    "fec": {
      "enabled": false,
      "scheme": null,
      "data_chunks": null,
      "parity_chunks": null
    }
  },
  "provenance": {
    "source_worker_id": "lmcache-worker-a",
    "source_session_id": null,
    "source_request_id": null,
    "source_engine": "lmcache",
    "source_commit": null,
    "benchmark_run_id": null,
    "notes": ["opaque payload, not interpreted by BIFROST"]
  }
}
```

## Validation CLI contract

```bash
bifrost kv validate object.meta.json --payload object.payload.bin --profile target_profile.json
```

Output examples:

```text
VALID object_id=blake3:abc tier=disk type=native_kv_page
```

```text
INVALID reason=tokenizer_hash_mismatch expected=sha256:aaa actual=sha256:bbb
```

```text
CORRUPT reason=payload_hash_mismatch expected=blake3:aaa actual=blake3:bbb
```

## Phase 1 acceptance tests

```text
[ ] canonical metadata encoding is deterministic
[ ] object_id is deterministic across processes
[ ] native metadata validates against matching profile
[ ] native metadata rejects model mismatch
[ ] native metadata rejects tokenizer mismatch
[ ] native metadata rejects rope mismatch
[ ] native metadata rejects dtype mismatch
[ ] opaque metadata roundtrips without reinterpretation
[ ] hash mismatch is detected
[ ] unknown schema version is rejected
```

## Sources reviewed

- LMCache overview: https://docs.lmcache.ai/
- LMCache integration guide: https://docs.lmcache.ai/developer_guide/integration.html
- LMCache remote storage plugins: https://docs.lmcache.ai/developer_guide/extending_lmcache/remote_storage_plugins.html
- vLLM KV transfer config: https://docs.vllm.ai/en/v0.10.2/api/vllm/config/kv_transfer.html

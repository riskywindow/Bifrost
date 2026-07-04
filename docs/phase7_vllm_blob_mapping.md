# Phase 7 vLLM Blob Mapping

Last verified: 2026-07-04

## Purpose

This document defines how vLLM-owned KVTransfer state maps into BIFROST
`opaque_engine_blob` objects.

The core rule is:

```text
vLLM owns KV semantics.
BIFROST owns opaque object identity, integrity, storage, and retrieval.
```

BIFROST must not reinterpret vLLM tensor layout, key/value ordering, block
table semantics, scheduler state, dtype meaning, or attention backend details
beyond opaque compatibility fields.

## vLLM opaque key material

The connector needs deterministic opaque key material for lookup. The key
material should be built from stable vLLM-provided or connector-captured
fields:

1. Engine namespace.
2. Integration namespace.
3. `layout_fingerprint`.
4. Model/config commitment when available.
5. Request or prefix identity supplied by vLLM scheduler metadata.
6. Layer identity.
7. KV block identity.
8. Blob role, such as layer block, request layer, or connector-specific opaque
   unit.

Do not use Python object identity, memory addresses, process IDs, port
numbers, benchmark run IDs, staging paths, retry counters, local store paths,
or noncanonical `repr` output as hash inputs.

The opaque key hash should be namespaced, deterministic, and versioned:

```text
opaque_engine_key_hash = blake3(
  "bifrost.vllm.kvtransfer.key.v1" || 0x00 || canonical_key_json
)
```

`canonical_key_json` must use sorted object keys and stable scalar types.

## Engine profile

Generated BIFROST metadata must use:

```text
schema_version: "bifrost.kv_object.v1alpha1"
object_type: "opaque_engine_blob"
engine_profile.engine_name: "vllm"
engine_profile.integration_name: "bifrost_vllm_kv_connector"
engine_profile.kv_cache_format: "opaque_vllm_kv_blob"
engine_profile.kv_layout: "opaque"
native_tensor_profile: null
prefix_profile: null
```

The `opaque_engine_profile` must include:

```text
engine_key_hash
engine_payload_type
engine_key_repr_version: "vllm_kvtransfer_key.v1"
layout_fingerprint
request_identity
layer_identity
block_identity
```

The `payload_profile` must include:

```text
byte_length
compression: "none"
payload_encoding: "raw_bytes"
```

Compression is out of scope for Phase 7.

## layout_fingerprint

`layout_fingerprint` is the compatibility commitment that prevents one vLLM
layout from loading bytes produced for another layout. It should be derived
from stable metadata such as:

1. vLLM version and KVTransfer API version when available.
2. Model identifier, model revision, or model/config hash when available.
3. Tensor dtype and quantization mode when exposed by vLLM.
4. Attention implementation when exposed by vLLM.
5. Block size, head count, KV head count, head dimension, and layer count when
   exposed by vLLM.
6. Connector blob format version.
7. CPU staging serialization version.

If the connector cannot compute a trustworthy layout fingerprint, it must not
load remote blobs. Save-only operation may still be allowed when the descriptor
records that the layout is unknown and those blobs are not treated as
load-compatible.

## Request, layer, and block identity

The connector should model opaque identity at the smallest safe unit vLLM
allows it to save and load. The expected fields are:

1. `request_identity`: vLLM request ID, prefix ID, or scheduler-provided
   transfer identity that is stable for the reusable KV unit.
2. `layer_identity`: layer index or vLLM-provided layer handle.
3. `block_identity`: KV block ID, slot mapping, block table entry, or
   connector-generated block identity derived from vLLM scheduler metadata.

These fields are compatibility material. They do not authorize BIFROST to
inspect tensor contents or synthesize partial layers.

## CPU staging serialization

Phase 7 uses CPU staging for payload serialization:

```text
vLLM-owned KV state -> CPU-staged opaque bytes -> BIFROST opaque_engine_blob
```

The serializer must:

1. Copy from vLLM-owned buffers only through public or inspected-compatible
   hooks.
2. Record byte length and payload hash over the exact stored bytes.
3. Include serialization format version in immutable metadata.
4. Avoid pickle for production-shaped vLLM payloads.
5. Avoid logging payload bytes.

The deserializer must:

1. Validate BIFROST descriptor, object ID, payload hash, engine profile, opaque
   key hash, and layout fingerprint first.
2. Return bytes only to the vLLM-owned compatibility adapter.
3. Refuse partial, corrupt, staged, quarantined, evicted, or incompatible
   objects.

## Fail-closed validation

Before any load is counted as a hit, the connector must verify:

1. `object_type == "opaque_engine_blob"`.
2. `engine_name == "vllm"`.
3. `integration_name == "bifrost_vllm_kv_connector"`.
4. `kv_cache_format == "opaque_vllm_kv_blob"`.
5. `opaque_engine_key_hash` matches the requested key material.
6. `layout_fingerprint` matches the active connector layout.
7. Request, layer, and block identity match the requested load.
8. Payload byte length and payload hash match.
9. Descriptor hash and object ID validate.
10. The store state is committed and serveable.

Any uncertainty is a miss, recompute decision, or deterministic connector
error. It is never a load hit.

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
  "bifrost.vllm.kv_blob.v1" || 0x00 || canonical_key_json
)
```

`canonical_key_json` must use sorted object keys and stable scalar types.
The Phase 7 implementation exposes this through:

1. `stable_vllm_blob_key(...)`: canonical JSON key material.
2. `vllm_blob_key_hash(...)`: the Phase 1
   `opaque_engine_profile.engine_key_hash`.
3. `stable_kv_cache_config_hash(...)`: a stable commitment to vLLM KV-cache
   configuration material.
4. `stable_layout_fingerprint(...)`: a compatibility commitment over the
   layout inputs available to the connector.

The canonical blob key includes `connector_instance_id`, `request_id`,
`model_fingerprint`, `kv_cache_config_hash`, `layer_name`, `block_ids`,
`role`, `vllm_version` when available, and `layout_fingerprint`.

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
engine_key_repr_version: "vllm_kv_blob_key.v1"
```

The Phase 1 schema currently permits only those three
`opaque_engine_profile` fields. vLLM-specific request, layer, block, layout,
dtype, shape, and device-origin details are therefore included as canonical
JSON inside `provenance.notes`, not as extra top-level descriptor fields. This
keeps generated objects valid through the Phase 1 `validate_object` path.

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

The tensor payload codec stores dense tensor contents as raw contiguous bytes:

```text
payload_bytes = tensor_to_payload(tensor, allow_cpu_staging=True)
tensor = payload_to_tensor(payload_bytes, dtype, shape, device="cpu")
```

CPU tensors are serialized directly. CUDA tensors are copied to CPU only when
CPU staging is explicitly allowed; otherwise serialization fails closed. Dtype,
shape, original device, and the CPU staging format version are recorded in the
descriptor provenance notes. These fields describe how to hand bytes back to
vLLM-owned code; BIFROST still does not reinterpret attention semantics.

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

## Target profile

The vLLM codec builds an opaque target profile with:

```text
accepts_object_type: "opaque_engine_blob"
engine_profile.engine_name: "vllm"
engine_profile.integration_name: "bifrost_vllm_kv_connector"
engine_profile.kv_cache_format: "opaque_vllm_kv_blob"
opaque_requirements.engine_key_hash: vllm_blob_key_hash(...)
opaque_requirements.engine_payload_type: "opaque_vllm_kv_blob"
opaque_requirements.engine_key_repr_version: "vllm_kv_blob_key.v1"
```

Phase 1 opaque compatibility checks compare the engine namespace,
integration namespace, cache format, and opaque key hash. Because the key hash
commits to request, layer, block, model, KV-cache config, role, vLLM version,
and layout fingerprint, a wrong target profile rejects through
`opaque_wrong_engine_key`.

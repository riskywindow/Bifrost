# Phase 5 Opaque Blob Mapping

Last verified: 2026-06-14

## Purpose

This document defines how LMCache-owned objects map into BIFROST
`opaque_engine_blob` objects.

The core rule is:

```text
LMCache owns KV semantics.
BIFROST owns opaque object identity, integrity, storage, and retrieval.
```

BIFROST must not reinterpret LMCache tensor layout, layer ordering, token
ranges, or dtype.

## CacheEngineKey mapping

LMCache `CacheEngineKey` maps to a BIFROST opaque key commitment named
`opaque_engine_key_hash`.

The mapping must be deterministic:

```text
canonical_key_repr = canonical_lmcache_key_repr(CacheEngineKey)
opaque_engine_key_hash = blake3(
  "bifrost.lmcache.key.v1" || 0x00 || canonical_key_repr
)
```

`canonical_lmcache_key_repr` must be stable across processes for the same
LMCache key. The preferred implementation should use LMCache-provided stable
fields or serialization if available. If fake tests use a local key class, the
fake class must expose the same canonicalization contract.

Do not use Python object identity, memory addresses, process IDs, dictionary
iteration order, or noncanonical `repr` output as the hash input.

The Phase 5 codec uses the following ordered strategy:

1. LMCache/key-provided stable hooks such as canonical string, stable repr,
   serialization, or bytes methods.
2. Dataclass, namedtuple, mapping, sequence, and public annotated or instance
   fields, encoded as canonical JSON with sorted object keys.
3. Custom string output only when it is single-line and does not contain memory
   addresses.

If none of those paths is available, key hashing fails closed.

## MemoryObj mapping

LMCache `MemoryObj` maps to BIFROST payload bytes.

The codec boundary is:

```text
payload_bytes = serialize_lmcache_memory_obj(memory_obj)
memory_obj = deserialize_lmcache_memory_obj(payload_bytes)
```

The serializer and deserializer are LMCache-owned or connector-local adapters
around LMCache APIs. BIFROST core must treat `payload_bytes` as opaque.

The codec prefers LMCache-native serialization and deserialization APIs when
they are discoverable by introspection. If no native API is available, it may
use a pickle fallback only when `allow_pickle_fallback` is explicitly enabled.
The pickle fallback exists for local fake tests only. It must not be enabled
for production or cross-trust-boundary LMCache deployments.

Required payload metadata:

```text
engine_payload_type:
  best available MemoryObj type name, or "opaque_lmcache_memory_obj"

byte_length:
  length of payload_bytes

payload_hash:
  hash of exact payload_bytes

compression:
  "none" in Phase 5
```

Compression is out of scope for Phase 5.

## Metadata generation

Generated metadata must use:

```text
schema_version: "bifrost.kv_object.v1alpha1"
object_type: "opaque_engine_blob"
engine_profile.engine_name: "lmcache"
engine_profile.integration_name: "lmcache_bifrost_remote_storage"
engine_profile.kv_cache_format: "opaque_lmcache_memory_obj"
engine_profile.kv_layout: "opaque"
opaque_engine_profile.engine_key_hash: opaque_engine_key_hash
opaque_engine_profile.engine_payload_type: best available MemoryObj type name
opaque_engine_profile.engine_key_repr_version: "lmcache_key_repr.v1"
payload_profile.byte_length: len(payload_bytes)
payload_profile.compression: "none"
payload_profile.payload_encoding: "raw_bytes"
native_tensor_profile: null
prefix_profile: null
```

The current Phase 1 schema requires a `model_profile` object even for opaque
objects. The LMCache codec therefore fills it with deterministic
`lmcache-opaque-unknown` placeholder values and unknown BLAKE3 commitments.
Those fields are not used for opaque compatibility checks. Native tensor
compatibility fields remain null, and the connector must not invent native KV
layout metadata.

Mutable local state must not be included in immutable object identity.

## Object ID computation

The object ID follows the Phase 1 canonical identity rule:

```text
payload_hash = blake3(payload_bytes)
metadata_without_object_id = canonical opaque metadata
object_id = blake3(canonical_metadata_without_object_id || payload_hash)
```

The object ID commits to:

1. Object type.
2. LMCache engine and integration metadata.
3. `opaque_engine_key_hash`.
4. Payload byte length.
5. Payload hash.
6. Canonical immutable metadata.

The object ID must not include:

1. Local file path.
2. Catalog row ID.
3. Staging or committed path.
4. Write state.
5. Last access time.
6. Pinned state.
7. Eviction score.
8. Retry count.
9. Process ID.
10. Benchmark run ID.

For deterministic codec output, `created_at_unix_ms` is fixed at `0` for
generated opaque descriptors. Wall-clock creation time is local store state and
must not affect immutable object identity.

## List behavior

Connector `list` returns stable key representations for committed and verified
opaque objects in the LMCache namespace.

Recommended list entry shape:

```text
lmcache:{opaque_engine_key_hash}
```

If the connector stores a sanitized canonical key representation for debugging,
it may expose that representation only when it is deterministic and does not
leak prompt text or sensitive tenant data.

List results are not sufficient for loading. `get` must still revalidate the
descriptor, payload, object ID, and requested key hash.

## Key repr handling

The canonical key representation is an internal hash input and should not be
assumed safe to log. If LMCache keys include prompt-derived material, token
content, tenant IDs, or request identifiers, the connector must prefer logging
only `opaque_engine_key_hash`.

Debug logs may include:

```text
opaque_engine_key_hash
payload byte length
object_id
store status
error reason code
```

Debug logs should not include:

```text
raw prompt text
raw token IDs unless explicitly enabled for local tests
full serialized MemoryObj bytes
tenant secrets
authorization headers
```

## Privacy and safety

Phase 5 is not a production security boundary, but it must avoid unnecessary
data exposure.

Rules:

1. Treat LMCache payloads as sensitive.
2. Do not log payload bytes.
3. Do not log raw cache keys by default.
4. Do not expose staged objects through `exists`, `get`, or `list`.
5. Validate payload hashes before returning bytes to LMCache.
6. Report misses rather than returning uncertain objects.
7. Keep production authentication out of scope rather than shipping partial
   security claims.

# Object Identity

Last verified: 2026-05-27

## Purpose

BIFROST object identity must be immutable, deterministic, and independent of local cache state.

An object ID answers:

```text
Which exact KV object descriptor and payload bytes does this name commit to?
```

It must not answer:

```text
Where is this object stored right now?
Is it pinned?
When was it last accessed?
Which cache tier currently owns it?
```

Those are mutable local record fields.

## Immutable descriptor

The immutable descriptor is the canonical metadata that defines the KV object.

It includes fields needed to validate:

1. Schema version.
2. Object type.
3. Model compatibility.
4. Engine compatibility.
5. Prefix identity.
6. Tensor or opaque payload meaning.
7. Payload byte length.
8. Payload hash.
9. Descriptor hash.
10. Object ID.

The descriptor is part of the object. Changing an immutable descriptor field creates a different object.

## Mutable local object record

A local object record describes one cache node's current handling of an object.

Mutable record fields may include:

```text
tier
pinned
last_accessed_unix_ms
cache_location
local_path
write_state
expires_at_unix_ms
eviction_priority
ref_count
```

These fields must not affect payload hash, descriptor hash, or object ID. Two machines may store the same immutable object in different tiers or paths and still refer to the same object ID.

## Canonical JSON

Phase 1 uses canonical JSON for descriptor hashing.

Canonical JSON requirements:

1. UTF-8 encoded bytes.
2. Object keys sorted lexicographically.
3. No insignificant whitespace.
4. Deterministic integer and string representation.
5. Arrays preserve order.
6. No NaN, infinity, or implementation-specific number encodings.
7. No duplicate object keys after parsing.
8. Unknown fields rejected unless the schema explicitly allows them.

The same descriptor must produce identical canonical bytes in Python and Rust.

## Schema evolution

Phase 1 accepts only `bifrost.kv_object.v1alpha1`. Unknown schema versions fail
closed with `unknown_schema_version`; validators do not guess forward
compatibility.

Future schema versions must define a new supported version string, immutable
descriptor shape, canonical hashing behavior, and migration expectations before
validators accept them. Adding fields to `v1alpha1` descriptors is not allowed:
strict schemas reject extra fields so that object identity remains stable.

## Payload hash

The payload hash commits to the exact payload bytes.

Format:

```text
payload_hash = "blake3:" || hex(blake3(payload_bytes))
```

The descriptor records both the payload hash and expected payload byte length. Validation rejects the object if either the byte length or hash mismatches.

## Descriptor hash

The descriptor hash commits to the immutable descriptor, excluding fields whose value depends on the descriptor hash or object ID itself.

Phase 1 descriptor hash input:

```text
canonical_json(immutable_descriptor_without_descriptor_hash_and_object_id)
```

Format:

```text
descriptor_hash = "blake3:" || hex(blake3(descriptor_hash_input))
```

Validation recomputes this value and rejects mismatches.

## Object ID

The object ID commits to the descriptor hash and payload hash.

Phase 1 object ID input:

```text
"bifrost.object_id.v1" || NUL || descriptor_hash || NUL || payload_hash
```

Format:

```text
object_id = "bifrost://object/blake3/" || hex(blake3(object_id_input))
```

The object ID is a durable content identity. It is not a storage path, URL for retrieval, or mutable cache key.

## Why mutable fields are excluded

Mutable fields like `tier`, `pinned`, `last_accessed_unix_ms`, and cache location must not affect identity because they can change without changing the KV object.

Including mutable state in object identity would cause correctness and operability failures:

1. Moving an object from RAM to disk would change its identity.
2. Reading an object would update `last_accessed_unix_ms` and create a false new object.
3. Pinning an object would invalidate existing references.
4. Two cache nodes could disagree on identity for identical bytes.
5. Eviction or placement policy could corrupt cross-language test vectors.

Immutable identity is for object correctness. Mutable records are for local cache management.

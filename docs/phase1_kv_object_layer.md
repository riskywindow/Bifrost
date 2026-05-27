# Phase 1 KV Object Layer

Last verified: 2026-05-27

## Purpose

Phase 1 builds the correctness-first KV object layer for BIFROST.

The layer defines what a KV-cache object is, how its immutable identity is computed, how payload bytes are committed to that identity, and how incompatible or corrupted objects are rejected before any later system can reuse them.

Phase 1 is not about moving KV bytes quickly. It is about making sure BIFROST can answer this question deterministically:

```text
Given a descriptor, payload, and target compatibility profile, is this KV object exactly the object it claims to be and safe to accept?
```

## What Phase 1 builds

Phase 1 includes:

1. Versioned KV object schemas.
2. Canonical JSON rules for descriptors.
3. Payload hash, descriptor hash, and object ID construction.
4. A stable validation error taxonomy.
5. Python reference implementation for canonicalization, hashing, and validation.
6. Fixture generator for accepted and rejected objects.
7. CLI for validating and inspecting fixtures.
8. Rust metadata mirror for parsing and validating the same descriptors.
9. Cross-language test vectors proving Python and Rust parity.

## Explicitly out of scope

Phase 1 does not include:

1. Networking.
2. Multipath transfer.
3. Object storage backends.
4. LMCache integration.
5. vLLM integration.
6. Dashboards.
7. Model inference.
8. Real KV extraction.
9. Real KV injection.
10. Remote cache discovery.
11. Eviction policy implementation.
12. Performance benchmarking beyond basic local validation timing if useful for diagnostics.

Any object storage, transfer, or inference integration work belongs to a later phase.

## Native KV page vs opaque engine blob

Phase 1 supports two object types at the schema and validation level.

### native_kv_page

A `native_kv_page` is a KV-cache page whose tensor meaning is described by BIFROST metadata.

BIFROST validates native fields such as:

```text
model hash
tokenizer hash
config hash
RoPE hash
dtype
layer count
KV head count
head dimension
engine name and version
attention implementation
KV layout
block size
prefix hash
token range
absolute position range
layer id
KV block id
tensor shape
tensor dtype
tensor layout
payload byte length
payload hash
```

If any required native compatibility field is missing, malformed, or mismatched, the object is rejected.

### opaque_engine_blob

An `opaque_engine_blob` is engine-owned bytes wrapped by BIFROST metadata.

BIFROST may validate:

```text
schema version
object type
engine key hash
engine name
engine version
integration name
payload byte length
payload hash
descriptor hash
object ID
payload encoding
compression
```

BIFROST must not reinterpret an opaque blob as tensors. If BIFROST cannot prove compatibility from the opaque metadata, it rejects the object or reports that the payload is not interpretable.

## Python first, Rust second

Python is the Phase 1 reference implementation.

The Python implementation defines:

1. Canonical JSON bytes.
2. Hash input construction.
3. Object ID construction.
4. Validation order.
5. Stable error reason behavior.
6. Fixture generation.

Rust mirrors the Python behavior after the reference behavior is covered by tests. Rust must not introduce a second interpretation of the schema. Cross-language test vectors are the contract.

## Acceptance criteria

Phase 1 is accepted when:

1. Schemas are documented and versioned.
2. Python can canonicalize, hash, and validate descriptors and payloads deterministically.
3. Python rejects all known-bad fixtures with stable reason codes.
4. The fixture generator creates reproducible accepted and rejected fixtures.
5. The CLI can validate fixture files and report reason codes.
6. Rust can parse and validate the same metadata shape.
7. Python and Rust agree on canonical JSON, payload hash, descriptor hash, object ID, and reason codes for cross-language fixtures.
8. CI runs the relevant Python, Rust, and parity tests.
9. No Phase 2 features are required for Phase 1 tests to pass.

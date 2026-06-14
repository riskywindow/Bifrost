# BIFROST Agent Guidance

Last verified: 2026-06-14

## Current phase

BIFROST is in Phase 5.

Phase 1 is complete. It produced immutable KV object descriptors, target
compatibility profiles, canonical object identity hashing, Python reference
validation, Rust mirror validation, deterministic fixtures, CLI tooling, and
cross-language test vectors.

Phase 2 is complete. It produced the synthetic transport protocol, chunking and
reassembly, a minimal transfer spool, single-path PUT, HAS, and GET, optional
multipath PUT and retry behavior, JSONL traces and metrics, and ContextStorm
synthetic transport benchmarks.

Phase 3 is complete. It produced the durable local KV object store, SQLite
catalog, lookup, query, inspect, stats, pinning, deterministic eviction,
prefix/session manifests, fsck, and ContextStorm store benchmarks.

Phase 4 is complete. It produced a CPU-friendly tiny-transformer correctness
harness, integer tokenization, real KV extraction, native KV page
serialization, store-backed rehydration, logit and greedy-continuation
comparisons, fail-closed corruption tests, and a cross-process KV teleport
demo.

The Phase 5 goal is to implement BIFROST as a custom LMCache remote storage
backend. LMCache-owned KV objects must be stored as BIFROST
`opaque_engine_blob` objects, indexed by LMCache `CacheEngineKey` hash,
retrieved by the connector, and roundtripped through fake and optional real
LMCache tests.

## Phase 5 scope

Agents may work on:

1. A Python BIFROST client surface for LMCache connector use.
2. A codec that wraps LMCache-owned bytes as `opaque_engine_blob` objects.
3. A `BifrostConnectorAdapter` for LMCache remote storage plugin loading.
4. A `BifrostRemoteConnector` implementing LMCache remote storage methods.
5. Mapping LMCache `CacheEngineKey` values to stable BIFROST
   `opaque_engine_key_hash` values.
6. Storing and retrieving LMCache `MemoryObj` payloads without interpreting
   tensor semantics.
7. Fake LMCache tests that run in CI without installing LMCache.
8. Optional real LMCache integration tests that skip when LMCache is not
   installed.
9. Optional vLLM plus LMCache smoke tests that are opt-in and skipped by
   default.
10. ContextStorm LMCache-style workloads that remain local and deterministic
    by default.

Agents must not implement a raw vLLM KVTransfer connector in Phase 5.

Do not add:

1. Raw vLLM KVTransfer integration.
2. SGLang integration.
3. Kubernetes.
4. Dashboards.
5. GPU-required tests.
6. Hugging Face model downloads or tokenizer downloads.
7. External model downloads.
8. Custom CUDA.
9. RDMA.
10. QUIC.
11. Compression.
12. Parity chunks or FEC.
13. Production authentication.
14. Distributed routing or scheduler logic.

GPU and real-serving demos are optional exploratory work only. They must be
skipped by default and must never be required by tests, CI, or default demo
commands.

## Correctness rules

BIFROST may miss a cache hit, but it must never return a wrong, corrupt,
partial, incompatible, or semantically uncertain object as an LMCache hit.

Phase 5 must use `opaque_engine_blob` for LMCache KV objects. LMCache owns the
tensor layout, serialization meaning, cache chunking, and rehydration semantics.
BIFROST may hash, validate, store, transfer, retrieve, list, and delete local
records for opaque bytes, but it must not reinterpret LMCache tensor semantics
or convert LMCache payloads into `native_kv_page`.

Every connector operation must fail closed. If serialization, descriptor
generation, validation, store commit, catalog lookup, payload integrity,
retrieval, deserialization, key matching, or connector lifecycle state is
uncertain, the connector must return a miss or raise a deterministic connector
error according to the method contract. It must not synthesize a `MemoryObj`
from suspect bytes.

Only committed and verified objects may satisfy LMCache `exists`, `get`,
`list`, or batched lookup operations. Staging objects must never be visible as
available cache hits and must never satisfy connector APIs that imply
availability.

Mutable store, transport, connector, or benchmark state must never be included
in immutable object identity. Fields such as staging path, committed path,
local tier, pinned state, write state, last access time, expiry, transfer
state, retry count, peer address, cache location, benchmark run ID, process ID,
demo label, and eviction score describe local records, not immutable opaque
objects.

Prefer boring correctness over clever optimization. Deterministic behavior,
stable tests, readable validation, explicit error reasons, durable catalog
updates, and clean LMCache miss behavior matter more than throughput in Phase 5.

## Dependencies

Do not add new production dependencies without a written justification in the
relevant change. Prefer standard library functionality and existing project
dependencies.

LMCache may be used by optional integration tests if it is installed in the
developer environment. Real LMCache tests must skip when LMCache is missing and
must not be required in CI unless CI explicitly installs LMCache for that job.

vLLM may be used only by opt-in smoke tests. vLLM tests must skip by default and
must not require GPU hardware, model downloads, cloud credentials, Docker, or
internet access in the default test path.

Test-only dependencies are acceptable when they materially improve coverage and
are scoped to tests.

## Error codes

Keep Phase 1 validation error reason codes stable. Once fixtures, tests, or
docs rely on a reason code, do not rename or delete it without a migration note
and updated compatibility expectations.

Phase 2 protocol and transfer errors should remain specific and deterministic.
Transport errors must not be conflated with object validation errors.

Phase 3 store errors should distinguish catalog errors, filesystem errors,
integrity errors, compatibility errors, manifest errors, eviction errors, and
fsck findings.

Phase 4 harness errors should distinguish model determinism errors, tokenizer
or token-hash errors, KV extraction errors, serialization errors, validation
errors, store roundtrip errors, manifest completeness errors, rehydration
errors, logit mismatch errors, and greedy-continuation mismatch errors.

Phase 5 connector errors should distinguish LMCache serialization errors,
opaque blob validation errors, key hashing errors, store commit errors, store
retrieval errors, missing objects, corrupt objects, descriptor mismatch, payload
hash mismatch, connector configuration errors, connector lifecycle errors, and
optional real-LMCache compatibility errors.

## Tests

Run relevant Rust and Python tests after changes.

Expected Phase 5 test coverage:

1. Stable `CacheEngineKey` canonical representation and
   `opaque_engine_key_hash` construction.
2. LMCache `MemoryObj` to payload bytes roundtrip through the opaque blob codec.
3. Descriptor generation for `opaque_engine_blob` with LMCache engine metadata.
4. Phase 1 Python and Rust validation for generated opaque descriptors and
   payloads.
5. Store commit, lookup, HAS, GET, query, stats, and list behavior for opaque
   LMCache blobs.
6. `BifrostConnectorAdapter` URL parsing and connector construction.
7. `BifrostRemoteConnector.exists`, `exists_sync`, `get`, `put`, `list`, and
   `close`.
8. Connector failure cases for missing object, corrupt payload, descriptor
   mismatch, target mismatch, key mismatch, store error, and serialization
   error.
9. Fake LMCache tests that run in CI without importing LMCache.
10. Optional real LMCache tests that skip when LMCache is not installed.
11. Optional vLLM plus LMCache smoke tests that are opt-in and skipped by
    default.
12. ContextStorm LMCache workload smoke tests that are CPU-only, local, and
    deterministic by default.
13. Phase 1 parity tests, Phase 2 transport tests, Phase 3 store tests, and
    Phase 4 tiny-transformer correctness tests remain green.

Keep tests CPU-only and local by default. Tests must not require GPU hardware,
cloud credentials, external services, LMCache, vLLM, Hugging Face downloads,
Docker, Kubernetes, or internet access unless explicitly marked optional and
skipped by default.

Root-required network fault tests, GPU demos, real LMCache tests, and vLLM
smoke tests must remain opt-in and skipped by default.

If a test cannot be run, state the reason in the final response.

## Implementation order

Build the LMCache remote storage integration before direct engine integrations.

Use Phase 1 opaque object validation as the acceptance gate for every LMCache
object generated by the connector. The LMCache adapter, BIFROST client,
transport, spool, catalog, store, manifest, and benchmark layers may track
local state, but they must not redefine opaque object identity or reinterpret
LMCache payload semantics.

Recommended order:

1. Phase 5 design docs and checklist.
2. Python client API for daemon-backed opaque object operations.
3. Opaque blob codec for LMCache key and payload mapping.
4. Fake LMCache key and memory object fixtures.
5. Connector adapter URL parsing and configuration.
6. Remote connector `exists`, `exists_sync`, `put`, `get`, `list`, and `close`.
7. Store roundtrip and fail-closed tests for fake LMCache objects.
8. Optional real LMCache import and API compatibility tests.
9. Optional vLLM plus LMCache smoke test harness.
10. ContextStorm LMCache workload scenarios.

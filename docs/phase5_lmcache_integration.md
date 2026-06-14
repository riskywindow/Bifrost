# Phase 5 LMCache Integration

Last verified: 2026-06-14

## Purpose

Phase 5 integrates BIFROST with LMCache as a custom remote storage backend.
LMCache remains responsible for cache chunk semantics and engine integration.
BIFROST provides verified opaque object storage, retrieval, transport, and
local durability behind the LMCache remote storage connector interface.

The required path is:

```text
vLLM or test harness
  -> LMCache
  -> BIFROST LMCache remote storage connector
  -> BIFROST Python client
  -> bifrostd Phase 2/3 store APIs
```

Direct vLLM KVTransfer integration is not part of Phase 5.

## Why LMCache first

LMCache is the first external integration because it already owns the KV cache
reuse problem and exposes the right boundary for BIFROST:

1. LMCache represents reusable KV cache state as cache engine keys and memory
   objects.
2. LMCache has a remote storage plugin surface with adapter and connector
   responsibilities.
3. The connector operations match BIFROST store operations: `exists`,
   `exists_sync`, `get`, `put`, `list`, and `close`.
4. BIFROST can integrate without forking LMCache or depending on vLLM internals.
5. Fake LMCache tests can exercise the contract in CI while real LMCache tests
   remain optional.

This is the narrowest production-adjacent integration that validates BIFROST as
a KV cache storage backend.

## Why opaque_engine_blob

LMCache-owned KV objects must be stored as BIFROST `opaque_engine_blob` objects,
not `native_kv_page`.

Reason:

1. LMCache owns `MemoryObj` semantics, tensor layout, chunk boundaries, and
   engine compatibility decisions.
2. BIFROST does not have enough information at the remote storage boundary to
   safely reinterpret a `MemoryObj` as native KV tensors.
3. Treating LMCache payloads as opaque preserves the integration contract even
   if LMCache changes internal tensor layout.
4. BIFROST can still provide value by validating payload hashes, descriptor
   hashes, object IDs, key hashes, committed store state, and retrieval
   integrity.

Opaque storage means BIFROST can reject corrupted or mismatched objects, but it
does not decide whether a byte range is a key tensor, value tensor, layer, or
token block.

## What Phase 5 builds

Phase 5 builds:

1. A Python BIFROST client API suitable for LMCache connector use.
2. An opaque blob codec for LMCache key and memory object mapping.
3. `BifrostConnectorAdapter` for LMCache plugin discovery and URL parsing.
4. `BifrostRemoteConnector` for LMCache remote storage operations.
5. Fake LMCache tests that validate behavior without installing LMCache.
6. Optional real LMCache tests that skip when LMCache is unavailable.
7. Optional vLLM plus LMCache smoke tests that are skipped by default.
8. ContextStorm scenarios that emulate LMCache-style opaque object workloads.

The connector must fail closed on serialization, validation, store, retrieval,
or lifecycle errors.

## Out of scope

Phase 5 does not build:

1. A raw vLLM KVTransfer connector.
2. SGLang integration.
3. Kubernetes deployment.
4. Dashboard UI.
5. GPU-required tests.
6. External model or tokenizer downloads.
7. Custom CUDA.
8. RDMA.
9. QUIC.
10. Compression.
11. Parity chunks or FEC.
12. Production authentication.
13. Distributed routing or scheduler logic.

Any real LMCache or vLLM demo must be opt-in and skipped by default.

## Later vLLM integration

Phase 5 prepares for vLLM without depending on vLLM internals. The supported
near-term route is:

```text
vLLM -> LMCache -> BIFROST remote storage plugin -> bifrostd
```

This allows a vLLM smoke test to demonstrate cache hits and misses through
LMCache while keeping BIFROST behind a stable storage boundary.

A future direct vLLM connector may use `native_kv_page` if vLLM exposes enough
stable tensor layout and compatibility metadata. That future work must have a
separate phase, design document, and fail-closed test plan.

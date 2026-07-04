# Phase 7 vLLM Connector

Last verified: 2026-07-04

## Purpose

Phase 7 builds a compatibility-first native vLLM KVTransfer connector for
BIFROST.

The target path is:

```text
vLLM KVTransfer -> BIFROST vLLM connector -> BIFROST Python client -> bifrostd
```

The connector must be dynamically importable by vLLM, testable through fake
vLLM interfaces in CI, and able to store and load vLLM-owned KV state through
BIFROST as `opaque_engine_blob` objects.

## Why this comes after LMCache and Phase 6

Phase 5 integrated BIFROST behind LMCache first because LMCache provided a
clear remote storage boundary and owned cache chunk semantics. That let
BIFROST prove the opaque-object storage contract without coupling directly to
vLLM internals.

Phase 6 then proved the real serving matrix:

1. `vllm_only`
2. `vllm_lmcache_local_cpu`
3. `vllm_lmcache_bifrost`

The final Phase 6 review reported no gate failures, 9 real rows across 3 modes
and 3 repetitions, observed BIFROST connector activity, observed LMCache local
CPU activity, clean fsck, and no speedup claim because BIFROST was slower in
the single-host run.

That sequence matters. Phase 7 starts from a known working BIFROST store,
client, opaque object validation model, connector metrics model, and serving
benchmark discipline. The direct vLLM connector can now focus on compatibility
with vLLM's KVTransfer API rather than proving the whole storage stack from
scratch.

## What Phase 7 proves

Phase 7 should prove:

1. vLLM can dynamically import the BIFROST connector package in compatible
   versions.
2. Fake vLLM tests can exercise the connector lifecycle in default CI without
   vLLM, LMCache, GPU, model downloads, tokenizers, or internet access.
3. The connector can save vLLM-owned KV blobs into BIFROST as committed and
   verified `opaque_engine_blob` objects.
4. The connector can load only compatible, committed, verified, and
   payload-valid blobs back to vLLM-owned buffers.
5. Missing, corrupt, incompatible, partial, or lifecycle-invalid blobs produce
   misses, recompute decisions, or deterministic connector errors.
6. Metrics and traces identify save, load, skip, recompute, and failure
   reasons without logging raw KV payload bytes.
7. Optional real vLLM smoke tests can verify import, construction, save-only
   behavior, and a 1P1D scaffold when explicitly enabled.

## Why opaque_engine_blob

Phase 7 uses `opaque_engine_blob`, not `native_kv_page`.

Reason:

1. vLLM owns the KV tensor layout, block allocation, scheduler metadata,
   staging buffers, and rehydration semantics.
2. The KVTransfer API is version-sensitive and may expose different object
   shapes, buffer owners, or method signatures across vLLM releases.
3. BIFROST can safely validate immutable byte identity, payload hashes, object
   IDs, committed store state, engine namespace, integration namespace, layout
   fingerprint, request identity, layer identity, and block identity.
4. BIFROST cannot safely infer whether an opaque byte range is a key tensor,
   value tensor, layer slice, block table, or scheduler-owned staging buffer.

Opaque storage means BIFROST can reject wrong bytes. It does not mean BIFROST
understands vLLM tensor semantics.

## Out of scope

Phase 7 does not build:

1. RDMA.
2. QUIC.
3. Compression.
4. Parity chunks or FEC.
5. GPU-direct transfer.
6. SGLang integration.
7. Kubernetes.
8. Dashboard UI.
9. Production authentication.
10. Custom CUDA.
11. Mandatory GPU CI.
12. Hugging Face model or tokenizer downloads in default tests, demos, or CI.
13. Distributed routing or scheduler logic beyond connector metadata required
    to reject incompatible blobs.

Real vLLM testing remains optional, opt-in, and skipped by default.

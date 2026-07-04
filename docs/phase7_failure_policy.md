# Phase 7 Failure Policy

Last verified: 2026-07-04

## Purpose

The direct vLLM connector must fail closed. BIFROST may miss a cache hit, but
it must never return wrong, corrupt, partial, incompatible, or semantically
uncertain KV state to vLLM.

## Outcomes

Phase 7 uses three outcome classes:

1. `hit`: bytes were loaded from a committed, verified, compatible BIFROST
   object and accepted by the vLLM-owned adapter.
2. `recompute`: the connector did not load bytes and vLLM should recompute or
   continue through its normal local path.
3. `fail`: a deterministic connector error should be raised because the method
   contract, lifecycle state, or operator configuration cannot continue safely.

`recompute` is preferred for ordinary misses. `fail` is required for
configuration errors, lifecycle misuse, detected corruption when the installed
vLLM contract cannot accept a miss, or internal invariants that make connector
state unreliable.

## Missing blob behavior

When no matching committed object exists:

1. `exists`-like checks return false when available.
2. Load methods return the vLLM-compatible miss shape or record the block for
   recompute.
3. Metrics increment miss and recompute counters.
4. No error is counted unless the lookup itself failed.

Reason code: `vllm_blob_missing`.

## Corrupt blob behavior

Corrupt means descriptor validation fails, payload hash mismatches, object ID
mismatches, BIFROST GET returns incomplete bytes, fsck marks the object dirty,
or the payload cannot be staged back into vLLM-owned buffers.

Behavior:

1. Never return bytes to vLLM as a hit.
2. Record the object ID and opaque key hash when available.
3. Increment corruption and load error counters.
4. Return recompute only if the vLLM lifecycle method allows a safe miss.
5. Otherwise raise a deterministic connector error.

Reason codes:

```text
vllm_blob_corrupt
payload_hash_mismatch
descriptor_mismatch
object_id_mismatch
cpu_staging_deserialization_error
```

## Incompatible blob behavior

Incompatible means the object is valid BIFROST data but does not match the
active vLLM layout, request, layer, block, model/config commitment, engine
namespace, integration namespace, or blob format.

Behavior:

1. Treat as a miss for load selection.
2. Do not delete the object automatically.
3. Preserve enough metrics to explain why it was rejected.
4. Never coerce the bytes into the current vLLM layout.

Reason codes:

```text
engine_mismatch
integration_mismatch
kv_cache_format_mismatch
layout_fingerprint_mismatch
request_identity_mismatch
layer_identity_mismatch
block_identity_mismatch
opaque_key_hash_mismatch
```

## Daemon unavailable behavior

Daemon unavailable means connect timeout, refused connection, protocol error,
store stats failure, BIFROST client closed state, or daemon health failure.

Behavior:

1. Save path: fail the save operation and do not count it as stored.
2. Load path: return recompute if vLLM can safely continue without remote KV.
3. Lifecycle or readiness path: raise a deterministic connector error.
4. Metrics must identify daemon unavailability separately from cache miss.

Reason codes:

```text
daemon_unavailable
daemon_timeout
daemon_protocol_error
store_commit_error
store_retrieval_error
```

## Lifecycle errors

Lifecycle errors include save before registration, load before registration,
use after shutdown, conflicting cache registration, duplicate incompatible
request state, or outstanding work that cannot be safely resolved.

Behavior:

1. Raise deterministic connector errors.
2. Do not synthesize misses if connector state itself is unreliable.
3. Flush metrics when possible.

Reason codes:

```text
connector_not_registered
connector_closed
connector_lifecycle_error
cache_registration_error
outstanding_work_cancelled
```

## Metrics and traces

Metrics should include:

1. Save count, save success count, save error count.
2. Load count, hit count, miss count, recompute count, load error count.
3. Bytes saved and bytes loaded.
4. Query count and candidate rejection counts.
5. Daemon error counts.
6. Validation error counts.
7. Lifecycle error counts.
8. Total save and load wall-clock milliseconds.

JSONL events should include:

```text
vllm_connector_initialized
vllm_kv_caches_registered
vllm_save_started
vllm_save_completed
vllm_load_started
vllm_load_hit
vllm_load_recompute
vllm_load_error
vllm_request_finished
vllm_connector_shutdown
```

Events may include `opaque_engine_key_hash`, `object_id`, `layout_fingerprint`,
layer ID, block ID, byte count, duration, and reason code. Events must not log
raw KV payload bytes, raw prompt text, authorization headers, or tenant
secrets.

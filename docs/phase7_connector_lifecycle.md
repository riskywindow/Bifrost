# Phase 7 Connector Lifecycle

Last verified: 2026-07-04

## Purpose

This document defines the lifecycle BIFROST should support for a direct vLLM
KVTransfer connector. Method names follow the expected vLLM V1 connector shape,
but the implementation must adapt only after the API inspector confirms the
installed signatures.

## `__init__`

`__init__` parses connector configuration, records the inspected vLLM API
shape, initializes BIFROST client configuration, sets lifecycle state to
`initialized`, and prepares metrics.

It must not:

1. Download models.
2. Initialize CUDA unless vLLM has already done so and passes safe handles.
3. Start background network mutation.
4. Mark the connector load-ready before KV caches are registered.

Invalid config raises a deterministic connector configuration error.

## `register_kv_caches`

`register_kv_caches` receives vLLM-owned cache handles or metadata. It records
only the compatibility facts needed to compute `layout_fingerprint`, stage
payload bytes, and validate later loads.

It must fail closed when required cache metadata is missing, conflicting, or
not stable enough to identify layout compatibility.

Successful registration transitions the connector to `registered`.

## `save_kv_layer`

`save_kv_layer` stages one save unit from vLLM-owned KV state, builds opaque
key material, serializes payload bytes through CPU staging, creates
`opaque_engine_blob` metadata, validates the descriptor, and starts or performs
the BIFROST put.

It must not count a save as successful until BIFROST reports the object stored
and verified. Failed saves do not create hits; vLLM may continue by recomputing
or by using its local path.

## `wait_for_save`

`wait_for_save` waits for outstanding save work to complete and returns or
raises according to the inspected vLLM contract.

It must preserve per-layer or per-block reason codes. Timeouts, daemon errors,
validation failures, and lifecycle errors must be visible in metrics and
traces.

## `start_load_kv`

`start_load_kv` receives scheduler metadata for a load request, computes opaque
key material, queries BIFROST by `engine_name`, `integration_name`, and
`opaque_engine_key_hash`, and starts retrieval for compatible candidates.

Missing objects are not errors by themselves. They are load misses and should
lead to recompute unless the installed vLLM method contract requires a
specific error return.

## `wait_for_layer_load`

`wait_for_layer_load` completes retrieval for a layer or block, validates the
stored object, and copies bytes back through the vLLM-owned adapter only after
all compatibility checks pass.

It must refuse:

1. Missing candidates.
2. Staged or unverified objects.
3. Corrupt payloads.
4. Descriptor or object ID mismatches.
5. Layout fingerprint mismatches.
6. Request, layer, or block mismatches.
7. Lifecycle calls before registration or after shutdown.

Refused loads are misses or deterministic errors, never partial hits.

## `get_block_ids_with_load_errors`

`get_block_ids_with_load_errors` reports block IDs that could not be loaded
cleanly. The connector should include stable reason codes internally and expose
only the shape expected by the installed vLLM version.

The method must not hide corruption or compatibility failures as successful
loads.

## `request_finished`

`request_finished` releases per-request connector state, records final metrics,
and clears temporary staging buffers. It may leave committed BIFROST objects in
the store.

It must not mutate immutable object identity or delete shared cache state
unless vLLM explicitly asks for eviction through a supported API.

## `shutdown`

`shutdown` drains or cancels outstanding work according to config, closes
BIFROST clients, flushes metrics, and transitions to `closed`.

After shutdown:

1. Save methods must raise lifecycle errors.
2. Load methods must miss or raise lifecycle errors according to the vLLM
   contract.
3. Metrics snapshots may still be readable.

## Fake lifecycle

Fake vLLM tests must exercise the same state machine without importing vLLM:

```text
new -> initialized -> registered -> saving/loading -> request_finished -> closed
```

The fake lifecycle should provide deterministic fake cache handles, request
metadata, layer IDs, block IDs, and byte payloads. It must run in CI with no
GPU, no vLLM, no LMCache, no model downloads, and no internet.

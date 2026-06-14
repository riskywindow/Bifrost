# Phase 5 Python Client

Last verified: 2026-06-14

## Purpose

Phase 5 needs a Python BIFROST client that the LMCache remote connector can use
without depending on Rust internals or shelling out to CLI commands. The client
speaks the existing Phase 2/3 daemon TCP frame protocol.

The client does not understand LMCache tensor semantics. It sends and receives
validated BIFROST objects; LMCache-specific opaque descriptor construction is a
separate Phase 5 step.

## Client API

The implemented async API is `bifrost_client.BifrostAsyncClient`:

```text
BifrostAsyncClient.connect() -> BifrostAsyncClient
BifrostAsyncClient.close() -> None
BifrostAsyncClient.ping() -> bool
BifrostAsyncClient.put_object(metadata, payload, chunk_size=262144) -> PutResult
BifrostAsyncClient.has_object(object_id) -> bool
BifrostAsyncClient.get_object(object_id) -> StoredObject
BifrostAsyncClient.query_by_opaque_key_hash(
  engine_name,
  integration_name,
  opaque_engine_key_hash,
) -> list[ObjectSummary]
BifrostAsyncClient.list_objects(...) -> list[ObjectSummary]
BifrostAsyncClient.stats() -> StoreStats
```

`ping()` currently performs protocol negotiation with the daemon. The Rust
daemon defines `ping`/`pong` frame types, but the request loop does not yet
handle a post-handshake `ping`, so the Python client uses a hello-only liveness
probe for compatibility with the existing daemon.

PUT validates the descriptor and payload through the Phase 1 Python validator
before sending bytes. GET revalidates returned descriptor and payload bytes
before constructing `StoredObject`. HAS returns `False` for normal misses.
Suspect GET bytes raise deterministic client errors instead of becoming hits.

## Sync Wrapper

The implemented sync wrapper is `bifrost_client.BifrostClient`. It owns a
private background event loop and submits async client calls with
`asyncio.run_coroutine_threadsafe`. This keeps synchronous LMCache methods from
depending on a caller-owned event loop and avoids nested `asyncio.run()` calls.

Limitations:

1. The wrapper should be closed explicitly so its background thread stops.
2. Operations remain blocking from the caller's perspective.
3. It is a thin bridge, not a connection pool; each daemon operation still uses
   a short-lived TCP connection like the Rust client helpers.

## Result Types

Implemented result fields:

```text
PutResult:
  object_id
  payload_hash
  descriptor_hash
  stored
  verified
  reason

StoredObject:
  object_id
  metadata
  payload
  payload_hash
  descriptor_hash

ObjectSummary:
  object_id
  object_type
  state
  byte_length
  model_hash
  prefix_hash
  engine_name
  integration_name
  opaque_engine_key_hash
  layer_id
  kv_block_id
  pin_count
  last_accessed_unix_ms

StoreStats:
  object_count
  total_logical_bytes
  total_bytes_on_disk
  staging_count
  committed_count
  verified_count
  pinned_count
  evictable_count
  evicting_count
  evicted_count
  quarantined_count
  missing_count
  corrupt_count
  total_pin_count
  total_access_count
  memory_tier_* counters
```

All results that imply availability refer only to daemon-served committed and
verified objects.

## Protocol Compatibility

The client uses the Rust daemon frame format:

```text
u32 big-endian header length
compact UTF-8 JSON header
header.payload_len raw payload bytes
```

The protocol version is `bifrost.transport.v1alpha1`.

Supported frame families:

```text
hello
put_begin, chunk, chunk_ack, put_commit, put_result
has_request, has_result
get_begin, get_result
query_request, query_result
list_request, list_result
stats_request, stats_result
error
```

The daemon does not expose an exact opaque-object helper yet. The client
composes existing PUT/HAS/GET/query calls. `query_by_opaque_key_hash` first
queries by daemon-supported filters, then GETs candidate objects and checks
`engine_profile.integration_name` from revalidated metadata before returning
summaries.

Protocol version checks fail closed. Unsupported versions and malformed frames
raise deterministic protocol errors.

## Error Taxonomy

The implemented public error taxonomy is:

```text
BifrostClientError
BifrostConnectionError
BifrostProtocolError
BifrostValidationError
BifrostNotFoundError
BifrostServerError
```

Future connector-specific layers may wrap these with narrower LMCache error
codes such as key mismatch or store commit failure.

## Testing

Implemented tests:

1. Pure protocol encode/decode compatibility for hello and chunk frames.
2. Payload length mismatch rejection.
3. Error frame mapping to `BifrostServerError`.
4. Async daemon roundtrip for the committed opaque fixture.
5. HAS, GET, query-by-opaque-key-hash, list, and stats against a local daemon.
6. Sync `has_object` through the background-loop wrapper.

Default tests use a local daemon and local fixtures. They do not require
LMCache, vLLM, GPU hardware, external services, or internet access. Daemon
integration tests skip clearly if Rust binaries are unavailable.

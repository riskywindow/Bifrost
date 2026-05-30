# Phase 3 Store CLI

Last verified: 2026-05-30

`bifrost-store` exposes Phase 3 local store operations through the daemon
transport protocol. The protocol version remains
`bifrost.transport.v1alpha1`; the Phase 3 store API adds frame types without
changing existing PUT, HAS, or GET frame meaning.

## Daemon protocol

The store API uses these frame types:

- `list_request`
- `list_result`
- `inspect_request`
- `inspect_result`
- `query_request`
- `query_result`
- `stats_request`
- `stats_result`
- `pin_request`
- `pin_result`
- `unpin_request`
- `unpin_result`
- `ttl_request`
- `ttl_result`
- `lifecycle_request`
- `lifecycle_result`

List and query requests carry a JSON `StoreObjectFilter` payload. Inspect and
stats requests have empty payloads. Result payloads are JSON and are stable for
tests and scripts.

Pin and unpin requests carry an `object_id` in the frame header and no payload.
TTL and lifecycle requests carry an `object_id` in the frame header and a small
JSON payload:

```json
{ "operation": "set", "expires_at_unix_ms": 1900000000000 }
{ "operation": "clear" }
{ "operation": "quarantine", "reason": "operator_requested" }
```

List, query, and inspect results only report servable objects. Staging,
missing, corrupt, quarantined, evicting, and catalog/filesystem-inconsistent
objects do not satisfy these availability APIs.

## Commands

```text
bifrost-store list --endpoint HOST:PORT [--state STATE] [--model-hash HASH] [--prefix-hash HASH] [--limit N] [--json]
bifrost-store inspect --endpoint HOST:PORT --object-id OBJECT_ID [--json]
bifrost-store query --endpoint HOST:PORT [--model-hash HASH] [--prefix-hash HASH] [--engine-name NAME] [--opaque-engine-key-hash HASH] [--layer-id N] [--kv-block-id N] [--json]
bifrost-store stats --endpoint HOST:PORT [--json]
bifrost-store pin --endpoint HOST:PORT --object-id OBJECT_ID
bifrost-store unpin --endpoint HOST:PORT --object-id OBJECT_ID
bifrost-store ttl set --endpoint HOST:PORT --object-id OBJECT_ID --expires-at-unix-ms N
bifrost-store ttl clear --endpoint HOST:PORT --object-id OBJECT_ID
bifrost-store quarantine --endpoint HOST:PORT --object-id OBJECT_ID --reason TEXT
```

Human-readable object output includes:

- `object_id`
- `object_type`
- `state`
- `byte_length`
- `prefix_hash`
- `layer_id`
- `kv_block_id`
- `pin_count`
- `last_accessed_unix_ms`

JSON list and query output:

```json
{
  "objects": [
    {
      "object_id": "bifrost://object/blake3/...",
      "object_type": "native_kv_page",
      "state": "verified",
      "byte_length": 786432,
      "model_hash": "blake3:...",
      "prefix_hash": "blake3:...",
      "engine_name": "bifrost-reference",
      "layer_id": 0,
      "kv_block_id": 0,
      "pin_count": 0,
      "last_accessed_unix_ms": 1779900000000
    }
  ]
}
```

JSON inspect output:

```json
{
  "found": true,
  "object": {
    "object_id": "bifrost://object/blake3/...",
    "object_type": "native_kv_page",
    "state": "verified",
    "byte_length": 786432,
    "prefix_hash": "blake3:...",
    "layer_id": 0,
    "kv_block_id": 0,
    "pin_count": 0,
    "last_accessed_unix_ms": 1779900000000
  },
  "descriptor_hash": "blake3:...",
  "payload_hash": "blake3:...",
  "schema_version": "bifrost.kv_object.v1alpha1",
  "created_at_unix_ms": 1779900000000,
  "committed_at_unix_ms": 1779900000000,
  "verified_at_unix_ms": 1779900000000,
  "files_present": true,
  "servable": true,
  "bytes_on_disk": 787000
}
```

JSON stats output includes catalog counts and byte totals:

```json
{
  "object_count": 1,
  "total_logical_bytes": 786432,
  "total_bytes_on_disk": 787000,
  "staging_count": 0,
  "committed_count": 0,
  "verified_count": 1,
  "pinned_count": 0,
  "evictable_count": 0,
  "evicting_count": 0,
  "evicted_count": 0,
  "quarantined_count": 0,
  "missing_count": 0,
  "corrupt_count": 0,
  "total_pin_count": 0,
  "total_access_count": 1
}
```

## Exit codes

- `0`: command succeeded.
- `1`: inspect did not find a servable object, query returned no matches, or a
  lifecycle operation was rejected by the store.
- `2`: usage, connection, I/O, protocol, or JSON error.

Pinning increments `pin_count`; unpinning decrements it and remains at zero if
the object is already unpinned. Objects with `pin_count > 0` are protected from
future eviction policies. Quarantined objects are not servable through HAS, GET,
list, query, or inspect availability APIs.

Eviction, manifests, and fsck are intentionally not exposed by this CLI yet.

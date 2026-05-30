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
- `evict_request`
- `evict_result`
- `manifest_request`
- `manifest_result`
- `fsck_request`
- `fsck_result`

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

Evict requests carry a JSON payload and no object ID:

```json
{
  "policy": "lru",
  "target_bytes": 1000000000,
  "max_objects": 100,
  "dry_run": true
}
```

Valid policies are `lru`, `size-aware-lru`, and `ttl-expired`.

Manifest requests carry a JSON payload tagged by operation:

```json
{ "operation": "create_prefix", "model_hash": "blake3:...", "prefix_hash": "blake3:...", "token_range_start": 0, "token_range_end": 128 }
{ "operation": "add_member", "manifest_id": "bifrost://manifest/blake3/...", "object_id": "bifrost://object/blake3/...", "required": true }
{ "operation": "inspect", "manifest_id": "bifrost://manifest/blake3/..." }
{ "operation": "list", "filter": { "prefix_hash": "blake3:..." } }
{ "operation": "check", "manifest_id": "bifrost://manifest/blake3/..." }
{ "operation": "pin", "manifest_id": "bifrost://manifest/blake3/..." }
{ "operation": "unpin", "manifest_id": "bifrost://manifest/blake3/..." }
```

Fsck requests carry a JSON payload and no object ID:

```json
{ "mode": "check" }
{ "mode": "repair" }
{ "mode": "quarantine" }
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
bifrost-store evict --endpoint HOST:PORT --policy lru|size-aware-lru|ttl-expired [--target-bytes N] [--max-objects N] [--dry-run] [--json]
bifrost-store fsck --endpoint HOST:PORT [--check|--repair|--quarantine] [--json]
bifrost-store manifest create-prefix --endpoint HOST:PORT --prefix-hash HASH --model-hash HASH --token-range-start N --token-range-end N [--tokenizer-hash HASH] [--rope-config-hash HASH] [--json]
bifrost-store manifest add-member --endpoint HOST:PORT --manifest-id ID --object-id OBJECT_ID [--required true|false]
bifrost-store manifest inspect --endpoint HOST:PORT --manifest-id ID [--json]
bifrost-store manifest list --endpoint HOST:PORT [--prefix-hash HASH] [--json]
bifrost-store manifest check --endpoint HOST:PORT --manifest-id ID [--json]
bifrost-store manifest pin --endpoint HOST:PORT --manifest-id ID
bifrost-store manifest unpin --endpoint HOST:PORT --manifest-id ID
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

JSON eviction output includes the policy, dry-run flag, target, starting and
final bytes, deterministic candidates, applied evictions, failures, protected
pinned count, skipped unsafe count, and final reason:

```json
{
  "policy": "lru",
  "dry_run": true,
  "target_bytes": 1000000000,
  "starting_bytes_on_disk": 1500000000,
  "final_bytes_on_disk": 1500000000,
  "planned_bytes": 600000000,
  "freed_bytes": 0,
  "candidates": [
    {
      "object_id": "bifrost://object/blake3/...",
      "state": "verified",
      "bytes_on_disk": 600000000,
      "byte_length": 599000000,
      "last_accessed_unix_ms": 1779900000000,
      "ttl_expires_at_unix_ms": null,
      "eviction_score": 0
    }
  ],
  "evicted": [],
  "failures": [],
  "protected_pinned_count": 0,
  "skipped_unsafe_count": 0,
  "target_reached": true,
  "reason": "dry_run"
}
```

JSON fsck output includes status, structured findings, severity counts,
applied mutations, and warnings:

```json
{
  "status": "dirty",
  "findings": [
    {
      "finding_type": "catalog_object_missing_payload_file",
      "severity": "error",
      "object_id": "bifrost://object/blake3/...",
      "path": "/store/objects/...",
      "message": "catalog object is missing its payload file",
      "suggested_action": "repair should mark the object missing"
    }
  ],
  "counts_by_severity": { "error": 1 },
  "mutations_applied": [],
  "warnings": []
}
```

JSON manifest output uses a common envelope:

```json
{
  "status": "ok",
  "reason": "",
  "manifest": {
    "manifest": {
      "manifest_id": "bifrost://manifest/blake3/...",
      "manifest_type": "prefix_manifest",
      "model_hash": "blake3:...",
      "prefix_hash": "blake3:...",
      "token_range_start": 0,
      "token_range_end": 128,
      "completeness_state": "complete",
      "created_at_unix_ms": 1779900000000,
      "updated_at_unix_ms": 1779900000000,
      "pin_count": 0
    },
    "members": [
      {
        "manifest_id": "bifrost://manifest/blake3/...",
        "object_id": "bifrost://object/blake3/...",
        "layer_id": 0,
        "kv_block_id": 0,
        "token_range_start": 0,
        "token_range_end": 128,
        "required": true
      }
    ]
  },
  "completeness": {
    "manifest_id": "bifrost://manifest/blake3/...",
    "completeness_state": "complete",
    "required_count": 1,
    "serveable_required_count": 1,
    "missing": []
  }
}
```

## Exit codes

- `0`: command succeeded.
- `1`: inspect did not find a servable object, query returned no matches, fsck
  found findings, or a lifecycle operation was rejected by the store.
- `2`: usage, connection, I/O, protocol, or JSON error.

Pinning increments `pin_count`; unpinning decrements it and remains at zero if
the object is already unpinned. Objects with `pin_count > 0` are protected from
every eviction policy. Quarantined objects are not servable through HAS, GET,
list, query, or inspect availability APIs and are not normal eviction
candidates.

Manifest pinning increments the manifest `pin_count` and increments `pin_count`
on required member objects, so existing deterministic eviction candidate
selection automatically skips those members. Unpinning reverses that protection.

Fsck defaults to `--check`; choose at most one of `--check`, `--repair`, or
`--quarantine`.

# Phase 5 LMCache Connector Contract

Last verified: 2026-06-14

## Purpose

This document defines the LMCache remote storage connector contract for
BIFROST. It is a design contract, not implementation code.

The connector has two public classes:

```text
BifrostConnectorAdapter:
  LMCache plugin adapter responsible for URL recognition and connector
  construction.

BifrostRemoteConnector:
  LMCache remote connector responsible for storing, finding, loading, listing,
  and closing BIFROST-backed opaque objects.
```

## BifrostConnectorAdapter

`BifrostConnectorAdapter` is the plugin loading boundary.

Responsibilities:

1. Advertise the BIFROST remote storage plugin type.
2. Recognize supported BIFROST URL schemes.
3. Parse connector configuration from the LMCache connector context.
4. Construct a configured `BifrostRemoteConnector`.
5. Reject unknown schemes or incomplete configuration deterministically.

The adapter must not perform store reads or writes. It may validate static
configuration and construct client objects.

## Supported URL schemes

Phase 5 supports:

```text
bifrost://HOST:PORT
bifrost+tcp://HOST:PORT
plugin://bifrost?endpoint=HOST:PORT
```

`bifrost://` is the preferred user-facing scheme. `bifrost+tcp://` is allowed
when configuration needs to make the transport explicit. `plugin://bifrost` is
accepted for LMCache plugin configurations that route through a generic plugin
scheme.

Unsupported schemes must be rejected by the adapter.

## BifrostRemoteConnector

`BifrostRemoteConnector` maps LMCache operations to BIFROST opaque object
operations. It must treat LMCache objects as opaque bytes and must not inspect
or reinterpret tensor layout.

Required construction inputs:

```text
endpoint:
  daemon endpoint or local test transport endpoint

namespace:
  logical namespace for LMCache objects, default "lmcache"

engine_name:
  "lmcache"

engine_version:
  detected or configured LMCache version, or "unknown" for fake tests

integration_name:
  "lmcache_bifrost_remote_storage"

timeout:
  connector operation timeout

strict_validation:
  whether descriptor validation failures raise or return misses according to
  method semantics
```

## Required methods

### exists

```text
async exists(key: CacheEngineKey) -> bool
```

Returns `True` only when a committed, verified BIFROST
`opaque_engine_blob` exists for the key hash and passes key, descriptor, and
payload integrity checks.

Returns `False` for missing, corrupt, incompatible, evicted, quarantined,
staged, or uncertain objects.

### exists_sync

```text
exists_sync(key: CacheEngineKey) -> bool
```

Synchronous equivalent of `exists`. It must use the same key hashing,
validation, and fail-closed behavior as the async method.

### get

```text
async get(key: CacheEngineKey) -> MemoryObj | None
```

Returns an LMCache `MemoryObj` only when:

1. The key can be canonicalized and hashed.
2. A matching committed object is found.
3. Descriptor and payload integrity verify.
4. The stored `opaque_engine_key_hash` matches the requested key hash.
5. The payload can be deserialized through the LMCache-owned serializer.

Returns `None` for cache misses and unavailable objects. Deterministic
connector errors may be raised for local configuration errors, closed connector
state, or serialization contract violations.

### put

```text
async put(key: CacheEngineKey, memory_obj: MemoryObj) -> None
```

Serializes the LMCache `MemoryObj`, builds an `opaque_engine_blob` descriptor,
validates descriptor and payload, and commits the verified object to BIFROST.

`put` must fail closed. If serialization, descriptor construction, validation,
or store commit fails, no object may become visible as an available cache hit.

### list

```text
async list() -> list[str]
```

Lists stable key representations for committed and verified LMCache objects in
the connector namespace.

The list result is advisory. Callers must still use `get` or `exists` for
integrity-checked access.

### close

```text
async close() -> None
```

Releases client resources and marks the connector closed. After `close`,
`exists`, `exists_sync`, `get`, `put`, and `list` must fail deterministically or
return misses according to the final implementation contract. They must not
silently reopen a connector unless that behavior is explicitly documented and
tested.

## Optional methods

### ping

```text
async ping() -> bool
```

Checks connectivity to the configured BIFROST endpoint. `ping` must not create
objects or mutate store state.

### Batched operations

Optional batched operations may be added after the single-key methods are
correct:

```text
async exists_many(keys: list[CacheEngineKey]) -> list[bool]
async get_many(keys: list[CacheEngineKey]) -> list[MemoryObj | None]
async put_many(items: list[tuple[CacheEngineKey, MemoryObj]]) -> None
```

Batched methods must preserve per-key fail-closed behavior. A corrupt or
unavailable object must not poison unrelated valid objects, and partial write
visibility rules must match single-key `put`.

## Failure semantics

The connector must distinguish:

1. Cache miss.
2. Key canonicalization error.
3. LMCache serialization error.
4. LMCache deserialization error.
5. BIFROST descriptor validation error.
6. Payload integrity error.
7. Object ID mismatch.
8. Store commit error.
9. Store retrieval error.
10. Closed connector error.
11. Invalid connector configuration.

`exists` and `exists_sync` should return `False` for object-level uncertainty.
`get` should return `None` for normal misses and unavailable objects. `put`
should raise a deterministic connector error when the object cannot be safely
committed.

No method may return a `MemoryObj` or `True` based only on a catalog row,
manifest row, local path, or unverified payload.

# Phase 5 LMCache Connector Contract

Last verified: 2026-06-14

## Purpose

This document defines the LMCache remote storage connector contract for
BIFROST and records the Phase 5 implementation behavior.

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

## LMCache compatibility boundary

The integration must import without LMCache installed. Optional LMCache imports
are isolated in `lmcache_bifrost.lmcache_compat`, which probes for:

```text
ConnectorAdapter
ConnectorContext
RemoteConnector
CacheEngineKey
MemoryObj
LMCacheEngineConfig
LMCacheMetadata
```

`has_lmcache()` returns whether the `lmcache` package can be imported.
`lmcache_version()` returns a version string when one is discoverable, otherwise
`None`. Importing `lmcache_bifrost` must not raise only because LMCache is
absent; errors belong in code paths that actually require real LMCache classes
or native serialization APIs.

CI-safe tests use fake `CacheEngineKey`, `MemoryObj`, config, metadata, and
connector context classes. These fakes are only test fixtures. They may exercise
the pickle fallback when `allow_pickle_fallback` is enabled, but production
paths must prefer LMCache-native serialization and must fail closed when no
native API is discoverable.

Memory object serialization capability detection returns one of:

```text
lmcache_native:
  A bytes-returning LMCache object method such as to_bytes, serialize, or dumps
  was found.

pickle_fallback:
  A known fake CI MemoryObj fixture was detected. This is test-only and still
  requires explicit config opt-in before serialization.

unsupported:
  No safe native API was discovered. The connector must treat this as a
  serialization miss or deterministic serialization error according to the
  method contract, not guess a payload format.
```

## Supported URL schemes

Phase 5 supports:

```text
bifrost://HOST:PORT
bifrost+tcp://HOST:PORT
plugin://bifrost?endpoint=HOST:PORT
plugin://bifrost.INSTANCE_NAME?endpoint=HOST:PORT
```

`bifrost://` is the preferred user-facing scheme. `bifrost+tcp://` is allowed
when configuration needs to make the transport explicit. `plugin://bifrost` is
accepted for LMCache plugin configurations that route through a generic plugin
scheme.

Unsupported schemes must be rejected by the adapter.

## Adapter configuration parsing

`BifrostConnectorAdapter` parses:

```text
endpoint:
  From bifrost://HOST:PORT, bifrost+tcp://HOST:PORT, endpoint query
  parameter, or endpoint/bifrost_endpoint in LMCache extra_config.

chunk_size:
  From the URL query, LMCache extra_config, LMCache config object, or the
  BIFROST default.

allow_pickle_fallback:
  From LMCache extra_config or URL query. This must remain false for production
  configurations and is intended for fake CI MemoryObj fixtures.

timeout_seconds:
  From the URL query, LMCache extra_config, LMCache config object, or the
  BIFROST default.

strict_validation:
  From the URL query, LMCache extra_config, LMCache config object, or the
  BIFROST default.
```

Direct BIFROST URLs require both host and port. `plugin://bifrost` URLs require
an endpoint query parameter or equivalent extra configuration because the
generic plugin URL does not carry a daemon endpoint by itself.

An example LMCache YAML file lives at
`integrations/lmcache_bifrost/examples/lmcache_config_bifrost.yaml`. It is an
example only; users must verify the exact plugin configuration shape against
their installed LMCache version.

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

The connector uses `bifrost_client.BifrostAsyncClient` for async methods and a
lazy `bifrost_client.BifrostClient` for `exists_sync` when it owns the client.
Fake tests may inject a client object exposing the same `put_object`,
`query_by_opaque_key_hash`, `get_object`, `list_objects`, and `close` surface.

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

The implementation queries by `engine_name`, `integration_name`, and
`opaque_engine_key_hash`, then GETs candidates and validates them against the
requested key target profile. Corrupt or incompatible candidates are treated as
misses for `exists`.

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

The implementation raises `BifrostLMCacheValidationError` for corrupt,
descriptor-mismatched, payload-hash-mismatched, or wrong-key objects. Missing
objects remain ordinary LMCache misses and return `None`.

### put

```text
async put(key: CacheEngineKey, memory_obj: MemoryObj) -> None
```

Serializes the LMCache `MemoryObj`, builds an `opaque_engine_blob` descriptor,
validates descriptor and payload, and commits the verified object to BIFROST.

`put` must fail closed. If serialization, descriptor construction, validation,
or store commit fails, no object may become visible as an available cache hit.

The implementation validates generated metadata and payload through the Phase 1
Python validator before sending to the daemon, then requires the client PUT
result to report both `stored` and `verified`.

### list

```text
async list() -> list[str]
```

Lists stable key representations for committed and verified LMCache objects in
the connector namespace.

The list result is advisory. Callers must still use `get` or `exists` for
integrity-checked access.

The implementation returns sorted entries of the form:

```text
lmcache:{opaque_engine_key_hash}
```

Each listed object is fetched and validated before its hash is returned.

### close

```text
async close() -> None
```

Releases client resources and marks the connector closed. After `close`,
`exists`, `exists_sync`, `get`, `put`, and `list` must fail deterministically or
return misses according to the final implementation contract. They must not
silently reopen a connector unless that behavior is explicitly documented and
tested.

The implementation makes `close` idempotent. After close, connector operations
raise `ConnectorConfigurationError`.

## Optional methods

### ping

```text
support_ping() -> bool
async ping() -> bool
```

`support_ping` returns `True`. `ping` checks connectivity to the configured
BIFROST endpoint by calling the BIFROST client `ping` API, or `stats` when an
injected test client only exposes that diagnostic path. `ping` must not create
objects or mutate store state. A live daemon returns `True`; connection,
protocol, or client errors raise `BifrostLMCacheStoreError` with a
`ping_failed` reason prefix.

### Batched operations

Optional batched operations may be added after the single-key methods are
correct:

```text
support_batched_contains() -> bool
async batched_contains(keys: list[CacheEngineKey]) -> list[bool]

support_batched_get() -> bool
async batched_get(keys: list[CacheEngineKey]) -> list[MemoryObj | None]

support_batched_put() -> bool
async batched_put(items: list[tuple[CacheEngineKey, MemoryObj]]) -> None
```

The support methods return `True` for the current fake-compatible Phase 5
surface. Real LMCache batched return-type expectations vary by version and are
treated as experimental until pinned by optional real-LMCache compatibility
tests.

The current implementation performs one verified single-key operation per
input because the BIFROST daemon does not expose a server-side batch query or
batch commit protocol. This is intentionally conservative: `batched_contains`
uses `exists`, `batched_get` uses `get`, and `batched_put` uses `put`.

Batched methods must preserve per-key fail-closed behavior. Missing keys return
`False` for `batched_contains` and `None` for `batched_get`. Corrupt,
incompatible, or uncertain hits follow the same behavior as single-key
`exists` and `get`. `batched_put` stops at the first failed item and raises the
underlying deterministic connector error type with a
`batched_put_failed:index=N:reason=...` message. Items committed before the
failure remain subject to ordinary single-key commit rules; later items are not
silently reported as stored.

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

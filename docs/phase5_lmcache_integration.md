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

## Plugin roundtrip example

A daemon-backed plugin smoke example lives at
`examples/lmcache_bifrost/plugin_roundtrip.py`. It constructs a fake
`CacheEngineKey` and fake `MemoryObj` by default, instantiates
`BifrostRemoteConnector`, then runs `put`, `exists`, `get`, and `list` against a
local `bifrostd`.

The fake `MemoryObj` path intentionally requires explicit pickle fallback:

```bash
python examples/lmcache_bifrost/plugin_roundtrip.py \
  --endpoint 127.0.0.1:7744 \
  --allow-pickle-fallback
```

JSON output is available for smoke tests and automation:

```bash
python examples/lmcache_bifrost/plugin_roundtrip.py \
  --endpoint 127.0.0.1:7744 \
  --allow-pickle-fallback \
  --json
```

The output includes:

```text
status
endpoint
key_repr
opaque_engine_key_hash
object_id
put_success
exists_result
get_success
list_count
payload_roundtrip_match
fsck_status
```

When `bifrost-store` is available, the script also reports an `fsck_status`.
Store stats are included as best-effort diagnostics. These diagnostics do not
change the connector correctness rule: only committed and verified objects may
satisfy LMCache hits.

## Optional real LMCache smoke

Real LMCache coverage is optional and must be explicitly enabled. The pytest
module `integrations/lmcache_bifrost/tests/test_real_lmcache_optional.py`
skips unless both conditions are true:

```bash
export BIFROST_RUN_REAL_LMCACHE_TESTS=1
python -c "import lmcache"
```

Run it with:

```bash
PYTHONPATH=bifrost_py:integrations/lmcache_bifrost \
  BIFROST_RUN_REAL_LMCACHE_TESTS=1 \
  pytest integrations/lmcache_bifrost/tests/test_real_lmcache_optional.py
```

These tests probe real LMCache imports, adapter construction, connector method
compatibility, and construction of `BifrostRemoteConnector` with a minimal
LMCache-shaped context. They do not require GPU hardware, vLLM, Hugging Face
tokens, internet access, model downloads, or a running BIFROST daemon.

A standalone compatibility script lives at
`examples/lmcache_bifrost/real_lmcache_smoke.py`:

```bash
python examples/lmcache_bifrost/real_lmcache_smoke.py --compat-only
python examples/lmcache_bifrost/real_lmcache_smoke.py --compat-only --json
```

The script reports the LMCache version, discovered compatibility classes,
connector method availability, adapter construction, and connector construction.
If a real `MemoryObj` can be constructed through a CPU-safe public no-argument
constructor, it attempts native byte serialization and deserialization through
the BIFROST opaque blob codec. If that public construction or native
deserialization route is unavailable, `--compat-only` exits with status 0 and
reports `compatibility only`.

For LMCache versions where valid `MemoryObj` construction requires a live
LMCache runtime, run the script with an explicit local factory:

```bash
PYTHONPATH=bifrost_py:integrations/lmcache_bifrost:/path/to/factory \
  python examples/lmcache_bifrost/real_lmcache_smoke.py \
  --memoryobj-factory my_lmcache_factory:make_memory_obj \
  --json
```

The factory must return a CPU-safe real LMCache `MemoryObj` and must not
download models, contact external services, require GPU hardware, or use
private LMCache APIs.

## LMCache configuration examples

Example LMCache configuration files live under
`integrations/lmcache_bifrost/examples/`:

```text
lmcache_config_bifrost.yaml
lmcache_config_bifrost_pickle_dev.yaml
```

`lmcache_config_bifrost.yaml` shows the production-shaped plugin registration,
including `remote_storage_plugins`, `module_path`, `class_name`, and a
`bifrost://HOST:PORT` remote URL.

`lmcache_config_bifrost_pickle_dev.yaml` is local development only. It enables
`allow_pickle_fallback` for fake/demo objects and must not be used with
untrusted payloads or production LMCache traffic.

LMCache plugin YAML fields have changed across releases. Treat these files as
version-sensitive examples and verify the exact configuration shape against the
installed LMCache version.

## Connector observability

`BifrostRemoteConnector` keeps lightweight in-process counters for local
debugging and tests:

```python
snapshot = connector.metrics_snapshot()
```

The snapshot includes operation counts, error counts, bytes moved, and total
`put`/`get` wall-clock milliseconds:

```text
put_count
get_count
exists_count
list_count
close_count
put_error_count
get_error_count
serialization_error_count
validation_error_count
bytes_put
bytes_get
total_put_ms
total_get_ms
```

These counters are connector-local process state. They are not part of
immutable BIFROST object identity and are not used to decide cache correctness.

Optional JSONL connector events can be enabled with `metrics_jsonl_path` in the
adapter query string, LMCache extra config, or direct `BifrostLMCacheConfig`:

```text
bifrost://127.0.0.1:7744?metrics_jsonl_path=/tmp/bifrost-lmcache.jsonl
```

Each line is valid JSON and includes `timestamp_unix_ms`, `operation`, and any
available `opaque_engine_key_hash`, `object_id`, `bytes`, `duration_ms`, and
`reason_code`. Event names are:

```text
connector_put_started
connector_put_completed
connector_get_started
connector_get_completed
connector_exists
connector_error
```

Store-level inspection remains separate. `bifrost-store inspect` exposes the
object type, LMCache engine and integration names, opaque engine key hash, byte
length, state, pin count, last access time, and payload hash for opaque objects.

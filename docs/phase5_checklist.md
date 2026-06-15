# Phase 5 Checklist

Last verified: 2026-06-15

## Python client

- [x] Define async `BifrostClient` opaque object methods.
- [x] Define sync wrappers needed by LMCache `exists_sync`.
- [x] Add daemon endpoint configuration and timeouts.
- [x] Add protocol/version compatibility checks.
- [x] Ensure committed and verified objects are the only available hits.
- [x] Return misses for missing, corrupt, staged, evicted, quarantined, or
  uncertain objects.
- [x] Add specific client errors for protocol, validation, store, integrity,
  key mismatch, timeout, connection, configuration, and closed-client cases.
- [x] Test async put/get/exists/list roundtrip with a local fake or daemon.
- [x] Test sync exists without event-loop deadlock.

## Opaque blob codec

- [x] Define canonical LMCache `CacheEngineKey` representation.
- [x] Compute `opaque_engine_key_hash` with a domain-separated hash input.
- [x] Add optional LMCache compatibility imports that do not break CI when
  LMCache is absent.
- [x] Detect LMCache-native, fake pickle fallback, and unsupported MemoryObj
  serialization capabilities.
- [x] Serialize fake LMCache `MemoryObj` fixtures to payload bytes.
- [x] Deserialize payload bytes back to fake `MemoryObj` fixtures.
- [x] Generate `opaque_engine_blob` descriptors for LMCache objects.
- [x] Populate LMCache engine and integration metadata.
- [x] Leave unknown native tensor compatibility fields null or absent.
- [x] Compute payload hash, descriptor hash, and object ID with Phase 1 rules.
- [x] Validate descriptors and payloads through Python reference validation.
- [x] Validate descriptors and payloads through Rust mirror validation.
- [x] Test key mismatch, payload hash mismatch, descriptor mismatch, and object
  ID mismatch.
- [x] Test unsupported schema rejection for generated opaque blobs.

## Connector adapter

- [x] Define `BifrostConnectorAdapter`.
- [x] Support `bifrost://HOST:PORT`.
- [x] Support `bifrost+tcp://HOST:PORT`.
- [x] Support `plugin://bifrost?endpoint=HOST:PORT`.
- [x] Support `plugin://bifrost.INSTANCE_NAME` naming.
- [x] Reject unsupported URL schemes.
- [x] Parse endpoint, chunk size, pickle fallback, timeout, and
  strict-validation settings.
- [x] Construct `BifrostRemoteConnector`.
- [x] Test adapter loading with fake LMCache interfaces.
- [x] Test invalid configuration fails deterministically.

## Remote connector

- [x] Define `BifrostRemoteConnector`.
- [x] Implement async `exists`.
- [x] Implement sync `exists_sync`.
- [x] Implement async `put`.
- [x] Implement async `get`.
- [x] Implement async `list`.
- [x] Implement async `close`.
- [x] Add optional `ping` if useful for diagnostics.
- [x] Add batched operations only after single-key methods are correct.
- [x] Ensure `put` never exposes partial writes.
- [x] Ensure `get` never returns a `MemoryObj` from unverified bytes.
- [x] Ensure `list` returns only committed and verified LMCache opaque objects.
- [x] Test closed-connector behavior.
- [x] Add in-process connector counters and `metrics_snapshot()`.
- [x] Add optional JSONL connector operation logging.
- [x] Test serialization, validation, store, retrieval, and key mismatch
  failures.
- [x] Add daemon-backed plugin roundtrip example for `put`, `exists`, `get`,
  and `list`.

## Rust opaque key store support

- [x] Add a mutable `opaque_key_index` sidecar table without changing Phase 1
  immutable object identity.
- [x] Backfill the sidecar index from existing opaque compatibility rows during
  migration.
- [x] Populate the opaque key index when committed opaque objects enter the
  store catalog.
- [x] Add catalog/store APIs for lookup by `engine_name`, `integration_name`,
  and `opaque_engine_key_hash`.
- [x] Add catalog/store APIs for listing opaque keys with optional engine and
  integration filters.
- [x] Track opaque key access time as mutable local catalog state.
- [x] Expose opaque key query/list through daemon protocol frames.
- [x] Add `bifrost-store opaque list` and `bifrost-store opaque get-key`.
- [x] Keep `bifrost-store query --opaque-engine-key-hash` available.
- [x] Ensure `bifrost-store inspect` shows opaque engine key fields and
  payload hash.
- [x] Ensure evicted and quarantined objects do not satisfy opaque key hits.
- [x] Add fsck detection for opaque key index rows pointing at missing objects.
- [x] Test opaque key migration idempotency.

## Fake LMCache tests

- [x] Add fake `CacheEngineKey` with stable canonical representation.
- [x] Add fake `MemoryObj` with deterministic bytes.
- [x] Add fake LMCache config, metadata, and connector context fixtures.
- [x] Run fake LMCache codec/compat tests in CI without importing LMCache.
- [x] Test fake put/get roundtrip through the connector.
- [x] Test fake `exists` and `exists_sync`.
- [x] Test fake ping and batched contains/get/put behavior.
- [x] Test fake list behavior.
- [x] Test missing key returns miss.
- [x] Test corrupt committed payload fails closed.
- [x] Test connector metrics and JSONL logging without real LMCache.
- [x] Test descriptor key hash mismatch fails closed.
- [x] Test serialization failure does not create a visible hit.
- [x] Run fake LMCache tests in CI without installing LMCache.
- [x] Test plugin roundtrip script JSON output, missing-daemon failure, and
  fake-object pickle fallback opt-in.

## Optional real LMCache tests

- [x] Detect whether LMCache is installed.
- [x] Skip with a clear reason when LMCache is unavailable.
- [x] Probe LMCache version and connector API shape.
- [x] Construct real or minimal LMCache-shaped connector context objects.
- [ ] Construct real LMCache key objects.
- [ ] Construct real LMCache memory objects when a CPU-safe public API is
  available.
- [x] Test adapter construction against a real LMCache install when opted in.
- [ ] Test put/get roundtrip when real APIs are available.
- [x] Add standalone real LMCache compatibility smoke script.
- [x] Document manual MemoryObj factory command for real LMCache environments.
- [x] Keep real LMCache tests out of required CI unless explicitly configured.

## Optional vLLM smoke

- [x] Detect whether vLLM is installed.
- [x] Detect whether LMCache is installed.
- [x] Require an explicit opt-in flag or environment variable.
- [x] Require local model path; do not download models or tokenizers.
- [x] Connect to a local `bifrostd` endpoint for readiness and before/after
  stats.
- [x] Provide scaffold config for LMCache to use BIFROST remote storage.
- [x] Scaffold first request and observe BIFROST opaque puts when explicitly
  opted in.
- [x] Scaffold repeated or overlapping request and observe access-count changes
  when explicitly opted in.
- [x] Emit JSON summary with versions, object counts, bytes, hits, misses, and
  skip reason.
- [x] Skip by default in CI and local test runs.

## ContextStorm LMCache workload

- [x] Add local deterministic opaque-object workload.
- [x] Generate fake LMCache key and memory object distributions.
- [x] Measure put, exists, get, list, validation, and store latency.
- [x] Record payload bytes, object counts, miss count, validation error count,
  BIFROST store object count, fsck status, and optional batch timings.
- [x] Include missing-key scenario.
- [x] Include corrupt object scenario.
- [x] Keep default scenario CPU-only and local.
- [x] Avoid LMCache, vLLM, GPU, downloads, cloud credentials, Docker,
  Kubernetes, and internet access by default.

## CI

- [x] Run fake LMCache connector tests.
- [x] Run opaque blob validation tests.
- [x] Run Python client fake/local tests.
- [x] Parse LMCache BIFROST example YAML in tests.
- [x] Install `integrations/lmcache_bifrost` in editable mode.
- [x] Build Rust binaries before daemon-backed Python and ContextStorm tests.
- [x] Run `cargo test --manifest-path bifrostd/Cargo.toml`.
- [x] Run `integrations/lmcache_bifrost` pytest fake tests.
- [x] Run ContextStorm `lmcache_connector_small_ci`.
- [x] Run stable small ContextStorm transport, store, and model scenarios.
- [x] Keep real LMCache tests skipped unless dependency is installed and
  `BIFROST_RUN_REAL_LMCACHE_TESTS=1` is set in a dedicated optional job.
- [x] Keep vLLM smoke skipped unless explicitly opted in.
- [x] Preserve Phase 1 parity tests.
- [x] Preserve Phase 2 transport tests.
- [x] Preserve Phase 3 store tests.
- [x] Preserve Phase 4 tiny-transformer correctness tests.

## Phase 5 local commands

Default CI-equivalent setup:

```bash
python -m pip install -e "bifrost_py[dev]" -e "contextstorm[dev]" -e "integrations/lmcache_bifrost[dev]"
cargo build --manifest-path bifrostd/Cargo.toml --bins
cargo test --manifest-path bifrostd/Cargo.toml
```

Run integration fake tests:

```bash
BIFROST_RUN_REAL_LMCACHE_TESTS=0 pytest integrations/lmcache_bifrost/tests
```

Run optional real LMCache tests after installing LMCache:

```bash
BIFROST_RUN_REAL_LMCACHE_TESTS=1 pytest integrations/lmcache_bifrost/tests/test_real_lmcache_optional.py
python examples/lmcache_bifrost/real_lmcache_smoke.py --compat-only --json
```

Run optional vLLM smoke only with local dependencies and a local model path:

```bash
BIFROST_RUN_VLLM_SMOKE=1 python examples/lmcache_bifrost/vllm_lmcache_smoke.py --run --model /path/to/local/model --json
```

Run ContextStorm LMCache small scenario:

```bash
contextstorm run contextstorm/scenarios/lmcache_connector_small_ci.yaml \
  --runs-root /tmp/contextstorm-runs \
  --run-id phase5-local-lmcache-connector-small
```

## Phase 5 done criteria

- [x] LMCache objects are stored only as `opaque_engine_blob`.
- [x] BIFROST never reinterprets LMCache tensor semantics.
- [x] `CacheEngineKey` maps to stable `opaque_engine_key_hash`.
- [x] `MemoryObj` payload bytes roundtrip through BIFROST.
- [x] Connector required methods are implemented and tested.
- [x] Fake LMCache tests run in CI.
- [x] Optional real LMCache tests skip cleanly when LMCache is unavailable.
- [x] Optional vLLM smoke is documented, opt-in, and skipped by default.
- [x] Corruption, mismatch, missing object, and store failure cases fail closed.
- [x] ContextStorm has a local LMCache-style opaque workload.
- [x] Phase 1 through Phase 4 required tests remain green.

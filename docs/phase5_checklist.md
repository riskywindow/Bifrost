# Phase 5 Checklist

Last verified: 2026-06-14

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

- [ ] Define canonical LMCache `CacheEngineKey` representation.
- [ ] Compute `opaque_engine_key_hash` with a domain-separated hash input.
- [ ] Serialize fake LMCache `MemoryObj` fixtures to payload bytes.
- [ ] Deserialize payload bytes back to fake `MemoryObj` fixtures.
- [ ] Generate `opaque_engine_blob` descriptors for LMCache objects.
- [ ] Populate LMCache engine and integration metadata.
- [ ] Leave unknown native tensor compatibility fields null or absent.
- [ ] Compute payload hash, descriptor hash, and object ID with Phase 1 rules.
- [ ] Validate descriptors and payloads through Python reference validation.
- [ ] Validate descriptors and payloads through Rust mirror validation.
- [ ] Test key mismatch, payload hash mismatch, descriptor mismatch, object ID
  mismatch, and unsupported schema rejection.

## Connector adapter

- [ ] Define `BifrostConnectorAdapter`.
- [ ] Support `bifrost://HOST:PORT`.
- [ ] Support `bifrost+tcp://HOST:PORT`.
- [ ] Support `plugin://bifrost?endpoint=HOST:PORT`.
- [ ] Reject unsupported URL schemes.
- [ ] Parse namespace, endpoint, timeout, and strict-validation settings.
- [ ] Construct `BifrostRemoteConnector`.
- [ ] Test adapter loading with fake LMCache interfaces.
- [ ] Test invalid configuration fails deterministically.

## Remote connector

- [ ] Define `BifrostRemoteConnector`.
- [ ] Implement async `exists`.
- [ ] Implement sync `exists_sync`.
- [ ] Implement async `put`.
- [ ] Implement async `get`.
- [ ] Implement async `list`.
- [ ] Implement async `close`.
- [ ] Add optional `ping` if useful for diagnostics.
- [ ] Add batched operations only after single-key methods are correct.
- [ ] Ensure `put` never exposes partial writes.
- [ ] Ensure `get` never returns a `MemoryObj` from unverified bytes.
- [ ] Ensure `list` returns only committed and verified LMCache opaque objects.
- [ ] Test closed-connector behavior.
- [ ] Test serialization, validation, store, retrieval, and key mismatch
  failures.

## Fake LMCache tests

- [ ] Add fake `CacheEngineKey` with stable canonical representation.
- [ ] Add fake `MemoryObj` with deterministic bytes.
- [ ] Test fake put/get roundtrip through the connector.
- [ ] Test fake `exists` and `exists_sync`.
- [ ] Test fake list behavior.
- [ ] Test missing key returns miss.
- [ ] Test corrupt committed payload fails closed.
- [ ] Test descriptor key hash mismatch fails closed.
- [ ] Test serialization failure does not create a visible hit.
- [ ] Run fake LMCache tests in CI without installing LMCache.

## Optional real LMCache tests

- [ ] Detect whether LMCache is installed.
- [ ] Skip with a clear reason when LMCache is unavailable.
- [ ] Probe LMCache version and connector API shape.
- [ ] Construct real or minimal LMCache key objects.
- [ ] Construct real or minimal LMCache memory objects.
- [ ] Test adapter import through LMCache plugin loading when possible.
- [ ] Test put/get roundtrip when real APIs are available.
- [ ] Keep real LMCache tests out of required CI unless explicitly configured.

## Optional vLLM smoke

- [ ] Detect whether vLLM is installed.
- [ ] Detect whether LMCache is installed.
- [ ] Require an explicit opt-in flag or environment variable.
- [ ] Require local model path; do not download models or tokenizers.
- [ ] Start or connect to a local `bifrostd`.
- [ ] Configure LMCache to use BIFROST remote storage.
- [ ] Run first request and observe BIFROST opaque puts.
- [ ] Run repeated or overlapping request and observe exists/get calls.
- [ ] Emit JSON summary with versions, object counts, bytes, hits, misses, and
  skip reason.
- [ ] Skip by default in CI and local test runs.

## ContextStorm LMCache workload

- [ ] Add local deterministic opaque-object workload.
- [ ] Generate fake LMCache key and memory object distributions.
- [ ] Measure put, exists, get, list, validation, and store latency.
- [ ] Record payload bytes, object counts, hit rate, miss rate, corrupt
  rejection count, and store error count.
- [ ] Include missing and corrupt object scenarios.
- [ ] Keep default scenario CPU-only and local.
- [ ] Avoid LMCache, vLLM, GPU, downloads, cloud credentials, Docker,
  Kubernetes, and internet access by default.

## CI

- [ ] Run fake LMCache connector tests.
- [ ] Run opaque blob validation tests.
- [ ] Run Python client fake/local tests.
- [ ] Keep real LMCache tests skipped unless dependency is installed in a
  dedicated optional job.
- [ ] Keep vLLM smoke skipped unless explicitly opted in.
- [ ] Preserve Phase 1 parity tests.
- [ ] Preserve Phase 2 transport tests.
- [ ] Preserve Phase 3 store tests.
- [ ] Preserve Phase 4 tiny-transformer correctness tests.

## Phase 5 done criteria

- [ ] LMCache objects are stored only as `opaque_engine_blob`.
- [ ] BIFROST never reinterprets LMCache tensor semantics.
- [ ] `CacheEngineKey` maps to stable `opaque_engine_key_hash`.
- [ ] `MemoryObj` payload bytes roundtrip through BIFROST.
- [ ] Connector required methods are implemented and tested.
- [ ] Fake LMCache tests run in CI.
- [ ] Optional real LMCache tests skip cleanly when LMCache is unavailable.
- [ ] Optional vLLM smoke is documented, opt-in, and skipped by default.
- [ ] Corruption, mismatch, missing object, and store failure cases fail closed.
- [ ] ContextStorm has a local LMCache-style opaque workload.
- [ ] Phase 1 through Phase 4 required tests remain green.

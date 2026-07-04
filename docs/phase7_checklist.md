# Phase 7 Checklist

Last verified: 2026-07-04

## API inspector

- [x] Add a no-vLLM-safe inspector command or module.
- [x] Report vLLM version, module paths, KVTransfer import path, and
  `KVTransferConfig` shape.
- [x] Report connector base classes and lifecycle method signatures when
  discoverable.
- [x] Detect `--kv-transfer-config` or installed equivalent without starting a
  server.
- [x] Emit deterministic skip reasons for missing or incompatible vLLM.
- [x] Avoid model downloads, CUDA initialization, and server startup.

## Fake vLLM interfaces

- [x] Add fake config objects for the inspected vLLM fields.
- [x] Add fake KV cache handles with deterministic layout metadata.
- [x] Add fake scheduler metadata for request, layer, and block identities.
- [ ] Exercise lifecycle order without importing vLLM, LMCache, torch, CUDA, or
  model assets.
- [ ] Include fake corruption, missing-object, and incompatible-layout cases.

## Connector package skeleton

- [x] Create an importable BIFROST vLLM connector package.
- [ ] Expose the dynamic import target expected by vLLM config.
- [x] Keep import side effects minimal.
- [x] Provide package metadata and version reporting.
- [x] Avoid new production dependencies unless justified in the change.

## Config parsing

- [ ] Parse BIFROST endpoint, timeout, chunk size, metrics JSONL path, and
  strict validation settings.
- [ ] Parse layout fingerprint inputs and model/config commitments.
- [ ] Preserve unknown vLLM config fields in diagnostics.
- [ ] Reject missing required compatibility fields.
- [ ] Gate real vLLM behavior behind explicit opt-in env vars.

## Opaque blob codec

- [ ] Generate `opaque_engine_blob` metadata for vLLM-owned blobs.
- [ ] Use `engine_name: "vllm"`.
- [ ] Use `integration_name: "bifrost_vllm_kv_connector"`.
- [ ] Use `kv_cache_format: "opaque_vllm_kv_blob"`.
- [ ] Compute deterministic `opaque_engine_key_hash`.
- [ ] Compute and validate `layout_fingerprint`.
- [ ] Record request, layer, and block identity.
- [ ] Store payload bytes through CPU staging with `compression: "none"`.
- [ ] Validate descriptor hash, payload hash, and object ID.

## Save path

- [ ] Implement `save_kv_layer` against fake vLLM interfaces.
- [ ] Validate metadata before calling BIFROST PUT.
- [ ] Count saves only after BIFROST reports stored and verified.
- [ ] Record save metrics and JSONL trace events.
- [ ] Fail closed on serialization, validation, daemon, or lifecycle errors.

## Load path

- [ ] Implement `start_load_kv` against fake vLLM interfaces.
- [ ] Query by engine, integration, and opaque key hash.
- [ ] Validate candidate store state before retrieval.
- [ ] Validate payload and compatibility before returning bytes to vLLM-owned
  adapters.
- [ ] Return misses or recompute decisions for missing blobs.
- [ ] Reject corrupt, incompatible, staged, quarantined, or partial blobs.
- [ ] Track block IDs with load errors.

## Scheduler metadata

- [ ] Define canonical request identity inputs.
- [ ] Define canonical layer identity inputs.
- [ ] Define canonical block identity inputs.
- [ ] Keep mutable process, port, retry, staging, benchmark, and local store
  state out of immutable object identity.
- [ ] Add tests for deterministic canonicalization.

## Failure policy

- [ ] Implement deterministic outcomes: hit, recompute, and fail.
- [ ] Add reason codes for missing, corrupt, incompatible, daemon-unavailable,
  and lifecycle failures.
- [ ] Ensure corrupt or incompatible blobs never become hits.
- [ ] Ensure daemon unavailability is not counted as a cache miss.
- [ ] Ensure lifecycle misuse raises deterministic errors.

## Metrics and traces

- [ ] Add connector-local counters for save, load, hit, miss, recompute,
  errors, and bytes.
- [ ] Add JSONL lifecycle events.
- [ ] Include reason codes and durations.
- [ ] Avoid logging raw KV bytes, raw prompt text, tokens, secrets, or
  authorization headers.
- [ ] Provide a metrics snapshot API for tests and reports.

## ContextStorm fake connector scenario

- [ ] Add a fake vLLM connector scenario.
- [ ] Exercise save, load, miss, recompute, and corruption rejection.
- [ ] Collect connector metrics and BIFROST store stats.
- [ ] Run fsck when a local daemon is used.
- [ ] Keep the scenario CPU-only and local by default.

## Optional real vLLM smoke

- [ ] Add optional import smoke.
- [ ] Add optional constructor smoke.
- [ ] Add optional save-only smoke.
- [ ] Add optional 1P1D scaffold.
- [ ] Require exact opt-in env vars from `docs/phase7_real_vllm_smoke.md`.
- [ ] Skip by default in CI.
- [ ] Require local model paths when model-backed vLLM paths are used.
- [ ] Never download models or tokenizers.

## CI

- [x] Run API inspector no-vLLM tests.
- [ ] Run fake vLLM lifecycle tests.
- [x] Run connector package import tests without real vLLM.
- [ ] Run config parsing tests.
- [ ] Run opaque blob codec tests.
- [ ] Run save and load fake tests.
- [ ] Run failure policy tests.
- [ ] Run metrics and trace tests.
- [ ] Run ContextStorm fake connector scenario.
- [ ] Keep real vLLM tests skipped unless explicitly enabled.
- [ ] Keep GPU tests skipped unless explicitly enabled.
- [ ] Require no Hugging Face tokens, model downloads, Docker, root, internet,
  CUDA, LMCache, vLLM, or external services in default CI.
- [ ] Preserve Phase 1 parity tests.
- [ ] Preserve Phase 2 transport tests.
- [ ] Preserve Phase 3 store tests.
- [ ] Preserve Phase 4 tiny-transformer correctness tests.
- [ ] Preserve Phase 5 LMCache connector tests.
- [ ] Preserve Phase 6 serving harness tests.

## Phase 7 done criteria

- [ ] API inspector reports compatible, incompatible, and no-vLLM states with
  deterministic reasons.
- [ ] Fake vLLM connector lifecycle runs in CI without real vLLM or GPU.
- [ ] Connector package is dynamically importable by compatible vLLM versions.
- [ ] Config parsing is version-sensitive and fail-closed.
- [ ] vLLM-owned KV blobs are stored as `opaque_engine_blob`.
- [ ] Save path stores only validated and verified BIFROST objects.
- [ ] Load path returns only compatible, committed, verified, and payload-valid
  blobs.
- [ ] Missing, corrupt, incompatible, daemon-unavailable, and lifecycle-error
  cases follow the documented failure policy.
- [ ] Metrics and traces report operation counts, bytes, durations, and reason
  codes without payload leakage.
- [ ] ContextStorm fake connector scenario passes by default.
- [ ] Optional real vLLM smoke tests are documented, opt-in, and skipped by
  default.
- [ ] No RDMA, QUIC, compression, parity chunks, GPU-direct transfer, SGLang,
  Kubernetes, dashboard, production auth, custom CUDA, model downloads, or
  mandatory GPU CI is introduced.
- [ ] Phase 1 through Phase 6 required tests remain green.

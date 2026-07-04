# Phase 7 Real vLLM Smoke

Last verified: 2026-07-04

## Purpose

Real vLLM smoke tests are optional compatibility checks for the direct BIFROST
KVTransfer connector. They are not part of default CI and must be skipped
unless explicitly enabled by the operator.

Default CI must run fake vLLM tests instead. Fake tests require no GPU, no
vLLM, no LMCache, no model downloads, and no internet.

## Exact opt-in environment variables

All real vLLM smoke tests require:

```bash
BIFROST_RUN_PHASE7_REAL_VLLM=1
```

Smoke tests that connect to BIFROST require:

```bash
BIFROST_PHASE7_BIFROST_ENDPOINT=127.0.0.1:7420
```

Smoke tests that instantiate serving or model-backed vLLM paths require a
local model path:

```bash
BIFROST_PHASE7_LOCAL_MODEL=/absolute/path/to/already-local-model
```

Additional opt-ins are specific to each smoke level:

```bash
BIFROST_PHASE7_REAL_IMPORT_SMOKE=1
BIFROST_PHASE7_REAL_CONSTRUCTOR_SMOKE=1
BIFROST_PHASE7_REAL_SAVE_ONLY_SMOKE=1
BIFROST_PHASE7_REAL_1P1D_SMOKE=1
```

The 1P1D scaffold may require GPU resources depending on the installed vLLM
version. It also requires:

```bash
BIFROST_PHASE7_ALLOW_GPU_SMOKE=1
```

No Phase 7 real smoke test may download models or tokenizers. A missing local
model path is a skip, not permission to download.

## Optional import smoke

Import smoke checks:

1. `import vllm`.
2. vLLM distribution version.
3. KVTransfer module import path.
4. `KVTransferConfig` availability or installed equivalent.
5. Dynamic import of the BIFROST connector module.

It must not start vLLM serving, initialize a model, require a GPU, connect to
BIFROST, or import LMCache.

Required environment:

```bash
BIFROST_RUN_PHASE7_REAL_VLLM=1
BIFROST_PHASE7_REAL_IMPORT_SMOKE=1
```

## Optional constructor smoke

Constructor smoke builds the BIFROST connector with real vLLM config objects
when the inspector reports a compatible API.

It checks:

1. Config field mapping.
2. Connector class construction.
3. Metrics snapshot availability.
4. No save or load before registration.
5. Clean shutdown.

Required environment:

```bash
BIFROST_RUN_PHASE7_REAL_VLLM=1
BIFROST_PHASE7_REAL_CONSTRUCTOR_SMOKE=1
```

If the constructor path touches BIFROST, it also requires
`BIFROST_PHASE7_BIFROST_ENDPOINT`.

## Optional save-only smoke

Save-only smoke registers vLLM-compatible cache metadata, stages a small
vLLM-owned or vLLM-shaped KV blob through the connector, and verifies that
BIFROST stores a committed `opaque_engine_blob`.

It checks:

1. CPU staging succeeds.
2. Metadata uses `engine_name: "vllm"`.
3. Metadata uses `integration_name: "bifrost_vllm_kv_connector"`.
4. Metadata uses `kv_cache_format: "opaque_vllm_kv_blob"`.
5. BIFROST PUT reports stored and verified.
6. Store query finds only committed and compatible objects.

Required environment:

```bash
BIFROST_RUN_PHASE7_REAL_VLLM=1
BIFROST_PHASE7_REAL_SAVE_ONLY_SMOKE=1
BIFROST_PHASE7_BIFROST_ENDPOINT=127.0.0.1:7420
```

If the installed vLLM save path requires a model-backed engine, the test must
also require `BIFROST_PHASE7_LOCAL_MODEL` and skip when absent.

## Optional 1P1D scaffold

The 1P1D scaffold is an exploratory smoke path for compatible vLLM versions
that expose disaggregated prefill/decode or producer/consumer KVTransfer
helpers.

It should verify:

1. The inspector detects the 1P1D API shape.
2. Producer and consumer connector roles can be configured.
3. The producer can save an opaque blob to BIFROST.
4. The consumer can attempt a compatible load.
5. Missing or incompatible loads recompute rather than returning suspect bytes.
6. Metrics report save, load, hit, miss, recompute, and failure counts.

Required environment:

```bash
BIFROST_RUN_PHASE7_REAL_VLLM=1
BIFROST_PHASE7_REAL_1P1D_SMOKE=1
BIFROST_PHASE7_ALLOW_GPU_SMOKE=1
BIFROST_PHASE7_BIFROST_ENDPOINT=127.0.0.1:7420
BIFROST_PHASE7_LOCAL_MODEL=/absolute/path/to/already-local-model
```

This scaffold must remain skipped in default CI.

## Skip policy

Real smoke tests must skip, not fail, when:

1. Required opt-in env vars are absent.
2. vLLM is not installed.
3. KVTransfer is not importable.
4. The inspector reports an incompatible API.
5. The BIFROST endpoint is required but unreachable.
6. A required local model path is absent.
7. The test is running in default CI.
8. GPU resources are required but `BIFROST_PHASE7_ALLOW_GPU_SMOKE=1` is absent.

They should fail only after all gates are satisfied and the operation violates
the connector contract.

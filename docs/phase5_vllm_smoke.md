# Phase 5 Optional vLLM Smoke

Last verified: 2026-06-14

## Purpose

The Phase 5 vLLM smoke test verifies the supported external route:

```text
vLLM -> LMCache -> BIFROST remote storage connector -> bifrostd
```

It is not a raw vLLM KVTransfer connector test. Direct vLLM integration remains
out of scope for Phase 5.

## Why opt-in

The smoke test is opt-in because vLLM and real serving environments may require
large dependencies, GPU hardware, model assets, runtime configuration, and
version-specific behavior. None of those requirements should affect default
developer tests or CI.

Default Phase 5 correctness is proven with fake LMCache tests, optional real
LMCache connector tests, and local BIFROST store roundtrips.

## Expected environment

The optional smoke environment may include:

```text
python with vLLM installed
python with LMCache installed
lmcache_bifrost connector package installed editable or in PYTHONPATH
bifrostd built and available
a local model already present on disk
local loopback networking
```

The smoke test must not download models or tokenizers. If a required local
model path is absent, the test must skip.

GPU use is allowed only when explicitly requested by the developer running the
smoke. CPU-capable configurations are preferred when practical, but the smoke
must remain skipped by default either way.

## Configuration outline

The smoke should configure LMCache remote storage to load the BIFROST adapter:

```text
remote storage plugin:
  type: bifrost
  adapter: BifrostConnectorAdapter
  connector: BifrostRemoteConnector
  endpoint: 127.0.0.1:7420
  object_type: opaque_engine_blob
```

The vLLM configuration should enable LMCache through documented LMCache/vLLM
mechanisms for the installed versions. The smoke must pin or print versions in
its output so failures can be triaged against API changes.

## Success criteria

The smoke succeeds when:

1. `bifrostd` starts with an empty local store.
2. vLLM starts with LMCache enabled and BIFROST configured as remote storage.
3. A first request produces LMCache remote `put` calls into BIFROST.
4. BIFROST commits verified `opaque_engine_blob` objects.
5. A repeated or overlapping request produces LMCache remote `exists` or `get`
   calls.
6. Retrieved objects pass descriptor, object ID, key hash, and payload
   integrity checks.
7. The connector reports cache hits or misses without returning corrupt or
   mismatched objects.
8. The smoke emits a concise JSON summary with versions, object counts, bytes,
   hit/miss counts, and any skip reason.

The smoke is allowed to miss cache reuse if LMCache or vLLM behavior changes.
It is not allowed to treat corrupt, staged, mismatched, or unverified objects as
hits.

## Skip conditions

The smoke must skip when:

1. vLLM is not installed.
2. LMCache is not installed.
3. The requested local model path is missing.
4. Required version probes fail.
5. `bifrostd` is unavailable.
6. GPU is required by the chosen configuration and opt-in GPU execution was not
   requested.

Skip output should state the exact reason.

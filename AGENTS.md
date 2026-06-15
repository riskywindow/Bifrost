# BIFROST Agent Guidance

Last verified: 2026-06-15

## Current phase

BIFROST is in Phase 6.

Phase 1 is complete. It produced immutable KV object descriptors, target
compatibility profiles, canonical object identity hashing, Python reference
validation, Rust mirror validation, deterministic fixtures, CLI tooling, and
cross-language test vectors.

Phase 2 is complete. It produced the synthetic transport protocol, chunking and
reassembly, a minimal transfer spool, single-path PUT, HAS, and GET, optional
multipath PUT and retry behavior, JSONL traces and metrics, and ContextStorm
synthetic transport benchmarks.

Phase 3 is complete. It produced the durable local KV object store, SQLite
catalog, lookup, query, inspect, stats, pinning, deterministic eviction,
prefix/session manifests, fsck, and ContextStorm store benchmarks.

Phase 4 is complete. It produced a CPU-friendly tiny-transformer correctness
harness, integer tokenization, real KV extraction, native KV page
serialization, store-backed rehydration, logit and greedy-continuation
comparisons, fail-closed corruption tests, and a cross-process KV teleport
demo.

Phase 5 is complete. It produced the Python BIFROST client, LMCache
`opaque_engine_blob` codec, LMCache connector adapter, LMCache remote
connector, fake connector tests, optional real LMCache smoke coverage, optional
vLLM smoke scaffolding, and ContextStorm LMCache connector workloads.

The Phase 6 goal is an end-to-end serving benchmark and reproducible report for
the supported real serving stack:

```text
vLLM -> LMCache -> BIFROST remote storage connector -> bifrostd
```

Phase 6 must prove that BIFROST can sit behind LMCache in a real vLLM serving
path, collect cache activity, compare against honest baselines, and report
TTFT, latency, correctness, and store health.

## Phase 6 scope

Agents may work on:

1. Phase 6 design docs, checklists, and reproducibility guidance.
2. An environment doctor for fake CI, LMCache connector, vLLM import, GPU
   serving, and full benchmark readiness.
3. Version-sensitive vLLM plus LMCache configuration generation.
4. A local process orchestrator for `bifrostd`, LMCache, and vLLM benchmark
   commands.
5. Serving workloads with repeated prefixes and deterministic fake-serving
   equivalents for CI.
6. Metrics collection for vLLM, LMCache when available, BIFROST connector
   counters, BIFROST store stats, and fsck.
7. Report generation that includes raw metrics, environment details, failures,
   skips, and baseline comparisons.
8. Optional real vLLM plus LMCache plus BIFROST benchmark runs that are opt-in
   and skipped by default.
9. Optional two-instance cache-sharing experiments through LMCache remote
   storage, only after the single-instance serving benchmark is correct.

Agents must not implement a raw vLLM KVTransfer connector in Phase 6.

Do not add:

1. Raw vLLM KVTransfer integration.
2. SGLang integration.
3. Kubernetes.
4. Dashboards.
5. GPU-required CI.
6. Hugging Face model downloads or tokenizer downloads in default paths.
7. External model downloads in tests, CI, or default demos.
8. Custom CUDA.
9. RDMA.
10. QUIC.
11. Compression.
12. Parity chunks or FEC.
13. Production authentication.
14. Distributed routing or scheduler logic.

Real vLLM tests and benchmarks are optional exploratory work only. They must be
skipped by default and must never be required by tests, CI, or default demo
commands.

## Correctness rules

BIFROST may miss a cache hit, but it must never return a wrong, corrupt,
partial, incompatible, or semantically uncertain object as an LMCache hit.

Phase 6 continues to use Phase 5 `opaque_engine_blob` storage for LMCache KV
objects. LMCache owns tensor layout, serialization meaning, cache chunking, and
rehydration semantics. BIFROST may hash, validate, store, transfer, retrieve,
list, delete, and report local records for opaque bytes, but it must not
reinterpret LMCache tensor semantics or convert LMCache payloads into
`native_kv_page`.

Every connector and benchmark operation must fail closed. If serialization,
descriptor generation, validation, store commit, catalog lookup, payload
integrity, retrieval, deserialization, key matching, connector lifecycle state,
or benchmark process state is uncertain, the connector must return a miss or
raise a deterministic connector error according to the method contract. It must
not synthesize a `MemoryObj` from suspect bytes.

Only committed and verified objects may satisfy LMCache `exists`, `get`,
`list`, batched lookup operations, or benchmark hit counters. Staging objects
must never be visible as available cache hits and must never satisfy connector
APIs that imply availability.

Mutable store, transport, connector, serving, or benchmark state must never be
included in immutable object identity. Fields such as staging path, committed
path, local tier, pinned state, write state, last access time, expiry, transfer
state, retry count, peer address, cache location, benchmark run ID, process ID,
demo label, request ID, server port, and eviction score describe local records
or measurements, not immutable opaque objects.

Benchmark correctness is part of the result. A Phase 6 report must state
deterministic settings, output comparison mode, skipped correctness checks,
store integrity status, connector errors, and any advisory-only limitations.

## Benchmark rules

Phase 6 must compare baselines honestly:

1. `vLLM only`.
2. `vLLM + LMCache local or CPU storage`.
3. `vLLM + LMCache + BIFROST remote storage`.

Do not claim speedups unless they are measured in the same report under stated
hardware, model, runtime, workload, and configuration conditions. If BIFROST is
slower, inconclusive, skipped, or only partially exercised, the report must say
so directly.

Every real-serving report must include:

1. Git commit and dirty-tree status.
2. Python, CUDA, driver, torch, vLLM, LMCache, BIFROST connector, and bifrostd
   versions when available.
3. Hardware and GPU details when available.
4. Model path or model identifier, with a statement that the model was already
   available locally.
5. Workload definition and request counts.
6. Baseline configuration files.
7. TTFT, p50/p95 latency, output token latency, requests per second, and error
   rate.
8. BIFROST connector operation counts and bytes stored or loaded.
9. LMCache hit/miss metrics when available.
10. BIFROST store stats and fsck status.
11. Failures, skipped components, and environment readiness level.

## Dependencies

Do not add new production dependencies without a written justification in the
relevant change. Prefer standard library functionality and existing project
dependencies.

LMCache may be used by optional integration tests or benchmarks if it is
installed in the developer environment. Real LMCache tests must skip when
LMCache is missing and must not be required in CI unless CI explicitly installs
LMCache for that job.

vLLM may be used only by opt-in smoke tests and benchmarks. vLLM tests must
skip by default and must not require GPU hardware, Hugging Face tokens, model
downloads, cloud credentials, Docker, root, Kubernetes, or internet access in
the default test path.

CI must not require GPU, vLLM, LMCache, Hugging Face tokens, model downloads,
root, Docker, or internet access. Fake serving workloads must run in CI.

Test-only dependencies are acceptable when they materially improve coverage and
are scoped to tests.

## Error codes

Keep Phase 1 validation error reason codes stable. Once fixtures, tests, or
docs rely on a reason code, do not rename or delete it without a migration note
and updated compatibility expectations.

Phase 2 protocol and transfer errors should remain specific and deterministic.
Transport errors must not be conflated with object validation errors.

Phase 3 store errors should distinguish catalog errors, filesystem errors,
integrity errors, compatibility errors, manifest errors, eviction errors, and
fsck findings.

Phase 4 harness errors should distinguish model determinism errors, tokenizer
or token-hash errors, KV extraction errors, serialization errors, validation
errors, store roundtrip errors, manifest completeness errors, rehydration
errors, logit mismatch errors, and greedy-continuation mismatch errors.

Phase 5 connector errors should distinguish LMCache serialization errors,
opaque blob validation errors, key hashing errors, store commit errors, store
retrieval errors, missing objects, corrupt objects, descriptor mismatch,
payload hash mismatch, connector configuration errors, connector lifecycle
errors, and optional real-LMCache compatibility errors.

Phase 6 benchmark errors should distinguish environment readiness failures,
configuration generation errors, process startup errors, readiness timeouts,
request failures, metrics parse errors, missing baseline results, correctness
mismatches, skipped optional components, store health failures, and report
generation errors.

## Tests

Run relevant Rust and Python tests after changes.

Expected Phase 6 test coverage:

1. Environment doctor checks for fake CI readiness without vLLM, LMCache, GPU,
   model assets, root, Docker, or internet access.
2. Environment doctor skip reasons for missing optional vLLM, LMCache, CUDA,
   GPU, local model path, ports, BIFROST daemon, connector package, disk space,
   or Hugging Face token.
3. Config generator output for vLLM-only, vLLM plus LMCache local or CPU
   storage, and vLLM plus LMCache plus BIFROST remote storage.
4. Version-sensitive warnings are emitted without failing default CI.
5. Deterministic fake serving workload runs in CI and exercises connector-like
   put/get/exists/list counters without importing vLLM or LMCache.
6. Workload generator produces repeated system prompt, document QA, code
   context, and multi-turn repeated-prefix request sets.
7. Benchmark runner records baselines separately and refuses to compare missing
   or incompatible baseline files as speedups.
8. Metrics ingestion parses vLLM bench serve JSON when present.
9. BIFROST connector metrics, store stats, bytes moved, and fsck status are
   included in fake and real reports when available.
10. Correctness checks compare deterministic outputs when configured and mark
    non-deterministic serving comparisons as advisory or skipped.
11. Optional real LMCache tests skip when LMCache is not installed.
12. Optional real vLLM serving tests skip unless explicitly opted in with a
    local model path and required dependencies.
13. Phase 1 parity tests, Phase 2 transport tests, Phase 3 store tests, Phase
    4 tiny-transformer correctness tests, and Phase 5 LMCache connector tests
    remain green.

Keep tests CPU-only and local by default. Tests must not require GPU hardware,
cloud credentials, external services, LMCache, vLLM, Hugging Face downloads,
Docker, Kubernetes, root, or internet access unless explicitly marked optional
and skipped by default.

Root-required network fault tests, GPU demos, real LMCache tests, vLLM smoke
tests, and full serving benchmarks must remain opt-in and skipped by default.

If a test cannot be run, state the reason in the final response.

## Implementation order

Build the end-to-end serving benchmark on top of the Phase 5 LMCache remote
storage integration. Do not bypass LMCache with direct vLLM KVTransfer work.

Recommended order:

1. Phase 6 design docs and checklist.
2. Environment doctor and readiness report.
3. Config generator for baselines and BIFROST-backed LMCache remote storage.
4. Process orchestrator for local serving benchmark runs.
5. Deterministic fake serving workload for CI.
6. Workload generator for repeated-prefix real-serving scenarios.
7. Benchmark runner with baseline separation and raw metrics capture.
8. Metrics collectors for vLLM, LMCache, BIFROST connector, BIFROST store, and
   fsck.
9. Correctness comparison and advisory/skip reporting.
10. Reproducible report generator.
11. Optional real vLLM plus LMCache plus BIFROST benchmark.
12. Optional two-instance cache-sharing experiment.

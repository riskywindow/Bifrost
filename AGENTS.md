# BIFROST Agent Guidance

Last verified: 2026-07-04

## Current phase

BIFROST is in Phase 7.

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

Phase 6 is complete. It produced the end-to-end serving benchmark and
reproducible report path for:

```text
vLLM -> LMCache -> BIFROST remote storage connector -> bifrostd
```

The final Phase 6 review reported no gate failures, a real three-mode serving
matrix with 9 rows across 3 modes and 3 repetitions, observed BIFROST
connector activity, observed LMCache local CPU activity, clean fsck, and no
speedup claim because BIFROST was slower in the single-host run.

The Phase 7 goal is a compatibility-first native vLLM KVTransfer connector for
BIFROST. Phase 7 builds a direct vLLM connector package that can be
dynamically imported by vLLM, supports fake-vLLM CI tests, stores and loads
vLLM-owned KV state through BIFROST as `opaque_engine_blob` objects, and
provides opt-in real vLLM smoke tests.

## Phase 7 scope

Agents may work on:

1. Phase 7 design docs, checklists, and reproducibility guidance.
2. A vLLM KVTransfer API inspector that records the installed vLLM import
   paths, configuration shape, connector base classes, lifecycle hooks, method
   signatures, scheduler metadata, and supported CLI/config flags.
3. Fake vLLM interfaces that model the connector lifecycle without importing
   vLLM, LMCache, torch, CUDA, model assets, or tokenizers.
4. A direct BIFROST vLLM KVTransfer connector package that is importable by
   vLLM through `KVTransferConfig` or the installed vLLM equivalent.
5. Version-sensitive configuration parsing and explicit real-vLLM support
   gates.
6. Opaque serialization of vLLM-owned KV blobs through CPU staging, with
   BIFROST object metadata using `engine_name: "vllm"`,
   `integration_name: "bifrost_vllm_kv_connector"`, and
   `kv_cache_format: "opaque_vllm_kv_blob"`.
7. Save and load paths for vLLM-owned opaque blobs that validate object
   descriptors, payload hashes, compatibility fields, object IDs, committed
   store state, and connector lifecycle state before any hit is returned.
8. Scheduler metadata capture needed for opaque request, layer, block, and
   layout identity, without reinterpreting vLLM tensor semantics.
9. Connector metrics, JSONL traces, deterministic reason codes, and
   ContextStorm fake connector scenarios.
10. Optional real vLLM import, constructor, save-only, and 1P1D smoke tests
    that are opt-in and skipped by default.

## Phase 7 out of scope

Do not add:

1. RDMA.
2. QUIC.
3. Compression.
4. Parity chunks or FEC.
5. GPU-direct transfer.
6. SGLang integration.
7. Kubernetes.
8. Dashboards.
9. Production authentication.
10. Distributed routing or scheduler logic beyond metadata required by the
    connector contract.
11. Mandatory GPU CI.
12. Hugging Face model downloads or tokenizer downloads in default paths.
13. External model downloads in tests, CI, or default demos.
14. Custom CUDA.

Direct vLLM KVTransfer connector work is permitted in Phase 7. Real vLLM tests
and smoke runs remain optional exploratory work only. They must be skipped by
default and must never be required by tests, CI, or default demo commands.

## Correctness rules

BIFROST may miss a cache hit, but it must never return a wrong, corrupt,
partial, incompatible, or semantically uncertain object as a vLLM or LMCache
hit.

Phase 7 stores vLLM-owned KV state as BIFROST `opaque_engine_blob` objects.
vLLM owns tensor layout, serialization meaning, cache block semantics,
scheduler decisions, and rehydration semantics. BIFROST may hash, validate,
store, transfer, retrieve, list, delete, and report local records for opaque
bytes, but it must not reinterpret vLLM tensor semantics or convert vLLM
payloads into `native_kv_page` in Phase 7.

BIFROST must not reinterpret vLLM tensor semantics beyond opaque
compatibility fields. Allowed opaque compatibility fields include stable engine
namespace, integration namespace, KV cache format, layout fingerprint, vLLM
version when available, model/config commitments when supplied by vLLM or the
operator, request identity, layer identity, block identity, payload byte
length, and payload hash. These fields are for rejecting incompatible blobs,
not for reconstructing tensor meaning inside BIFROST.

Every connector operation must fail closed. If daemon connectivity,
serialization, descriptor generation, validation, store commit, catalog
lookup, payload integrity, retrieval, deserialization, key matching, layout
compatibility, connector lifecycle state, or benchmark process state is
uncertain, the connector must return a miss, request recompute, or raise a
deterministic connector error according to the method contract. It must not
synthesize vLLM KV state from suspect bytes.

Only committed and verified objects may satisfy vLLM connector loads, lookup
operations, or benchmark hit counters. Staging objects must never be visible as
available cache hits and must never satisfy connector APIs that imply
availability.

Mutable store, transport, connector, serving, or benchmark state must never be
included in immutable object identity. Fields such as staging path, committed
path, local tier, pinned state, write state, last access time, expiry,
transfer state, retry count, peer address, cache location, benchmark run ID,
process ID, demo label, request ID assigned by a benchmark harness, server
port, and eviction score describe local records or measurements, not immutable
opaque objects.

Phase 6 benchmark correctness remains part of the historical result. Phase 7
must not rewrite Phase 6 as a speedup result. The final Phase 6 evidence must
continue to state that BIFROST was slower in the single-host real matrix and
that no speedup was claimed.

## Phase 6 benchmark rules

Phase 6 compared baselines honestly:

1. `vllm_only`.
2. `vllm_lmcache_local_cpu`.
3. `vllm_lmcache_bifrost`.

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
root, Docker, or internet access. Fake vLLM tests must run in CI with no GPU,
no vLLM, no LMCache, no model downloads, and no internet. Fake serving
workloads from Phase 6 must remain CPU-only and local by default.

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

Phase 7 connector errors should distinguish vLLM API inspection failures,
dynamic import failures, connector configuration errors, lifecycle errors,
KV-cache registration errors, scheduler metadata errors, key hashing errors,
layout fingerprint mismatches, CPU staging serialization errors, opaque blob
validation errors, store commit errors, store retrieval errors, missing
objects, corrupt objects, descriptor mismatches, payload hash mismatches,
daemon unavailable errors, load recompute decisions, optional real-vLLM
compatibility errors, and skipped optional components.

## Tests

Run relevant Rust and Python tests after changes.

Expected Phase 7 test coverage:

1. API inspector tests run without vLLM installed and report a deterministic
   skip or not-ready result.
2. Fake vLLM lifecycle tests run in CI without GPU, vLLM, LMCache, model
   downloads, tokenizers, root, Docker, internet, or external services.
3. Connector package import tests validate dynamic import strings without
   importing real vLLM by default.
4. Config parsing tests cover endpoint, timeouts, chunk size, metrics path,
   strict validation, layout fingerprint inputs, and real-vLLM opt-in gates.
5. Opaque blob codec tests cover `engine_name: "vllm"`,
   `integration_name: "bifrost_vllm_kv_connector"`,
   `kv_cache_format: "opaque_vllm_kv_blob"`, layout fingerprint, request,
   layer, block identity, payload hashes, object IDs, and fail-closed
   validation.
6. Save-path fake tests verify that only validated and committed objects are
   counted as saved.
7. Load-path fake tests verify missing, corrupt, incompatible, partial, staged,
   and daemon-unavailable blobs become misses, recompute decisions, or
   deterministic errors according to the lifecycle method contract.
8. Scheduler metadata tests ensure mutable process, port, benchmark, retry,
   staging, and local store state never enters immutable object identity.
9. Metrics and JSONL trace tests cover operation counts, bytes, reason codes,
   lifecycle events, and no raw payload logging.
10. ContextStorm fake connector scenarios exercise save/load/recompute without
    importing vLLM or LMCache.
11. Optional real vLLM import, constructor, save-only, and 1P1D smoke tests
    skip unless explicitly enabled with local dependencies and local model
    assets when needed.
12. Phase 1 parity tests, Phase 2 transport tests, Phase 3 store tests, Phase
    4 tiny-transformer correctness tests, Phase 5 LMCache connector tests, and
    Phase 6 serving harness tests remain green.

Keep tests CPU-only and local by default. Tests must not require GPU hardware,
cloud credentials, external services, LMCache, vLLM, Hugging Face downloads,
Docker, Kubernetes, root, or internet access unless explicitly marked optional
and skipped by default.

Root-required network fault tests, GPU demos, real LMCache tests, vLLM smoke
tests, and full serving benchmarks must remain opt-in and skipped by default.

If a test cannot be run, state the reason in the final response.

## Implementation order

Build the direct vLLM connector on top of the Phase 5 opaque object storage
contract and Phase 6 serving evidence. Do not bypass BIFROST validation or
reinterpret vLLM tensors as native pages in Phase 7.

Recommended order:

1. Phase 7 design docs and checklist.
2. vLLM API inspector with no-vLLM skip behavior.
3. Fake vLLM interfaces and lifecycle harness for CI.
4. Connector package skeleton and dynamic import target.
5. Version-sensitive config parsing and opt-in real-vLLM gates.
6. vLLM opaque blob codec and layout fingerprint mapping.
7. Save path through CPU staging into BIFROST `opaque_engine_blob`.
8. Load path from committed and verified BIFROST objects back to vLLM-owned
   opaque blobs.
9. Scheduler metadata capture and immutable identity tests.
10. Failure policy, deterministic reason codes, metrics, and traces.
11. ContextStorm fake connector scenario.
12. Optional real vLLM import and constructor smoke.
13. Optional real vLLM save-only smoke.
14. Optional real vLLM 1P1D scaffold.

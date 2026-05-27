# BIFROST Agent Guidance

Last verified: 2026-05-27

## Current phase

BIFROST is in Phase 2.

Phase 1 is complete. It produced immutable KV object descriptors, target
compatibility profiles, canonical object identity hashing, Python reference
validation, Rust mirror validation, deterministic fixtures, CLI tooling, and
cross-language test vectors.

The Phase 2 goal is a synthetic KV transport layer and benchmark harness. A
valid Phase 1 KV object should be chunked, transferred over TCP, reassembled,
revalidated, committed into a minimal object spool, and benchmarked under local
synthetic workloads.

## Phase 2 scope

Agents may work on:

1. TCP transport framing and local daemon/client CLIs.
2. Deterministic chunking and reassembly.
3. Single-path PUT and GET transfers.
4. Minimal object spool with staging and committed areas.
5. Metrics for transfer latency, throughput, chunk counts, validation failures,
   and cache hit or miss behavior.
6. Synthetic benchmark harnesses for local CPU-only workloads.
7. Multipath transfer experiments after the single-path path is correct.
8. Retry, timeout, and fault-injection tests that remain local and opt-in when
   root privileges or host network mutation are required.

Agents must not implement later-phase integrations or production systems during
Phase 2.

Do not add:

1. LMCache integration.
2. vLLM integration.
3. Real model KV extraction.
4. Real KV injection.
5. GPU inference.
6. Dashboards.
7. QUIC.
8. Compression.
9. RDMA.
10. Production authentication or authorization.
11. Full object-store eviction, pinning, or cache policy.
12. Parity chunks or erasure coding.

## Correctness rules

BIFROST may miss a cache hit, but it must never serve wrong or partial KV state.

BIFROST must fail closed. If compatibility, integrity, schema meaning, transfer
completion, or object identity is uncertain, reject the object or report a miss.

Phase 2 must reuse Phase 1 Rust validation before accepting or committing
objects. A transferred object is not servable just because all bytes arrived.
The daemon must validate the descriptor, payload hash, descriptor hash, object
ID, and target compatibility with the Phase 1 Rust validator before commit.

Never serve from staging. Staging paths are for incomplete transfers only.

Never commit an object until all chunks are present and full object validation
passes. Chunk-level checks may reject bad chunks early, but successful commit
requires whole-object validation.

Mutable spool state must never be included in immutable object identity. Fields
such as staging path, committed path, local tier, pinned state, write state,
last access time, expiry, transfer state, retry count, peer address, and cache
location describe local records, not immutable KV objects.

Prefer boring correctness over clever optimization. Deterministic behavior,
stable tests, readable validation, explicit error reasons, and crash-safe local
state matter more than throughput in Phase 2.

## Dependencies

Do not add new production dependencies without a written justification in the
relevant change. Prefer standard library functionality and existing project
dependencies.

Test-only dependencies are acceptable when they materially improve coverage and
are scoped to tests.

## Error codes

Keep Phase 1 validation error reason codes stable. Once fixtures, tests, or
docs rely on a reason code, do not rename or delete it without a migration note
and updated compatibility expectations.

Phase 2 protocol and transfer errors should be specific and deterministic.
Transport errors must not be conflated with object validation errors.

## Tests

Run relevant Rust and Python tests after changes.

Expected Phase 2 test coverage:

1. Frame encoding and decoding.
2. Protocol version and required-field validation.
3. Chunk ordering, duplicate chunks, missing chunks, and corrupted chunks.
4. Single-path PUT and GET over local TCP.
5. Spool staging, atomic commit, restart cleanup, and never-serve-from-staging.
6. Revalidation before commit using Phase 1 Rust validation.
7. Metrics emitted for successful and failed transfers.
8. Synthetic benchmark smoke tests that are CPU-only and local.
9. Cross-language Phase 1 parity tests remain green.

Keep tests CPU-only and local by default. Tests must not require GPU hardware,
cloud credentials, external services, LMCache, vLLM, or internet access.

Root-required network fault tests must be opt-in and skipped by default.

If a test cannot be run, state the reason in the final response.

## Implementation order

Build single-path transport first and multipath second.

Use the Phase 1 Rust validator as the acceptance gate for transferred objects.
The transport and spool layers may track local state, but they must not redefine
KV object identity or compatibility.

Recommended order:

1. Protocol frames and version negotiation.
2. Chunker and reassembler.
3. Local spool staging and atomic commit.
4. Single-path PUT.
5. Single-path GET.
6. Metrics and benchmark harness.
7. Retry and timeout behavior.
8. Multipath experiments after the single-path path is correct.

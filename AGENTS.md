# BIFROST Agent Guidance

Last verified: 2026-06-02

## Current phase

BIFROST is in Phase 4.

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

The Phase 4 goal is to build a CPU-friendly tiny-transformer KV correctness
harness. BIFROST must prove that real transformer KV cache state can be
extracted, serialized into native KV pages, stored, retrieved, rehydrated, and
used to resume decoding with logits matching an uninterrupted baseline.

## Phase 4 scope

Agents may work on:

1. A local deterministic PyTorch tiny transformer used only as a correctness
   harness.
2. Integer tokenization based on explicit token IDs, not external tokenizer
   packages or downloaded tokenizer assets.
3. KV extraction from the tiny model's `past_key_values`.
4. Serialization of every generated KV page as a Phase 1 `native_kv_page`.
5. Target profile generation for the tiny model and harness engine.
6. Store roundtrips through the Phase 3 local store and manifest APIs.
7. Rehydration of verified native KV pages back into the tiny model's
   `past_key_values` layout.
8. Logit and greedy-continuation comparisons against an uninterrupted
   baseline.
9. Corruption, mismatch, missing-page, and manifest-incompleteness tests that
   prove the harness fails closed.
10. ContextStorm model-correctness benchmark scenarios that remain CPU-only,
   local, and deterministic.

Agents must not implement external integrations or production systems during
Phase 4.

Do not add:

1. LMCache integration.
2. vLLM integration.
3. Hugging Face model downloads or tokenizer downloads.
4. Production model support.
5. GPU requirements.
6. Custom CUDA.
7. Dashboards.
8. Compression.
9. QUIC.
10. RDMA.
11. Scheduler logic.
12. Kubernetes.

GPU demos are optional exploratory work only. They must be skipped by default
and must never be required by tests, CI, or default demo commands.

## Correctness rules

BIFROST may miss a cache hit, but it must never rehydrate wrong or partial KV
state.

BIFROST must fail closed. If compatibility, integrity, catalog consistency,
schema meaning, transfer completion, object identity, local store state, prefix
identity, token identity, layer completeness, dtype, tensor layout, or
rehydration shape is uncertain, reject the object or report a miss.

Phase 4 must reuse Phase 1 native KV validation as the acceptance gate for every
KV page generated from the tiny model. Store indexing, manifest membership, and
demo convenience code are not substitutes for object validation. A catalog row
or manifest row does not make a page rehydratable unless file-level integrity,
descriptor hash, payload hash, object ID, target compatibility, prefix identity,
and tensor layout are valid.

Only committed and verified objects may be used for rehydration. Staging objects
must never be listed as available cache hits and must never satisfy HAS, GET,
lookup, query, manifest, stats, or tiny-harness APIs that imply availability.

Every rehydration must prove layer and block completeness for the requested
prefix. If any required layer/block page is absent, corrupt, incompatible,
quarantined, evicted, or only partially understood, the harness must recompute
locally or report a cache miss. It must not inject a partial session unless the
valid prefix boundary is explicit and tested.

Mutable store or harness state must never be included in immutable object
identity. Fields such as staging path, committed path, local tier, pinned state,
write state, last access time, expiry, transfer state, retry count, peer
address, cache location, manifest membership, benchmark run ID, process ID,
demo label, and eviction score describe local records, not immutable KV objects.

Prefer boring correctness over clever optimization. Deterministic behavior,
stable tests, readable validation, explicit error reasons, durable catalog
updates, and exact baseline comparisons matter more than throughput in Phase 4.

## Dependencies

Do not add new production dependencies without a written justification in the
relevant change. Prefer standard library functionality and existing project
dependencies.

PyTorch may be used for the Phase 4 tiny-transformer harness if it is already
part of the project test environment or the change explicitly documents why it
is needed. The model must be defined locally with deterministic weights; it must
not download external model or tokenizer assets.

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

Phase 4 harness errors should distinguish model determinism errors, tokenizer or
token-hash errors, KV extraction errors, serialization errors, validation
errors, store roundtrip errors, manifest completeness errors, rehydration
errors, logit mismatch errors, and greedy-continuation mismatch errors.

## Tests

Run relevant Rust and Python tests after changes.

Expected Phase 4 test coverage:

1. Tiny transformer deterministic initialization and CPU-only generation.
2. Integer tokenization, token hash construction, prefix hash construction, and
   target profile generation.
3. KV extraction from every layer and block boundary.
4. Serialization of every model-generated KV page as `native_kv_page`.
5. Phase 1 Python and Rust validation for generated descriptors and payloads.
6. Store commit, lookup, GET, query, stats, and manifest roundtrip for generated
   pages.
7. Rehydration shape, dtype, layer ordering, and token-range checks.
8. Logit comparison between uninterrupted and extract-store-rehydrate paths.
9. Greedy continuation equality after rehydration.
10. Missing page, corrupt payload, descriptor mismatch, target mismatch, token
    mismatch, prefix mismatch, dtype mismatch, and layer-order mismatch failure
    cases.
11. Prefix and session manifest completeness and missing-block queries for tiny
    model pages.
12. Cross-process demo smoke tests that remain local and deterministic.
13. ContextStorm model benchmark smoke tests that are CPU-only, local, and
    deterministic.
14. Phase 1 parity tests, Phase 2 transport tests, and Phase 3 store tests
    remain green.

Keep tests CPU-only and local by default. Tests must not require GPU hardware,
cloud credentials, external services, LMCache, vLLM, Hugging Face downloads,
Docker, Kubernetes, or internet access.

Root-required network fault tests and GPU demos must remain opt-in and skipped
by default.

If a test cannot be run, state the reason in the final response.

## Implementation order

Build the tiny-transformer correctness harness before external integrations.

Use the Phase 1 native KV validator as the acceptance gate for every generated
page. The model harness, transport, spool, catalog, store, manifest, and demo
layers may track local state, but they must not redefine KV object identity or
compatibility.

Recommended order:

1. Phase 4 design docs and checklist.
2. Deterministic tiny transformer and integer-token test fixtures.
3. KV extraction and native page serialization.
4. Tiny-model target profile and prefix/token hash generation.
5. Phase 1 validation tests for generated pages.
6. Store roundtrip and manifest completeness tests.
7. Rehydration into `past_key_values`.
8. Logit and greedy continuation correctness tests.
9. Corruption and mismatch fail-closed tests.
10. Cross-process KV teleportation demo.
11. ContextStorm model correctness benchmark scenarios.

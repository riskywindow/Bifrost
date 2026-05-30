# BIFROST Agent Guidance

Last verified: 2026-05-30

## Current phase

BIFROST is in Phase 3.

Phase 1 is complete. It produced immutable KV object descriptors, target
compatibility profiles, canonical object identity hashing, Python reference
validation, Rust mirror validation, deterministic fixtures, CLI tooling, and
cross-language test vectors.

Phase 2 is complete. It produced the synthetic transport protocol, chunking and
reassembly, a minimal transfer spool, single-path PUT, HAS, and GET, optional
multipath PUT and retry behavior, JSONL traces and metrics, and ContextStorm
synthetic transport benchmarks.

The Phase 3 goal is to turn the Phase 2 transfer spool into a durable local KV
object store. The store should index committed KV objects, support lookup,
query, stats, pinning, deterministic eviction, prefix and session manifests,
fsck, and ContextStorm store benchmarks.

## Phase 3 scope

Agents may work on:

1. A durable local object catalog for committed KV objects.
2. Catalog migrations and schema-version checks.
3. Store lookup, query, stats, pin, unpin, eviction, and fsck APIs.
4. Daemon and CLI surfaces for local store operations.
5. Disk-tier indexing and an optional memory tier for already-verified objects.
6. Prefix manifests and optional session manifests for groups of KV objects.
7. Deterministic eviction policies including LRU, size-aware LRU, TTL
   expiration, and target-byte eviction.
8. Catalog and filesystem reconciliation, quarantine, and conservative repair.
9. ContextStorm store benchmarks that remain CPU-only, local, and
   deterministic.

Agents must not implement external integrations or production systems during
Phase 3.

Do not add:

1. LMCache integration.
2. Language-model integration.
3. vLLM integration.
4. Real model KV extraction.
5. Real KV injection.
6. GPU inference.
7. Dashboards.
8. QUIC.
9. Compression.
10. RDMA.
11. Production authentication or authorization.
12. Parity chunks or erasure coding.
13. Kubernetes.

## Correctness rules

BIFROST may miss a cache hit, but it must never serve wrong or partial KV state.

BIFROST must fail closed. If compatibility, integrity, catalog consistency,
schema meaning, transfer completion, object identity, or local store state is
uncertain, reject the object or report a miss.

Phase 3 must reuse Phase 1 Rust validation as the acceptance gate for committed
objects. Store indexing is not a substitute for object validation. A catalog row
does not make an object servable unless file-level integrity, descriptor hash,
payload hash, object ID, and target compatibility are valid.

Only committed and verified objects may be served. Staging objects must never be
listed as available cache hits and must never satisfy HAS, GET, lookup, query,
manifest, or stats APIs that imply availability.

Every committed object must have both file-level integrity and catalog
consistency. If the catalog and filesystem disagree, the store must treat the
object as unavailable until fsck or validation proves a safe state.

Pinned objects must never be evicted. Eviction must be deterministic and
testable: the same catalog state, policy, target, and clock input must choose
the same victims.

fsck must fail closed. Suspect, corrupt, conflicting, or partially understood
objects must be quarantined or marked unavailable rather than served. Repair may
restore availability only after full validation succeeds.

Mutable store state must never be included in immutable object identity. Fields
such as staging path, committed path, local tier, pinned state, write state,
last access time, expiry, transfer state, retry count, peer address, cache
location, manifest membership, and eviction score describe local records, not
immutable KV objects.

Prefer boring correctness over clever optimization. Deterministic behavior,
stable tests, readable validation, explicit error reasons, durable catalog
updates, and crash-safe local state matter more than throughput in Phase 3.

## Dependencies

Do not add new production dependencies without a written justification in the
relevant change. Prefer standard library functionality and existing project
dependencies.

SQLite is the intended Phase 3 catalog backend. If the Rust implementation
needs a production SQLite crate that is not already present, justify the
dependency in the schema or implementation change.

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

## Tests

Run relevant Rust and Python tests after changes.

Expected Phase 3 test coverage:

1. Catalog migrations and schema-version checks.
2. Object indexing only after commit and validation.
3. Lookup, query, stats, pin, unpin, eviction, and fsck APIs.
4. Staging objects never reported as hits or manifest members.
5. File-level integrity and catalog consistency checks before serving.
6. Pinned object protection under every eviction policy.
7. Deterministic eviction victim selection with fixed clock inputs.
8. Prefix and session manifest completeness and missing-block queries.
9. Catalog vs filesystem reconciliation, orphan detection, missing object
   detection, corruption detection, quarantine, and repair behavior.
10. Optional memory tier behavior without changing immutable object identity.
11. ContextStorm store benchmark smoke tests that are CPU-only, local, and
   deterministic.
12. Cross-language Phase 1 parity tests and Phase 2 transport tests remain
   green.

Keep tests CPU-only and local by default. Tests must not require GPU hardware,
cloud credentials, external services, LMCache, vLLM, Docker, Kubernetes, or
internet access.

Root-required network fault tests must remain opt-in and skipped by default.

If a test cannot be run, state the reason in the final response.

## Implementation order

Build the durable local store before external integrations.

Use the Phase 1 Rust validator as the acceptance gate for transferred objects.
The transport, spool, catalog, and store layers may track local state, but they
must not redefine KV object identity or compatibility.

Recommended order:

1. Catalog schema and migrations.
2. Store record model and commit-time indexing for verified objects.
3. Lookup, query, stats, and daemon or CLI read surfaces.
4. Pin and unpin state.
5. Deterministic eviction policies and tests.
6. Prefix manifests and optional session manifests.
7. fsck reconciliation, quarantine, and repair.
8. Optional memory tier for verified objects.
9. ContextStorm store benchmark scenarios.

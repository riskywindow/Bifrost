# Phase 3 Store Layer

Last verified: 2026-05-30

## Objective

Phase 3 turns the Phase 2 transfer spool into a durable local KV object store.
The store indexes committed KV objects, answers local cache queries, maintains
pinning and eviction state, records object access, supports prefix and session
manifests, runs fsck, and exposes store-focused ContextStorm benchmarks.

The store is still local infrastructure. It is not an LMCache integration, a
vLLM integration, an inference engine, a distributed metadata service, or a
production cache cluster.

## Relationship to earlier phases

Phase 1 remains the source of truth for immutable KV object meaning:

1. Descriptor schema.
2. Payload hash.
3. Descriptor hash.
4. Object ID.
5. Target compatibility.
6. Validation error reason codes.

Phase 2 remains the source of truth for transport correctness:

1. Frames.
2. Chunking.
3. Reassembly.
4. PUT, HAS, and GET behavior.
5. Staging and committed spool areas.
6. JSONL traces and transfer metrics.

Phase 3 does not redefine object identity or transfer completion. It adds
durable local store metadata around already committed and verified objects.

## Durable object catalog

The Phase 3 catalog records local facts about committed objects:

1. Object identity and descriptor hashes copied from Phase 1 validation.
2. File paths, byte sizes, and file-level hashes.
3. Compatibility keys needed for lookup and query.
4. Pinning state.
5. Access timestamps and counters.
6. Manifest membership.
7. Store events and fsck findings.

The catalog must not make an object servable by itself. Serving requires both a
consistent catalog record and a valid committed file set. If catalog state and
filesystem state disagree, the store reports a miss or quarantine state until a
safe repair validates the object.

SQLite is the initial catalog backend because it provides local durability,
transactions, indexes, and migrations without introducing a service dependency.

## Disk tier

The disk tier is the authoritative Phase 3 storage tier. It builds on the
Phase 2 committed object layout and may keep descriptor and payload files under
the existing `objects/` tree.

Disk-tier invariants:

1. Staging files are never available cache hits.
2. Committed files are indexed only after Phase 1 validation succeeds.
3. Catalog commits and file commits must be crash-safe.
4. A committed object missing descriptor or payload files is unavailable.
5. A committed object with mismatched size, hash, descriptor hash, payload hash,
   object ID, or compatibility is unavailable and must be quarantined or marked
   suspect.

Initial Store API commit behavior writes committed descriptor and payload files
atomically, then inserts the catalog object, disk location, compatibility, and
access rows before marking the object verified. If catalog indexing fails after
the atomic file commit, the initial implementation removes the committed files
before returning the error. This fail-closed choice avoids leaving an object
that can become available without a consistent catalog row; future fsck repair
work may replace this with an explicit recoverable orphan/quarantine workflow.

## Optional memory tier

The memory tier is optional in Phase 3. It may cache descriptor bytes, payload
bytes, decoded metadata, or lookup results for objects already verified on disk.

Memory-tier rules:

1. Memory entries must reference a committed object ID.
2. Memory entries must not bypass compatibility or integrity checks.
3. Memory state must be disposable and reconstructable from disk plus catalog.
4. Memory residency must not affect immutable object identity.
5. Eviction from memory must not imply eviction from disk.

The initial implementation is an in-process CPU memory cache on each daemon or
store handle. It is disabled by default and uses simple LRU. It caches metadata
bytes for verified, servable objects and caches payload bytes only when payload
caching is explicitly enabled and the payload is within the configured maximum
object size. Every memory hit is preceded by the normal catalog and filesystem
servability check; stale memory entries cannot make an evicted, quarantined,
missing, corrupt, staged, or otherwise unavailable object servable.

`bifrost-daemon` exposes:

```text
--memory-tier-bytes N
--memory-tier-cache-payloads true|false
--memory-tier-max-object-bytes N
```

`--memory-tier-bytes` defaults to `0`, which disables the tier. Payload caching
defaults to `false` because payload bytes can be memory-heavy. Memory entries
are invalidated when store operations change object state, including pinning,
unpinning, TTL changes, eviction, quarantine, and re-verification.

## Store API

The local store API should be small and deterministic:

```text
put_committed(descriptor, payload, validation_result) -> object_record
has(object_id, target_profile) -> hit | miss | reject
get(object_id, target_profile) -> descriptor + payload | miss | reject
lookup(query) -> object summaries
stats() -> store stats
pin(object_id, reason) -> pinned
unpin(object_id, reason) -> unpinned
evict(policy, target) -> eviction report
manifest_put(manifest) -> manifest record
manifest_get(manifest_id) -> manifest record | miss
manifest_missing(manifest_id) -> missing member list
fsck(mode) -> findings and actions
```

`put_committed` is a store-internal operation used after transfer commit or
local import. It must not accept unvalidated objects.

`has`, `get`, `lookup`, and manifest queries must never read from staging or
return staged objects as available.

## Out of scope

Phase 3 must not implement:

1. LMCache integration.
2. Language-model integration.
3. vLLM integration.
4. Real model KV extraction.
5. Real KV injection.
6. GPU inference.
7. Dashboard work.
8. QUIC.
9. Compression.
10. RDMA.
11. Production authentication or authorization.
12. Parity chunks or erasure coding.
13. Kubernetes.
14. Distributed consensus or remote catalog replication.

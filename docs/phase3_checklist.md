# Phase 3 Checklist

Last verified: 2026-05-30

## Catalog

- [x] Create a local SQLite catalog under the store root.
- [x] Add `schema_migrations`.
- [x] Add `objects`.
- [x] Add `object_locations`.
- [x] Add `object_compatibility`.
- [x] Add `object_access`.
- [x] Add `prefix_manifests`.
- [x] Add `manifest_members`.
- [x] Add `store_events`.
- [x] Add deterministic indexes for lookup, state scans, access scans, and
      manifest membership.
- [x] Ensure staging objects are never indexed as available.
- [x] Ensure catalog rows do not redefine Phase 1 object identity.

## Migrations

- [x] Run migrations transactionally.
- [x] Record migration version, name, and timestamp.
- [x] Reject unknown future schema versions by default.
- [x] Test fresh database creation.
- [x] Test idempotent startup on an already-migrated database.
- [ ] Test failed migration rollback.

## Store API

- [x] Add store initialization and open checks.
- [x] Add commit-time indexing only after Phase 1 Rust validation succeeds.
- [x] Add `has` for verified catalog-consistent objects.
- [x] Add `get` for verified catalog-consistent objects.
- [x] Add query by object ID and compatibility fields.
- [x] Add local store stats.
- [x] Add deterministic error reasons for catalog, filesystem, integrity,
      compatibility, manifest, eviction, and fsck failures.
- [x] Ensure catalog and filesystem disagreement reports miss or rejection.
- [x] Ensure corrupt, missing, quarantined, staging, and evicting objects are
      not served.

## Daemon API

- [x] Keep Phase 2 PUT, HAS, and GET behavior green.
- [x] Index successful PUT commits in the Phase 3 catalog.
- [x] Add local store lookup endpoint or command surface.
- [x] Add store stats endpoint or command surface.
- [x] Add pin and unpin endpoint or command surface.
- [x] Add eviction dry-run and apply endpoint or command surface.
- [ ] Add fsck dry-run and repair endpoint or command surface.
- [x] Keep protocol errors separate from store errors.

## CLI

- [ ] Add `store init` or equivalent if explicit initialization is needed.
- [x] Add object lookup.
- [x] Add object stats.
- [x] Add object pin.
- [x] Add object unpin.
- [x] Add eviction dry-run.
- [x] Add eviction apply.
- [ ] Add manifest inspect.
- [ ] Add manifest missing.
- [ ] Add fsck dry-run.
- [ ] Add fsck repair.
- [x] Keep outputs deterministic enough for tests.

## Pinning

- [x] Persist pinned state in the catalog.
- [ ] Persist optional pin reason.
- [x] Record pin and unpin events.
- [x] Ensure pinned objects are never disk eviction victims.
- [x] Test pinning does not make corrupt or missing objects servable.
- [x] Test unpinning returns an otherwise eligible object to eviction
      eligibility.

## Eviction

- [x] Implement LRU dry-run.
- [x] Implement LRU apply.
- [x] Implement size-aware LRU dry-run.
- [x] Implement size-aware LRU apply.
- [x] Implement TTL expiration dry-run.
- [x] Implement TTL expiration apply.
- [x] Implement target-byte eviction.
- [x] Use fixed clock inputs in tests.
- [x] Break ties by object ID.
- [x] Skip and report pinned objects.
- [x] Record eviction events.
- [x] Test partial deletion and catalog update failures.

## Manifests

- [ ] Add prefix manifest creation.
- [ ] Add prefix manifest lookup.
- [ ] Add ordered manifest members.
- [ ] Add completeness checks.
- [ ] Add missing-block queries.
- [ ] Add optional session manifest model if needed.
- [ ] Ensure manifests reference object IDs, not mutable file paths.
- [ ] Ensure required evicted, missing, corrupt, quarantined, or staging
      members make the manifest incomplete.
- [ ] Test deterministic member ordering.

## fsck

- [ ] Add catalog scan.
- [ ] Add committed filesystem scan.
- [ ] Add staging scan.
- [ ] Add quarantine scan.
- [ ] Detect corrupt descriptors.
- [ ] Detect corrupt payloads.
- [ ] Detect object ID mismatches.
- [ ] Detect missing descriptor files.
- [ ] Detect missing payload files.
- [ ] Detect orphan descriptor and payload files.
- [ ] Detect manifest members that are unavailable.
- [ ] Add dry-run report mode.
- [ ] Add conservative repair mode.
- [ ] Quarantine suspect objects.
- [ ] Never serve suspect objects during or after fsck.

## Memory tier

- [ ] Keep memory tier optional.
- [ ] Cache only already-verified objects or derived metadata.
- [ ] Ensure memory entries reference committed object IDs.
- [ ] Ensure memory misses fall back to disk and catalog validation.
- [ ] Ensure memory eviction does not imply disk eviction.
- [ ] Ensure memory state is reconstructable.
- [ ] Test restart with empty memory tier.

## ContextStorm store benchmarks

- [ ] Add CPU-only local store benchmark scenarios.
- [ ] Add lookup-heavy workload.
- [ ] Add GET-hit workload from verified disk objects.
- [ ] Add manifest missing-block workload.
- [ ] Add pin and eviction workload.
- [ ] Add fsck dry-run workload.
- [ ] Record catalog latency, lookup latency, GET latency, hit rate, miss rate,
      eviction victims, bytes freed, fsck findings, and manifest completeness.
- [ ] Keep benchmark inputs deterministic.
- [ ] Keep default scenarios free of GPU, LMCache, vLLM, Docker, Kubernetes,
      cloud credentials, and internet access.

## CI

- [ ] Keep Phase 1 Python tests green.
- [x] Keep Phase 1 Rust tests green.
- [x] Keep cross-language identity vector tests green.
- [x] Keep Phase 2 protocol, chunker, spool, PUT, HAS, GET, and ContextStorm
      transport tests green.
- [x] Add Phase 3 catalog unit tests.
- [x] Add Phase 3 store API tests.
- [x] Add Phase 3 eviction tests.
- [ ] Add Phase 3 manifest tests.
- [ ] Add Phase 3 fsck tests.
- [ ] Add ContextStorm store smoke scenario.
- [ ] Keep all default CI tests CPU-only and local.

## Phase 3 done criteria

- [x] A committed Phase 2 object is indexed in a durable local catalog only
      after Phase 1 Rust validation succeeds.
- [x] Store lookup and stats work after daemon restart.
- [x] HAS and GET only serve verified, catalog-consistent objects.
- [x] Staging objects are never listed as available cache hits.
- [x] File-level integrity and catalog consistency are checked before serving.
- [x] Pinning is durable and prevents eviction.
- [x] Eviction is deterministic, testable, and never evicts pinned objects.
- [ ] Prefix manifests support completeness and missing-block queries.
- [ ] fsck detects catalog/filesystem drift and quarantines suspect objects.
- [ ] ContextStorm includes CPU-only deterministic store benchmarks.
- [ ] No LMCache, language-model integration, vLLM, real KV extraction, GPU
      inference, dashboard, QUIC, compression, RDMA, parity chunk, production
      auth, or Kubernetes work is included.

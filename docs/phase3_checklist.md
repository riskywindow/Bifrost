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
- [x] Add fsck dry-run and repair endpoint or command surface.
- [x] Keep protocol errors separate from store errors.

## CLI

- [ ] Add `store init` or equivalent if explicit initialization is needed.
- [x] Add object lookup.
- [x] Add object stats.
- [x] Add object pin.
- [x] Add object unpin.
- [x] Add eviction dry-run.
- [x] Add eviction apply.
- [x] Add manifest inspect.
- [x] Add manifest missing.
- [x] Add fsck dry-run.
- [x] Add fsck repair.
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

- [x] Add prefix manifest creation.
- [x] Add prefix manifest lookup.
- [x] Add ordered manifest members.
- [x] Add completeness checks.
- [x] Add missing-block queries.
- [x] Add optional session manifest model if needed.
- [x] Ensure manifests reference object IDs, not mutable file paths.
- [x] Ensure required evicted, missing, corrupt, quarantined, or staging
      members make the manifest incomplete.
- [x] Test deterministic member ordering.

## fsck

- [x] Add catalog scan.
- [x] Add committed filesystem scan.
- [x] Add staging scan.
- [ ] Add quarantine scan.
- [x] Detect corrupt descriptors.
- [x] Detect corrupt payloads.
- [x] Detect object ID mismatches.
- [x] Detect missing descriptor files.
- [x] Detect missing payload files.
- [x] Detect orphan descriptor and payload files.
- [x] Detect manifest members that are unavailable.
- [x] Add dry-run report mode.
- [x] Add conservative repair mode.
- [x] Quarantine suspect objects.
- [x] Never serve suspect objects during or after fsck.

## Memory tier

- [x] Keep memory tier optional.
- [x] Cache only already-verified objects or derived metadata.
- [x] Ensure memory entries reference committed object IDs.
- [x] Ensure memory misses fall back to disk and catalog validation.
- [x] Ensure memory eviction does not imply disk eviction.
- [x] Ensure memory state is reconstructable.
- [x] Test restart with empty memory tier.

## ContextStorm store benchmarks

- [x] Add CPU-only local store benchmark scenarios.
- [x] Add lookup-heavy workload.
- [x] Add GET-hit workload from verified disk objects.
- [x] Add manifest missing-block workload.
- [x] Add pin and eviction workload.
- [x] Add fsck dry-run workload.
- [x] Record catalog latency, lookup latency, GET latency, hit rate, miss rate,
      eviction victims, bytes freed, fsck findings, and manifest completeness.
- [x] Keep benchmark inputs deterministic.
- [x] Keep default scenarios free of GPU, LMCache, vLLM, Docker, Kubernetes,
      cloud credentials, and internet access.

## Phase 3 hardening notes

- [x] Manifest member insertion now requires the object to be serveable at
      insertion time; missing, quarantined, corrupt, evicted, staging, or
      catalog-inconsistent objects are rejected or reported missing.
- [x] Eviction start rechecks the guarded catalog update so an object that
      becomes pinned before eviction is not treated as an eviction victim.
- [x] Store byte stats count disk locations only.
- [x] fsck orphan repair imports only valid descriptor/payload pairs located
      at the deterministic committed object paths.
- [x] Quarantine chooses a non-overwriting quarantine directory when a previous
      quarantine for the same object already exists.
- [x] ContextStorm scenario loading is stable from either the repository root
      or the `contextstorm` package directory.
- [x] Committed-only catalog rows cannot be pinned into availability; pinning
      requires the object to have reached verified state first.

## CI

- [x] Keep Phase 1 Python tests green.
- [x] Keep Phase 1 Rust tests green.
- [x] Keep cross-language identity vector tests green.
- [x] Keep Phase 2 protocol, chunker, spool, PUT, HAS, GET, and ContextStorm
      transport tests green.
- [x] Add Phase 3 catalog unit tests.
- [x] Add Phase 3 store API tests.
- [x] Add Phase 3 eviction tests.
- [x] Add Phase 3 manifest tests.
- [x] Add Phase 3 fsck tests.
- [x] Add ContextStorm store smoke scenario.
- [x] Keep all default CI tests CPU-only and local.

### Phase 3 CI and local test commands

The Phase 3 GitHub Actions workflow lives at `.github/workflows/phase3.yml`.
It installs editable Python packages for `bifrost_py` and `contextstorm`,
runs Phase 1 Python tests, builds all `bifrostd` binaries, runs
`bifrostd` Rust tests, runs ContextStorm unit tests, runs the Phase 2
`small_ci` transport smoke scenario, and runs the Phase 3 `store_small_ci`
store scenario.

Default CI must stay CPU-only and local. It must not run root-required network
fault tests, long store benchmarks, large payload transfers, GPU workloads,
LMCache or vLLM integration, dashboard tasks, Docker, Kubernetes, cloud
credentials, or external services. Optional scenarios may be run manually, but
skips or failures from those optional runs must not fail CI.

Run the full local Rust test suite from the repository root:

```text
cargo test --manifest-path bifrostd/Cargo.toml
```

Build the Rust binaries used by ContextStorm scenarios:

```text
cargo build --manifest-path bifrostd/Cargo.toml --bins
```

Run Python tests from the repository root:

```text
python -m pip install -e "bifrost_py[dev]" -e "contextstorm[dev]"
pytest bifrost_py/tests tests
cd contextstorm
pytest tests
cd ..
```

Run the required small Phase 3 store scenario after building Rust binaries:

```text
contextstorm run contextstorm/scenarios/store_small_ci.yaml \
  --runs-root /tmp/contextstorm-runs \
  --run-id phase3-store-small
contextstorm report /tmp/contextstorm-runs/phase3-store-small
```

Run the small Phase 2 transport smoke scenario that remains in Phase 3 CI:

```text
contextstorm run contextstorm/scenarios/small_ci.yaml \
  --runs-root /tmp/contextstorm-runs \
  --run-id phase3-transport-small
```

Run optional longer store scenarios manually. These are outside required CI:

```text
contextstorm run contextstorm/scenarios/store_eviction.yaml \
  --runs-root /tmp/contextstorm-runs \
  --run-id phase3-store-eviction

contextstorm run contextstorm/scenarios/store_manifest.yaml \
  --runs-root /tmp/contextstorm-runs \
  --run-id phase3-store-manifest

contextstorm run contextstorm/scenarios/store_memory_tier.yaml \
  --runs-root /tmp/contextstorm-runs \
  --run-id phase3-store-memory-tier
```

Run a manual Phase 3 store workflow against a local daemon:

```text
rm -rf /tmp/bifrost-store-manual
mkdir -p /tmp/bifrost-store-manual
cargo build --manifest-path bifrostd/Cargo.toml --bins
bifrostd/target/debug/bifrost-daemon \
  --listen 127.0.0.1:7420 \
  --spool /tmp/bifrost-store-manual \
  --trace-jsonl /tmp/bifrost-store-manual/daemon.jsonl
```

In another terminal:

```text
OBJECT_ID=$(
  bifrostd/target/debug/bifrost-xfer put \
    --endpoint 127.0.0.1:7420 \
    --meta fixtures/native_valid/tiny_gpt_layer0_block0.meta.json \
    --payload fixtures/native_valid/tiny_gpt_layer0_block0.payload.bin \
    --target fixtures/native_valid/target_profile.json \
    --json | python -c 'import json,sys; print(json.load(sys.stdin)["object_id"])'
)

bifrostd/target/debug/bifrost-xfer get \
  --endpoint 127.0.0.1:7420 \
  --object-id "$OBJECT_ID" \
  --out /tmp/bifrost-store-manual/get \
  --json

cmp fixtures/native_valid/tiny_gpt_layer0_block0.payload.bin \
  /tmp/bifrost-store-manual/get/payload.bin

bifrostd/target/debug/bifrost-store list \
  --endpoint 127.0.0.1:7420 \
  --json

bifrostd/target/debug/bifrost-store inspect \
  --endpoint 127.0.0.1:7420 \
  --object-id "$OBJECT_ID" \
  --json

bifrostd/target/debug/bifrost-store pin \
  --endpoint 127.0.0.1:7420 \
  --object-id "$OBJECT_ID"

bifrostd/target/debug/bifrost-store evict \
  --endpoint 127.0.0.1:7420 \
  --policy lru \
  --target-bytes 0 \
  --dry-run \
  --json

bifrostd/target/debug/bifrost-store fsck \
  --endpoint 127.0.0.1:7420 \
  --check \
  --json
```

## Phase 3 done criteria

- [x] A committed Phase 2 object is indexed in a durable local catalog only
      after Phase 1 Rust validation succeeds.
- [x] Store lookup and stats work after daemon restart.
- [x] HAS and GET only serve verified, catalog-consistent objects.
- [x] Staging objects are never listed as available cache hits.
- [x] File-level integrity and catalog consistency are checked before serving.
- [x] Pinning is durable and prevents eviction.
- [x] Eviction is deterministic, testable, and never evicts pinned objects.
- [x] Prefix manifests support completeness and missing-block queries.
- [x] fsck detects catalog/filesystem drift and quarantines suspect objects.
- [x] ContextStorm includes CPU-only deterministic store benchmarks.
- [x] No LMCache, language-model integration, vLLM, real KV extraction, GPU
      inference, dashboard, QUIC, compression, RDMA, parity chunk, production
      auth, or Kubernetes work is included.

# ContextStorm Synthetic Benchmark

Last verified: 2026-05-29

ContextStorm is the Phase 2 synthetic KV benchmark harness and Phase 3 local
store benchmark harness. It exercises local BIFROST transport and store
behavior with deterministic Phase 1-style KV objects and records the artifacts
needed to inspect correctness and basic behavior.

ContextStorm is not a model benchmark. It does not run inference, allocate GPU
KV cache, call LMCache, call vLLM, emulate QUIC, compress payloads, or use
root-required network mutation unless an operator explicitly passes the local
fault opt-in flag.

## Commands

From `contextstorm/`:

```text
cd ../bifrostd
cargo build --bins
cd ../contextstorm
contextstorm generate-synthetic --out /tmp/bifrost-object --size 1048576
contextstorm run scenarios/small_ci.yaml \
  --runs-root /tmp/contextstorm-runs \
  --run-id phase2-local-small
contextstorm report /tmp/contextstorm-runs/phase2-local-small
```

When running directly from the source tree without installing the package:

```text
PYTHONPATH=. python -m contextstorm.cli run scenarios/small_ci.yaml \
  --runs-root /tmp/contextstorm-runs \
  --run-id phase2-local-small
PYTHONPATH=. python -m contextstorm.cli report /tmp/contextstorm-runs/phase2-local-small
```

Root-required local network profiles are disabled by default. To run a `tc`
profile on a local machine where you intentionally allow qdisc changes:

```text
sudo PYTHONPATH=. python -m contextstorm.cli run scenarios/lossy_two_path.yaml \
  --allow-root-faults \
  --runs-root /tmp/contextstorm-runs \
  --run-id phase2-local-lossy-two-path
```

The process-level runner requires built Rust binaries:

```text
cd bifrostd
cargo build
```

ContextStorm looks for `bifrost-daemon` and `bifrost-xfer` in
`bifrostd/target/debug/`, `target/debug/`, `$PATH`, or explicit
`BIFROST_DAEMON` and `BIFROST_XFER` environment variables.

Store scenarios also require `bifrost-store` in the same locations or via the
`BIFROST_STORE` environment variable:

```text
PYTHONPATH=. python -m contextstorm.cli run scenarios/store_small_ci.yaml \
  --runs-root /tmp/contextstorm-runs \
  --run-id phase3-store-small
PYTHONPATH=. python -m contextstorm.cli report /tmp/contextstorm-runs/phase3-store-small
```

## Scenario Format

Scenario files are small YAML documents using only simple mappings and lists:

```yaml
name: small_ci
object_size_bytes: 1048576
chunk_size_bytes: 262144
object_type: opaque_engine_blob
paths:
  - name: primary
    start_daemon: true
operations: [put, has, get]
repetitions: 1
timeout_seconds: 30
```

Supported fields:

1. `name`
2. `object_size_bytes`
3. `chunk_size_bytes`
4. `object_type`: `native_kv_page` or `opaque_engine_blob`
5. `model_shape`, optional approximate synthetic metadata:
   `layers`, `num_kv_heads`, `head_dim`, `tokens`, and `dtype`
6. `paths`: one or more local path entries with `name`, optional `endpoint`,
   and `start_daemon`
7. `operations`: `put`, `has`, and/or `get`
8. `repetitions`
9. `fault_profile`, optional profile name or profile path
10. `timeout_seconds`

Phase 3 store scenarios use `workload: store` and store operation names:

```yaml
name: store_small_ci
workload: store
object_count: 5
object_size_bytes: 1 MiB
chunk_size_bytes: 256 KiB
operations:
  - put_objects
  - list_objects
  - query_objects
  - get_objects
  - fsck
memory_tier_bytes: 0
repetitions: 1
timeout_seconds: 30
```

Store scenario fields:

1. `name`
2. `workload: store`
3. `object_count`
4. `object_size_bytes`, integer bytes or `KiB`/`MiB`/`GiB`
5. `chunk_size_bytes`, integer bytes or `KiB`/`MiB`/`GiB`
6. `object_type`: currently `opaque_engine_blob` by default
7. `operations`
8. `memory_tier_bytes`
9. `memory_tier_cache_payloads`
10. `memory_tier_max_object_bytes`
11. `target_bytes`, for eviction scenarios
12. `pin_fraction`, for pin and eviction scenarios
13. `policy`, such as `lru`
14. `manifest_complete_before_eviction`
15. `manifest_complete_after_eviction`
16. `repetitions`
17. `timeout_seconds`

Supported store operations:

1. `put_objects`
2. `get_objects`
3. `has_objects`
4. `list_objects`
5. `query_objects`
6. `inspect_objects`
7. `pin_objects`
8. `unpin_objects`
9. `evict`
10. `create_manifest`
11. `add_manifest_members`
12. `check_manifest`
13. `fsck`

The built-in scenarios are:

1. `small_ci.yaml`: 1 MiB, one daemon, PUT/HAS/GET once.
2. `local_single_path.yaml`: 16 MiB, one daemon, three repetitions.
3. `local_two_path.yaml`: 16 MiB, two local daemons, multipath PUT, three
   repetitions.
4. `path_failure.yaml`: one live local path plus one intentionally missing
   endpoint. This remains local and does not require root.
5. `lossy_two_path.yaml`: two local daemons with `loss_1pct`; requires
   `--allow-root-faults` and root to apply `tc_netem`.
6. `dead_path.yaml`: two local daemons with `path_death`; kills the secondary
   daemon and does not require root.
7. `store_small_ci.yaml`: five 1 MiB objects, store list/query/get/fsck.
8. `store_eviction.yaml`: pinned-object protection and deterministic LRU
   target-byte eviction.
9. `store_manifest.yaml`: prefix manifest creation, membership, completeness,
   eviction, and completeness recheck.
10. `store_memory_tier.yaml`: optional memory tier hit/miss counters.

## Workload Classes

ContextStorm uses stable workload labels in docs and scenario names:

1. `tiny`: sub-MiB synthetic objects for very fast unit-style checks.
2. `small`: 1 MiB CI smoke objects; `small_ci.yaml` is the default.
3. `medium`: 16 MiB loopback scenarios that expose chunking and throughput.
4. `large`: opt-in local scenarios for sustained spool and transfer behavior.

CI should stay on `small_ci.yaml` unless maintainers intentionally expand the
budget. Larger local runs should be treated as exploratory measurements, not as
portable performance thresholds.

## Synthetic Objects

Synthetic payload bytes are deterministic. Object descriptors are built from the
Phase 1 Python fixture and hashing helpers, then finalized with the Phase 1
object identity algorithm. For Phase 2, `object_size_bytes` is the controlling
workload size. `model_shape` updates metadata enough to make reports readable,
but ContextStorm does not claim those bytes are real model KV tensors.

For `native_kv_page`, ContextStorm keeps the Phase 1 native tensor byte-length
invariant intact. If `model_shape` is omitted, `object_size_bytes` must be a
positive multiple of four and ContextStorm derives a simple valid shape. If
`model_shape` is provided, its implied byte length must exactly match
`object_size_bytes`.

Generated artifacts:

```text
meta.json
payload.bin
target_profile.json
manifest.json
```

The runner passes `meta.json`, `payload.bin`, and `target_profile.json` through
`bifrost-xfer put`, so the daemon still performs Rust-side Phase 1 validation
before commit.

## Run Artifacts

Each run writes to `runs/<timestamp_or_id>/`:

```text
scenario.yaml
run.json
inputs/rep_000/{meta.json,payload.bin,target_profile.json,manifest.json}
outputs/rep_000/{meta.json,payload.bin}
traces/daemon_primary.jsonl
traces/put_000.jsonl
traces/get_000.jsonl
commands/put_000.json
commands/has_000.json
commands/get_000.json
summary.json
summary.md
```

`run.json` records stdout, stderr, exit code, parsed CLI JSON, trace paths,
environment notes, and per-operation metrics. `contextstorm run` writes
`summary.json` and `summary.md` automatically after `run.json`; `contextstorm
report RUN_DIR` regenerates those summaries.

Fault-enabled runs also write:

```text
fault_events.jsonl
```

`fault_events.jsonl` records profile loading, apply, skip, and cleanup events.
For `tc_netem`, ContextStorm prints the exact `tc` apply and cleanup commands
before any command is run.

## Fault Profiles

Profiles live under `contextstorm/network_profiles/` and use these fields:

1. `type`: `none`, `tc_netem`, `process_kill`, or `artificial_delay`
2. `interface`: network interface for `tc_netem`, such as `lo`
3. `delay_ms`
4. `jitter_ms`
5. `loss_percent`
6. `rate_mbit`
7. `apply_at_ms`
8. `remove_at_ms`
9. `target_path`: path name for process and ContextStorm-side timing faults

Safe-by-default profiles:

1. `clean.yaml`: no fault.
2. `path_death.yaml`: process kill of one ContextStorm-managed daemon. This is
   local, CPU-only, and does not require root. It is useful as an opt-in local
   fault scenario and is covered by tests when Rust binaries and loopback TCP
   are available, but it is intentionally kept out of the default small CI
   smoke path.

Root-required profiles:

1. `delay_50ms.yaml`
2. `loss_1pct.yaml`
3. `loss_5pct.yaml`
4. `bandwidth_50mbit.yaml`

`tc_netem` profiles are skipped unless `--allow-root-faults` is passed. Even
with the flag, ContextStorm checks for root privileges and the `tc` command
before applying the profile. Cleanup is attempted in the runner `finally` path,
and skipped cleanup is recorded clearly when permissions or tools are absent.

## Metrics

ContextStorm computes:

1. `transfer_duration_ms`
2. `effective_throughput_mib_s`
3. `bytes_sent`
4. `bytes_received`
5. `chunks_sent`
6. `retries`
7. `timeouts`
8. `success`
9. `reason_code`
10. `committed_object_verified`
11. `get_payload_matches_put_payload`

Store scenarios compute:

1. `put_duration_ms`
2. `get_duration_ms`
3. `has_latency_ms`
4. `list_latency_ms`
5. `query_latency_ms`
6. `inspect_latency_ms`
7. `fsck_duration_ms`
8. `eviction_duration_ms`
9. `objects_inserted`
10. `objects_evicted`
11. `objects_pinned`
12. `bytes_committed`
13. `bytes_evicted`
14. `manifest_completeness`
15. `store_bytes_before`
16. `store_bytes_after`
17. `memory_tier_hits`
18. `memory_tier_misses`

Store reports include correctness checks for `payload_roundtrip_match`,
`pinned_not_evicted`, `fsck_clean_after_run`, and
`manifest_completeness_expected`.

Metrics are derived from `bifrost-xfer --json` snapshots when present and from
trace JSONL files as a fallback. `committed_object_verified` is true only when a
HAS check confirms the object is present after PUT. `get_payload_matches_put_payload`
is true only when the fetched payload bytes exactly match the generated payload.

## Reports

`contextstorm report RUN_DIR` writes:

1. `summary.json`
2. `summary.md`

The Markdown report includes an overview, a per-run or per-operation metrics
table, environment notes, and a reminder that the benchmark is local synthetic
transport or store behavior only.

## Tests

The default Python tests cover deterministic synthetic generation, scenario
loading, trace and store metric parsing, fault profile loading and skip
behavior, report writing, and process-level `small_ci` and `store_small_ci`
smoke tests. Process-level tests skip when Rust binaries are not built.

```text
cd contextstorm
PYTHONPATH=. pytest
```

CI should not require root, Docker, `tc`, netem, GPU hardware, internet access,
LMCache, or vLLM. Root-required fault tests must stay opt-in and skipped by
default.

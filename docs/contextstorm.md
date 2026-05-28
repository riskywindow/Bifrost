# ContextStorm Synthetic Benchmark

Last verified: 2026-05-27

ContextStorm is the Phase 2 synthetic KV benchmark harness. It exercises local
BIFROST transport with deterministic Phase 1-style KV objects and records the
artifacts needed to inspect correctness and basic transfer behavior.

ContextStorm is not a model benchmark. It does not run inference, allocate GPU
KV cache, call LMCache, call vLLM, emulate QUIC, compress payloads, or use
root-required network mutation.

## Commands

From `contextstorm/`:

```text
contextstorm generate-synthetic --out /tmp/bifrost-object --size 1048576
contextstorm run scenarios/small_ci.yaml
contextstorm report ../runs/RUN_ID
```

When running directly from the source tree without installing the package:

```text
PYTHONPATH=. python -m contextstorm.cli run scenarios/small_ci.yaml
PYTHONPATH=. python -m contextstorm.cli report ../runs/RUN_ID
```

The process-level runner requires built Rust binaries:

```text
cd bifrostd
cargo build
```

ContextStorm looks for `bifrost-daemon` and `bifrost-xfer` in
`bifrostd/target/debug/`, `target/debug/`, `$PATH`, or explicit
`BIFROST_DAEMON` and `BIFROST_XFER` environment variables.

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
9. `fault_profile`, optional label for later opt-in fault profiles
10. `timeout_seconds`

The built-in scenarios are:

1. `small_ci.yaml`: 1 MiB, one daemon, PUT/HAS/GET once.
2. `local_single_path.yaml`: 16 MiB, one daemon, three repetitions.
3. `local_two_path.yaml`: 16 MiB, two local daemons, multipath PUT, three
   repetitions.
4. `path_failure.yaml`: one live local path plus one intentionally missing
   endpoint. This remains local and does not require root.

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
environment notes, and per-operation metrics.

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

Metrics are derived from `bifrost-xfer --json` snapshots when present and from
trace JSONL files as a fallback. `committed_object_verified` is true only when a
HAS check confirms the object is present after PUT. `get_payload_matches_put_payload`
is true only when the fetched payload bytes exactly match the generated payload.

## Reports

`contextstorm report RUN_DIR` writes:

1. `summary.json`
2. `summary.md`

The Markdown report includes an overview, a per-run metrics table, environment
notes, and a reminder that the benchmark is local synthetic transport only.

## Tests

The default Python tests cover deterministic synthetic generation, scenario
loading, trace metric parsing, report writing, and a process-level `small_ci`
smoke test. The process-level test skips when Rust binaries are not built.

```text
cd contextstorm
PYTHONPATH=. pytest
```

# Phase 2 Checklist

Last verified: 2026-05-29

## Protocol

- [x] Define `bifrost.transport.v1alpha1` constants in Rust.
- [x] Encode frames as `u32 header_len`, JSON header, and raw payload bytes.
- [x] Decode frames independent of TCP read boundaries.
- [x] Validate required header fields for every frame type.
- [x] Reject unsupported versions.
- [x] Reject unknown frame types.
- [x] Reject malformed headers and payload length mismatches.
- [x] Reject oversized headers and payloads before allocation.
- [x] Add deterministic protocol error codes.
- [x] Cover `hello`, `ping`, `pong`, and `error` with unit tests.

## Chunker

- [x] Chunk payload bytes deterministically by configured chunk size.
- [x] Record chunk index, offset, length, and chunk hash.
- [x] Reject invalid chunk sizes.
- [x] Reject chunk offset and length mismatches.
- [x] Reject chunk hash mismatches.
- [x] Accept duplicate chunks only when bytes and metadata match.
- [x] Detect missing chunks before commit.
- [x] Test zero-length, one-chunk, exact-boundary, and multi-chunk payloads.

## Single-path PUT

- [x] Implement local TCP client and daemon handshake.
- [x] Send `put_begin` with descriptor bytes.
- [x] Create staging state only after transfer shape validation.
- [x] Send and acknowledge chunk frames.
- [ ] Retry rejected chunks while the request is live.
- [x] Send `put_commit` only after all chunks are acknowledged.
- [x] Reassemble full payload from staging.
- [x] Run Phase 1 Rust validation before commit.
- [x] Return `put_result` with stable status and reason.
- [x] Test successful PUT for valid Phase 1 fixtures.
- [x] Test rejected PUT for invalid descriptor, payload hash mismatch, and
      object ID mismatch.

## Single-path GET

- [x] Return miss for absent objects.
- [x] Return miss for staged objects.
- [x] Return miss or rejection for corrupt committed records.
- [x] Return descriptor bytes in `get_result` for committed objects.
- [x] Return payload bytes as chunk frames.
- [x] Verify chunks on the client side.
- [x] Verify returned descriptor and payload with Phase 1 validation.
- [x] Test GET after PUT round trip.
- [x] Test GET does not serve from staging.

## Spool

- [x] Create a minimal spool layout with `staging/`, `objects/`, and
      `quarantine/`.
- [x] Keep transfer state outside immutable descriptors.
- [x] Keep committed record state outside immutable descriptors.
- [x] Write incomplete transfers only under staging.
- [x] Atomically commit validated objects into `objects/{object_id}`.
- [x] Handle already-committed identical objects deterministically.
- [x] Reject conflicting object paths.
- [ ] Clean up or quarantine stale staging records on startup.
- [x] Never serve partial, corrupt, unknown, or staged objects.
- [x] Test crash-style interrupted committed records with temporary
      directories.

## Metrics

- [x] Count PUT attempts, commits, rejections, and protocol errors.
- [x] Count GET attempts, hits, misses, rejections, and protocol errors.
- [ ] Count bytes transferred and bytes committed.
- [ ] Count chunks, duplicate chunks, missing chunks, and hash mismatches.
- [ ] Count Phase 1 validation failures by reason code.
- [ ] Measure PUT and GET latency.
- [ ] Report ContextStorm throughput.
- [x] Keep metrics local and dependency-light.
- [x] Test that expected metrics are emitted for success and failure paths.
- [x] Count multipath chunk retries, ack timeouts, dead paths, and transfer
      failures.

## Multipath

- [x] Start only after single-path PUT and GET are correct.
- [x] Use multiple local TCP connections for synthetic transfer.
- [x] Preserve one object identity across all paths.
- [x] Reassemble chunks deterministically regardless of arrival path.
- [x] Handle duplicate chunks from different paths.
- [x] Commit only after all chunks and Phase 1 validation pass.
- [ ] Report per-path bytes, chunks, errors, and latency in public snapshots.
- [x] Keep QUIC, RDMA, compression, and parity chunks out of scope.

## Retry and timeout

- [x] Define request timeout behavior.
- [x] Define initial chunk retry behavior for multipath PUT path failures.
- [x] Define connection close behavior for in-flight multipath PUT chunk retry.
- [x] Retry timed-out multipath PUT chunks on another healthy path when
      available.
- [x] Enforce a per-chunk retry ceiling.
- [x] Reject PUT on commit if any chunk is missing.
- [ ] Remove or quarantine abandoned staging state.
- [x] Make timeout tests deterministic and local.
- [x] Keep root-required network fault tests opt-in and skipped by default.

## ContextStorm

- [x] Define `tiny`, `small`, `medium`, and `large` workload guidance.
- [x] Keep `small_ci` suitable for CI.
- [x] Generate or select valid Phase 1 objects for benchmark inputs.
- [x] Run PUT workloads through the daemon.
- [x] Run GET workloads only against committed objects.
- [x] Validate returned payload bytes against generated payload bytes.
- [x] Record latency, throughput, chunks, validation failures, misses, and
      retries.
- [x] Add loopback single-path, two-path, and local path-failure scenarios.
- [x] Keep root-required fault profiles local opt-in.

## CI

- [x] Keep all default tests CPU-only and local.
- [x] Run Phase 1 Python tests.
- [x] Run Phase 1 Rust tests.
- [x] Run cross-language identity vector tests.
- [x] Run Phase 2 protocol unit tests.
- [x] Run Phase 2 chunker and reassembly tests.
- [x] Run Phase 2 spool tests.
- [x] Run local TCP PUT and GET smoke tests.
- [x] Run ContextStorm `small_ci` smoke test.
- [x] Skip root-required network fault tests by default.

### CI and local test commands

The Phase 2 GitHub Actions workflow lives at `.github/workflows/phase2.yml`.
It installs both Python packages, runs Phase 1 Python tests, runs ContextStorm
unit tests, runs `bifrostd` Rust tests, builds the Phase 2 binaries, and runs
the local `small_ci` ContextStorm scenario. The smoke scenario uses a 1 MiB
object and loopback TCP only; it does not require root, network emulation, GPU,
Docker, LMCache, vLLM, or internet access during the scenario.

Run the full default local checks from the repository root:

```text
python -m pip install -e "bifrost_py[dev]" -e "contextstorm[dev]"
pytest bifrost_py/tests tests
cargo test --manifest-path bifrostd/Cargo.toml
cargo build --manifest-path bifrostd/Cargo.toml --bins
cd contextstorm
pytest tests
cd ..
contextstorm run contextstorm/scenarios/small_ci.yaml \
  --runs-root /tmp/contextstorm-runs \
  --run-id phase2-local-small
```

Run only the small CI scenario after building binaries:

```text
cargo build --manifest-path bifrostd/Cargo.toml --bins
contextstorm run contextstorm/scenarios/small_ci.yaml \
  --runs-root /tmp/contextstorm-runs \
  --run-id phase2-local-small
```

Run optional local fault scenarios explicitly. These are not part of default CI,
and failures or skips from root-required fault setup must not fail CI:

```text
contextstorm run contextstorm/scenarios/dead_path.yaml \
  --runs-root /tmp/contextstorm-runs \
  --run-id phase2-local-dead-path

sudo contextstorm run contextstorm/scenarios/lossy_two_path.yaml \
  --allow-root-faults \
  --runs-root /tmp/contextstorm-runs \
  --run-id phase2-local-lossy-two-path
```

## Phase 2 done criteria

- [x] A valid Phase 1 object can be PUT over single-path TCP.
- [x] The daemon chunks, reassembles, and revalidates the object before commit.
- [x] The daemon never commits incomplete or invalid objects.
- [x] The daemon never serves from staging.
- [x] A committed object can be fetched with GET and revalidated by the client.
- [x] HAS returns true only for committed and servable objects.
- [x] Metrics exist for successful and failed transfer paths.
- [x] ContextStorm can run a CPU-only local benchmark.
- [x] Multipath synthetic PUT transfer is implemented.
- [x] No LMCache, vLLM, real KV extraction, GPU inference, dashboard, QUIC,
      compression, RDMA, production auth, or cache eviction work is included.

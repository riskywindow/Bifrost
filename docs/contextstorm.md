# ContextStorm Synthetic Benchmark

Last verified: 2026-05-27

## Purpose

ContextStorm is the Phase 2 synthetic KV benchmark harness.

Its purpose is to exercise the local transport path with deterministic synthetic
KV objects:

1. Generate or reuse valid Phase 1 fixture-style objects.
2. PUT them through the daemon over TCP.
3. GET committed objects back from the daemon.
4. Verify that returned objects still pass Phase 1 identity and compatibility
   validation.
5. Record local metrics for throughput, latency, misses, validation failures,
   and retry behavior.

ContextStorm is not a model benchmark. It does not run inference, allocate GPU
KV cache, call LMCache, call vLLM, or measure token latency.

## Workload sizes

The benchmark should define stable workload classes:

```text
tiny:
  object payloads small enough for fast CI smoke tests

small:
  dozens to hundreds of objects sized like small KV pages

medium:
  enough payload bytes to expose chunking and throughput behavior locally

large:
  local opt-in only; useful for sustained transfer and spool behavior
```

Each workload should report object count, payload bytes per object, total
payload bytes, chunk size, chunk count, and request concurrency.

CI should use `tiny` only unless maintainers explicitly expand the budget.

## Network profiles

ContextStorm should start with loopback TCP profiles:

```text
loopback_baseline:
  no artificial delay or packet loss

loopback_parallel:
  multiple client workers against one local daemon

loopback_slow_reader:
  client or daemon intentionally reads slowly to exercise backpressure

loopback_fault_opt_in:
  root-required host network mutation or packet loss simulation
```

Root-required network fault profiles must be opt-in and skipped by default.
They must not run in normal CI.

## Metrics

ContextStorm should collect:

1. PUT count, success count, rejection count, and error count.
2. GET count, hit count, miss count, rejection count, and error count.
3. Object bytes and payload bytes transferred.
4. Chunk count and duplicate chunk count.
5. Chunk hash mismatch count.
6. Phase 1 validation failure count by reason code.
7. PUT latency.
8. GET latency.
9. End-to-end throughput.
10. Spool committed object count and committed byte count.
11. Retry and timeout counts.

Metrics should be deterministic enough for tests to assert presence and basic
sanity. Performance thresholds should be conservative in CI because local host
load varies.

## CI vs local opt-in

CI should run:

1. Frame and protocol unit tests.
2. Chunker and reassembly unit tests.
3. Spool unit tests using temporary directories.
4. Single-daemon loopback PUT and GET smoke tests.
5. ContextStorm `tiny` smoke workload.
6. Existing Phase 1 Python, Rust, and parity tests.

Local opt-in runs may include:

1. `small`, `medium`, and `large` workloads.
2. Longer duration soak tests.
3. Multipath synthetic profiles.
4. Root-required network fault profiles.
5. Host-specific throughput exploration.

Local opt-in benchmarks should print enough environment context to explain
results without making those numbers a correctness contract.

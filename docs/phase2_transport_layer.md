# Phase 2 Transport Layer

Last verified: 2026-05-29

## Objective

Phase 2 builds a synthetic KV transport layer for BIFROST.

The goal is to prove that a valid Phase 1 KV object can be chunked, transferred
over local TCP, reassembled, revalidated, committed into a minimal spool, and
measured under synthetic workloads.

Phase 2 is not an inference integration milestone. It does not extract real KV
state from a model, inject KV state into a model, or optimize production cache
placement. It establishes the local transport and storage correctness path that
later phases can use.

## Single-path first, multipath second

The first transport path is a single TCP connection between a local client and a
local daemon.

Single-path support must prove:

1. Protocol version negotiation.
2. PUT transfer from descriptor and payload files into a remote spool.
3. Chunking and reassembly independent of TCP read boundaries.
4. Full Phase 1 validation before commit.
5. GET transfer for committed objects only.
6. Deterministic misses for absent or invalid objects.
7. Metrics for success and failure cases.

Multipath work starts only after the single-path path is correct. Multipath in
Phase 2 means synthetic transfer over multiple local TCP connections with
deterministic reassembly. It does not include QUIC, RDMA, compression, parity
chunks, adaptive path selection, or production congestion control.

## Core Rust components

Phase 2 should be implemented primarily in Rust because the receiving daemon
must use the Phase 1 Rust validator before accepting objects.

Expected components:

1. `transport` frame encoder and decoder for `bifrost.transport.v1alpha1`.
2. `chunker` for deterministic payload slicing and chunk metadata.
3. `reassembly` state for tracking received chunks and detecting completion.
4. `spool` for staging, validation, atomic commit, and committed reads.
5. `daemon` TCP server for local PUT, GET, HAS, ping, and metrics.
6. `client` CLI for local PUT, GET, HAS, and benchmark commands.
7. `metrics` counters and histograms exposed through logs or a local text
   snapshot format.
8. `contextstorm` benchmark harness for synthetic object transfers.

Names may follow existing repository conventions, but the ownership boundaries
should remain clear: protocol code does not validate KV object meaning, spool
code does not redefine object identity, and benchmark code does not bypass the
acceptance path.

## Dependency note

The Phase 2 daemon uses Tokio for local TCP listener concurrency and async
client I/O. This keeps one connection from blocking other PUT transfers while
preserving a single-process, local-only implementation for Phase 2 tests.

## How Phase 2 builds on Phase 1

Phase 1 defines the immutable object contract:

1. Descriptor schema.
2. Target compatibility profile.
3. Canonical descriptor bytes.
4. Payload hash.
5. Descriptor hash.
6. Object ID.
7. Stable validation reason codes.

Phase 2 transports and stores those objects. It does not change their identity.

Before an object is committed to the spool, the daemon must re-run Phase 1 Rust
validation against the received descriptor, reassembled payload, and configured
target profile. A successful transfer with failed validation is rejected. A
partial or uncertain transfer is a miss, not a servable object.

## Current hardening behavior

The v1alpha1 frame decoder rejects unsupported protocol versions, unknown frame
types, missing required fields, payload length mismatches, oversized headers,
and oversized payload declarations before allocating the payload buffer. Error
frames carry a structured `status` and non-empty `reason`.

Committed spool reads are conservative. `HAS`, `GET`, and direct committed reads
require both descriptor and payload files to exist and re-run Phase 1 object
validation before serving bytes. Corrupt or incomplete committed records are
reported as misses or validation rejections rather than cache hits.

The daemon handles each accepted TCP connection in a Tokio task. The local spool
serializes staging and commit mutations with an in-process lock, so concurrent
Phase 2 transfers cannot promote incomplete staging state. This remains a
single-process local transport, not a distributed object store.

## Out of scope

Phase 2 does not include:

1. LMCache integration.
2. vLLM integration.
3. Real model KV extraction or injection.
4. GPU inference.
5. Dashboards.
6. QUIC.
7. RDMA.
8. Compression.
9. Production authentication or authorization.
10. Full object-store eviction.
11. Pinning or cache policy.
12. Parity chunks or erasure coding.
13. Cloud object storage.
14. Cross-host deployment automation.

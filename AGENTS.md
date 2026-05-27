# BIFROST Agent Guidance

Last verified: 2026-05-27

## Current phase

BIFROST is in Phase 1.

The Phase 1 goal is a correctness-first KV object layer. Before BIFROST moves bytes over a network, stores objects remotely, integrates with inference engines, or serves dashboards, it must define, hash, validate, and reject KV-cache objects deterministically.

## Phase 1 scope

Agents may work on:

1. KV object schemas.
2. Canonical JSON and hashing.
3. Payload, descriptor, and object ID validation.
4. Known-good and known-bad fixtures.
5. A small CLI for validating and inspecting fixtures.
6. Python reference implementation.
7. Rust metadata mirror.
8. Cross-language parity tests between Python and Rust.

Agents must not implement Phase 2 or later features during Phase 1.

Do not add:

1. Networking.
2. Object storage services.
3. LMCache integrations.
4. vLLM integrations.
5. Dashboards.
6. Model inference.
7. Real KV extraction or injection.
8. Multipath transfer.

## Correctness rules

BIFROST must fail closed. If compatibility, integrity, schema meaning, or object identity is uncertain, reject the object.

Mutable storage state must never be included in immutable object identity. Fields such as local tier, pinned state, cache path, write state, last access time, expiry, eviction metadata, and cache location describe a local record, not the immutable KV object.

Prefer boring correctness over clever optimization. Deterministic behavior, stable tests, readable validation, and explicit error reasons matter more than speed in Phase 1.

## Dependencies

Do not add new production dependencies without a written justification in the relevant change. Prefer standard library functionality and existing project dependencies.

Test-only dependencies are acceptable when they materially improve coverage and are scoped to tests.

## Error codes

Keep validation error reason codes stable. Once fixtures, tests, or docs rely on a reason code, do not rename or delete it without a migration note and updated compatibility expectations.

Validators should return the most specific stable reason code available. Ambiguous objects should be rejected with a deterministic reason.

## Tests

Run Python and Rust tests after relevant changes.

Expected Phase 1 test coverage:

1. Python schema validation.
2. Python canonicalization and hashing.
3. Python fixture validation.
4. Rust metadata parsing and validation mirror.
5. Cross-language test vectors for canonical JSON, payload hash, descriptor hash, and object ID.
6. CLI behavior for accepted and rejected fixtures.

If a test cannot be run, state the reason in the final response.

## Implementation order

Use Python as the reference implementation first. Mirror the metadata structures, hashes, and validation behavior in Rust second.

Rust and Python must agree on canonical bytes, hashes, object IDs, and error reason codes for the same fixtures.

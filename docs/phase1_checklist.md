# Phase 1 Checklist

Last verified: 2026-05-27

## Schemas

- [x] Define supported `schema_version` values.
- [x] Define supported `object_type` values: `native_kv_page` and `opaque_engine_blob`.
- [x] Define immutable descriptor fields.
- [x] Define mutable local object record fields outside immutable identity.
- [x] Define required fields for `native_kv_page`.
- [x] Define required fields for `opaque_engine_blob`.
- [x] Define rejected extra-field behavior.
- [x] Define allowed hash string formats.
- [x] Define allowed compression values for Phase 1.
- [x] Define allowed payload encoding values for Phase 1.
- [x] Document schema evolution rules.

## Validation results and errors

- [x] Define Python stable reason code constants.
- [x] Define Python validation result type.
- [x] Validate validation result objects against JSON Schema.
- [x] Enforce docs, schema, and Python reason-code parity in tests.

## Python canonicalization and hashing

- [x] Implement canonical JSON serialization.
- [x] Reject duplicate JSON object keys in CLI JSON inputs.
- [x] Reject non-canonical or unsupported number forms.
- [x] Sort object keys lexicographically.
- [x] Preserve array order.
- [x] Omit descriptor hash and object ID from descriptor hash input.
- [x] Compute payload hash from exact payload bytes.
- [x] Compute descriptor hash from canonical immutable descriptor bytes.
- [x] Compute object ID from descriptor hash and payload hash.
- [x] Add deterministic test vectors for canonical bytes.
- [x] Add deterministic test vectors for all hash outputs.

## Python validator

- [x] Parse descriptor JSON in CLI entry points.
- [x] Validate schema version.
- [x] Validate object type.
- [x] Validate required fields.
- [x] Reject unknown fields.
- [x] Validate field types.
- [x] Validate payload byte length.
- [x] Validate payload hash.
- [x] Validate descriptor hash.
- [x] Validate object ID.
- [x] Validate native model compatibility fields.
- [x] Validate native engine compatibility fields.
- [x] Validate native prefix fields.
- [x] Validate native tensor fields.
- [x] Validate opaque engine key fields.
- [x] Validate opaque engine and integration fields.
- [x] Return stable reason codes.
- [x] Document and test deterministic error ordering for multi-error objects.
- [x] Add known-good fixture tests.
- [x] Add known-bad fixture tests for every reason code where practical.
- [x] Add fail-closed edge-case tests for malformed hash prefixes and negative numeric fields.

## Fixture generator

- [x] Generate deterministic native accepted fixture.
- [x] Generate deterministic native accepted fixture for layer 3, block 7.
- [x] Generate deterministic opaque accepted fixture.
- [x] Generate object identity test vectors.
- [x] Generate payload hash mismatch fixture.
- [x] Generate descriptor hash mismatch fixture.
- [x] Generate object ID mismatch fixture.
- [x] Generate byte length mismatch fixture.
- [x] Generate compatibility mismatch fixtures.
- [x] Generate malformed schema fixtures.
- [x] Store expected reason code with each rejected fixture.
- [x] Make identity vector generation reproducible in CI.

## CLI

- [x] Add command to validate one descriptor and payload pair.
- [ ] Add command to validate a fixture directory. Phase 1 validates fixture file triples; directory loading is currently used by fixture corruption.
- [x] Print `ACCEPTED` for accepted objects.
- [x] Print stable reason codes for rejected objects.
- [x] Return non-zero exit status for rejected or malformed objects.
- [x] Add machine-readable output mode.
- [x] Add CLI tests for accepted fixtures.
- [x] Add CLI tests for rejected fixtures.

## Rust metadata mirror

- [x] Define Rust metadata structs matching the schema.
- [x] Define Rust reason code enum or constants matching Python.
- [x] Parse descriptor JSON.
- [x] Reject unsupported schema versions.
- [x] Reject unsupported object types.
- [x] Validate required fields and types.
- [x] Validate native metadata fields.
- [x] Validate opaque metadata fields.
- [x] Recompute descriptor hash and object ID.
- [x] Keep mutable local record fields outside object identity types.
- [x] Add Rust unit tests for accepted metadata.
- [x] Add Rust unit tests for rejected metadata.

## Cross-language test vectors

- [x] Share canonical JSON fixtures between Python and Rust.
- [x] Share payload bytes between Python and Rust.
- [x] Assert identical payload hash values.
- [x] Assert identical descriptor hash values.
- [x] Assert identical object ID values.
- [x] Assert identical accepted or rejected reason codes.
- [x] Include native KV page vectors.
- [x] Include nonzero native KV page vectors.
- [x] Include opaque engine blob vectors.
- [x] Include recursive metadata key-order invariant tests.
- [x] Include mutable local record examples proving mutable storage fields are outside descriptor identity.

## CI

- [x] Run Python unit tests.
- [x] Run Python CLI tests.
- [x] Run Rust unit tests.
- [x] Run cross-language parity tests.
- [x] Run identity vector generation check.
- [x] Fail CI if generated identity vectors differ from committed fixtures.
- [x] Fail CI if reason code lists diverge between docs, Python, and Rust.

## Phase 1 done criteria

- [x] Documentation defines scope, identity, validation, and error codes.
- [x] Python reference implementation is complete.
- [x] Rust metadata mirror is complete.
- [x] Fixtures cover accepted and rejected paths.
- [x] CLI validates fixtures deterministically.
- [x] Python and Rust agree on all committed test vectors.
- [x] CI enforces Python tests, Rust tests, identity vector determinism, and parity.
- [x] No networking, object storage, LMCache, vLLM, dashboard, inference, or real KV extraction work is required.
- [x] BIFROST rejects uncertain compatibility or integrity every time.

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
- [ ] Document schema evolution rules.

## Python canonicalization and hashing

- [ ] Implement canonical JSON serialization.
- [ ] Reject duplicate JSON object keys.
- [ ] Reject non-canonical or unsupported number forms.
- [ ] Sort object keys lexicographically.
- [ ] Preserve array order.
- [ ] Omit descriptor hash and object ID from descriptor hash input.
- [ ] Compute payload hash from exact payload bytes.
- [ ] Compute descriptor hash from canonical immutable descriptor bytes.
- [ ] Compute object ID from descriptor hash and payload hash.
- [ ] Add deterministic test vectors for canonical bytes.
- [ ] Add deterministic test vectors for all hash outputs.

## Python validator

- [ ] Parse descriptor JSON.
- [ ] Validate schema version.
- [ ] Validate object type.
- [ ] Validate required fields.
- [ ] Reject unknown fields.
- [ ] Validate field types.
- [ ] Validate payload byte length.
- [ ] Validate payload hash.
- [ ] Validate descriptor hash.
- [ ] Validate object ID.
- [ ] Validate native model compatibility fields.
- [ ] Validate native engine compatibility fields.
- [ ] Validate native prefix fields.
- [ ] Validate native tensor fields.
- [ ] Validate opaque engine key fields.
- [ ] Validate opaque engine and integration fields.
- [ ] Return stable reason codes.
- [ ] Add known-good fixture tests.
- [ ] Add known-bad fixture tests for every reason code where practical.

## Fixture generator

- [ ] Generate deterministic native accepted fixture.
- [ ] Generate deterministic opaque accepted fixture.
- [ ] Generate descriptor-only test vectors.
- [ ] Generate payload hash mismatch fixture.
- [ ] Generate descriptor hash mismatch fixture.
- [ ] Generate object ID mismatch fixture.
- [ ] Generate byte length mismatch fixture.
- [ ] Generate compatibility mismatch fixtures.
- [ ] Generate malformed schema fixtures.
- [ ] Store expected reason code with each rejected fixture.
- [ ] Make fixture generation reproducible in CI.

## CLI

- [ ] Add command to validate one descriptor and payload pair.
- [ ] Add command to validate a fixture directory.
- [ ] Print `accepted` for accepted objects.
- [ ] Print stable reason codes for rejected objects.
- [ ] Return non-zero exit status for rejected or malformed objects.
- [ ] Add machine-readable output mode.
- [ ] Add CLI tests for accepted fixtures.
- [ ] Add CLI tests for rejected fixtures.

## Rust metadata mirror

- [ ] Define Rust metadata structs matching the schema.
- [ ] Define Rust reason code enum or constants matching Python.
- [ ] Parse descriptor JSON.
- [ ] Reject unsupported schema versions.
- [ ] Reject unsupported object types.
- [ ] Validate required fields and types.
- [ ] Validate native metadata fields.
- [ ] Validate opaque metadata fields.
- [ ] Recompute descriptor hash and object ID.
- [ ] Keep mutable local record fields outside object identity types.
- [ ] Add Rust unit tests for accepted metadata.
- [ ] Add Rust unit tests for rejected metadata.

## Cross-language test vectors

- [ ] Share canonical JSON fixtures between Python and Rust.
- [ ] Share payload bytes between Python and Rust.
- [ ] Assert identical payload hash values.
- [ ] Assert identical descriptor hash values.
- [ ] Assert identical object ID values.
- [ ] Assert identical accepted or rejected reason codes.
- [ ] Include native KV page vectors.
- [ ] Include opaque engine blob vectors.
- [ ] Include mutable local record examples proving identity does not change.

## CI

- [ ] Run Python unit tests.
- [ ] Run Python CLI tests.
- [ ] Run Rust unit tests.
- [ ] Run cross-language parity tests.
- [ ] Run fixture generation check.
- [ ] Fail CI if generated fixtures differ from committed fixtures.
- [ ] Fail CI if reason code lists diverge between docs, Python, and Rust.

## Phase 1 done criteria

- [ ] Documentation defines scope, identity, validation, and error codes.
- [ ] Python reference implementation is complete.
- [ ] Rust metadata mirror is complete.
- [ ] Fixtures cover accepted and rejected paths.
- [ ] CLI validates fixtures deterministically.
- [ ] Python and Rust agree on all committed test vectors.
- [ ] CI enforces Python tests, Rust tests, fixture determinism, and parity.
- [ ] No networking, object storage, LMCache, vLLM, dashboard, inference, or real KV extraction work is required.
- [ ] BIFROST rejects uncertain compatibility or integrity every time.

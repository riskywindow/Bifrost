# BIFROST Phase 1

BIFROST is currently in Phase 1: a correctness-first KV object layer for
deterministic schema validation, canonical JSON hashing, fixture validation, and
Python/Rust parity tests.

BIFROST may miss a cache hit, but it must never serve wrong KV state. Phase 1
therefore rejects uncertain compatibility or integrity instead of guessing.
Wrong model, tokenizer, RoPE config, dtype, KV layout, prefix identity, token
range, payload bytes, descriptor hash, or object ID must fail closed.

Phase 1 does not implement networking, object storage, LMCache or vLLM
integration, dashboards, model inference, real KV extraction, or real KV
injection.

## Install Python Package

```sh
python -m pip install -e "bifrost_py[dev]"
```

This installs the `bifrost-kv` console script and the Python test dependencies.

## Run Tests

```sh
pytest bifrost_py/tests tests
cargo test --manifest-path bifrostd/Cargo.toml
```

The test suite is local-only. It does not require a GPU, internet access, vLLM,
LMCache, cloud credentials, or object storage services.

## Generate Fixtures

Regenerate the committed Phase 1 fixtures:

```sh
python tools/generate_phase1_fixtures.py
python tools/generate_identity_vectors.py
```

CI checks that generated identity vectors do not drift from the committed
fixtures.

You can also create one deterministic native fixture with the CLI:

```sh
bifrost-kv make-native-fixture --out /tmp/bifrost-native-fixture
```

## Validate A Fixture

Validate the committed native fixture:

```sh
bifrost-kv validate \
  --meta fixtures/native_valid/tiny_gpt_layer0_block0.meta.json \
  --payload fixtures/native_valid/tiny_gpt_layer0_block0.payload.bin \
  --target fixtures/native_valid/target_profile.json
```

Accepted objects print `ACCEPTED` and include the object ID, payload hash, and
descriptor hash.

Validate an invalid fixture with machine-readable output:

```sh
bifrost-kv validate \
  --meta fixtures/invalid/wrong_tokenizer_hash/meta.json \
  --payload fixtures/invalid/wrong_tokenizer_hash/payload.bin \
  --target fixtures/invalid/wrong_tokenizer_hash/target_profile.json \
  --json
```

Rejected objects return exit code `1` and include a stable `reason_code`, such
as `wrong_tokenizer_hash` or `payload_hash_mismatch`. CLI usage errors,
unreadable files, malformed JSON, and duplicate JSON object keys return exit
code `2`.

See `docs/validation_errors.md` for the full reason-code contract.

## Rust Validation Binary

The Rust mirror includes a validation binary:

```sh
cargo run --manifest-path bifrostd/Cargo.toml --bin bifrost-kv-validate -- \
  --meta fixtures/native_valid/tiny_gpt_layer0_block0.meta.json \
  --payload fixtures/native_valid/tiny_gpt_layer0_block0.payload.bin \
  --target fixtures/native_valid/target_profile.json
```

Python is the Phase 1 reference implementation; Rust mirrors metadata parsing,
validation reason codes, hashes, object IDs, and fixture behavior.

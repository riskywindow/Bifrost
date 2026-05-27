# Phase 1 CLI

Last verified: 2026-05-27

## Purpose

`bifrost-kv` is the Phase 1 command-line tool for validating and inspecting local KV object fixtures. It only reads and writes local descriptor, payload, target profile, and expected-result files.

The CLI does not implement networking, object storage, LMCache integration, vLLM integration, inference, dashboards, or real KV extraction.

## Validate

```sh
bifrost-kv validate --meta fixtures/native_valid/tiny_gpt_layer0_block0.meta.json \
  --payload fixtures/native_valid/tiny_gpt_layer0_block0.payload.bin \
  --target fixtures/native_valid/target_profile.json
```

Accepted human-readable output:

```text
ACCEPTED
Object ID: bifrost://object/blake3/...
Payload hash: blake3:...
Descriptor hash: blake3:...
```

Rejected human-readable output:

```text
REJECTED: wrong_tokenizer_hash
Object ID: bifrost://object/blake3/...
Payload hash: blake3:...
Descriptor hash: blake3:...
```

Machine-readable output uses the `ValidationResult` JSON shape:

```sh
bifrost-kv validate --meta fixtures/invalid/wrong_tokenizer_hash/meta.json \
  --payload fixtures/invalid/wrong_tokenizer_hash/payload.bin \
  --target fixtures/invalid/wrong_tokenizer_hash/target_profile.json \
  --json
```

Exit codes:

- `0`: object accepted.
- `1`: object rejected.
- `2`: CLI usage error, unreadable file, or unparseable JSON input.

## ID

```sh
bifrost-kv id --meta fixtures/native_valid/tiny_gpt_layer0_block0.meta.json \
  --payload fixtures/native_valid/tiny_gpt_layer0_block0.payload.bin
```

This computes and prints:

```text
Object ID: bifrost://object/blake3/...
Payload hash: blake3:...
Descriptor hash: blake3:...
```

`id` computes identity only. It does not validate target compatibility. It still fails if the metadata is too malformed to compute descriptor identity.

JSON output is also available:

```sh
bifrost-kv id --meta fixtures/native_valid/tiny_gpt_layer0_block0.meta.json \
  --payload fixtures/native_valid/tiny_gpt_layer0_block0.payload.bin \
  --json
```

## Make Native Fixture

```sh
bifrost-kv make-native-fixture --out /tmp/bifrost-native-fixture
```

This writes:

- `meta.json`
- `payload.bin`
- `target_profile.json`

The generated fixture is deterministic and should validate with exit code `0`.

## Corrupt Fixture

```sh
bifrost-kv corrupt-fixture --fixture /tmp/bifrost-native-fixture \
  --corruption wrong_tokenizer_hash \
  --out /tmp/bifrost-wrong-tokenizer
```

This writes:

- `meta.json`
- `payload.bin`
- `target_profile.json`
- `expected_result.json`

Supported corruption names:

- `payload_byte_flip`
- `wrong_tokenizer_hash`
- `wrong_rope_hash`
- `object_id_mismatch`

The generated corrupted fixture is deterministic and should validate with exit code `1`. The stable reason code is recorded in `expected_result.json`.

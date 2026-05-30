# BIFROST Phase 2

BIFROST is currently in Phase 2: a correctness-first synthetic KV transport
layer and local benchmark harness. Phase 1 object validation, canonical JSON
hashing, fixture validation, and Python/Rust parity remain the acceptance gate
for every transferred object.

BIFROST may miss a cache hit, but it must never serve wrong or partial KV state.
Phase 2 transfers a valid Phase 1 object over local TCP, chunks and reassembles
the payload, revalidates descriptor hash, payload hash, object ID, and target
compatibility with the Rust validator, then commits only validated objects into
a minimal local spool.

Phase 2 does not implement LMCache integration, vLLM integration, dashboards,
GPU inference, real KV extraction, real KV injection, QUIC, RDMA, compression,
production authentication, or cache eviction policy.

## Install Python Package

```sh
python -m pip install -e "bifrost_py[dev]"
```

This installs the `bifrost-kv` console script and the Python test dependencies.

## Run Tests

```sh
pytest bifrost_py/tests tests
cargo test --manifest-path bifrostd/Cargo.toml
cd contextstorm
PYTHONPATH=. pytest
```

The test suite is local-only. It does not require a GPU, internet access, vLLM,
LMCache, cloud credentials, or object storage services.

## Phase 2 Local Transport

Build the daemon and transfer client:

```sh
cargo build --manifest-path bifrostd/Cargo.toml --bins
```

Start a local daemon with an empty spool:

```sh
bifrostd/target/debug/bifrost-daemon \
  --listen 127.0.0.1:7420 \
  --spool /tmp/bifrost-spool \
  --trace-jsonl /tmp/bifrost-daemon.jsonl
```

PUT a committed fixture:

```sh
bifrostd/target/debug/bifrost-xfer put \
  --endpoint 127.0.0.1:7420 \
  --meta fixtures/native_valid/tiny_gpt_layer0_block0.meta.json \
  --payload fixtures/native_valid/tiny_gpt_layer0_block0.payload.bin \
  --target fixtures/native_valid/target_profile.json \
  --trace-jsonl /tmp/bifrost-put.jsonl
```

Check whether the object is committed and servable:

```sh
bifrostd/target/debug/bifrost-xfer has \
  --endpoint 127.0.0.1:7420 \
  --object-id bifrost://object/blake3/<object-id-hex>
```

Fetch exact committed bytes:

```sh
bifrostd/target/debug/bifrost-xfer get \
  --endpoint 127.0.0.1:7420 \
  --object-id bifrost://object/blake3/<object-id-hex> \
  --out /tmp/bifrost-get
```

`HAS` and `GET` only serve committed objects. Staged, partial, corrupt, or
invalid records are reported as misses or rejections.

Trace JSONL files include transfer IDs, object IDs, chunk indexes, byte counts,
reason codes, and multipath path names where applicable. `bifrost-xfer --json`
also includes a local metrics snapshot with bytes, chunks, retries, timeouts,
path failures, and transfer success or failure counts.

## ContextStorm

Run the small CPU-only local benchmark:

```sh
cargo build --manifest-path bifrostd/Cargo.toml --bins
cd contextstorm
PYTHONPATH=. python -m contextstorm.cli run scenarios/small_ci.yaml \
  --runs-root /tmp/contextstorm-runs \
  --run-id phase2-local-small
PYTHONPATH=. python -m contextstorm.cli report /tmp/contextstorm-runs/phase2-local-small
```

The run directory contains `run.json`, command records, trace JSONL files,
`summary.json`, and `summary.md`. Reports summarize local synthetic transfer
latency, throughput, bytes, chunks, retries, misses, and payload match checks.

Optional local fault scenarios are explicit. Process-kill scenarios do not
require root; `tc_netem` profiles require both root and `--allow-root-faults`:

```sh
PYTHONPATH=. python -m contextstorm.cli run scenarios/dead_path.yaml \
  --runs-root /tmp/contextstorm-runs \
  --run-id phase2-local-dead-path

sudo PYTHONPATH=. python -m contextstorm.cli run scenarios/lossy_two_path.yaml \
  --allow-root-faults \
  --runs-root /tmp/contextstorm-runs \
  --run-id phase2-local-lossy-two-path
```

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

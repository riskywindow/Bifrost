# Phase 4 Demo

Last verified: 2026-06-02

## Purpose

The Phase 4 demo should show KV teleportation for the tiny transformer:
deterministic prefix KV state is extracted in one process, stored through
BIFROST as native KV pages, retrieved in another process, rehydrated, and used
to continue decoding with logits matching the uninterrupted baseline.

The demo is local, CPU-only, and deterministic by default.

## Demo script

The expected script shape is:

```text
python -m bifrost_tiny.demo teleport \
  --endpoint 127.0.0.1:9000 \
  --store-root /tmp/bifrost-phase4-store \
  --tokens 1,5,9,2,7,3,4,8,6 \
  --prefix-len 6 \
  --continuation-len 3 \
  --block-size-tokens 4 \
  --dtype float32 \
  --json
```

The module name may change during implementation if the repository's Python
package layout requires it, but the demo contract should stay stable:

1. Local deterministic tiny model.
2. Integer token IDs supplied on the command line or by a fixture name.
3. CPU by default.
4. No external model or tokenizer downloads.
5. Native KV page generation.
6. Store roundtrip through the daemon or local store API.
7. Manifest completeness check.
8. Rehydrated continuation comparison.

## Expected output

Human-readable output should include:

```text
model: bifrost_tiny_transformer phase4.v1
device: cpu
dtype: float32
prefix_tokens: 6
continuation_tokens: 3
block_size_tokens: 4
pages_written: 4
pages_read: 4
manifest_complete: true
max_logit_abs_diff: 0.000000
max_logit_rel_diff: 0.000000
greedy_tokens_match: true
result: pass
```

JSON output should be stable for tests:

```json
{
  "result": "pass",
  "model_id": "bifrost_tiny_transformer",
  "model_revision": "phase4.v1",
  "device": "cpu",
  "dtype": "float32",
  "prefix_token_count": 6,
  "continuation_token_count": 3,
  "block_size_tokens": 4,
  "pages_written": 4,
  "pages_read": 4,
  "manifest_id": "bifrost://manifest/blake3/...",
  "manifest_complete": true,
  "max_logit_abs_diff": 0.0,
  "max_logit_rel_diff": 0.0,
  "greedy_tokens_baseline": [3, 11, 4],
  "greedy_tokens_roundtrip": [3, 11, 4],
  "greedy_tokens_match": true
}
```

If any page is missing, corrupt, incompatible, or not manifest-complete, the
demo should report `result: miss` or `result: reject` with a deterministic
reason rather than attempting partial rehydration.

## Running locally with a daemon

Build Rust binaries:

```text
cd bifrostd
cargo build --bins
```

Start a local daemon with a fresh store root:

```text
./target/debug/bifrost-daemon \
  --listen 127.0.0.1:9000 \
  --store-root /tmp/bifrost-phase4-store
```

Run the demo from the repository root in another terminal:

```text
PYTHONPATH=. python -m bifrost_tiny.demo teleport \
  --endpoint 127.0.0.1:9000 \
  --store-root /tmp/bifrost-phase4-store \
  --tokens 1,5,9,2,7,3,4,8,6 \
  --prefix-len 6 \
  --continuation-len 3 \
  --block-size-tokens 4 \
  --dtype float32
```

If the final implementation supports direct local-store mode for tests, it may
also provide:

```text
PYTHONPATH=. python -m bifrost_tiny.demo teleport \
  --store-root /tmp/bifrost-phase4-store \
  --no-daemon \
  --fixture small_multiblock \
  --json
```

Direct mode is a test convenience. The cross-process demo should still exercise
the daemon path before Phase 4 is considered complete.

## Optional GPU demo

An optional GPU demo may exist only behind an explicit flag such as:

```text
--device cuda --allow-gpu
```

The default must remain CPU. CI, docs smoke commands, and required correctness
tests must not require GPU hardware or CUDA.

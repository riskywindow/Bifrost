# Phase 4 Demo

Last verified: 2026-06-13

## Purpose

The Phase 4 demo should show KV teleportation for the tiny transformer:
deterministic prefix KV state is extracted in one process, stored through
BIFROST as native KV pages, retrieved in another process, rehydrated, and used
to continue decoding with logits matching the uninterrupted baseline.

The demo is local, CPU-only, and deterministic by default.

## Demo script

The current local correctness smoke is a no-daemon, one-process script:

```text
PYTHONPATH=bifrost_py python examples/tiny_transformer/local_kv_roundtrip.py \
  --prompt "1 2 3 4 5" \
  --decode-tokens 4 \
  --block-size 2 \
  --seed 1234
```

It parses explicit integer token IDs, initializes the deterministic CPU tiny
transformer, runs an uninterrupted greedy baseline, serializes the prefill
`past_key_values` into validated Phase 1 `native_kv_page` objects, rehydrates
those pages in memory, resumes greedy generation, and compares logits and
continuation tokens.

For stable test output:

```text
PYTHONPATH=bifrost_py python examples/tiny_transformer/local_kv_roundtrip.py \
  --prompt "1 2 3 4 5" \
  --decode-tokens 4 \
  --block-size 2 \
  --seed 1234 \
  --json
```

The JSON summary includes:

```json
{
  "status": "pass",
  "prompt_tokens": [1, 2, 3, 4, 5],
  "baseline_continuation": [7, 7, 7, 127],
  "rehydrated_continuation": [7, 7, 7, 127],
  "continuation_match": true,
  "logit_max_abs_error": 0.0,
  "page_count": 6,
  "layer_count": 2,
  "block_size_tokens": 2,
  "object_ids": ["bifrost://object/blake3/..."]
}
```

This script intentionally does not use a daemon, local store, manifests,
cross-process transfer, ContextStorm, LMCache, vLLM, external model downloads,
GPU execution, dashboards, compression, QUIC, or RDMA.

The daemon-backed store roundtrip smoke exercises the Phase 3 committed store
path without manifests:

```text
PYTHONPATH=bifrost_py python examples/tiny_transformer/store_kv_roundtrip.py \
  --endpoint 127.0.0.1:9000 \
  --prompt "1 2 3 4 5" \
  --decode-tokens 4 \
  --block-size 2 \
  --seed 1234 \
  --json
```

It initializes the deterministic tiny transformer, parses explicit integer
token IDs, runs the uninterrupted greedy baseline, serializes the prefill
`past_key_values` into validated Phase 1 `native_kv_page` objects, writes
temporary metadata, payload, and target-profile files, PUTs every page through
`bifrost-xfer`, confirms every page is servable with `bifrost-store inspect`,
GETs every page back through `bifrost-xfer`, validates the fetched bytes with
the Phase 1 Python validator, rehydrates the fetched pages, and compares logits
plus greedy continuation tokens.

The JSON summary includes:

```json
{
  "status": "pass",
  "prompt_tokens": [1, 2, 3, 4, 5],
  "page_count": 6,
  "put_success_count": 6,
  "get_success_count": 6,
  "object_ids": ["bifrost://object/blake3/..."],
  "baseline_continuation": [7, 7, 7, 127],
  "rehydrated_continuation": [7, 7, 7, 127],
  "continuation_match": true,
  "logit_max_abs_error": 0.0,
  "total_put_ms": 12.0,
  "total_get_ms": 10.0,
  "rehydrate_ms": 1.0
}
```

The daemon-backed manifest roundtrip adds the Phase 3 prefix manifest gate:

```text
PYTHONPATH=bifrost_py python examples/tiny_transformer/manifest_kv_roundtrip.py \
  --endpoint 127.0.0.1:9000 \
  --prompt "1 2 3 4 5" \
  --decode-tokens 4 \
  --block-size 2 \
  --seed 1234 \
  --json
```

After all pages are PUT and inspected, the script creates a prefix manifest
using the full prompt prefix identity: `model_hash`, `tokenizer_hash`,
`rope_config_hash`, `prefix_hash`, and token range `[0, prompt_token_count)`.
It adds every generated page object as a required member. The store derives the
member `layer_id`, `kv_block_id`, and page token range from each committed
object descriptor. Before rehydration, the script checks manifest completeness
and verifies the inspected member list covers every expected tiny-model
`(layer_id, kv_block_id)` page.

The JSON summary includes:

```json
{
  "status": "pass",
  "manifest_id": "bifrost://manifest/blake3/...",
  "manifest_completeness": "complete",
  "page_count": 6,
  "required_member_count": 6,
  "missing_member_count": 0,
  "continuation_match": true,
  "logit_max_abs_error": 0.0
}
```

If the store reports an incomplete, corrupt, or unknown manifest, or if the
manifest omits an expected layer/block page, the harness reports failure rather
than attempting partial rehydration.

These store roundtrips do not implement the cross-process worker A/B demo,
ContextStorm model scenarios, LMCache, vLLM, external model downloads, GPU
execution, dashboards, compression, QUIC, or RDMA.

The later cross-process demo contract should stay stable:

1. Local deterministic tiny model.
2. Integer token IDs supplied on the command line or by a fixture name.
3. CPU by default.
4. No external model or tokenizer downloads.
5. Native KV page generation.
6. Store roundtrip through the daemon or local store API.
7. Manifest completeness check.
8. Rehydrated continuation comparison.

## Expected output

Human-readable output from the manifest-gated demo includes:

```text
model: bifrost_tiny_transformer phase4.v1
device: cpu
dtype: float32
prompt_tokens: [1, 2, 3, 4, 5]
manifest_id: bifrost://manifest/blake3/...
manifest_completeness: complete
page_count: 6
required_member_count: 6
missing_member_count: 0
put_success_count: 6
get_success_count: 6
baseline_continuation: [7, 7, 7, 127]
rehydrated_continuation: [7, 7, 7, 127]
continuation_match: true
logit_max_abs_error: 0.000000000
total_put_ms: 12.000
total_get_ms: 10.000
rehydrate_ms: 1.000
result: pass
```

JSON output should be stable for tests. The manifest-gated demo includes:

```json
{
  "status": "pass",
  "manifest_id": "bifrost://manifest/blake3/...",
  "manifest_completeness": "complete",
  "page_count": 6,
  "required_member_count": 6,
  "missing_member_count": 0,
  "continuation_match": true,
  "logit_max_abs_error": 0.0,
  "prompt_tokens": [1, 2, 3, 4, 5],
  "put_success_count": 6,
  "get_success_count": 6,
  "baseline_continuation": [7, 7, 7, 127],
  "rehydrated_continuation": [7, 7, 7, 127]
}
```

The later cross-process demo should preserve the same correctness signal:

```text
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
cargo build --manifest-path bifrostd/Cargo.toml --bins
```

Start a local daemon with a fresh store root:

```text
bifrostd/target/debug/bifrost-daemon \
  --listen 127.0.0.1:9000 \
  --spool /tmp/bifrost-phase4-store
```

Run the demo from the repository root in another terminal:

```text
PYTHONPATH=bifrost_py python examples/tiny_transformer/store_kv_roundtrip.py \
  --endpoint 127.0.0.1:9000 \
  --prompt "1 2 3 4 5" \
  --decode-tokens 4 \
  --block-size 2 \
  --seed 1234 \
  --json
```

The harness accepts `--work-dir PATH` to retain the generated metadata,
payload, target-profile, and fetched GET files for inspection.

Run the manifest-gated variant:

```text
PYTHONPATH=bifrost_py python examples/tiny_transformer/manifest_kv_roundtrip.py \
  --endpoint 127.0.0.1:9000 \
  --prompt "1 2 3 4 5" \
  --decode-tokens 4 \
  --block-size 2 \
  --seed 1234 \
  --json
```

The manifest-gated variant accepts the same `--work-dir PATH` option.

## Optional GPU demo

An optional GPU demo may exist only behind an explicit flag such as:

```text
--device cuda --allow-gpu
```

The default must remain CPU. CI, docs smoke commands, and required correctness
tests must not require GPU hardware or CUDA.

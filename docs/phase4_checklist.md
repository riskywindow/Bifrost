# Phase 4 Checklist

Last verified: 2026-06-13

## Tiny transformer

- [x] Define a local decoder-only transformer with explicit
  `past_key_values`.
- [x] Keep default shape small enough for CPU CI.
- [x] Use deterministic weight initialization.
- [x] Disable dropout and nondeterministic sampling.
- [x] Provide `forward(input_ids, past_key_values=None, use_cache=True)`.
- [x] Test deterministic logits across repeated CPU runs.
- [x] Document model config hash inputs.
- [x] Keep production model support out of scope.

## Tokenizer and hashing

- [x] Use integer token IDs, not external tokenizers.
- [x] Define canonical token ID byte encoding.
- [x] Define deterministic tokenizer config and tokenizer hash.
- [x] Define positional or RoPE config and hash.
- [x] Build token hash from exact token sequence.
- [x] Build prefix hash from tokenizer hash, positional config hash, token hash,
  and absolute position range.
- [ ] Test token mismatch rejection.
- [x] Test prefix mismatch rejection.

## KV extraction

- [x] Extract key/value tensors from every decoder layer.
- [x] Require known internal layout:
  `[seq_len, num_kv_heads, head_dim]`.
- [x] Keep the tiny-harness KV cache batch-free; model inputs remain 1-D or
  batch size 1.
- [x] Split extracted tensors into deterministic token blocks.
- [x] Map `layer_id`, `kv_block_id`, and `token_range` exactly.
- [x] Include a multi-block prefix fixture.
- [x] Include a final partial block fixture if supported.
- [ ] Test layer-order and block-order mismatch rejection.

## KV serialization

- [x] Serialize every generated page as Phase 1 `native_kv_page`.
- [x] Use canonical payload layout `[2, block_tokens, num_kv_heads, head_dim]`.
- [x] Store key as payload index 0 and value as payload index 1.
- [x] Use deterministic contiguous little-endian tensor bytes.
- [x] Record exact tensor shape, dtype, byte length, and layout.
- [x] Compute payload hash, descriptor hash, and object ID with Phase 1 rules.
- [x] Ensure mutable store and demo state is excluded from object identity.
- [ ] Test dtype, shape, layout, byte-length, and hash mismatch rejection.

## Validation

- [x] Validate generated pages with Python reference validation.
- [ ] Validate generated pages with Rust mirror validation.
- [x] Generate target profiles deterministically from tiny-model config.
- [ ] Reject unknown schema versions.
- [x] Reject incompatible target profiles.
- [ ] Keep Phase 1 validation reason codes stable.
- [ ] Add Phase 4-specific errors for extraction, serialization, rehydration,
  logit mismatch, and continuation mismatch.

## Store roundtrip

- [x] Commit only verified native KV pages to the Phase 3 store.
- [ ] Prove staging pages are never returned as hits.
- [ ] Query pages by model hash, prefix hash, layer ID, and block ID.
- [x] Retrieve payloads through GET before rehydration.
- [x] Recheck file-level integrity before use.
- [x] Test roundtrip through daemon-backed store path.
- [ ] Test local-store convenience path if implemented.
- [ ] Test missing, evicted, quarantined, corrupt, and catalog-inconsistent
  objects fail closed.

## Manifest integration

- [x] Create prefix manifests for tiny-model page sets.
- [ ] Create session manifests if continuation state needs session grouping.
- [x] Add every required layer/block page as a required member.
- [x] Check manifest completeness before rehydration.
- [x] Report deterministic missing-block reasons.
- [x] Pin manifests without changing object identity.
- [x] Test incomplete manifest rejection.
- [x] Test corrupt or unavailable member makes manifest incomplete or
  unavailable.

## Cross-process demo

- [x] Add a no-daemon one-process script that serializes validated native KV
  pages, rehydrates them, and compares logits plus greedy continuation.
- [x] Process A runs prefix and extracts native KV pages.
- [x] Process A stores pages through BIFROST.
- [x] Process A records or prints manifest ID and comparison metadata.
- [x] Process B retrieves pages by manifest-gated handoff members.
- [x] Process B rehydrates `past_key_values`.
- [x] Process B resumes greedy decoding.
- [x] Demo compares logits and greedy token IDs with baseline.
- [x] Local no-daemon demo emits stable human-readable and JSON output.
- [x] Store-backed daemon demo emits stable human-readable and JSON output.
- [x] Cross-process worker A/B demo emits stable human-readable and JSON output.
- [x] Demo remains CPU-only and deterministic by default.

## Corruption tests

- [x] Flip one payload byte and verify payload hash rejection.
- [ ] Change descriptor tensor shape and verify descriptor hash or validation
  rejection.
- [ ] Change object ID and verify identity rejection.
- [x] Change model hash and verify compatibility rejection.
- [x] Change tokenizer hash and verify compatibility rejection.
- [ ] Change positional config hash and verify compatibility rejection.
- [x] Change prefix hash and verify miss or rejection.
- [x] Remove one layer/block page and verify no full rehydration occurs.
- [x] Mark one page quarantined or evicted and verify manifest incompleteness.
- [ ] Force logit mismatch with wrong-but-well-shaped KV and verify test
  failure.

## ContextStorm model benchmark

- [ ] Add CPU-only model-correctness workload class.
- [ ] Generate deterministic tiny-model KV pages, not synthetic byte-only pages.
- [ ] Measure extract, validate, store, retrieve, rehydrate, and compare steps.
- [ ] Record page counts, bytes, manifest completeness, logit diffs, and greedy
  match status.
- [ ] Keep default scenario small enough for CI smoke tests.
- [ ] Avoid GPU, external services, downloads, root permissions, and network
  fault profiles by default.
- [ ] Preserve existing Phase 2 transport and Phase 3 store scenarios.

## CI

- [x] Run required tiny-transformer tests on CPU.
- [ ] Skip GPU demos by default.
- [ ] Skip optional `float16` tests when CPU support is unreliable.
- [ ] Require no internet access.
- [ ] Require no Hugging Face, LMCache, vLLM, Docker, Kubernetes, CUDA, or cloud
  credentials.
- [ ] Keep Phase 1 parity tests green.
- [ ] Keep Phase 2 transport tests green.
- [ ] Keep Phase 3 store tests green.
- [x] Include at least one end-to-end extract-rehydrate-logit comparison.
- [x] Include at least one local no-daemon extract-rehydrate-greedy
  continuation comparison.
- [x] Include at least one end-to-end extract-store-rehydrate-logit comparison.

## Phase 4 done criteria

- [x] Tiny transformer produces deterministic CPU logits and
  `past_key_values`.
- [x] Every generated KV page is a validated Phase 1 `native_kv_page`.
- [x] Native pages roundtrip through the Phase 3 store.
- [x] Prefix or session manifest proves layer/block completeness.
- [x] Rehydrated logits match uninterrupted baseline within required
  `float32` tolerance.
- [x] Greedy continuation tokens match baseline in the no-daemon local
  roundtrip.
- [x] Corruption and mismatch cases fail closed.
- [x] Cross-process worker A/B KV teleportation demo passes locally.
- [ ] ContextStorm model benchmark smoke scenario passes locally and in CI.
- [ ] No forbidden Phase 4 scope was implemented.

# Phase 4 Roundtrip Correctness

Last verified: 2026-06-02

## Purpose

Phase 4 correctness is measured by comparing an uninterrupted tiny-transformer
decode against a path that extracts KV state, stores it as BIFROST native KV
pages, retrieves it, rehydrates it, and resumes decoding. The rehydrated path is
acceptable only when logits and greedy continuation match the baseline within
defined tolerances.

## Baseline generation path

The baseline path runs the tiny transformer without BIFROST intervention:

```text
1. Build deterministic model and integer token input.
2. Run the prefix tokens on CPU with use_cache=true.
3. Keep the returned past_key_values in memory.
4. Decode continuation tokens greedily from the in-memory cache.
5. Record logits at the comparison positions and the generated token IDs.
```

The baseline must use the same model instance, weights, dtype, and token IDs as
the roundtrip path. The model must be in eval mode and must not use dropout or
sampling.

## Extract-store-rehydrate path

The roundtrip path exercises BIFROST:

```text
1. Build deterministic model and integer token input.
2. Run the prefix tokens on CPU with use_cache=true.
3. Extract past_key_values by layer and token block.
4. Serialize each layer/block as a Phase 1 native_kv_page.
5. Validate descriptors and payloads with Phase 1 validation.
6. Commit verified pages to the Phase 3 local store.
7. Create or update a prefix/session manifest for the page set.
8. Retrieve the verified pages through store GET or query plus GET.
9. Rehydrate pages into the tiny model's past_key_values layout.
10. Decode the same continuation greedily from the rehydrated cache.
11. Compare logits and generated token IDs against the baseline.
```

Store queries and manifests are allowed to miss. They are not allowed to return
staged, corrupt, incompatible, missing, quarantined, evicted, or catalog-
inconsistent pages as usable cache hits.

## Logit comparison

Required `float32` tests should compare logits at:

1. The first token generated after rehydration.
2. Every greedy continuation step in the required smoke test.
3. At least one test with a prefix spanning multiple KV blocks.

Recommended required tolerance:

```text
float32:
  rtol = 1e-5
  atol = 1e-6
```

If the implementation uses only deterministic CPU operations and preserves
exact byte layout, many comparisons may be bit-identical. Tests should still use
tolerances so harmless CPU math differences do not hide the intended contract.

Optional `float16` tolerance:

```text
float16:
  rtol = 1e-2
  atol = 1e-3
```

`float16` tests must be opt-in or skipped when CPU support is not reliable.
They must never weaken the required `float32` correctness gate.

## Greedy continuation comparison

Greedy continuation compares token IDs, not text. Phase 4 uses integer tokens,
so no tokenizer decoding is required.

The comparison procedure is:

```text
for each continuation step:
  baseline_next = argmax(baseline_logits)
  roundtrip_next = argmax(roundtrip_logits)
  assert baseline_next == roundtrip_next
  feed the selected token into the next step for each path
```

Tests should include:

1. A short prefix contained in one block.
2. A prefix spanning multiple blocks.
3. A continuation length greater than one token.
4. A case where the final prefix block is not full, if variable final blocks are
   supported.

If logits are within tolerance but greedy tokens differ, the roundtrip fails.
If greedy tokens match but logits are outside tolerance, the roundtrip also
fails.

## Manifest and completeness checks

Before rehydration, the harness must prove that the required page set is
complete:

```text
required pages = all layers * all blocks covering the prefix token range
```

The manifest check or equivalent store query must confirm every required page
is verified, file-present, compatible, and mapped to the expected layer/block
coordinates. Missing-block queries must return deterministic reasons for
unavailable pages.

The harness must reject a manifest with `incomplete`, `corrupt`, or `unknown`
completeness for a full-prefix rehydration.

## Failure cases

Phase 4 tests should prove fail-closed behavior for:

1. Missing layer page.
2. Missing block page.
3. Corrupt payload bytes.
4. Descriptor hash mismatch.
5. Payload hash mismatch.
6. Object ID mismatch.
7. Target profile mismatch.
8. Token hash mismatch.
9. Prefix hash mismatch.
10. Token range mismatch.
11. Dtype mismatch.
12. Tensor shape mismatch.
13. Tensor layout mismatch.
14. Layer ID or block ID mismatch.
15. Page from the wrong model hash.
16. Page from the wrong tokenizer hash.
17. Page from the wrong positional config hash.
18. Staging, quarantined, evicted, missing, or catalog-inconsistent object.
19. Manifest declared complete when an expected member is unavailable.
20. Rehydrated logits outside tolerance.
21. Greedy continuation token mismatch.

In each case the expected behavior is a miss, rejection, quarantine, or explicit
test failure. The harness must never silently use suspect KV state.

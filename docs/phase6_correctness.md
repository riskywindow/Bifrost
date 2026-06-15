# Phase 6 Correctness

Last verified: 2026-06-15

## Purpose

Phase 6 measures a real serving path, but performance is only meaningful if the
benchmark does not hide incorrect responses, corrupt cache objects, or
configuration drift. Correctness checks must be explicit, deterministic where
possible, and honest about limitations.

## Deterministic settings

Real-serving runs should use deterministic generation settings when supported:

```text
temperature: 0
top_p: 1
top_k: disabled or default deterministic equivalent
max_tokens: fixed
seed: fixed when supported
chat template: fixed
model path: fixed local path
served model name: fixed
prompt serialization: recorded
```

The report should record any setting that could affect outputs. If a backend
does not support a deterministic setting, the report must state that.

## Output comparison strategy

Strict output comparison is valid only when all compared variants use the same
model, prompt serialization, decoding settings, output token limit, and serving
API behavior.

Recommended checks:

1. Compare request success or failure status.
2. Compare normalized response text exactly when deterministic settings are
   active.
3. Compare token counts when token IDs are available.
4. Compare known-answer fields for synthetic prompts where exact text is
   expected.
5. Record differences by request ID and baseline.

The benchmark should keep raw responses or hashes of raw responses according to
the selected privacy and artifact policy.

## Response equivalence limitations

Serving outputs may differ for reasons unrelated to BIFROST:

1. Non-deterministic GPU kernels.
2. Different batching decisions.
3. Different chat template application.
4. Different vLLM or LMCache versions.
5. Floating point variation.
6. Timeouts or partial streamed responses.
7. Tokenizer configuration mismatch.

When these conditions apply, correctness checks should be marked advisory or
skipped rather than overstated.

## Cache correctness

For BIFROST-backed runs:

1. LMCache objects must remain `opaque_engine_blob`.
2. BIFROST must not reinterpret tensor semantics.
3. Only committed and verified objects may satisfy hits.
4. Descriptor, object ID, opaque key hash, and payload hash checks must pass
   before data is returned to LMCache.
5. Connector errors must be counted and surfaced.
6. Store fsck findings must be included in the report.

A corrupt, mismatched, staged, missing, or semantically uncertain object must
produce a miss or deterministic connector error, not a served hit.

## Skipped or advisory checks

Correctness checks may be skipped or advisory when:

1. The run is a fake CI workload and does not produce real model text.
2. vLLM or LMCache is not installed.
3. The local model path is unavailable.
4. The installed serving stack cannot enforce deterministic decoding.
5. Streaming output boundaries prevent exact comparison.
6. A baseline failed before producing comparable responses.
7. The benchmark intentionally measures only environment readiness.

Skipped and advisory checks must be listed in the report with reasons.

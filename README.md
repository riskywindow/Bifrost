# BIFROST Phase 0 Docs

This package contains the Phase 0 deliverable docs for BIFROST, a commodity-network KV-cache fabric for long-context LLM inference.

## Files

```text
docs/
  scope.md
  correctness_contract.md
  kv_object_format.md
  benchmark_plan.md
  integration_strategy.md
```

## Intended use

These docs are written as repo-ready project contracts. They define what to build, what not to build, how correctness is enforced, how KV objects are represented, what benchmarks must prove, and how BIFROST should integrate with LMCache and vLLM.

## Recommended next step

Use these docs to initialize the repository, then start Phase 1 with:

```text
bifrost_py/kv_schema.py
bifrost_py/compatibility.py
bifrost_py/hashing.py
bifrostd/src/cache/object_meta.rs
bifrostd/src/cache/validate.rs
```

The first implementation target should be a deterministic KV object validator with known-good and known-bad fixtures.

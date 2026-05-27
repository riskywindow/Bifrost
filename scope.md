# BIFROST Phase 0 Scope

Last verified: 2026-05-27

## One sentence

BIFROST is a commodity-network KV-cache fabric for long-context LLM inference: it makes model context state movable, verifiable, cacheable, and recoverable across ordinary machines.

## Core thesis

Long-context inference is not just a model-serving problem. It is a state-movement problem. When a model processes a long prompt, it creates KV-cache state that can represent seconds or minutes of GPU work. Today, much of that state is treated as local, fragile, and tied to one worker. BIFROST treats KV cache as a first-class systems object.

The project is scoped around this claim:

> A long-context session should be able to survive path failure, worker restart, cache relocation, and heterogeneous commodity networking without blindly recomputing the entire prompt.

## What BIFROST is

BIFROST is a layer between inference engines, KV-cache systems, and storage or transport backends.

It provides:

1. A compatibility-safe KV object format.
2. A Rust daemon for PUT, GET, HAS, LIST, and object verification.
3. A multipath transfer engine for moving large KV objects across unreliable paths.
4. A local cache hierarchy spanning RAM and disk.
5. A Python client and integration adapter.
6. An LMCache remote storage plugin as the first serious integration.
7. A tiny-transformer correctness harness for end-to-end KV extraction and injection.
8. A benchmark suite, ContextStorm, for measuring performance and failure behavior.
9. A dashboard for path health, cache state, object verification, and session rehydration.

## What BIFROST is not

BIFROST is not:

1. A replacement for vLLM, SGLang, TensorRT-LLM, or LMCache.
2. A full inference server.
3. A new attention kernel.
4. A custom CUDA project.
5. A datacenter RDMA protocol.
6. A Kubernetes operator in the first release.
7. A promise of production-grade security in v0.
8. A claim that commodity networking matches frontier datacenter fabrics.

The purpose is to build a rigorous, accessible, measurable systems layer that demonstrates the key primitive: KV-cache mobility under real correctness constraints.

## Why this matters

KV caches are becoming infrastructure. LMCache describes itself as a way to store KV caches for reusable text so the same text need only be prefetched once, reducing time to first token and saving GPU cycles. vLLM Production Stack documents remote KV cache sharing with LMCache, moving large KV caches from GPU memory to remote shared storage to increase cache hits and potentially improve fault tolerance. vLLM examples also include disaggregated prefill, CPU offload, and KV cache sharing with LMCache.

BIFROST focuses on the gap between those ideas and commodity deployment:

1. What happens when the network is lossy?
2. What happens when a path dies mid-transfer?
3. What metadata prevents unsafe cache reuse?
4. Can a cheap cache node, laptop, VPS relay, and cloud GPU cooperate usefully?
5. Can a session be rehydrated without full re-prefill?

## Design center

BIFROST is designed for the following initial environment:

```text
machines:
  - one local or rented GPU worker
  - one CPU cache node with RAM and SSD
  - optional cheap VPS relay
  - optional second worker process or machine

network:
  - normal TCP by default
  - optional QUIC later
  - no RDMA assumption
  - network faults injected through tc/netem

models:
  - tiny transformer correctness harness first
  - small open model through vLLM and LMCache later
  - no full model training
```

## First integration target

The first production-adjacent integration target is LMCache remote storage plugins.

The reason is practical: LMCache exposes a custom remote storage connector interface with a ConnectorAdapter and RemoteConnector, including operations such as exists, exists_sync, get, put, list, and close. That gives BIFROST a clean place to act as a remote KV storage backend.

The second integration target is vLLM through LMCache. vLLM Production Stack already documents remote KV cache sharing with LMCache, and vLLM examples document disaggregated prefill and KV cache sharing through LMCache.

Direct vLLM KV connector work is a stretch goal, not Phase 0 scope.

## Primary users

### 1. ML infra candidate or builder

Needs a serious project demonstrating systems engineering, correctness, benchmarking, inference-stack literacy, and failure testing.

### 2. Small lab or independent researcher

Wants to run long-context workloads across cheap machines without recomputing repeated context every time.

### 3. Open-source inference ecosystem developer

Wants a transport and cache fabric that can plug into LMCache-like systems and benchmark remote KV movement under hostile networking.

## Phase 0 deliverables

The Phase 0 docs define the foundation before coding:

```text
docs/
  scope.md
  correctness_contract.md
  kv_object_format.md
  benchmark_plan.md
  integration_strategy.md
```

Each document is intended to be repo-ready and used as an implementation contract.

## Success criteria for the full project

BIFROST succeeds if the final project can show all of the following:

```text
correctness:
  - zero incorrect KV reuse in benchmark trials
  - corrupted KV objects detected before use
  - incompatible metadata rejected deterministically
  - tiny-transformer KV roundtrip matches baseline logits within tolerance

performance:
  - repeated long-context TTFT reduced by 3x to 8x in measured workloads
  - multipath transfer improves throughput over single path under lossy or jittery profiles
  - object lookup overhead stays low enough to justify reuse

resilience:
  - transfer completes when a primary path dies
  - cache node restart does not expose partial objects
  - worker rehydration demo avoids full re-prefill

integration:
  - LMCache remote storage plugin works
  - vLLM plus LMCache plus BIFROST demo runs
  - ContextStorm benchmark is reproducible
```

## Success criteria for Phase 0

Phase 0 is complete when:

1. The scope is narrow enough that an MVP can be built.
2. The correctness contract defines non-negotiable invariants.
3. The KV object format has a concrete schema and compatibility algorithm.
4. The benchmark plan defines workloads, baselines, metrics, and fault profiles.
5. The integration strategy chooses LMCache first and explains why.
6. Every later phase can use these docs as acceptance criteria.

## Architecture overview

```text
                 +-----------------------------+
                 | vLLM or tiny transformer    |
                 | prefill / decode worker     |
                 +--------------+--------------+
                                |
                                | KV store/load through adapter
                                v
                 +-----------------------------+
                 | LMCache BIFROST connector   |
                 +--------------+--------------+
                                |
                                | Python client API
                                v
+-------------------------------+--------------------------------+
|                         bifrostd                                |
|                                                                |
|  object validator     RAM tier       disk tier                 |
|  compatibility check  index          crash-safe commits         |
|  hash verifier        eviction       object inspection          |
|                                                                |
|  transport: chunker -> scheduler -> paths -> reassembler        |
|                                                                |
+-------------------------------+--------------------------------+
                                |
                                | TCP or future QUIC paths
                                v
                 +-----------------------------+
                 | peer cache or worker node   |
                 +-----------------------------+
```

## Non-negotiable design principles

### 1. Correctness before speed

BIFROST must reject incompatible or corrupted KV state even if accepting it would improve benchmark numbers.

### 2. Measurement before claims

Every performance claim must be backed by ContextStorm output, environment metadata, version pins, and logs.

### 3. Integrate, do not fork

The project should plug into existing systems where possible. LMCache is the first integration because it has a custom remote storage plugin surface.

### 4. Fail closed

If object compatibility is uncertain, BIFROST must report a miss or rejection. It must not attempt risky cache reuse.

### 5. Commodity first

The project should be useful without RDMA, without H100s, and without a multi-node datacenter cluster.

## Initial risks

### Risk: vLLM and LMCache APIs change

Mitigation:

1. Pin versions for benchmark runs.
2. Keep the tiny-transformer harness independent.
3. Treat direct vLLM connector integration as a stretch goal.
4. Keep LMCache adapter small and well isolated.

### Risk: KV serialization details are engine-specific

Mitigation:

1. Phase 4 verifies end-to-end KV extraction and injection on a tiny model.
2. Phase 5 initially stores LMCache MemoryObj bytes rather than trying to reinterpret all internals.
3. Rich BIFROST metadata is layered around engine-owned bytes.

### Risk: multipath benefits are weak on one local machine

Mitigation:

1. Use tc/netem to create controlled path diversity and failure.
2. Use a cheap VPS relay for a real second path.
3. Report both synthetic transport results and real LMCache results.

### Risk: the project becomes too large

Mitigation:

Cut order:

1. Direct vLLM connector.
2. QUIC.
3. parity chunks.
4. React dashboard.
5. multi-node GPU demo.
6. compression.
7. Kubernetes.

Keep order:

1. KV object correctness.
2. Rust daemon.
3. storage hierarchy.
4. tiny-transformer harness.
5. LMCache plugin.
6. ContextStorm benchmark.

## Phase 0 acceptance checklist

```text
[ ] scope.md states exactly what BIFROST is and is not
[ ] correctness_contract.md defines compatibility and failure invariants
[ ] kv_object_format.md defines metadata, keys, hashes, and validation
[ ] benchmark_plan.md defines ContextStorm workloads and metrics
[ ] integration_strategy.md selects LMCache first and direct vLLM later
[ ] source links are documented
[ ] no Phase 1 coding depends on an unresolved architectural question
```

## Sources reviewed

- LMCache overview: https://docs.lmcache.ai/
- LMCache integration guide: https://docs.lmcache.ai/developer_guide/integration.html
- LMCache remote storage plugins: https://docs.lmcache.ai/developer_guide/extending_lmcache/remote_storage_plugins.html
- vLLM Production Stack KV cache sharing: https://docs.vllm.ai/projects/production-stack/en/latest/use_cases/sharing-kv-cache.html
- vLLM LMCache examples: https://docs.vllm.ai/en/latest/examples/disaggregated/lmcache/
- vLLM KV transfer config: https://docs.vllm.ai/en/v0.10.2/api/vllm/config/kv_transfer.html
- OpenAI MRC overview: https://openai.com/index/mrc-supercomputer-networking/

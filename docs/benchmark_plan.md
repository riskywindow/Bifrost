# ContextStorm Benchmark Plan

Last verified: 2026-05-27

## Purpose

ContextStorm is the benchmark suite for BIFROST. It exists to prevent the project from becoming a cool demo with vague claims.

ContextStorm measures whether BIFROST improves long-context inference state movement under realistic commodity-network conditions while preserving correctness.

## Benchmark claim template

Every published result should follow this form:

```text
On workload W, with network profile N, model or synthetic KV shape M, and system versions V, BIFROST changed metric X from baseline A to result B while reporting correctness counters C.
```

No benchmark result is valid without:

```text
commit hash
hardware description
network profile
model or synthetic KV shape
runtime versions
correctness counters
raw metrics file
```

## Benchmark dimensions

ContextStorm has four independent dimensions:

```text
workload:
  what request pattern creates or reuses KV state

network profile:
  what failures or path conditions are injected

system variant:
  what cache or transfer backend is used

model or KV shape:
  real model, tiny harness, or synthetic KV payload
```

## Workloads

### 1. repeated_longdoc_qa

A user asks multiple questions over the same long document.

Shape:

```text
initial prompt: 16k to 64k tokens
follow-up prompts: short user questions over same context
expected benefit: avoid repeated prefill
```

Metrics:

```text
TTFT
cache hit rate
KV load latency
GPU seconds avoided
```

### 2. codebase_session

A developer loads a repo summary or large code context, then asks several questions.

Shape:

```text
initial prompt: file tree plus selected files
follow-ups: bug explanation, refactor question, API search
expected benefit: reusable context blocks
```

Metrics:

```text
TTFT
prefill avoided
cache reuse across turns
```

### 3. rag_reuse

Multiple requests include overlapping retrieved documents.

Shape:

```text
request A: docs 1, 2, 3
request B: docs 2, 3, 4
request C: docs 1, 3, 5
expected benefit: non-trivial repeated chunks
```

Metrics:

```text
chunk-level hit rate
partial reuse ratio
TTFT
```

### 4. agent_memory_session

A long-running agent repeatedly includes a persistent memory or state prefix.

Shape:

```text
stable memory prefix: 4k to 16k tokens
changing task suffix: 1k to 4k tokens
expected benefit: pinned prefix cache
```

Metrics:

```text
pinned-prefix hit rate
lookup latency
TTFT
```

### 5. synthetic_kv_transfer

No model required. Transfer payloads shaped like KV caches.

Shapes:

```text
small: 64 MiB
medium: 512 MiB
large: 2 GiB
huge: 8 GiB
```

Model-shaped payload example:

```text
layers: 32
heads: 32
head_dim: 128
tokens: 32768
dtype: fp16
```

Metrics:

```text
transfer throughput
p50/p95/p99 chunk latency
goodput by path
retries
completion time
hash verification time
```

## Network profiles

All profiles should be expressed as YAML and implemented through Linux tc/netem where possible.

### clean_lan.yaml

```yaml
name: clean_lan
delay_ms: 1
jitter_ms: 0
loss_pct: 0
rate_mbit: null
reorder_pct: 0
```

### wifi_jitter.yaml

```yaml
name: wifi_jitter
delay_ms: 20
jitter_ms: 30
loss_pct: 0.5
rate_mbit: 200
reorder_pct: 0
```

### loss_1pct.yaml

```yaml
name: loss_1pct
delay_ms: 30
jitter_ms: 5
loss_pct: 1.0
rate_mbit: null
reorder_pct: 0
```

### loss_5pct.yaml

```yaml
name: loss_5pct
delay_ms: 50
jitter_ms: 10
loss_pct: 5.0
rate_mbit: null
reorder_pct: 0
```

### bandwidth_collapse.yaml

```yaml
name: bandwidth_collapse
delay_ms: 20
jitter_ms: 5
loss_pct: 0.2
rate_mbit: 25
reorder_pct: 0
```

### dead_primary_path.yaml

```yaml
name: dead_primary_path
events:
  - at_seconds: 5
    action: drop_all
    path: primary
```

### asymmetric_paths.yaml

```yaml
name: asymmetric_paths
paths:
  primary:
    delay_ms: 20
    loss_pct: 0.2
    rate_mbit: 300
  relay:
    delay_ms: 80
    loss_pct: 0.0
    rate_mbit: 100
```

## System variants

### Baseline A: no cache

Every request recomputes prefill.

### Baseline B: local cache only

LMCache local CPU or disk backend, no BIFROST.

### Baseline C: simple remote storage

A single-path remote storage backend, such as filesystem, object store, Redis, or simple HTTP, depending on what is cheap and easy.

### Variant D: BIFROST single path

BIFROST object verification and storage without multipath scheduling.

### Variant E: BIFROST multipath

BIFROST uses two or more measured paths and adaptive chunk scheduling.

### Variant F: BIFROST multipath plus parity

Optional later variant using parity chunks.

## Metrics

### Inference metrics

```text
TTFT_seconds
inter_token_latency_seconds
request_latency_seconds
tokens_per_second
requests_per_second
```

### Cache metrics

```text
cache_hit_count
cache_miss_count
cache_hit_rate
cache_lookup_latency_seconds
kv_load_latency_seconds
kv_store_latency_seconds
bytes_loaded
bytes_stored
prefill_tokens_avoided
estimated_gpu_seconds_avoided
```

### Transport metrics

```text
transfer_bytes
transfer_seconds
goodput_mbps
path_goodput_mbps
chunk_latency_p50_seconds
chunk_latency_p95_seconds
chunk_latency_p99_seconds
retry_count
timeout_count
path_failover_seconds
```

### Correctness metrics

```text
incorrect_cache_reuse_count
corrupt_object_detected_count
hash_mismatch_count
compatibility_rejection_count
partial_write_recovered_count
missing_layer_rejection_count
tiny_transformer_logit_error_max
tiny_transformer_kl_divergence
```

### Cost metrics

```text
gpu_seconds_used
gpu_seconds_avoided
estimated_dollars_used
estimated_dollars_saved
```

## Primary headline metrics

The project should optimize for these final headline metrics:

```text
repeated long-context TTFT reduction
transfer completion under path failure
zero incorrect cache reuse
p95 transfer latency under packet loss
worker or process rehydration time
```

## Minimum viable benchmark matrix

For a strong first release:

```text
workloads:
  - synthetic_kv_transfer
  - repeated_longdoc_qa
  - tiny_transformer_roundtrip
  - one vLLM plus LMCache repeated-context demo

network profiles:
  - clean_lan
  - loss_1pct
  - loss_5pct
  - dead_primary_path
  - bandwidth_collapse

system variants:
  - no cache
  - LMCache local
  - BIFROST single path
  - BIFROST multipath
```

## Stretch benchmark matrix

```text
workloads:
  - codebase_session
  - rag_reuse
  - agent_memory_session

network profiles:
  - wifi_jitter
  - asymmetric_paths
  - cache_node_restart
  - worker_preemption

system variants:
  - BIFROST multipath plus parity
  - direct vLLM connector prototype if built
```

## Benchmark runner CLI

```bash
contextstorm run configs/synthetic_transfer_loss_5pct.yaml
contextstorm run configs/repeated_longdoc_lmcache_bifrost.yaml
contextstorm report runs/2026-05-27-sweep-001
```

Output structure:

```text
runs/2026-05-27-sweep-001/
  config.yaml
  environment.json
  metrics.jsonl
  correctness.json
  system_versions.json
  plots/
  report.html
  raw_logs/
```

## Environment capture

Each run must capture:

```text
hostname
kernel version
CPU model
RAM
GPU model if present
GPU driver if present
CUDA version if present
Python version
Rust version
bifrostd commit
bifrost_py commit
LMCache version
vLLM version
model ID
network profile
```

## Example benchmark config

```yaml
name: repeated_longdoc_bifrost_loss_1pct
workload: repeated_longdoc_qa
system_variant: bifrost_multipath
model:
  engine: vllm_lmcache
  model_id: mistralai/Mistral-7B-Instruct-v0.2
  max_model_len: 16384
network:
  profiles:
    primary: loss_1pct
    relay: clean_lan
cache:
  backend: bifrost
  bifrost_endpoint: 127.0.0.1:7744
  local_disk_gb: 50
requests:
  initial_context_tokens: 12000
  followup_count: 20
  output_tokens: 64
metrics:
  collect_prometheus: true
  collect_correctness: true
```

## Expected plots

The final report should include:

```text
TTFT vs context length
TTFT baseline vs cache variants
transfer completion time vs packet loss
p95 chunk latency vs network profile
cache hit rate vs repeated turns
path goodput over time
failover timeline under dead primary path
correctness counters by run
```

## Success targets

These are target goals, not assumed results:

| Metric | Strong target |
|---|---:|
| Incorrect cache reuse | 0 |
| Corrupt object detection | 100 percent of injected corruptions |
| Repeated long-context TTFT reduction | 3x to 8x |
| Multipath improvement under 5 percent loss | 2x or better than single-path baseline |
| Path failure recovery | transfer completes without manual restart |
| Cache lookup overhead | less than 10 ms median |
| Control-plane overhead | less than 100 ms median |
| Tiny-transformer greedy continuation | identical after KV roundtrip |

## Reporting rules

Do not publish a single aggregate number without distributions.

For every key metric, report:

```text
mean
median
p95
p99
min
max
sample count
```

For every performance win, report the cost:

```text
extra CPU
extra memory
extra network traffic
extra latency overhead
```

## Budget discipline

Most ContextStorm testing should run without GPUs:

```text
synthetic transfer: CPU only
object store tests: CPU only
network fault profiles: CPU only
tiny-transformer roundtrip: CPU or cheap GPU
vLLM plus LMCache final demo: rented GPU only after earlier phases pass
```

## Sources reviewed

- LMCache overview and 3x to 10x TTFT/GPU-cycle savings claim for many use cases: https://docs.lmcache.ai/
- LMCache integration guide: https://docs.lmcache.ai/developer_guide/integration.html
- vLLM Production Stack KV sharing with LMCache: https://docs.vllm.ai/projects/production-stack/en/latest/use_cases/sharing-kv-cache.html
- vLLM LMCache examples: https://docs.vllm.ai/en/latest/examples/disaggregated/lmcache/
- OpenAI MRC overview for multipath inspiration: https://openai.com/index/mrc-supercomputer-networking/

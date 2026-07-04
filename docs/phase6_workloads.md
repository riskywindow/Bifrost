# Phase 6 Workloads

Last verified: 2026-06-20

## Purpose

Phase 6 workloads should create repeated-prefix serving patterns where LMCache
can reasonably reuse KV state and BIFROST can be observed as remote storage.
They must be deterministic enough to compare baselines and small enough to run
locally when a developer opts into real serving.

Real workloads require an already available local model. CI uses the fake
serving workload instead.

## JSONL request schema

Serving workloads are written as JSONL. Each line is one request:

```json
{
  "request_id": "fake_ci_small-1234-00000",
  "prompt": "System policy ...",
  "max_tokens": 16,
  "temperature": 0.0,
  "top_p": 1.0,
  "stop": ["optional stop"],
  "metadata": {
    "workload_name": "fake_ci_small",
    "prefix_id": "fake_ci_small-prefix-1234-0000",
    "repeat_group": 0,
    "expected_cache_reuse": false,
    "phase": "measured",
    "prompt_token_estimate": 112
  }
}
```

`stop` is optional. `prompt_token_estimate` is advisory and currently computed
without a tokenizer from prompt character length. It is not included in object
identity or correctness decisions.

`metadata.phase` is required for newly generated workloads and defaults to
`measured` when older JSONL files are read. Valid values are:

1. `engine_warmup`
2. `cache_population`
3. `measured`

The schema and JSONL helpers live in
`bifrost_py/bifrost_serving/request_schema.py`.

## Benchmark phases

The serving runner has explicit benchmark phases:

1. `engine_warmup`: ordinary engine warmup requests with isolated synthetic
   prefixes that must not overlap measured prefix groups. These samples are
   excluded from reported benchmark latency and throughput.
2. `cache_population`: one configurable request per measured prefix group.
   These requests prepare cache state and are excluded from measured latency.
   Their request IDs and raw results are preserved.
3. `measured`: only requests marked `phase=measured`. Top-level performance
   fields are computed exclusively from these samples.

The default phase order is:

```text
engine_warmup,cache_population,measured
```

Population is opt-in by default for compatibility with older runner commands.
Use `--population-requests-per-prefix 1` to send one population request for
each measured prefix group. Use `--engine-warmup-requests` for isolated engine
warmup and `--measured-requests-per-prefix` to cap measured samples per prefix.

## Generator

The deterministic generator lives in
`bifrost_py/bifrost_serving/workloads.py`. The CLI wrapper is:

```bash
python tools/bifrost_generate_serving_workload.py \
  --workload fake-ci-small \
  --out runs/phase6-workload/requests.jsonl \
  --request-count 8 \
  --prefix-repeat-groups 2 \
  --max-tokens 16 \
  --seed 1234 \
  --prefix-size small \
  --json-summary runs/phase6-workload/summary.json
```

Supported CLI workloads:

1. `repeated-system-prompt`
2. `repeated-document-qa`
3. `repeated-code-context`
4. `multi-turn-same-prefix`
5. `synthetic-random-prefix-control`
6. `fake-ci-small`

All generators are deterministic for the tuple:

```text
workload, seed, request_count, prefix_repeat_groups, max_tokens, prefix_size
```

`--prefix-size` accepts `small`, `medium`, `large`, or an explicit positive
character count. Generation does not import vLLM, LMCache, torch,
transformers, tokenizers, or any network client.

## Repeated system prompt workload

Shape:

```text
stable system prompt: long instruction or policy text
request suffix: short independent user questions
expected reuse: common system prompt prefix
```

Use this workload to validate basic repeated-prefix cache behavior. Engine
warmup is generated as a separate non-overlapping phase by the runner; cache
population uses one or more requests per measured prefix group before measured
requests are sent.

Generated requests use one stable prefix per repeat group. The first request in
each group has `expected_cache_reuse=false`; later requests in the same group
set it to `true`.

Metrics of interest:

1. TTFT change after warmup.
2. LMCache hit/miss counts when available.
3. BIFROST `put`, `exists`, and `get` counts.
4. Output correctness under deterministic settings when possible.

## Repeated document QA workload

Shape:

```text
stable document prefix: local text supplied by the benchmark fixture
request suffix: multiple questions over the same document
expected reuse: document prefix and possibly LMCache chunk reuse
```

The document must live in the repository or be generated locally. Do not
download benchmark corpora by default.

The workload should report prompt byte length, approximate token count when
available, question count, and whether all variants used the same document.

The default document is generated locally from deterministic benchmark text.
No external corpus is downloaded.

## Repeated code context workload

Shape:

```text
stable code context: generated or repository-local code snippets
request suffix: explanation, bug hunt, and refactor questions
expected reuse: code context prefix
```

Use this workload to represent developer-agent sessions. It should avoid
embedding very large repository snapshots in default configs; real runs may
select a larger local fixture explicitly.

The default code context is synthetic and repository-themed. It is not a raw
repository snapshot.

## Multi-turn repeated-prefix workload

Shape:

```text
turn 1: long context plus first question
turn 2..N: conversation history with stable prefix and changing suffix
expected reuse: stable initial context and prior turns, depending on LMCache
```

This workload should record how prompts are constructed for each turn because
chat templates and history formatting can change cache boundaries.

Correctness comparisons are often advisory here unless decoding is fully
deterministic and the same prompt serialization is proven across variants.

The generator records the serialized prompt directly in JSONL so later runners
and reports can preserve the exact prompt boundary used for each request.

## Synthetic random-prefix control

The random-prefix control intentionally gives each request a distinct prefix
and sets `expected_cache_reuse=false`. It is used to compare repeated-prefix
workloads against a low-reuse request stream under the same request count,
seed, max tokens, and prefix size.

## Deterministic fake serving workload for CI

The fake serving workload must run in CI without importing vLLM, real LMCache,
GPU libraries, model assets, tokenizers, or internet clients.

`fake-ci-small` is the default CI workload. It produces small deterministic
prompts, records repeat-group metadata, and writes a summary with:

1. Request count.
2. Seed.
3. Configured and actual repeat groups.
4. Prefix ID counts.
5. Expected cache reuse count.
6. Repeated-prefix ratio.
7. Advisory dependency flags, all `false`.

The fake serving path is split into cache backends:

1. `NoCacheBackend`: always misses and performs no cache storage.
2. `LocalMemoryCacheBackend`: uses an in-process dictionary and reports only
   local fake backend counters.
3. `BifrostLMCacheBackend`: uses the real Phase 5
   `BifrostRemoteConnector`, fake Phase 5 `CacheEngineKey` and `MemoryObj`
   types, and a local `bifrost-daemon`.

The default `serve_fake_small_ci.yaml` candidate mode is
`fake_bifrost_lmcache`. It starts a temporary local BIFROST daemon, sends cache
population requests, sends measured repeated-prefix requests, and requires
actual connector `put`, `exists`, and `get` activity. The fake server still
generates deterministic OpenAI-compatible responses and synthetic timing; this
is not real vLLM performance evidence.

It should exercise:

1. A request stream with stable prefixes and changing suffixes.
2. Cache-shaped repeated-prefix hit and miss activity through a configured
   cache backend.
3. Real connector `put`, `exists`, and `get` operations in
   `fake_bifrost_lmcache`; counters must come from
   `actual_bifrost_remote_connector`, not synthesized fake server fields.
4. Deterministic TTFT-like and latency-like timing fields generated by the
   fake runner, not claimed as real serving performance.
5. BIFROST store stats and fsck when a local daemon-backed fake run is used.
6. A report with the same schema shape as real-serving reports.

Fake timing must be labeled with
`performance_metrics_source: synthetic_fake_server`. Connector counters from
`fake_bifrost_lmcache` must be labeled with
`connector_metrics_source: actual_bifrost_remote_connector`.

The current generator creates request streams only. HTTP clients, fake serving
metrics, BIFROST connector/store collectors, and report generation are separate
Phase 6 steps. The default fake CI path now executes real BIFROST connector
operations against a live local daemon while preserving the fake-server timing
label.

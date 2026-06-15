# Phase 6 Workloads

Last verified: 2026-06-15

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
    "prompt_token_estimate": 112
  }
}
```

`stop` is optional. `prompt_token_estimate` is advisory and currently computed
without a tokenizer from prompt character length. It is not included in object
identity or correctness decisions.

The schema and JSONL helpers live in
`bifrost_py/bifrost_serving/request_schema.py`.

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

Use this workload to validate basic repeated-prefix cache behavior. It should
include a warmup request and repeated measured requests with the same system
prompt.

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

The fake serving workload must run in CI without importing vLLM or LMCache.

`fake-ci-small` is the default CI workload. It produces small deterministic
prompts, records repeat-group metadata, and writes a summary with:

1. Request count.
2. Seed.
3. Configured and actual repeat groups.
4. Prefix ID counts.
5. Expected cache reuse count.
6. Repeated-prefix ratio.
7. Advisory dependency flags, all `false`.

It should simulate:

1. A request stream with stable prefixes and changing suffixes.
2. Cacheable opaque payload creation.
3. Connector-like `put`, `exists`, `get`, and `list` activity.
4. Deterministic TTFT-like and latency-like timing fields generated by the
   fake runner, not claimed as real serving performance.
5. BIFROST store stats and fsck when a local daemon-backed fake run is used.
6. A report with the same schema shape as real-serving reports.

Fake metrics must be labeled as fake. They validate orchestration, metrics
plumbing, report generation, and fail-closed handling, not vLLM performance.

The current generator creates request streams only. HTTP clients, fake serving
metrics, connector-like activity, and report generation are separate Phase 6
steps.

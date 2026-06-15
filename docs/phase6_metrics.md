# Phase 6 Metrics

Last verified: 2026-06-15

## Purpose

Phase 6 metrics must make the serving benchmark auditable. Raw metrics should
be preserved, derived metrics should be reproducible, and reports must clearly
separate real serving measurements from fake CI measurements.

## Serving metrics

### TTFT

Time to first token measures request start to first generated token. It is the
primary metric for cache reuse in prefill-heavy workloads.

Report:

1. Mean when useful.
2. p50.
3. p95.
4. Per-baseline raw values or raw metrics file path.

### End-to-end latency

End-to-end latency measures request start to completed response.

Report p50 and p95 for every baseline. If streaming and non-streaming clients
use different measurement boundaries, the report must state that boundary.

### Output token latency

Output token latency measures decode performance after the first token. It may
be reported as inter-token latency, time per output token, or the field exposed
by `vllm bench serve`.

The report must preserve the source field name.

### Requests per second

Requests per second should be reported for the measured interval and request
set. Do not compare RPS across variants that used different concurrency,
request counts, prompt lengths, output lengths, or failed request handling.

### Error rate

Error rate should include:

1. HTTP or serving request failures.
2. Timeout count.
3. vLLM process failures.
4. LMCache process or connector failures.
5. BIFROST daemon or store failures.
6. Correctness mismatches when strict comparison is enabled.

## BIFROST connector metrics

For BIFROST-backed runs, report:

```text
put_count
get_count
exists_count
list_count
close_count
put_error_count
get_error_count
exists_error_count
serialization_error_count
validation_error_count
store_error_count
key_mismatch_count
payload_hash_mismatch_count
bytes_put
bytes_get
```

Exact names may follow the connector implementation, but the report should map
them to stable Phase 6 names.

## BIFROST bytes stored and loaded

Report:

1. Bytes submitted to BIFROST by connector puts.
2. Bytes returned by connector gets.
3. Store object count before and after each run.
4. Store total payload bytes before and after each run.
5. Evicted or quarantined object counts when available.

These counters must not be treated as LMCache hit rates by themselves.

## LMCache metrics

When available, report LMCache:

1. Hit count.
2. Miss count.
3. Local storage hits.
4. Remote storage hits.
5. Remote storage puts.
6. Evictions.
7. Any version-specific cache reuse counters.

If LMCache metrics are unavailable, the report should say so and fall back to
BIFROST connector observations as lower-level evidence.

## vLLM bench serve JSON ingestion

When `vllm bench serve` JSON is available, ingest it as raw input and preserve
the original file in the run directory.

The parser should extract:

1. TTFT fields.
2. End-to-end latency fields.
3. Output token latency or inter-token latency fields.
4. Request throughput.
5. Token throughput when available.
6. Error counts or failed request records.
7. Benchmark arguments.

If a field is absent in the installed vLLM version, the derived report should
mark it missing instead of inventing a value.

## Store health and fsck

Every BIFROST-backed report should include:

1. `bifrost-store stats` or daemon stats before and after the run.
2. `bifrost-store fsck` status when available.
3. Corrupt, missing, orphaned, staged, quarantined, or index-inconsistent
   object counts.
4. Whether fsck was skipped and why.

A failed fsck does not become a cache hit. It is a benchmark failure or health
finding that must be shown in the report.

## Runner summary schema

The Phase 6 serving runner writes `summary.json` with schema version
`bifrost.serving_summary.v1`. The current runner computes:

1. `request_count`
2. `success_count`
3. `error_count`
4. `error_rate`
5. `p50_latency_ms`
6. `p95_latency_ms`
7. `mean_latency_ms`
8. `p50_ttft_ms`, `p95_ttft_ms`, and `mean_ttft_ms` when TTFT is available
9. `ttft_available_count`
10. `output_token_count` and `mean_output_tokens` when parseable
11. `throughput_rps`
12. `run_duration_s`
13. `cache_expected_request_count`
14. `repeated_prefix_group_count`
15. `bifrost_stats_delta`
16. `connector_metrics_delta`

For non-streaming OpenAI-compatible responses, TTFT is usually unavailable and
the TTFT fields are `null` with `ttft_available_count` set to zero.

The runner derives output token count from OpenAI-compatible `usage` fields
when present, falling back to whitespace token counting only when the response
does not expose a token count. Raw response JSON is preserved in
`raw_requests.jsonl` so the derivation can be audited.

When BIFROST daemon stats are collected, numeric fields are diffed between the
before and after snapshots. `total_logical_bytes` is also exposed as
`bytes_stored` in the delta for the stable Phase 6 report vocabulary. Connector
operation counters such as `put_count`, `get_count`, `exists_count`, and
`list_count` are included when a metrics source exposes those names; otherwise
the field remains `null` and the report must say that connector metrics were
unavailable.

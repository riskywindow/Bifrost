# Phase 6 Metrics

Last verified: 2026-06-20

## Purpose

Phase 6 metrics must make the serving benchmark auditable. Raw metrics should
be preserved, derived metrics should be reproducible, and reports must clearly
separate real serving measurements from fake CI measurements.

## Serving metrics

All top-level performance metrics in `summary.json` are measured-phase metrics
only. `engine_warmup` and `cache_population` samples are preserved as raw
records and phase sections, but they must not contribute to top-level latency,
TTFT, throughput, output-token, cache-expected, or error aggregates. Runner
validation fails if non-measured rows leak into the measured aggregate.

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

The default fake CI candidate uses `BifrostLMCacheBackend`, which instantiates
the actual Phase 5 `BifrostRemoteConnector`. Its summary must include:

1. `connector_metrics_source: actual_bifrost_remote_connector`
2. `performance_metrics_source: synthetic_fake_server`
3. Actual connector `put_count`, `exists_count`, and `get_count`
4. Actual connector `bytes_put` and `bytes_get`
5. BIFROST store object-count delta
6. fsck status from the local daemon store

The fake server may expose cache-hit timing for harness tests, but it must not
fabricate connector counters. When connector metrics are unavailable, the
report must say unavailable instead of projecting fake cache hits into
connector operations.

## Phase 6 collectors

The dependency-light collectors live in
`bifrost_py/bifrost_serving/collectors.py`. Each collector supports:

```python
snapshot_before()
snapshot_after()
delta()
```

Snapshots preserve the raw source payload under `raw` when data is available.
Unavailable optional sources return a structured `status: "unavailable"` or
`status: "error"` snapshot instead of failing the benchmark.

`BifrostMetricsCollector` uses the Python BIFROST client to collect daemon store
stats, object count, total logical bytes as `bytes_stored`, committed
LMCache-shaped opaque object count when object listing is available, optional
fsck output, and optional connector metrics from a JSONL log or endpoint. The
collector does not reinterpret LMCache payloads; it only counts
`opaque_engine_blob` records exposed by the store API.

`LMCacheMetricsCollector` queries a configured LMCache management or metrics
endpoint. Version-specific fields are preserved as raw JSON or raw text, and
known hit, miss, local hit, remote hit, remote put, and eviction counters are
extracted defensively when their names are recognizable. If no endpoint is
configured or the endpoint is unreachable, the benchmark continues and the
snapshot records why LMCache metrics were unavailable.

`VLLMMetricsCollector` queries a configured vLLM metrics endpoint and preserves
raw JSON or text/Prometheus-style metrics. It extracts a small set of known
request and cache fields when present, but missing fields remain `null` rather
than invented.

Every extracted metric carries an authoritative source label where the source
is known. Supported labels are:

1. `vllm_bench_serve`
2. `vllm_metrics_endpoint`
3. `lmcache_prometheus`
4. `lmcache_internal_api`
5. `bifrost_connector_metrics`
6. `bifrost_connector_jsonl`
7. `bifrost_store_stats`
8. `synthetic_fake_server`
9. `unavailable`

Synthetic fake-server timing and cache-shape counters must never be collapsed
into real BIFROST connector counters. Aggregation keeps same-name metrics as
multiple source-tagged records instead of overwriting one source with another.
Missing optional metrics are represented as unavailable or `null`, never as
zero.

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

The LMCache parser recognizes these exact metrics from Prometheus text or JSON
internal API payloads:

```text
lmcache:num_retrieve_requests
lmcache:num_store_requests
lmcache:num_lookup_requests
lmcache:num_requested_tokens
lmcache:num_hit_tokens
lmcache:num_stored_tokens
lmcache:num_lookup_tokens
lmcache:num_lookup_hits
lmcache:retrieve_hit_rate
lmcache:lookup_hit_rate
lmcache:time_to_retrieve
lmcache:time_to_store
lmcache:time_to_lookup
```

Unknown `lmcache:*` numeric fields are preserved under raw/unknown metrics for
later inspection. A missing field remains unavailable and must not be displayed
as `0`.

## Reproducibility bundle

Each mode directory should contain the complete provenance bundle:

```text
resolved_run_config.yaml
generated_vllm_command.json
generated_lmcache_config.yaml              # LMCache modes
generated_bifrost_connector_config.json    # BIFROST mode
workload.jsonl
phase_plan.json
environment_doctor.json
versions.json
command_manifest.json
metrics_before.json
metrics_after_population.json
metrics_after_measured.json
raw_requests.jsonl
stdout.log
stderr.log
artifact_manifest.json
```

`artifact_manifest.json` uses schema
`bifrost.phase6_artifact_manifest.v1` and records each artifact's relative
path, SHA-256, byte size, and artifact type. The report lists generated config
artifacts and hashes when a manifest is present, verifies artifact hashes, and
shows missing required artifacts directly.

`versions.json` captures Python, OS, CUDA/GPU when available, vLLM, LMCache,
`lmcache_bifrost`, torch, bifrostd path/version, model local/remote status,
workload hash, git commit, and dirty worktree status. Environment values are
redacted for API keys, Hugging Face tokens, authorization headers, tokens, and
secrets before being written.

## vLLM bench serve JSON ingestion

When `vllm bench serve` JSON is available, ingest it as raw input and preserve
the original file in the run directory.

The optional integration lives in `bifrost_py/bifrost_serving/vllm_bench.py`
and is exposed through `tools/bifrost_run_vllm_bench_serve.py`. It is defensive
because vLLM benchmark JSON field names vary by version. The parser extracts
fields by matching known metric name fragments and leaves absent fields as
missing instead of inventing values.

The parser extracts:

1. TTFT fields.
2. End-to-end latency fields.
3. Output token latency or inter-token latency fields.
4. Request throughput.
5. Token throughput when available.
6. Error counts or failed request records.
7. Benchmark arguments.

If a field is absent in the installed vLLM version, the derived report should
mark it missing instead of inventing a value.

When the installed `vllm bench serve` exposes `--num-warmups`, the BIFROST
command builder passes configured ordinary server warmups through that option.
Those vLLM warmups are not a replacement for BIFROST cache population: cache
population remains the explicit Phase 6 `cache_population` phase and is
reported separately.

The current ingestion summary schema is `bifrost.vllm_bench_serve_ingest.v1`
and contains:

1. `raw_result_path`
2. `request_count`
3. `throughput_rps`
4. `token_throughput`
5. `ttft`
6. `latency`
7. `output_token_latency`
8. `error_count`
9. `benchmark_args`
10. `raw_top_level_keys`

The optional run summary schema is `bifrost.vllm_bench_serve_run.v1`. It records
availability, the exact command, return code, stdout and stderr tails, result
ingestion status, and whether the run was `dry_run`, `skipped`, `completed`, or
`failed`.

## Store health and fsck

Every BIFROST-backed report should include:

1. `bifrost-store stats` or daemon stats before and after the run.
2. `bifrost-store fsck` status when available.
3. Corrupt, missing, orphaned, staged, quarantined, or index-inconsistent
   object counts.
4. Whether fsck was skipped and why.

A failed fsck does not become a cache hit. It is a benchmark failure or health
finding that must be shown in the report.

Reports summarize the current environment doctor readiness keys:
`fake_ci_ready`, `gpu_serving_ready`, and `full_benchmark_ready`. Missing
optional serving readiness remains a skip or limitation, not a fake-CI failure.

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

The summary also includes:

1. `phase` with value `measured` for the top-level aggregate.
2. `phase_order` and `phase_timeout_seconds`.
3. `phase_sections`, keyed by `engine_warmup`, `cache_population`, and
   `measured` when those phases ran.
4. `phase_validation`, including measured and non-measured raw row counts.

Each raw request record includes `phase`, `prefix_id`, `repeat_group`, and
`expected_cache_reuse` in addition to the full metadata object. Population
phase sections include BIFROST and backend metric snapshots immediately before
and after population, with numeric deltas when available. Missing connector,
LMCache, or BIFROST counters remain skipped, unavailable, or error snapshots;
the runner does not synthesize connector counters.

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

For `fake_bifrost_lmcache`, connector metrics are collected from the connector
JSONL emitted by the real connector and from the backend's `/metrics` snapshot.
The report treats timing as fake-serving performance only, even when the
connector and BIFROST store counters are real.

## Baseline comparison summary schema

The Phase 6 comparison runner writes `comparison_summary.json` with schema
version `bifrost.serving_baseline_comparison.v1`. The summary preserves:

1. `mode_results`: one record per requested mode, with `completed`, `skipped`,
   or `failed` status.
2. `summary_path` and artifact paths for completed mode runs.
3. `skip_reason` for skipped real vLLM modes or missing real-mode inputs.
4. `comparisons`: derived comparisons against the first completed mode.
5. `notes`: advisory statements about skipped, failed, or fake-only results.

Each comparison includes:

1. `baseline_mode`
2. `candidate_mode`
3. `latency_delta_ms` and `latency_delta_pct` from p50 end-to-end latency
4. `ttft_delta_ms` and `ttft_delta_pct` from p50 TTFT when available
5. `error_rate_delta`
6. `bifrost_stats_delta` copied from the candidate run summary
7. `cache_activity_observed`
8. `notes`
9. `skipped_reason`

`cache_activity_observed` is true when a supported metrics source shows
positive cache-shaped activity, such as fake backend cache hits or misses,
connector operation counters, connector bytes, or BIFROST store/operation
deltas. These observations are evidence that cache-related paths were
exercised, not proof of an LMCache hit rate unless LMCache metrics are present.

Skipped, failed, or missing modes are included in the JSON and Markdown output,
but their deltas remain `null` and they must not be described as measured
speedups.

## Serving report artifacts

The Phase 6 report generator reads a completed serving run directory and, when
available, a baseline comparison directory:

```text
tools/bifrost_report_serving_benchmark.py \
  --run-dir runs/phase6-serving/fake_with_cache \
  --comparison-dir runs/phase6-serving-comparison \
  --out runs/phase6-serving-report \
  --format all
```

If `--out` is omitted, artifacts are written under
`<run-dir>/serving_report/` so the runner's source `summary.json` is not
overwritten. `--format` may be `markdown`, `json`, `csv`, or `all`.

The generated artifacts are:

1. `report.md`: human-readable report with environment, scenario, workload,
   mode, latency, BIFROST activity, correctness, skipped component, and
   limitation sections.
2. `summary.json`: machine-readable report summary with schema version
   `bifrost.serving_report.v1`.
3. `per_request.csv`: flattened request metrics from `raw_requests.jsonl`.
4. `comparison.csv`: flattened comparison deltas when
   `comparison_summary.json` exists in `--comparison-dir`.

The report generator is an artifact transformer. It does not start vLLM,
LMCache, BIFROST, download models, inspect the network, or require GPU
hardware. Missing TTFT, missing BIFROST stats, skipped real vLLM modes, and
missing comparison inputs are rendered explicitly as unavailable, skipped, or
not provided. It does not infer speedups unless comparison records contain both
baseline and candidate metrics.

# Phase 6 Checklist

Last verified: 2026-07-04

## Final Phase 6 review evidence

Phase 6 is complete. The final review reported:

- Phase 6 status: complete.
- Gate failures: none.
- Real modes executed: 9 rows, 3 modes x 3 repetitions.
- Modes: `vllm_only`, `vllm_lmcache_local_cpu`, and
  `vllm_lmcache_bifrost`.
- BIFROST connector activity observed.
- LMCache local CPU activity observed.
- BIFROST fsck clean.
- No speedup claimed because BIFROST was slower in the single-host run.

This checklist preserves the original Phase 6 acceptance criteria and marks
stale unchecked boxes as complete only where the final review or existing
coverage satisfies them.

## Environment doctor

- [x] Report Python version and executable.
- [x] Report platform, CPU, memory when available, and disk space.
- [x] Detect CUDA, driver visibility, and GPU names when available.
- [x] Detect `torch` import, version, CUDA availability, and device count.
- [x] Detect `vllm` import and version.
- [x] Detect `lmcache` import and version.
- [x] Detect BIFROST Python client import.
- [x] Detect LMCache BIFROST connector import and adapter availability.
- [x] Detect `bifrostd` binary availability.
- [x] Probe configured BIFROST daemon endpoint.
- [x] Validate connector config parseability.
- [x] Check local model path existence without downloading assets.
- [x] Report Hugging Face token presence as advisory only.
- [x] Check required port availability.
- [x] Check writable run directory and disk-space threshold.
- [x] Report git commit and dirty-tree status.
- [x] Emit readiness level and precise skip reasons.

## Config generator

- [x] Generate vLLM-only baseline config.
- [x] Generate vLLM plus LMCache local or CPU storage baseline config.
- [x] Generate vLLM plus LMCache plus BIFROST remote storage config.
- [x] Generate BIFROST connector config metadata for the BIFROST candidate.
- [x] Generate first-class three-baseline matrix artifacts with fairness
  validation.
- [x] Explicitly emit vLLM prefix-caching settings for every matrix mode.
- [x] Record effective environment variables for each variant.
- [x] Emit version-sensitive warnings.
- [x] Disable pickle fallback for real LMCache traffic.
- [x] Keep model paths local and never trigger downloads.
- [x] Save generated configs as report artifacts.

## Process orchestrator

- [x] Start and stop local `bifrostd` for BIFROST-backed runs.
- [x] Start and stop vLLM serving processes only when explicitly opted in.
- [x] Support LMCache in-process mode.
- [x] Support optional LMCache multiprocess mode.
- [x] Wait for readiness probes with deterministic timeouts.
- [x] Capture stdout, stderr, exit status, and logs for every process.
- [x] Avoid root, Docker, internet, and network mutation in default paths.
- [x] Cleanly terminate child processes on failure.

## Workload generator

- [x] Generate repeated system prompt workloads.
- [x] Generate repeated document QA workloads from local or generated text.
- [x] Generate repeated code context workloads from local fixtures.
- [x] Generate multi-turn repeated-prefix workloads.
- [x] Generate deterministic fake serving workloads for CI.
- [x] Record prompt serialization and request IDs.
- [x] Record approximate prompt and output token lengths when available.

## Benchmark runner

- [x] Run baselines separately.
- [x] Preserve raw metrics for each baseline.
- [x] Refuse to compare missing or incompatible baselines as speedups.
- [x] Provide response correctness comparison modes for exact, normalized,
  token-count, advisory, and skipped checks.
- [x] Support explicit engine warmup, cache population, and measured request
  phases.
- [x] Exclude engine warmup and cache population samples from top-level
  measured latency, TTFT, throughput, and error aggregates.
- [x] Preserve population request IDs, raw results, and immediate before/after
  cache or connector metric snapshots when collectors are configured.
- [x] Record request count, concurrency, max output tokens, and timeout.
- [x] Capture request failures and timeouts.
- [x] Preserve generated configs with the run output.
- [x] Add guarded real-serving matrix executor for `vllm_only`,
  `vllm_lmcache_local_cpu`, and `vllm_lmcache_bifrost`.
- [x] Support real matrix dry-run without vLLM, LMCache, GPU, or model assets.
- [x] Support repetitions, deterministic rotated mode order, unique ports, and
  one artifact directory per mode and repetition.
- [x] Keep the real matrix completion gate strict: skips, dry-run, missing
  samples, missing configs, skipped correctness, absent LMCache activity,
  absent BIFROST activity, or dirty fsck prevent PASS.

## Metrics collectors

- [x] Ingest `vllm bench serve` JSON when available.
- [x] Capture HTTP request start, response end, status code, and error fields
  for fake OpenAI-compatible serving requests.
- [x] Capture real serving TTFT when exposed by `vllm bench serve` JSON.
- [x] Capture p50 and p95 end-to-end latency.
- [x] Capture output token latency when exposed by `vllm bench serve` JSON.
- [x] Capture requests per second.
- [x] Capture error rate.
- [x] Capture BIFROST connector `put`, `get`, `exists`, and `list` counts.
- [x] Capture BIFROST connector error counts.
- [x] Capture BIFROST bytes stored through daemon stats when available.
- [x] Capture LMCache hit/miss metrics when available.
- [x] Parse exact LMCache Prometheus/internal API metric names and preserve
  unknown LMCache raw metrics.
- [x] Preserve authoritative metric source labels, including synthetic fake
  metrics and unavailable optional metrics.
- [x] Capture BIFROST store stats before and after BIFROST-backed runs.
- [x] Capture fsck status or skip reason.

## Report generator

- [x] Include commit, dirty-tree status, and repository path.
- [x] Include Python, torch, vLLM, LMCache, connector, bifrostd, CUDA, and
  driver versions when available.
- [x] Include hardware and GPU details when available.
- [x] Include model path or identifier and local-asset statement when the run
  or comparison artifacts contain a model value.
- [x] Include workload definition and request counts.
- [x] Include all generated configs from the artifact manifest, with hashes.
- [x] Include raw metrics file paths.
- [x] Include baseline comparison tables.
- [x] Include failures and skipped components.
- [x] Include environment readiness level.
- [x] Avoid speedup claims unless measured.
- [x] Include phase counts.
- [x] Show metric sources and unavailable metrics explicitly.
- [x] Verify artifact hashes in the reproducibility bundle.

## Fake CI workload

- [x] Run OpenAI-compatible fake server without vLLM.
- [x] Run OpenAI-compatible fake server without LMCache.
- [x] Run OpenAI-compatible fake server without GPU or CUDA.
- [x] Run OpenAI-compatible fake server without Hugging Face token.
- [x] Run OpenAI-compatible fake server without model downloads.
- [x] Run OpenAI-compatible fake server without Docker, root, internet, or
  external services.
- [x] Send generated workload requests through the HTTP client.
- [x] Exercise small-scale client concurrency.
- [x] Exercise cache-shaped repeated-prefix hit and miss counters in the fake
  server.
- [x] Exercise real connector-backed `put`, `exists`, `get`, and `list`
  counters in the default fake CI suite without synthesizing LMCache hits. The
  fake serving connector path exercises actual `put`, `exists`, and `get`;
  focused connector metrics and plugin roundtrip coverage exercise `list`.
- [x] Produce fake latency fields and label non-streaming TTFT as unavailable
  or estimated.
- [x] Simulate repeated-prefix cache hits with reduced fake latency.
- [x] Produce a report with the same schema shape as real reports.
- [x] Include HTTP failure-path coverage for fake serving errors.
- [x] Add ContextStorm `serve_fake_small_ci.yaml` fake serving scenario.
- [x] Refactor fake serving around a cache-backend protocol.
- [x] Keep `NoCacheBackend` and `LocalMemoryCacheBackend` for harness-only
  fake cache coverage.
- [x] Add `BifrostLMCacheBackend` that uses the real Phase 5
  `BifrostRemoteConnector` with fake Phase 5 `CacheEngineKey` and `MemoryObj`
  types.
- [x] Make the default fake CI candidate start local `bifrost-daemon`, execute
  actual connector `put`, `exists`, and `get`, collect connector JSONL, collect
  BIFROST stats, and run fsck.
- [x] Label connector metrics as `actual_bifrost_remote_connector` and timing
  as `synthetic_fake_server`.
- [x] Provide a one-command fake serving demo that runs workload generation,
  fake no-cache and BIFROST connector-backed fake benchmarks, comparison, and
  report generation without GPU, vLLM, real LMCache import, model downloads, or
  internet access.

## Optional real vLLM workload

- [x] Require explicit opt-in.
- [x] Require local model path.
- [x] Skip when vLLM bench CLI is unavailable.
- [x] Add ContextStorm opt-in real serving scenario skipped by default.
- [x] Add optional real vLLM + LMCache + BIFROST demo scaffold skipped by
  default.
- [x] Skip when LMCache is unavailable.
- [x] Skip when required GPU serving resources are unavailable.
- [x] Scaffold vLLM-only baseline as an explicitly requested demo mode.
- [x] Scaffold vLLM plus LMCache local or CPU storage baseline in the
  first-class matrix.
- [x] Add guarded real matrix execution path for vLLM plus LMCache local or CPU
  storage baseline.
- [x] Run vLLM plus LMCache local or CPU storage baseline on a real ready host.
- [x] Scaffold vLLM plus LMCache plus BIFROST variant behind real-run opt-in.
- [x] Add guarded real matrix execution path for vLLM plus LMCache plus BIFROST.
- [x] Capture BIFROST observations in the optional demo when the run is
  executed.
- [x] Report all skip and failure reasons.

## Optional two-instance cache-sharing experiment

- [x] Gate the experiment so it runs only after the single-instance benchmark
  is correct.
- [x] Require explicit opt-in.
- [x] Use LMCache remote storage through BIFROST, not raw vLLM KVTransfer.
- [x] Add ContextStorm opt-in two-instance scenario marker skipped by default.
- [x] Add optional two-instance demo scaffold skipped by default.
- [x] Add example config with separate ports and shared BIFROST endpoint.
- [x] Start two serving instances with separate ports and clear run labels when
  explicitly opted in.
- [x] Warm one instance and measure reuse from the second when supported.
- [x] Report whether observed reuse is real, absent, or inconclusive.
- [x] Preserve correctness and fsck checks.

## Local commands

These commands assume the local packages are installed the same way CI installs
them:

```bash
python -m pip install \
  -e "bifrost_py[dev]" \
  -e "contextstorm[dev]" \
  -e "integrations/lmcache_bifrost[dev]"
```

Environment doctor:

```bash
python -m bifrost_serving.env_doctor \
  --endpoint 127.0.0.1:7420 \
  --output-json runs/phase6-env-doctor/report.json \
  --json
```

Fake serving demo:

```bash
python examples/serving_benchmark/fake_serving_demo.py \
  --output-dir runs/phase6-serving/fake-demo \
  --request-count 8 \
  --concurrency 2 \
  --json
```

ContextStorm fake serving scenario:

```bash
contextstorm run contextstorm/scenarios/serve_fake_small_ci.yaml \
  --runs-root runs/contextstorm \
  --run-id phase6-serve-fake-small
```

Focused real-connector fake CI regression:

```bash
pytest -q tests/test_phase6_real_connector_fake_ci.py
```

Guarded real matrix dry-run:

```bash
python tools/bifrost_run_phase6_real_matrix.py \
  --config examples/serving_benchmark/phase6_real_matrix.yaml \
  --dry-run \
  --json
```

Optional guarded real matrix execution:

```bash
BIFROST_RUN_REAL_VLLM=1 \
python tools/bifrost_run_phase6_real_matrix.py \
  --config examples/serving_benchmark/phase6_real_matrix.yaml \
  --model /path/to/already-local-model \
  --allow-real-vllm \
  --json
```

Optional real vLLM plus LMCache plus BIFROST demo:

```bash
BIFROST_RUN_REAL_VLLM=1 \
python examples/serving_benchmark/vllm_lmcache_bifrost_demo.py \
  --mode run \
  --output-dir runs/phase6-serving/real-vllm-lmcache-bifrost \
  --model /path/to/already-local-model \
  --allow-real-vllm \
  --json
```

Optional two-instance cache-sharing experiment:

```bash
BIFROST_RUN_TWO_INSTANCE_CACHE_SHARE=1 BIFROST_RUN_REAL_VLLM=1 \
python examples/serving_benchmark/two_instance_cache_share_demo.py \
  --mode run \
  --output-dir runs/phase6-serving/two-instance-cache-share \
  --model /path/to/already-local-model \
  --allow-real-vllm \
  --json
```

Optional `vllm bench serve` integration:

```bash
BIFROST_RUN_VLLM_BENCH=1 \
python -m bifrost_serving.vllm_bench \
  --base-url http://127.0.0.1:8000 \
  --endpoint /v1/chat/completions \
  --result-dir runs/phase6-serving/vllm-bench \
  --backend openai-chat \
  --num-prompts 16 \
  --max-concurrency 2 \
  --allow-real-vllm-bench \
  --json
```

## CI

- [x] Run environment doctor in fake CI mode.
- [x] Run fake OpenAI-compatible serving client/server tests.
- [x] Run one-command fake serving demo tests.
- [x] Run optional real vLLM demo scaffold tests without starting vLLM.
- [x] Run optional two-instance scaffold tests without starting vLLM.
- [x] Run response correctness and equivalence utility tests.
- [x] Run report generation tests.
- [x] Run ContextStorm serving metrics and fake serving scenario tests.
- [x] Run config generation tests without importing vLLM or LMCache.
- [x] Run three-baseline matrix generation tests without importing vLLM or
  LMCache.
- [x] Run `.github/workflows/phase6.yml` with local editable installs for
  `bifrost_py`, `contextstorm`, and `integrations/lmcache_bifrost`.
- [x] Build Rust binaries before serving harness checks.
- [x] Run `cargo test --manifest-path bifrostd/Cargo.toml`.
- [x] Run `pytest bifrost_py/tests tests`.
- [x] Run `pytest integrations/lmcache_bifrost/tests`.
- [x] Run `pytest contextstorm/tests`.
- [x] Run the fake serving demo command in CI.
- [x] Run `contextstorm/scenarios/serve_fake_small_ci.yaml` in CI.
- [x] Keep real LMCache tests skipped unless explicitly enabled with
  `BIFROST_RUN_REAL_LMCACHE_TESTS=1`.
- [x] Keep real vLLM tests skipped unless explicitly enabled.
- [x] Keep `vLLM bench serve` tests skipped unless explicitly enabled with
  `BIFROST_RUN_VLLM_BENCH=1`.
- [x] Keep GPU tests skipped unless explicitly enabled.
- [x] Require no Hugging Face tokens, model downloads, Docker, root, internet,
  CUDA, or external services in default test, demo, and scenario paths.
- [x] Preserve Phase 1 parity tests.
- [x] Preserve Phase 2 transport tests.
- [x] Preserve Phase 3 store tests.
- [x] Preserve Phase 4 tiny-transformer correctness tests.
- [x] Preserve Phase 5 LMCache connector tests.

## Phase 6 done criteria

- [x] Environment doctor reports readiness levels and skip reasons.
- [x] Config generator covers all three baseline variants.
- [x] Fake serving workload runs in CI and emits a report.
- [x] Benchmark runner captures raw metrics and baseline separation.
- [x] Metrics collectors include vLLM JSON, LMCache metrics when available,
  BIFROST connector counters, store stats, bytes, and fsck.
- [x] Correctness checks are strict when deterministic and advisory or skipped
  with reasons when not.
- [x] Report generator includes environment details, failures, skipped
  components, raw metrics, configs, and store health.
- [x] Optional real vLLM plus LMCache plus BIFROST run is documented, opt-in,
  and skipped by default.
- [x] Optional two-instance cache-sharing experiment is opt-in and clearly
  labeled.
- [x] No raw vLLM KVTransfer, SGLang, Kubernetes, dashboard, compression,
  QUIC, RDMA, production auth, custom CUDA, or scheduler logic was introduced
  during Phase 6.
- [x] CI remains CPU-only, local, deterministic, and free of external service
  requirements by default.
- [x] Phase 1 through Phase 5 required tests remain green.

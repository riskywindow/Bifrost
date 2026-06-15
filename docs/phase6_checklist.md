# Phase 6 Checklist

Last verified: 2026-06-15

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

- [ ] Generate vLLM-only baseline config.
- [ ] Generate vLLM plus LMCache local or CPU storage baseline config.
- [x] Generate vLLM plus LMCache plus BIFROST remote storage config.
- [ ] Generate BIFROST daemon config or command metadata.
- [x] Record effective environment variables for each variant.
- [x] Emit version-sensitive warnings.
- [x] Disable pickle fallback for real LMCache traffic.
- [x] Keep model paths local and never trigger downloads.
- [x] Save generated configs as report artifacts.

## Process orchestrator

- [x] Start and stop local `bifrostd` for BIFROST-backed runs.
- [x] Start and stop vLLM serving processes only when explicitly opted in.
- [ ] Support LMCache in-process mode.
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

- [ ] Run baselines separately.
- [x] Preserve raw metrics for each baseline.
- [ ] Refuse to compare missing or incompatible baselines as speedups.
- [ ] Support warmup and measured request phases.
- [x] Record request count, concurrency, max output tokens, and timeout.
- [x] Capture request failures and timeouts.
- [x] Preserve generated configs with the run output.

## Metrics collectors

- [ ] Ingest `vllm bench serve` JSON when available.
- [x] Capture HTTP request start, response end, status code, and error fields
  for fake OpenAI-compatible serving requests.
- [ ] Capture real serving TTFT.
- [x] Capture p50 and p95 end-to-end latency.
- [ ] Capture output token latency.
- [x] Capture requests per second.
- [x] Capture error rate.
- [ ] Capture BIFROST connector `put`, `get`, `exists`, and `list` counts.
- [ ] Capture BIFROST connector error counts.
- [x] Capture BIFROST bytes stored through daemon stats when available.
- [ ] Capture LMCache hit/miss metrics when available.
- [x] Capture BIFROST store stats before and after BIFROST-backed runs.
- [ ] Capture fsck status or skip reason.

## Report generator

- [ ] Include commit, dirty-tree status, and repository path.
- [ ] Include Python, torch, vLLM, LMCache, connector, bifrostd, CUDA, and
  driver versions when available.
- [ ] Include hardware and GPU details when available.
- [ ] Include model path or identifier and local-asset statement.
- [ ] Include workload definition and request counts.
- [ ] Include all generated configs.
- [ ] Include raw metrics file paths.
- [ ] Include baseline comparison tables.
- [ ] Include failures and skipped components.
- [ ] Include environment readiness level.
- [ ] Avoid speedup claims unless measured.

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
- [ ] Exercise connector-like `put`, `exists`, `get`, and `list` counters.
- [x] Produce fake latency fields and label non-streaming TTFT as unavailable
  or estimated.
- [x] Simulate repeated-prefix cache hits with reduced fake latency.
- [x] Produce a report with the same schema shape as real reports.
- [x] Include HTTP failure-path coverage for fake serving errors.

## Optional real vLLM workload

- [x] Require explicit opt-in.
- [x] Require local model path.
- [ ] Skip when vLLM is unavailable.
- [ ] Skip when LMCache is unavailable.
- [ ] Skip when required GPU serving resources are unavailable.
- [ ] Run vLLM-only baseline.
- [ ] Run vLLM plus LMCache local or CPU storage baseline.
- [ ] Run vLLM plus LMCache plus BIFROST variant.
- [ ] Capture LMCache and BIFROST observations.
- [ ] Report all skip and failure reasons.

## Optional two-instance cache-sharing experiment

- [ ] Run only after single-instance benchmark is correct.
- [ ] Require explicit opt-in.
- [ ] Use LMCache remote storage through BIFROST, not raw vLLM KVTransfer.
- [ ] Start two serving instances with separate ports and clear run labels.
- [ ] Warm one instance and measure reuse from the second when supported.
- [ ] Report whether observed reuse is real, absent, or inconclusive.
- [ ] Preserve correctness and fsck checks.

## CI

- [x] Run environment doctor in fake CI mode.
- [x] Run fake OpenAI-compatible serving client/server tests.
- [x] Run report generation tests.
- [x] Run config generation tests without importing vLLM or LMCache.
- [ ] Keep real LMCache tests skipped unless explicitly enabled.
- [ ] Keep real vLLM tests skipped unless explicitly enabled.
- [ ] Keep GPU tests skipped unless explicitly enabled.
- [ ] Require no Hugging Face tokens, model downloads, Docker, root, internet,
  or external services.
- [ ] Preserve Phase 1 parity tests.
- [ ] Preserve Phase 2 transport tests.
- [ ] Preserve Phase 3 store tests.
- [ ] Preserve Phase 4 tiny-transformer correctness tests.
- [ ] Preserve Phase 5 LMCache connector tests.

## Phase 6 done criteria

- [ ] Environment doctor reports readiness levels and skip reasons.
- [ ] Config generator covers all three baseline variants.
- [ ] Fake serving workload runs in CI and emits a report.
- [ ] Benchmark runner captures raw metrics and baseline separation.
- [ ] Metrics collectors include vLLM JSON, LMCache metrics when available,
  BIFROST connector counters, store stats, bytes, and fsck.
- [ ] Correctness checks are strict when deterministic and advisory or skipped
  with reasons when not.
- [ ] Report generator includes environment details, failures, skipped
  components, raw metrics, configs, and store health.
- [ ] Optional real vLLM plus LMCache plus BIFROST run is documented, opt-in,
  and skipped by default.
- [ ] Optional two-instance cache-sharing experiment is opt-in and clearly
  labeled.
- [ ] No raw vLLM KVTransfer, SGLang, Kubernetes, dashboard, compression, QUIC,
  RDMA, production auth, custom CUDA, or scheduler logic is introduced.
- [ ] CI remains CPU-only, local, deterministic, and free of external service
  requirements by default.
- [ ] Phase 1 through Phase 5 required tests remain green.

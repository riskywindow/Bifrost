# Phase 6 Serving Benchmark

Last verified: 2026-06-15

## Objective

Phase 6 builds an end-to-end serving benchmark and reproducible report for:

```text
vLLM -> LMCache -> BIFROST remote storage connector -> bifrostd
```

The benchmark must show whether BIFROST can sit behind LMCache in a real vLLM
serving path, observe cache activity, preserve correctness, and report store
health. It is a measurement milestone, not a production deployment milestone.

The report must include TTFT, end-to-end latency, output token latency,
requests per second, error rate, connector activity, bytes stored and loaded,
LMCache hit/miss data when available, BIFROST store stats, fsck status,
environment details, failures, and skipped components.

## Why LMCache instead of raw vLLM KVTransfer

Phase 6 uses the Phase 5 LMCache remote storage integration because LMCache owns
the cache key, memory object, chunking, and engine rehydration semantics in
this path. BIFROST remains a verified remote storage backend for LMCache-owned
opaque bytes.

This boundary keeps the benchmark aligned with the correctness contract:

1. BIFROST stores LMCache payloads as `opaque_engine_blob`.
2. LMCache decides whether cached state is usable by vLLM.
3. BIFROST validates object identity, key hash, payload hash, committed store
   state, and retrieval integrity.
4. The benchmark does not depend on unstable vLLM internal KV tensor layout.

Raw vLLM KVTransfer integration remains out of scope for Phase 6.

## Baselines

Every real-serving report should compare three variants under the same
hardware, model, workload, request count, concurrency, sampling settings, and
vLLM core runtime settings:

1. `vllm_only`: no LMCache and no BIFROST remote storage.
2. `vllm_lmcache_local_cpu`: LMCache enabled with local CPU storage and no
   BIFROST remote backend.
3. `vllm_lmcache_bifrost`: LMCache remote storage configured to use the
   BIFROST connector and a local `bifrostd`, with local CPU storage disabled
   for the primary isolation experiment.

The matrix generator writes these variants together and validates that common
fields match. For the primary isolation matrix, vLLM automatic prefix caching
is explicitly disabled in every mode with the equivalent of
`--no-enable-prefix-caching`.

The report must not claim a speedup unless all compared variants ran
successfully and the metrics show it. Missing, failed, skipped, or
configuration-incompatible baselines must be reported as such.

## Guarded Real Matrix Executor

The guarded real-serving matrix executor lives in
`bifrost_py/bifrost_serving/real_matrix.py` and is available through:

```bash
python tools/bifrost_run_phase6_real_matrix.py \
  --config examples/serving_benchmark/phase6_real_matrix.yaml \
  --dry-run \
  --json
```

Dry-run mode expands the three real-serving modes, repetitions, rotated order,
ports, generated vLLM commands, LMCache configs, BIFROST connector configs,
workload hash, and artifact directories without importing or starting vLLM,
LMCache, GPU code, or model assets. Dry-run output is deliberately not a PASS:
the machine-readable completion gate has `passed: false` because no measured
real samples, correctness evidence, LMCache activity, BIFROST activity, or fsck
result exists.

Real execution is refused unless `--allow-real-vllm` is passed or
`BIFROST_RUN_REAL_VLLM=1` is set. Real execution is also refused in CI. Before
starting a server, the executor requires:

1. A visible GPU through `torch.cuda`.
2. Importable vLLM.
3. Importable LMCache.
4. Importable `lmcache_bifrost` and adapter.
5. A `bifrost-daemon` executable.
6. A local model path, unless model downloads are explicitly allowed.
7. Sufficient disk space.
8. All planned vLLM and BIFROST ports free.

The executor runs the same fairness matrix for every repetition:

1. `vllm_only`
2. `vllm_lmcache_local_cpu`
3. `vllm_lmcache_bifrost`

All modes share the same GPU selector, model, served model name, dtype,
maximum model length, sampling settings, output token count, concurrency,
request rate, workload bytes and SHA-256, vLLM prefix-caching setting, and core
vLLM flags. The LMCache local CPU and BIFROST modes use the same LMCache
connector mode.

For every mode and repetition, the executor writes a separate directory and
uses fresh process state, LMCache config state, BIFROST store root, metrics
files, and ports. It runs engine warmup, cache population, and measured phases
through the serving benchmark runner, then stops all processes and writes logs,
configs, raw requests, metrics snapshots, and an artifact manifest.

The root output includes:

1. `artifact_manifest.json`
2. `summary.json`
3. `comparison_report.json`
4. `sanitized_evidence_bundle.json`
5. `completion_gate.json`
6. One `rep_XX/<mode>/` directory per mode and repetition

The final gate passes only when every requested real mode completes, measured
phases have nonzero samples, required configs are captured, correctness
checking executes, LMCache local mode reports actual store and retrieve
activity, BIFROST mode reports actual connector or store activity, and BIFROST
fsck is clean. Skips and failures make the top-level status incomplete or
failed; fake or dry-run evidence can never satisfy the real-serving gate.

## Out of scope

Phase 6 does not build:

1. Raw vLLM KVTransfer integration.
2. SGLang integration.
3. Kubernetes deployment.
4. Dashboard UI.
5. Production authentication.
6. RDMA.
7. QUIC.
8. Compression.
9. Parity chunks or FEC.
10. Custom CUDA.
11. Scheduler or distributed routing logic.
12. Mandatory GPU CI.
13. Required model downloads or tokenizer downloads.

Real serving is optional and opt-in. Default CI must use fake serving workloads
that require no vLLM, LMCache, GPU, Hugging Face token, Docker, root, internet,
or external service.

## Done criteria

Phase 6 is done when:

1. The environment doctor reports readiness levels and precise skip reasons.
2. Config generation covers all three benchmark variants.
3. A fake serving workload runs in CI and produces a report.
4. Real vLLM plus LMCache plus BIFROST runs are documented, opt-in, and skipped
   by default.
5. The benchmark runner captures raw metrics for each baseline separately.
6. Reports include environment details, failures, skipped components, and store
   health.
7. Correctness checks are deterministic where possible and clearly marked
   advisory or skipped where serving nondeterminism prevents strict equality.
8. BIFROST connector and store metrics are included for BIFROST-backed runs.
9. The benchmark refuses to report unmeasured speedups.
10. Phase 1 through Phase 5 required tests remain green.

## OpenAI-compatible fake serving path

Phase 6 includes a lightweight OpenAI-compatible HTTP client and fake local
server for CI and benchmark-harness testing. This path validates request
generation, HTTP plumbing, concurrency, latency capture, error capture, and
cache-shaped metrics without importing vLLM, LMCache, torch, tokenizers, or any
model assets.

The client lives in `bifrost_py/bifrost_serving/http_client.py`. It supports:

1. Configurable `base_url`, endpoint, timeout, concurrency, headers, and model
   name.
2. `/v1/completions` and `/v1/chat/completions` request JSON generation from
   Phase 6 `ServingRequest` JSONL records.
3. OpenAI-compatible response parsing for completion `text` and chat
   `message.content`.
4. Per-request timing fields: `request_start`, `response_end`, `status_code`,
   `error`, end-to-end latency, and optional `first_token_time`.

Non-streaming requests are the default and are used by fake CI tests. In this
mode TTFT is unavailable because the client receives the response body only
after the server has completed the request. Reports over fake non-streaming
traffic must label TTFT as unavailable or estimated from end-to-end latency.
The client has simple Server-Sent Events parsing for compatible streaming
responses, but real vLLM streaming benchmark coverage remains opt-in future
work.

The fake server lives in `bifrost_py/bifrost_serving/fake_server.py` and can be
started with:

```bash
python tools/bifrost_fake_openai_server.py \
  --host 127.0.0.1 \
  --port 8000 \
  --simulate-cache true \
  --base-delay-ms 50 \
  --cache-hit-delay-ms 5 \
  --per-token-delay-ms 1
```

The fake server exposes:

1. `POST /v1/completions`
2. `POST /v1/chat/completions`
3. `GET /healthz`
4. `GET /metrics`

When cache simulation is enabled, the first request for a `metadata.prefix_id`
is counted as a miss and later requests for the same prefix are counted as
hits. The server delays misses by `--base-delay-ms`, hits by
`--cache-hit-delay-ms`, and adds `--per-token-delay-ms * max_tokens` to both.
This simulates repeated-prefix latency behavior for CI only; it is not a vLLM
or LMCache performance measurement.

## Serving benchmark runner

The benchmark runner lives in `bifrost_py/bifrost_serving/runner.py` and is
available through:

```bash
python tools/bifrost_run_serving_benchmark.py \
  --workload-jsonl runs/workload.jsonl \
  --base-url http://127.0.0.1:8000 \
  --endpoint /v1/completions \
  --backend fake \
  --concurrency 4 \
  --timeout-seconds 30 \
  --output-dir runs/phase6-serving/fake \
  --label fake-ci \
  --collect-bifrost-stats false \
  --json
```

The runner does not start `bifrostd`, LMCache, vLLM, or the fake server. It
sends a prepared workload to an already running OpenAI-compatible endpoint and
writes one run directory per baseline or variant.

Supported inputs:

1. `--workload-jsonl PATH`
2. `--base-url URL`
3. `--endpoint PATH`, default `/v1/completions`
4. `--backend openai-compatible|fake`
5. `--concurrency N`
6. `--request-rate N`, optional submit pacing in requests per second
7. `--timeout-seconds N`
8. `--output-dir PATH`
9. `--label LABEL`
10. `--headers KEY=VALUE`, repeatable
11. `--bifrost-endpoint HOST:PORT`, optional
12. `--collect-bifrost-stats true|false`
13. `--json`

Artifacts written:

1. `raw_requests.jsonl`: one record per request, including request ID,
   metadata, HTTP status, latency, optional TTFT, output token count when
   parseable, error text, and response JSON.
2. `summary.json`: run-level metrics, workload summary, environment doctor
   output, optional BIFROST stats before and after, optional fake backend
   metrics before and after, and artifact paths.
3. `config.json`: effective runner configuration.
4. `workload.jsonl`: copied workload input.

`config.json` preserves header names for reproducibility but redacts likely
secret values such as authorization, token, API key, secret, and cookie
headers.

When `--collect-bifrost-stats true` is used, the runner reads daemon store
stats through the existing Python BIFROST client before and after the request
phase. Missing endpoints or unreachable daemons are recorded as skipped or
error status in `summary.json`; they do not create synthetic cache hits or
hide request failures.

## Process orchestrator

The process orchestrator lives in `bifrost_py/bifrost_serving/orchestrator.py`
with the CLI wrapper:

```bash
python tools/bifrost_orchestrate_serving.py \
  --scenario fake \
  --output-dir runs/phase6-serving/orchestrator-fake \
  --vllm-port 8000 \
  --dry-run \
  --json
```

Supported scenarios are:

1. `fake`: starts only the local OpenAI-compatible fake server and probes
   `/healthz`.
2. `vllm-only`: plans or starts a guarded `vllm serve` process.
3. `lmcache-local`: plans or starts a guarded vLLM process with local LMCache
   mode metadata.
4. `vllm-lmcache-bifrost`: plans or starts `bifrost-daemon`, optional
   `python -m lmcache.server`, and guarded `vllm serve`.

The orchestrator is a lifecycle tool. It starts processes, waits for readiness,
writes logs and manifests under `--output-dir`, and then stops processes before
returning. It does not run the serving workload itself; use
`tools/bifrost_run_serving_benchmark.py` against an already running endpoint
for request metrics.

Safety guards:

1. Real vLLM scenarios refuse to start unless `--allow-real-vllm` is passed or
   `BIFROST_RUN_REAL_VLLM=1` is set.
2. Non-local model values are rejected unless
   `BIFROST_ALLOW_MODEL_DOWNLOADS=1` is set.
3. Real serving scenarios are refused in CI; only `--scenario fake` is allowed.
4. Logs are written under the run output directory.
5. Processes are terminated on readiness failures and normal completion.
6. No root, Docker, internet, GPU, vLLM, LMCache, or model asset is required
   for the fake scenario or its tests.

Dry run mode writes `orchestrator_manifest.json` with the exact commands that
would be used and starts nothing. Non-dry-run mode also writes
`orchestrator_final_status.json` after cleanup.

Warmup, cache population, and measured phase splitting is implemented in the
serving benchmark runner and is used by the guarded real matrix executor.

## Optional Two-Instance Cache-Sharing Experiment

The optional two-instance scaffold lives in
`examples/serving_benchmark/two_instance_cache_share_demo.py`. It is designed
to show whether two serving instances can share LMCache remote storage through
the same BIFROST daemon when a local real-serving environment supports it:

```text
instance A: vLLM -> LMCache -> BIFROST remote storage connector -> bifrostd
instance B: vLLM -> LMCache -> BIFROST remote storage connector -> same bifrostd
```

This experiment is not a Phase 6 hard requirement. It remains optional,
exploratory, and skipped by default. It does not implement or use a raw vLLM
KVTransfer connector.

CI-safe dry-run:

```bash
python examples/serving_benchmark/two_instance_cache_share_demo.py \
  --mode dry-run \
  --output-dir runs/phase6-serving/two-instance \
  --model /path/to/local/model \
  --json
```

Dry-run starts no services and prints the planned commands for instance A and
instance B, expected vLLM and LMCache ports, the BIFROST endpoint, workload
paths, and output paths. Readiness mode also starts nothing:

```bash
python examples/serving_benchmark/two_instance_cache_share_demo.py \
  --mode readiness \
  --output-dir runs/phase6-serving/two-instance \
  --model /path/to/local/model \
  --json
```

Run mode refuses to continue unless `--allow-real-vllm` is passed or
`BIFROST_RUN_REAL_VLLM=1` is set. It also refuses real execution in CI, and it
requires a local model path unless model downloads are explicitly allowed by
the operator.

The planned real experiment is sequential so it does not require multi-GPU:

1. Start a shared `bifrostd`.
2. Start instance A with LMCache configured for BIFROST remote storage.
3. Send repeated-prefix requests to instance A to populate remote cache state.
4. Collect BIFROST store stats after instance A.
5. Stop instance A serving processes while keeping the shared `bifrostd`.
6. Start instance B with LMCache configured for the same BIFROST endpoint.
7. Send the same repeated-prefix workload to instance B.
8. Collect BIFROST store stats after instance B.

The summary compares BIFROST object count after instance A, BIFROST GET
activity during instance B, and p50 latency difference when the serving client
can measure it. Positive BIFROST GET activity during instance B is evidence
that LMCache attempted remote reuse. Missing GET activity, failed readiness,
or inconclusive latency must be reported directly and must not be presented as
a speedup.

The checked-in example config is
`examples/serving_benchmark/configs/two_instance_cache_share_example.yaml`.
It documents the shared BIFROST endpoint, separate serving ports, LMCache
remote storage plugin shape, repeated-prefix workloads, and advisory
comparison fields.

## One-command fake serving demo

The CI-safe fake serving demo lives in
`examples/serving_benchmark/fake_serving_demo.py`:

```bash
python examples/serving_benchmark/fake_serving_demo.py \
  --output-dir runs/phase6-serving/fake-demo \
  --request-count 8 \
  --concurrency 2
```

Use `--json` to print a machine-readable summary. The demo generates a small
repeated-prefix workload, runs the fake no-cache baseline, runs the fake
cache-simulating candidate, compares their metrics, writes a Phase 6 report,
and prints PASS or FAIL with run directories, p50 and p95 latency, simulated
cache-hit effect, correctness status, and report path.

This demo is a harness test only. It proves that fake CI can exercise workload
generation, local OpenAI-compatible serving, benchmark execution, baseline
comparison, report generation, and advisory correctness reporting. It does not
prove real vLLM, LMCache, or BIFROST serving speedup.

## Optional `vllm bench serve` runner

Phase 6 includes an optional wrapper around the upstream vLLM benchmark client:

```bash
python tools/bifrost_run_vllm_bench_serve.py \
  --base-url http://127.0.0.1:8000 \
  --endpoint /v1/completions \
  --backend openai \
  --result-dir runs/phase6-serving/vllm-bench \
  --num-prompts 32 \
  --request-rate 4 \
  --max-concurrency 8 \
  --metadata mode=vllm-only \
  --dry-run \
  --json
```

The wrapper lives in `bifrost_py/bifrost_serving/vllm_bench.py`. It detects:

1. Whether the `vllm` CLI is on `PATH`.
2. Whether `vllm bench serve --help` runs successfully.
3. The vLLM CLI version when `vllm --version` reports one.
4. The option names exposed by the installed `vllm bench serve` help text.

Real execution is refused unless `--allow-real-vllm-bench` is passed or
`BIFROST_RUN_VLLM_BENCH=1` is set. Dry runs and unavailable vLLM environments
do not start a server, import vLLM, use GPU, download models, contact Hugging
Face, or require LMCache.

The command builder is help-text aware. It adds options such as `--backend`,
`--base-url`, `--endpoint`, `--dataset-path`, `--dataset-name`,
`--num-prompts`, `--request-rate`, `--max-concurrency`, `--save-result`,
`--save-detailed`, `--result-dir`, `--result-filename`, and `--metadata` only
when the installed vLLM help text advertises those flags. Unsupported options
are recorded as warnings in `bifrost_vllm_bench_command.json`.

Dataset handling is version-sensitive:

1. If `--dataset-path` is provided and supported, the wrapper passes it through.
2. If no dataset path is provided and the help text advertises a `random`
   dataset, the wrapper uses `--dataset-name random`.
3. If random datasets are not advertised but `--dataset-path` is supported,
   the wrapper writes a small local synthetic ShareGPT-shaped dataset under the
   result directory and passes that path.
4. If no compatible dataset option is detected, the command is still recorded
   with a warning so the run can be inspected instead of silently inventing an
   unsupported invocation.

Artifacts:

1. `bifrost_vllm_bench_command.json`: availability, detected options, command,
   warnings, expected result path, and synthetic dataset path when generated.
2. `bifrost_vllm_bench_summary.json`: dry-run, skipped, completed, or failed
   status plus parsed vLLM result data when available.
3. The raw vLLM bench JSON file, usually the configured
   `--result-filename`, is left in the result directory.

This wrapper is a benchmark client integration only. It does not implement raw
vLLM KVTransfer, does not start vLLM serving, and does not bypass LMCache for
BIFROST-backed Phase 6 runs.

## Optional real vLLM + LMCache + BIFROST demo

An opt-in real-serving demo scaffold lives in
`examples/serving_benchmark/vllm_lmcache_bifrost_demo.py` with usage notes in
`examples/serving_benchmark/vllm_lmcache_bifrost_README.md`.

Safe dry-run mode:

```bash
python examples/serving_benchmark/vllm_lmcache_bifrost_demo.py \
  --mode dry-run \
  --output-dir runs/phase6-serving/real-demo \
  --model /path/to/local/model
```

Readiness-only mode:

```bash
python examples/serving_benchmark/vllm_lmcache_bifrost_demo.py \
  --mode readiness \
  --output-dir runs/phase6-serving/real-demo \
  --model /path/to/local/model \
  --json
```

Real run mode is refused unless `--allow-real-vllm` is passed or
`BIFROST_RUN_REAL_VLLM=1` is set. It is also refused in CI. The readiness
report checks vLLM import or CLI availability, LMCache import,
`lmcache_bifrost` import, BIFROST daemon reachability, GPU availability, local
model path status, required ports, and Hugging Face token presence as advisory
only.

When run, the demo generates a repeated-prefix workload, writes effective
LMCache/BIFROST configs, starts the BIFROST-backed serving stack, runs the
serving benchmark, captures BIFROST stats before and after, writes comparison
and report artifacts, prints latency fields, reports BIFROST object-count
delta, and lists skipped baselines. The vLLM-only baseline is run only when
`--include-vllm-only-baseline` is requested. The LMCache local/CPU baseline is
reported as skipped by this single-stack demo.

Checked-in example configuration files live under
`examples/serving_benchmark/configs/`:

1. `one_gpu_inprocess_example.yaml`
2. `mp_mode_example.yaml`

These files are examples only. LMCache and vLLM configuration names are
version-sensitive and must be verified against the installed packages before a
real run.

## Baseline comparison runner

The baseline comparison runner lives in `bifrost_py/bifrost_serving/compare.py`
and is available through:

```bash
python tools/bifrost_compare_serving_baselines.py \
  --workload-jsonl runs/workload.jsonl \
  --output-dir runs/phase6-serving/compare \
  --modes fake_no_cache \
  --modes fake_with_cache \
  --concurrency 4 \
  --json
```

Supported mode labels are:

1. `fake_no_cache`
2. `fake_with_cache`
3. `vllm_only`
4. `vllm_lmcache_local_cpu`
5. `vllm_lmcache_bifrost`

The fake modes start isolated local fake OpenAI-compatible servers, run the
existing serving benchmark runner against them, stop the servers, and preserve
one run directory per mode. `fake_with_cache` enables repeated-prefix cache
simulation; `fake_no_cache` does not.

The real vLLM modes are opt-in. Unless `--allow-real-vllm` is passed, they are
recorded as skipped in the comparison summary instead of failed. With
`--allow-real-vllm`, the comparison runner assumes a compatible real serving
endpoint is already reachable at `http://127.0.0.1:8000`; use the orchestrator
or an equivalent manual setup to start the real stack before running the
comparison. The `vllm_lmcache_bifrost` mode may collect BIFROST daemon stats
when `--bifrost-endpoint HOST:PORT` is supplied.

CLI inputs:

1. `--workload-jsonl PATH`
2. `--output-dir PATH`
3. `--modes MODE`, repeatable
4. `--concurrency N`
5. `--request-rate N`, optional
6. `--bifrost-endpoint HOST:PORT`, optional
7. `--model MODEL_OR_PATH`, required only for opted-in real vLLM modes
8. `--allow-real-vllm`
9. `--json`

Artifacts written:

1. `comparison_summary.json`: machine-readable mode results, skipped reasons,
   comparison deltas, cache-activity observations, notes, and artifact paths.
2. `comparison_summary.md`: basic Markdown summary for quick inspection.
3. `<mode>/summary.json`, `<mode>/raw_requests.jsonl`, `<mode>/config.json`,
   and `<mode>/workload.jsonl` for completed modes.
4. `<mode>/mode_result.json` for every requested mode, including skipped real
   modes.

The comparison uses the first completed mode as the baseline and compares every
requested mode against it. Missing, skipped, failed, or incomplete modes do not
become speedup evidence. The summary reports p50 latency deltas, p50 TTFT
deltas when available, error-rate deltas, BIFROST stats deltas, whether cache
activity was observed, notes, and skipped reasons.

# Phase 6 vLLM and LMCache Configuration

Last verified: 2026-06-15

## Purpose

Phase 6 configuration tooling should generate explicit files for each benchmark
variant so results are reproducible and comparable. Generated files are
benchmark artifacts and must be included in the final report.

Config generation must be version-sensitive. vLLM and LMCache have changed
configuration names and plugin loading behavior across releases, so the
generator should print warnings when it cannot confirm that a field is valid
for the installed versions.

## Generated config files

The current generator lives in `bifrost_py/bifrost_serving/config_gen.py` with
the CLI wrapper `tools/bifrost_generate_serving_config.py`.

Example:

```bash
python tools/bifrost_generate_serving_config.py \
  --endpoint 127.0.0.1:7744 \
  --model /path/to/local/model \
  --mode lmcache-inprocess \
  --output-dir runs/phase6-config \
  --port 8000 \
  --lmcache-port 9000 \
  --chunk-size 262144
```

Supported modes are:

1. `fake`, for CI-safe artifact generation.
2. `lmcache-inprocess`, for a vLLM process using LMCache in-process.
3. `lmcache-mp`, for version-specific LMCache multiprocess experiments.
4. `bifrost-remote-storage`, for explicitly documenting the remote storage
   plugin shape.
5. `vllm-bench-serve`, for benchmark-client command scaffolding.

The CLI writes:

```text
README.md
bifrost_lmcache_inprocess.yaml
bifrost_lmcache_mp.yaml
serving.env
vllm_serve_bifrost_lmcache.sh
lmcache_server_bifrost.sh
vllm_bench_serve_bifrost_lmcache.sh
```

Checked-in examples live under `examples/serving_configs/`.

A benchmark run should record at least:

```text
environment.json
workload.json
vllm_only.env
vllm_lmcache_local.env
vllm_lmcache_bifrost.env
lmcache_local.yaml
lmcache_bifrost.yaml
bifrost_daemon.json
benchmark_plan.json
```

The exact filenames may evolve, but the report must retain the effective
configuration for each baseline.

The current generator focuses on the BIFROST-backed LMCache remote storage
variant plus guarded command scaffolds. vLLM-only and LMCache-local baseline
artifact generation remain tracked separately in the Phase 6 checklist.

## In-process LMCache mode

In-process LMCache mode is the simplest real-serving path when supported by the
installed LMCache and vLLM versions.

The generated config should define:

1. How vLLM enables LMCache.
2. LMCache local or CPU storage settings for the non-BIFROST baseline.
3. LMCache remote storage plugin settings for the BIFROST variant.
4. Cache size and eviction settings when exposed by LMCache.
5. Logging and metrics settings that do not alter cache semantics.

The benchmark should prefer in-process mode for initial real runs because it
reduces process orchestration complexity.

## LMCache multiprocess mode

LMCache multiprocess mode may be needed for some versions or deployment shapes.
When generated, the config should define:

1. LMCache controller or worker commands.
2. IPC or network endpoints.
3. Startup ordering and readiness probes.
4. Log file locations.
5. Shutdown behavior.
6. The same local-storage and BIFROST remote-storage variants used by
   in-process mode.

Multiprocess mode must remain optional until the single-process serving path is
understood for the installed versions.

## BIFROST remote storage settings

The BIFROST-backed LMCache config should include:

```text
remote storage plugin name
module path for lmcache_bifrost adapter
BifrostConnectorAdapter class name
BIFROST endpoint
operation timeout
strict validation mode
chunk size or payload limits when supported
metrics/logging output path
pickle fallback disabled for real LMCache traffic
object type: opaque_engine_blob
```

The config must never ask BIFROST to reinterpret LMCache tensors. LMCache
payloads remain engine-owned opaque bytes.

`allow_pickle_fallback` defaults to `false`. The generator rejects enabling it
for real LMCache modes; it may only be enabled in `fake` mode for local fake
object experiments.

## Script safety

Generated scripts are executable but guarded. They refuse to start unless the
operator sets one of:

```text
BIFROST_RUN_VLLM_SERVE=1
BIFROST_RUN_LMCACHE_SERVER=1
BIFROST_RUN_VLLM_BENCH=1
```

They do not embed private token values and do not set Hugging Face token
environment variables. If the configured model does not resolve to a local
path, vLLM-facing scripts refuse to continue unless the operator explicitly
sets `BIFROST_ALLOW_MODEL_DOWNLOADS=1`.

The comments in each script state that GPU/CUDA, vLLM, LMCache, connector
installation, and exact command-line flags are version-sensitive optional
requirements. Tests execute only the refusal paths and do not start vLLM,
LMCache, or `bifrostd`.

## Version-sensitive warnings

The generator should warn when:

1. vLLM or LMCache is absent.
2. The installed version is unknown or outside tested ranges.
3. Expected config fields are missing from public APIs.
4. Plugin loading names differ from repository examples.
5. LMCache metrics names are unavailable.
6. `vllm bench serve` arguments differ from the expected schema.
7. The selected model or serving flags imply GPU requirements.

Warnings should be included in the report. They should not fail fake CI.

## Optional vLLM bench serve integration

When available, `vllm bench serve` is the preferred benchmark client because it
already emits serving metrics in machine-readable form. Phase 6 should ingest
its JSON output when present.

The integration should capture:

1. Command line.
2. Request count, concurrency, prompt lengths, and output lengths.
3. TTFT.
4. End-to-end latency.
5. Inter-token or output-token latency.
6. Requests per second.
7. Error counts.

If `vllm bench serve` is unavailable or incompatible, the runner may use a
small local benchmark client against the vLLM OpenAI-compatible endpoint. The
report must state which client produced the metrics.

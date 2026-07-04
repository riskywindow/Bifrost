# Optional Real vLLM + LMCache + BIFROST Demo

This example is an opt-in Phase 6 serving scaffold for:

```text
vLLM -> LMCache -> BIFROST remote storage connector -> bifrostd
```

It does not implement raw vLLM KVTransfer. Dry-run and readiness modes are safe
for default CI because they do not start vLLM, LMCache, `bifrostd`, use a GPU,
download models, or require a Hugging Face token.

## Dry Run

```bash
python examples/serving_benchmark/vllm_lmcache_bifrost_demo.py \
  --mode dry-run \
  --output-dir runs/phase6-serving/real-demo \
  --model /path/to/local/model
```

Dry run writes a repeated-prefix workload, generated LMCache/BIFROST configs,
an environment readiness JSON file, and the commands that would be used.

## Readiness

```bash
python examples/serving_benchmark/vllm_lmcache_bifrost_demo.py \
  --mode readiness \
  --output-dir runs/phase6-serving/real-demo \
  --model /path/to/local/model \
  --json
```

Readiness reports vLLM import or CLI availability, LMCache import,
`lmcache_bifrost` import, `bifrostd` reachability, GPU visibility, local model
path status, port availability, and Hugging Face token presence as advisory.

## Run

Real execution is refused unless one of these is set:

```bash
--allow-real-vllm
BIFROST_RUN_REAL_VLLM=1
```

Example:

```bash
BIFROST_RUN_REAL_VLLM=1 \
python examples/serving_benchmark/vllm_lmcache_bifrost_demo.py \
  --mode run \
  --allow-real-vllm \
  --output-dir runs/phase6-serving/real-demo \
  --model /path/to/local/model \
  --bifrost-endpoint 127.0.0.1:7420 \
  --request-count 16 \
  --concurrency 2
```

The demo starts the BIFROST-backed stack, runs a repeated-prefix benchmark,
captures BIFROST stats before and after, writes comparison/report artifacts,
prints latency fields, reports skipped baselines, and stops child processes.
Use `--include-vllm-only-baseline` to also run a vLLM-only baseline. The
LMCache local/CPU baseline is intentionally reported as skipped by this
single-stack demo.

No report should be interpreted as a speedup claim unless the compared
baselines completed under the same model, hardware, runtime, workload, and
configuration.

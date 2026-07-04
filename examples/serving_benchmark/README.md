# Phase 6 Fake Serving Demo

This example runs the Phase 6 serving benchmark harness without vLLM, LMCache,
GPU hardware, model downloads, internet access, Docker, or root privileges.
It is intended for CI and local harness validation only.

```bash
python examples/serving_benchmark/fake_serving_demo.py \
  --output-dir runs/phase6-serving/fake-demo \
  --request-count 8 \
  --concurrency 2
```

For machine-readable output:

```bash
python examples/serving_benchmark/fake_serving_demo.py \
  --output-dir runs/phase6-serving/fake-demo \
  --json
```

The demo:

1. Generates a small repeated-prefix workload.
2. Starts and benchmarks a fake no-cache OpenAI-compatible server.
3. Starts and benchmarks a fake cache-simulating OpenAI-compatible server.
4. Compares the two fake runs.
5. Writes a Phase 6 serving report for the cache-simulating run.
6. Prints a concise PASS/FAIL summary with run directories, p50/p95 latency,
   simulated cache-hit effect, correctness status, and report path.

The fake cache simulation lowers response delay after a repeated prefix has
been seen. That proves the benchmark harness can observe and report a
cache-shaped latency effect. It does not prove any real vLLM, LMCache, or
BIFROST serving speedup.

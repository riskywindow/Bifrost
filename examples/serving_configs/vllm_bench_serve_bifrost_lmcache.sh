#!/usr/bin/env bash
set -euo pipefail

# Generated optional vLLM bench serve scaffold.
# Version-sensitive: `vllm bench serve` arguments vary by vLLM release.

if [[ "${BIFROST_RUN_VLLM_BENCH:-}" != "1" ]]; then
  echo "Refusing to run vLLM benchmark without BIFROST_RUN_VLLM_BENCH=1." >&2
  exit 2
fi

DEFAULT_MODEL='./local-model'
MODEL="${BIFROST_VLLM_MODEL:-${DEFAULT_MODEL}}"
PORT="${BIFROST_VLLM_PORT:-8000}"

if [[ ! -e "${MODEL}" && "${BIFROST_ALLOW_MODEL_DOWNLOADS:-}" != "1" ]]; then
  echo "Refusing to benchmark a non-local model without BIFROST_ALLOW_MODEL_DOWNLOADS=1: ${MODEL}" >&2
  exit 2
fi

exec vllm bench serve \
  --backend openai-chat \
  --base-url "http://127.0.0.1:${PORT}" \
  --model "${MODEL}" \
  --num-prompts "${BIFROST_BENCH_NUM_PROMPTS:-16}" \
  --request-rate "${BIFROST_BENCH_REQUEST_RATE:-1.0}"

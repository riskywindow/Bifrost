#!/usr/bin/env bash
set -euo pipefail

# Generated optional vLLM + LMCache + BIFROST serving scaffold.
# This may require GPU hardware, CUDA, vLLM, LMCache, lmcache_bifrost, a running
# bifrostd daemon, and a model already available locally.
# Version-sensitive: exact vLLM LMCache flags may vary by release.

if [[ "${BIFROST_RUN_VLLM_SERVE:-}" != "1" ]]; then
  cat >&2 <<'EOF'
Refusing to start vLLM. Set BIFROST_RUN_VLLM_SERVE=1 only after verifying:
- bifrostd is running at the configured BIFROST_ENDPOINT
- vLLM, LMCache, and lmcache_bifrost are installed
- the model path is local, or BIFROST_ALLOW_MODEL_DOWNLOADS=1 is explicitly set
- GPU/CUDA requirements for your vLLM version are satisfied

No private tokens are required or embedded by this script.
EOF
  exit 2
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_MODEL='./local-model'
MODEL="${BIFROST_VLLM_MODEL:-${DEFAULT_MODEL}}"
PORT="${BIFROST_VLLM_PORT:-8000}"
export BIFROST_ENDPOINT="${BIFROST_ENDPOINT:-127.0.0.1:7744}"
export LMCACHE_CONFIG_FILE="${LMCACHE_CONFIG_FILE:-${SCRIPT_DIR}/bifrost_lmcache_inprocess.yaml}"

if [[ ! -e "${MODEL}" && "${BIFROST_ALLOW_MODEL_DOWNLOADS:-}" != "1" ]]; then
  echo "Refusing to pass a non-local model to vLLM without BIFROST_ALLOW_MODEL_DOWNLOADS=1: ${MODEL}" >&2
  exit 2
fi

exec vllm serve "${MODEL}" \
  --host 127.0.0.1 \
  --port "${PORT}" \
  --enable-prefix-caching

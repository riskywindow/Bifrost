#!/usr/bin/env bash
set -euo pipefail

# Optional vLLM + LMCache + BIFROST smoke scaffold.
#
# This script is intentionally guarded. It may require GPU hardware, a local
# model, vLLM, LMCache, and environment-specific setup. It must not run in CI or
# default developer test paths unless explicitly requested.

if [[ "${BIFROST_RUN_VLLM_SMOKE:-}" != "1" ]]; then
  cat >&2 <<'EOF'
Refusing to run the optional vLLM smoke.

Set BIFROST_RUN_VLLM_SMOKE=1 after you have prepared:
- a running bifrostd daemon
- vLLM installed in this Python environment
- LMCache installed in this Python environment
- lmcache_bifrost installed editable or present in PYTHONPATH
- a local model path already available on disk

Expected daemon start command:
  cargo run --manifest-path bifrostd/Cargo.toml --bin bifrost-daemon -- \
    --listen 127.0.0.1:7744 \
    --spool /tmp/bifrost-vllm-lmcache-smoke/spool

Expected config file:
  examples/lmcache_bifrost/vllm_lmcache_bifrost_config.yaml

No private tokens are required or read by this script.
EOF
  exit 2
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CONFIG="${BIFROST_VLLM_LMCACHE_CONFIG:-${REPO_ROOT}/examples/lmcache_bifrost/vllm_lmcache_bifrost_config.yaml}"
ENDPOINT="${BIFROST_ENDPOINT:-127.0.0.1:7744}"
MODEL="${BIFROST_VLLM_MODEL:-}"

if [[ -z "${MODEL}" ]]; then
  echo "BIFROST_VLLM_MODEL must point to a local model path." >&2
  exit 2
fi

export PYTHONPATH="${REPO_ROOT}/bifrost_py:${REPO_ROOT}/integrations/lmcache_bifrost:${PYTHONPATH:-}"
export LMCACHE_CONFIG_FILE="${CONFIG}"

python "${REPO_ROOT}/examples/lmcache_bifrost/vllm_lmcache_smoke.py" \
  --endpoint "${ENDPOINT}" \
  --config "${CONFIG}" \
  --model "${MODEL}" \
  --run \
  --json

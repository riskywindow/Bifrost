#!/usr/bin/env bash
set -euo pipefail

# Generated optional LMCache multiprocess scaffold.
# Version-sensitive: LMCache server/controller command names vary by release.
# This script is documented, guarded, and may need local edits for your version.

if [[ "${BIFROST_RUN_LMCACHE_SERVER:-}" != "1" ]]; then
  cat >&2 <<'EOF'
Refusing to start an LMCache server. Set BIFROST_RUN_LMCACHE_SERVER=1 only
after verifying the LMCache multiprocess command for your installed version.
No private tokens are required or embedded by this script.
EOF
  exit 2
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export BIFROST_ENDPOINT="${BIFROST_ENDPOINT:-127.0.0.1:7744}"
export LMCACHE_CONFIG_FILE="${LMCACHE_CONFIG_FILE:-${SCRIPT_DIR}/bifrost_lmcache_mp.yaml}"
PORT="${BIFROST_LMCACHE_PORT:-9000}"

exec python -m lmcache.server \
  --host 127.0.0.1 \
  --port "${PORT}" \
  --config "${LMCACHE_CONFIG_FILE}"

#!/usr/bin/env python3
"""CLI wrapper for the Phase 7 vLLM KVTransfer API inspector."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
source_path = REPO_ROOT / "bifrost_py"
if str(source_path) not in sys.path:
    sys.path.insert(0, str(source_path))

from bifrost_vllm.api_inspector import main


if __name__ == "__main__":
    raise SystemExit(main())

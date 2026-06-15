#!/usr/bin/env python3
"""CLI wrapper for the Phase 6 fake OpenAI-compatible server."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
for path in (
    REPO_ROOT / "bifrost_py",
    REPO_ROOT / "integrations" / "lmcache_bifrost",
):
    text = str(path)
    if text not in sys.path:
        sys.path.insert(0, text)

from bifrost_serving.fake_server import main


if __name__ == "__main__":
    raise SystemExit(main())

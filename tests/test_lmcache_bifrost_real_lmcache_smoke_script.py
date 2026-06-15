from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "examples" / "lmcache_bifrost" / "real_lmcache_smoke.py"


def test_real_lmcache_smoke_compat_only_json_exits_zero() -> None:
    result = run_script("--compat-only", "--json")

    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    assert summary["status"] in ("pass", "compatibility only")
    assert isinstance(summary["lmcache_installed"], bool)
    assert isinstance(summary["imports"], dict)
    assert summary["connector_methods"]["exists"] is True
    assert summary["connector_methods"]["get"] is True
    assert summary["connector_methods"]["put"] is True
    assert summary["connector_methods"]["list"] is True
    assert summary["connector_methods"]["close"] is True


def test_real_lmcache_smoke_has_clear_compatibility_only_output() -> None:
    result = run_script("--compat-only")

    assert result.returncode == 0, result.stderr
    assert "status:" in result.stdout
    assert "lmcache installed:" in result.stdout
    assert "memory obj roundtrip:" in result.stdout


def run_script(*args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        [
            str(REPO_ROOT / "bifrost_py"),
            str(REPO_ROOT / "integrations" / "lmcache_bifrost"),
        ]
    )
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=REPO_ROOT,
        env=env,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

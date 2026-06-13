from __future__ import annotations

import json
import os
import subprocess
import sys
from math import ceil
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
BIFROST_PY = REPO_ROOT / "bifrost_py"
SCRIPT = REPO_ROOT / "examples" / "tiny_transformer" / "local_kv_roundtrip.py"


def run_script(*args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(BIFROST_PY)
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=REPO_ROOT,
        env=env,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def test_script_returns_pass_for_deterministic_prompt() -> None:
    result = run_script(
        "--prompt",
        "1 2 3 4 5",
        "--decode-tokens",
        "4",
        "--block-size",
        "2",
        "--seed",
        "1234",
        "--json",
    )

    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    assert summary["status"] == "pass"


def test_json_output_parses_and_reports_roundtrip_correctness() -> None:
    result = run_script(
        "--prompt",
        "1 2 3 4 5",
        "--decode-tokens",
        "4",
        "--block-size",
        "2",
        "--json",
    )

    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)

    assert summary["prompt_tokens"] == [1, 2, 3, 4, 5]
    assert summary["continuation_match"] is True
    assert summary["baseline_continuation"] == summary["rehydrated_continuation"]
    assert summary["logit_max_abs_error"] <= 1e-6
    assert summary["page_count"] == summary["layer_count"] * ceil(5 / 2)
    assert len(summary["object_ids"]) == summary["page_count"]
    assert len(set(summary["object_ids"])) == summary["page_count"]


def test_script_exits_nonzero_on_invalid_prompt_tokens() -> None:
    result = run_script(
        "--prompt",
        "1 nope 3",
        "--decode-tokens",
        "2",
        "--block-size",
        "2",
    )

    assert result.returncode != 0
    assert "invalid integer token" in result.stderr

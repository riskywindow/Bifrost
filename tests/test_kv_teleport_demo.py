from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
BIFROST_PY = REPO_ROOT / "bifrost_py"
WORKER_PREFILL = REPO_ROOT / "examples" / "tiny_transformer" / "worker_prefill.py"
WORKER_DECODE = REPO_ROOT / "examples" / "tiny_transformer" / "worker_decode.py"
DEMO = REPO_ROOT / "examples" / "tiny_transformer" / "kv_teleport_demo.py"

from test_store_kv_roundtrip import Daemon, _find_binary, _free_port


@pytest.fixture()
def binaries() -> dict[str, Path]:
    paths = {
        "daemon": _find_binary("bifrost-daemon"),
        "xfer": _find_binary("bifrost-xfer"),
        "store": _find_binary("bifrost-store"),
    }
    missing = [name for name, path in paths.items() if path is None]
    if missing:
        pytest.skip(
            "Rust binaries unavailable; run "
            "`cargo build --manifest-path bifrostd/Cargo.toml --bins`"
        )
    return {name: path for name, path in paths.items() if path is not None}


@pytest.fixture()
def daemon(tmp_path: Path, binaries: dict[str, Path]):
    daemon = Daemon(
        binary=binaries["daemon"],
        endpoint=f"127.0.0.1:{_free_port()}",
        spool=tmp_path / "spool",
    )
    daemon.start()
    try:
        yield daemon
    finally:
        daemon.stop()


def test_worker_prefill_handoff_then_worker_decode_matches_baseline(
    tmp_path: Path,
    daemon: Daemon,
    binaries: dict[str, Path],
) -> None:
    handoff = tmp_path / "handoff.json"
    prefill = _run_json(
        [
            str(WORKER_PREFILL),
            "--endpoint",
            daemon.endpoint,
            "--prompt",
            "1 2 3 4 5",
            "--block-size",
            "2",
            "--seed",
            "1234",
            "--handoff",
            str(handoff),
            "--json",
        ],
        binaries,
    )

    assert prefill["status"] == "pass"
    assert handoff.is_file()
    assert prefill["manifest_completeness"] == "complete"
    assert prefill["page_count"] == 6

    decode = _run_json(
        [
            str(WORKER_DECODE),
            "--endpoint",
            daemon.endpoint,
            "--handoff",
            str(handoff),
            "--decode-tokens",
            "4",
            "--verify-baseline",
            "--json",
        ],
        binaries,
    )

    assert decode["status"] == "pass"
    assert decode["manifest_completeness"] == "complete"
    assert decode["baseline_continuation"] == decode["bifrost_continuation"]
    assert decode["continuation_match"] is True
    assert decode["logit_max_abs_error"] <= 1e-6


def test_kv_teleport_demo_returns_pass(
    tmp_path: Path,
    daemon: Daemon,
    binaries: dict[str, Path],
) -> None:
    summary = _run_json(
        [
            str(DEMO),
            "--endpoint",
            daemon.endpoint,
            "--prompt",
            "1 2 3 4 5",
            "--decode-tokens",
            "4",
            "--block-size",
            "2",
            "--seed",
            "1234",
            "--work-dir",
            str(tmp_path / "demo"),
            "--json",
        ],
        binaries,
    )

    assert summary["result"] == "pass"
    assert summary["manifest_complete"] is True
    assert summary["baseline_continuation"] == summary["bifrost_continuation"]
    assert summary["greedy_tokens_match"] is True
    assert summary["logit_max_abs_error"] <= 1e-6


def test_missing_handoff_fails_clearly(
    tmp_path: Path,
    daemon: Daemon,
    binaries: dict[str, Path],
) -> None:
    result = _run_json(
        [
            str(WORKER_DECODE),
            "--endpoint",
            daemon.endpoint,
            "--handoff",
            str(tmp_path / "missing.json"),
            "--decode-tokens",
            "1",
            "--verify-baseline",
            "--json",
        ],
        binaries,
        check=False,
    )

    assert result.returncode == 2
    assert result.value["status"] == "fail"
    assert "handoff file not found" in result.value["error"]


def test_missing_page_fails_before_rehydration(
    tmp_path: Path,
    daemon: Daemon,
    binaries: dict[str, Path],
) -> None:
    handoff = tmp_path / "handoff.json"
    _run_json(
        [
            str(WORKER_PREFILL),
            "--endpoint",
            daemon.endpoint,
            "--prompt",
            "1 2 3 4 5",
            "--block-size",
            "2",
            "--seed",
            "1234",
            "--handoff",
            str(handoff),
            "--json",
        ],
        binaries,
    )
    handoff_doc = json.loads(handoff.read_text(encoding="utf-8"))
    handoff_doc["object_ids"][0] = "bifrost://object/blake3/" + "9" * 64
    handoff.write_text(json.dumps(handoff_doc, sort_keys=True) + "\n", encoding="utf-8")

    result = _run_json(
        [
            str(WORKER_DECODE),
            "--endpoint",
            daemon.endpoint,
            "--handoff",
            str(handoff),
            "--decode-tokens",
            "4",
            "--verify-baseline",
            "--json",
        ],
        binaries,
        check=False,
    )

    assert result.returncode == 2
    assert result.value["status"] == "fail"
    assert "GET miss" in result.value["error"]


def test_daemon_restart_between_workers_passes(
    tmp_path: Path,
    daemon: Daemon,
    binaries: dict[str, Path],
) -> None:
    handoff = tmp_path / "handoff.json"
    prefill = _run_json(
        [
            str(WORKER_PREFILL),
            "--endpoint",
            daemon.endpoint,
            "--prompt",
            "1 2 3 4 5",
            "--block-size",
            "2",
            "--seed",
            "1234",
            "--handoff",
            str(handoff),
            "--json",
        ],
        binaries,
    )
    assert prefill["status"] == "pass"

    daemon.restart()

    decode = _run_json(
        [
            str(WORKER_DECODE),
            "--endpoint",
            daemon.endpoint,
            "--handoff",
            str(handoff),
            "--decode-tokens",
            "4",
            "--verify-baseline",
            "--json",
        ],
        binaries,
    )

    assert decode["status"] == "pass"
    assert decode["baseline_continuation"] == decode["bifrost_continuation"]


class JsonRunResult:
    def __init__(self, returncode: int, value: dict[str, Any]) -> None:
        self.returncode = returncode
        self.value = value

    def __getitem__(self, key: str) -> Any:
        return self.value[key]


def _run_json(
    args: list[str],
    binaries: dict[str, Path],
    *,
    check: bool = True,
) -> JsonRunResult:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(BIFROST_PY)
    env["PATH"] = f"{binaries['xfer'].parent}{os.pathsep}{env.get('PATH', '')}"
    completed = subprocess.run(
        [sys.executable, *args],
        cwd=REPO_ROOT,
        env=env,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if check and completed.returncode != 0:
        raise AssertionError(
            f"command failed ({completed.returncode}): {args}\n"
            f"stdout: {completed.stdout}\nstderr: {completed.stderr}"
        )
    value = json.loads(completed.stdout)
    return JsonRunResult(completed.returncode, value)

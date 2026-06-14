#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
WORKER_PREFILL = Path(__file__).resolve().parent / "worker_prefill.py"
WORKER_DECODE = Path(__file__).resolve().parent / "worker_decode.py"

DEFAULT_PROMPT = "1 2 3 4"
DEFAULT_DECODE_TOKENS = 4
DEFAULT_BLOCK_SIZE = 4
DEFAULT_SEED = 1234
LOGIT_ATOL = 1e-6


def run_kv_teleport_demo(
    *,
    endpoint: str,
    prompt: str,
    decode_tokens: int,
    block_size: int,
    seed: int,
    work_dir: str | Path | None = None,
    restart_daemon_command: str | None = None,
) -> dict[str, Any]:
    if not endpoint:
        raise ValueError("--endpoint must be non-empty")

    with _managed_work_dir(work_dir) as root:
        handoff = root / "handoff.json"
        prefill = _run_worker_json(
            [
                sys.executable,
                str(WORKER_PREFILL),
                "--endpoint",
                endpoint,
                "--prompt",
                prompt,
                "--block-size",
                str(block_size),
                "--seed",
                str(seed),
                "--handoff",
                str(handoff),
                "--work-dir",
                str(root / "prefill"),
                "--json",
            ],
            "worker_prefill",
        )
        if restart_daemon_command:
            _run_restart_command(restart_daemon_command)
        decode = _run_worker_json(
            [
                sys.executable,
                str(WORKER_DECODE),
                "--endpoint",
                endpoint,
                "--handoff",
                str(handoff),
                "--decode-tokens",
                str(decode_tokens),
                "--verify-baseline",
                "--work-dir",
                str(root / "decode"),
                "--json",
            ],
            "worker_decode",
        )

    continuation_match = (
        decode.get("baseline_continuation") == decode.get("bifrost_continuation")
    )
    manifest_complete = decode.get("manifest_completeness") == "complete"
    logit_max_abs_error = float(decode.get("logit_max_abs_error", float("inf")))
    result = (
        "pass"
        if prefill.get("status") == "pass"
        and decode.get("status") == "pass"
        and manifest_complete
        and continuation_match
        and logit_max_abs_error <= LOGIT_ATOL
        else "fail"
    )
    return {
        "result": result,
        "model_id": decode.get("model_id", "bifrost_tiny_transformer"),
        "model_revision": decode.get("model_revision", "phase4.v1"),
        "device": "cpu",
        "dtype": decode.get("dtype", "float32"),
        "prompt_tokens": decode.get("prompt_tokens", prefill.get("prompt_tokens")),
        "prefix_token_count": len(decode.get("prompt_tokens") or []),
        "continuation_token_count": decode_tokens,
        "block_size_tokens": block_size,
        "pages_written": prefill.get("put_success_count", 0),
        "pages_read": decode.get("pages_read", 0),
        "page_count": prefill.get("page_count", 0),
        "manifest_id": prefill.get("manifest_id"),
        "manifest_completeness": decode.get("manifest_completeness"),
        "manifest_complete": manifest_complete,
        "baseline_continuation": decode.get("baseline_continuation", []),
        "bifrost_continuation": decode.get("bifrost_continuation", []),
        "logit_max_abs_error": logit_max_abs_error,
        "max_logit_abs_diff": logit_max_abs_error,
        "greedy_tokens_baseline": decode.get("baseline_continuation", []),
        "greedy_tokens_roundtrip": decode.get("bifrost_continuation", []),
        "greedy_tokens_match": continuation_match,
        "prefill": prefill,
        "decode": decode,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the cross-process tiny-transformer KV teleportation demo."
    )
    parser.add_argument("--endpoint", required=True, help="BIFROST daemon HOST:PORT")
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--decode-tokens", type=int, default=DEFAULT_DECODE_TOKENS)
    parser.add_argument("--block-size", type=int, default=DEFAULT_BLOCK_SIZE)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--work-dir")
    parser.add_argument("--restart-daemon-command")
    parser.add_argument("--json", action="store_true", help="print a JSON summary")
    args = parser.parse_args(argv)

    try:
        summary = run_kv_teleport_demo(
            endpoint=args.endpoint,
            prompt=args.prompt,
            decode_tokens=args.decode_tokens,
            block_size=args.block_size,
            seed=args.seed,
            work_dir=args.work_dir,
            restart_daemon_command=args.restart_daemon_command,
        )
    except Exception as exc:
        failure = {"result": "fail", "error": str(exc)}
        if args.json:
            print(json.dumps(failure, sort_keys=True))
        else:
            print(f"result: fail\nerror: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(summary, sort_keys=True))
    else:
        print(_format_human_summary(summary))
    return 0 if summary["result"] == "pass" else 1


def _run_worker_json(argv: list[str], label: str) -> dict[str, Any]:
    completed = subprocess.run(
        argv,
        cwd=REPO_ROOT,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"{label} failed ({completed.returncode}): "
            f"stdout: {completed.stdout}\nstderr: {completed.stderr}"
        )
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"{label} did not return JSON: stdout: {completed.stdout}"
        ) from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"{label} returned non-object JSON")
    return value


def _run_restart_command(command: str) -> None:
    completed = subprocess.run(
        shlex.split(command),
        cwd=REPO_ROOT,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"restart daemon command failed ({completed.returncode}): "
            f"stdout: {completed.stdout}\nstderr: {completed.stderr}"
        )


def _format_human_summary(summary: dict[str, Any]) -> str:
    return "\n".join(
        [
            "model: bifrost_tiny_transformer phase4.v1",
            "device: cpu",
            f"dtype: {summary['dtype']}",
            f"prompt: {summary['prompt_tokens']}",
            f"page_count: {summary['page_count']}",
            f"manifest_id: {summary['manifest_id']}",
            f"manifest_completeness: {summary['manifest_completeness']}",
            f"baseline_continuation: {summary['baseline_continuation']}",
            f"BIFROST_continuation: {summary['bifrost_continuation']}",
            f"logit_max_abs_error: {summary['logit_max_abs_error']:.9f}",
            f"result: {'PASS' if summary['result'] == 'pass' else 'FAIL'}",
        ]
    )


def _managed_work_dir(work_dir: str | Path | None):
    if work_dir is not None:
        path = Path(work_dir)
        path.mkdir(parents=True, exist_ok=True)
        return _ExistingWorkDir(path)
    return _TemporaryWorkDir()


class _ExistingWorkDir:
    def __init__(self, path: Path) -> None:
        self.path = path

    def __enter__(self) -> Path:
        return self.path

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None


class _TemporaryWorkDir:
    def __init__(self) -> None:
        import tempfile

        self._temp = tempfile.TemporaryDirectory(prefix="bifrost-kv-teleport-")

    def __enter__(self) -> Path:
        return Path(self._temp.__enter__())

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        return self._temp.__exit__(exc_type, exc, tb)


if __name__ == "__main__":
    raise SystemExit(main())

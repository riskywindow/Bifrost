#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "bifrost_py"))

from bifrost_model.store_roundtrip import run_store_roundtrip


DEFAULT_PROMPT = "1 2 3 4"
DEFAULT_DECODE_TOKENS = 4
DEFAULT_BLOCK_SIZE = 4
DEFAULT_SEED = 1234


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run a daemon-backed tiny-transformer KV store roundtrip."
    )
    parser.add_argument("--endpoint", required=True, help="BIFROST daemon HOST:PORT")
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--decode-tokens", type=int, default=DEFAULT_DECODE_TOKENS)
    parser.add_argument("--block-size", type=int, default=DEFAULT_BLOCK_SIZE)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--work-dir")
    parser.add_argument("--json", action="store_true", help="print a JSON summary")
    args = parser.parse_args(argv)

    try:
        summary = run_store_roundtrip(
            endpoint=args.endpoint,
            prompt=args.prompt,
            decode_tokens=args.decode_tokens,
            block_size=args.block_size,
            seed=args.seed,
            work_dir=args.work_dir,
        )
    except Exception as exc:
        if args.json:
            print(json.dumps({"status": "fail", "error": str(exc)}, sort_keys=True))
        else:
            print(f"result: fail\nerror: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(summary, sort_keys=True))
    else:
        print(_format_human_summary(summary))

    return 0 if summary["status"] == "pass" else 1


def _format_human_summary(summary: dict[str, Any]) -> str:
    return "\n".join(
        [
            "model: bifrost_tiny_transformer phase4.v1",
            "device: cpu",
            "dtype: float32",
            f"prompt_tokens: {summary['prompt_tokens']}",
            f"page_count: {summary['page_count']}",
            f"put_success_count: {summary['put_success_count']}",
            f"get_success_count: {summary['get_success_count']}",
            f"baseline_continuation: {summary['baseline_continuation']}",
            f"rehydrated_continuation: {summary['rehydrated_continuation']}",
            f"continuation_match: {str(summary['continuation_match']).lower()}",
            f"logit_max_abs_error: {summary['logit_max_abs_error']:.9f}",
            f"total_put_ms: {summary['total_put_ms']:.3f}",
            f"total_get_ms: {summary['total_get_ms']:.3f}",
            f"rehydrate_ms: {summary['rehydrate_ms']:.3f}",
            f"result: {summary['status']}",
        ]
    )


if __name__ == "__main__":
    raise SystemExit(main())

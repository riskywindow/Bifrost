#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from math import ceil
from pathlib import Path
from typing import Any

import torch

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "bifrost_py"))

from bifrost_model import TinyIntTokenizer, TinyTransformer, TinyTransformerConfig
from bifrost_model.kv_cache import resume_generate_greedy
from bifrost_model.kv_page_codec import kv_cache_to_native_pages, native_pages_to_kv_cache


DEFAULT_PROMPT = "1 2 3 4"
DEFAULT_DECODE_TOKENS = 4
DEFAULT_BLOCK_SIZE = 4
DEFAULT_SEED = 1234
LOGIT_ATOL = 1e-6


def run_roundtrip(
    *,
    prompt: str,
    decode_tokens: int,
    block_size: int,
    seed: int,
) -> dict[str, Any]:
    if decode_tokens < 0:
        raise ValueError("--decode-tokens must be non-negative")
    if block_size <= 0:
        raise ValueError("--block-size must be positive")

    config = TinyTransformerConfig(seed=seed)
    tokenizer = TinyIntTokenizer(vocab_size=config.vocab_size)
    prompt_tokens = tokenizer.encode(prompt)
    if not prompt_tokens:
        raise ValueError("--prompt must contain at least one integer token")

    input_ids = torch.tensor(prompt_tokens, dtype=torch.long)
    model = TinyTransformer(config)
    model.eval()

    with torch.no_grad():
        baseline_tokens = model.generate_greedy(input_ids, max_new_tokens=decode_tokens)
        baseline_continuation = baseline_tokens[len(prompt_tokens) :].tolist()

        prefix_logits, past_key_values = model.prefill(input_ids)
        pages = kv_cache_to_native_pages(
            past_key_values,
            model,
            tokenizer,
            config,
            prompt_tokens,
            block_size,
        )
        rehydrated = native_pages_to_kv_cache(pages, config)

        next_input_id = int(torch.argmax(prefix_logits[-1]).item())
        baseline_next_logits, _ = model.decode_one(next_input_id, past_key_values)
        rehydrated_next_logits, _ = model.decode_one(next_input_id, rehydrated)
        logit_max_abs_error = float(
            torch.max(torch.abs(baseline_next_logits - rehydrated_next_logits)).item()
        )

        rehydrated_continuation = resume_generate_greedy(
            model,
            next_input_id,
            rehydrated,
            max_new_tokens=decode_tokens,
        ).tolist()

    object_ids = [page.metadata["object_id"] for page in pages]
    expected_page_count = config.num_layers * ceil(len(prompt_tokens) / block_size)
    page_count = len(pages)
    object_ids_unique = len(object_ids) == len(set(object_ids))
    continuation_match = baseline_continuation == rehydrated_continuation
    logits_match = logit_max_abs_error <= LOGIT_ATOL
    page_count_match = page_count == expected_page_count
    status = (
        "pass"
        if continuation_match and logits_match and page_count_match and object_ids_unique
        else "fail"
    )

    return {
        "status": status,
        "prompt_tokens": prompt_tokens,
        "baseline_continuation": baseline_continuation,
        "rehydrated_continuation": rehydrated_continuation,
        "continuation_match": continuation_match,
        "logit_max_abs_error": logit_max_abs_error,
        "page_count": page_count,
        "layer_count": config.num_layers,
        "block_size_tokens": block_size,
        "object_ids": object_ids,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run a one-process tiny-transformer KV serialization roundtrip."
    )
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--decode-tokens", type=int, default=DEFAULT_DECODE_TOKENS)
    parser.add_argument("--block-size", type=int, default=DEFAULT_BLOCK_SIZE)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--json", action="store_true", help="print a JSON summary")
    args = parser.parse_args(argv)

    try:
        summary = run_roundtrip(
            prompt=args.prompt,
            decode_tokens=args.decode_tokens,
            block_size=args.block_size,
            seed=args.seed,
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
            f"baseline_continuation: {summary['baseline_continuation']}",
            f"rehydrated_continuation: {summary['rehydrated_continuation']}",
            f"continuation_match: {str(summary['continuation_match']).lower()}",
            f"logit_max_abs_error: {summary['logit_max_abs_error']:.9f}",
            f"page_count: {summary['page_count']}",
            f"layer_count: {summary['layer_count']}",
            f"block_size_tokens: {summary['block_size_tokens']}",
            "object_ids_unique: "
            f"{str(len(summary['object_ids']) == len(set(summary['object_ids']))).lower()}",
            f"result: {summary['status']}",
        ]
    )


if __name__ == "__main__":
    raise SystemExit(main())

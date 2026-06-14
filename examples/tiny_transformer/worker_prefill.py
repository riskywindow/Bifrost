#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "bifrost_py"))

from bifrost_model.manifest_roundtrip import create_complete_prefix_manifest
from bifrost_model.profile import MODEL_ID, MODEL_REVISION
from bifrost_model.store_roundtrip import (
    inspect_native_pages,
    prepare_store_roundtrip,
    put_native_pages,
    write_native_page_files,
)


DEFAULT_PROMPT = "1 2 3 4"
DEFAULT_BLOCK_SIZE = 4
DEFAULT_SEED = 1234


def run_worker_prefill(
    *,
    endpoint: str,
    prompt: str,
    block_size: int,
    seed: int,
    handoff: str | Path,
    work_dir: str | Path | None = None,
) -> dict[str, Any]:
    if not endpoint:
        raise ValueError("--endpoint must be non-empty")

    handoff_path = Path(handoff)
    root = Path(work_dir) if work_dir is not None else handoff_path.parent / "prefill-pages"
    root.mkdir(parents=True, exist_ok=True)

    prepared = prepare_store_roundtrip(
        prompt=prompt,
        decode_tokens=1,
        block_size=block_size,
        seed=seed,
    )
    page_files = write_native_page_files(prepared.pages, root)
    put_totals = put_native_pages(endpoint=endpoint, page_files=page_files)
    object_ids = [page.metadata["object_id"] for page in prepared.pages]
    inspect_native_pages(endpoint=endpoint, object_ids=object_ids)
    manifest = create_complete_prefix_manifest(
        endpoint=endpoint,
        prepared=prepared,
        page_files=page_files,
    )

    handoff_doc = {
        "schema_version": "bifrost.phase4.kv_teleport_handoff.v1",
        "model_id": MODEL_ID,
        "model_revision": MODEL_REVISION,
        "device": "cpu",
        "dtype": prepared.config.dtype,
        "seed": seed,
        "config": asdict(prepared.config),
        "prompt": prompt,
        "prompt_tokens": prepared.prompt_tokens,
        "block_size_tokens": block_size,
        "manifest_id": manifest.manifest_id,
        "manifest_completeness": manifest.completeness_state,
        "object_ids": object_ids,
        "pages": [
            {
                "object_id": page.metadata["object_id"],
                "metadata": page.metadata,
                "target_profile": page.target_profile,
            }
            for page in prepared.pages
        ],
        "next_input": {
            "convention": "argmax of final prefill logits; continuation includes this token first",
            "token_id": prepared.next_input_id,
        },
    }
    handoff_path.parent.mkdir(parents=True, exist_ok=True)
    handoff_path.write_text(
        json.dumps(handoff_doc, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )

    status = (
        "pass"
        if put_totals.success_count == len(prepared.pages)
        and manifest.completeness_state == "complete"
        else "fail"
    )
    return {
        "status": status,
        "prompt_tokens": prepared.prompt_tokens,
        "config": asdict(prepared.config),
        "seed": seed,
        "manifest_id": manifest.manifest_id,
        "manifest_completeness": manifest.completeness_state,
        "page_count": len(prepared.pages),
        "put_success_count": put_totals.success_count,
        "object_ids": object_ids,
        "handoff": str(handoff_path),
        "next_input_token": prepared.next_input_id,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Prefill the tiny transformer and hand off native KV pages."
    )
    parser.add_argument("--endpoint", required=True, help="BIFROST daemon HOST:PORT")
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--block-size", type=int, default=DEFAULT_BLOCK_SIZE)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--handoff", required=True)
    parser.add_argument("--work-dir")
    parser.add_argument("--json", action="store_true", help="print a JSON summary")
    args = parser.parse_args(argv)

    try:
        summary = run_worker_prefill(
            endpoint=args.endpoint,
            prompt=args.prompt,
            block_size=args.block_size,
            seed=args.seed,
            handoff=args.handoff,
            work_dir=args.work_dir,
        )
    except Exception as exc:
        failure = {"status": "fail", "error": str(exc)}
        if args.json:
            print(json.dumps(failure, sort_keys=True))
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
            f"dtype: {summary['config']['dtype']}",
            f"prompt: {summary['prompt_tokens']}",
            f"page_count: {summary['page_count']}",
            f"manifest_id: {summary['manifest_id']}",
            f"manifest_completeness: {summary['manifest_completeness']}",
            f"put_success_count: {summary['put_success_count']}",
            f"handoff: {summary['handoff']}",
            f"result: {summary['status']}",
        ]
    )


if __name__ == "__main__":
    raise SystemExit(main())

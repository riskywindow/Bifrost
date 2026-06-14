#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "bifrost_py"))

from bifrost_model.kv_page_codec import NativePage
from bifrost_model.manifest_roundtrip import inspect_and_check_manifest
from bifrost_model.profile import MODEL_ID, MODEL_REVISION
from bifrost_model.store_roundtrip import (
    LOGIT_ATOL,
    PageFileSet,
    complete_store_roundtrip,
    get_native_pages,
    prepare_store_roundtrip,
)


DEFAULT_DECODE_TOKENS = 4


def run_worker_decode(
    *,
    endpoint: str,
    handoff: str | Path,
    decode_tokens: int,
    verify_baseline: bool,
    work_dir: str | Path | None = None,
) -> dict[str, Any]:
    if not endpoint:
        raise ValueError("--endpoint must be non-empty")
    if decode_tokens < 0:
        raise ValueError("--decode-tokens must be non-negative")

    handoff_path = Path(handoff)
    if not handoff_path.is_file():
        raise FileNotFoundError(f"handoff file not found: {handoff_path}")
    handoff_doc = _read_handoff(handoff_path)
    if handoff_doc.get("schema_version") != "bifrost.phase4.kv_teleport_handoff.v1":
        raise ValueError("handoff schema_version is unsupported")

    prompt = str(handoff_doc["prompt"])
    seed = int(handoff_doc["seed"])
    block_size = int(handoff_doc["block_size_tokens"])
    object_ids = _object_ids(handoff_doc)
    if not object_ids:
        raise ValueError("handoff contains no object_ids")

    prepared = prepare_store_roundtrip(
        prompt=prompt,
        decode_tokens=decode_tokens,
        block_size=block_size,
        seed=seed,
    )
    if prepared.prompt_tokens != handoff_doc.get("prompt_tokens"):
        raise ValueError("handoff prompt_tokens do not match deterministic tokenizer output")
    if prepared.next_input_id != (handoff_doc.get("next_input") or {}).get("token_id"):
        raise ValueError("handoff next input token does not match deterministic prefill")

    manifest_id = str(handoff_doc["manifest_id"])
    manifest = inspect_and_check_manifest(
        endpoint=endpoint,
        prepared=prepared,
        manifest_id=manifest_id,
    )
    if manifest.completeness_state != "complete":
        raise RuntimeError(
            "manifest is not complete: "
            f"{manifest.completeness_state} missing={manifest.missing_member_count}"
        )

    expected_pages = _expected_pages_from_handoff(handoff_doc)
    page_files = _page_files_for_handoff(handoff_doc, handoff_path.parent)
    root = Path(work_dir) if work_dir is not None else handoff_path.parent / "decode-pages"
    try:
        fetched_pages, get_totals = get_native_pages(
            endpoint=endpoint,
            expected_pages=expected_pages,
            page_files=page_files,
            work_dir=root,
        )
    except RuntimeError as exc:
        if "not_found" in str(exc) or "GET miss" in str(exc):
            raise RuntimeError(f"GET miss while fetching handoff pages: {exc}") from exc
        raise
    completion = complete_store_roundtrip(prepared, fetched_pages)

    continuation_match = completion["continuation_match"]
    logits_match = completion["logit_max_abs_error"] <= LOGIT_ATOL
    status = (
        "pass"
        if get_totals.success_count == len(object_ids)
        and continuation_match
        and (logits_match if verify_baseline else True)
        else "fail"
    )
    return {
        "status": status,
        "model_id": MODEL_ID,
        "model_revision": MODEL_REVISION,
        "device": "cpu",
        "dtype": prepared.config.dtype,
        "prompt_tokens": prepared.prompt_tokens,
        "decode_tokens": decode_tokens,
        "block_size_tokens": block_size,
        "manifest_id": manifest_id,
        "manifest_completeness": manifest.completeness_state,
        "page_count": len(object_ids),
        "pages_read": get_totals.success_count,
        "baseline_continuation": prepared.baseline_continuation,
        "bifrost_continuation": completion["rehydrated_continuation"],
        "continuation_match": continuation_match,
        "logit_max_abs_error": completion["logit_max_abs_error"],
        "verify_baseline": verify_baseline,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Decode from tiny-transformer KV pages handed off by worker_prefill."
    )
    parser.add_argument("--endpoint", required=True, help="BIFROST daemon HOST:PORT")
    parser.add_argument("--handoff", required=True)
    parser.add_argument("--decode-tokens", type=int, default=DEFAULT_DECODE_TOKENS)
    parser.add_argument("--verify-baseline", action="store_true")
    parser.add_argument("--work-dir")
    parser.add_argument("--json", action="store_true", help="print a JSON summary")
    args = parser.parse_args(argv)

    try:
        summary = run_worker_decode(
            endpoint=args.endpoint,
            handoff=args.handoff,
            decode_tokens=args.decode_tokens,
            verify_baseline=args.verify_baseline,
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


def _read_handoff(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("handoff JSON root must be an object")
    return value


def _object_ids(handoff_doc: dict[str, Any]) -> list[str]:
    object_ids = handoff_doc.get("object_ids")
    if not isinstance(object_ids, list) or not all(
        isinstance(object_id, str) for object_id in object_ids
    ):
        raise ValueError("handoff object_ids must be a string list")
    return object_ids


def _expected_pages_from_handoff(handoff_doc: dict[str, Any]) -> list[NativePage]:
    pages = handoff_doc.get("pages")
    if not isinstance(pages, list):
        raise ValueError("handoff pages must be a list")
    expected: list[NativePage] = []
    for page in pages:
        if not isinstance(page, dict):
            raise ValueError("handoff page entries must be objects")
        expected.append(
            NativePage(
                metadata=page["metadata"],
                payload=b"",
                target_profile=page["target_profile"],
            )
        )
    return expected


def _page_files_for_handoff(
    handoff_doc: dict[str, Any],
    root: Path,
) -> list[PageFileSet]:
    files: list[PageFileSet] = []
    for index, object_id in enumerate(_object_ids(handoff_doc)):
        page_dir = root / "handoff-pages" / f"page-{index:04d}"
        files.append(
            PageFileSet(
                object_id=object_id,
                page_dir=page_dir,
                meta_path=page_dir / "meta.json",
                payload_path=page_dir / "payload.bin",
                target_path=page_dir / "target.json",
            )
        )
    return files


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
            f"result: {summary['status']}",
        ]
    )


if __name__ == "__main__":
    raise SystemExit(main())

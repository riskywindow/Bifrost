#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROMPT = "1 2 3 4 5"
DEFAULT_DECODE_TOKENS = 2
DEFAULT_BLOCK_SIZE = 2
DEFAULT_SEED = 1234

if str(REPO_ROOT / "bifrost_py") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "bifrost_py"))

from bifrost_model.manifest_roundtrip import (
    add_manifest_member,
    check_manifest,
    create_prefix_manifest,
)
from bifrost_model.store_roundtrip import (
    PageFileSet,
    prepare_store_roundtrip,
    put_native_pages,
    write_native_page_files,
)


def run_corrupt_kv_page_demo(
    *,
    endpoint: str,
    store_root: str | Path,
    prompt: str,
    decode_tokens: int,
    block_size: int,
    seed: int,
    work_dir: str | Path,
    page_index: int,
    xfer_bin: str | Path | None = None,
    store_bin: str | Path | None = None,
) -> dict[str, Any]:
    root = Path(work_dir)
    root.mkdir(parents=True, exist_ok=True)
    prepared = prepare_store_roundtrip(
        prompt=prompt,
        decode_tokens=decode_tokens,
        block_size=block_size,
        seed=seed,
    )
    page_files = write_native_page_files(prepared.pages, root / "pages")
    if page_index < 0 or page_index >= len(page_files):
        raise ValueError("--page-index is out of range")

    put_totals = put_native_pages(
        endpoint=endpoint,
        page_files=page_files,
        xfer_bin=xfer_bin,
    )
    manifest_id = _create_complete_manifest(
        endpoint=endpoint,
        prepared_pages=prepared.pages,
        prompt_token_count=len(prepared.prompt_tokens),
        page_files=page_files,
        store_bin=store_bin,
    )

    selected = page_files[page_index]
    payload_path = _committed_payload_path(Path(store_root), selected.object_id)
    _flip_first_byte(payload_path)

    fsck = _run_store_json(
        store_bin,
        ["fsck", "--endpoint", endpoint, "--check", "--json"],
        allow_dirty=True,
    )
    manifest = check_manifest(
        endpoint=endpoint,
        manifest_id=manifest_id,
        store_bin=store_bin,
    )
    completeness = manifest.get("completeness") or {}
    missing = completeness.get("missing") or []
    selected_missing = next(
        (
            item
            for item in missing
            if item.get("object_id") == selected.object_id
        ),
        None,
    )
    rehydration_attempted = completeness.get("completeness_state") == "complete"
    expected_failure_reason = (
        selected_missing.get("reason") if isinstance(selected_missing, dict) else None
    )

    result = (
        "pass"
        if put_totals.success_count == len(page_files)
        and fsck.get("status") == "dirty"
        and "payload_hash_mismatch" in _finding_types(fsck)
        and not rehydration_attempted
        and expected_failure_reason
        else "fail"
    )
    return {
        "result": result,
        "prompt_tokens": prepared.prompt_tokens,
        "page_count": len(page_files),
        "corrupted_object_id": selected.object_id,
        "corrupted_payload_path": str(payload_path),
        "manifest_id": manifest_id,
        "manifest_completeness": completeness.get("completeness_state", "unknown"),
        "expected_failure_reason": expected_failure_reason,
        "rehydration_attempted": rehydration_attempted,
        "fsck_status": fsck.get("status"),
        "fsck_finding_types": sorted(_finding_types(fsck)),
        "fsck_findings": fsck.get("findings", []),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Corrupt one stored tiny-transformer KV page and prove fail-closed handling."
    )
    parser.add_argument("--endpoint", required=True, help="BIFROST daemon HOST:PORT")
    parser.add_argument(
        "--store-root",
        required=True,
        help="local store root used by the daemon, for deliberate disk corruption",
    )
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--decode-tokens", type=int, default=DEFAULT_DECODE_TOKENS)
    parser.add_argument("--block-size", type=int, default=DEFAULT_BLOCK_SIZE)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--work-dir", required=True)
    parser.add_argument("--page-index", type=int, default=0)
    parser.add_argument("--json", action="store_true", help="print a JSON summary")
    args = parser.parse_args(argv)

    try:
        summary = run_corrupt_kv_page_demo(
            endpoint=args.endpoint,
            store_root=args.store_root,
            prompt=args.prompt,
            decode_tokens=args.decode_tokens,
            block_size=args.block_size,
            seed=args.seed,
            work_dir=args.work_dir,
            page_index=args.page_index,
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


def _create_complete_manifest(
    *,
    endpoint: str,
    prepared_pages: list[Any],
    prompt_token_count: int,
    page_files: list[PageFileSet],
    store_bin: str | Path | None,
) -> str:
    create_result = create_prefix_manifest(
        endpoint=endpoint,
        pages=prepared_pages,
        token_range_start=0,
        token_range_end=prompt_token_count,
        store_bin=store_bin,
    )
    manifest_id = create_result["manifest"]["manifest"]["manifest_id"]
    for page in page_files:
        add_manifest_member(
            endpoint=endpoint,
            manifest_id=manifest_id,
            object_id=page.object_id,
            store_bin=store_bin,
        )
    initial = check_manifest(
        endpoint=endpoint,
        manifest_id=manifest_id,
        store_bin=store_bin,
    )
    state = initial["completeness"]["completeness_state"]
    if state != "complete":
        raise RuntimeError(f"manifest did not become complete before corruption: {state}")
    return manifest_id


def _run_store_json(
    store_bin: str | Path | None,
    args: list[str],
    *,
    allow_dirty: bool = False,
) -> dict[str, Any]:
    store = _resolve_store_bin(store_bin)
    completed = subprocess.run(
        [str(store), *args],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    allowed = {0, 1} if allow_dirty else {0}
    if completed.returncode not in allowed:
        raise RuntimeError(
            f"store command failed ({completed.returncode}): {completed.args}\n"
            f"stdout: {completed.stdout}\nstderr: {completed.stderr}"
        )
    return json.loads(completed.stdout)


def _resolve_store_bin(explicit: str | Path | None) -> Path:
    if explicit is not None:
        return Path(explicit)
    import shutil

    found = shutil.which("bifrost-store")
    if found:
        return Path(found)
    candidate = REPO_ROOT / "bifrostd" / "target" / "debug" / "bifrost-store"
    if candidate.exists():
        return candidate
    raise FileNotFoundError(
        "bifrost-store binary not found; run "
        "`cargo build --manifest-path bifrostd/Cargo.toml --bins`"
    )


def _flip_first_byte(path: Path) -> None:
    payload = bytearray(path.read_bytes())
    if not payload:
        raise ValueError(f"cannot corrupt empty payload: {path}")
    payload[0] ^= 0x01
    path.write_bytes(payload)


def _committed_payload_path(store_root: Path, object_id: str) -> Path:
    suffix = _object_suffix(object_id)
    return store_root / "objects" / suffix[:2] / suffix[2:4] / f"{suffix}.payload.bin"


def _object_suffix(object_id: str) -> str:
    prefix = "bifrost://object/blake3/"
    if not object_id.startswith(prefix):
        raise ValueError(f"unsupported object ID: {object_id}")
    return object_id.removeprefix(prefix)


def _finding_types(fsck_result: dict[str, Any]) -> set[str]:
    return {finding["finding_type"] for finding in fsck_result.get("findings", [])}


def _format_human_summary(summary: dict[str, Any]) -> str:
    lines = [
        "model: bifrost_tiny_transformer phase4.v1",
        "device: cpu",
        f"prompt: {summary['prompt_tokens']}",
        f"page_count: {summary['page_count']}",
        f"corrupted_object_id: {summary['corrupted_object_id']}",
        f"fsck_status: {summary['fsck_status']}",
        f"fsck_finding_types: {', '.join(summary['fsck_finding_types'])}",
        f"manifest_completeness: {summary['manifest_completeness']}",
        f"expected_failure_reason: {summary['expected_failure_reason']}",
        f"rehydration_attempted: {summary['rehydration_attempted']}",
        f"result: {'PASS' if summary['result'] == 'pass' else 'FAIL'}",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())

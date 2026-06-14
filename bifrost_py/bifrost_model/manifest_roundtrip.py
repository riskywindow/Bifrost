from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from bifrost_model.kv_page_codec import NativePage
from bifrost_model.store_roundtrip import (
    LOGIT_ATOL,
    PageFileSet,
    PreparedStoreRoundtrip,
    _managed_work_dir,
    _resolve_binary,
    _run_json_command,
    complete_store_roundtrip,
    get_native_pages,
    inspect_native_pages,
    prepare_store_roundtrip,
    put_native_pages,
    write_native_page_files,
)


@dataclass(frozen=True)
class ManifestWorkflowResult:
    manifest_id: str
    create_result: dict[str, Any]
    inspect_result: dict[str, Any]
    check_result: dict[str, Any]
    completeness_state: str
    required_member_count: int
    missing_member_count: int
    missing_expected_members: list[dict[str, int]]


def run_manifest_roundtrip(
    *,
    endpoint: str,
    prompt: str,
    decode_tokens: int,
    block_size: int,
    seed: int,
    work_dir: str | Path | None = None,
    xfer_bin: str | Path | None = None,
    store_bin: str | Path | None = None,
) -> dict[str, Any]:
    if not endpoint:
        raise ValueError("--endpoint must be non-empty")

    with _managed_work_dir(work_dir) as root:
        prepared = prepare_store_roundtrip(
            prompt=prompt,
            decode_tokens=decode_tokens,
            block_size=block_size,
            seed=seed,
        )
        page_files = write_native_page_files(prepared.pages, root)
        put_totals = put_native_pages(
            endpoint=endpoint,
            page_files=page_files,
            xfer_bin=xfer_bin,
        )
        inspect_native_pages(
            endpoint=endpoint,
            object_ids=[page.object_id for page in page_files],
            store_bin=store_bin,
        )
        manifest = create_complete_prefix_manifest(
            endpoint=endpoint,
            prepared=prepared,
            page_files=page_files,
            store_bin=store_bin,
        )
        if manifest.completeness_state != "complete":
            raise RuntimeError(
                "manifest is not complete: "
                f"{manifest.completeness_state} missing={manifest.missing_member_count}"
            )

        member_page_files = page_files_for_manifest_members(
            manifest.inspect_result,
            page_files,
        )
        fetched_pages, get_totals = get_native_pages(
            endpoint=endpoint,
            expected_pages=prepared.pages,
            page_files=member_page_files,
            work_dir=root,
            xfer_bin=xfer_bin,
        )
        completion = complete_store_roundtrip(prepared, fetched_pages)

    status = (
        "pass"
        if put_totals.success_count == len(prepared.pages)
        and get_totals.success_count == len(prepared.pages)
        and manifest.completeness_state == "complete"
        and completion["continuation_match"]
        and completion["logit_max_abs_error"] <= LOGIT_ATOL
        else "fail"
    )
    return {
        "status": status,
        "manifest_id": manifest.manifest_id,
        "manifest_completeness": manifest.completeness_state,
        "page_count": len(prepared.pages),
        "required_member_count": manifest.required_member_count,
        "missing_member_count": manifest.missing_member_count,
        "continuation_match": completion["continuation_match"],
        "logit_max_abs_error": completion["logit_max_abs_error"],
        "prompt_tokens": prepared.prompt_tokens,
        "put_success_count": put_totals.success_count,
        "get_success_count": get_totals.success_count,
        "object_ids": [page.metadata["object_id"] for page in prepared.pages],
        "baseline_continuation": prepared.baseline_continuation,
        "rehydrated_continuation": completion["rehydrated_continuation"],
        "total_put_ms": put_totals.elapsed_ms,
        "total_get_ms": get_totals.elapsed_ms,
        "rehydrate_ms": completion["rehydrate_ms"],
    }


def create_complete_prefix_manifest(
    *,
    endpoint: str,
    prepared: PreparedStoreRoundtrip,
    page_files: Sequence[PageFileSet],
    store_bin: str | Path | None = None,
    required: bool = True,
) -> ManifestWorkflowResult:
    create_result = create_prefix_manifest(
        endpoint=endpoint,
        pages=prepared.pages,
        token_range_start=0,
        token_range_end=len(prepared.prompt_tokens),
        store_bin=store_bin,
    )
    manifest_id = _manifest_id_from_result(create_result)
    for page in page_files:
        add_manifest_member(
            endpoint=endpoint,
            manifest_id=manifest_id,
            object_id=page.object_id,
            required=required,
            store_bin=store_bin,
        )
    return inspect_and_check_manifest(
        endpoint=endpoint,
        prepared=prepared,
        manifest_id=manifest_id,
        store_bin=store_bin,
    )


def create_prefix_manifest(
    *,
    endpoint: str,
    pages: Sequence[NativePage],
    token_range_start: int,
    token_range_end: int,
    store_bin: str | Path | None = None,
) -> dict[str, Any]:
    identity = manifest_identity_from_pages(
        pages,
        token_range_start=token_range_start,
        token_range_end=token_range_end,
    )
    store = _resolve_binary("bifrost-store", store_bin)
    _, result = _run_json_command(
        [
            str(store),
            "manifest",
            "create-prefix",
            "--endpoint",
            endpoint,
            "--model-hash",
            identity["model_hash"],
            "--tokenizer-hash",
            identity["tokenizer_hash"],
            "--rope-config-hash",
            identity["rope_config_hash"],
            "--prefix-hash",
            identity["prefix_hash"],
            "--token-range-start",
            str(identity["token_range_start"]),
            "--token-range-end",
            str(identity["token_range_end"]),
            "--json",
        ]
    )
    return result


def add_manifest_member(
    *,
    endpoint: str,
    manifest_id: str,
    object_id: str,
    required: bool = True,
    store_bin: str | Path | None = None,
) -> None:
    store = _resolve_binary("bifrost-store", store_bin)
    argv = [
        str(store),
        "manifest",
        "add-member",
        "--endpoint",
        endpoint,
        "--manifest-id",
        manifest_id,
        "--object-id",
        object_id,
    ]
    if not required:
        argv.append("--required=false")
    completed = subprocess.run(
        argv,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"manifest add-member failed for {object_id}: "
            f"stdout: {completed.stdout}\nstderr: {completed.stderr}"
        )


def inspect_and_check_manifest(
    *,
    endpoint: str,
    prepared: PreparedStoreRoundtrip,
    manifest_id: str,
    store_bin: str | Path | None = None,
) -> ManifestWorkflowResult:
    inspect_result = inspect_manifest(
        endpoint=endpoint,
        manifest_id=manifest_id,
        store_bin=store_bin,
    )
    check_result = check_manifest(
        endpoint=endpoint,
        manifest_id=manifest_id,
        store_bin=store_bin,
    )
    coverage_missing = missing_expected_members(prepared, inspect_result)
    completeness = check_result.get("completeness") or {}
    store_missing = completeness.get("missing") or []
    store_state = str(completeness.get("completeness_state", "unknown"))
    state = "complete" if store_state == "complete" and not coverage_missing else "incomplete"
    required_count = sum(
        1 for member in _members_from_inspect(inspect_result) if member.get("required") is True
    )
    return ManifestWorkflowResult(
        manifest_id=manifest_id,
        create_result={},
        inspect_result=inspect_result,
        check_result=check_result,
        completeness_state=state,
        required_member_count=required_count,
        missing_member_count=len(store_missing) + len(coverage_missing),
        missing_expected_members=coverage_missing,
    )


def inspect_manifest(
    *,
    endpoint: str,
    manifest_id: str,
    store_bin: str | Path | None = None,
) -> dict[str, Any]:
    store = _resolve_binary("bifrost-store", store_bin)
    _, result = _run_json_command(
        [
            str(store),
            "manifest",
            "inspect",
            "--endpoint",
            endpoint,
            "--manifest-id",
            manifest_id,
            "--json",
        ]
    )
    return result


def check_manifest(
    *,
    endpoint: str,
    manifest_id: str,
    store_bin: str | Path | None = None,
) -> dict[str, Any]:
    store = _resolve_binary("bifrost-store", store_bin)
    _, result = _run_json_command(
        [
            str(store),
            "manifest",
            "check",
            "--endpoint",
            endpoint,
            "--manifest-id",
            manifest_id,
            "--json",
        ]
    )
    return result


def pin_manifest(
    *,
    endpoint: str,
    manifest_id: str,
    store_bin: str | Path | None = None,
) -> None:
    _run_plain_store_command(
        store_bin,
        [
            "manifest",
            "pin",
            "--endpoint",
            endpoint,
            "--manifest-id",
            manifest_id,
        ],
    )


def quarantine_object(
    *,
    endpoint: str,
    object_id: str,
    reason: str,
    store_bin: str | Path | None = None,
) -> None:
    _run_plain_store_command(
        store_bin,
        [
            "quarantine",
            "--endpoint",
            endpoint,
            "--object-id",
            object_id,
            "--reason",
            reason,
        ],
    )


def evict_store(
    *,
    endpoint: str,
    max_objects: int,
    store_bin: str | Path | None = None,
) -> dict[str, Any]:
    store = _resolve_binary("bifrost-store", store_bin)
    _, result = _run_json_command(
        [
            str(store),
            "evict",
            "--endpoint",
            endpoint,
            "--policy",
            "lru",
            "--max-objects",
            str(max_objects),
            "--json",
        ]
    )
    return result


def manifest_identity_from_pages(
    pages: Sequence[NativePage],
    *,
    token_range_start: int,
    token_range_end: int,
) -> dict[str, Any]:
    if not pages:
        raise ValueError("pages must contain at least one native KV page")
    first = pages[0].metadata
    model = first["model_profile"]
    prefix = first["prefix_profile"]
    prefix_hash = prefix["prefix_hash"]
    for page in pages:
        page_prefix_hash = page.metadata["prefix_profile"]["prefix_hash"]
        if page_prefix_hash != prefix_hash:
            raise ValueError("pages do not share one prompt prefix_hash")
    return {
        "model_hash": model["model_hash"],
        "tokenizer_hash": model["tokenizer_hash"],
        "rope_config_hash": model["rope_config_hash"],
        "prefix_hash": prefix_hash,
        "token_range_start": token_range_start,
        "token_range_end": token_range_end,
    }


def page_files_for_manifest_members(
    inspect_result: dict[str, Any],
    page_files: Sequence[PageFileSet],
) -> list[PageFileSet]:
    by_id = {page.object_id: page for page in page_files}
    ordered: list[PageFileSet] = []
    for member in sorted(
        _members_from_inspect(inspect_result),
        key=lambda item: (
            item.get("layer_id", -1),
            item.get("kv_block_id", -1),
            item.get("object_id", ""),
        ),
    ):
        if member.get("required") is not True:
            continue
        object_id = member["object_id"]
        try:
            ordered.append(by_id[object_id])
        except KeyError as exc:
            raise RuntimeError(f"manifest member has no local page mapping: {object_id}") from exc
    return ordered


def missing_expected_members(
    prepared: PreparedStoreRoundtrip,
    inspect_result: dict[str, Any],
) -> list[dict[str, int]]:
    expected = {
        (
            page.metadata["native_tensor_profile"]["layer_id"],
            page.metadata["native_tensor_profile"]["kv_block_id"],
        )
        for page in prepared.pages
    }
    observed = {
        (member.get("layer_id"), member.get("kv_block_id"))
        for member in _members_from_inspect(inspect_result)
        if member.get("required") is True
    }
    return [
        {"layer_id": layer_id, "kv_block_id": kv_block_id}
        for layer_id, kv_block_id in sorted(expected - observed)
    ]


def _manifest_id_from_result(result: dict[str, Any]) -> str:
    manifest = result.get("manifest") or {}
    record = manifest.get("manifest") or {}
    manifest_id = record.get("manifest_id")
    if not isinstance(manifest_id, str) or not manifest_id:
        raise RuntimeError("manifest create did not return manifest_id")
    return manifest_id


def _members_from_inspect(result: dict[str, Any]) -> list[dict[str, Any]]:
    manifest = result.get("manifest") or {}
    members = manifest.get("members") or []
    if not isinstance(members, list):
        raise RuntimeError("manifest inspect returned invalid members")
    return members


def _run_plain_store_command(
    store_bin: str | Path | None,
    args: list[str],
) -> None:
    store = _resolve_binary("bifrost-store", store_bin)
    completed = subprocess.run(
        [str(store), *args],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"store command failed: {completed.args}\n"
            f"stdout: {completed.stdout}\nstderr: {completed.stderr}"
        )


__all__ = [
    "ManifestWorkflowResult",
    "add_manifest_member",
    "check_manifest",
    "create_complete_prefix_manifest",
    "create_prefix_manifest",
    "evict_store",
    "inspect_and_check_manifest",
    "inspect_manifest",
    "manifest_identity_from_pages",
    "missing_expected_members",
    "page_files_for_manifest_members",
    "pin_manifest",
    "quarantine_object",
    "run_manifest_roundtrip",
]

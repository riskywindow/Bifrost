from __future__ import annotations

from copy import deepcopy
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Iterator

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
BIFROST_PY = REPO_ROOT / "bifrost_py"
DEMO = REPO_ROOT / "examples" / "tiny_transformer" / "corrupt_kv_page_demo.py"

sys.path.insert(0, str(BIFROST_PY))

from bifrost_kv.hashing import compute_descriptor_hash, compute_object_id
from bifrost_kv.validate import validate_object
from bifrost_model.kv_page_codec import NativePage, native_page_to_kv_block, native_pages_to_kv_cache
from bifrost_model.manifest_roundtrip import (
    add_manifest_member,
    check_manifest,
    create_prefix_manifest,
    inspect_and_check_manifest,
    quarantine_object,
)
from bifrost_model.store_roundtrip import (
    PageFileSet,
    get_native_pages,
    prepare_store_roundtrip,
    put_native_pages,
    write_native_page_files,
)
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
def daemon(tmp_path: Path, binaries: dict[str, Path]) -> Iterator[Daemon]:
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


def test_payload_byte_flip_before_put_rejects(
    tmp_path: Path,
    daemon: Daemon,
    binaries: dict[str, Path],
) -> None:
    prepared, page_files = _prepared_page_files(tmp_path)
    payload = bytearray(page_files[0].payload_path.read_bytes())
    payload[0] ^= 0x01
    page_files[0].payload_path.write_bytes(payload)

    result = _xfer_put_json(
        daemon.endpoint,
        page_files[0],
        xfer_bin=binaries["xfer"],
    )

    assert result["accepted"] is False
    assert result["reason"] == "payload_hash_mismatch"
    assert validate_object(
        prepared.pages[0].metadata,
        bytes(payload),
        prepared.pages[0].target_profile,
    ).reason_code == "payload_hash_mismatch"


def test_committed_payload_byte_flip_get_fsck_and_manifest_fail_closed(
    tmp_path: Path,
    daemon: Daemon,
    binaries: dict[str, Path],
) -> None:
    prepared, page_files = _store_prepared_pages(tmp_path, daemon, binaries)
    manifest_id = _complete_manifest(daemon, binaries, prepared, page_files)
    corrupt_id = page_files[0].object_id
    _flip_committed_payload_byte(daemon.spool, corrupt_id)

    get_result = _xfer_get_json(
        daemon.endpoint,
        corrupt_id,
        tmp_path / "bad-get",
        xfer_bin=binaries["xfer"],
    )
    fsck = _fsck_json(daemon.endpoint, binaries["store"], mode="check")
    manifest = inspect_and_check_manifest(
        endpoint=daemon.endpoint,
        prepared=prepared,
        manifest_id=manifest_id,
        store_bin=binaries["store"],
    )
    fetched_pages, _ = get_native_pages(
        endpoint=daemon.endpoint,
        expected_pages=prepared.pages,
        page_files=page_files[1:],
        work_dir=tmp_path / "partial-get",
        xfer_bin=binaries["xfer"],
    )

    assert get_result["found"] is False
    assert get_result["reason"] in {"not_found", "payload_hash_mismatch"}
    assert fsck["status"] == "dirty"
    assert _finding_types(fsck) >= {"payload_hash_mismatch"}
    assert manifest.completeness_state == "incomplete"
    assert manifest.missing_member_count >= 1
    assert any(
        missing["object_id"] == corrupt_id
        and missing["reason"] in {"catalog_inconsistent", "object_absent", "object_corrupt"}
        for missing in manifest.check_result["completeness"]["missing"]
    )
    with pytest.raises(ValueError, match="has no KV cache blocks|wrong block count|missing"):
        native_pages_to_kv_cache(fetched_pages, prepared.config)


def test_corrupt_committed_metadata_hash_makes_object_unservable_and_fsck_dirty(
    tmp_path: Path,
    daemon: Daemon,
    binaries: dict[str, Path],
) -> None:
    _, page_files = _store_prepared_pages(tmp_path, daemon, binaries)
    object_id = page_files[0].object_id
    metadata_path = _committed_meta_path(daemon.spool, object_id)
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["integrity"]["descriptor_hash"] = "blake3:" + "8" * 64
    metadata_path.write_text(json.dumps(metadata, sort_keys=True) + "\n", encoding="utf-8")

    inspect = _store_inspect_json(daemon.endpoint, object_id, binaries["store"])
    get_result = _xfer_get_json(
        daemon.endpoint,
        object_id,
        tmp_path / "bad-metadata-get",
        xfer_bin=binaries["xfer"],
    )
    fsck = _fsck_json(daemon.endpoint, binaries["store"], mode="check")

    assert inspect["found"] is False
    assert inspect["reason"] == "not_found"
    assert get_result["found"] is False
    assert get_result["reason"] in {"not_found", "descriptor_hash_mismatch"}
    assert fsck["status"] == "dirty"
    assert _finding_types(fsck) >= {"metadata_hash_mismatch"}


def test_deleted_payload_file_is_missing_and_manifest_incomplete(
    tmp_path: Path,
    daemon: Daemon,
    binaries: dict[str, Path],
) -> None:
    prepared, page_files = _store_prepared_pages(tmp_path, daemon, binaries)
    manifest_id = _complete_manifest(daemon, binaries, prepared, page_files)
    object_id = page_files[-1].object_id
    _committed_payload_path(daemon.spool, object_id).unlink()

    fsck = _fsck_json(daemon.endpoint, binaries["store"], mode="check")
    manifest = inspect_and_check_manifest(
        endpoint=daemon.endpoint,
        prepared=prepared,
        manifest_id=manifest_id,
        store_bin=binaries["store"],
    )

    assert fsck["status"] == "dirty"
    assert _finding_types(fsck) >= {"catalog_object_missing_payload_file"}
    assert manifest.completeness_state == "incomplete"
    assert any(
        missing["object_id"] == object_id
        and missing["reason"] in {"catalog_inconsistent", "object_absent", "object_corrupt"}
        for missing in manifest.check_result["completeness"]["missing"]
    )


def test_quarantined_object_is_not_rehydrated(
    tmp_path: Path,
    daemon: Daemon,
    binaries: dict[str, Path],
) -> None:
    prepared, page_files = _store_prepared_pages(tmp_path, daemon, binaries)
    manifest_id = _complete_manifest(daemon, binaries, prepared, page_files)
    object_id = page_files[0].object_id

    quarantine_object(
        endpoint=daemon.endpoint,
        object_id=object_id,
        reason="phase4_adversarial_test",
        store_bin=binaries["store"],
    )
    manifest = inspect_and_check_manifest(
        endpoint=daemon.endpoint,
        prepared=prepared,
        manifest_id=manifest_id,
        store_bin=binaries["store"],
    )
    get_result = _xfer_get_json(
        daemon.endpoint,
        object_id,
        tmp_path / "quarantined-get",
        xfer_bin=binaries["xfer"],
    )

    assert get_result["found"] is False
    assert manifest.completeness_state == "incomplete"
    assert any(
        missing["object_id"] == object_id and missing["reason"] == "object_quarantined"
        for missing in manifest.check_result["completeness"]["missing"]
    )


@pytest.mark.parametrize(
    ("name", "mutate", "reason"),
    [
        (
            "wrong_model_hash",
            lambda target: target["model_profile"].__setitem__("model_hash", "blake3:" + "1" * 64),
            "wrong_model_hash",
        ),
        (
            "wrong_tokenizer_hash",
            lambda target: target["model_profile"].__setitem__("tokenizer_hash", "blake3:" + "2" * 64),
            "wrong_tokenizer_hash",
        ),
        (
            "wrong_rope_config_hash",
            lambda target: target["model_profile"].__setitem__("rope_config_hash", "blake3:" + "3" * 64),
            "wrong_rope_hash",
        ),
        (
            "wrong_dtype",
            lambda target: target["model_profile"].__setitem__("dtype", "float16"),
            "wrong_dtype",
        ),
        (
            "wrong_prefix_hash",
            lambda target: target["prefix_requirements"].__setitem__("prefix_hash", "blake3:" + "4" * 64),
            "wrong_prefix_hash",
        ),
    ],
)
def test_native_page_target_incompatibility_rejects(
    name: str,
    mutate: Callable[[dict[str, Any]], None],
    reason: str,
) -> None:
    page = _first_page()
    target = deepcopy(page.target_profile)
    mutate(target)

    result = validate_object(page.metadata, page.payload, target)

    assert name
    assert result.status == "rejected"
    assert result.reason_code == reason
    with pytest.raises(ValueError, match=reason):
        native_page_to_kv_block(page.metadata, page.payload, target)


@pytest.mark.parametrize(
    ("name", "mutate", "reason"),
    [
        (
            "wrong_layer_id",
            lambda metadata: metadata["native_tensor_profile"].__setitem__(
                "layer_id", metadata["model_profile"]["num_layers"]
            ),
            "invalid_layer_id",
        ),
        (
            "wrong_kv_block_id",
            lambda metadata: metadata["native_tensor_profile"].__setitem__("kv_block_id", 7),
            "invalid_kv_block_id",
        ),
        (
            "wrong_native_dtype",
            lambda metadata: (
                metadata["native_tensor_profile"].__setitem__("tensor_dtype", "float16"),
                metadata["model_profile"].__setitem__("dtype", "float16"),
            ),
            "wrong_dtype",
        ),
        (
            "wrong_tensor_layout",
            lambda metadata: metadata["native_tensor_profile"].__setitem__(
                "tensor_layout", "token_kv_head_dim"
            ),
            "invalid_tensor_layout",
        ),
    ],
)
def test_native_page_metadata_incompatibility_rejects_after_identity_recompute(
    name: str,
    mutate: Callable[[dict[str, Any]], None],
    reason: str,
) -> None:
    page = _first_page()
    metadata = deepcopy(page.metadata)
    mutate(metadata)
    _refresh_identity(metadata, page.payload)
    target = deepcopy(page.target_profile)

    result = validate_object(metadata, page.payload, target)

    assert name
    assert result.status == "rejected"
    assert result.reason_code == reason


def test_missing_layer_and_missing_block_fail_assembly() -> None:
    prepared = prepare_store_roundtrip(
        prompt="1 2 3 4 5",
        decode_tokens=2,
        block_size=2,
        seed=1234,
    )
    pages = prepared.pages

    without_layer = [
        page
        for page in pages
        if page.metadata["native_tensor_profile"]["layer_id"] != prepared.config.num_layers - 1
    ]
    without_block = [
        page
        for page in pages
        if not (
            page.metadata["native_tensor_profile"]["layer_id"] == 0
            and page.metadata["native_tensor_profile"]["kv_block_id"] == 1
        )
    ]

    with pytest.raises(ValueError, match="has no KV cache blocks"):
        native_pages_to_kv_cache(without_layer, prepared.config)
    with pytest.raises(ValueError, match="wrong block count|missing or overlapping"):
        native_pages_to_kv_cache(without_block, prepared.config)


def test_duplicate_conflicting_page_fails_assembly() -> None:
    prepared = prepare_store_roundtrip(
        prompt="1 2 3 4 5",
        decode_tokens=2,
        block_size=2,
        seed=1234,
    )
    duplicate = NativePage(
        metadata=deepcopy(prepared.pages[0].metadata),
        payload=bytes(prepared.pages[0].payload),
        target_profile=deepcopy(prepared.pages[0].target_profile),
    )

    with pytest.raises(ValueError, match="duplicate native KV page"):
        native_pages_to_kv_cache([*prepared.pages, duplicate], prepared.config)


def test_demo_corrupts_page_runs_fsck_and_reports_expected_failure(
    tmp_path: Path,
    daemon: Daemon,
    binaries: dict[str, Path],
) -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(BIFROST_PY)
    env["PATH"] = f"{binaries['xfer'].parent}{os.pathsep}{env.get('PATH', '')}"
    result = subprocess.run(
        [
            sys.executable,
            str(DEMO),
            "--endpoint",
            daemon.endpoint,
            "--prompt",
            "1 2 3 4 5",
            "--decode-tokens",
            "2",
            "--block-size",
            "2",
            "--seed",
            "1234",
            "--work-dir",
            str(tmp_path / "demo"),
            "--store-root",
            str(daemon.spool),
            "--json",
        ],
        cwd=REPO_ROOT,
        env=env,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    assert summary["result"] == "pass"
    assert summary["fsck_status"] == "dirty"
    assert "payload_hash_mismatch" in summary["fsck_finding_types"]
    assert summary["rehydration_attempted"] is False
    assert summary["expected_failure_reason"] in {
        "catalog_inconsistent",
        "object_absent",
        "object_corrupt",
    }


def _first_page() -> NativePage:
    return prepare_store_roundtrip(
        prompt="1 2 3 4 5",
        decode_tokens=2,
        block_size=2,
        seed=1234,
    ).pages[0]


def _prepared_page_files(tmp_path: Path):
    prepared = prepare_store_roundtrip(
        prompt="1 2 3 4 5",
        decode_tokens=2,
        block_size=2,
        seed=1234,
    )
    return prepared, write_native_page_files(prepared.pages, tmp_path / "pages")


def _store_prepared_pages(
    tmp_path: Path,
    daemon: Daemon,
    binaries: dict[str, Path],
):
    prepared, page_files = _prepared_page_files(tmp_path)
    put_native_pages(
        endpoint=daemon.endpoint,
        page_files=page_files,
        xfer_bin=binaries["xfer"],
    )
    return prepared, page_files


def _complete_manifest(
    daemon: Daemon,
    binaries: dict[str, Path],
    prepared,
    page_files: list[PageFileSet],
) -> str:
    create_result = create_prefix_manifest(
        endpoint=daemon.endpoint,
        pages=prepared.pages,
        token_range_start=0,
        token_range_end=len(prepared.prompt_tokens),
        store_bin=binaries["store"],
    )
    manifest_id = create_result["manifest"]["manifest"]["manifest_id"]
    for page in page_files:
        add_manifest_member(
            endpoint=daemon.endpoint,
            manifest_id=manifest_id,
            object_id=page.object_id,
            store_bin=binaries["store"],
        )
    checked = check_manifest(
        endpoint=daemon.endpoint,
        manifest_id=manifest_id,
        store_bin=binaries["store"],
    )
    assert checked["completeness"]["completeness_state"] == "complete"
    return manifest_id


def _refresh_identity(metadata: dict[str, Any], payload: bytes) -> None:
    payload_hash = metadata["integrity"]["payload_hash"]
    descriptor_hash = compute_descriptor_hash(metadata, payload_hash)
    metadata["integrity"]["descriptor_hash"] = descriptor_hash
    metadata["object_id"] = compute_object_id(descriptor_hash, payload_hash)


def _flip_committed_payload_byte(store_root: Path, object_id: str) -> None:
    payload_path = _committed_payload_path(store_root, object_id)
    payload = bytearray(payload_path.read_bytes())
    payload[0] ^= 0x01
    payload_path.write_bytes(payload)


def _committed_payload_path(store_root: Path, object_id: str) -> Path:
    suffix = _object_suffix(object_id)
    return store_root / "objects" / suffix[:2] / suffix[2:4] / f"{suffix}.payload.bin"


def _committed_meta_path(store_root: Path, object_id: str) -> Path:
    suffix = _object_suffix(object_id)
    return store_root / "objects" / suffix[:2] / suffix[2:4] / f"{suffix}.meta.json"


def _object_suffix(object_id: str) -> str:
    prefix = "bifrost://object/blake3/"
    assert object_id.startswith(prefix)
    return object_id.removeprefix(prefix)


def _fsck_json(endpoint: str, store_bin: Path, *, mode: str) -> dict[str, Any]:
    flag = {"check": "--check", "repair": "--repair", "quarantine": "--quarantine"}[mode]
    result = subprocess.run(
        [str(store_bin), "fsck", "--endpoint", endpoint, flag, "--json"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert result.returncode in {0, 1}, result.stderr
    return json.loads(result.stdout)


def _store_inspect_json(endpoint: str, object_id: str, store_bin: Path) -> dict[str, Any]:
    result = subprocess.run(
        [
            str(store_bin),
            "inspect",
            "--endpoint",
            endpoint,
            "--object-id",
            object_id,
            "--json",
        ],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert result.returncode in {0, 1}, result.stderr
    return json.loads(result.stdout)


def _xfer_put_json(endpoint: str, page: PageFileSet, *, xfer_bin: Path) -> dict[str, Any]:
    result = subprocess.run(
        [
            str(xfer_bin),
            "--json",
            "put",
            "--endpoint",
            endpoint,
            "--meta",
            str(page.meta_path),
            "--payload",
            str(page.payload_path),
            "--target",
            str(page.target_path),
        ],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert result.returncode == 1, result.stderr
    return json.loads(result.stdout)


def _xfer_get_json(endpoint: str, object_id: str, out_dir: Path, *, xfer_bin: Path) -> dict[str, Any]:
    result = subprocess.run(
        [
            str(xfer_bin),
            "--json",
            "get",
            "--endpoint",
            endpoint,
            "--object-id",
            object_id,
            "--out",
            str(out_dir),
        ],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert result.returncode == 1, result.stderr
    return json.loads(result.stdout)


def _finding_types(fsck_result: dict[str, Any]) -> set[str]:
    return {finding["finding_type"] for finding in fsck_result["findings"]}

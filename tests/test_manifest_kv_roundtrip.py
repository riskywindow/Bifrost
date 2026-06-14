from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
BIFROST_PY = REPO_ROOT / "bifrost_py"
SCRIPT = REPO_ROOT / "examples" / "tiny_transformer" / "manifest_kv_roundtrip.py"

sys.path.insert(0, str(BIFROST_PY))

from bifrost_model.manifest_roundtrip import (
    add_manifest_member,
    create_prefix_manifest,
    evict_store,
    inspect_and_check_manifest,
    pin_manifest,
    quarantine_object,
    run_manifest_roundtrip,
)
from bifrost_model.store_roundtrip import (
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


def test_manifest_roundtrip_passes_and_uses_required_members(
    tmp_path: Path,
    daemon: Daemon,
    binaries: dict[str, Path],
) -> None:
    summary = run_manifest_roundtrip(
        endpoint=daemon.endpoint,
        prompt="1 2 3 4 5",
        decode_tokens=4,
        block_size=2,
        seed=1234,
        work_dir=tmp_path / "roundtrip",
        xfer_bin=binaries["xfer"],
        store_bin=binaries["store"],
    )

    assert summary["status"] == "pass"
    assert summary["manifest_id"].startswith("bifrost://manifest/blake3/")
    assert summary["manifest_completeness"] == "complete"
    assert summary["page_count"] == 6
    assert summary["required_member_count"] == summary["page_count"]
    assert summary["missing_member_count"] == 0
    assert summary["continuation_match"] is True
    assert summary["logit_max_abs_error"] <= 1e-6


def test_manifest_roundtrip_script_json_output(
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
            str(SCRIPT),
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
            str(tmp_path / "script-work"),
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
    assert summary["status"] == "pass"
    assert summary["manifest_completeness"] == "complete"
    assert summary["required_member_count"] == summary["page_count"]
    assert summary["missing_member_count"] == 0
    assert summary["continuation_match"] is True


def test_missing_one_page_makes_phase4_manifest_completeness_incomplete(
    tmp_path: Path,
    daemon: Daemon,
    binaries: dict[str, Path],
) -> None:
    prepared, page_files = _store_prepared_pages(tmp_path, daemon, binaries)
    manifest_id = _create_manifest_id(daemon, binaries, prepared)
    for page in page_files[:-1]:
        add_manifest_member(
            endpoint=daemon.endpoint,
            manifest_id=manifest_id,
            object_id=page.object_id,
            store_bin=binaries["store"],
        )

    result = inspect_and_check_manifest(
        endpoint=daemon.endpoint,
        prepared=prepared,
        manifest_id=manifest_id,
        store_bin=binaries["store"],
    )

    assert result.completeness_state == "incomplete"
    assert result.required_member_count == len(page_files) - 1
    assert result.missing_member_count == 1
    assert result.missing_expected_members == [{"layer_id": 1, "kv_block_id": 2}]


@pytest.mark.parametrize("mutation", ["evict", "quarantine"])
def test_unavailable_required_member_makes_manifest_incomplete(
    tmp_path: Path,
    daemon: Daemon,
    binaries: dict[str, Path],
    mutation: str,
) -> None:
    prepared, page_files = _store_prepared_pages(tmp_path, daemon, binaries)
    manifest_id = _create_manifest_id(daemon, binaries, prepared)
    for page in page_files:
        add_manifest_member(
            endpoint=daemon.endpoint,
            manifest_id=manifest_id,
            object_id=page.object_id,
            store_bin=binaries["store"],
        )

    if mutation == "evict":
        eviction = evict_store(
            endpoint=daemon.endpoint,
            max_objects=1,
            store_bin=binaries["store"],
        )
        assert len(eviction["evicted"]) == 1
    else:
        quarantine_object(
            endpoint=daemon.endpoint,
            object_id=page_files[0].object_id,
            reason="phase4_manifest_test",
            store_bin=binaries["store"],
        )

    result = inspect_and_check_manifest(
        endpoint=daemon.endpoint,
        prepared=prepared,
        manifest_id=manifest_id,
        store_bin=binaries["store"],
    )

    assert result.completeness_state == "incomplete"
    assert result.missing_member_count >= 1
    store_missing = result.check_result["completeness"]["missing"]
    assert len(store_missing) >= 1
    expected_reasons = (
        {"object_absent", "object_evicted"}
        if mutation == "evict"
        else {"object_quarantined"}
    )
    assert store_missing[0]["reason"] in expected_reasons


def test_wrong_prefix_page_cannot_be_added_to_manifest(
    tmp_path: Path,
    daemon: Daemon,
    binaries: dict[str, Path],
) -> None:
    prepared, page_files = _store_prepared_pages(tmp_path / "right", daemon, binaries)
    wrong = prepare_store_roundtrip(
        prompt="1 2 3 4 6",
        decode_tokens=1,
        block_size=2,
        seed=1234,
    )
    wrong_files = write_native_page_files(wrong.pages, tmp_path / "wrong")
    put_native_pages(
        endpoint=daemon.endpoint,
        page_files=wrong_files,
        xfer_bin=binaries["xfer"],
    )
    manifest_id = _create_manifest_id(daemon, binaries, prepared)
    add_manifest_member(
        endpoint=daemon.endpoint,
        manifest_id=manifest_id,
        object_id=page_files[0].object_id,
        store_bin=binaries["store"],
    )

    with pytest.raises(RuntimeError, match="prefix_hash mismatch"):
        add_manifest_member(
            endpoint=daemon.endpoint,
            manifest_id=manifest_id,
            object_id=wrong_files[0].object_id,
            store_bin=binaries["store"],
        )


def test_pinned_manifest_protects_required_members_from_eviction(
    tmp_path: Path,
    daemon: Daemon,
    binaries: dict[str, Path],
) -> None:
    prepared, page_files = _store_prepared_pages(tmp_path, daemon, binaries)
    manifest_id = _create_manifest_id(daemon, binaries, prepared)
    for page in page_files:
        add_manifest_member(
            endpoint=daemon.endpoint,
            manifest_id=manifest_id,
            object_id=page.object_id,
            store_bin=binaries["store"],
        )

    pin_manifest(
        endpoint=daemon.endpoint,
        manifest_id=manifest_id,
        store_bin=binaries["store"],
    )
    eviction = evict_store(
        endpoint=daemon.endpoint,
        max_objects=len(page_files),
        store_bin=binaries["store"],
    )
    result = inspect_and_check_manifest(
        endpoint=daemon.endpoint,
        prepared=prepared,
        manifest_id=manifest_id,
        store_bin=binaries["store"],
    )

    assert eviction["evicted"] == []
    assert eviction["protected_pinned_count"] >= len(page_files)
    assert result.completeness_state == "complete"
    assert result.missing_member_count == 0


def _store_prepared_pages(
    tmp_path: Path,
    daemon: Daemon,
    binaries: dict[str, Path],
):
    prepared = prepare_store_roundtrip(
        prompt="1 2 3 4 5",
        decode_tokens=4,
        block_size=2,
        seed=1234,
    )
    page_files = write_native_page_files(prepared.pages, tmp_path / "pages")
    put_native_pages(
        endpoint=daemon.endpoint,
        page_files=page_files,
        xfer_bin=binaries["xfer"],
    )
    return prepared, page_files


def _create_manifest_id(
    daemon: Daemon,
    binaries: dict[str, Path],
    prepared,
) -> str:
    result = create_prefix_manifest(
        endpoint=daemon.endpoint,
        pages=prepared.pages,
        token_range_start=0,
        token_range_end=len(prepared.prompt_tokens),
        store_bin=binaries["store"],
    )
    return result["manifest"]["manifest"]["manifest_id"]

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import tomllib

REPO_ROOT = Path(__file__).resolve().parents[1]
BIFROST_PY = REPO_ROOT / "bifrost_py"
FIXTURES_ROOT = REPO_ROOT / "fixtures"
sys.path.insert(0, str(BIFROST_PY))

from bifrost_kv.fixtures import native_metadata, native_payload, native_target_profile
from bifrost_kv.hashing import compute_object_identity
from bifrost_kv.schema import validate_validation_result
from bifrost_kv.validate import validate_object


def run_cli(*args: str | Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(BIFROST_PY)
    return subprocess.run(
        [sys.executable, "-m", "bifrost_kv.cli", *(str(arg) for arg in args)],
        check=False,
        cwd=REPO_ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as file:
        value = json.load(file)
    assert isinstance(value, dict)
    return value


def write_native_fixture(path: Path) -> None:
    path.mkdir()
    write_json(path / "meta.json", native_metadata())
    (path / "payload.bin").write_bytes(native_payload())
    write_json(path / "target_profile.json", native_target_profile())


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")


def test_console_script_is_registered() -> None:
    pyproject = tomllib.loads((BIFROST_PY / "pyproject.toml").read_text())

    assert pyproject["project"]["scripts"]["bifrost-kv"] == "bifrost_kv.cli:main"


def test_validate_accepts_native_valid_fixture() -> None:
    fixture_dir = FIXTURES_ROOT / "native_valid"

    result = run_cli(
        "validate",
        "--meta",
        fixture_dir / "tiny_gpt_layer0_block0.meta.json",
        "--payload",
        fixture_dir / "tiny_gpt_layer0_block0.payload.bin",
        "--target",
        fixture_dir / "target_profile.json",
    )

    assert result.returncode == 0
    assert result.stderr == ""
    assert result.stdout.startswith("ACCEPTED\n")
    assert "Object ID: bifrost://object/blake3/" in result.stdout


def test_validate_rejects_invalid_fixture() -> None:
    fixture_dir = FIXTURES_ROOT / "invalid" / "wrong_tokenizer_hash"

    result = run_cli(
        "validate",
        "--meta",
        fixture_dir / "meta.json",
        "--payload",
        fixture_dir / "payload.bin",
        "--target",
        fixture_dir / "target_profile.json",
    )

    assert result.returncode == 1
    assert result.stderr == ""
    assert result.stdout.startswith("REJECTED: wrong_tokenizer_hash\n")


def test_validate_json_emits_validation_result_schema() -> None:
    fixture_dir = FIXTURES_ROOT / "native_valid"

    result = run_cli(
        "validate",
        "--meta",
        fixture_dir / "tiny_gpt_layer0_block0.meta.json",
        "--payload",
        fixture_dir / "tiny_gpt_layer0_block0.payload.bin",
        "--target",
        fixture_dir / "target_profile.json",
        "--json",
    )

    assert result.returncode == 0
    value = json.loads(result.stdout)
    assert validate_validation_result(value) == []
    assert value["status"] == "accepted"
    assert value["reason_code"] == "accepted"


def test_validate_json_rejected_output_matches_validation_result_schema() -> None:
    fixture_dir = FIXTURES_ROOT / "invalid" / "payload_hash_mismatch"

    result = run_cli(
        "validate",
        "--meta",
        fixture_dir / "meta.json",
        "--payload",
        fixture_dir / "payload.bin",
        "--target",
        fixture_dir / "target_profile.json",
        "--json",
    )

    assert result.returncode == 1
    value = json.loads(result.stdout)
    assert validate_validation_result(value) == []
    assert value["status"] == "rejected"
    assert value["reason_code"] == "payload_hash_mismatch"


def test_id_emits_same_identity_as_compute_object_identity() -> None:
    fixture_dir = FIXTURES_ROOT / "native_valid"
    metadata = load_json(fixture_dir / "tiny_gpt_layer0_block0.meta.json")
    payload = (fixture_dir / "tiny_gpt_layer0_block0.payload.bin").read_bytes()
    expected = compute_object_identity(metadata, payload)

    result = run_cli(
        "id",
        "--meta",
        fixture_dir / "tiny_gpt_layer0_block0.meta.json",
        "--payload",
        fixture_dir / "tiny_gpt_layer0_block0.payload.bin",
        "--json",
    )

    assert result.returncode == 0
    assert json.loads(result.stdout) == {
        "descriptor_hash": expected.descriptor_hash,
        "object_id": expected.object_id,
        "payload_hash": expected.payload_hash,
    }


def test_make_native_fixture_creates_files_that_validate(tmp_path: Path) -> None:
    out_dir = tmp_path / "native"

    result = run_cli("make-native-fixture", "--out", out_dir)

    assert result.returncode == 0
    metadata = load_json(out_dir / "meta.json")
    payload = (out_dir / "payload.bin").read_bytes()
    target = load_json(out_dir / "target_profile.json")
    assert validate_object(metadata, payload, target).status == "accepted"


def test_corrupt_fixture_creates_invalid_fixture_with_expected_reason(
    tmp_path: Path,
) -> None:
    fixture_dir = tmp_path / "native"
    out_dir = tmp_path / "corrupt"
    write_native_fixture(fixture_dir)

    result = run_cli(
        "corrupt-fixture",
        "--fixture",
        fixture_dir,
        "--corruption",
        "payload_byte_flip",
        "--out",
        out_dir,
    )

    assert result.returncode == 0
    metadata = load_json(out_dir / "meta.json")
    payload = (out_dir / "payload.bin").read_bytes()
    target = load_json(out_dir / "target_profile.json")
    expected = load_json(out_dir / "expected_result.json")
    actual = validate_object(metadata, payload, target)
    assert actual.status == "rejected"
    assert actual.reason_code == "payload_hash_mismatch"
    assert expected["reason_code"] == actual.reason_code


def test_cli_exits_2_for_missing_files(tmp_path: Path) -> None:
    result = run_cli(
        "validate",
        "--meta",
        tmp_path / "missing.meta.json",
        "--payload",
        tmp_path / "missing.payload.bin",
    )

    assert result.returncode == 2
    assert "cannot read metadata JSON" in result.stderr


def test_cli_exits_2_for_duplicate_json_keys(tmp_path: Path) -> None:
    meta = tmp_path / "duplicate.meta.json"
    payload = tmp_path / "payload.bin"
    meta.write_text(
        '{"schema_version":"bifrost.kv_object.v1alpha1","schema_version":"future"}',
        encoding="utf-8",
    )
    payload.write_bytes(b"")

    result = run_cli("validate", "--meta", meta, "--payload", payload)

    assert result.returncode == 2
    assert "duplicate JSON object key: schema_version" in result.stderr


def test_cli_exits_2_for_usage_errors() -> None:
    result = run_cli("validate")

    assert result.returncode == 2
    assert "usage:" in result.stderr

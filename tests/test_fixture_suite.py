from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from bifrost_kv.errors import ACCEPTED
from bifrost_kv.validate import validate_object

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES_ROOT = REPO_ROOT / "fixtures"


def load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as file:
        value = json.load(file)
    assert isinstance(value, dict)
    return value


def test_native_valid_fixture_is_accepted() -> None:
    fixture_dir = FIXTURES_ROOT / "native_valid"
    metadata = load_json(fixture_dir / "tiny_gpt_layer0_block0.meta.json")
    payload = (fixture_dir / "tiny_gpt_layer0_block0.payload.bin").read_bytes()
    target = load_json(fixture_dir / "target_profile.json")

    result = validate_object(metadata, payload, target)

    assert result.status == "accepted"
    assert result.reason_code == ACCEPTED


def test_native_layer3_block7_fixture_is_accepted() -> None:
    fixture_dir = FIXTURES_ROOT / "native_valid_layer3_block7"
    metadata = load_json(fixture_dir / "tiny_gpt_layer3_block7.meta.json")
    payload = (fixture_dir / "tiny_gpt_layer3_block7.payload.bin").read_bytes()
    target = load_json(fixture_dir / "target_profile.json")

    result = validate_object(metadata, payload, target)

    assert result.status == "accepted"
    assert result.reason_code == ACCEPTED
    assert metadata["native_tensor_profile"]["layer_id"] == 3
    assert metadata["native_tensor_profile"]["kv_block_id"] == 7


def test_opaque_valid_fixture_is_accepted() -> None:
    fixture_dir = FIXTURES_ROOT / "opaque_valid"
    metadata = load_json(fixture_dir / "lmcache_blob.meta.json")
    payload = (fixture_dir / "lmcache_blob.payload.bin").read_bytes()
    target = load_json(fixture_dir / "target_profile.json")

    result = validate_object(metadata, payload, target)

    assert result.status == "accepted"
    assert result.reason_code == ACCEPTED


def test_invalid_fixtures_match_expected_results() -> None:
    invalid_dirs = sorted(
        path for path in (FIXTURES_ROOT / "invalid").iterdir() if path.is_dir()
    )
    assert invalid_dirs

    for fixture_dir in invalid_dirs:
        metadata = load_json(fixture_dir / "meta.json")
        payload = (fixture_dir / "payload.bin").read_bytes()
        target = load_json(fixture_dir / "target_profile.json")
        expected = load_json(fixture_dir / "expected_result.json")

        result = validate_object(metadata, payload, target)

        assert result.status == expected["status"], fixture_dir.name
        assert result.status == "rejected", fixture_dir.name
        assert result.reason_code == expected["reason_code"], fixture_dir.name

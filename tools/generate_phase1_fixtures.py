#!/usr/bin/env python3
"""Generate deterministic Phase 1 fixture files."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "bifrost_py"))

from bifrost_kv.fixtures import (  # noqa: E402
    invalid_fixture_cases,
    native_metadata,
    native_payload,
    native_target_profile,
    opaque_metadata,
    opaque_payload,
    opaque_target_profile,
)
from bifrost_kv.result import ValidationResult  # noqa: E402
from bifrost_kv.validate import validate_object  # noqa: E402


def main() -> int:
    fixtures_root = REPO_ROOT / "fixtures"
    invalid_root = fixtures_root / "invalid"

    valid_count = 0
    invalid_count = 0

    _write_fixture_dir(
        fixtures_root / "native_valid",
        "tiny_gpt_layer0_block0",
        native_metadata(),
        native_payload(),
        native_target_profile(),
    )
    valid_count += 1

    _write_fixture_dir(
        fixtures_root / "opaque_valid",
        "lmcache_blob",
        opaque_metadata(),
        opaque_payload(),
        opaque_target_profile(),
    )
    valid_count += 1

    if invalid_root.exists():
        shutil.rmtree(invalid_root)
    invalid_root.mkdir(parents=True, exist_ok=True)

    for case in invalid_fixture_cases():
        result = validate_object(case.metadata, case.payload, case.target_profile)
        if result.status != "rejected" or result.reason_code != case.expected_reason:
            raise RuntimeError(
                f"{case.name}: expected rejected/{case.expected_reason}, "
                f"got {result.status}/{result.reason_code}"
            )

        case_dir = invalid_root / case.name
        case_dir.mkdir(parents=True, exist_ok=True)
        _write_json(case_dir / "meta.json", case.metadata)
        _write_bytes(case_dir / "payload.bin", case.payload)
        _write_json(case_dir / "target_profile.json", case.target_profile)
        _write_json(case_dir / "expected_result.json", result.to_dict())
        invalid_count += 1

    print(
        f"Generated {valid_count} valid fixture directories and "
        f"{invalid_count} invalid fixture directories under {fixtures_root}"
    )
    return 0


def _write_fixture_dir(
    path: Path,
    stem: str,
    metadata: dict[str, Any],
    payload: bytes,
    target_profile: dict[str, Any],
) -> None:
    path.mkdir(parents=True, exist_ok=True)
    result = validate_object(metadata, payload, target_profile)
    if result.status != "accepted":
        raise RuntimeError(f"{path.name}: expected accepted, got {result.reason_code}")

    _write_json(path / f"{stem}.meta.json", metadata)
    _write_bytes(path / f"{stem}.payload.bin", payload)
    _write_json(path / "target_profile.json", target_profile)


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _write_bytes(path: Path, value: bytes) -> None:
    path.write_bytes(value)


if __name__ == "__main__":
    raise SystemExit(main())

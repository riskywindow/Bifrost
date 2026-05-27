#!/usr/bin/env python3
"""Generate Phase 1 cross-language object identity vectors."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

REPO_ROOT = Path(__file__).resolve().parents[1]
BIFROST_PY = REPO_ROOT / "bifrost_py"
if str(BIFROST_PY) not in sys.path:
    sys.path.insert(0, str(BIFROST_PY))

from bifrost_kv.hashing import compute_object_identity

VECTOR_DIR = REPO_ROOT / "fixtures" / "test_vectors"
VECTOR_PATH = VECTOR_DIR / "object_identity_vectors.json"
REORDERED_NATIVE_META = VECTOR_DIR / "native_tiny_gpt_layer0_block0.reordered.meta.json"


def load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as file:
        value = json.load(file)
    if not isinstance(value, dict):
        raise TypeError(f"{path}: expected JSON object")
    return value


def write_json(path: Path, value: Any, *, sort_keys: bool) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=sort_keys) + "\n",
        encoding="utf-8",
    )


def reverse_keys_recursively(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: reverse_keys_recursively(value[key])
            for key in sorted(value.keys(), reverse=True)
        }
    if isinstance(value, list):
        return [reverse_keys_recursively(item) for item in value]
    return value


def relative(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def vector_for(name: str, meta_path: Path, payload_path: Path) -> dict[str, str]:
    metadata = load_json(meta_path)
    payload = payload_path.read_bytes()
    identity = compute_object_identity(metadata, payload)
    return {
        "name": name,
        "meta_path": relative(meta_path),
        "payload_path": relative(payload_path),
        "expected_payload_hash": identity.payload_hash,
        "expected_descriptor_hash": identity.descriptor_hash,
        "expected_object_id": identity.object_id,
    }


def main() -> int:
    VECTOR_DIR.mkdir(parents=True, exist_ok=True)

    native_meta = REPO_ROOT / "fixtures/native_valid/tiny_gpt_layer0_block0.meta.json"
    native_payload = REPO_ROOT / "fixtures/native_valid/tiny_gpt_layer0_block0.payload.bin"
    native_layer3_block7_meta = (
        REPO_ROOT
        / "fixtures/native_valid_layer3_block7/tiny_gpt_layer3_block7.meta.json"
    )
    native_layer3_block7_payload = (
        REPO_ROOT
        / "fixtures/native_valid_layer3_block7/tiny_gpt_layer3_block7.payload.bin"
    )
    opaque_meta = REPO_ROOT / "fixtures/opaque_valid/lmcache_blob.meta.json"
    opaque_payload = REPO_ROOT / "fixtures/opaque_valid/lmcache_blob.payload.bin"

    write_json(
        REORDERED_NATIVE_META,
        reverse_keys_recursively(load_json(native_meta)),
        sort_keys=False,
    )

    vectors = [
        vector_for("native_tiny_gpt_layer0_block0", native_meta, native_payload),
        vector_for(
            "native_tiny_gpt_layer3_block7",
            native_layer3_block7_meta,
            native_layer3_block7_payload,
        ),
        vector_for("opaque_lmcache_blob", opaque_meta, opaque_payload),
        vector_for(
            "native_tiny_gpt_layer0_block0_key_order_variant",
            REORDERED_NATIVE_META,
            native_payload,
        ),
    ]
    write_json(VECTOR_PATH, vectors, sort_keys=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

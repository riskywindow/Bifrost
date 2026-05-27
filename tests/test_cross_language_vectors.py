from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
BIFROST_PY = REPO_ROOT / "bifrost_py"
if str(BIFROST_PY) not in sys.path:
    sys.path.insert(0, str(BIFROST_PY))

from bifrost_kv.hashing import compute_object_identity

VECTOR_PATH = REPO_ROOT / "fixtures/test_vectors/object_identity_vectors.json"


def load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as file:
        return json.load(file)


def reverse_keys_recursively(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: reverse_keys_recursively(value[key])
            for key in sorted(value.keys(), reverse=True)
        }
    if isinstance(value, list):
        return [reverse_keys_recursively(item) for item in value]
    return value


def test_python_identity_matches_committed_vectors() -> None:
    vectors = load_json(VECTOR_PATH)
    assert isinstance(vectors, list)

    for vector in vectors:
        metadata = load_json(REPO_ROOT / vector["meta_path"])
        payload = (REPO_ROOT / vector["payload_path"]).read_bytes()
        identity = compute_object_identity(metadata, payload)

        assert identity.payload_hash == vector["expected_payload_hash"], vector["name"]
        assert (
            identity.descriptor_hash == vector["expected_descriptor_hash"]
        ), vector["name"]
        assert identity.object_id == vector["expected_object_id"], vector["name"]


def test_python_object_identity_ignores_recursive_metadata_key_order() -> None:
    metadata = load_json(
        REPO_ROOT / "fixtures/native_valid/tiny_gpt_layer0_block0.meta.json"
    )
    payload = (
        REPO_ROOT / "fixtures/native_valid/tiny_gpt_layer0_block0.payload.bin"
    ).read_bytes()

    original = compute_object_identity(metadata, payload)
    reordered = compute_object_identity(reverse_keys_recursively(metadata), payload)

    assert reordered == original

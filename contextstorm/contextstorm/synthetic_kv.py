"""Deterministic synthetic KV object generation."""

from __future__ import annotations

import json
import sys
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def _ensure_phase1_importable() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    bifrost_py = repo_root / "bifrost_py"
    if bifrost_py.exists() and str(bifrost_py) not in sys.path:
        sys.path.insert(0, str(bifrost_py))


_ensure_phase1_importable()

from bifrost_kv.fixtures import (  # noqa: E402
    CREATED_AT_UNIX_MS,
    deterministic_hash,
    deterministic_payload,
    finalize_identity,
    native_metadata,
    native_target_profile_for_metadata,
    opaque_metadata,
)


@dataclass(frozen=True)
class ModelShape:
    layers: int = 12
    num_kv_heads: int = 12
    head_dim: int = 64
    tokens: int = 256
    dtype: str = "float16"

    @classmethod
    def from_mapping(cls, value: dict[str, Any] | None) -> "ModelShape":
        if not value:
            return cls()
        return cls(
            layers=int(value.get("layers", cls.layers)),
            num_kv_heads=int(value.get("num_kv_heads", cls.num_kv_heads)),
            head_dim=int(value.get("head_dim", cls.head_dim)),
            tokens=int(value.get("tokens", cls.tokens)),
            dtype=str(value.get("dtype", cls.dtype)),
        )


@dataclass(frozen=True)
class SyntheticObject:
    metadata: dict[str, Any]
    payload: bytes
    target_profile: dict[str, Any]

    @property
    def object_id(self) -> str:
        return str(self.metadata["object_id"])


def generate_synthetic_object(
    *,
    object_size_bytes: int,
    object_type: str = "opaque_engine_blob",
    model_shape: dict[str, Any] | ModelShape | None = None,
) -> SyntheticObject:
    """Build a valid Phase 1-style object with deterministic synthetic bytes."""

    if object_size_bytes < 0:
        raise ValueError("object_size_bytes must be non-negative")
    if object_type not in {"native_kv_page", "opaque_engine_blob"}:
        raise ValueError(f"unsupported object_type: {object_type}")

    provided_shape = model_shape is not None
    shape = (
        model_shape
        if isinstance(model_shape, ModelShape)
        else ModelShape.from_mapping(model_shape)
    )
    if object_type == "native_kv_page":
        shape = (
            _validated_native_shape(shape, object_size_bytes)
            if provided_shape
            else _native_shape_for_size(object_size_bytes)
        )
    payload = deterministic_payload(object_size_bytes)

    if object_type == "native_kv_page":
        metadata = _native_metadata(shape)
        finalized = finalize_identity(metadata, payload)
        target = native_target_profile_for_metadata(finalized)
    else:
        metadata = _opaque_metadata(shape)
        finalized = finalize_identity(metadata, payload)
        target = {
            "schema_version": "bifrost.target_profile.v1alpha1",
            "accepts_object_type": "opaque_engine_blob",
            "model_profile": None,
            "engine_profile": deepcopy(finalized["engine_profile"]),
            "prefix_requirements": None,
            "opaque_requirements": deepcopy(finalized["opaque_engine_profile"]),
        }

    return SyntheticObject(metadata=finalized, payload=payload, target_profile=target)


def write_synthetic_object(obj: SyntheticObject, out_dir: Path) -> dict[str, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    meta_path = out_dir / "meta.json"
    payload_path = out_dir / "payload.bin"
    target_path = out_dir / "target_profile.json"
    manifest_path = out_dir / "manifest.json"

    meta_path.write_text(json.dumps(obj.metadata, indent=2, sort_keys=True) + "\n")
    payload_path.write_bytes(obj.payload)
    target_path.write_text(
        json.dumps(obj.target_profile, indent=2, sort_keys=True) + "\n"
    )
    manifest = {
        "object_id": obj.object_id,
        "payload_bytes": len(obj.payload),
        "meta": str(meta_path),
        "payload": str(payload_path),
        "target_profile": str(target_path),
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return manifest


def _native_metadata(shape: ModelShape) -> dict[str, Any]:
    metadata = native_metadata()
    metadata["created_at_unix_ms"] = CREATED_AT_UNIX_MS
    metadata["created_by"] = "contextstorm"
    metadata["model_profile"]["num_layers"] = shape.layers
    metadata["model_profile"]["num_attention_heads"] = shape.num_kv_heads
    metadata["model_profile"]["num_kv_heads"] = shape.num_kv_heads
    metadata["model_profile"]["head_dim"] = shape.head_dim
    metadata["model_profile"]["dtype"] = shape.dtype
    metadata["engine_profile"]["block_size_tokens"] = shape.tokens
    metadata["prefix_profile"]["token_count"] = shape.tokens
    metadata["prefix_profile"]["token_range"] = {"start": 0, "end": shape.tokens}
    metadata["prefix_profile"]["absolute_position_range"] = {
        "start": 0,
        "end": shape.tokens,
    }
    metadata["prefix_profile"]["prefix_hash"] = deterministic_hash(
        f"contextstorm:prefix:0:{shape.tokens}"
    )
    metadata["prefix_profile"]["token_hash"] = deterministic_hash(
        f"contextstorm:tokens:0:{shape.tokens}"
    )
    metadata["native_tensor_profile"]["block_size_tokens"] = shape.tokens
    metadata["native_tensor_profile"]["block_token_count"] = shape.tokens
    metadata["native_tensor_profile"]["token_range"] = {"start": 0, "end": shape.tokens}
    metadata["native_tensor_profile"]["tensor_shape"] = [
        2,
        shape.tokens,
        shape.num_kv_heads,
        shape.head_dim,
    ]
    metadata["native_tensor_profile"]["tensor_dtype"] = shape.dtype
    metadata["provenance"] = {
        "source": "contextstorm",
        "notes": "Deterministic synthetic Phase 2 benchmark object.",
        "producer_commit": "0" * 40,
        "producer_hostname": "localhost",
    }
    return metadata


def _native_shape_for_size(object_size_bytes: int) -> ModelShape:
    # Native Phase 1 validation requires byte_length to match the tensor shape.
    # The smallest simple valid shape uses one KV head, one dimension, and
    # float16, so each token contributes four bytes for K/V.
    bytes_per_token = 2 * 1 * 1 * 2
    if object_size_bytes == 0 or object_size_bytes % bytes_per_token != 0:
        raise ValueError(
            "native_kv_page object_size_bytes must be a positive multiple of 4 "
            "when model_shape is omitted"
        )
    return ModelShape(
        layers=1,
        num_kv_heads=1,
        head_dim=1,
        tokens=object_size_bytes // bytes_per_token,
        dtype="float16",
    )


def _validated_native_shape(shape: ModelShape, object_size_bytes: int) -> ModelShape:
    dtype_width = {"float16": 2, "bfloat16": 2, "float32": 4}.get(shape.dtype)
    if dtype_width is None:
        raise ValueError(f"unsupported native dtype: {shape.dtype}")
    expected = 2 * shape.tokens * shape.num_kv_heads * shape.head_dim * dtype_width
    if expected != object_size_bytes:
        raise ValueError(
            "native_kv_page object_size_bytes does not match model_shape "
            f"(expected {expected}, got {object_size_bytes})"
        )
    return shape


def _opaque_metadata(shape: ModelShape) -> dict[str, Any]:
    metadata = opaque_metadata()
    metadata["created_by"] = "contextstorm"
    metadata["model_profile"]["num_layers"] = shape.layers
    metadata["model_profile"]["num_attention_heads"] = shape.num_kv_heads
    metadata["model_profile"]["num_kv_heads"] = shape.num_kv_heads
    metadata["model_profile"]["head_dim"] = shape.head_dim
    metadata["model_profile"]["dtype"] = shape.dtype
    metadata["engine_profile"]["block_size_tokens"] = shape.tokens
    metadata["opaque_engine_profile"]["engine_key_hash"] = deterministic_hash(
        f"contextstorm:opaque:{shape.layers}:{shape.num_kv_heads}:{shape.head_dim}:{shape.tokens}:{shape.dtype}"
    )
    metadata["provenance"] = {
        "source": "contextstorm",
        "notes": "Deterministic synthetic Phase 2 benchmark object.",
        "producer_commit": "0" * 40,
        "producer_hostname": "localhost",
    }
    return metadata

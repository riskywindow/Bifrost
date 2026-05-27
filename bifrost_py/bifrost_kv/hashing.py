"""BLAKE3 identity hashing for BIFROST Phase 1 KV objects."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from blake3 import blake3

from bifrost_kv.canonical import canonical_encode
from bifrost_kv.types import ObjectIdentity

HASH_PREFIX = "blake3:"
OBJECT_ID_PREFIX = "bifrost://object/blake3/"
OBJECT_ID_DOMAIN = b"bifrost.object_id.v1\x00"


def blake3_hex(data: bytes) -> str:
    return f"{HASH_PREFIX}{blake3(data).hexdigest()}"


def compute_payload_hash(payload: bytes) -> str:
    return blake3_hex(payload)


def normalized_descriptor_for_hashing(
    meta: dict[str, Any], payload_hash: str
) -> dict[str, Any]:
    normalized = deepcopy(meta)
    normalized["object_id"] = None

    integrity = normalized.get("integrity")
    if not isinstance(integrity, dict):
        raise TypeError("$.integrity: expected object")

    integrity["descriptor_hash"] = None
    integrity["payload_hash"] = payload_hash
    return normalized


def compute_descriptor_hash(meta: dict[str, Any], payload_hash: str) -> str:
    normalized = normalized_descriptor_for_hashing(meta, payload_hash)
    return blake3_hex(canonical_encode(normalized))


def compute_object_id(descriptor_hash: str, payload_hash: str) -> str:
    material = (
        OBJECT_ID_DOMAIN
        + descriptor_hash.encode("utf-8")
        + b"\x00"
        + payload_hash.encode("utf-8")
    )
    return f"{OBJECT_ID_PREFIX}{blake3(material).hexdigest()}"


def compute_object_identity(meta: dict[str, Any], payload: bytes) -> ObjectIdentity:
    payload_hash = compute_payload_hash(payload)
    descriptor_hash = compute_descriptor_hash(meta, payload_hash)
    object_id = compute_object_id(descriptor_hash, payload_hash)
    return ObjectIdentity(
        payload_hash=payload_hash,
        descriptor_hash=descriptor_hash,
        object_id=object_id,
    )

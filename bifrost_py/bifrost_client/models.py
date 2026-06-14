"""Dataclasses returned by the Python BIFROST daemon client."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class BifrostClientConfig:
    endpoint: str = "127.0.0.1:7420"
    timeout_seconds: float = 10.0
    default_chunk_size: int = 256 * 1024


@dataclass(frozen=True, slots=True)
class PutResult:
    object_id: str
    payload_hash: str | None
    descriptor_hash: str | None
    stored: bool
    verified: bool
    reason: str = ""


@dataclass(frozen=True, slots=True)
class StoredObject:
    object_id: str
    metadata: dict[str, Any]
    payload: bytes
    payload_hash: str | None
    descriptor_hash: str | None


@dataclass(frozen=True, slots=True)
class ObjectSummary:
    object_id: str
    object_type: str
    state: str
    byte_length: int
    model_hash: str | None = None
    prefix_hash: str | None = None
    engine_name: str | None = None
    integration_name: str | None = None
    opaque_engine_key_hash: str | None = None
    layer_id: int | None = None
    kv_block_id: int | None = None
    pin_count: int = 0
    last_accessed_unix_ms: int | None = None


@dataclass(frozen=True, slots=True)
class StoreStats:
    object_count: int
    total_logical_bytes: int
    total_bytes_on_disk: int
    staging_count: int
    committed_count: int
    verified_count: int
    pinned_count: int
    evictable_count: int
    evicting_count: int
    evicted_count: int
    quarantined_count: int
    missing_count: int
    corrupt_count: int
    total_pin_count: int
    total_access_count: int
    memory_tier_enabled: bool
    memory_tier_bytes: int
    memory_tier_capacity_bytes: int
    memory_tier_hits: int
    memory_tier_misses: int
    memory_tier_evictions: int

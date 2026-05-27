"""Types exposed by the BIFROST Python reference implementation."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ObjectIdentity:
    payload_hash: str
    descriptor_hash: str
    object_id: str

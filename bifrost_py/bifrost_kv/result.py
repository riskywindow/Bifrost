"""Validation result type for BIFROST Phase 1 validators."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal

from bifrost_kv.errors import ACCEPTED, REASON_CODES, ReasonCode

VALIDATION_RESULT_SCHEMA_VERSION = "bifrost.validation_result.v1alpha1"
ValidationStatus = Literal["accepted", "rejected"]


@dataclass(frozen=True, slots=True)
class ValidationResult:
    schema_version: str = VALIDATION_RESULT_SCHEMA_VERSION
    status: ValidationStatus = "rejected"
    reason_code: str = ""
    object_id: str | None = None
    payload_hash: str | None = None
    descriptor_hash: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def accepted(
        cls,
        object_id: str,
        payload_hash: str,
        descriptor_hash: str,
        details: dict[str, Any] | None = None,
    ) -> "ValidationResult":
        return cls(
            status="accepted",
            reason_code=ACCEPTED,
            object_id=object_id,
            payload_hash=payload_hash,
            descriptor_hash=descriptor_hash,
            details={} if details is None else dict(details),
        )

    @classmethod
    def rejected(
        cls,
        reason_code: str | ReasonCode,
        object_id: str | None = None,
        payload_hash: str | None = None,
        descriptor_hash: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> "ValidationResult":
        reason = str(reason_code)
        if reason == ACCEPTED:
            raise ValueError("rejected result cannot use accepted reason code")
        if reason not in REASON_CODES:
            raise ValueError(f"unknown validation reason code: {reason}")

        return cls(
            status="rejected",
            reason_code=reason,
            object_id=object_id,
            payload_hash=payload_hash,
            descriptor_hash=descriptor_hash,
            details={} if details is None else dict(details),
        )

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ValidationResult":
        return cls(
            schema_version=value["schema_version"],
            status=value["status"],
            reason_code=value["reason_code"],
            object_id=value["object_id"],
            payload_hash=value["payload_hash"],
            descriptor_hash=value["descriptor_hash"],
            details=dict(value["details"]),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "status": self.status,
            "reason_code": self.reason_code,
            "object_id": self.object_id,
            "payload_hash": self.payload_hash,
            "descriptor_hash": self.descriptor_hash,
            "details": dict(self.details),
        }

    def to_json(self) -> str:
        return json.dumps(
            self.to_dict(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )


__all__ = [
    "VALIDATION_RESULT_SCHEMA_VERSION",
    "ValidationResult",
    "ValidationStatus",
]

"""Deterministic error types for the BIFROST vLLM connector."""

from __future__ import annotations


class BifrostVLLMConnectorError(Exception):
    """Base class for deterministic vLLM connector failures."""

    reason_code = "vllm_connector_error"

    def __init__(self, message: str | None = None, *, reason_code: str | None = None):
        self.reason_code = reason_code or self.reason_code
        super().__init__(message or self.reason_code)


class VLLMAPIInspectionError(BifrostVLLMConnectorError):
    """Raised when the vLLM KVTransfer API cannot be inspected safely."""

    reason_code = "vllm_api_inspection_failed"


class DynamicImportError(BifrostVLLMConnectorError):
    """Raised when vLLM cannot dynamically import the connector."""

    reason_code = "dynamic_import_failed"


class ConnectorConfigurationError(BifrostVLLMConnectorError):
    """Raised for invalid BIFROST vLLM connector configuration."""

    reason_code = "connector_configuration_error"


class ConnectorLifecycleError(BifrostVLLMConnectorError):
    """Raised when connector lifecycle state is invalid."""

    reason_code = "connector_lifecycle_error"


class UnsupportedOperationError(BifrostVLLMConnectorError):
    """Raised for Phase 7 operations that are not implemented yet."""

    reason_code = "unsupported_operation"


class KVCacheRegistrationError(BifrostVLLMConnectorError):
    """Raised when KV cache registration fails or is unsafe."""

    reason_code = "kv_cache_registration_error"


class SchedulerMetadataError(BifrostVLLMConnectorError):
    """Raised when scheduler metadata cannot be captured safely."""

    reason_code = "scheduler_metadata_error"


class KeyHashingError(BifrostVLLMConnectorError):
    """Raised when opaque key material cannot be hashed safely."""

    reason_code = "key_hashing_error"


class LayoutFingerprintMismatchError(BifrostVLLMConnectorError):
    """Raised when an opaque blob layout fingerprint is incompatible."""

    reason_code = "layout_fingerprint_mismatch"


class CPUStagingSerializationError(BifrostVLLMConnectorError):
    """Raised when vLLM-owned state cannot be CPU-staged safely."""

    reason_code = "cpu_staging_serialization_error"


class OpaqueBlobValidationError(BifrostVLLMConnectorError):
    """Raised when vLLM opaque blob metadata fails validation."""

    reason_code = "opaque_blob_validation_error"


class StoreCommitError(BifrostVLLMConnectorError):
    """Raised when BIFROST store commit fails or is unverified."""

    reason_code = "store_commit_error"


class StoreRetrievalError(BifrostVLLMConnectorError):
    """Raised when BIFROST object retrieval fails."""

    reason_code = "store_retrieval_error"


class MissingObjectError(BifrostVLLMConnectorError):
    """Raised when a requested opaque vLLM object is absent."""

    reason_code = "missing_object"


class CorruptObjectError(BifrostVLLMConnectorError):
    """Raised when an opaque vLLM object is corrupt."""

    reason_code = "corrupt_object"


class DescriptorMismatchError(BifrostVLLMConnectorError):
    """Raised when a descriptor does not match the requested object."""

    reason_code = "descriptor_mismatch"


class PayloadHashMismatchError(BifrostVLLMConnectorError):
    """Raised when stored payload bytes do not match their hash."""

    reason_code = "payload_hash_mismatch"


class DaemonUnavailableError(BifrostVLLMConnectorError):
    """Raised when bifrostd is required but unavailable."""

    reason_code = "daemon_unavailable"


class LoadRecomputeDecision(BifrostVLLMConnectorError):
    """Raised internally when the safe outcome is request recompute."""

    reason_code = "load_recompute"


class OptionalRealVLLMCompatibilityError(BifrostVLLMConnectorError):
    """Raised when optional real-vLLM support gates are not satisfied."""

    reason_code = "optional_real_vllm_compatibility_error"


class SkippedOptionalComponentError(BifrostVLLMConnectorError):
    """Raised when an optional Phase 7 component is intentionally skipped."""

    reason_code = "skipped_optional_component"


__all__ = [
    "BifrostVLLMConnectorError",
    "CPUStagingSerializationError",
    "ConnectorConfigurationError",
    "ConnectorLifecycleError",
    "CorruptObjectError",
    "DaemonUnavailableError",
    "DescriptorMismatchError",
    "DynamicImportError",
    "KeyHashingError",
    "KVCacheRegistrationError",
    "LayoutFingerprintMismatchError",
    "LoadRecomputeDecision",
    "MissingObjectError",
    "OpaqueBlobValidationError",
    "OptionalRealVLLMCompatibilityError",
    "PayloadHashMismatchError",
    "SchedulerMetadataError",
    "SkippedOptionalComponentError",
    "StoreCommitError",
    "StoreRetrievalError",
    "UnsupportedOperationError",
    "VLLMAPIInspectionError",
]

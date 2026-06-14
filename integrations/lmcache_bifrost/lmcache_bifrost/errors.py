"""Phase 5 LMCache integration error types."""

from __future__ import annotations


class BifrostLMCacheError(Exception):
    """Base class for deterministic LMCache integration failures."""


class KeyCodecError(BifrostLMCacheError):
    """Raised when a CacheEngineKey cannot be represented or hashed safely."""


class MemoryObjSerializationError(BifrostLMCacheError):
    """Raised when an LMCache MemoryObj cannot be serialized safely."""


class MemoryObjDeserializationError(BifrostLMCacheError):
    """Raised when payload bytes cannot be deserialized into a MemoryObj."""


class OpaqueBlobValidationError(BifrostLMCacheError):
    """Raised when generated opaque blob metadata fails Phase 1 validation."""


class ConnectorConfigurationError(BifrostLMCacheError):
    """Raised for invalid BIFROST LMCache integration configuration."""


__all__ = [
    "BifrostLMCacheError",
    "ConnectorConfigurationError",
    "KeyCodecError",
    "MemoryObjDeserializationError",
    "MemoryObjSerializationError",
    "OpaqueBlobValidationError",
]

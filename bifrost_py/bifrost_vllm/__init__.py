"""Phase 7 helpers for the optional direct vLLM KVTransfer integration.

The package must remain importable without vLLM installed. Connector
implementation code is added in later Phase 7 steps; this module currently
exposes only the API-surface inspector.
"""

from __future__ import annotations

from .api_inspector import (
    has_vllm,
    inspect_available_kv_connector_modules,
    inspect_dynamic_connector_support,
    inspect_kv_connector_base_v1,
    inspect_kv_transfer_config,
    inspect_result,
    vllm_version,
)

__all__ = [
    "has_vllm",
    "inspect_available_kv_connector_modules",
    "inspect_dynamic_connector_support",
    "inspect_kv_connector_base_v1",
    "inspect_kv_transfer_config",
    "inspect_result",
    "vllm_version",
]

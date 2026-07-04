"""Optional vLLM compatibility probes for the BIFROST connector.

This module must stay safe when vLLM is absent. Import failures are reported
through diagnostics instead of escaping at import time.
"""

from __future__ import annotations

import importlib
import importlib.metadata
from functools import lru_cache
from types import ModuleType
from typing import Any

VLLM_DISTRIBUTION = "vllm"
VLLM_MODULE = "vllm"
KV_CONNECTOR_BASE_MODULE = "vllm.distributed.kv_transfer.kv_connector.v1.base"
KV_CONNECTOR_BASE_NAME = "KVConnectorBase_V1"
KV_CONNECTOR_ROLE_NAME = "KVConnectorRole"


def has_vllm() -> bool:
    """Return True when ``import vllm`` succeeds in this process."""

    module, _error = _import_optional(VLLM_MODULE)
    return module is not None


def vllm_version() -> str | None:
    """Return the imported or installed vLLM version when discoverable."""

    module, _error = _import_optional(VLLM_MODULE)
    if module is not None:
        version = getattr(module, "__version__", None)
        if version:
            return str(version)
    try:
        return importlib.metadata.version(VLLM_DISTRIBUTION)
    except importlib.metadata.PackageNotFoundError:
        return None


def get_connector_base_class() -> type[Any] | None:
    """Return vLLM's V1 KV connector base class, or None when unavailable."""

    module, _error = _import_optional(KV_CONNECTOR_BASE_MODULE)
    if module is None:
        return None
    base_class = getattr(module, KV_CONNECTOR_BASE_NAME, None)
    return base_class if isinstance(base_class, type) else None


def get_connector_role_enum() -> type[Any] | None:
    """Return vLLM's connector role enum, or None when unavailable."""

    module, _error = _import_optional(KV_CONNECTOR_BASE_MODULE)
    if module is None:
        return None
    role_enum = getattr(module, KV_CONNECTOR_ROLE_NAME, None)
    return role_enum if isinstance(role_enum, type) else None


def compatibility_diagnostics() -> dict[str, Any]:
    """Return no-throw diagnostics for optional real-vLLM compatibility."""

    vllm_module, vllm_error = _import_optional(VLLM_MODULE)
    base_module, base_error = _import_optional(KV_CONNECTOR_BASE_MODULE)
    base_class = get_connector_base_class()
    role_enum = get_connector_role_enum()

    unsupported_reasons: list[str] = []
    import_errors: dict[str, str] = {}
    if vllm_module is None:
        unsupported_reasons.append("vllm is not importable")
    if vllm_error is not None:
        import_errors[VLLM_MODULE] = _error_message(vllm_error)
    if base_module is None:
        unsupported_reasons.append(
            f"{KV_CONNECTOR_BASE_MODULE} is not importable"
        )
    if base_error is not None:
        import_errors[KV_CONNECTOR_BASE_MODULE] = _error_message(base_error)
    if base_module is not None and base_class is None:
        unsupported_reasons.append(
            f"{KV_CONNECTOR_BASE_MODULE}.{KV_CONNECTOR_BASE_NAME} is missing"
        )
    if base_module is not None and role_enum is None:
        unsupported_reasons.append(
            f"{KV_CONNECTOR_BASE_MODULE}.{KV_CONNECTOR_ROLE_NAME} is missing"
        )

    return {
        "vllm_available": vllm_module is not None,
        "vllm_version": vllm_version(),
        "connector_base_module": KV_CONNECTOR_BASE_MODULE,
        "connector_base_class": KV_CONNECTOR_BASE_NAME,
        "connector_base_class_available": base_class is not None,
        "connector_role_enum": KV_CONNECTOR_ROLE_NAME,
        "connector_role_enum_available": role_enum is not None,
        "unsupported_reasons": unsupported_reasons,
        "import_errors": import_errors,
    }


@lru_cache(maxsize=None)
def _import_optional(module_name: str) -> tuple[ModuleType | None, BaseException | None]:
    try:
        return importlib.import_module(module_name), None
    except Exception as exc:  # pragma: no cover - depends on optional vLLM installs.
        return None, exc


def _error_message(error: BaseException) -> str:
    return f"{type(error).__name__}: {error}"


__all__ = [
    "KV_CONNECTOR_BASE_MODULE",
    "KV_CONNECTOR_BASE_NAME",
    "KV_CONNECTOR_ROLE_NAME",
    "compatibility_diagnostics",
    "get_connector_base_class",
    "get_connector_role_enum",
    "has_vllm",
    "vllm_version",
]

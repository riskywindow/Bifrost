"""Configuration for the BIFROST vLLM KVTransfer connector."""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from typing import Any, Mapping

from .errors import ConnectorConfigurationError

DEFAULT_ENDPOINT = "127.0.0.1:7420"
DEFAULT_CHUNK_SIZE = 256 * 1024
ENGINE_NAME = "vllm"
INTEGRATION_NAME = "bifrost_vllm_kv_connector"
KV_CACHE_FORMAT = "opaque_vllm_kv_blob"
VALID_TRANSFER_MODES = frozenset(("disabled", "sync", "async"))
VALID_FAILURE_POLICIES = frozenset(("fail", "recompute"))


@dataclass(frozen=True, slots=True)
class BifrostVLLMConnectorConfig:
    endpoint: str = DEFAULT_ENDPOINT
    chunk_size: int = DEFAULT_CHUNK_SIZE
    engine_name: str = ENGINE_NAME
    integration_name: str = INTEGRATION_NAME
    kv_cache_format: str = KV_CACHE_FORMAT
    strict_validation: bool = True
    allow_cpu_staging: bool = True
    save_mode: str = "sync"
    load_mode: str = "sync"
    failure_policy: str = "fail"
    timeout_seconds: float = 10.0
    connector_instance_id: str | None = None
    trace_jsonl_path: str | None = None
    model_fingerprint: str | None = None
    layout_fingerprint: str | None = None
    unknown_fields: Mapping[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        endpoint = str(self.endpoint).strip()
        save_mode = str(self.save_mode).strip().lower()
        load_mode = str(self.load_mode).strip().lower()
        failure_policy = str(self.failure_policy).strip().lower()
        object.__setattr__(self, "endpoint", endpoint)
        object.__setattr__(self, "save_mode", save_mode)
        object.__setattr__(self, "load_mode", load_mode)
        object.__setattr__(self, "failure_policy", failure_policy)
        object.__setattr__(self, "chunk_size", _coerce_positive_int("chunk_size", self.chunk_size))
        object.__setattr__(
            self,
            "timeout_seconds",
            _coerce_positive_float("timeout_seconds", self.timeout_seconds),
        )
        object.__setattr__(
            self,
            "connector_instance_id",
            _optional_non_empty_str("connector_instance_id", self.connector_instance_id),
        )
        object.__setattr__(
            self,
            "trace_jsonl_path",
            _optional_non_empty_str("trace_jsonl_path", self.trace_jsonl_path),
        )
        object.__setattr__(
            self,
            "model_fingerprint",
            _optional_non_empty_str("model_fingerprint", self.model_fingerprint),
        )
        object.__setattr__(
            self,
            "layout_fingerprint",
            _optional_non_empty_str("layout_fingerprint", self.layout_fingerprint),
        )
        object.__setattr__(self, "unknown_fields", dict(self.unknown_fields))

        if not endpoint:
            raise ConnectorConfigurationError("endpoint must be non-empty")
        if self.engine_name != ENGINE_NAME:
            raise ConnectorConfigurationError(f"engine_name must be {ENGINE_NAME!r}")
        if self.integration_name != INTEGRATION_NAME:
            raise ConnectorConfigurationError(
                f"integration_name must be {INTEGRATION_NAME!r}"
            )
        if self.kv_cache_format != KV_CACHE_FORMAT:
            raise ConnectorConfigurationError(
                f"kv_cache_format must be {KV_CACHE_FORMAT!r}"
            )
        if not isinstance(self.strict_validation, bool):
            raise ConnectorConfigurationError("strict_validation must be a boolean")
        if not isinstance(self.allow_cpu_staging, bool):
            raise ConnectorConfigurationError("allow_cpu_staging must be a boolean")
        if not self.allow_cpu_staging:
            raise ConnectorConfigurationError(
                "allow_cpu_staging=false is unsupported in Phase 7; "
                "fake and real connector paths require CPU staging"
            )
        if save_mode not in VALID_TRANSFER_MODES:
            raise ConnectorConfigurationError(
                "save_mode must be one of: disabled, sync, async"
            )
        if load_mode not in VALID_TRANSFER_MODES:
            raise ConnectorConfigurationError(
                "load_mode must be one of: disabled, sync, async"
            )
        if failure_policy not in VALID_FAILURE_POLICIES:
            raise ConnectorConfigurationError("failure_policy must be fail or recompute")

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> "BifrostVLLMConnectorConfig":
        """Parse config from a plain dict or a vLLM-style config dict."""

        if data is None:
            return cls()
        if not isinstance(data, Mapping):
            raise ConnectorConfigurationError("config must be a mapping")
        return cls._from_mapping(_extract_extra_config(data))

    @classmethod
    def from_vllm_config(cls, source: object | None) -> "BifrostVLLMConnectorConfig":
        """Parse config from vLLM/FakeVllm config objects or mappings."""

        if source is None:
            return cls()
        if isinstance(source, cls):
            return source
        if isinstance(source, Mapping):
            return cls.from_dict(source)
        kv_transfer_config = getattr(source, "kv_transfer_config", source)
        extra_config = getattr(kv_transfer_config, "kv_connector_extra_config", None)
        if extra_config is None and isinstance(kv_transfer_config, Mapping):
            extra_config = kv_transfer_config.get("kv_connector_extra_config")
        if extra_config is None:
            return cls()
        if not isinstance(extra_config, Mapping):
            raise ConnectorConfigurationError(
                "kv_connector_extra_config must be a mapping"
            )
        return cls.from_dict(extra_config)

    @classmethod
    def _from_mapping(cls, data: Mapping[str, Any]) -> "BifrostVLLMConnectorConfig":
        init_field_names = {
            item.name for item in fields(cls) if item.init and item.name != "unknown_fields"
        }
        normalized = dict(data)
        if "metrics_jsonl_path" in normalized:
            if (
                "trace_jsonl_path" in normalized
                and normalized["trace_jsonl_path"] != normalized["metrics_jsonl_path"]
            ):
                raise ConnectorConfigurationError(
                    "trace_jsonl_path and metrics_jsonl_path disagree"
                )
            normalized["trace_jsonl_path"] = normalized.pop("metrics_jsonl_path")

        kwargs: dict[str, Any] = {}
        unknown: dict[str, Any] = {}
        for key, value in normalized.items():
            if key in init_field_names:
                kwargs[key] = _coerce_config_value(key, value)
            else:
                unknown[str(key)] = value
        return cls(**kwargs, unknown_fields=unknown)


def _extract_extra_config(data: Mapping[str, Any]) -> Mapping[str, Any]:
    extra_config = data.get("kv_connector_extra_config")
    if extra_config is None:
        return data
    if not isinstance(extra_config, Mapping):
        raise ConnectorConfigurationError("kv_connector_extra_config must be a mapping")
    return extra_config


def _coerce_config_value(key: str, value: Any) -> Any:
    if key == "chunk_size":
        return _coerce_positive_int(key, value)
    if key == "timeout_seconds":
        return _coerce_positive_float(key, value)
    if key in {"strict_validation", "allow_cpu_staging"}:
        return _coerce_bool(key, value)
    if key in {
        "endpoint",
        "engine_name",
        "integration_name",
        "kv_cache_format",
        "save_mode",
        "load_mode",
        "failure_policy",
    }:
        return "" if value is None else str(value)
    if key in {
        "connector_instance_id",
        "trace_jsonl_path",
        "model_fingerprint",
        "layout_fingerprint",
    }:
        return None if value is None else str(value)
    return value


def _coerce_positive_int(name: str, value: Any) -> int:
    if isinstance(value, bool):
        raise ConnectorConfigurationError(f"{name} must be a positive integer")
    try:
        coerced = int(value)
    except (TypeError, ValueError) as exc:
        raise ConnectorConfigurationError(
            f"{name} must be a positive integer"
        ) from exc
    if coerced <= 0:
        raise ConnectorConfigurationError(f"{name} must be positive")
    return coerced


def _coerce_positive_float(name: str, value: Any) -> float:
    if isinstance(value, bool):
        raise ConnectorConfigurationError(f"{name} must be a positive number")
    try:
        coerced = float(value)
    except (TypeError, ValueError) as exc:
        raise ConnectorConfigurationError(f"{name} must be a positive number") from exc
    if coerced <= 0:
        raise ConnectorConfigurationError(f"{name} must be positive")
    return coerced


def _coerce_bool(name: str, value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    raise ConnectorConfigurationError(f"{name} must be a boolean")


def _optional_non_empty_str(name: str, value: str | None) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    if not normalized:
        raise ConnectorConfigurationError(f"{name} must be non-empty when set")
    return normalized


__all__ = [
    "BifrostVLLMConnectorConfig",
    "DEFAULT_CHUNK_SIZE",
    "DEFAULT_ENDPOINT",
    "ENGINE_NAME",
    "INTEGRATION_NAME",
    "KV_CACHE_FORMAT",
    "VALID_FAILURE_POLICIES",
    "VALID_TRANSFER_MODES",
]

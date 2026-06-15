"""LMCache ConnectorAdapter for BIFROST remote storage."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import parse_qs, urlparse

from lmcache_bifrost.config import BifrostLMCacheConfig
from lmcache_bifrost.connector import BifrostRemoteConnector
from lmcache_bifrost.errors import ConnectorConfigurationError
from lmcache_bifrost.lmcache_compat import ConnectorAdapter as LMCacheConnectorAdapter

PLUGIN_TYPE = "bifrost"
SUPPORTED_SCHEMES = frozenset(("bifrost", "bifrost+tcp", "plugin"))

_BaseConnectorAdapter = LMCacheConnectorAdapter or object


class BifrostConnectorAdapter(_BaseConnectorAdapter):  # type: ignore[misc]
    """LMCache plugin adapter that recognizes and configures BIFROST storage."""

    plugin_type = PLUGIN_TYPE
    type = PLUGIN_TYPE

    @classmethod
    def can_parse(cls, url: str | None) -> bool:
        if not url:
            return False
        parsed = urlparse(str(url))
        if parsed.scheme in ("bifrost", "bifrost+tcp"):
            return _has_host_port(parsed)
        if parsed.scheme != "plugin":
            return False
        plugin_name = _plugin_name(parsed)
        return plugin_name == PLUGIN_TYPE or plugin_name.startswith(f"{PLUGIN_TYPE}.")

    def create_connector(self, context: object) -> BifrostRemoteConnector:
        return BifrostRemoteConnector(parse_config(context), context=context)


def parse_config(context: object) -> BifrostLMCacheConfig:
    """Build BIFROST connector config from a fake or real LMCache context."""

    defaults = BifrostLMCacheConfig()
    lmcache_config = _attr(context, "config")
    extra_config = _extra_config(context, lmcache_config)
    remote_url = _remote_url(context, lmcache_config, extra_config)

    if not BifrostConnectorAdapter.can_parse(remote_url):
        raise ConnectorConfigurationError(f"unsupported BIFROST remote URL: {remote_url}")

    parsed = urlparse(remote_url)
    query = _query_values(parsed.query)
    endpoint = _endpoint_from_url(parsed, query, extra_config, lmcache_config)

    chunk_size = _int_setting("chunk_size", query, extra_config, lmcache_config)
    allow_pickle_fallback = _bool_setting(
        "allow_pickle_fallback",
        query,
        extra_config,
        lmcache_config,
    )
    timeout_seconds = _float_setting(
        "timeout_seconds",
        query,
        extra_config,
        lmcache_config,
    )
    strict_validation = _bool_setting(
        "strict_validation",
        query,
        extra_config,
        lmcache_config,
    )
    metrics_jsonl_path = _string_setting(
        "metrics_jsonl_path",
        query,
        extra_config,
        lmcache_config,
    )

    return BifrostLMCacheConfig(
        endpoint=endpoint,
        chunk_size=chunk_size
        if chunk_size is not None
        else defaults.chunk_size,
        allow_pickle_fallback=allow_pickle_fallback
        if allow_pickle_fallback is not None
        else defaults.allow_pickle_fallback,
        timeout_seconds=timeout_seconds
        if timeout_seconds is not None
        else defaults.timeout_seconds,
        strict_validation=strict_validation
        if strict_validation is not None
        else defaults.strict_validation,
        metrics_jsonl_path=metrics_jsonl_path
        if metrics_jsonl_path is not None
        else defaults.metrics_jsonl_path,
    )


def _plugin_name(parsed: Any) -> str:
    return parsed.netloc or parsed.path.lstrip("/").split("/", 1)[0]


def _has_host_port(parsed: Any) -> bool:
    try:
        return bool(parsed.hostname and parsed.port)
    except ValueError:
        return False


def _remote_url(
    context: object,
    lmcache_config: object | None,
    extra_config: Mapping[str, Any],
) -> str:
    for source in (context, lmcache_config):
        if source is None:
            continue
        for name in ("remote_url", "url", "remote_storage_url"):
            value = _attr(source, name)
            if value:
                return str(value)
    value = extra_config.get("remote_url") or extra_config.get("url")
    if value:
        return str(value)
    raise ConnectorConfigurationError("BIFROST remote_url is required")


def _endpoint_from_url(
    parsed: Any,
    query: Mapping[str, str],
    extra_config: Mapping[str, Any],
    lmcache_config: object | None,
) -> str:
    endpoint = query.get("endpoint") or _string_from_mapping(
        extra_config,
        "endpoint",
        "bifrost_endpoint",
    )
    if endpoint:
        return _normalize_endpoint(endpoint)

    config_endpoint = _attr(lmcache_config, "endpoint")
    if config_endpoint:
        return _normalize_endpoint(str(config_endpoint))

    if parsed.scheme in ("bifrost", "bifrost+tcp"):
        if not parsed.hostname or parsed.port is None:
            raise ConnectorConfigurationError(
                "bifrost remote URL must include host and port"
            )
        return f"{parsed.hostname}:{parsed.port}"

    raise ConnectorConfigurationError(
        "plugin://bifrost requires endpoint in query or extra_config"
    )


def _normalize_endpoint(endpoint: str) -> str:
    value = endpoint.strip()
    if not value:
        raise ConnectorConfigurationError("endpoint must be non-empty")
    parsed = urlparse(value)
    if parsed.scheme in ("bifrost", "bifrost+tcp"):
        if not parsed.hostname or parsed.port is None:
            raise ConnectorConfigurationError("endpoint URL must include host and port")
        return f"{parsed.hostname}:{parsed.port}"
    return value


def _extra_config(
    context: object,
    lmcache_config: object | None,
) -> Mapping[str, Any]:
    for source in (context, lmcache_config):
        value = _attr(source, "extra_config")
        if isinstance(value, Mapping):
            return value
    return {}


def _query_values(query: str) -> dict[str, str]:
    parsed = parse_qs(query, keep_blank_values=False)
    return {key: values[-1] for key, values in parsed.items() if values}


def _int_setting(
    name: str,
    query: Mapping[str, str],
    extra_config: Mapping[str, Any],
    lmcache_config: object | None,
) -> int | None:
    value = _setting_value(name, query, extra_config, lmcache_config)
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ConnectorConfigurationError(f"{name} must be an integer") from exc


def _float_setting(
    name: str,
    query: Mapping[str, str],
    extra_config: Mapping[str, Any],
    lmcache_config: object | None,
) -> float | None:
    value = _setting_value(name, query, extra_config, lmcache_config)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ConnectorConfigurationError(f"{name} must be a number") from exc


def _bool_setting(
    name: str,
    query: Mapping[str, str],
    extra_config: Mapping[str, Any],
    lmcache_config: object | None,
) -> bool | None:
    value = _setting_value(name, query, extra_config, lmcache_config)
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    lowered = str(value).strip().lower()
    if lowered in ("1", "true", "yes", "on"):
        return True
    if lowered in ("0", "false", "no", "off"):
        return False
    raise ConnectorConfigurationError(f"{name} must be a boolean")


def _string_setting(
    name: str,
    query: Mapping[str, str],
    extra_config: Mapping[str, Any],
    lmcache_config: object | None,
) -> str | None:
    value = _setting_value(name, query, extra_config, lmcache_config)
    if value is None:
        return None
    value = str(value)
    if not value:
        raise ConnectorConfigurationError(f"{name} must be non-empty")
    return value


def _setting_value(
    name: str,
    query: Mapping[str, str],
    extra_config: Mapping[str, Any],
    lmcache_config: object | None,
) -> Any:
    if name in query:
        return query[name]
    if name in extra_config:
        return extra_config[name]
    return _attr(lmcache_config, name)


def _string_from_mapping(mapping: Mapping[str, Any], *names: str) -> str | None:
    for name in names:
        value = mapping.get(name)
        if value:
            return str(value)
    return None


def _attr(source: object | None, name: str) -> Any:
    if source is None:
        return None
    return getattr(source, name, None)


__all__ = ["BifrostConnectorAdapter", "PLUGIN_TYPE", "parse_config"]

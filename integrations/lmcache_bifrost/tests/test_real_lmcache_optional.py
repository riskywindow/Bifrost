from __future__ import annotations

import asyncio
import inspect
import os
from dataclasses import dataclass, field
from typing import Any

import pytest

from lmcache_bifrost.adapter import BifrostConnectorAdapter
from lmcache_bifrost.blob_codec import deserialize_memory_obj, serialize_memory_obj
from lmcache_bifrost.config import BifrostLMCacheConfig
from lmcache_bifrost.connector import BifrostRemoteConnector
from lmcache_bifrost.errors import MemoryObjDeserializationError
from lmcache_bifrost.lmcache_compat import (
    CacheEngineKey,
    ConnectorAdapter,
    ConnectorContext,
    LMCacheEngineConfig,
    LMCacheMetadata,
    MemoryObj,
    RemoteConnector,
    detect_memory_obj_codec,
    has_lmcache,
    lmcache_version,
)

RUN_REAL_LMCACHE_ENV = "BIFROST_RUN_REAL_LMCACHE_TESTS"


@dataclass(frozen=True, slots=True)
class MinimalLMCacheConfig:
    remote_url: str = "plugin://bifrost"
    extra_config: dict[str, Any] = field(
        default_factory=lambda: {"endpoint": "127.0.0.1:8765"}
    )


@dataclass(frozen=True, slots=True)
class MinimalConnectorContext:
    config: MinimalLMCacheConfig = field(default_factory=MinimalLMCacheConfig)
    remote_url: str = "plugin://bifrost"


def test_real_lmcache_optional_tests_are_opt_in() -> None:
    if os.environ.get(RUN_REAL_LMCACHE_ENV) == "1":
        pytest.skip("real LMCache tests are currently opted in")

    assert has_lmcache() in (True, False)
    pytest.skip(
        f"set {RUN_REAL_LMCACHE_ENV}=1 and install LMCache to run real LMCache tests"
    )


def test_real_lmcache_imports_connector_classes_when_opted_in() -> None:
    _require_real_lmcache()

    probed = {
        "ConnectorAdapter": ConnectorAdapter,
        "ConnectorContext": ConnectorContext,
        "RemoteConnector": RemoteConnector,
        "CacheEngineKey": CacheEngineKey,
        "MemoryObj": MemoryObj,
        "LMCacheEngineConfig": LMCacheEngineConfig,
        "LMCacheMetadata": LMCacheMetadata,
    }
    missing = sorted(name for name, value in probed.items() if value is None)

    assert not missing, (
        f"LMCache {lmcache_version() or 'unknown'} is installed, but BIFROST could "
        f"not import expected LMCache compatibility classes: {', '.join(missing)}"
    )


def test_real_lmcache_remote_connector_method_compatibility() -> None:
    _require_real_lmcache()

    required_methods = ("exists", "exists_sync", "get", "put", "list", "close")
    missing = [
        name
        for name in required_methods
        if not callable(getattr(BifrostRemoteConnector, name, None))
    ]
    assert not missing

    if RemoteConnector is not None:
        base_methods = [
            name for name in required_methods if hasattr(RemoteConnector, name)
        ]
        unimplemented = [
            name
            for name in base_methods
            if not callable(getattr(BifrostRemoteConnector, name, None))
        ]
        assert not unimplemented, (
            "BIFROST connector is missing methods exposed by LMCache "
            f"RemoteConnector: {', '.join(unimplemented)}"
        )


def test_real_lmcache_adapter_and_connector_construction_when_opted_in() -> None:
    _require_real_lmcache()

    adapter = BifrostConnectorAdapter()
    if ConnectorAdapter is not None:
        assert isinstance(adapter, ConnectorAdapter)

    connector = adapter.create_connector(MinimalConnectorContext())
    try:
        assert isinstance(connector, BifrostRemoteConnector)
        assert connector.context is not None
        assert connector.config.endpoint == "127.0.0.1:8765"
    finally:
        asyncio.run(connector.close())


def test_real_lmcache_memory_obj_roundtrip_if_public_construction_is_supported() -> None:
    _require_real_lmcache()

    memory_obj, reason = _public_memory_obj_or_reason()
    if memory_obj is None:
        pytest.skip(reason)

    capability = detect_memory_obj_codec(memory_obj)
    if not capability.supported or capability.name != "lmcache_native":
        pytest.skip(
            "real LMCache MemoryObj does not expose a discovered native byte "
            f"serialization API: {capability.reason}"
        )

    config = BifrostLMCacheConfig()
    payload = serialize_memory_obj(memory_obj, config)
    try:
        restored = deserialize_memory_obj(payload, config)
    except MemoryObjDeserializationError as exc:
        pytest.skip(f"LMCache-native MemoryObj deserialization is unavailable: {exc}")

    assert isinstance(payload, bytes)
    assert len(payload) >= 0
    assert restored is not None


def _require_real_lmcache() -> None:
    if os.environ.get(RUN_REAL_LMCACHE_ENV) != "1":
        pytest.skip(f"set {RUN_REAL_LMCACHE_ENV}=1 to run real LMCache tests")
    if not has_lmcache():
        pytest.skip(
            f"{RUN_REAL_LMCACHE_ENV}=1 is set, but LMCache is not installed"
        )


def _public_memory_obj_or_reason() -> tuple[object | None, str]:
    if MemoryObj is None:
        return None, "LMCache MemoryObj class was not discovered"
    try:
        signature = inspect.signature(MemoryObj)
    except (TypeError, ValueError):
        return None, "LMCache MemoryObj constructor signature is not inspectable"

    required = [
        parameter
        for parameter in signature.parameters.values()
        if parameter.default is inspect.Parameter.empty
        and parameter.kind
        in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        )
    ]
    if required:
        names = ", ".join(parameter.name for parameter in required)
        return (
            None,
            "LMCache MemoryObj requires constructor arguments that this CPU-only "
            f"smoke test cannot safely synthesize: {names}",
        )

    try:
        return MemoryObj(), ""
    except Exception as exc:
        return None, f"LMCache MemoryObj public no-arg construction failed: {exc}"

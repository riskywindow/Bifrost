"""Optional LMCache imports and serialization capability probing."""

from __future__ import annotations

from dataclasses import dataclass
import importlib
import importlib.metadata
from types import ModuleType
from typing import Any


@dataclass(frozen=True, slots=True)
class CodecCapability:
    """Detected MemoryObj serialization support."""

    name: str
    supported: bool
    reason: str
    type_info: dict[str, Any] | None = None


_LMCacheModule: ModuleType | None = None
_LMCacheImportError: BaseException | None = None


ConnectorAdapter: type[Any] | None
ConnectorContext: type[Any] | None
RemoteConnector: type[Any] | None
CacheEngineKey: type[Any] | None
MemoryObj: type[Any] | None
LMCacheEngineConfig: type[Any] | None
LMCacheMetadata: type[Any] | None


def _import_lmcache() -> ModuleType | None:
    global _LMCacheImportError, _LMCacheModule
    if _LMCacheModule is not None or _LMCacheImportError is not None:
        return _LMCacheModule
    try:
        _LMCacheModule = importlib.import_module("lmcache")
    except ImportError as exc:
        _LMCacheImportError = exc
        return None
    return _LMCacheModule


def _find_attr(module: ModuleType | None, names: tuple[str, ...]) -> type[Any] | None:
    if module is None:
        return None
    for name in names:
        value = getattr(module, name, None)
        if isinstance(value, type):
            return value
    return None


def _find_module_attr(
    module_names: tuple[str, ...],
    attr_names: tuple[str, ...],
) -> type[Any] | None:
    root = _import_lmcache()
    found = _find_attr(root, attr_names)
    if found is not None:
        return found
    for module_name in module_names:
        try:
            module = importlib.import_module(module_name)
        except ImportError:
            continue
        found = _find_attr(module, attr_names)
        if found is not None:
            return found
    return None


ConnectorAdapter = _find_module_attr(
    (
        "lmcache.v1.storage_backend.connector",
        "lmcache.integration.connector",
        "lmcache.integration.storage_backend",
        "lmcache.v1.storage_backend.remote_connector",
    ),
    ("ConnectorAdapter",),
)
ConnectorContext = _find_module_attr(
    (
        "lmcache.v1.storage_backend.connector",
        "lmcache.integration.connector",
        "lmcache.integration.storage_backend",
        "lmcache.v1.storage_backend.remote_connector",
    ),
    ("ConnectorContext",),
)
RemoteConnector = _find_module_attr(
    (
        "lmcache.v1.storage_backend.connector.base_connector",
        "lmcache.integration.connector",
        "lmcache.integration.storage_backend",
        "lmcache.v1.storage_backend.connector",
        "lmcache.v1.storage_backend.remote_connector",
    ),
    ("RemoteConnector",),
)
CacheEngineKey = _find_module_attr(
    (
        "lmcache.utils",
        "lmcache.v1.cache_engine",
        "lmcache.v1.cache_engine.cache_engine",
        "lmcache.cache_engine",
    ),
    ("CacheEngineKey",),
)
MemoryObj = _find_module_attr(
    (
        "lmcache.v1.memory_management",
        "lmcache.v1.memory_management.memory_allocator",
        "lmcache.memory_management",
    ),
    ("MemoryObj",),
)
LMCacheEngineConfig = _find_module_attr(
    (
        "lmcache.config",
        "lmcache.v1.config",
        "lmcache.v1.cache_engine",
    ),
    ("LMCacheEngineConfig",),
)
LMCacheMetadata = _find_module_attr(
    (
        "lmcache.v1.metadata",
        "lmcache.config",
        "lmcache.v1.config",
        "lmcache.v1.cache_engine",
    ),
    ("LMCacheMetadata",),
)


def has_lmcache() -> bool:
    """Return whether the LMCache package can be imported."""

    return _import_lmcache() is not None


def lmcache_version() -> str | None:
    """Return the installed LMCache version, if discoverable."""

    module = _import_lmcache()
    if module is None:
        return None
    version = getattr(module, "__version__", None)
    if version:
        return str(version)
    try:
        return importlib.metadata.version("lmcache")
    except importlib.metadata.PackageNotFoundError:
        return None


def detect_memory_obj_codec(memory_obj: object) -> CodecCapability:
    """Detect a safe MemoryObj serialization route without guessing semantics."""

    native_method = _native_serializer(memory_obj)
    if native_method is not None:
        return CodecCapability(
            name="lmcache_native",
            supported=True,
            reason=f"memory object exposes {native_method}",
            type_info=_type_info(memory_obj, {"serializer": native_method}),
        )

    if _is_fake_memory_obj(memory_obj):
        return CodecCapability(
            name="pickle_fallback",
            supported=True,
            reason="fake MemoryObj fixture may use test-only pickle fallback",
            type_info=_type_info(memory_obj, {"serializer": "pickle"}),
        )

    return CodecCapability(
        name="unsupported",
        supported=False,
        reason="LMCache-native MemoryObj serialization API was not discovered",
        type_info=_type_info(memory_obj, None),
    )


def serialize_with_lmcache_native(memory_obj: object) -> bytes | None:
    """Serialize with a discovered LMCache-native object method, if present."""

    method_name = _native_serializer(memory_obj)
    if method_name is None:
        return None
    method = getattr(memory_obj, method_name)
    try:
        value = method()
    except TypeError:
        return None
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value)
    return None


def deserialize_with_lmcache_native(
    payload: bytes,
    type_info: dict[str, Any] | None = None,
) -> object | None:
    """Deserialize with a discovered LMCache-native function, if present."""

    del type_info
    for module_name, function_names in (
        ("lmcache", ("deserialize_memory_obj", "load_memory_obj")),
        ("lmcache.v1.memory_management", ("deserialize_memory_obj", "load_memory_obj")),
        ("lmcache.storage_backend", ("deserialize_memory_obj", "load_memory_obj")),
    ):
        try:
            module = importlib.import_module(module_name)
        except ImportError:
            continue
        for function_name in function_names:
            function = getattr(module, function_name, None)
            if callable(function):
                try:
                    return function(payload)
                except TypeError:
                    continue
    return None


def _native_serializer(memory_obj: object) -> str | None:
    for name in ("to_bytes", "serialize", "dumps"):
        attr = getattr(memory_obj, name, None)
        if attr is None or not callable(attr):
            continue
        try:
            value = attr()
        except TypeError:
            continue
        if isinstance(value, (bytes, bytearray, memoryview)):
            return name
    return None


def _type_info(
    memory_obj: object,
    extra: dict[str, Any] | None,
) -> dict[str, Any]:
    result = {
        "module": memory_obj.__class__.__module__,
        "qualname": memory_obj.__class__.__qualname__,
    }
    if extra:
        result.update(extra)
    return result


def _is_fake_memory_obj(memory_obj: object) -> bool:
    module = memory_obj.__class__.__module__
    name = memory_obj.__class__.__qualname__
    return module.endswith("tests.fakes") and name == "FakeMemoryObj"


__all__ = [
    "CacheEngineKey",
    "CodecCapability",
    "ConnectorAdapter",
    "ConnectorContext",
    "LMCacheEngineConfig",
    "LMCacheMetadata",
    "MemoryObj",
    "RemoteConnector",
    "deserialize_with_lmcache_native",
    "detect_memory_obj_codec",
    "has_lmcache",
    "lmcache_version",
    "serialize_with_lmcache_native",
]

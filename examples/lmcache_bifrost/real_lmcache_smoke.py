#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib
import inspect
import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "bifrost_py"))
sys.path.insert(0, str(REPO_ROOT / "integrations" / "lmcache_bifrost"))

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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Probe the optional real LMCache BIFROST connector surface."
    )
    parser.add_argument(
        "--compat-only",
        action="store_true",
        help="exit 0 when only compatibility probing is possible",
    )
    parser.add_argument(
        "--memoryobj-factory",
        help=(
            "optional module:function returning a CPU-safe real LMCache MemoryObj "
            "for native serialization smoke testing"
        ),
    )
    parser.add_argument("--json", action="store_true", help="print a JSON summary")
    args = parser.parse_args(argv)

    summary = _run(args)
    if args.json:
        print(json.dumps(summary, sort_keys=True))
    else:
        print(_format_human_summary(summary))

    if summary["status"] == "pass":
        return 0
    if args.compat_only and summary["status"] == "compatibility only":
        return 0
    return 1


def _run(args: argparse.Namespace) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "status": "fail",
        "lmcache_installed": has_lmcache(),
        "lmcache_version": lmcache_version(),
        "imports": {},
        "connector_methods": {},
        "adapter_constructed": False,
        "connector_constructed": False,
        "memory_obj_roundtrip": "not attempted",
        "memory_obj_skip_reason": None,
        "errors": [],
    }
    _probe_imports(summary)
    _probe_connector_methods(summary)
    _probe_adapter_and_connector(summary)

    if not summary["lmcache_installed"]:
        summary["status"] = "compatibility only"
        summary["memory_obj_skip_reason"] = "LMCache is not installed"
        return summary

    memory_obj, reason = _memory_obj_for_smoke(args.memoryobj_factory)
    if memory_obj is None:
        summary["status"] = "compatibility only"
        summary["memory_obj_skip_reason"] = reason
        return summary

    capability = detect_memory_obj_codec(memory_obj)
    if not capability.supported or capability.name != "lmcache_native":
        summary["status"] = "compatibility only"
        summary["memory_obj_skip_reason"] = (
            "real LMCache MemoryObj native byte serialization is unavailable: "
            f"{capability.reason}"
        )
        return summary

    try:
        payload = serialize_memory_obj(memory_obj, BifrostLMCacheConfig())
        restored = deserialize_memory_obj(payload, BifrostLMCacheConfig())
    except MemoryObjDeserializationError as exc:
        summary["status"] = "compatibility only"
        summary["memory_obj_skip_reason"] = (
            "LMCache-native MemoryObj deserialization is unavailable: " + str(exc)
        )
        return summary
    except Exception as exc:
        summary["errors"].append(f"MemoryObj roundtrip failed: {exc}")
        return summary

    summary["memory_obj_roundtrip"] = "pass"
    summary["memory_obj_payload_bytes"] = len(payload)
    summary["memory_obj_restored_type"] = _type_name(restored)
    summary["status"] = "pass"
    return summary


def _probe_imports(summary: dict[str, Any]) -> None:
    classes = {
        "ConnectorAdapter": ConnectorAdapter,
        "ConnectorContext": ConnectorContext,
        "RemoteConnector": RemoteConnector,
        "CacheEngineKey": CacheEngineKey,
        "MemoryObj": MemoryObj,
        "LMCacheEngineConfig": LMCacheEngineConfig,
        "LMCacheMetadata": LMCacheMetadata,
    }
    summary["imports"] = {
        name: _type_name(value) if value is not None else None
        for name, value in classes.items()
    }


def _probe_connector_methods(summary: dict[str, Any]) -> None:
    required = ("exists", "exists_sync", "get", "put", "list", "close")
    summary["connector_methods"] = {
        name: callable(getattr(BifrostRemoteConnector, name, None))
        for name in required
    }
    if RemoteConnector is not None:
        summary["lmcache_remote_connector_methods"] = {
            name: hasattr(RemoteConnector, name) for name in required
        }


def _probe_adapter_and_connector(summary: dict[str, Any]) -> None:
    try:
        adapter = BifrostConnectorAdapter()
        summary["adapter_constructed"] = True
    except Exception as exc:
        summary["errors"].append(f"adapter construction failed: {exc}")
        return

    try:
        connector = adapter.create_connector(MinimalConnectorContext())
        summary["connector_constructed"] = isinstance(
            connector,
            BifrostRemoteConnector,
        )
        summary["connector_config"] = asdict(connector.config)
    except Exception as exc:
        summary["errors"].append(f"connector construction failed: {exc}")


def _memory_obj_for_smoke(factory: str | None) -> tuple[object | None, str]:
    if factory:
        try:
            return _call_factory(factory), ""
        except Exception as exc:
            return None, f"MemoryObj factory failed: {exc}"
    return _public_memory_obj_or_reason()


def _call_factory(spec: str) -> object:
    module_name, separator, function_name = spec.partition(":")
    if not separator or not module_name or not function_name:
        raise ValueError("--memoryobj-factory must be module:function")
    module = importlib.import_module(module_name)
    function = getattr(module, function_name)
    if not callable(function):
        raise TypeError(f"{spec} is not callable")
    return function()


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
            f"smoke script cannot safely synthesize: {names}",
        )

    try:
        return MemoryObj(), ""
    except Exception as exc:
        return None, f"LMCache MemoryObj public no-arg construction failed: {exc}"


def _format_human_summary(summary: dict[str, Any]) -> str:
    lines = [
        f"status: {summary['status']}",
        f"lmcache installed: {summary['lmcache_installed']}",
        f"lmcache version: {summary['lmcache_version'] or 'unknown'}",
        f"adapter constructed: {summary['adapter_constructed']}",
        f"connector constructed: {summary['connector_constructed']}",
        f"memory obj roundtrip: {summary['memory_obj_roundtrip']}",
    ]
    if summary.get("memory_obj_skip_reason"):
        lines.append(f"memory obj skip reason: {summary['memory_obj_skip_reason']}")
    errors = summary.get("errors") or []
    for error in errors:
        lines.append(f"error: {error}")
    return "\n".join(lines)


def _type_name(value: object) -> str:
    if isinstance(value, type):
        return f"{value.__module__}.{value.__qualname__}"
    return f"{value.__class__.__module__}.{value.__class__.__qualname__}"


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict, dataclass
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "bifrost_py"))
sys.path.insert(0, str(REPO_ROOT / "integrations" / "lmcache_bifrost"))

from lmcache_bifrost.config import BifrostLMCacheConfig
from lmcache_bifrost.connector import BifrostRemoteConnector
from lmcache_bifrost.key_codec import opaque_engine_key_hash, stable_key_repr
from lmcache_bifrost.lmcache_compat import has_lmcache, lmcache_version


@dataclass(frozen=True, slots=True)
class DemoCacheEngineKey:
    model_id: str
    block_hash: str
    tokens: tuple[int, ...]
    extra: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class DemoMemoryObj:
    payload: bytes
    dtype: str = "float16"
    shape: tuple[int, ...] = (1,)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run a BIFROST LMCache remote-storage plugin roundtrip."
    )
    parser.add_argument("--endpoint", required=True, help="BIFROST daemon HOST:PORT")
    parser.add_argument(
        "--allow-pickle-fallback",
        action="store_true",
        help="enable test-only pickle serialization for fake/demo MemoryObj values",
    )
    parser.add_argument(
        "--real-lmcache",
        action="store_true",
        help="use real LMCache objects when the installed LMCache API supports this demo",
    )
    parser.add_argument(
        "--bifrost-store",
        help="optional path to bifrost-store for fsck diagnostics",
    )
    parser.add_argument("--json", action="store_true", help="print a JSON summary")
    args = parser.parse_args(argv)

    try:
        summary = asyncio.run(_run(args))
    except Exception as exc:
        summary = _base_summary(args.endpoint)
        summary["status"] = "fail"
        summary["error"] = str(exc)
        if args.json:
            print(json.dumps(summary, sort_keys=True))
        else:
            print(_format_human_summary(summary), file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(summary, sort_keys=True))
    else:
        print(_format_human_summary(summary))
    return 0 if summary["status"] == "pass" else 1


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    _validate_endpoint(args.endpoint)
    key, memory_obj = _make_objects(args.real_lmcache)
    key_repr = stable_key_repr(key)
    key_hash = opaque_engine_key_hash(key)
    summary = _base_summary(args.endpoint)
    summary.update(
        {
            "key_repr": key_repr,
            "opaque_engine_key_hash": key_hash,
        }
    )

    config = BifrostLMCacheConfig(
        endpoint=args.endpoint,
        allow_pickle_fallback=args.allow_pickle_fallback,
        timeout_seconds=5,
    )
    connector = BifrostRemoteConnector(config)
    try:
        await connector.put(key, memory_obj)
        summary["put_success"] = True

        exists_result = await connector.exists(key)
        summary["exists_result"] = exists_result

        fetched = await connector.get(key)
        summary["get_success"] = fetched is not None
        summary["payload_roundtrip_match"] = _payload_bytes(fetched) == _payload_bytes(
            memory_obj
        )

        listed = await connector.list()
        summary["list_count"] = len(listed)

        object_id = await _object_id_for_key(connector, key_hash)
        if object_id is not None:
            summary["object_id"] = object_id

        stats = await _store_stats(connector)
        if stats is not None:
            summary["store_stats"] = stats
    finally:
        await connector.close()

    fsck = _fsck_status(args.endpoint, args.bifrost_store)
    if fsck is not None:
        summary["fsck_status"] = fsck

    summary["status"] = (
        "pass"
        if summary["put_success"]
        and summary["exists_result"] is True
        and summary["get_success"]
        and summary["list_count"] >= 1
        and summary["payload_roundtrip_match"]
        else "fail"
    )
    return summary


def _base_summary(endpoint: str) -> dict[str, Any]:
    return {
        "status": "fail",
        "endpoint": endpoint,
        "key_repr": None,
        "opaque_engine_key_hash": None,
        "object_id": None,
        "put_success": False,
        "exists_result": False,
        "get_success": False,
        "list_count": 0,
        "payload_roundtrip_match": False,
        "fsck_status": None,
    }


def _make_objects(real_lmcache: bool) -> tuple[object, object]:
    if real_lmcache:
        if not has_lmcache():
            raise RuntimeError("real LMCache mode requested, but LMCache is not installed")
        version = lmcache_version() or "unknown"
        raise RuntimeError(
            "real LMCache mode is version-sensitive and is not constructible by this "
            f"standalone demo for LMCache {version}; use the fake mode or an LMCache "
            "serving stack that loads the plugin"
        )
    return (
        DemoCacheEngineKey(
            model_id="demo-tiny",
            block_hash="plugin-roundtrip",
            tokens=(101, 202, 303, 404),
            extra=(("tenant", "local-demo"),),
        ),
        DemoMemoryObj(
            payload=b"lmcache-owned-opaque-bytes:plugin-roundtrip",
            shape=(1, 4, 8),
        ),
    )


async def _object_id_for_key(
    connector: BifrostRemoteConnector,
    key_hash: str,
) -> str | None:
    query = getattr(connector.client, "query_by_opaque_key_hash", None)
    if not callable(query):
        return None
    candidates = await connector._maybe_await(  # noqa: SLF001 - diagnostic example.
        query(
            connector.config.engine_name,
            connector.config.integration_name,
            key_hash,
        )
    )
    for candidate in candidates:
        object_id = getattr(candidate, "object_id", None)
        if isinstance(object_id, str) and object_id:
            return object_id
    return None


async def _store_stats(connector: BifrostRemoteConnector) -> dict[str, Any] | None:
    stats = getattr(connector.client, "stats", None)
    if not callable(stats):
        return None
    try:
        value = await connector._maybe_await(stats())  # noqa: SLF001 - diagnostic example.
    except Exception:
        return None
    if hasattr(value, "__dataclass_fields__"):
        return asdict(value)
    return value if isinstance(value, dict) else None


def _fsck_status(endpoint: str, bifrost_store: str | None) -> str | None:
    binary = Path(bifrost_store) if bifrost_store else _find_binary("bifrost-store")
    if binary is None or not binary.exists():
        return None
    result = subprocess.run(
        [str(binary), "fsck", "--endpoint", endpoint, "--check", "--json"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if result.returncode != 0:
        return "failed"
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return "unparseable"
    status = payload.get("status")
    return str(status) if status is not None else "unknown"


def _find_binary(name: str) -> Path | None:
    for candidate in (
        REPO_ROOT / "bifrostd" / "target" / "debug" / name,
        REPO_ROOT / "target" / "debug" / name,
    ):
        if candidate.exists():
            return candidate
    found = shutil.which(name)
    return Path(found) if found else None


def _payload_bytes(value: object | None) -> bytes | None:
    payload = getattr(value, "payload", None)
    return bytes(payload) if isinstance(payload, (bytes, bytearray, memoryview)) else None


def _validate_endpoint(endpoint: str) -> None:
    host, separator, port_text = endpoint.rpartition(":")
    if not separator or not host:
        raise ValueError("--endpoint must be HOST:PORT")
    port = int(port_text)
    if port <= 0 or port > 65535:
        raise ValueError("--endpoint port must be between 1 and 65535")


def _format_human_summary(summary: dict[str, Any]) -> str:
    lines = [
        "BIFROST LMCache plugin roundtrip",
        f"endpoint: {summary['endpoint']}",
        f"key_repr: {summary['key_repr']}",
        f"opaque_engine_key_hash: {summary['opaque_engine_key_hash']}",
        f"object_id: {summary.get('object_id')}",
        f"put_success: {str(summary['put_success']).lower()}",
        f"exists_result: {str(summary['exists_result']).lower()}",
        f"get_success: {str(summary['get_success']).lower()}",
        f"list_count: {summary['list_count']}",
        "payload_roundtrip_match: "
        f"{str(summary['payload_roundtrip_match']).lower()}",
        f"fsck_status: {summary.get('fsck_status')}",
    ]
    stats = summary.get("store_stats")
    if isinstance(stats, dict):
        lines.append(f"store_object_count: {stats.get('object_count')}")
        lines.append(f"store_verified_count: {stats.get('verified_count')}")
    if summary.get("error"):
        lines.append(f"error: {summary['error']}")
    lines.append(f"result: {summary['status']}")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())

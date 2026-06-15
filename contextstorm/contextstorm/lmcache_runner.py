"""LMCache connector workloads for ContextStorm Phase 5."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .lmcache_metrics import LMCacheOperationMetrics
from .runner import (
    ContextStormError,
    REPO_ROOT,
    _environment,
    _find_binary,
    _free_port,
    _load_simple_yaml,
    _parse_json_stdout,
    _resolve_contextstorm_path,
    _stop_daemon,
    _wait_for_endpoint,
)
from .store_runner import _parse_size

BIFROST_PY = REPO_ROOT / "bifrost_py"
LMCACHE_INTEGRATION = REPO_ROOT / "integrations" / "lmcache_bifrost"
for path in (BIFROST_PY, LMCACHE_INTEGRATION):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from lmcache_bifrost.blob_codec import (  # noqa: E402
    deserialize_memory_obj,
    serialize_memory_obj,
)
from lmcache_bifrost.config import BifrostLMCacheConfig  # noqa: E402
from lmcache_bifrost.connector import BifrostRemoteConnector  # noqa: E402
from lmcache_bifrost.errors import BifrostLMCacheValidationError  # noqa: E402
from lmcache_bifrost.key_codec import opaque_engine_key_hash  # noqa: E402
from bifrost_client.models import ObjectSummary, PutResult, StoredObject  # noqa: E402


LMCACHE_OPERATIONS = {
    "put",
    "exists",
    "get",
    "list",
    "stats",
    "fsck",
    "fake_lmcache_connector_roundtrip",
    "fake_lmcache_connector_repeated_get",
    "fake_lmcache_connector_batched_ops",
    "fake_lmcache_connector_corrupt_object",
    "real_lmcache_connector_smoke",
    "vllm_lmcache_smoke",
}
OPTIONAL_OPERATIONS = {
    "real_lmcache_connector_smoke",
    "vllm_lmcache_smoke",
}
LMCACHE_SCENARIO_DETECTION_OPERATIONS = {
    "exists",
    "fake_lmcache_connector_roundtrip",
    "fake_lmcache_connector_repeated_get",
    "fake_lmcache_connector_batched_ops",
    "real_lmcache_connector_smoke",
    "vllm_lmcache_smoke",
}


@dataclass(frozen=True, slots=True)
class FakeCacheEngineKey:
    model_id: str
    block_hash: str
    tokens: tuple[int, ...]
    extra: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class FakeMemoryObj:
    payload: bytes
    dtype: str = "float16"
    shape: tuple[int, ...] = (1,)


@dataclass(frozen=True)
class LMCacheScenario:
    name: str
    object_count: int
    payload_size_bytes: int
    chunk_size_bytes: int
    operations: tuple[str, ...]
    repetitions: int
    timeout_seconds: int
    workload: str = "lmcache"
    repeated_get_count: int = 3
    allow_pickle_fallback: bool = True
    real_lmcache_opt_in_env: str = "BIFROST_RUN_REAL_LMCACHE_CONTEXTSTORM"
    vllm_opt_in_env: str = "BIFROST_RUN_VLLM_LMCACHE_CONTEXTSTORM"


def is_lmcache_scenario(path: Path) -> bool:
    data = _load_simple_yaml(path)
    operations = {str(op) for op in data.get("operations", [])}
    return (
        bool(operations & LMCACHE_SCENARIO_DETECTION_OPERATIONS)
        or str(data.get("workload", "")) == "lmcache"
    )


def load_lmcache_scenario(path: Path) -> LMCacheScenario:
    data = _load_simple_yaml(path)
    operations = tuple(str(op) for op in data.get("operations", ["fake_lmcache_connector_roundtrip"]))
    unknown = sorted(set(operations) - LMCACHE_OPERATIONS)
    if unknown:
        raise ContextStormError(f"unsupported LMCache operations in {path}: {unknown}")
    return LMCacheScenario(
        name=str(data["name"]),
        workload=str(data.get("workload", "lmcache")),
        object_count=int(data.get("object_count", 1)),
        payload_size_bytes=_parse_size(data.get("payload_size_bytes", data.get("object_size_bytes", 65536))),
        chunk_size_bytes=_parse_size(data.get("chunk_size_bytes", 262144)),
        operations=operations,
        repetitions=int(data.get("repetitions", 1)),
        timeout_seconds=int(data.get("timeout_seconds", 60)),
        repeated_get_count=int(data.get("repeated_get_count", 3)),
        allow_pickle_fallback=bool(data.get("allow_pickle_fallback", True)),
        real_lmcache_opt_in_env=str(
            data.get("real_lmcache_opt_in_env", "BIFROST_RUN_REAL_LMCACHE_CONTEXTSTORM")
        ),
        vllm_opt_in_env=str(
            data.get("vllm_opt_in_env", "BIFROST_RUN_VLLM_LMCACHE_CONTEXTSTORM")
        ),
    )


def run_lmcache_scenario(
    scenario_path: Path,
    *,
    runs_root: Path | None = None,
    run_id: str | None = None,
) -> Path:
    scenario_path = _resolve_contextstorm_path(scenario_path)
    scenario = load_lmcache_scenario(scenario_path)
    needs_daemon = any(operation not in OPTIONAL_OPERATIONS for operation in scenario.operations)
    daemon_bin = _find_binary("bifrost-daemon") if needs_daemon else None
    store_bin = _find_binary("bifrost-store") if needs_daemon else None
    if needs_daemon:
        missing = [
            name
            for name, value in {
                "bifrost-daemon": daemon_bin,
                "bifrost-store": store_bin,
            }.items()
            if value is None
        ]
        if missing:
            raise ContextStormError(
                "missing Rust binaries: "
                + ", ".join(missing)
                + ". Build with `cargo build --bins` in bifrostd."
            )

    runs_root = runs_root or REPO_ROOT / "runs"
    run_id = run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = runs_root / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    for name in ("inputs", "outputs", "traces", "commands"):
        (run_dir / name).mkdir()
    shutil.copyfile(scenario_path, run_dir / "scenario.yaml")

    run_record: dict[str, Any] = {
        "schema_version": "contextstorm.run.v1",
        "benchmark_kind": "lmcache",
        "scenario": _scenario_to_dict(scenario),
        "started_at_unix_ms": int(time.time() * 1000),
        "environment": _environment(),
        "operations": [],
        "notes": {
            "cpu_only": True,
            "requires_gpu": False,
            "requires_root": False,
            "external_downloads": False,
            "real_lmcache_required_by_default": False,
            "vllm_required_by_default": False,
            "raw_vllm_connector": False,
        },
    }

    temp_root = tempfile.TemporaryDirectory(prefix="contextstorm-lmcache-")
    daemon: dict[str, Any] | None = None
    endpoint: str | None = None
    try:
        if needs_daemon:
            assert daemon_bin is not None
            endpoint = f"127.0.0.1:{_free_port()}"
            trace_path = run_dir / "traces" / "daemon_primary.jsonl"
            spool = Path(temp_root.name) / "store_primary"
            command = [
                str(daemon_bin),
                "--listen",
                endpoint,
                "--spool",
                str(spool),
                "--trace-jsonl",
                str(trace_path),
            ]
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            daemon = {
                "path_name": "primary",
                "command": command,
                "trace_jsonl": str(trace_path),
                "process": process,
            }
            _wait_for_endpoint(endpoint, scenario.timeout_seconds)

        for repetition in range(scenario.repetitions):
            for operation in scenario.operations:
                result = asyncio.run(
                    _run_lmcache_operation(
                        scenario=scenario,
                        operation=operation,
                        repetition=repetition,
                        endpoint=endpoint,
                        store_bin=store_bin,
                    )
                )
                path = run_dir / "commands" / f"{operation}_{repetition:03d}.json"
                path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
                run_record["operations"].append(result)
    finally:
        if daemon is not None:
            run_record["daemons"] = [_stop_daemon(daemon, run_dir)]
        else:
            run_record["daemons"] = []
        temp_root.cleanup()
        run_record["finished_at_unix_ms"] = int(time.time() * 1000)
        (run_dir / "run.json").write_text(
            json.dumps(run_record, indent=2, sort_keys=True) + "\n"
        )
        from .lmcache_report import write_lmcache_report

        write_lmcache_report(run_dir)
    return run_dir


async def _run_lmcache_operation(
    *,
    scenario: LMCacheScenario,
    operation: str,
    repetition: int,
    endpoint: str | None,
    store_bin: Path | None,
) -> dict[str, Any]:
    if operation == "real_lmcache_connector_smoke":
        return _optional_record(
            scenario,
            operation,
            repetition,
            scenario.real_lmcache_opt_in_env,
            "real LMCache ContextStorm smoke is opt-in",
            [
                sys.executable,
                str(REPO_ROOT / "examples" / "lmcache_bifrost" / "real_lmcache_smoke.py"),
                "--compat-only",
                "--json",
            ],
        )
    if operation == "vllm_lmcache_smoke":
        return _optional_record(
            scenario,
            operation,
            repetition,
            scenario.vllm_opt_in_env,
            "vLLM plus LMCache ContextStorm smoke is opt-in",
            [
                sys.executable,
                str(REPO_ROOT / "examples" / "lmcache_bifrost" / "vllm_lmcache_smoke.py"),
                "--json",
            ],
        )
    if endpoint is None or store_bin is None:
        raise ContextStormError(f"{operation} requires a local BIFROST daemon")

    started = time.perf_counter()
    config = BifrostLMCacheConfig(
        endpoint=endpoint,
        chunk_size=scenario.chunk_size_bytes,
        allow_pickle_fallback=scenario.allow_pickle_fallback,
        timeout_seconds=float(scenario.timeout_seconds),
    )
    connector = BifrostRemoteConnector(config)
    keys, objects = _fake_items(scenario, repetition)
    summary: dict[str, Any] = {"status": "fail", "operation": operation}
    try:
        if operation in {"put", "fake_lmcache_connector_roundtrip"}:
            summary = await _put_exists_get_list(connector, config, keys, objects)
        elif operation == "exists":
            await _put_all(connector, keys, objects)
            summary = await _exists_all(connector, keys)
        elif operation == "get":
            await _put_all(connector, keys, objects)
            summary = await _get_all(connector, config, keys, objects)
        elif operation == "list":
            await _put_all(connector, keys, objects)
            summary = await _list(connector, len(keys))
        elif operation == "stats":
            await _put_all(connector, keys, objects)
            summary = await _stats(connector)
        elif operation == "fsck":
            await _put_all(connector, keys, objects)
            summary = _fsck(store_bin, endpoint, scenario.timeout_seconds)
        elif operation == "fake_lmcache_connector_repeated_get":
            await _put_all(connector, keys, objects)
            summary = await _repeated_get(connector, config, keys, objects, scenario.repeated_get_count)
        elif operation == "fake_lmcache_connector_batched_ops":
            summary = await _batched_ops(connector, config, keys, objects)
        elif operation == "fake_lmcache_connector_corrupt_object":
            summary = await _corrupt_object_probe(scenario, repetition)
        else:  # pragma: no cover - guarded by load_lmcache_scenario
            raise ContextStormError(f"unsupported LMCache operation: {operation}")
        metrics = _metrics_from_summary(operation, repetition, summary)
        exit_code = 0 if metrics.success else 1
    except Exception as exc:
        reason = _reason_code(exc)
        summary = {"status": "fail", "error": str(exc)}
        metrics = LMCacheOperationMetrics(
            operation=operation,
            repetition=repetition,
            success=False,
            reason_code=reason,
            failures=[
                {
                    "operation": operation,
                    "reason_code": reason,
                    "message": str(exc),
                }
            ],
        )
        exit_code = 1
    finally:
        close_started = time.perf_counter()
        await connector.close()
        close_ms = _duration_ms_float(close_started)
    metrics.connector_close_ms = close_ms
    summary.setdefault("elapsed_ms", _duration_ms_float(started))
    return {
        "operation": operation,
        "repetition": repetition,
        "command": [],
        "exit_code": exit_code,
        "stdout": json.dumps(summary, sort_keys=True),
        "stderr": "" if exit_code == 0 else str(summary.get("error", "")),
        "parsed_stdout": summary,
        "metrics": metrics.to_dict(),
    }


async def _put_exists_get_list(
    connector: BifrostRemoteConnector,
    config: BifrostLMCacheConfig,
    keys: list[FakeCacheEngineKey],
    objects: list[FakeMemoryObj],
) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    summary.update(await _put_all(connector, keys, objects))
    summary.update(await _exists_all(connector, keys))
    summary.update(await _get_all(connector, config, keys, objects))
    summary.update(await _list(connector, len(keys)))
    summary.update(await _missing_probe(connector))
    stats = await _stats(connector)
    summary.update(stats)
    summary["status"] = "pass" if _roundtrip_success(summary, len(keys)) else "fail"
    return summary


async def _put_all(
    connector: BifrostRemoteConnector,
    keys: list[FakeCacheEngineKey],
    objects: list[FakeMemoryObj],
) -> dict[str, Any]:
    started = time.perf_counter()
    for key, memory_obj in zip(keys, objects, strict=True):
        await connector.put(key, memory_obj)
    return {
        "status": "pass",
        "connector_put_ms": _duration_ms_float(started),
        "object_count": len(keys),
        "bytes_put": sum(len(memory_obj.payload) for memory_obj in objects),
    }


async def _exists_all(
    connector: BifrostRemoteConnector,
    keys: list[FakeCacheEngineKey],
) -> dict[str, Any]:
    started = time.perf_counter()
    results = [await connector.exists(key) for key in keys]
    exists_true = all(results)
    return {
        "status": "pass" if exists_true else "fail",
        "connector_exists_ms": _duration_ms_float(started),
        "exists_true_after_put": exists_true,
    }


async def _get_all(
    connector: BifrostRemoteConnector,
    config: BifrostLMCacheConfig,
    keys: list[FakeCacheEngineKey],
    objects: list[FakeMemoryObj],
) -> dict[str, Any]:
    serialize_started = time.perf_counter()
    payloads = [serialize_memory_obj(memory_obj, config) for memory_obj in objects]
    serialization_ms = _duration_ms_float(serialize_started)
    started = time.perf_counter()
    fetched = [await connector.get(key) for key in keys]
    get_ms = _duration_ms_float(started)
    deserialize_started = time.perf_counter()
    for payload in payloads:
        deserialize_memory_obj(payload, config)
    deserialization_ms = _duration_ms_float(deserialize_started)
    matches = [item == expected for item, expected in zip(fetched, objects, strict=True)]
    all_match = all(matches)
    return {
        "status": "pass" if all_match else "fail",
        "connector_get_ms": get_ms,
        "serialization_ms": serialization_ms,
        "deserialization_ms": deserialization_ms,
        "bytes_get": sum(len(memory_obj.payload) for memory_obj in fetched if isinstance(memory_obj, FakeMemoryObj)),
        "roundtrip_match_count": sum(1 for match in matches if match),
        "all_fake_roundtrips_match": all_match,
    }


async def _list(connector: BifrostRemoteConnector, expected_count: int) -> dict[str, Any]:
    started = time.perf_counter()
    listed = await connector.list()
    contains_expected = len(listed) >= expected_count
    return {
        "status": "pass" if contains_expected else "fail",
        "connector_list_ms": _duration_ms_float(started),
        "list_count": len(listed),
        "list_contains_expected_count": contains_expected,
    }


async def _missing_probe(connector: BifrostRemoteConnector) -> dict[str, Any]:
    missing = FakeCacheEngineKey("contextstorm-fake", "missing", (999999,))
    fetched = await connector.get(missing)
    exists = await connector.exists(missing)
    return {
        "missing_count": 1 if fetched is None and exists is False else 0,
        "missing_returns_none": fetched is None and exists is False,
    }


async def _stats(connector: BifrostRemoteConnector) -> dict[str, Any]:
    stats = getattr(connector.client, "stats", None)
    if not callable(stats):
        return {"bifrost_store_object_count": 0}
    value = await connector._maybe_await(stats())  # noqa: SLF001 - workload diagnostic.
    if hasattr(value, "__dataclass_fields__"):
        payload = asdict(value)
    elif isinstance(value, dict):
        payload = value
    else:
        payload = {}
    return {
        "status": "pass",
        "bifrost_store_object_count": int(payload.get("object_count") or 0),
        "store_stats": payload,
    }


def _fsck(store_bin: Path, endpoint: str, timeout_seconds: int) -> dict[str, Any]:
    started = time.perf_counter()
    completed = subprocess.run(
        [str(store_bin), "fsck", "--check", "--json", "--endpoint", endpoint],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout_seconds,
        check=False,
    )
    parsed = _parse_json_stdout(completed.stdout)
    status = str(parsed.get("status") or "failed")
    return {
        "status": "pass" if completed.returncode == 0 and status == "clean" else "fail",
        "fsck_status": status,
        "fsck_clean": completed.returncode == 0 and status == "clean",
        "fsck_duration_ms": _duration_ms_float(started),
        "fsck_stdout": parsed,
        "error": completed.stderr if completed.returncode != 0 else None,
    }


async def _repeated_get(
    connector: BifrostRemoteConnector,
    config: BifrostLMCacheConfig,
    keys: list[FakeCacheEngineKey],
    objects: list[FakeMemoryObj],
    count: int,
) -> dict[str, Any]:
    aggregate = {
        "connector_get_ms": 0.0,
        "bytes_get": 0,
        "roundtrip_match_count": 0,
        "all_fake_roundtrips_match": True,
    }
    for _ in range(count):
        result = await _get_all(connector, config, keys, objects)
        aggregate["connector_get_ms"] += float(result["connector_get_ms"])
        aggregate["bytes_get"] += int(result["bytes_get"])
        aggregate["roundtrip_match_count"] += int(result["roundtrip_match_count"])
        aggregate["all_fake_roundtrips_match"] = bool(
            aggregate["all_fake_roundtrips_match"]
        ) and bool(result["all_fake_roundtrips_match"])
    aggregate["object_count"] = len(keys)
    aggregate["status"] = "pass" if aggregate["all_fake_roundtrips_match"] else "fail"
    return aggregate


async def _batched_ops(
    connector: BifrostRemoteConnector,
    config: BifrostLMCacheConfig,
    keys: list[FakeCacheEngineKey],
    objects: list[FakeMemoryObj],
) -> dict[str, Any]:
    support_put = getattr(connector, "support_batched_put", lambda: False)
    support_contains = getattr(connector, "support_batched_contains", lambda: False)
    support_get = getattr(connector, "support_batched_get", lambda: False)
    if not (support_put() and support_contains() and support_get()):
        return {
            "status": "pass",
            "skipped": True,
            "skip_reason": "batched LMCache connector methods are not available",
        }
    started = time.perf_counter()
    await connector.batched_put(list(zip(keys, objects, strict=True)))
    batched_put_ms = _duration_ms_float(started)

    missing = FakeCacheEngineKey("contextstorm-fake", "missing-batch", (777777,))
    started = time.perf_counter()
    contains = await connector.batched_contains([*keys, missing])
    batched_contains_ms = _duration_ms_float(started)

    started = time.perf_counter()
    fetched = await connector.batched_get([*keys, missing])
    batched_get_ms = _duration_ms_float(started)

    expected_contains = [True] * len(keys) + [False]
    expected_get = [*objects, None]
    batch_contains_match = contains == expected_contains
    batch_get_match = fetched == expected_get
    get_summary = await _get_all(connector, config, keys, objects)
    stats = await _stats(connector)
    return {
        "status": "pass" if batch_contains_match and batch_get_match else "fail",
        "object_count": len(keys),
        "bytes_put": sum(len(memory_obj.payload) for memory_obj in objects),
        "bytes_get": get_summary["bytes_get"],
        "roundtrip_match_count": get_summary["roundtrip_match_count"],
        "all_fake_roundtrips_match": get_summary["all_fake_roundtrips_match"],
        "missing_count": 1 if fetched[-1] is None else 0,
        "missing_returns_none": fetched[-1] is None,
        "batched_put_ms": batched_put_ms,
        "batched_contains_ms": batched_contains_ms,
        "batched_get_ms": batched_get_ms,
        "batch_contains_match": batch_contains_match,
        "batch_get_match": batch_get_match,
        "serialization_ms": get_summary["serialization_ms"],
        "deserialization_ms": get_summary["deserialization_ms"],
        **stats,
    }


async def _corrupt_object_probe(
    scenario: LMCacheScenario,
    repetition: int,
) -> dict[str, Any]:
    client = _CorruptibleFakeBifrostClient()
    config = BifrostLMCacheConfig(
        endpoint="contextstorm-fake",
        chunk_size=scenario.chunk_size_bytes,
        allow_pickle_fallback=True,
        timeout_seconds=float(scenario.timeout_seconds),
    )
    connector = BifrostRemoteConnector(config, client=client)
    keys, objects = _fake_items(scenario, repetition)
    key = keys[0]
    memory_obj = objects[0]
    started = time.perf_counter()
    try:
        await connector.put(key, memory_obj)
        put_ms = _duration_ms_float(started)
        client.corrupt_payload_for_key(key)
        exists_started = time.perf_counter()
        exists_after_corrupt = await connector.exists(key)
        exists_ms = _duration_ms_float(exists_started)
        get_started = time.perf_counter()
        rejected = False
        reason = None
        try:
            await connector.get(key)
        except BifrostLMCacheValidationError as exc:
            rejected = True
            reason = str(exc)
        get_ms = _duration_ms_float(get_started)
        return {
            "status": "pass" if rejected and exists_after_corrupt is False else "fail",
            "object_count": 1,
            "bytes_put": len(memory_obj.payload),
            "connector_put_ms": put_ms,
            "connector_exists_ms": exists_ms,
            "connector_get_ms": get_ms,
            "corrupt_object_rejected": rejected and exists_after_corrupt is False,
            "corrupt_rejection_count": 1 if rejected and exists_after_corrupt is False else 0,
            "validation_error_count": 0,
            "corrupt_rejection_reason": reason,
        }
    finally:
        await connector.close()


def _metrics_from_summary(
    operation: str,
    repetition: int,
    summary: dict[str, Any],
) -> LMCacheOperationMetrics:
    status = str(summary.get("status", "fail")).lower()
    skipped = bool(summary.get("skipped"))
    validation_errors = int(summary.get("validation_error_count") or 0)
    success = (status == "pass" or skipped) and validation_errors == 0
    reason = None if success else str(summary.get("reason") or "lmcache_correctness_failed")
    failures = [] if success else [
        {
            "operation": operation,
            "reason_code": reason,
            "message": str(summary.get("error") or "LMCache connector workload failed"),
        }
    ]
    return LMCacheOperationMetrics(
        operation=operation,
        repetition=repetition,
        success=success,
        reason_code=reason,
        skipped=skipped,
        connector_put_ms=float(summary.get("connector_put_ms") or 0.0),
        connector_exists_ms=float(summary.get("connector_exists_ms") or 0.0),
        connector_get_ms=float(summary.get("connector_get_ms") or 0.0),
        connector_list_ms=float(summary.get("connector_list_ms") or 0.0),
        serialization_ms=float(summary.get("serialization_ms") or 0.0),
        deserialization_ms=float(summary.get("deserialization_ms") or 0.0),
        object_count=int(summary.get("object_count") or 0),
        bytes_put=int(summary.get("bytes_put") or 0),
        bytes_get=int(summary.get("bytes_get") or 0),
        roundtrip_match_count=int(summary.get("roundtrip_match_count") or 0),
        missing_count=int(summary.get("missing_count") or 0),
        validation_error_count=validation_errors,
        corrupt_rejection_count=int(summary.get("corrupt_rejection_count") or 0),
        bifrost_store_object_count=int(summary.get("bifrost_store_object_count") or 0),
        fsck_status=summary.get("fsck_status"),
        exists_true_after_put=_optional_bool(summary.get("exists_true_after_put")),
        missing_returns_none=_optional_bool(summary.get("missing_returns_none")),
        all_fake_roundtrips_match=_optional_bool(summary.get("all_fake_roundtrips_match")),
        fsck_clean=_optional_bool(summary.get("fsck_clean")),
        batched_put_ms=float(summary.get("batched_put_ms") or 0.0),
        batched_contains_ms=float(summary.get("batched_contains_ms") or 0.0),
        batched_get_ms=float(summary.get("batched_get_ms") or 0.0),
        batch_contains_match=_optional_bool(summary.get("batch_contains_match")),
        batch_get_match=_optional_bool(summary.get("batch_get_match")),
        corrupt_object_rejected=_optional_bool(summary.get("corrupt_object_rejected")),
        failures=failures,
    )


def _optional_record(
    scenario: LMCacheScenario,
    operation: str,
    repetition: int,
    env_name: str,
    message: str,
    command: list[str],
) -> dict[str, Any]:
    enabled = os.environ.get(env_name) == "1"
    if enabled:
        try:
            completed = subprocess.run(
                command,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=scenario.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            completed = subprocess.CompletedProcess(
                command,
                124,
                stdout=exc.stdout or "",
                stderr=exc.stderr or "optional smoke script timed out",
            )
        summary = _parse_last_json(completed.stdout)
        if not summary:
            summary = {
                "status": "fail",
                "error": "optional smoke script did not emit JSON",
            }
        success = completed.returncode == 0
        metrics = LMCacheOperationMetrics(
            operation=operation,
            repetition=repetition,
            success=success,
            reason_code=None if success else "optional_smoke_failed",
            failures=[] if success else [
                {
                    "operation": operation,
                    "reason_code": "optional_smoke_failed",
                    "message": str(summary.get("error") or completed.stderr),
                }
            ],
        )
        exit_code = completed.returncode
        stdout = completed.stdout
        stderr = completed.stderr
        record_command = command
    else:
        summary = {
            "status": "pass",
            "skipped": True,
            "skip_reason": f"{message}; set {env_name}=1 to opt in",
            "scenario": scenario.name,
        }
        metrics = LMCacheOperationMetrics(
            operation=operation,
            repetition=repetition,
            success=True,
            skipped=True,
            reason_code="opt_in_not_enabled",
        )
        exit_code = 0
        stdout = json.dumps(summary, sort_keys=True)
        stderr = ""
        record_command = []
    return {
        "operation": operation,
        "repetition": repetition,
        "command": record_command,
        "exit_code": exit_code,
        "stdout": stdout,
        "stderr": stderr,
        "parsed_stdout": summary,
        "metrics": metrics.to_dict(),
    }


class _CorruptibleFakeBifrostClient:
    def __init__(self) -> None:
        self.objects: dict[str, StoredObject] = {}

    def put_object(
        self,
        metadata: dict[str, Any],
        payload: bytes,
        chunk_size: int,
    ) -> PutResult:
        del chunk_size
        object_id = str(metadata["object_id"])
        self.objects[object_id] = StoredObject(
            object_id=object_id,
            metadata=metadata,
            payload=payload,
            payload_hash=metadata["integrity"]["payload_hash"],
            descriptor_hash=metadata["integrity"]["descriptor_hash"],
        )
        return PutResult(
            object_id=object_id,
            payload_hash=metadata["integrity"]["payload_hash"],
            descriptor_hash=metadata["integrity"]["descriptor_hash"],
            stored=True,
            verified=True,
        )

    def query_by_opaque_key_hash(
        self,
        engine_name: str,
        integration_name: str,
        opaque_engine_key_hash: str,
    ) -> list[ObjectSummary]:
        return [
            _summary_from_stored(stored)
            for stored in self.objects.values()
            if stored.metadata["engine_profile"]["engine_name"] == engine_name
            and stored.metadata["engine_profile"]["integration_name"] == integration_name
            and stored.metadata["opaque_engine_profile"]["engine_key_hash"]
            == opaque_engine_key_hash
        ]

    def get_object(self, object_id: str) -> StoredObject:
        return self.objects[object_id]

    def close(self) -> None:
        return None

    def corrupt_payload_for_key(self, key: FakeCacheEngineKey) -> None:
        key_hash = opaque_engine_key_hash(key)
        for object_id, stored in list(self.objects.items()):
            if stored.metadata["opaque_engine_profile"]["engine_key_hash"] == key_hash:
                payload = b"x" + stored.payload[1:]
                self.objects[object_id] = replace(stored, payload=payload)
                return
        raise ContextStormError("corruption probe key was not stored")


def _summary_from_stored(stored: StoredObject) -> ObjectSummary:
    metadata = stored.metadata
    engine = metadata["engine_profile"]
    opaque = metadata["opaque_engine_profile"]
    return ObjectSummary(
        object_id=stored.object_id,
        object_type=metadata["object_type"],
        state="verified",
        byte_length=len(stored.payload),
        engine_name=engine["engine_name"],
        integration_name=engine["integration_name"],
        opaque_engine_key_hash=opaque["engine_key_hash"],
    )


def _parse_last_json(stdout: str) -> dict[str, Any]:
    parsed = _parse_json_stdout(stdout)
    if parsed:
        return parsed
    for line in reversed(stdout.splitlines()):
        parsed = _parse_json_stdout(line)
        if parsed:
            return parsed
    return {}


def _fake_items(
    scenario: LMCacheScenario,
    repetition: int,
) -> tuple[list[FakeCacheEngineKey], list[FakeMemoryObj]]:
    keys = []
    objects = []
    for index in range(scenario.object_count):
        ordinal = repetition * scenario.object_count + index
        keys.append(
            FakeCacheEngineKey(
                model_id="contextstorm-fake-lmcache",
                block_hash=f"block-{ordinal:08d}",
                tokens=tuple(range(ordinal, ordinal + 8)),
                extra=(("workload", scenario.name),),
            )
        )
        objects.append(
            FakeMemoryObj(
                payload=_deterministic_payload(scenario.payload_size_bytes, ordinal),
                shape=(1, scenario.payload_size_bytes),
            )
        )
    return keys, objects


def _deterministic_payload(size: int, seed: int) -> bytes:
    return bytes(((offset + seed) % 251 for offset in range(size)))


def _roundtrip_success(summary: dict[str, Any], object_count: int) -> bool:
    return (
        bool(summary.get("exists_true_after_put"))
        and bool(summary.get("all_fake_roundtrips_match"))
        and bool(summary.get("missing_returns_none"))
        and int(summary.get("roundtrip_match_count") or 0) == object_count
        and int(summary.get("list_count") or 0) >= object_count
    )


def _reason_code(exc: Exception) -> str:
    if isinstance(exc, BifrostLMCacheValidationError):
        return "lmcache_validation_error"
    if isinstance(exc, ContextStormError):
        return "lmcache_scenario_error"
    if isinstance(exc, ValueError):
        return "lmcache_configuration_error"
    return "lmcache_workload_error"


def _optional_bool(value: Any) -> bool | None:
    if value is None:
        return None
    return bool(value)


def _duration_ms_float(started: float) -> float:
    return max(0.001, (time.perf_counter() - started) * 1000.0)


def _scenario_to_dict(scenario: LMCacheScenario) -> dict[str, Any]:
    return {
        "name": scenario.name,
        "workload": scenario.workload,
        "object_count": scenario.object_count,
        "payload_size_bytes": scenario.payload_size_bytes,
        "chunk_size_bytes": scenario.chunk_size_bytes,
        "operations": list(scenario.operations),
        "repetitions": scenario.repetitions,
        "timeout_seconds": scenario.timeout_seconds,
        "lmcache": {
            "fake_objects": True,
            "allow_pickle_fallback": scenario.allow_pickle_fallback,
            "real_lmcache_opt_in_env": scenario.real_lmcache_opt_in_env,
            "vllm_opt_in_env": scenario.vllm_opt_in_env,
        },
    }

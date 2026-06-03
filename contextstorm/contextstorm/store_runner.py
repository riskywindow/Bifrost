"""Store workload runner for ContextStorm Phase 3 benchmarks."""

from __future__ import annotations

import json
import shutil
import socket
import subprocess
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .runner import (
    ContextStormError,
    PathConfig,
    REPO_ROOT,
    _environment,
    _find_binary,
    _load_simple_yaml,
    _parse_json_stdout,
    _resolve_contextstorm_path,
    _resolve_paths,
    _stop_daemon,
    _wait_for_endpoint,
)
from .store_metrics import StoreOperationMetrics
from .synthetic_kv import generate_synthetic_object, write_synthetic_object


STORE_OPERATIONS = {
    "put_objects",
    "get_objects",
    "has_objects",
    "list_objects",
    "query_objects",
    "inspect_objects",
    "pin_objects",
    "unpin_objects",
    "evict",
    "create_manifest",
    "add_manifest_members",
    "check_manifest",
    "fsck",
}


@dataclass(frozen=True)
class StoreScenario:
    name: str
    object_count: int
    object_size_bytes: int
    chunk_size_bytes: int
    operations: tuple[str, ...]
    repetitions: int
    timeout_seconds: int
    object_type: str = "opaque_engine_blob"
    memory_tier_bytes: int = 0
    memory_tier_cache_payloads: bool = False
    memory_tier_max_object_bytes: int | None = None
    target_bytes: int | None = None
    pin_fraction: float = 0.0
    policy: str = "lru"
    manifest_expected_complete_before_eviction: bool | None = None
    manifest_expected_complete_after_eviction: bool | None = None


def is_store_scenario(path: Path) -> bool:
    data = _load_simple_yaml(path)
    operations = {str(op) for op in data.get("operations", [])}
    return bool(operations & STORE_OPERATIONS) or str(data.get("workload", "")) == "store"


def load_store_scenario(path: Path) -> StoreScenario:
    data = _load_simple_yaml(path)
    operations = tuple(str(op) for op in data.get("operations", ["put_objects"]))
    unknown = sorted(set(operations) - STORE_OPERATIONS)
    if unknown:
        raise ContextStormError(f"unsupported store operations in {path}: {unknown}")
    return StoreScenario(
        name=str(data["name"]),
        object_count=int(data.get("object_count", 1)),
        object_size_bytes=_parse_size(data["object_size_bytes"]),
        chunk_size_bytes=_parse_size(data.get("chunk_size_bytes", 262144)),
        operations=operations,
        repetitions=int(data.get("repetitions", 1)),
        timeout_seconds=int(data.get("timeout_seconds", 30)),
        object_type=str(data.get("object_type", "opaque_engine_blob")),
        memory_tier_bytes=_parse_size(data.get("memory_tier_bytes", 0)),
        memory_tier_cache_payloads=bool(data.get("memory_tier_cache_payloads", False)),
        memory_tier_max_object_bytes=(
            _parse_size(data["memory_tier_max_object_bytes"])
            if data.get("memory_tier_max_object_bytes") is not None
            else None
        ),
        target_bytes=(
            _parse_size(data["target_bytes"])
            if data.get("target_bytes") is not None
            else None
        ),
        pin_fraction=float(data.get("pin_fraction", 0.0)),
        policy=str(data.get("policy", "lru")),
        manifest_expected_complete_before_eviction=_optional_bool(
            data.get("manifest_complete_before_eviction")
        ),
        manifest_expected_complete_after_eviction=_optional_bool(
            data.get("manifest_complete_after_eviction")
        ),
    )


def run_store_scenario(
    scenario_path: Path,
    *,
    runs_root: Path | None = None,
    run_id: str | None = None,
) -> Path:
    scenario_path = _resolve_contextstorm_path(scenario_path)
    scenario = load_store_scenario(scenario_path)
    daemon_bin = _find_binary("bifrost-daemon")
    xfer_bin = _find_binary("bifrost-xfer")
    store_bin = _find_binary("bifrost-store")
    missing = [
        name
        for name, value in {
            "bifrost-daemon": daemon_bin,
            "bifrost-xfer": xfer_bin,
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

    endpoint = f"127.0.0.1:{_free_port()}"
    path_config = _resolve_paths((PathConfig(name="primary", endpoint=endpoint),))[0]
    temp_root = tempfile.TemporaryDirectory(prefix="contextstorm-store-")
    trace_path = run_dir / "traces" / "daemon_primary.jsonl"
    spool = Path(temp_root.name) / "store_primary"
    daemon_command = [
        str(daemon_bin),
        "--listen",
        str(path_config.endpoint),
        "--spool",
        str(spool),
        "--trace-jsonl",
        str(trace_path),
        "--memory-tier-bytes",
        str(scenario.memory_tier_bytes),
        "--memory-tier-cache-payloads",
        "true" if scenario.memory_tier_cache_payloads else "false",
    ]
    if scenario.memory_tier_max_object_bytes is not None:
        daemon_command.extend(
            ["--memory-tier-max-object-bytes", str(scenario.memory_tier_max_object_bytes)]
        )
    process = subprocess.Popen(
        daemon_command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    daemon = {
        "path_name": "primary",
        "command": daemon_command,
        "trace_jsonl": str(trace_path),
        "process": process,
    }

    run_record: dict[str, Any] = {
        "schema_version": "contextstorm.run.v1",
        "benchmark_kind": "store",
        "scenario": _scenario_to_dict(scenario),
        "started_at_unix_ms": int(time.time() * 1000),
        "environment": _environment(),
        "operations": [],
    }
    object_ids: list[str] = []
    payload_paths: dict[str, Path] = {}
    prefix_hashes: list[str] = []
    model_hashes: list[str] = []
    pinned_ids: set[str] = set()
    manifest_id: str | None = None

    try:
        _wait_for_endpoint(str(path_config.endpoint), scenario.timeout_seconds)
        for repetition in range(scenario.repetitions):
            for operation in scenario.operations:
                if operation == "put_objects":
                    result = _put_objects(
                        xfer_bin,
                        scenario,
                        path_config.endpoint or endpoint,
                        run_dir,
                        repetition,
                        object_ids,
                        payload_paths,
                        prefix_hashes,
                        model_hashes,
                    )
                elif operation == "has_objects":
                    result = _has_objects(
                        xfer_bin,
                        scenario,
                        path_config.endpoint or endpoint,
                        run_dir,
                        repetition,
                        object_ids,
                    )
                elif operation == "get_objects":
                    result = _get_objects(
                        xfer_bin,
                        scenario,
                        path_config.endpoint or endpoint,
                        run_dir,
                        repetition,
                        object_ids,
                        payload_paths,
                    )
                elif operation == "list_objects":
                    result = _store_cli_operation(
                        store_bin,
                        scenario,
                        path_config.endpoint or endpoint,
                        run_dir,
                        repetition,
                        operation,
                        ["list", "--json"],
                        latency_key="list_latency_ms",
                    )
                elif operation == "query_objects":
                    result = _store_cli_operation(
                        store_bin,
                        scenario,
                        path_config.endpoint or endpoint,
                        run_dir,
                        repetition,
                        operation,
                        ["query", "--json"],
                        latency_key="query_latency_ms",
                    )
                elif operation == "inspect_objects":
                    result = _inspect_objects(
                        store_bin,
                        scenario,
                        path_config.endpoint or endpoint,
                        run_dir,
                        repetition,
                        object_ids,
                    )
                elif operation == "pin_objects":
                    result = _pin_objects(
                        store_bin,
                        scenario,
                        path_config.endpoint or endpoint,
                        run_dir,
                        repetition,
                        object_ids,
                        pinned_ids,
                    )
                elif operation == "unpin_objects":
                    result = _unpin_objects(
                        store_bin,
                        scenario,
                        path_config.endpoint or endpoint,
                        run_dir,
                        repetition,
                        pinned_ids,
                    )
                elif operation == "evict":
                    result = _evict(
                        store_bin,
                        scenario,
                        path_config.endpoint or endpoint,
                        run_dir,
                        repetition,
                        pinned_ids,
                    )
                elif operation == "create_manifest":
                    result = _create_manifest(
                        store_bin,
                        scenario,
                        path_config.endpoint or endpoint,
                        run_dir,
                        repetition,
                        prefix_hashes,
                        model_hashes,
                    )
                    manifest_id = _extract_manifest_id(result.get("parsed_stdout", {}))
                elif operation == "add_manifest_members":
                    result = _add_manifest_members(
                        store_bin,
                        scenario,
                        path_config.endpoint or endpoint,
                        run_dir,
                        repetition,
                        manifest_id,
                        object_ids,
                    )
                elif operation == "check_manifest":
                    result = _check_manifest(
                        store_bin,
                        scenario,
                        path_config.endpoint or endpoint,
                        run_dir,
                        repetition,
                        manifest_id,
                    )
                elif operation == "fsck":
                    result = _store_cli_operation(
                        store_bin,
                        scenario,
                        path_config.endpoint or endpoint,
                        run_dir,
                        repetition,
                        operation,
                        ["fsck", "--check", "--json"],
                        latency_key="fsck_duration_ms",
                    )
                    status = str(result.get("parsed_stdout", {}).get("status", ""))
                    result["metrics"]["fsck_clean_after_run"] = status.lower() == "clean"
                else:  # pragma: no cover - guarded by load_store_scenario
                    raise ContextStormError(f"unsupported store operation: {operation}")
                run_record["operations"].append(result)
    finally:
        run_record["daemons"] = [_stop_daemon(daemon, run_dir)]
        temp_root.cleanup()
        run_record["finished_at_unix_ms"] = int(time.time() * 1000)
        (run_dir / "run.json").write_text(
            json.dumps(run_record, indent=2, sort_keys=True) + "\n"
        )
        from .store_report import write_store_report

        write_store_report(run_dir)
    return run_dir


def _put_objects(
    xfer_bin: Path,
    scenario: StoreScenario,
    endpoint: str,
    run_dir: Path,
    repetition: int,
    object_ids: list[str],
    payload_paths: dict[str, Path],
    prefix_hashes: list[str],
    model_hashes: list[str],
) -> dict[str, Any]:
    records = []
    inserted_ids = []
    started = time.monotonic()
    for index in range(scenario.object_count):
        model_shape = _model_shape_for_object(
            scenario.object_type,
            scenario.object_size_bytes,
            index + repetition * scenario.object_count,
        )
        synthetic = generate_synthetic_object(
            object_size_bytes=scenario.object_size_bytes,
            object_type=scenario.object_type,
            model_shape=model_shape,
        )
        input_dir = run_dir / "inputs" / f"rep_{repetition:03d}_obj_{index:04d}"
        manifest = write_synthetic_object(synthetic, input_dir)
        command = [
            str(xfer_bin),
            "--json",
            "put",
            "--endpoint",
            endpoint,
            "--meta",
            manifest["meta"],
            "--payload",
            manifest["payload"],
            "--target",
            manifest["target_profile"],
            "--chunk-size",
            str(scenario.chunk_size_bytes),
        ]
        records.append(_run_process(command, scenario.timeout_seconds))
        object_ids.append(synthetic.object_id)
        inserted_ids.append(synthetic.object_id)
        payload_paths[synthetic.object_id] = input_dir / "payload.bin"
        prefix_hash = (synthetic.metadata.get("prefix_profile") or {}).get("prefix_hash")
        if prefix_hash and prefix_hash not in prefix_hashes:
            prefix_hashes.append(str(prefix_hash))
        model_hash = (synthetic.metadata.get("model_profile") or {}).get("model_hash")
        if model_hash and model_hash not in model_hashes:
            model_hashes.append(str(model_hash))
    duration_ms = _duration_ms(started)
    success = all(record["exit_code"] == 0 for record in records)
    metrics = StoreOperationMetrics(
        operation="put_objects",
        repetition=repetition,
        success=success,
        reason_code=_first_reason(records),
        put_duration_ms=duration_ms,
        objects_inserted=sum(1 for record in records if record["exit_code"] == 0),
        bytes_committed=sum(
            payload_paths[object_id].stat().st_size for object_id in inserted_ids
        ),
    )
    return _write_operation_record(
        run_dir, "put_objects", repetition, [], records, metrics.to_dict()
    )


def _has_objects(
    xfer_bin: Path,
    scenario: StoreScenario,
    endpoint: str,
    run_dir: Path,
    repetition: int,
    object_ids: list[str],
) -> dict[str, Any]:
    started = time.monotonic()
    records = [
        _run_process(
            [
                str(xfer_bin),
                "--json",
                "has",
                "--endpoint",
                endpoint,
                "--object-id",
                object_id,
            ],
            scenario.timeout_seconds,
        )
        for object_id in object_ids
    ]
    present = [bool(record["parsed_stdout"].get("present")) for record in records]
    metrics = StoreOperationMetrics(
        operation="has_objects",
        repetition=repetition,
        success=all(record["exit_code"] == 0 for record in records) and all(present),
        reason_code=_first_reason(records),
        has_latency_ms=_duration_ms(started),
    )
    return _write_operation_record(
        run_dir, "has_objects", repetition, [], records, metrics.to_dict()
    )


def _get_objects(
    xfer_bin: Path,
    scenario: StoreScenario,
    endpoint: str,
    run_dir: Path,
    repetition: int,
    object_ids: list[str],
    payload_paths: dict[str, Path],
) -> dict[str, Any]:
    started = time.monotonic()
    records = []
    matches = []
    for index, object_id in enumerate(object_ids):
        out_dir = run_dir / "outputs" / f"rep_{repetition:03d}_obj_{index:04d}"
        command = [
            str(xfer_bin),
            "--json",
            "get",
            "--endpoint",
            endpoint,
            "--object-id",
            object_id,
            "--out",
            str(out_dir),
            "--chunk-size",
            str(scenario.chunk_size_bytes),
        ]
        record = _run_process(command, scenario.timeout_seconds)
        records.append(record)
        output_payload = out_dir / "payload.bin"
        matches.append(
            output_payload.exists()
            and output_payload.read_bytes() == payload_paths[object_id].read_bytes()
        )
    metrics = StoreOperationMetrics(
        operation="get_objects",
        repetition=repetition,
        success=all(record["exit_code"] == 0 for record in records) and all(matches),
        reason_code=_first_reason(records),
        get_duration_ms=_duration_ms(started),
        payload_roundtrip_match=all(matches) if matches else None,
    )
    return _write_operation_record(
        run_dir, "get_objects", repetition, [], records, metrics.to_dict()
    )


def _inspect_objects(
    store_bin: Path,
    scenario: StoreScenario,
    endpoint: str,
    run_dir: Path,
    repetition: int,
    object_ids: list[str],
) -> dict[str, Any]:
    started = time.monotonic()
    records = [
        _run_store_command(
            store_bin,
            endpoint,
            ["inspect", "--object-id", object_id, "--json"],
            scenario.timeout_seconds,
        )
        for object_id in object_ids
    ]
    found = [bool(record["parsed_stdout"].get("found")) for record in records]
    metrics = StoreOperationMetrics(
        operation="inspect_objects",
        repetition=repetition,
        success=all(record["exit_code"] == 0 for record in records) and all(found),
        reason_code=_first_reason(records),
        inspect_latency_ms=_duration_ms(started),
    )
    return _write_operation_record(
        run_dir, "inspect_objects", repetition, [], records, metrics.to_dict()
    )


def _pin_objects(
    store_bin: Path,
    scenario: StoreScenario,
    endpoint: str,
    run_dir: Path,
    repetition: int,
    object_ids: list[str],
    pinned_ids: set[str],
) -> dict[str, Any]:
    count = max(1, int(len(object_ids) * scenario.pin_fraction)) if object_ids else 0
    selected = object_ids[:count]
    started = time.monotonic()
    records = [
        _run_store_command(
            store_bin,
            endpoint,
            ["pin", "--object-id", object_id],
            scenario.timeout_seconds,
        )
        for object_id in selected
    ]
    pinned_ids.update(selected)
    metrics = StoreOperationMetrics(
        operation="pin_objects",
        repetition=repetition,
        success=all(record["exit_code"] == 0 for record in records),
        reason_code=_first_reason(records),
        inspect_latency_ms=_duration_ms(started),
        objects_pinned=sum(1 for record in records if record["exit_code"] == 0),
    )
    return _write_operation_record(
        run_dir, "pin_objects", repetition, [], records, metrics.to_dict()
    )


def _unpin_objects(
    store_bin: Path,
    scenario: StoreScenario,
    endpoint: str,
    run_dir: Path,
    repetition: int,
    pinned_ids: set[str],
) -> dict[str, Any]:
    selected = sorted(pinned_ids)
    started = time.monotonic()
    records = [
        _run_store_command(
            store_bin,
            endpoint,
            ["unpin", "--object-id", object_id],
            scenario.timeout_seconds,
        )
        for object_id in selected
    ]
    pinned_ids.clear()
    metrics = StoreOperationMetrics(
        operation="unpin_objects",
        repetition=repetition,
        success=all(record["exit_code"] == 0 for record in records),
        reason_code=_first_reason(records),
        inspect_latency_ms=_duration_ms(started),
    )
    return _write_operation_record(
        run_dir, "unpin_objects", repetition, [], records, metrics.to_dict()
    )


def _evict(
    store_bin: Path,
    scenario: StoreScenario,
    endpoint: str,
    run_dir: Path,
    repetition: int,
    pinned_ids: set[str],
) -> dict[str, Any]:
    args = ["evict", "--policy", scenario.policy, "--json"]
    if scenario.target_bytes is not None:
        args.extend(["--target-bytes", str(scenario.target_bytes)])
    started = time.monotonic()
    before = _stats(store_bin, endpoint, scenario.timeout_seconds)
    record = _run_store_command(store_bin, endpoint, args, scenario.timeout_seconds)
    after = _stats(store_bin, endpoint, scenario.timeout_seconds)
    report = record["parsed_stdout"]
    evicted = report.get("evicted") or []
    evicted_ids = {str(item.get("object_id")) for item in evicted}
    metrics = StoreOperationMetrics(
        operation="evict",
        repetition=repetition,
        success=record["exit_code"] == 0,
        reason_code=record["parsed_stdout"].get("reason") or None,
        eviction_duration_ms=_duration_ms(started),
        objects_evicted=len(evicted),
        bytes_evicted=int(report.get("freed_bytes") or 0),
        store_bytes_before=int(before.get("total_bytes_on_disk") or 0),
        store_bytes_after=int(after.get("total_bytes_on_disk") or 0),
        memory_tier_hits=int(after.get("memory_tier_hits") or 0),
        memory_tier_misses=int(after.get("memory_tier_misses") or 0),
        pinned_not_evicted=not bool(pinned_ids & evicted_ids) if pinned_ids else None,
    )
    return _write_operation_record(
        run_dir, "evict", repetition, record["command"], [record], metrics.to_dict()
    )


def _create_manifest(
    store_bin: Path,
    scenario: StoreScenario,
    endpoint: str,
    run_dir: Path,
    repetition: int,
    prefix_hashes: list[str],
    model_hashes: list[str],
) -> dict[str, Any]:
    prefix_hash = (
        prefix_hashes[0] if prefix_hashes else f"contextstorm-store-prefix-{repetition}"
    )
    model_hash = model_hashes[0] if model_hashes else "contextstorm-store-model"
    token_range_end = (
        scenario.object_size_bytes // 4
        if scenario.object_type == "native_kv_page"
        else max(1, scenario.object_count)
    )
    return _store_cli_operation(
        store_bin,
        scenario,
        endpoint,
        run_dir,
        repetition,
        "create_manifest",
        [
            "manifest",
            "create-prefix",
            "--prefix-hash",
            prefix_hash,
            "--model-hash",
            model_hash,
            "--token-range-start",
            "0",
            "--token-range-end",
            str(token_range_end),
            "--json",
        ],
        latency_key="inspect_latency_ms",
    )


def _add_manifest_members(
    store_bin: Path,
    scenario: StoreScenario,
    endpoint: str,
    run_dir: Path,
    repetition: int,
    manifest_id: str | None,
    object_ids: list[str],
) -> dict[str, Any]:
    if not manifest_id:
        raise ContextStormError("add_manifest_members requires create_manifest first")
    records = [
        _run_store_command(
            store_bin,
            endpoint,
            [
                "manifest",
                "add-member",
                "--manifest-id",
                manifest_id,
                "--object-id",
                object_id,
            ],
            scenario.timeout_seconds,
        )
        for object_id in object_ids
    ]
    metrics = StoreOperationMetrics(
        operation="add_manifest_members",
        repetition=repetition,
        success=all(record["exit_code"] == 0 for record in records),
        reason_code=_first_reason(records),
    )
    return _write_operation_record(
        run_dir, "add_manifest_members", repetition, [], records, metrics.to_dict()
    )


def _check_manifest(
    store_bin: Path,
    scenario: StoreScenario,
    endpoint: str,
    run_dir: Path,
    repetition: int,
    manifest_id: str | None,
) -> dict[str, Any]:
    if not manifest_id:
        raise ContextStormError("check_manifest requires create_manifest first")
    result = _store_cli_operation(
        store_bin,
        scenario,
        endpoint,
        run_dir,
        repetition,
        "check_manifest",
        ["manifest", "check", "--manifest-id", manifest_id, "--json"],
        latency_key="inspect_latency_ms",
    )
    completeness = (result.get("parsed_stdout") or {}).get("completeness") or {}
    required = int(completeness.get("required_count") or 0)
    serveable = int(completeness.get("serveable_required_count") or 0)
    ratio = (serveable / required) if required else None
    result["metrics"]["manifest_completeness"] = ratio
    expected = scenario.manifest_expected_complete_before_eviction
    if any((run_dir / "commands").glob("evict_*.json")):
        expected = scenario.manifest_expected_complete_after_eviction
    if expected is not None:
        result["metrics"]["manifest_completeness_expected"] = (
            (ratio == 1.0) if expected else (ratio != 1.0)
        )
    return result


def _store_cli_operation(
    store_bin: Path,
    scenario: StoreScenario,
    endpoint: str,
    run_dir: Path,
    repetition: int,
    operation: str,
    args: list[str],
    *,
    latency_key: str,
) -> dict[str, Any]:
    started = time.monotonic()
    record = _run_store_command(store_bin, endpoint, args, scenario.timeout_seconds)
    metrics = StoreOperationMetrics(
        operation=operation,
        repetition=repetition,
        success=record["exit_code"] == 0,
        reason_code=record["parsed_stdout"].get("reason") or None,
    ).to_dict()
    metrics[latency_key] = _duration_ms(started)
    stats = _stats(store_bin, endpoint, scenario.timeout_seconds)
    metrics["store_bytes_after"] = int(stats.get("total_bytes_on_disk") or 0)
    metrics["memory_tier_hits"] = int(stats.get("memory_tier_hits") or 0)
    metrics["memory_tier_misses"] = int(stats.get("memory_tier_misses") or 0)
    return _write_operation_record(
        run_dir, operation, repetition, record["command"], [record], metrics
    )


def _run_store_command(
    store_bin: Path, endpoint: str, args: list[str], timeout_seconds: int
) -> dict[str, Any]:
    command = [str(store_bin), *args, "--endpoint", endpoint]
    return _run_process(command, timeout_seconds)


def _run_process(command: list[str], timeout_seconds: int) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            command,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_seconds,
            check=False,
        )
        stdout = completed.stdout
        stderr = completed.stderr
        exit_code = completed.returncode
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        exit_code = 124
    return {
        "command": command,
        "exit_code": exit_code,
        "stdout": stdout,
        "stderr": stderr,
        "parsed_stdout": _parse_json_stdout(stdout),
    }


def _write_operation_record(
    run_dir: Path,
    operation: str,
    repetition: int,
    command: list[str],
    records: list[dict[str, Any]],
    metrics: dict[str, Any],
) -> dict[str, Any]:
    record = {
        "operation": operation,
        "repetition": repetition,
        "command": command,
        "exit_code": 0 if metrics.get("success") else 1,
        "stdout": "\n".join(item.get("stdout", "") for item in records),
        "stderr": "\n".join(item.get("stderr", "") for item in records),
        "parsed_stdout": records[-1].get("parsed_stdout", {}) if records else {},
        "subcommands": records,
        "metrics": metrics,
    }
    path = run_dir / "commands" / f"{operation}_{repetition:03d}.json"
    path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    return record


def _stats(store_bin: Path, endpoint: str, timeout_seconds: int) -> dict[str, Any]:
    record = _run_store_command(store_bin, endpoint, ["stats", "--json"], timeout_seconds)
    return record["parsed_stdout"] if record["exit_code"] == 0 else {}


def _extract_manifest_id(response: dict[str, Any]) -> str | None:
    manifest = response.get("manifest") or {}
    if "manifest" in manifest:
        manifest = manifest["manifest"]
    return manifest.get("manifest_id")


def _first_reason(records: list[dict[str, Any]]) -> str | None:
    for record in records:
        if record["exit_code"] == 0:
            continue
        parsed = record.get("parsed_stdout") or {}
        return parsed.get("reason") or record.get("stderr") or "command_failed"
    return None


def _duration_ms(started: float) -> int:
    return max(1, int((time.monotonic() - started) * 1000))


def _model_shape_for_object(
    object_type: str, object_size_bytes: int, variant: int
) -> dict[str, Any]:
    if object_type == "native_kv_page":
        return {
            "layers": 1 + variant,
            "num_kv_heads": 1,
            "head_dim": 1,
            "tokens": object_size_bytes // 4,
            "dtype": "float16",
        }
    return {"tokens": 256 + variant}


def _parse_size(value: Any) -> int:
    if isinstance(value, int):
        return value
    text = str(value).strip()
    parts = text.split()
    if len(parts) == 2:
        number = int(parts[0])
        unit = parts[1].lower()
        if unit in {"kib", "kb"}:
            return number * 1024
        if unit in {"mib", "mb"}:
            return number * 1024 * 1024
        if unit in {"gib", "gb"}:
            return number * 1024 * 1024 * 1024
    return int(text)


def _optional_bool(value: Any) -> bool | None:
    if value is None:
        return None
    return bool(value)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _scenario_to_dict(scenario: StoreScenario) -> dict[str, Any]:
    return {
        "name": scenario.name,
        "object_count": scenario.object_count,
        "object_size_bytes": scenario.object_size_bytes,
        "chunk_size_bytes": scenario.chunk_size_bytes,
        "object_type": scenario.object_type,
        "operations": list(scenario.operations),
        "repetitions": scenario.repetitions,
        "timeout_seconds": scenario.timeout_seconds,
        "memory_tier_bytes": scenario.memory_tier_bytes,
        "memory_tier_cache_payloads": scenario.memory_tier_cache_payloads,
        "memory_tier_max_object_bytes": scenario.memory_tier_max_object_bytes,
        "target_bytes": scenario.target_bytes,
        "pin_fraction": scenario.pin_fraction,
        "policy": scenario.policy,
    }

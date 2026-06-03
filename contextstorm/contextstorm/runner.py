"""Scenario loading and process runner for ContextStorm."""

from __future__ import annotations

import json
import os
import platform
import shutil
import socket
import subprocess
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .metrics import (
    OperationMetrics,
    load_trace_jsonl,
    metrics_snapshot_totals,
    summarize_trace_events,
    throughput_mib_s,
)
from .faults import FaultController, load_fault_profile
from .synthetic_kv import generate_synthetic_object, write_synthetic_object


REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class PathConfig:
    name: str
    endpoint: str | None = None
    start_daemon: bool = True


@dataclass(frozen=True)
class Scenario:
    name: str
    object_size_bytes: int
    chunk_size_bytes: int
    paths: tuple[PathConfig, ...]
    operations: tuple[str, ...]
    repetitions: int
    timeout_seconds: int
    object_type: str = "opaque_engine_blob"
    model_shape: dict[str, Any] | None = None
    fault_profile: str | None = None


class ContextStormError(RuntimeError):
    pass


def load_scenario(path: Path) -> Scenario:
    data = _load_simple_yaml(path)
    paths_value = data.get("paths") or [{"name": "primary"}]
    paths: list[PathConfig] = []
    for index, item in enumerate(paths_value):
        if isinstance(item, str):
            paths.append(PathConfig(name=item))
        elif isinstance(item, dict):
            paths.append(
                PathConfig(
                    name=str(item.get("name") or f"path{index}"),
                    endpoint=item.get("endpoint"),
                    start_daemon=bool(item.get("start_daemon", item.get("daemon", True))),
                )
            )
        else:
            raise ContextStormError(f"invalid path entry in {path}: {item!r}")

    return Scenario(
        name=str(data["name"]),
        object_size_bytes=int(data["object_size_bytes"]),
        chunk_size_bytes=int(data["chunk_size_bytes"]),
        paths=tuple(paths),
        operations=tuple(str(op) for op in data.get("operations", ["put", "has", "get"])),
        repetitions=int(data.get("repetitions", 1)),
        timeout_seconds=int(data.get("timeout_seconds", 30)),
        object_type=str(data.get("object_type", "opaque_engine_blob")),
        model_shape=data.get("model_shape"),
        fault_profile=data.get("fault_profile"),
    )


def run_scenario(
    scenario_path: Path,
    *,
    runs_root: Path | None = None,
    run_id: str | None = None,
    allow_root_faults: bool = False,
) -> Path:
    scenario_path = _resolve_contextstorm_path(scenario_path)
    scenario = load_scenario(scenario_path)
    fault_profile = load_fault_profile(scenario.fault_profile)
    daemon_bin = _find_binary("bifrost-daemon")
    xfer_bin = _find_binary("bifrost-xfer")
    if daemon_bin is None or xfer_bin is None:
        missing = [
            name
            for name, value in {
                "bifrost-daemon": daemon_bin,
                "bifrost-xfer": xfer_bin,
            }.items()
            if value is None
        ]
        raise ContextStormError(
            "missing Rust binaries: "
            + ", ".join(missing)
            + ". Build with `cargo build` in bifrostd."
        )

    runs_root = runs_root or REPO_ROOT / "runs"
    run_id = run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = runs_root / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    (run_dir / "inputs").mkdir()
    (run_dir / "traces").mkdir()
    (run_dir / "commands").mkdir()
    shutil.copyfile(scenario_path, run_dir / "scenario.yaml")

    run_record: dict[str, Any] = {
        "schema_version": "contextstorm.run.v1",
        "scenario": _scenario_to_dict(scenario),
        "started_at_unix_ms": int(time.time() * 1000),
        "environment": _environment(),
        "operations": [],
        "fault_profile": {
            "type": fault_profile.type,
            "path": str(fault_profile.path) if fault_profile.path else None,
            "allow_root_faults": allow_root_faults,
        },
        "fault_events_jsonl": str(run_dir / "fault_events.jsonl"),
    }

    daemons: list[dict[str, Any]] = []
    temp_root = tempfile.TemporaryDirectory(prefix="contextstorm-")
    fault_controller = FaultController(
        fault_profile,
        allow_root_faults=allow_root_faults,
        events_path=run_dir / "fault_events.jsonl",
    )
    try:
        resolved_paths = _resolve_paths(scenario.paths)
        for path_config in resolved_paths:
            if not path_config.start_daemon:
                continue
            trace_path = run_dir / "traces" / f"daemon_{path_config.name}.jsonl"
            spool = Path(temp_root.name) / f"spool_{path_config.name}"
            command = [
                str(daemon_bin),
                "--listen",
                str(path_config.endpoint),
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
            daemons.append(
                {
                    "path_name": path_config.name,
                    "command": command,
                    "trace_jsonl": str(trace_path),
                    "process": process,
                }
            )
            _wait_for_endpoint(str(path_config.endpoint), scenario.timeout_seconds)
        fault_controller.register_daemons(daemons)
        fault_controller.start()

        for repetition in range(scenario.repetitions):
            synthetic = generate_synthetic_object(
                object_size_bytes=scenario.object_size_bytes,
                object_type=scenario.object_type,
                model_shape=scenario.model_shape,
            )
            input_dir = run_dir / "inputs" / f"rep_{repetition:03d}"
            manifest = write_synthetic_object(synthetic, input_dir)
            object_id = synthetic.object_id
            has_verified = False
            get_matches: bool | None = None

            if "put" in scenario.operations:
                fault_controller.maybe_apply_artificial_delay(_primary_path_name(resolved_paths))
                result = _run_put(
                    xfer_bin,
                    scenario,
                    resolved_paths,
                    manifest,
                    run_dir,
                    repetition,
                )
                run_record["operations"].append(result)
            if "has" in scenario.operations:
                fault_controller.maybe_apply_artificial_delay(_primary_path_name(resolved_paths))
                result = _run_has(
                    xfer_bin,
                    scenario,
                    _primary_endpoint(resolved_paths),
                    object_id,
                    run_dir,
                    repetition,
                )
                has_verified = bool(result.get("parsed_stdout", {}).get("present"))
                run_record["operations"].append(result)
            if "get" in scenario.operations:
                fault_controller.maybe_apply_artificial_delay(_primary_path_name(resolved_paths))
                out_dir = run_dir / "outputs" / f"rep_{repetition:03d}"
                result = _run_get(
                    xfer_bin,
                    scenario,
                    _primary_endpoint(resolved_paths),
                    object_id,
                    out_dir,
                    run_dir,
                    repetition,
                    input_dir / "payload.bin",
                )
                get_matches = result["metrics"]["get_payload_matches_put_payload"]
                run_record["operations"].append(result)

            for operation in run_record["operations"]:
                if operation.get("repetition") == repetition:
                    operation["metrics"]["committed_object_verified"] = has_verified
                    if get_matches is not None:
                        operation["metrics"]["get_payload_matches_put_payload"] = get_matches
    finally:
        fault_controller.cleanup()
        daemon_records = []
        for daemon in daemons:
            daemon_records.append(_stop_daemon(daemon, run_dir))
        run_record["daemons"] = daemon_records
        temp_root.cleanup()
        run_record["finished_at_unix_ms"] = int(time.time() * 1000)
        (run_dir / "run.json").write_text(
            json.dumps(run_record, indent=2, sort_keys=True) + "\n"
        )
        from .report import write_report

        write_report(run_dir)

    return run_dir


def _run_put(
    xfer_bin: Path,
    scenario: Scenario,
    paths: tuple[PathConfig, ...],
    manifest: dict[str, str],
    run_dir: Path,
    repetition: int,
) -> dict[str, Any]:
    trace_path = run_dir / "traces" / f"put_{repetition:03d}.jsonl"
    command = [
        str(xfer_bin),
        "--json",
        "put",
        "--meta",
        manifest["meta"],
        "--payload",
        manifest["payload"],
        "--target",
        manifest["target_profile"],
        "--chunk-size",
        str(scenario.chunk_size_bytes),
        "--trace-jsonl",
        str(trace_path),
    ]
    if len(paths) == 1:
        command.extend(["--endpoint", str(paths[0].endpoint)])
    else:
        for path_config in paths:
            command.extend(["--path", f"{path_config.name}={path_config.endpoint}"])
    return _run_command(
        command, scenario.timeout_seconds, "put", repetition, trace_path, run_dir
    )


def _run_has(
    xfer_bin: Path,
    scenario: Scenario,
    endpoint: str,
    object_id: str,
    run_dir: Path,
    repetition: int,
) -> dict[str, Any]:
    command = [
        str(xfer_bin),
        "--json",
        "has",
        "--endpoint",
        endpoint,
        "--object-id",
        object_id,
    ]
    return _run_command(command, scenario.timeout_seconds, "has", repetition, None, run_dir)


def _run_get(
    xfer_bin: Path,
    scenario: Scenario,
    endpoint: str,
    object_id: str,
    out_dir: Path,
    run_dir: Path,
    repetition: int,
    put_payload: Path,
) -> dict[str, Any]:
    trace_path = run_dir / "traces" / f"get_{repetition:03d}.jsonl"
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
        "--trace-jsonl",
        str(trace_path),
    ]
    result = _run_command(
        command, scenario.timeout_seconds, "get", repetition, trace_path, run_dir
    )
    output_payload = out_dir / "payload.bin"
    result["metrics"]["get_payload_matches_put_payload"] = (
        output_payload.exists() and output_payload.read_bytes() == put_payload.read_bytes()
    )
    return result


def _run_command(
    command: list[str],
    timeout_seconds: int,
    operation: str,
    repetition: int,
    trace_path: Path | None,
    run_dir: Path,
) -> dict[str, Any]:
    started = time.monotonic()
    timed_out = False
    try:
        completed = subprocess.run(
            command,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_seconds,
            check=False,
        )
        exit_code = completed.returncode
        stdout = completed.stdout
        stderr = completed.stderr
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        exit_code = 124
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
    duration_ms = max(1, int((time.monotonic() - started) * 1000))
    parsed = _parse_json_stdout(stdout)
    events = load_trace_jsonl(trace_path) if trace_path else []
    trace_summary = summarize_trace_events(events)
    snapshot_totals = metrics_snapshot_totals(parsed.get("metrics"))
    byte_count = (
        snapshot_totals["bytes_sent"]
        or snapshot_totals["bytes_received"]
        or trace_summary["bytes_sent"]
        or trace_summary["bytes_received"]
    )
    metrics = OperationMetrics(
        operation=operation,
        repetition=repetition,
        transfer_duration_ms=duration_ms,
        effective_throughput_mib_s=throughput_mib_s(byte_count, duration_ms),
        bytes_sent=snapshot_totals["bytes_sent"] or int(trace_summary["bytes_sent"]),
        bytes_received=snapshot_totals["bytes_received"]
        or int(trace_summary["bytes_received"]),
        chunks_sent=snapshot_totals["chunks_sent"] or int(trace_summary["chunks_sent"]),
        retries=snapshot_totals["retries"] or int(trace_summary["retries"]),
        timeouts=(snapshot_totals["timeouts"] or int(trace_summary["timeouts"]))
        + (1 if timed_out else 0),
        success=exit_code == 0,
        reason_code=trace_summary["reason_code"] or parsed.get("reason"),
        committed_object_verified=False,
        get_payload_matches_put_payload=None,
    )
    record = {
        "operation": operation,
        "repetition": repetition,
        "command": command,
        "exit_code": exit_code,
        "stdout": stdout,
        "stderr": stderr,
        "parsed_stdout": parsed,
        "trace_jsonl": str(trace_path) if trace_path else None,
        "metrics": metrics.to_dict(),
    }
    command_path = run_dir / "commands" / f"{operation}_{repetition:03d}.json"
    command_path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    return record


def _find_binary(name: str) -> Path | None:
    env_name = name.upper().replace("-", "_")
    if os.environ.get(env_name):
        candidate = Path(os.environ[env_name])
        if candidate.exists():
            return candidate
    for candidate in [
        REPO_ROOT / "bifrostd" / "target" / "debug" / name,
        REPO_ROOT / "target" / "debug" / name,
    ]:
        if candidate.exists():
            return candidate
    found = shutil.which(name)
    return Path(found) if found else None


def _resolve_paths(paths: tuple[PathConfig, ...]) -> tuple[PathConfig, ...]:
    resolved: list[PathConfig] = []
    for path in paths:
        endpoint = path.endpoint or f"127.0.0.1:{_free_port()}"
        resolved.append(
            PathConfig(
                name=path.name,
                endpoint=endpoint,
                start_daemon=path.start_daemon,
            )
        )
    return tuple(resolved)


def _primary_endpoint(paths: tuple[PathConfig, ...]) -> str:
    for path in paths:
        if path.start_daemon:
            return str(path.endpoint)
    return str(paths[0].endpoint)


def _primary_path_name(paths: tuple[PathConfig, ...]) -> str:
    for path in paths:
        if path.start_daemon:
            return path.name
    return paths[0].name


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_endpoint(endpoint: str, timeout_seconds: int) -> None:
    host, port_text = endpoint.rsplit(":", 1)
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, int(port_text)), timeout=0.2):
                return
        except OSError:
            time.sleep(0.05)
    raise ContextStormError(f"daemon did not become ready: {endpoint}")


def _stop_daemon(daemon: dict[str, Any], run_dir: Path) -> dict[str, Any]:
    process = daemon["process"]
    if process.poll() is not None:
        stdout, stderr = process.communicate(timeout=1)
    else:
        process.terminate()
        try:
            stdout, stderr = process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate(timeout=5)
    record = {
        "path_name": daemon["path_name"],
        "command": daemon["command"],
        "exit_code": process.returncode,
        "stdout": stdout,
        "stderr": stderr,
        "trace_jsonl": daemon["trace_jsonl"],
    }
    record_path = run_dir / "commands" / f"daemon_{daemon['path_name']}.json"
    record_path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    return record


def _parse_json_stdout(stdout: str) -> dict[str, Any]:
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return {}


def _environment() -> dict[str, Any]:
    return {
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "repo_root": str(REPO_ROOT),
    }


def _scenario_to_dict(scenario: Scenario) -> dict[str, Any]:
    return {
        "name": scenario.name,
        "object_size_bytes": scenario.object_size_bytes,
        "chunk_size_bytes": scenario.chunk_size_bytes,
        "object_type": scenario.object_type,
        "model_shape": scenario.model_shape,
        "paths": [path.__dict__ for path in scenario.paths],
        "operations": list(scenario.operations),
        "repetitions": scenario.repetitions,
        "fault_profile": scenario.fault_profile,
        "timeout_seconds": scenario.timeout_seconds,
    }


def _load_simple_yaml(path: Path) -> dict[str, Any]:
    path = _resolve_contextstorm_path(path)
    text = path.read_text()
    if text.lstrip().startswith("{"):
        return json.loads(text)
    lines = [
        line.rstrip()
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    result: dict[str, Any] = {}
    index = 0
    while index < len(lines):
        line = lines[index]
        if line.startswith(" "):
            raise ContextStormError(f"unexpected indentation in {path}: {line}")
        key, value = _split_key_value(line, path)
        if value:
            result[key] = _parse_scalar(value)
            index += 1
            continue
        index += 1
        block: list[str] = []
        while index < len(lines) and lines[index].startswith("  "):
            block.append(lines[index])
            index += 1
        result[key] = _parse_block(block, path)
    return result


def _resolve_contextstorm_path(path: Path) -> Path:
    if path.exists() or path.is_absolute():
        return path
    for candidate in (REPO_ROOT / "contextstorm" / path, REPO_ROOT / path):
        if candidate.exists():
            return candidate
    return path


def _parse_block(block: list[str], path: Path) -> Any:
    if not block:
        return None
    if block[0].startswith("  - "):
        values: list[Any] = []
        current: dict[str, Any] | None = None
        for line in block:
            if line.startswith("  - "):
                item = line[4:]
                if ": " in item or item.endswith(":"):
                    key, value = _split_key_value(item, path)
                    current = {key: _parse_scalar(value) if value else None}
                    values.append(current)
                else:
                    current = None
                    values.append(_parse_scalar(item))
                continue
            if line.startswith("    ") and current is not None:
                key, value = _split_key_value(line.strip(), path)
                current[key] = _parse_scalar(value)
                continue
            raise ContextStormError(f"invalid list block in {path}: {line}")
        return values
    mapping: dict[str, Any] = {}
    for line in block:
        key, value = _split_key_value(line.strip(), path)
        if not value:
            nested: dict[str, Any] = {}
            mapping[key] = nested
        else:
            mapping[key] = _parse_scalar(value)
    return mapping


def _split_key_value(line: str, path: Path) -> tuple[str, str]:
    if ":" not in line:
        raise ContextStormError(f"expected key/value in {path}: {line}")
    key, value = line.split(":", 1)
    return key.strip(), value.strip()


def _parse_scalar(value: str) -> Any:
    if value == "":
        return None
    if value in {"true", "True"}:
        return True
    if value in {"false", "False"}:
        return False
    if value in {"null", "None"}:
        return None
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [_parse_scalar(part.strip()) for part in inner.split(",")]
    if value.startswith('"') and value.endswith('"'):
        return value[1:-1]
    try:
        return int(value)
    except ValueError:
        return value

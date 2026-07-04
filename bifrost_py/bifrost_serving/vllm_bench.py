"""Optional Phase 6 integration with ``vllm bench serve``."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

DEFAULT_RESULT_FILENAME = "vllm_bench_serve_result.json"
SUPPORTED_BACKENDS = {"openai", "openai-chat"}
_OPTION_RE = re.compile(r"(?<![\w-])(--[A-Za-z][A-Za-z0-9-]*)")


@dataclass(frozen=True, slots=True)
class VLLMBenchAvailability:
    available: bool
    status: str
    reason: str = ""
    vllm_path: str | None = None
    version: str | None = None
    help_text: str = ""
    supported_options: frozenset[str] = frozenset()

    def to_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "status": self.status,
            "reason": self.reason,
            "vllm_path": self.vllm_path,
            "version": self.version,
            "supported_options": sorted(self.supported_options),
        }


@dataclass(frozen=True, slots=True)
class VLLMBenchServeConfig:
    base_url: str
    endpoint: str
    result_dir: Path
    backend: str = "openai"
    dataset_path: Path | None = None
    num_prompts: int = 8
    num_warmups: int = 0
    request_rate: float | None = None
    max_concurrency: int | None = None
    save_result: bool = True
    save_detailed: bool = True
    result_filename: str = DEFAULT_RESULT_FILENAME
    metadata: dict[str, str] = field(default_factory=dict)
    extra_args: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class VLLMBenchCommand:
    command: list[str]
    warnings: list[str]
    expected_result_path: Path
    synthetic_dataset_path: Path | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "command": self.command,
            "warnings": self.warnings,
            "expected_result_path": str(self.expected_result_path),
            "synthetic_dataset_path": (
                str(self.synthetic_dataset_path) if self.synthetic_dataset_path else None
            ),
        }


@dataclass(frozen=True, slots=True)
class VLLMBenchRunResult:
    status: str
    command: list[str]
    summary: dict[str, Any]
    command_path: Path
    summary_path: Path | None = None
    raw_result_path: Path | None = None
    returncode: int | None = None
    reason: str = ""


class VLLMBenchSafetyError(RuntimeError):
    """Raised when a real vLLM bench invocation is not explicitly allowed."""


def check_vllm_bench_available(
    *,
    vllm_command: str = "vllm",
    timeout_seconds: float = 10.0,
) -> VLLMBenchAvailability:
    path = shutil.which(vllm_command)
    if not path:
        return VLLMBenchAvailability(
            available=False,
            status="skipped",
            reason="vLLM CLI is not on PATH; skipping optional `vllm bench serve`.",
            version=_metadata_version(),
        )

    version = _detect_vllm_version(path, timeout_seconds)
    try:
        help_result = subprocess.run(
            [path, "bench", "serve", "--help"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_seconds,
            check=False,
        )
    except Exception as exc:
        return VLLMBenchAvailability(
            available=False,
            status="skipped",
            reason=f"`vllm bench serve --help` could not run: {exc}",
            vllm_path=path,
            version=version,
        )

    help_text = f"{help_result.stdout}\n{help_result.stderr}"
    if help_result.returncode != 0:
        return VLLMBenchAvailability(
            available=False,
            status="skipped",
            reason="`vllm bench serve --help` returned a non-zero exit status.",
            vllm_path=path,
            version=version,
            help_text=help_text,
            supported_options=frozenset(parse_help_options(help_text)),
        )
    return VLLMBenchAvailability(
        available=True,
        status="available",
        vllm_path=path,
        version=version,
        help_text=help_text,
        supported_options=frozenset(parse_help_options(help_text)),
    )


def parse_help_options(help_text: str) -> set[str]:
    return set(_OPTION_RE.findall(help_text))


def build_vllm_bench_serve_command(
    config: VLLMBenchServeConfig,
    availability: VLLMBenchAvailability,
) -> VLLMBenchCommand:
    _validate_config(config)
    if not availability.vllm_path:
        raise ValueError("vLLM CLI path is required to build a command")
    supported = availability.supported_options
    help_text = availability.help_text.lower()
    command = [availability.vllm_path, "bench", "serve"]
    warnings: list[str] = []

    _append_option(command, warnings, supported, "--backend", config.backend)
    _append_option(command, warnings, supported, "--base-url", config.base_url)
    _append_option(command, warnings, supported, "--endpoint", config.endpoint)
    _append_option(command, warnings, supported, "--num-prompts", str(config.num_prompts))
    if config.num_warmups > 0:
        _append_option(command, warnings, supported, "--num-warmups", str(config.num_warmups))
    if config.request_rate is not None:
        _append_option(command, warnings, supported, "--request-rate", str(config.request_rate))
    if config.max_concurrency is not None:
        _append_option(
            command,
            warnings,
            supported,
            "--max-concurrency",
            str(config.max_concurrency),
        )

    synthetic_dataset_path = _append_dataset_args(command, warnings, supported, help_text, config)

    if config.save_result:
        _append_flag(command, warnings, supported, "--save-result")
    if config.save_detailed:
        _append_flag(command, warnings, supported, "--save-detailed")
    _append_option(command, warnings, supported, "--result-dir", str(config.result_dir))
    _append_option(command, warnings, supported, "--result-filename", config.result_filename)
    for key in sorted(config.metadata):
        _append_option(command, warnings, supported, "--metadata", f"{key}={config.metadata[key]}")
    command.extend(config.extra_args)

    return VLLMBenchCommand(
        command=command,
        warnings=warnings,
        expected_result_path=config.result_dir / config.result_filename,
        synthetic_dataset_path=synthetic_dataset_path,
    )


def run_vllm_bench_serve(
    config: VLLMBenchServeConfig,
    *,
    allow_real_vllm_bench: bool = False,
    timeout_seconds: float | None = None,
) -> VLLMBenchRunResult:
    allowed = allow_real_vllm_bench or os.environ.get("BIFROST_RUN_VLLM_BENCH") == "1"
    availability = check_vllm_bench_available()
    if not availability.available:
        return _skipped_result(config, availability)

    command = build_vllm_bench_serve_command(config, availability)
    command_path = _write_command(config, availability, command)
    if not allowed:
        raise VLLMBenchSafetyError(
            "refusing to run `vllm bench serve` without --allow-real-vllm-bench "
            "or BIFROST_RUN_VLLM_BENCH=1"
        )

    started = time.time()
    completed = subprocess.run(
        command.command,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout_seconds,
        check=False,
    )
    ended = time.time()
    raw_result_path = find_vllm_bench_result(config.result_dir, command.expected_result_path)
    ingested = ingest_vllm_bench_result(raw_result_path) if raw_result_path else _missing_ingestion()
    summary: dict[str, Any] = {
        "schema_version": "bifrost.vllm_bench_serve_run.v1",
        "status": "completed" if completed.returncode == 0 else "failed",
        "returncode": completed.returncode,
        "started_unix_s": started,
        "ended_unix_s": ended,
        "run_duration_s": max(0.0, ended - started),
        "availability": availability.to_dict(),
        "command": command.to_dict(),
        "stdout_tail": completed.stdout[-4000:],
        "stderr_tail": completed.stderr[-4000:],
        "vllm_bench_result": ingested,
    }
    summary_path = config.result_dir / "bifrost_vllm_bench_summary.json"
    _write_json(summary_path, summary)
    return VLLMBenchRunResult(
        status=summary["status"],
        command=command.command,
        summary=summary,
        command_path=command_path,
        summary_path=summary_path,
        raw_result_path=raw_result_path,
        returncode=completed.returncode,
    )


def dry_run_vllm_bench_serve(config: VLLMBenchServeConfig) -> VLLMBenchRunResult:
    availability = check_vllm_bench_available()
    if not availability.available:
        return _skipped_result(config, availability)
    command = build_vllm_bench_serve_command(config, availability)
    command_path = _write_command(config, availability, command)
    summary = {
        "schema_version": "bifrost.vllm_bench_serve_run.v1",
        "status": "dry_run",
        "availability": availability.to_dict(),
        "command": command.to_dict(),
    }
    summary_path = config.result_dir / "bifrost_vllm_bench_summary.json"
    _write_json(summary_path, summary)
    return VLLMBenchRunResult(
        status="dry_run",
        command=command.command,
        summary=summary,
        command_path=command_path,
        summary_path=summary_path,
    )


def ingest_vllm_bench_result(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    flattened = _flatten(raw)
    ttft = _matching_fields(flattened, ("ttft", "time_to_first_token"))
    latency = _matching_fields(flattened, ("latency", "e2e", "end_to_end"))
    output_token_latency = _matching_fields(
        flattened,
        ("inter_token", "itl", "output_token_latency", "time_per_output_token"),
    )
    return {
        "schema_version": "bifrost.vllm_bench_serve_ingest.v1",
        "status": "ok",
        "raw_result_path": str(path),
        "request_count": _first_number(
            flattened,
            (
                "request_count",
                "num_requests",
                "num_prompts",
                "total_requests",
                "completed_requests",
                "successful_requests",
            ),
            integer=True,
        ),
        "throughput_rps": _first_number(
            flattened,
            ("request_throughput", "requests_per_second", "throughput_rps", "rps"),
        ),
        "token_throughput": _first_number(
            flattened,
            ("token_throughput", "output_throughput", "tokens_per_second"),
        ),
        "ttft": ttft,
        "latency": latency,
        "output_token_latency": output_token_latency,
        "error_count": _error_count(raw, flattened),
        "benchmark_args": _benchmark_args(raw),
        "raw_top_level_keys": sorted(raw) if isinstance(raw, dict) else [],
    }


def find_vllm_bench_result(result_dir: Path, expected: Path) -> Path | None:
    if expected.exists():
        return expected
    if not result_dir.exists():
        return None
    candidates = [
        path
        for path in result_dir.glob("*.json")
        if path.name not in {"bifrost_vllm_bench_command.json", "bifrost_vllm_bench_summary.json"}
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run optional `vllm bench serve` for Phase 6")
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--result-dir", required=True, type=Path)
    parser.add_argument("--backend", choices=sorted(SUPPORTED_BACKENDS), default="openai")
    parser.add_argument("--dataset-path", type=Path, default=None)
    parser.add_argument("--num-prompts", type=int, required=True)
    parser.add_argument("--num-warmups", type=int, default=0)
    parser.add_argument("--request-rate", type=float, default=None)
    parser.add_argument("--max-concurrency", type=int, default=None)
    parser.add_argument("--metadata", action="append", default=[], metavar="KEY=VALUE")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--allow-real-vllm-bench", action="store_true")
    parser.add_argument("--json", action="store_true")
    try:
        args = parser.parse_args(argv)
        config = VLLMBenchServeConfig(
            base_url=args.base_url,
            endpoint=args.endpoint,
            result_dir=args.result_dir,
            backend=args.backend,
            dataset_path=args.dataset_path,
            num_prompts=args.num_prompts,
            num_warmups=args.num_warmups,
            request_rate=args.request_rate,
            max_concurrency=args.max_concurrency,
            metadata=_parse_metadata(args.metadata),
        )
        result = (
            dry_run_vllm_bench_serve(config)
            if args.dry_run
            else run_vllm_bench_serve(
                config,
                allow_real_vllm_bench=args.allow_real_vllm_bench,
            )
        )
        if args.json:
            print(json.dumps(result.summary, indent=2, sort_keys=True))
        else:
            print(f"{result.status}: wrote {result.command_path}")
        return 1 if result.status == "failed" else 0
    except SystemExit:
        raise
    except VLLMBenchSafetyError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"bifrost vllm bench serve failed: {exc}", file=sys.stderr)
        return 2


def _detect_vllm_version(path: str, timeout_seconds: float) -> str | None:
    try:
        result = subprocess.run(
            [path, "--version"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_seconds,
            check=False,
        )
    except Exception:
        return None
    text = (result.stdout or result.stderr).strip()
    return text.splitlines()[0] if text else _metadata_version()


def _metadata_version() -> str | None:
    try:
        return importlib.metadata.version("vllm")
    except importlib.metadata.PackageNotFoundError:
        return None


def _append_option(
    command: list[str],
    warnings: list[str],
    supported: set[str] | frozenset[str],
    option: str,
    value: str,
) -> None:
    if option in supported:
        command.extend([option, value])
    else:
        warnings.append(f"skipped unsupported vLLM bench option {option}")


def _append_flag(
    command: list[str],
    warnings: list[str],
    supported: set[str] | frozenset[str],
    option: str,
) -> None:
    if option in supported:
        command.append(option)
    else:
        warnings.append(f"skipped unsupported vLLM bench flag {option}")


def _append_dataset_args(
    command: list[str],
    warnings: list[str],
    supported: set[str] | frozenset[str],
    help_text: str,
    config: VLLMBenchServeConfig,
) -> Path | None:
    if config.dataset_path is not None:
        _append_option(command, warnings, supported, "--dataset-path", str(config.dataset_path))
        if "--dataset-name" in supported and "sharegpt" in help_text:
            command.extend(["--dataset-name", "sharegpt"])
        return None

    if "--dataset-name" in supported and "random" in help_text:
        command.extend(["--dataset-name", "random"])
        return None

    if "--dataset-path" in supported:
        path = config.result_dir / "synthetic_sharegpt_dataset.json"
        _write_synthetic_sharegpt(path, config.num_prompts)
        if "--dataset-name" in supported and "sharegpt" in help_text:
            command.extend(["--dataset-name", "sharegpt"])
        command.extend(["--dataset-path", str(path)])
        return path

    warnings.append(
        "no compatible dataset option detected; provide extra_args for this vLLM version"
    )
    return None


def _write_synthetic_sharegpt(path: Path, count: int) -> None:
    rows = []
    for index in range(count):
        rows.append(
            {
                "id": f"bifrost-synthetic-{index:05d}",
                "conversations": [
                    {
                        "from": "human",
                        "value": (
                            "BIFROST Phase 6 synthetic repeated-prefix prompt. "
                            f"Request {index}. Summarize the cache benchmark boundary."
                        ),
                    },
                    {"from": "gpt", "value": "Synthetic reference answer."},
                ],
            }
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    flattened: dict[str, Any] = {}
    if isinstance(value, dict):
        for key, child in value.items():
            child_key = f"{prefix}.{key}" if prefix else str(key)
            flattened.update(_flatten(child, child_key))
    elif isinstance(value, list):
        if all(not isinstance(item, (dict, list)) for item in value):
            flattened[prefix] = value
        else:
            for index, child in enumerate(value):
                flattened.update(_flatten(child, f"{prefix}.{index}"))
    elif prefix:
        flattened[prefix] = value
    return flattened


def _matching_fields(flattened: dict[str, Any], needles: tuple[str, ...]) -> dict[str, Any]:
    matched: dict[str, Any] = {}
    for key, value in flattened.items():
        lower_key = key.lower()
        if any(needle in lower_key for needle in needles) and isinstance(value, (int, float)):
            matched[key] = value
    return matched


def _first_number(
    flattened: dict[str, Any],
    key_needles: tuple[str, ...],
    *,
    integer: bool = False,
) -> int | float | None:
    for needle in key_needles:
        for key, value in flattened.items():
            if needle in key.lower() and isinstance(value, (int, float)) and not isinstance(value, bool):
                return int(value) if integer else float(value)
    return None


def _error_count(raw: Any, flattened: dict[str, Any]) -> int | None:
    value = _first_number(
        flattened,
        ("error_count", "failed_requests", "num_errors", "num_failed_requests"),
        integer=True,
    )
    if value is not None:
        return int(value)
    if isinstance(raw, dict):
        for key in ("errors", "failed", "failures"):
            errors = raw.get(key)
            if isinstance(errors, list):
                return len(errors)
    return None


def _benchmark_args(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    for key in ("args", "benchmark_args", "metadata"):
        value = raw.get(key)
        if isinstance(value, dict):
            return value
    return None


def _missing_ingestion() -> dict[str, Any]:
    return {
        "schema_version": "bifrost.vllm_bench_serve_ingest.v1",
        "status": "missing",
        "reason": "vLLM bench did not produce a JSON result file at a detected path.",
    }


def _skipped_result(
    config: VLLMBenchServeConfig,
    availability: VLLMBenchAvailability,
) -> VLLMBenchRunResult:
    config.result_dir.mkdir(parents=True, exist_ok=True)
    command_path = config.result_dir / "bifrost_vllm_bench_command.json"
    summary = {
        "schema_version": "bifrost.vllm_bench_serve_run.v1",
        "status": "skipped",
        "reason": availability.reason,
        "availability": availability.to_dict(),
    }
    _write_json(command_path, summary)
    summary_path = config.result_dir / "bifrost_vllm_bench_summary.json"
    _write_json(summary_path, summary)
    return VLLMBenchRunResult(
        status="skipped",
        command=[],
        summary=summary,
        command_path=command_path,
        summary_path=summary_path,
        reason=availability.reason,
    )


def _write_command(
    config: VLLMBenchServeConfig,
    availability: VLLMBenchAvailability,
    command: VLLMBenchCommand,
) -> Path:
    path = config.result_dir / "bifrost_vllm_bench_command.json"
    _write_json(
        path,
        {
            "schema_version": "bifrost.vllm_bench_serve_command.v1",
            "availability": availability.to_dict(),
            "command": command.to_dict(),
        },
    )
    return path


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _parse_metadata(values: list[str]) -> dict[str, str]:
    metadata: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise ValueError(f"metadata must be KEY=VALUE: {value!r}")
        key, metadata_value = value.split("=", 1)
        if not key:
            raise ValueError("metadata key must be non-empty")
        metadata[key] = metadata_value
    return metadata


def _validate_config(config: VLLMBenchServeConfig) -> None:
    if config.backend not in SUPPORTED_BACKENDS:
        raise ValueError(f"unsupported vLLM bench backend: {config.backend}")
    if not config.base_url:
        raise ValueError("base_url must be non-empty")
    if not config.endpoint:
        raise ValueError("endpoint must be non-empty")
    if config.num_prompts <= 0:
        raise ValueError("num_prompts must be positive")
    if config.num_warmups < 0:
        raise ValueError("num_warmups must be non-negative")
    if config.request_rate is not None and config.request_rate <= 0:
        raise ValueError("request_rate must be positive when provided")
    if config.max_concurrency is not None and config.max_concurrency <= 0:
        raise ValueError("max_concurrency must be positive when provided")
    if config.dataset_path is not None and not config.dataset_path.exists():
        raise ValueError(f"dataset_path does not exist: {config.dataset_path}")
    if not config.result_filename:
        raise ValueError("result_filename must be non-empty")


__all__ = [
    "DEFAULT_RESULT_FILENAME",
    "SUPPORTED_BACKENDS",
    "VLLMBenchAvailability",
    "VLLMBenchCommand",
    "VLLMBenchRunResult",
    "VLLMBenchSafetyError",
    "VLLMBenchServeConfig",
    "build_vllm_bench_serve_command",
    "check_vllm_bench_available",
    "dry_run_vllm_bench_serve",
    "find_vllm_bench_result",
    "ingest_vllm_bench_result",
    "main",
    "parse_help_options",
    "run_vllm_bench_serve",
]

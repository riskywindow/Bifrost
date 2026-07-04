"""Serving benchmark request JSONL schema for Phase 6."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class RequestMetadata:
    workload_name: str
    prefix_id: str
    repeat_group: int
    expected_cache_reuse: bool
    prompt_token_estimate: int | None = None
    phase: str = "measured"

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "workload_name": self.workload_name,
            "prefix_id": self.prefix_id,
            "repeat_group": self.repeat_group,
            "expected_cache_reuse": self.expected_cache_reuse,
            "phase": self.phase,
        }
        if self.prompt_token_estimate is not None:
            data["prompt_token_estimate"] = self.prompt_token_estimate
        return data


@dataclass(frozen=True, slots=True)
class ServingRequest:
    request_id: str
    prompt: str
    max_tokens: int
    temperature: float
    top_p: float
    metadata: RequestMetadata
    stop: list[str] | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "request_id": self.request_id,
            "prompt": self.prompt,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "top_p": self.top_p,
            "metadata": self.metadata.to_dict(),
        }
        if self.stop is not None:
            data["stop"] = list(self.stop)
        return data

    def to_json_line(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))


def request_from_dict(data: dict[str, Any]) -> ServingRequest:
    required = ("request_id", "prompt", "max_tokens", "temperature", "top_p", "metadata")
    missing = [name for name in required if name not in data]
    if missing:
        raise ValueError(f"serving request missing fields: {missing}")
    metadata_data = data["metadata"]
    if not isinstance(metadata_data, dict):
        raise ValueError("serving request metadata must be an object")
    metadata_required = (
        "workload_name",
        "prefix_id",
        "repeat_group",
        "expected_cache_reuse",
    )
    metadata_missing = [name for name in metadata_required if name not in metadata_data]
    if metadata_missing:
        raise ValueError(f"serving request metadata missing fields: {metadata_missing}")
    stop = data.get("stop")
    if stop is not None and not isinstance(stop, list):
        raise ValueError("serving request stop must be a list when present")
    request = ServingRequest(
        request_id=str(data["request_id"]),
        prompt=str(data["prompt"]),
        max_tokens=int(data["max_tokens"]),
        temperature=float(data["temperature"]),
        top_p=float(data["top_p"]),
        stop=[str(item) for item in stop] if stop is not None else None,
        metadata=RequestMetadata(
            workload_name=str(metadata_data["workload_name"]),
            prefix_id=str(metadata_data["prefix_id"]),
            repeat_group=int(metadata_data["repeat_group"]),
            expected_cache_reuse=bool(metadata_data["expected_cache_reuse"]),
            prompt_token_estimate=(
                int(metadata_data["prompt_token_estimate"])
                if metadata_data.get("prompt_token_estimate") is not None
                else None
            ),
            phase=str(metadata_data.get("phase", data.get("phase", "measured"))),
        ),
    )
    validate_request(request)
    return request


def request_from_json_line(line: str) -> ServingRequest:
    return request_from_dict(json.loads(line))


def validate_request(request: ServingRequest) -> None:
    if not request.request_id:
        raise ValueError("request_id must be non-empty")
    if not request.prompt:
        raise ValueError("prompt must be non-empty")
    if request.max_tokens <= 0:
        raise ValueError("max_tokens must be positive")
    if request.temperature < 0:
        raise ValueError("temperature must be non-negative")
    if not 0 < request.top_p <= 1:
        raise ValueError("top_p must be in (0, 1]")
    if not request.metadata.workload_name:
        raise ValueError("metadata.workload_name must be non-empty")
    if not request.metadata.prefix_id:
        raise ValueError("metadata.prefix_id must be non-empty")
    if request.metadata.phase not in {"engine_warmup", "cache_population", "measured"}:
        raise ValueError("metadata.phase must be engine_warmup, cache_population, or measured")
    if request.metadata.repeat_group < 0:
        raise ValueError("metadata.repeat_group must be non-negative")
    if (
        request.metadata.prompt_token_estimate is not None
        and request.metadata.prompt_token_estimate <= 0
    ):
        raise ValueError("metadata.prompt_token_estimate must be positive when present")


def write_jsonl(path: str | bytes | "os.PathLike[str]", requests: list[ServingRequest]) -> None:
    from pathlib import Path

    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        "".join(request.to_json_line() + "\n" for request in requests),
        encoding="utf-8",
    )


def read_jsonl(path: str | bytes | "os.PathLike[str]") -> list[ServingRequest]:
    from pathlib import Path

    requests: list[ServingRequest] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        requests.append(request_from_json_line(line))
    return requests


__all__ = [
    "RequestMetadata",
    "ServingRequest",
    "read_jsonl",
    "request_from_dict",
    "request_from_json_line",
    "validate_request",
    "write_jsonl",
]

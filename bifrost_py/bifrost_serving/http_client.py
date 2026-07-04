"""OpenAI-compatible HTTP client utilities for Phase 6 serving benchmarks."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, Iterable
from urllib.parse import urljoin

from .request_schema import ServingRequest


DEFAULT_COMPLETIONS_ENDPOINT = "/v1/completions"


@dataclass(frozen=True, slots=True)
class OpenAIClientConfig:
    base_url: str
    endpoint: str = DEFAULT_COMPLETIONS_ENDPOINT
    model: str = "bifrost-fake-model"
    timeout_s: float = 30.0
    concurrency: int = 1
    headers: dict[str, str] = field(default_factory=dict)
    include_serving_metadata: bool = True
    stream: bool = False


@dataclass(frozen=True, slots=True)
class ServingResponseTiming:
    request_id: str
    request_start: float
    first_token_time: float | None
    response_end: float
    status_code: int | None
    error: str | None
    output_text: str
    response_json: dict[str, Any] | None

    @property
    def latency_s(self) -> float:
        return self.response_end - self.request_start

    @property
    def ttft_s(self) -> float | None:
        if self.first_token_time is None:
            return None
        return self.first_token_time - self.request_start


class OpenAICompatibleClient:
    """Small dependency-free client for OpenAI-compatible completion APIs."""

    def __init__(self, config: OpenAIClientConfig) -> None:
        if config.concurrency <= 0:
            raise ValueError("concurrency must be positive")
        if config.timeout_s <= 0:
            raise ValueError("timeout_s must be positive")
        self.config = config

    def send(self, request: ServingRequest) -> ServingResponseTiming:
        payload = build_request_json(
            request,
            endpoint=self.config.endpoint,
            model=self.config.model,
            stream=self.config.stream,
            include_serving_metadata=self.config.include_serving_metadata,
        )
        request_start = time.perf_counter()
        first_token_time: float | None = None
        status_code: int | None = None
        output_text = ""
        response_json: dict[str, Any] | None = None
        error: str | None = None

        http_request = urllib.request.Request(
            _join_url(self.config.base_url, self.config.endpoint),
            data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                **self.config.headers,
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(  # noqa: S310 - benchmark URL is caller-provided.
                http_request,
                timeout=self.config.timeout_s,
            ) as response:
                status_code = int(response.status)
                if self.config.stream:
                    first_token_time, output_text, response_json = _read_streaming_response(
                        response
                    )
                else:
                    body = response.read()
                    response_json = json.loads(body.decode("utf-8"))
                    output_text = parse_response_text(response_json)
        except urllib.error.HTTPError as exc:
            status_code = int(exc.code)
            body = exc.read().decode("utf-8", errors="replace")
            error = body or str(exc)
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            error = str(exc)

        response_end = time.perf_counter()
        return ServingResponseTiming(
            request_id=request.request_id,
            request_start=request_start,
            first_token_time=first_token_time,
            response_end=response_end,
            status_code=status_code,
            error=error,
            output_text=output_text,
            response_json=response_json,
        )

    def send_many(self, requests: Iterable[ServingRequest]) -> list[ServingResponseTiming]:
        request_list = list(requests)
        if not request_list:
            return []
        results: list[ServingResponseTiming | None] = [None] * len(request_list)
        with ThreadPoolExecutor(max_workers=self.config.concurrency) as executor:
            future_to_index = {
                executor.submit(self.send, request): index
                for index, request in enumerate(request_list)
            }
            for future in as_completed(future_to_index):
                results[future_to_index[future]] = future.result()
        return [result for result in results if result is not None]


def build_request_json(
    request: ServingRequest,
    *,
    endpoint: str = DEFAULT_COMPLETIONS_ENDPOINT,
    model: str = "bifrost-fake-model",
    stream: bool = False,
    include_serving_metadata: bool = True,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model,
        "max_tokens": request.max_tokens,
        "temperature": request.temperature,
        "top_p": request.top_p,
        "stream": stream,
        "ignore_eos": True,
    }
    if request.stop is not None:
        payload["stop"] = list(request.stop)
    if _is_chat_endpoint(endpoint):
        payload["messages"] = [{"role": "user", "content": request.prompt}]
    else:
        payload["prompt"] = request.prompt
    if include_serving_metadata:
        payload["request_id"] = request.request_id
        payload["metadata"] = request.metadata.to_dict()
    return payload


def parse_response_text(response_json: dict[str, Any]) -> str:
    choices = response_json.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    first = choices[0]
    if not isinstance(first, dict):
        return ""
    text = first.get("text")
    if isinstance(text, str):
        return text
    message = first.get("message")
    if isinstance(message, dict) and isinstance(message.get("content"), str):
        return str(message["content"])
    delta = first.get("delta")
    if isinstance(delta, dict) and isinstance(delta.get("content"), str):
        return str(delta["content"])
    return ""


def _read_streaming_response(response: Any) -> tuple[float | None, str, dict[str, Any] | None]:
    first_token_time: float | None = None
    chunks: list[str] = []
    last_json: dict[str, Any] | None = None
    for raw_line in response:
        line = raw_line.decode("utf-8", errors="replace").strip()
        if not line:
            continue
        if line.startswith("data:"):
            line = line[5:].strip()
        if line == "[DONE]":
            break
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        last_json = data
        text = parse_response_text(data)
        if text:
            if first_token_time is None:
                first_token_time = time.perf_counter()
            chunks.append(text)
    return first_token_time, "".join(chunks), last_json


def _join_url(base_url: str, endpoint: str) -> str:
    return urljoin(base_url.rstrip("/") + "/", endpoint.lstrip("/"))


def _is_chat_endpoint(endpoint: str) -> bool:
    return "chat/completions" in endpoint.strip("/")


__all__ = [
    "DEFAULT_COMPLETIONS_ENDPOINT",
    "OpenAIClientConfig",
    "OpenAICompatibleClient",
    "ServingResponseTiming",
    "build_request_json",
    "parse_response_text",
]

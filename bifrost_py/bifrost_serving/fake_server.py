"""Dependency-free fake OpenAI-compatible server for Phase 6 CI tests."""

from __future__ import annotations

import argparse
import json
import threading
import time
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


@dataclass(slots=True)
class FakeOpenAIServerConfig:
    host: str = "127.0.0.1"
    port: int = 8000
    simulate_cache: bool = False
    base_delay_ms: float = 0.0
    cache_hit_delay_ms: float = 0.0
    per_token_delay_ms: float = 0.0
    model: str = "bifrost-fake-model"


@dataclass(slots=True)
class FakeOpenAIMetrics:
    requests: int = 0
    completions: int = 0
    chat_completions: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    errors: int = 0
    output_tokens: int = 0
    seen_prefixes: set[str] = field(default_factory=set)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "bifrost.fake_openai_metrics.v1",
            "requests": self.requests,
            "completions": self.completions,
            "chat_completions": self.chat_completions,
            "cache_hits": self.cache_hits,
            "cache_misses": self.cache_misses,
            "errors": self.errors,
            "output_tokens": self.output_tokens,
            "seen_prefix_count": len(self.seen_prefixes),
        }


class FakeOpenAIServer:
    def __init__(self, config: FakeOpenAIServerConfig) -> None:
        self.config = config
        self.metrics = FakeOpenAIMetrics()
        self.lock = threading.Lock()
        handler = _make_handler(self)
        self.httpd = ThreadingHTTPServer((config.host, config.port), handler)
        self.thread: threading.Thread | None = None

    @property
    def host(self) -> str:
        return str(self.httpd.server_address[0])

    @property
    def port(self) -> int:
        return int(self.httpd.server_address[1])

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def serve_forever(self) -> None:
        self.httpd.serve_forever()

    def start_in_thread(self) -> None:
        self.thread = threading.Thread(target=self.serve_forever, daemon=True)
        self.thread.start()

    def shutdown(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        if self.thread is not None:
            self.thread.join(timeout=5)


def create_server(config: FakeOpenAIServerConfig) -> FakeOpenAIServer:
    return FakeOpenAIServer(config)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--simulate-cache", choices=("true", "false"), default="false")
    parser.add_argument("--base-delay-ms", type=float, default=0.0)
    parser.add_argument("--cache-hit-delay-ms", type=float, default=0.0)
    parser.add_argument("--per-token-delay-ms", type=float, default=0.0)
    args = parser.parse_args(argv)

    server = create_server(
        FakeOpenAIServerConfig(
            host=args.host,
            port=args.port,
            simulate_cache=args.simulate_cache == "true",
            base_delay_ms=args.base_delay_ms,
            cache_hit_delay_ms=args.cache_hit_delay_ms,
            per_token_delay_ms=args.per_token_delay_ms,
        )
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 130
    finally:
        server.shutdown()
    return 0


def _make_handler(server: FakeOpenAIServer) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - stdlib handler API.
            if self.path == "/healthz":
                self._write_json(HTTPStatus.OK, {"ok": True})
                return
            if self.path == "/metrics":
                with server.lock:
                    metrics = server.metrics.to_dict()
                self._write_json(HTTPStatus.OK, metrics)
                return
            self._write_json(HTTPStatus.NOT_FOUND, {"error": "not found"})

        def do_POST(self) -> None:  # noqa: N802 - stdlib handler API.
            if self.path not in {"/v1/completions", "/v1/chat/completions"}:
                self._record_error()
                self._write_json(HTTPStatus.NOT_FOUND, {"error": "unsupported endpoint"})
                return

            try:
                payload = self._read_json()
                if _should_fail(payload):
                    raise ValueError("forced fake server error")
                prefix_id = _prefix_id(payload)
                max_tokens = max(1, int(payload.get("max_tokens", 16)))
                is_chat = self.path == "/v1/chat/completions"
                cache_hit = self._record_request(prefix_id, is_chat=is_chat)
                _sleep_for_request(server.config, cache_hit=cache_hit, max_tokens=max_tokens)
                text = _deterministic_text(payload, prefix_id=prefix_id, max_tokens=max_tokens)
                with server.lock:
                    server.metrics.output_tokens += max_tokens
                if is_chat:
                    body = _chat_response(server.config.model, text, max_tokens, cache_hit, prefix_id)
                else:
                    body = _completion_response(
                        server.config.model,
                        text,
                        max_tokens,
                        cache_hit,
                        prefix_id,
                    )
                self._write_json(HTTPStatus.OK, body)
            except Exception as exc:  # deterministic fake failure path for tests.
                self._record_error()
                self._write_json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {"error": {"message": str(exc), "type": "fake_server_error"}},
                )

        def log_message(self, format: str, *args: object) -> None:
            return

        def _read_json(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0"))
            data = self.rfile.read(length)
            payload = json.loads(data.decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("request body must be a JSON object")
            return payload

        def _write_json(self, status: HTTPStatus, body: dict[str, Any]) -> None:
            encoded = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
            self.send_response(int(status))
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def _record_request(self, prefix_id: str, *, is_chat: bool) -> bool:
            with server.lock:
                metrics = server.metrics
                metrics.requests += 1
                if is_chat:
                    metrics.chat_completions += 1
                else:
                    metrics.completions += 1
                cache_hit = server.config.simulate_cache and prefix_id in metrics.seen_prefixes
                if server.config.simulate_cache:
                    if cache_hit:
                        metrics.cache_hits += 1
                    else:
                        metrics.cache_misses += 1
                        metrics.seen_prefixes.add(prefix_id)
                return cache_hit

        def _record_error(self) -> None:
            with server.lock:
                server.metrics.errors += 1

    return Handler


def _prefix_id(payload: dict[str, Any]) -> str:
    metadata = payload.get("metadata")
    if isinstance(metadata, dict) and metadata.get("prefix_id"):
        return str(metadata["prefix_id"])
    return "missing-prefix"


def _should_fail(payload: dict[str, Any]) -> bool:
    if payload.get("force_error") is True:
        return True
    prompt = payload.get("prompt")
    if isinstance(prompt, str) and "__BIFROST_FAKE_ERROR__" in prompt:
        return True
    messages = payload.get("messages")
    if isinstance(messages, list):
        for message in messages:
            if isinstance(message, dict) and "__BIFROST_FAKE_ERROR__" in str(
                message.get("content", "")
            ):
                return True
    return False


def _sleep_for_request(
    config: FakeOpenAIServerConfig,
    *,
    cache_hit: bool,
    max_tokens: int,
) -> None:
    delay_ms = config.cache_hit_delay_ms if cache_hit else config.base_delay_ms
    delay_ms += config.per_token_delay_ms * max_tokens
    if delay_ms > 0:
        time.sleep(delay_ms / 1000.0)


def _deterministic_text(payload: dict[str, Any], *, prefix_id: str, max_tokens: int) -> str:
    source = payload.get("prompt")
    if not isinstance(source, str):
        messages = payload.get("messages")
        if isinstance(messages, list) and messages:
            source = str(messages[-1].get("content", ""))
        else:
            source = ""
    checksum = sum(source.encode("utf-8")) % 10007
    tokens = [f"tok{(checksum + index) % 997}" for index in range(max_tokens)]
    return f"fake prefix={prefix_id} " + " ".join(tokens)


def _completion_response(
    model: str,
    text: str,
    max_tokens: int,
    cache_hit: bool,
    prefix_id: str,
) -> dict[str, Any]:
    return {
        "id": f"cmpl-bifrost-fake-{prefix_id}",
        "object": "text_completion",
        "created": 0,
        "model": model,
        "choices": [{"index": 0, "text": text, "finish_reason": "length"}],
        "usage": {"prompt_tokens": 0, "completion_tokens": max_tokens, "total_tokens": max_tokens},
        "bifrost_fake": {"cache_hit": cache_hit, "prefix_id": prefix_id},
    }


def _chat_response(
    model: str,
    text: str,
    max_tokens: int,
    cache_hit: bool,
    prefix_id: str,
) -> dict[str, Any]:
    return {
        "id": f"chatcmpl-bifrost-fake-{prefix_id}",
        "object": "chat.completion",
        "created": 0,
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "length",
            }
        ],
        "usage": {"prompt_tokens": 0, "completion_tokens": max_tokens, "total_tokens": max_tokens},
        "bifrost_fake": {"cache_hit": cache_hit, "prefix_id": prefix_id},
    }


__all__ = [
    "FakeOpenAIMetrics",
    "FakeOpenAIServer",
    "FakeOpenAIServerConfig",
    "create_server",
    "main",
]

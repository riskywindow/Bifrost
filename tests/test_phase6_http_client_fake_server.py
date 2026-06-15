from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
for source_path in (
    REPO_ROOT / "bifrost_py",
    REPO_ROOT / "integrations" / "lmcache_bifrost",
):
    text = str(source_path)
    if text not in sys.path:
        sys.path.insert(0, text)

from bifrost_serving.fake_server import FakeOpenAIServerConfig, create_server
from bifrost_serving.http_client import OpenAIClientConfig, OpenAICompatibleClient
from bifrost_serving.request_schema import RequestMetadata, ServingRequest
from bifrost_serving.workloads import WorkloadConfig, generate_workload


def test_fake_server_starts_and_responds() -> None:
    server = create_server(FakeOpenAIServerConfig(port=0))
    server.start_in_thread()
    try:
        client = OpenAICompatibleClient(OpenAIClientConfig(base_url=server.base_url))
        request = _request("starts", "prefix-a")

        result = client.send(request)

        assert result.status_code == 200
        assert result.error is None
        assert result.output_text.startswith("fake prefix=prefix-a")
        assert result.latency_s >= 0
        assert result.ttft_s is None
    finally:
        server.shutdown()


def test_client_sends_generated_workload_requests_and_records_latency() -> None:
    server = create_server(FakeOpenAIServerConfig(port=0, per_token_delay_ms=1))
    server.start_in_thread()
    try:
        generated = generate_workload(
            WorkloadConfig(
                workload_name="fake_ci_small",
                request_count=4,
                prefix_repeat_groups=2,
                max_tokens=3,
                seed=17,
            )
        )
        client = OpenAICompatibleClient(
            OpenAIClientConfig(base_url=server.base_url, concurrency=2)
        )

        results = client.send_many(generated.requests)

        assert [result.request_id for result in results] == [
            request.request_id for request in generated.requests
        ]
        assert all(result.status_code == 200 for result in results)
        assert all(result.error is None for result in results)
        assert all(result.latency_s > 0 for result in results)
        assert all(result.output_text for result in results)
    finally:
        server.shutdown()


def test_fake_cache_simulation_reduces_latency_on_repeated_prefix() -> None:
    server = create_server(
        FakeOpenAIServerConfig(
            port=0,
            simulate_cache=True,
            base_delay_ms=70,
            cache_hit_delay_ms=1,
            per_token_delay_ms=0,
        )
    )
    server.start_in_thread()
    try:
        client = OpenAICompatibleClient(OpenAIClientConfig(base_url=server.base_url))
        first = client.send(_request("first", "shared-prefix"))
        second = client.send(_request("second", "shared-prefix"))

        assert first.status_code == 200
        assert second.status_code == 200
        assert first.response_json is not None
        assert second.response_json is not None
        assert first.response_json["bifrost_fake"]["cache_hit"] is False
        assert second.response_json["bifrost_fake"]["cache_hit"] is True
        assert second.latency_s < first.latency_s

        metrics = _get_json(server.base_url + "/metrics")
        assert metrics["cache_misses"] == 1
        assert metrics["cache_hits"] == 1
    finally:
        server.shutdown()


def test_concurrency_works_at_small_scale() -> None:
    server = create_server(FakeOpenAIServerConfig(port=0, base_delay_ms=20))
    server.start_in_thread()
    try:
        generated = generate_workload(
            WorkloadConfig(
                workload_name="fake_ci_small",
                request_count=8,
                prefix_repeat_groups=2,
                max_tokens=2,
                seed=29,
            )
        )
        client = OpenAICompatibleClient(
            OpenAIClientConfig(base_url=server.base_url, concurrency=4)
        )

        results = client.send_many(generated.requests)

        assert len(results) == 8
        assert all(result.status_code == 200 for result in results)
        metrics = _get_json(server.base_url + "/metrics")
        assert metrics["requests"] == 8
        assert metrics["completions"] == 8
    finally:
        server.shutdown()


def test_errors_are_captured() -> None:
    server = create_server(FakeOpenAIServerConfig(port=0))
    server.start_in_thread()
    try:
        client = OpenAICompatibleClient(OpenAIClientConfig(base_url=server.base_url))
        request = ServingRequest(
            request_id="error",
            prompt="please trigger __BIFROST_FAKE_ERROR__",
            max_tokens=2,
            temperature=0.0,
            top_p=1.0,
            metadata=RequestMetadata(
                workload_name="fake_ci_small",
                prefix_id="error-prefix",
                repeat_group=0,
                expected_cache_reuse=False,
            ),
        )

        result = client.send(request)

        assert result.status_code == 500
        assert result.error is not None
        assert "forced fake server error" in result.error
        assert result.output_text == ""
    finally:
        server.shutdown()


def test_chat_endpoint_response_parser() -> None:
    server = create_server(FakeOpenAIServerConfig(port=0))
    server.start_in_thread()
    try:
        client = OpenAICompatibleClient(
            OpenAIClientConfig(base_url=server.base_url, endpoint="/v1/chat/completions")
        )

        result = client.send(_request("chat", "chat-prefix"))

        assert result.status_code == 200
        assert result.error is None
        assert result.output_text.startswith("fake prefix=chat-prefix")
        assert result.response_json is not None
        assert result.response_json["object"] == "chat.completion"
    finally:
        server.shutdown()


def _request(request_id: str, prefix_id: str) -> ServingRequest:
    return ServingRequest(
        request_id=request_id,
        prompt=f"System prompt for {request_id}",
        max_tokens=4,
        temperature=0.0,
        top_p=1.0,
        metadata=RequestMetadata(
            workload_name="fake_ci_small",
            prefix_id=prefix_id,
            repeat_group=0,
            expected_cache_reuse=False,
        ),
    )


def _get_json(url: str) -> dict[str, object]:
    with urllib.request.urlopen(url, timeout=5) as response:  # noqa: S310 - local test URL.
        return json.loads(response.read().decode("utf-8"))

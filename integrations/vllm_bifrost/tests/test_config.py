from __future__ import annotations

import pytest

from vllm_bifrost.config import (
    DEFAULT_CHUNK_SIZE,
    DEFAULT_ENDPOINT,
    ENGINE_NAME,
    INTEGRATION_NAME,
    KV_CACHE_FORMAT,
    BifrostVLLMConnectorConfig,
)
from vllm_bifrost.errors import ConnectorConfigurationError


def test_defaults_are_safe() -> None:
    config = BifrostVLLMConnectorConfig()

    assert config.endpoint == DEFAULT_ENDPOINT
    assert config.chunk_size == DEFAULT_CHUNK_SIZE
    assert config.engine_name == ENGINE_NAME
    assert config.integration_name == INTEGRATION_NAME
    assert config.kv_cache_format == KV_CACHE_FORMAT
    assert config.strict_validation is True
    assert config.allow_cpu_staging is True
    assert config.save_mode == "sync"
    assert config.load_mode == "sync"
    assert config.failure_policy == "fail"
    assert config.timeout_seconds == 10.0
    assert config.connector_instance_id is None
    assert config.trace_jsonl_path is None
    assert config.model_fingerprint is None
    assert config.layout_fingerprint is None


def test_config_parses_from_plain_dict() -> None:
    config = BifrostVLLMConnectorConfig.from_dict(
        {
            "endpoint": "10.0.0.1:7420",
            "chunk_size": "131072",
            "timeout_seconds": "2.5",
            "strict_validation": "true",
            "allow_cpu_staging": True,
            "save_mode": "ASYNC",
            "load_mode": "disabled",
            "failure_policy": "recompute",
            "connector_instance_id": "connector-a",
            "trace_jsonl_path": "runs/vllm-trace.jsonl",
            "model_fingerprint": "model:abc",
            "layout_fingerprint": "layout:def",
            "future_vllm_field": {"preserved": True},
        }
    )

    assert config.endpoint == "10.0.0.1:7420"
    assert config.chunk_size == 131072
    assert config.timeout_seconds == 2.5
    assert config.save_mode == "async"
    assert config.load_mode == "disabled"
    assert config.failure_policy == "recompute"
    assert config.connector_instance_id == "connector-a"
    assert config.trace_jsonl_path == "runs/vllm-trace.jsonl"
    assert config.model_fingerprint == "model:abc"
    assert config.layout_fingerprint == "layout:def"
    assert config.unknown_fields == {"future_vllm_field": {"preserved": True}}


def test_config_parses_from_kv_connector_extra_config() -> None:
    config = BifrostVLLMConnectorConfig.from_dict(
        {
            "kv_connector": "BifrostKVConnector",
            "kv_connector_extra_config": {
                "endpoint": "127.0.0.1:9000",
                "metrics_jsonl_path": "runs/alias.jsonl",
            },
        }
    )

    assert config.endpoint == "127.0.0.1:9000"
    assert config.trace_jsonl_path == "runs/alias.jsonl"
    assert config.unknown_fields == {}


def test_config_parses_from_fake_vllm_config_object() -> None:
    class FakeKVTransferConfig:
        kv_connector_extra_config = {
            "endpoint": "127.0.0.1:8000",
            "layout_fingerprint": "fake-layout",
        }

    class FakeVllmConfig:
        kv_transfer_config = FakeKVTransferConfig()

    config = BifrostVLLMConnectorConfig.from_vllm_config(FakeVllmConfig())

    assert config.endpoint == "127.0.0.1:8000"
    assert config.layout_fingerprint == "fake-layout"


@pytest.mark.parametrize(
    "data",
    [
        {"endpoint": ""},
        {"endpoint": None},
        {"chunk_size": 0},
        {"chunk_size": -1},
        {"chunk_size": True},
        {"timeout_seconds": 0},
        {"save_mode": "eager"},
        {"load_mode": "eager"},
        {"failure_policy": "ignore"},
        {"strict_validation": "maybe"},
        {"allow_cpu_staging": False},
        {"connector_instance_id": ""},
        {"trace_jsonl_path": ""},
        {"model_fingerprint": ""},
        {"layout_fingerprint": ""},
        {"engine_name": "other"},
        {"integration_name": "other"},
        {"kv_cache_format": "native_kv_page"},
    ],
)
def test_invalid_config_rejects(data: dict[str, object]) -> None:
    with pytest.raises(ConnectorConfigurationError):
        BifrostVLLMConnectorConfig.from_dict(data)


def test_metrics_alias_conflict_rejects() -> None:
    with pytest.raises(ConnectorConfigurationError):
        BifrostVLLMConnectorConfig.from_dict(
            {
                "trace_jsonl_path": "runs/a.jsonl",
                "metrics_jsonl_path": "runs/b.jsonl",
            }
        )


def test_non_mapping_config_rejects() -> None:
    with pytest.raises(ConnectorConfigurationError):
        BifrostVLLMConnectorConfig.from_dict(["not", "a", "mapping"])  # type: ignore[arg-type]

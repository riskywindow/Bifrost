from __future__ import annotations

import pytest

from lmcache_bifrost.adapter import BifrostConnectorAdapter, parse_config
from lmcache_bifrost.connector import BifrostRemoteConnector
from lmcache_bifrost.errors import ConnectorConfigurationError
from lmcache_bifrost.lmcache_compat import ConnectorAdapter, has_lmcache
from tests.fakes import FakeConnectorContext, FakeLMCacheConfig


def test_adapter_can_parse_bifrost_url() -> None:
    assert BifrostConnectorAdapter.can_parse("bifrost://127.0.0.1:7744")
    assert BifrostConnectorAdapter.can_parse(
        "bifrost://localhost:7744?chunk_size=262144"
    )
    assert BifrostConnectorAdapter.can_parse("bifrost+tcp://127.0.0.1:7744")


def test_adapter_can_parse_plugin_url() -> None:
    assert BifrostConnectorAdapter.can_parse("plugin://bifrost")
    assert BifrostConnectorAdapter.can_parse("plugin://bifrost.instance_name")
    assert BifrostConnectorAdapter.can_parse(
        "plugin://bifrost?endpoint=127.0.0.1:7744"
    )


def test_adapter_rejects_unrelated_urls() -> None:
    assert not BifrostConnectorAdapter.can_parse("file:///tmp/cache")
    assert not BifrostConnectorAdapter.can_parse("plugin://redis")
    assert not BifrostConnectorAdapter.can_parse("bifrost://localhost")
    assert not BifrostConnectorAdapter.can_parse("")


def test_config_parsing_from_url_query_and_extra_config() -> None:
    context = FakeConnectorContext(
        remote_url="bifrost://localhost:7744?chunk_size=262144",
        config=FakeLMCacheConfig(
            remote_url="bifrost://localhost:7744?chunk_size=262144",
            extra_config={
                "allow_pickle_fallback": True,
                "timeout_seconds": "2.5",
                "strict_validation": "false",
            },
        ),
    )

    config = parse_config(context)

    assert config.endpoint == "localhost:7744"
    assert config.chunk_size == 262144
    assert config.allow_pickle_fallback is True
    assert config.timeout_seconds == 2.5
    assert config.strict_validation is False


def test_plugin_config_parsing_uses_endpoint_from_extra_config() -> None:
    context = FakeConnectorContext(
        remote_url="plugin://bifrost",
        config=FakeLMCacheConfig(
            remote_url="plugin://bifrost",
            extra_config={"endpoint": "127.0.0.1:7744", "chunk_size": 4096},
        ),
    )

    config = parse_config(context)

    assert config.endpoint == "127.0.0.1:7744"
    assert config.chunk_size == 4096


def test_create_connector_returns_connector_with_expected_config() -> None:
    context = FakeConnectorContext(
        remote_url="bifrost://127.0.0.1:7744?chunk_size=8192",
        config=FakeLMCacheConfig(
            remote_url="bifrost://127.0.0.1:7744?chunk_size=8192",
            extra_config={"allow_pickle_fallback": True},
        ),
    )

    connector = BifrostConnectorAdapter().create_connector(context)

    assert isinstance(connector, BifrostRemoteConnector)
    assert connector.context is context
    assert connector.config.endpoint == "127.0.0.1:7744"
    assert connector.config.chunk_size == 8192
    assert connector.config.allow_pickle_fallback is True


def test_invalid_plugin_config_fails_deterministically() -> None:
    context = FakeConnectorContext(
        remote_url="plugin://bifrost",
        config=FakeLMCacheConfig(remote_url="plugin://bifrost"),
    )

    with pytest.raises(ConnectorConfigurationError):
        parse_config(context)


@pytest.mark.skipif(not has_lmcache(), reason="LMCache is not installed")
def test_real_lmcache_adapter_subclass_when_installed() -> None:
    assert ConnectorAdapter is not None
    assert issubclass(BifrostConnectorAdapter, ConnectorAdapter)

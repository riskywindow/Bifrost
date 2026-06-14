from __future__ import annotations

import pytest

import lmcache_bifrost
from lmcache_bifrost.blob_codec import serialize_memory_obj
from lmcache_bifrost.config import BifrostLMCacheConfig
from lmcache_bifrost.errors import MemoryObjSerializationError
from lmcache_bifrost.lmcache_compat import (
    CacheEngineKey,
    CodecCapability,
    ConnectorAdapter,
    ConnectorContext,
    LMCacheEngineConfig,
    LMCacheMetadata,
    MemoryObj,
    RemoteConnector,
    deserialize_with_lmcache_native,
    detect_memory_obj_codec,
    has_lmcache,
    lmcache_version,
    serialize_with_lmcache_native,
)
from tests.fakes import (
    FakeCacheEngineKey,
    FakeConnectorContext,
    FakeLMCacheConfig,
    FakeLMCacheMetadata,
    FakeMemoryObj,
)


def test_importing_package_does_not_require_lmcache() -> None:
    assert lmcache_bifrost.BifrostLMCacheConfig is BifrostLMCacheConfig
    assert isinstance(lmcache_bifrost.has_lmcache(), bool)


def test_has_lmcache_and_version_have_stable_shapes() -> None:
    assert isinstance(has_lmcache(), bool)
    version = lmcache_version()
    assert version is None or isinstance(version, str)


def test_fake_classes_support_codec_and_connector_tests() -> None:
    key = FakeCacheEngineKey("tiny", "abc", (1, 2, 3), (("tenant", "ci"),))
    memory_obj = FakeMemoryObj(b"lmcache-owned-bytes", dtype="float16", shape=(2, 4))
    config = FakeLMCacheConfig(extra_config={"namespace": "lmcache"})
    metadata = FakeLMCacheMetadata(model_name="tiny", worker_id=3)
    context = FakeConnectorContext(config=config, metadata=metadata)

    assert key.model_id == "tiny"
    assert memory_obj.payload == b"lmcache-owned-bytes"
    assert context.config.extra_config["namespace"] == "lmcache"
    assert context.metadata.worker_id == 3


def test_detect_memory_obj_codec_reports_pickle_for_fake_fixture() -> None:
    capability = detect_memory_obj_codec(FakeMemoryObj(b"payload"))

    assert isinstance(capability, CodecCapability)
    assert capability.supported is True
    assert capability.name == "pickle_fallback"
    assert capability.type_info is not None
    assert capability.type_info["serializer"] == "pickle"


def test_pickle_fallback_for_fake_objects_still_requires_config_opt_in() -> None:
    memory_obj = FakeMemoryObj(b"payload")

    with pytest.raises(MemoryObjSerializationError):
        serialize_memory_obj(memory_obj, BifrostLMCacheConfig())

    payload = serialize_memory_obj(
        memory_obj,
        BifrostLMCacheConfig(allow_pickle_fallback=True),
    )
    assert payload.startswith(b"bifrost.lmcache.pickle.v1\x00")


def test_unknown_memory_obj_codec_is_unsupported_without_guessing() -> None:
    class UnknownMemoryObj:
        pass

    capability = detect_memory_obj_codec(UnknownMemoryObj())

    assert capability.supported is False
    assert capability.name == "unsupported"
    assert "not discovered" in capability.reason
    assert serialize_with_lmcache_native(UnknownMemoryObj()) is None
    assert deserialize_with_lmcache_native(b"payload") is None


def test_native_bytes_method_is_detected_and_used() -> None:
    class NativeMemoryObj:
        def to_bytes(self) -> bytes:
            return b"native-payload"

    memory_obj = NativeMemoryObj()
    capability = detect_memory_obj_codec(memory_obj)

    assert capability.supported is True
    assert capability.name == "lmcache_native"
    assert serialize_with_lmcache_native(memory_obj) == b"native-payload"


@pytest.mark.skipif(not has_lmcache(), reason="LMCache is not installed")
def test_real_lmcache_import_smoke_when_installed() -> None:
    assert has_lmcache() is True
    assert lmcache_version() is None or isinstance(lmcache_version(), str)


@pytest.mark.skipif(not has_lmcache(), reason="LMCache is not installed")
def test_real_lmcache_class_probe_when_installed() -> None:
    probed = (
        ConnectorAdapter,
        ConnectorContext,
        RemoteConnector,
        CacheEngineKey,
        MemoryObj,
        LMCacheEngineConfig,
        LMCacheMetadata,
    )

    assert any(value is not None for value in probed)

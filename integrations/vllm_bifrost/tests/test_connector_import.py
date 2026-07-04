from __future__ import annotations

import importlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from vllm_bifrost.compat import compatibility_diagnostics
from vllm_bifrost.connector import BifrostKVConnector
from vllm_bifrost.errors import ConnectorLifecycleError, UnsupportedOperationError


def test_connector_module_import_does_not_require_optional_runtime_modules() -> None:
    package_root = Path(__file__).resolve().parents[1]
    code = """
import importlib
import json
import sys

module = importlib.import_module("vllm_bifrost.connector")
cls = module.BifrostKVConnector
print(json.dumps({
    "class_name": cls.__name__,
    "has_vllm": "vllm" in sys.modules,
    "has_lmcache": "lmcache" in sys.modules,
    "has_lmcache_bifrost": "lmcache_bifrost" in sys.modules,
    "has_torch": "torch" in sys.modules,
}))
"""
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=package_root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )

    result = json.loads(completed.stdout)

    assert result["class_name"] == "BifrostKVConnector"
    assert result["has_lmcache"] is False
    assert result["has_lmcache_bifrost"] is False
    if not result["has_vllm"]:
        assert result["has_torch"] is False


def test_connector_class_imports_by_installed_module_path() -> None:
    module = importlib.import_module("vllm_bifrost.connector")

    assert module.BifrostKVConnector is BifrostKVConnector


def test_connector_class_imports_by_repo_namespace_module_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    monkeypatch.syspath_prepend(str(repo_root))

    cls = _resolve_colon_path(
        "integrations.vllm_bifrost.vllm_bifrost.connector:BifrostKVConnector"
    )

    assert cls.__name__ == "BifrostKVConnector"


def test_repo_namespace_import_works_with_repo_root_only() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    code = """
import importlib

module = importlib.import_module(
    "integrations.vllm_bifrost.vllm_bifrost.connector"
)
print(module.BifrostKVConnector.__name__)
"""
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=repo_root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )

    assert completed.stdout.strip() == "BifrostKVConnector"


def test_compatibility_diagnostics_do_not_throw_without_vllm() -> None:
    diagnostics = compatibility_diagnostics()

    assert "vllm_available" in diagnostics
    assert "unsupported_reasons" in diagnostics
    assert isinstance(diagnostics["unsupported_reasons"], list)


def test_stub_lifecycle_records_calls_and_returns_safe_defaults(tmp_path: Path) -> None:
    trace_path = tmp_path / "connector.jsonl"
    connector = BifrostKVConnector(
        config={
            "connector_instance_id": "connector-test",
            "trace_jsonl_path": str(trace_path),
            "failure_policy": "recompute",
            "save_mode": "disabled",
        }
    )

    assert connector.register_kv_caches({"layer_0": object()}) is None
    assert connector.start_load_kv(object()) is None
    assert connector.wait_for_layer_load("layer_0") is None
    assert connector.save_kv_layer("layer_0", object(), object()) is None
    assert connector.wait_for_save() is None
    assert connector.get_finished({"request-0"}) == (None, None)
    assert connector.get_block_ids_with_load_errors() == []
    assert connector.get_num_new_matched_tokens(object(), 0) == (None, False)
    assert connector.update_state_after_alloc(object(), object(), 0) is None
    assert connector.request_finished(object(), [1, 2]) == (False, None)

    stats = connector.get_kv_connector_stats()
    methods = [entry["method"] for entry in connector.call_history]

    assert "register_kv_caches" in methods
    assert "start_load_kv" in methods
    assert "save_kv_layer" in methods
    assert stats["connector_instance_id"] == "connector-test"
    assert stats["save_kv_layer_count"] == 1
    assert stats["save_success_count"] == 0
    assert stats["start_load_kv_count"] == 1
    assert stats["load_hit_count"] == 0
    assert stats["load_skipped_count"] == 1
    assert stats["load_recompute_count"] == 1

    connector.shutdown()
    assert connector.get_kv_connector_stats()["closed"] is True
    assert trace_path.exists()
    assert "payload" not in trace_path.read_text(encoding="utf-8")


def test_lifecycle_after_shutdown_rejects() -> None:
    connector = BifrostKVConnector()
    connector.shutdown()

    with pytest.raises(ConnectorLifecycleError):
        connector.start_load_kv(object())


def test_real_vllm_shaped_config_raises_for_unimplemented_transfer_hooks() -> None:
    RealLikeVllmConfig = type("VllmConfig", (), {"__module__": "vllm.config"})
    connector = BifrostKVConnector(RealLikeVllmConfig())

    with pytest.raises(UnsupportedOperationError):
        connector.start_load_kv(object())


def _resolve_colon_path(path: str) -> type[object]:
    module_path, separator, attr = path.partition(":")
    assert separator == ":"
    module = importlib.import_module(module_path)
    resolved = getattr(module, attr)
    assert isinstance(resolved, type)
    return resolved

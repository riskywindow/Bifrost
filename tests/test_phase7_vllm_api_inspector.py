from __future__ import annotations

import dataclasses
import importlib
import importlib.metadata
import json
import os
import subprocess
import sys
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
for source_path in (REPO_ROOT / "bifrost_py",):
    text = str(source_path)
    if text not in sys.path:
        sys.path.insert(0, text)

from bifrost_vllm import api_inspector

TOOL = REPO_ROOT / "tools" / "bifrost_vllm_api_inspect.py"


def test_inspector_runs_without_vllm(monkeypatch: pytest.MonkeyPatch) -> None:
    _hide_vllm(monkeypatch)

    result = api_inspector.inspect_result()

    assert result["status"] == "not_installed"
    assert result["dynamic_connector_supported"] is False
    assert result["imports"]["vllm"]["imported"] is False
    assert result["config_fields"]["kv_connector"]["present"] is False
    assert result["connector_base_methods"]["register_kv_caches"]["present"] is False
    assert "vLLM is not importable" in result["unsupported_reasons"][0]


def test_missing_vllm_json_output_parses_and_exits_zero(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _hide_vllm(monkeypatch)
    output = tmp_path / "surface.json"

    exit_code = api_inspector.main(["--json", "--output", str(output)])

    stdout_data = json.loads(capsys.readouterr().out)
    file_data = json.loads(output.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert stdout_data["status"] == "not_installed"
    assert file_data["status"] == "not_installed"


def test_tool_script_json_output_parses() -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT / "bifrost_py")
    env["BIFROST_VLLM_INSPECT_SKIP_CLI"] = "1"

    result = subprocess.run(
        [sys.executable, str(TOOL), "--json"],
        cwd=REPO_ROOT,
        env=env,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["status"] in {"not_installed", "installed", "partial", "error"}
    assert "imports" in data


def test_fake_vllm_surface_reports_config_and_base_methods(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_vllm(monkeypatch)

    result = api_inspector.inspect_result()

    assert api_inspector.has_vllm() is True
    assert api_inspector.vllm_version() == "0.fake"
    assert result["status"] == "installed"
    assert result["vllm_version"] == "0.fake"
    assert result["dynamic_connector_supported"] is True
    assert result["config_fields"]["kv_connector"]["present"] is True
    assert result["config_fields"]["kv_connector_module_path"]["present"] is True
    assert result["config_fields"]["kv_connector_extra_config"]["present"] is True
    assert result["config_fields"]["kv_buffer_size"]["present"] is True
    assert result["connector_base_methods"]["__init__"]["present"] is True
    assert result["connector_base_methods"]["start_load_kv"]["present"] is True
    assert result["connector_base_methods"]["start_load_kv"]["coroutine"] is True
    assert (
        result["connector_base_methods"]["start_load_kv"]["signature"]
        == "(self, request)"
    )


def test_signature_extraction_is_deterministic(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_vllm(monkeypatch)

    first = api_inspector.inspect_kv_connector_base_v1()
    second = api_inspector.inspect_kv_connector_base_v1()

    assert first["methods"] == second["methods"]
    assert first["methods"]["__init__"]["signature"] == "(self, config, role=None)"
    assert first["methods"]["save_kv_layer"]["signature"].startswith(
        "(self, layer_name"
    )


def test_fake_kv_transfer_config_field_sources(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_vllm(monkeypatch)

    config = api_inspector.inspect_kv_transfer_config()

    assert config["available"] is True
    assert config["missing_expected_fields"] == []
    assert "dataclass_fields" in config["fields"]["kv_connector"]["sources"]
    assert "annotations" in config["fields"]["kv_role"]["sources"]
    assert "signature" in config["fields"]["kv_port"]["sources"]


def _hide_vllm(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in list(sys.modules):
        if name == "vllm" or name.startswith("vllm."):
            monkeypatch.delitem(sys.modules, name, raising=False)

    real_import = importlib.import_module

    def fake_import(name: str, package: str | None = None):
        if name == "vllm" or name.startswith("vllm."):
            raise ModuleNotFoundError("No module named 'vllm'")
        return real_import(name, package)

    real_version = importlib.metadata.version

    def fake_version(distribution: str) -> str:
        if distribution == "vllm":
            raise importlib.metadata.PackageNotFoundError(distribution)
        return real_version(distribution)

    monkeypatch.setattr(importlib, "import_module", fake_import)
    monkeypatch.setattr(importlib.metadata, "version", fake_version)


def _install_fake_vllm(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in list(sys.modules):
        if name == "vllm" or name.startswith("vllm."):
            monkeypatch.delitem(sys.modules, name, raising=False)

    vllm = _module("vllm", package=True)
    vllm.__version__ = "0.fake"
    distributed = _module("vllm.distributed", package=True)
    kv_transfer = _module("vllm.distributed.kv_transfer", package=True)
    kv_connector = _module("vllm.distributed.kv_transfer.kv_connector", package=True)
    v1 = _module("vllm.distributed.kv_transfer.kv_connector.v1", package=True)
    base = _module("vllm.distributed.kv_transfer.kv_connector.v1.base")
    config = _module("vllm.config")

    @dataclasses.dataclass
    class KVTransferConfig:
        kv_connector: str | None = None
        kv_connector_module_path: str | None = None
        kv_connector_extra_config: dict[str, object] | None = None
        kv_role: str | None = None
        kv_rank: int = 0
        kv_parallel_size: int = 1
        kv_ip: str | None = None
        kv_port: int | None = None
        kv_load_failure_policy: str | None = None
        engine_id: str | None = None
        kv_buffer_device: str | None = None
        kv_buffer_size: int | None = None

    KVTransferConfig.__module__ = "vllm.config"

    class KVConnectorBase_V1:
        def __init__(self, config, role=None):
            self.config = config
            self.role = role

        def register_kv_caches(self, kv_caches):
            return None

        def register_cross_layers_kv_cache(self, kv_caches):
            return None

        async def start_load_kv(self, request):
            return None

        def wait_for_layer_load(self, layer_name):
            return None

        def save_kv_layer(self, layer_name, kv_cache, attn_metadata, **kwargs):
            return None

        def wait_for_save(self):
            return None

        def get_finished(self, finished_req_ids=None):
            return [], []

        def get_block_ids_with_load_errors(self):
            return []

        def get_num_new_matched_tokens(self, request, num_computed_tokens):
            return 0

        def update_state_after_alloc(self, request, blocks):
            return None

        def build_connector_meta(self, scheduler_output):
            return {}

        def request_finished(self, request, block_ids):
            return None

        def shutdown(self):
            return None

        def get_kv_connector_stats(self):
            return {}

    KVConnectorBase_V1.__module__ = (
        "vllm.distributed.kv_transfer.kv_connector.v1.base"
    )

    config.KVTransferConfig = KVTransferConfig
    base.KVConnectorBase_V1 = KVConnectorBase_V1

    _link(vllm, "config", config)
    _link(vllm, "distributed", distributed)
    _link(distributed, "kv_transfer", kv_transfer)
    _link(kv_transfer, "kv_connector", kv_connector)
    _link(kv_connector, "v1", v1)
    _link(v1, "base", base)

    for module in (vllm, distributed, kv_transfer, kv_connector, v1, base, config):
        monkeypatch.setitem(sys.modules, module.__name__, module)

    monkeypatch.setattr(api_inspector.shutil, "which", lambda name: None)


def _module(name: str, *, package: bool = False) -> types.ModuleType:
    module = types.ModuleType(name)
    module.__file__ = f"/fake/{name.replace('.', '/')}.py"
    if package:
        module.__path__ = []  # type: ignore[attr-defined]
    return module


def _link(parent: types.ModuleType, attr: str, child: types.ModuleType) -> None:
    setattr(parent, attr, child)

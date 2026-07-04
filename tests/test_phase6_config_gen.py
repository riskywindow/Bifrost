from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
for source_path in (
    REPO_ROOT / "bifrost_py",
    REPO_ROOT / "integrations" / "lmcache_bifrost",
):
    text = str(source_path)
    if text not in sys.path:
        sys.path.insert(0, text)

from bifrost_serving.config_gen import ServingConfigRequest, generate_serving_config

EXAMPLES = REPO_ROOT / "examples" / "serving_configs"
CLI = REPO_ROOT / "tools" / "bifrost_generate_serving_config.py"


def test_fake_config_generation_works(tmp_path: Path) -> None:
    result = generate_serving_config(
        ServingConfigRequest(
            endpoint="127.0.0.1:7799",
            model="./missing-local-model",
            mode="fake",
            output_dir=tmp_path,
            port=8100,
            lmcache_port=9100,
            chunk_size=131072,
        )
    )

    assert result.files["lmcache_inprocess"].exists()
    assert result.files["lmcache_mp"].exists()
    assert "Fake mode" in "\n".join(result.warnings)
    text = result.files["lmcache_inprocess"].read_text(encoding="utf-8")
    assert "endpoint: 127.0.0.1:7799" in text
    assert "chunk_size: 131072" in text
    assert "allow_pickle_fallback: false" in text


def test_inprocess_config_generation_works(tmp_path: Path) -> None:
    result = generate_serving_config(
        ServingConfigRequest(
            endpoint="127.0.0.1:7801",
            model="./local-model",
            mode="lmcache-inprocess",
            output_dir=tmp_path,
            port=8101,
            lmcache_port=9101,
        )
    )

    parsed = _load_yaml(result.files["lmcache_inprocess"])
    assert parsed["mode"] == "lmcache_inprocess"
    assert parsed["local_cpu"] is True
    assert parsed["multiprocess"]["enabled"] is False
    assert parsed["remote_url"] == "bifrost://127.0.0.1:7801"


def test_mp_config_generation_works(tmp_path: Path) -> None:
    result = generate_serving_config(
        ServingConfigRequest(
            endpoint="127.0.0.1:7802",
            model="./local-model",
            mode="lmcache-mp",
            output_dir=tmp_path,
            port=8102,
            lmcache_port=9102,
        )
    )

    parsed = _load_yaml(result.files["lmcache_mp"])
    assert parsed["mode"] == "lmcache_mp"
    assert parsed["enable_multiprocess"] is True
    assert parsed["multiprocess"]["port"] == 9102
    assert "bifrost_lmcache_mp.yaml" in result.files["vllm_serve"].read_text(
        encoding="utf-8"
    )


def test_remote_storage_and_bench_modes_are_supported(tmp_path: Path) -> None:
    for mode in ("bifrost-remote-storage", "vllm-bench-serve"):
        result = generate_serving_config(
            ServingConfigRequest(
                endpoint="127.0.0.1:7803",
                model="./local-model",
                mode=mode,
                output_dir=tmp_path / mode,
            )
        )

        readme = result.files["readme"].read_text(encoding="utf-8")
        assert mode.replace("-", "_") in readme
        assert result.files["bench"].exists()


def test_generated_yaml_files_parse() -> None:
    for path in (
        EXAMPLES / "bifrost_lmcache_inprocess.yaml",
        EXAMPLES / "bifrost_lmcache_mp.yaml",
    ):
        parsed = _load_yaml(path)
        assert parsed["remote_storage_plugins"] == ["bifrost"]
        extra = parsed["extra_config"]
        assert (
            extra["remote_storage_plugin.bifrost.module_path"]
            == "lmcache_bifrost.adapter"
        )
        assert (
            extra["remote_storage_plugin.bifrost.class_name"]
            == "BifrostConnectorAdapter"
        )
        assert extra["object_type"] == "opaque_engine_blob"


def test_scripts_are_executable_or_documented() -> None:
    for path in (
        EXAMPLES / "vllm_serve_bifrost_lmcache.sh",
        EXAMPLES / "lmcache_server_bifrost.sh",
        EXAMPLES / "vllm_bench_serve_bifrost_lmcache.sh",
    ):
        assert os.access(path, os.X_OK)
        text = path.read_text(encoding="utf-8")
        assert "Refusing" in text
        assert "Version-sensitive" in text


def test_endpoint_and_ports_are_substituted_correctly(tmp_path: Path) -> None:
    result = generate_serving_config(
        ServingConfigRequest(
            endpoint="localhost:7810",
            model="/tmp/local-model",
            mode="lmcache-mp",
            output_dir=tmp_path,
            port=8110,
            lmcache_port=9110,
        )
    )

    combined = "\n".join(path.read_text(encoding="utf-8") for path in result.files.values())
    assert "localhost:7810" in combined
    assert "8110" in combined
    assert "9110" in combined


def test_pickle_fallback_defaults_to_false(tmp_path: Path) -> None:
    result = generate_serving_config(ServingConfigRequest(output_dir=tmp_path))
    parsed = _load_yaml(result.files["lmcache_inprocess"])

    extra = parsed["extra_config"]
    assert extra["allow_pickle_fallback"] is False
    assert "BIFROST_ALLOW_PICKLE_FALLBACK=0" in result.files["env"].read_text(
        encoding="utf-8"
    )


def test_non_fake_pickle_fallback_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="fake mode"):
        generate_serving_config(
            ServingConfigRequest(
                mode="lmcache-inprocess",
                output_dir=tmp_path,
                allow_pickle_fallback=True,
            )
        )


def test_no_hf_token_is_embedded() -> None:
    combined = "\n".join(
        path.read_text(encoding="utf-8")
        for path in EXAMPLES.rglob("*")
        if path.is_file()
    )

    assert "HF_TOKEN=" not in combined
    assert "HUGGING_FACE_HUB_TOKEN=" not in combined
    assert "hf_" not in combined


def test_generation_requires_no_internet_gpu_vllm_or_lmcache(tmp_path: Path) -> None:
    before = set(sys.modules)
    generate_serving_config(ServingConfigRequest(output_dir=tmp_path, mode="fake"))
    imported = set(sys.modules) - before

    assert "vllm" not in imported
    assert "lmcache" not in imported
    assert "torch" not in imported


def test_cli_dry_run_does_not_write_files(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(CLI),
            "--endpoint",
            "127.0.0.1:7820",
            "--model",
            "./local-model",
            "--mode",
            "fake",
            "--output-dir",
            str(tmp_path / "out"),
            "--dry-run",
        ],
        cwd=REPO_ROOT,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    assert result.returncode == 0
    assert "Would write" in result.stdout
    assert not (tmp_path / "out").exists()


def test_guarded_scripts_do_not_start_processes_without_opt_in() -> None:
    for path in (
        EXAMPLES / "vllm_serve_bifrost_lmcache.sh",
        EXAMPLES / "lmcache_server_bifrost.sh",
        EXAMPLES / "vllm_bench_serve_bifrost_lmcache.sh",
    ):
        env = os.environ.copy()
        env.pop("BIFROST_RUN_VLLM_SERVE", None)
        env.pop("BIFROST_RUN_LMCACHE_SERVER", None)
        env.pop("BIFROST_RUN_VLLM_BENCH", None)
        result = subprocess.run(
            [str(path)],
            cwd=REPO_ROOT,
            env=env,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        assert result.returncode == 2
        assert "Refusing" in result.stderr


def _load_yaml(path: Path) -> dict:
    yaml = pytest.importorskip("yaml")
    return yaml.safe_load(path.read_text(encoding="utf-8"))

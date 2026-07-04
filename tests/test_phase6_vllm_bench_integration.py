from __future__ import annotations

import json
import shutil
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

from bifrost_serving import vllm_bench
from bifrost_serving.vllm_bench import (
    VLLMBenchAvailability,
    VLLMBenchSafetyError,
    VLLMBenchServeConfig,
    build_vllm_bench_serve_command,
    check_vllm_bench_available,
    dry_run_vllm_bench_serve,
    ingest_vllm_bench_result,
    run_vllm_bench_serve,
)


HELP_TEXT = """
usage: vllm bench serve
  --backend {openai,openai-chat}
  --base-url BASE_URL
  --endpoint ENDPOINT
  --dataset-name {sharegpt,random}
  --dataset-path DATASET_PATH
  --num-prompts NUM_PROMPTS
  --num-warmups NUM_WARMUPS
  --request-rate REQUEST_RATE
  --max-concurrency MAX_CONCURRENCY
  --save-result
  --save-detailed
  --result-dir RESULT_DIR
  --result-filename RESULT_FILENAME
  --metadata METADATA
"""


def test_dry_run_builds_command(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(vllm_bench, "check_vllm_bench_available", lambda: _availability())

    result = dry_run_vllm_bench_serve(
        VLLMBenchServeConfig(
            base_url="http://127.0.0.1:8000",
            endpoint="/v1/chat/completions",
            result_dir=tmp_path,
            backend="openai-chat",
            num_prompts=12,
            num_warmups=3,
            request_rate=2.5,
            max_concurrency=4,
            metadata={"mode": "vllm-only", "run": "test"},
        )
    )

    assert result.status == "dry_run"
    command = result.command
    assert command[:3] == ["/fake/bin/vllm", "bench", "serve"]
    assert _arg_value(command, "--backend") == "openai-chat"
    assert _arg_value(command, "--base-url") == "http://127.0.0.1:8000"
    assert _arg_value(command, "--endpoint") == "/v1/chat/completions"
    assert _arg_value(command, "--dataset-name") == "random"
    assert _arg_value(command, "--num-prompts") == "12"
    assert _arg_value(command, "--num-warmups") == "3"
    assert _arg_value(command, "--request-rate") == "2.5"
    assert _arg_value(command, "--max-concurrency") == "4"
    assert "--save-result" in command
    assert "--save-detailed" in command
    assert _arg_value(command, "--result-dir") == str(tmp_path)
    assert _arg_value(command, "--result-filename") == "vllm_bench_serve_result.json"
    assert "mode=vllm-only" in _arg_values(command, "--metadata")
    assert "run=test" in _arg_values(command, "--metadata")
    assert result.command_path.exists()
    assert result.summary_path is not None
    assert result.summary_path.exists()


def test_unavailable_vllm_skips_cleanly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: None)

    availability = check_vllm_bench_available()

    assert availability.available is False
    assert availability.status == "skipped"
    assert "not on PATH" in availability.reason


def test_run_skips_when_vllm_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        vllm_bench,
        "check_vllm_bench_available",
        lambda: VLLMBenchAvailability(
            available=False,
            status="skipped",
            reason="vLLM CLI is not on PATH",
        ),
    )

    result = run_vllm_bench_serve(
        VLLMBenchServeConfig(
            base_url="http://127.0.0.1:8000",
            endpoint="/v1/completions",
            result_dir=tmp_path,
            num_prompts=1,
        )
    )

    assert result.status == "skipped"
    assert "not on PATH" in result.reason
    assert result.summary_path is not None
    assert json.loads(result.summary_path.read_text(encoding="utf-8"))["status"] == "skipped"


def test_run_refuses_real_bench_without_opt_in(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(vllm_bench, "check_vllm_bench_available", lambda: _availability())

    with pytest.raises(VLLMBenchSafetyError):
        run_vllm_bench_serve(
            VLLMBenchServeConfig(
                base_url="http://127.0.0.1:8000",
                endpoint="/v1/completions",
                result_dir=tmp_path,
                num_prompts=1,
            )
        )


def test_fake_vllm_bench_json_parses(tmp_path: Path) -> None:
    path = tmp_path / "result.json"
    path.write_text(
        json.dumps(
            {
                "num_prompts": 10,
                "request_throughput": 3.25,
                "total_token_throughput": 91.0,
                "mean_ttft_ms": 12.5,
                "median_ttft_ms": 11.0,
                "p95_ttft_ms": 20.0,
                "median_e2el_ms": 55.0,
                "p95_e2el_ms": 75.0,
                "mean_itl_ms": 2.1,
                "error_count": 1,
                "args": {"backend": "openai"},
            }
        ),
        encoding="utf-8",
    )

    summary = ingest_vllm_bench_result(path)

    assert summary["status"] == "ok"
    assert summary["request_count"] == 10
    assert summary["throughput_rps"] == 3.25
    assert summary["ttft"]["mean_ttft_ms"] == 12.5
    assert summary["latency"]["median_e2el_ms"] == 55.0
    assert summary["output_token_latency"]["mean_itl_ms"] == 2.1
    assert summary["error_count"] == 1
    assert summary["benchmark_args"]["backend"] == "openai"
    assert summary["raw_result_path"] == str(path)


def test_version_varying_json_fields_are_tolerated(tmp_path: Path) -> None:
    path = tmp_path / "variant.json"
    path.write_text(
        json.dumps(
            {
                "summary": {
                    "total_requests": 4,
                    "requests_per_second": 1.5,
                    "latency": {"p50_ms": 100.0, "p95_ms": 140.0},
                    "time_to_first_token": {"p50_ms": 17.0},
                },
                "failures": [{"request_id": "bad"}],
                "metadata": {"version": "variant"},
            }
        ),
        encoding="utf-8",
    )

    summary = ingest_vllm_bench_result(path)

    assert summary["request_count"] == 4
    assert summary["throughput_rps"] == 1.5
    assert summary["error_count"] == 1
    assert summary["latency"]["summary.latency.p50_ms"] == 100.0
    assert summary["ttft"]["summary.time_to_first_token.p50_ms"] == 17.0
    assert summary["output_token_latency"] == {}


def test_command_builder_generates_synthetic_dataset_when_needed(tmp_path: Path) -> None:
    help_text = HELP_TEXT.replace("random", "")
    availability = VLLMBenchAvailability(
        available=True,
        status="available",
        vllm_path="/fake/bin/vllm",
        help_text=help_text,
        supported_options=frozenset(vllm_bench.parse_help_options(help_text)),
    )

    command = build_vllm_bench_serve_command(
        VLLMBenchServeConfig(
            base_url="http://127.0.0.1:8000",
            endpoint="/v1/completions",
            result_dir=tmp_path,
            num_prompts=2,
        ),
        availability,
    )

    assert command.synthetic_dataset_path is not None
    assert command.synthetic_dataset_path.exists()
    assert _arg_value(command.command, "--dataset-path") == str(command.synthetic_dataset_path)
    data = json.loads(command.synthetic_dataset_path.read_text(encoding="utf-8"))
    assert len(data) == 2


def test_availability_detects_version_and_help(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: "/fake/bin/vllm")

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        if args == ["/fake/bin/vllm", "--version"]:
            return subprocess.CompletedProcess(args, 0, stdout="vllm 1.2.3\n", stderr="")
        if args == ["/fake/bin/vllm", "bench", "serve", "--help"]:
            return subprocess.CompletedProcess(args, 0, stdout=HELP_TEXT, stderr="")
        raise AssertionError(args)

    monkeypatch.setattr(subprocess, "run", fake_run)

    availability = check_vllm_bench_available()

    assert availability.available is True
    assert availability.version == "vllm 1.2.3"
    assert "--base-url" in availability.supported_options
    assert "--save-detailed" in availability.supported_options


def _availability() -> VLLMBenchAvailability:
    return VLLMBenchAvailability(
        available=True,
        status="available",
        vllm_path="/fake/bin/vllm",
        version="vllm 1.2.3",
        help_text=HELP_TEXT,
        supported_options=frozenset(vllm_bench.parse_help_options(HELP_TEXT)),
    )


def _arg_value(command: list[str], option: str) -> str:
    index = command.index(option)
    return command[index + 1]


def _arg_values(command: list[str], option: str) -> list[str]:
    values: list[str] = []
    for index, value in enumerate(command):
        if value == option:
            values.append(command[index + 1])
    return values

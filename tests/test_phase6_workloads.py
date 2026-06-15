from __future__ import annotations

import json
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

from bifrost_serving.request_schema import read_jsonl, request_from_json_line
from bifrost_serving.workloads import WorkloadConfig, generate_workload, write_workload

CLI = REPO_ROOT / "tools" / "bifrost_generate_serving_workload.py"
SCENARIO = REPO_ROOT / "contextstorm" / "scenarios" / "serve_workload_small_ci.yaml"


def test_jsonl_parses(tmp_path: Path) -> None:
    generated = generate_workload(
        WorkloadConfig(workload_name="fake_ci_small", request_count=4, seed=7)
    )
    output = tmp_path / "requests.jsonl"
    write_workload(generated, out=output)

    parsed = read_jsonl(output)

    assert len(parsed) == 4
    assert parsed[0].metadata.workload_name == "fake_ci_small"
    assert parsed[0].metadata.prompt_token_estimate is not None
    assert request_from_json_line(output.read_text(encoding="utf-8").splitlines()[0])


def test_request_ids_are_unique() -> None:
    generated = generate_workload(
        WorkloadConfig(workload_name="repeated_system_prompt", request_count=16, seed=11)
    )
    request_ids = [request.request_id for request in generated.requests]

    assert len(request_ids) == len(set(request_ids))


def test_same_seed_produces_identical_workload() -> None:
    config = WorkloadConfig(
        workload_name="repeated_document_qa",
        request_count=8,
        prefix_repeat_groups=2,
        seed=99,
        prefix_length_chars=512,
    )

    first = generate_workload(config).to_jsonl()
    second = generate_workload(config).to_jsonl()

    assert first == second


def test_different_seed_changes_workload() -> None:
    base = WorkloadConfig(
        workload_name="repeated_code_context",
        request_count=8,
        prefix_repeat_groups=2,
        prefix_length_chars=512,
    )

    first = generate_workload(base).to_jsonl()
    second = generate_workload(
        WorkloadConfig(
            workload_name=base.workload_name,
            request_count=base.request_count,
            prefix_repeat_groups=base.prefix_repeat_groups,
            max_tokens=base.max_tokens,
            seed=base.seed + 1,
            prefix_length_chars=base.prefix_length_chars,
        )
    ).to_jsonl()

    assert first != second


def test_repeated_workload_contains_repeated_prefixes() -> None:
    generated = generate_workload(
        WorkloadConfig(
            workload_name="multi_turn_same_prefix",
            request_count=9,
            prefix_repeat_groups=3,
            seed=5,
            prefix_length_chars=256,
        )
    )
    prefix_counts = generated.summary["prefix_id_counts"]

    assert set(prefix_counts.values()) == {3}
    assert generated.summary["expected_cache_reuse_count"] == 6
    assert generated.summary["repeated_prefix_ratio"] > 0.5


def test_synthetic_random_control_has_lower_repeated_prefix_ratio() -> None:
    repeated = generate_workload(
        WorkloadConfig(
            workload_name="repeated_system_prompt",
            request_count=12,
            prefix_repeat_groups=3,
            seed=123,
        )
    )
    control = generate_workload(
        WorkloadConfig(
            workload_name="synthetic_random_prefix_control",
            request_count=12,
            prefix_repeat_groups=3,
            seed=123,
        )
    )

    assert control.summary["repeated_prefix_ratio"] == 0.0
    assert repeated.summary["repeated_prefix_ratio"] > control.summary["repeated_prefix_ratio"]


def test_workload_summary_reports_expected_repeat_groups() -> None:
    generated = generate_workload(
        WorkloadConfig(
            workload_name="repeated_document_qa",
            request_count=10,
            prefix_repeat_groups=4,
            seed=44,
        )
    )

    assert generated.summary["request_count"] == 10
    assert generated.summary["prefix_repeat_groups"] == 4
    assert generated.summary["actual_repeat_groups"] == 4
    assert generated.summary["requires_model"] is False
    assert generated.summary["requires_internet"] is False
    assert generated.summary["requires_gpu"] is False
    assert generated.summary["requires_tokenizer"] is False


def test_cli_writes_jsonl_and_summary(tmp_path: Path) -> None:
    output = tmp_path / "workload.jsonl"
    summary = tmp_path / "summary.json"
    result = subprocess.run(
        [
            sys.executable,
            str(CLI),
            "--workload",
            "fake-ci-small",
            "--out",
            str(output),
            "--request-count",
            "6",
            "--prefix-repeat-groups",
            "2",
            "--max-tokens",
            "12",
            "--seed",
            "2026",
            "--prefix-size",
            "small",
            "--json-summary",
            str(summary),
        ],
        cwd=REPO_ROOT,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert len(read_jsonl(output)) == 6
    data = json.loads(summary.read_text(encoding="utf-8"))
    assert data["workload_name"] == "fake_ci_small"
    assert data["actual_repeat_groups"] == 2


def test_no_external_dependencies_required(tmp_path: Path) -> None:
    before = set(sys.modules)

    generated = generate_workload(
        WorkloadConfig(workload_name="fake_ci_small", request_count=3, seed=1)
    )
    write_workload(generated, out=tmp_path / "fake.jsonl")
    imported = set(sys.modules) - before

    assert "vllm" not in imported
    assert "lmcache" not in imported
    assert "torch" not in imported
    assert "transformers" not in imported


def test_contextstorm_serving_scenario_documents_fake_ci_workload() -> None:
    text = SCENARIO.read_text(encoding="utf-8")

    assert "workload: serving_requests" in text
    assert "generator: fake-ci-small" in text
    assert "requires_internet: false" in text
    assert "requires_gpu: false" in text
    assert "requires_tokenizer: false" in text


def test_cli_rejects_invalid_prefix_size(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(CLI),
            "--workload",
            "fake-ci-small",
            "--out",
            str(tmp_path / "bad.jsonl"),
            "--prefix-size",
            "not-a-size",
        ],
        cwd=REPO_ROOT,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    assert result.returncode == 2
    assert "prefix-size" in result.stderr

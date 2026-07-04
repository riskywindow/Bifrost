from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
for source_path in (
    REPO_ROOT / "bifrost_py",
    REPO_ROOT / "integrations" / "lmcache_bifrost",
):
    text = str(source_path)
    if text not in sys.path:
        sys.path.insert(0, text)

from bifrost_serving.correctness import (
    DeterministicRequestSettings,
    ResponseComparisonConfig,
    build_deterministic_request_params,
    compare_run_outputs,
    normalize_response_text,
)


def test_exact_match_passes() -> None:
    result = compare_run_outputs(
        [_row("req-0", "same response", tokens=2)],
        [_row("req-0", "same response", tokens=2)],
        ResponseComparisonConfig(mode="exact_text"),
    )

    assert result.status == "pass"
    assert result.match_count == 1
    assert result.mismatch_count == 0
    assert result.missing_count == 0
    assert result.examples == []


def test_normalized_match_passes_when_whitespace_differs() -> None:
    result = compare_run_outputs(
        [_row("req-0", "  BIFROST   response\ntext  ")],
        [_row("req-0", "BIFROST response text")],
        ResponseComparisonConfig(mode="normalized_text"),
    )

    assert result.status == "pass"
    assert result.match_count == 1
    assert normalize_response_text("  A\t\tB\nC ") == "A B C"


def test_mismatch_reported_clearly() -> None:
    result = compare_run_outputs(
        [_row("req-0", "expected answer")],
        [_row("req-0", "different answer")],
        ResponseComparisonConfig(mode="exact_text"),
    )

    assert result.status == "fail"
    assert result.match_count == 0
    assert result.mismatch_count == 1
    assert result.examples == [
        {
            "request_id": "req-0",
            "reason": "response_mismatch",
            "reference_compared": "expected answer",
            "candidate_compared": "different answer",
            "reference_raw": "expected answer",
            "candidate_raw": "different answer",
        }
    ]


def test_missing_request_reported() -> None:
    result = compare_run_outputs(
        [_row("req-0", "ok"), _row("req-1", "missing")],
        [_row("req-0", "ok")],
        ResponseComparisonConfig(mode="normalized_text"),
    )

    assert result.status == "fail"
    assert result.match_count == 1
    assert result.mismatch_count == 0
    assert result.missing_count == 1
    assert result.examples[0]["request_id"] == "req-1"
    assert result.examples[0]["reason"] == "missing_request"
    assert result.examples[0]["candidate_present"] is False


def test_advisory_mode_never_fails_benchmark() -> None:
    result = compare_run_outputs(
        [_row("req-0", "expected"), _row("req-1", "reference only")],
        [_row("req-0", "different")],
        ResponseComparisonConfig(mode="advisory_only", reason="backend sampling is nondeterministic"),
    )

    assert result.status == "advisory"
    assert result.mismatch_count == 1
    assert result.missing_count == 1
    assert "backend sampling is nondeterministic" in result.notes
    assert any("advisory_only mode" in note for note in result.notes)


def test_skipped_mode_documented() -> None:
    result = compare_run_outputs(
        [_row("req-0", "expected")],
        [_row("req-0", "different")],
        ResponseComparisonConfig(mode="skipped", reason="output text was not captured"),
    )

    assert result.status == "skipped"
    assert result.match_count == 0
    assert result.mismatch_count == 0
    assert result.missing_count == 0
    assert result.notes == ["output text was not captured"]


def test_token_count_only_compares_counts_without_text() -> None:
    result = compare_run_outputs(
        [_row("req-0", None, tokens=5)],
        [_row("req-0", None, tokens=5)],
        ResponseComparisonConfig(mode="token_count_only"),
    )

    assert result.status == "pass"
    assert result.match_count == 1


def test_deterministic_settings_include_seed_only_when_supported() -> None:
    without_seed = build_deterministic_request_params(max_tokens=8, seed=123)
    with_seed = build_deterministic_request_params(
        max_tokens=8,
        seed=123,
        seed_supported=True,
    )
    settings = DeterministicRequestSettings(max_tokens=8, seed=123, seed_supported=True)

    assert without_seed == {"temperature": 0, "top_p": 1, "max_tokens": 8}
    assert with_seed == {"temperature": 0, "top_p": 1, "max_tokens": 8, "seed": 123}
    assert settings.to_dict()["request_params"] == with_seed


def test_unavailable_output_text_fails_only_strict_text_mode() -> None:
    strict = compare_run_outputs(
        [_row("req-0", None, tokens=1)],
        [_row("req-0", None, tokens=1)],
        ResponseComparisonConfig(mode="exact_text"),
    )
    advisory = compare_run_outputs(
        [_row("req-0", None, tokens=1)],
        [_row("req-0", None, tokens=1)],
        ResponseComparisonConfig(mode="advisory_only"),
    )

    assert strict.status == "fail"
    assert strict.examples[0]["reason"] == "output_text_unavailable"
    assert advisory.status == "advisory"


def _row(request_id: str, text: str | None, *, tokens: int | None = None) -> dict[str, object]:
    row: dict[str, object] = {
        "request_id": request_id,
        "status": 200,
        "error": None,
        "output_token_count": tokens,
    }
    if text is not None:
        row["response_json"] = {"choices": [{"text": text}]}
    else:
        row["response_json"] = None
    return row

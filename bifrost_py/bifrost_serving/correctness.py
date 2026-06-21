"""Response correctness utilities for Phase 6 serving benchmarks."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Literal

ComparisonMode = Literal[
    "exact_text",
    "normalized_text",
    "token_count_only",
    "advisory_only",
    "skipped",
]

COMPARISON_MODES: set[str] = {
    "exact_text",
    "normalized_text",
    "token_count_only",
    "advisory_only",
    "skipped",
}

_SPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class DeterministicRequestSettings:
    """Generation settings used when a backend can run deterministically."""

    temperature: int = 0
    top_p: int = 1
    max_tokens: int = 16
    seed: int | None = None
    seed_supported: bool = False

    def to_request_params(self) -> dict[str, Any]:
        params: dict[str, Any] = {
            "temperature": self.temperature,
            "top_p": self.top_p,
            "max_tokens": self.max_tokens,
        }
        if self.seed_supported and self.seed is not None:
            params["seed"] = self.seed
        return params

    def to_dict(self) -> dict[str, Any]:
        return {
            "temperature": self.temperature,
            "top_p": self.top_p,
            "max_tokens": self.max_tokens,
            "seed": self.seed if self.seed_supported else None,
            "seed_supported": self.seed_supported,
            "request_params": self.to_request_params(),
        }


@dataclass(frozen=True, slots=True)
class ResponseComparisonConfig:
    mode: ComparisonMode = "normalized_text"
    lowercase: bool = False
    max_examples: int = 5
    reason: str | None = None
    deterministic_settings: DeterministicRequestSettings | None = None


@dataclass(frozen=True, slots=True)
class ResponseComparisonResult:
    mode: ComparisonMode
    status: Literal["pass", "fail", "advisory", "skipped"]
    match_count: int
    mismatch_count: int
    missing_count: int
    compared_count: int
    reference_count: int
    candidate_count: int
    examples: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    deterministic_settings: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "status": self.status,
            "match_count": self.match_count,
            "mismatch_count": self.mismatch_count,
            "missing_count": self.missing_count,
            "compared_count": self.compared_count,
            "reference_count": self.reference_count,
            "candidate_count": self.candidate_count,
            "examples": list(self.examples),
            "notes": list(self.notes),
            "deterministic_settings": self.deterministic_settings,
        }


def build_deterministic_request_params(
    *,
    max_tokens: int,
    seed: int | None = None,
    seed_supported: bool = False,
) -> dict[str, Any]:
    """Build deterministic generation parameters without assuming seed support."""

    if max_tokens <= 0:
        raise ValueError("max_tokens must be positive")
    return DeterministicRequestSettings(
        max_tokens=max_tokens,
        seed=seed,
        seed_supported=seed_supported,
    ).to_request_params()


def normalize_response_text(text: str, *, lowercase: bool = False) -> str:
    normalized = _SPACE_RE.sub(" ", text.strip())
    return normalized.lower() if lowercase else normalized


def compare_run_outputs(
    reference_outputs: Iterable[dict[str, Any]],
    candidate_outputs: Iterable[dict[str, Any]],
    config: ResponseComparisonConfig | None = None,
) -> ResponseComparisonResult:
    cfg = config or ResponseComparisonConfig()
    _validate_mode(cfg.mode)
    if cfg.max_examples < 0:
        raise ValueError("max_examples must be non-negative")

    reference_by_id = _index_by_request_id(reference_outputs, label="reference")
    candidate_by_id = _index_by_request_id(candidate_outputs, label="candidate")
    deterministic = (
        cfg.deterministic_settings.to_dict() if cfg.deterministic_settings is not None else None
    )

    if cfg.mode == "skipped":
        return ResponseComparisonResult(
            mode=cfg.mode,
            status="skipped",
            match_count=0,
            mismatch_count=0,
            missing_count=0,
            compared_count=0,
            reference_count=len(reference_by_id),
            candidate_count=len(candidate_by_id),
            examples=[],
            notes=[cfg.reason or "response comparison was skipped"],
            deterministic_settings=deterministic,
        )

    examples: list[dict[str, Any]] = []
    notes: list[str] = []
    if cfg.reason:
        notes.append(cfg.reason)
    if cfg.mode == "advisory_only":
        notes.append("advisory_only mode records differences without failing the benchmark")

    reference_ids = set(reference_by_id)
    candidate_ids = set(candidate_by_id)
    missing_ids = sorted(reference_ids.symmetric_difference(candidate_ids))
    for request_id in missing_ids:
        if len(examples) >= cfg.max_examples:
            break
        examples.append(
            {
                "request_id": request_id,
                "reason": "missing_request",
                "reference_present": request_id in reference_by_id,
                "candidate_present": request_id in candidate_by_id,
            }
        )

    match_count = 0
    mismatch_count = 0
    compared_count = 0
    for request_id in sorted(reference_ids.intersection(candidate_ids)):
        reference = reference_by_id[request_id]
        candidate = candidate_by_id[request_id]
        compared_count += 1
        match, example = _compare_one(request_id, reference, candidate, cfg)
        if match:
            match_count += 1
        else:
            mismatch_count += 1
            if len(examples) < cfg.max_examples:
                examples.append(example)

    missing_count = len(missing_ids)
    if cfg.mode == "advisory_only":
        status: Literal["pass", "fail", "advisory", "skipped"] = "advisory"
    elif mismatch_count or missing_count:
        status = "fail"
    else:
        status = "pass"

    return ResponseComparisonResult(
        mode=cfg.mode,
        status=status,
        match_count=match_count,
        mismatch_count=mismatch_count,
        missing_count=missing_count,
        compared_count=compared_count,
        reference_count=len(reference_by_id),
        candidate_count=len(candidate_by_id),
        examples=examples,
        notes=notes,
        deterministic_settings=deterministic,
    )


def _compare_one(
    request_id: str,
    reference: dict[str, Any],
    candidate: dict[str, Any],
    config: ResponseComparisonConfig,
) -> tuple[bool, dict[str, Any]]:
    if config.mode == "token_count_only":
        reference_count = _extract_token_count(reference)
        candidate_count = _extract_token_count(candidate)
        if reference_count is not None and candidate_count is not None:
            if reference_count == candidate_count:
                return True, {}
            reason = "token_count_mismatch"
        else:
            reason = "token_count_unavailable"
        return False, {
            "request_id": request_id,
            "reason": reason,
            "reference_token_count": reference_count,
            "candidate_token_count": candidate_count,
        }

    reference_text = _extract_response_text(reference)
    candidate_text = _extract_response_text(candidate)
    if reference_text is None or candidate_text is None:
        return False, {
            "request_id": request_id,
            "reason": "output_text_unavailable",
            "reference_text_available": reference_text is not None,
            "candidate_text_available": candidate_text is not None,
            "reference_status": reference.get("status"),
            "candidate_status": candidate.get("status"),
            "reference_error": reference.get("error"),
            "candidate_error": candidate.get("error"),
        }

    if config.mode == "exact_text" or config.mode == "advisory_only":
        reference_value = reference_text
        candidate_value = candidate_text
    else:
        reference_value = normalize_response_text(reference_text, lowercase=config.lowercase)
        candidate_value = normalize_response_text(candidate_text, lowercase=config.lowercase)

    if reference_value == candidate_value:
        return True, {}
    return False, {
        "request_id": request_id,
        "reason": "response_mismatch",
        "reference_compared": reference_value,
        "candidate_compared": candidate_value,
        "reference_raw": reference_text,
        "candidate_raw": candidate_text,
    }


def _extract_response_text(row: dict[str, Any]) -> str | None:
    for key in ("output_text", "response_text", "text"):
        value = row.get(key)
        if isinstance(value, str):
            return value
    response_json = row.get("response_json")
    if not isinstance(response_json, dict):
        return None
    choices = response_json.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return None
    first = choices[0]
    text = first.get("text")
    if isinstance(text, str):
        return text
    message = first.get("message")
    if isinstance(message, dict) and isinstance(message.get("content"), str):
        return str(message["content"])
    delta = first.get("delta")
    if isinstance(delta, dict) and isinstance(delta.get("content"), str):
        return str(delta["content"])
    return None


def _extract_token_count(row: dict[str, Any]) -> int | None:
    value = row.get("output_token_count")
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    response_json = row.get("response_json")
    if isinstance(response_json, dict):
        usage = response_json.get("usage")
        if isinstance(usage, dict):
            completion_tokens = usage.get("completion_tokens")
            if isinstance(completion_tokens, int):
                return completion_tokens
            if isinstance(completion_tokens, float) and completion_tokens.is_integer():
                return int(completion_tokens)
    return None


def _index_by_request_id(
    rows: Iterable[dict[str, Any]],
    *,
    label: str,
) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for row in rows:
        request_id = row.get("request_id")
        if not isinstance(request_id, str) or not request_id:
            raise ValueError(f"{label} row missing non-empty request_id")
        if request_id in indexed:
            raise ValueError(f"{label} row has duplicate request_id: {request_id}")
        indexed[request_id] = row
    return indexed


def _validate_mode(mode: str) -> None:
    if mode not in COMPARISON_MODES:
        raise ValueError(f"unsupported comparison mode: {mode}")


__all__ = [
    "COMPARISON_MODES",
    "ComparisonMode",
    "DeterministicRequestSettings",
    "ResponseComparisonConfig",
    "ResponseComparisonResult",
    "build_deterministic_request_params",
    "compare_run_outputs",
    "normalize_response_text",
]

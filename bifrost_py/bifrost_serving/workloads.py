"""Deterministic Phase 6 serving request workload generation."""

from __future__ import annotations

import argparse
import json
import random
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from .request_schema import RequestMetadata, ServingRequest, write_jsonl

WORKLOADS = {
    "repeated_system_prompt",
    "repeated_document_qa",
    "repeated_code_context",
    "multi_turn_same_prefix",
    "synthetic_random_prefix_control",
    "fake_ci_small",
}

CLI_WORKLOAD_ALIASES = {
    "repeated-system-prompt": "repeated_system_prompt",
    "repeated-document-qa": "repeated_document_qa",
    "repeated-code-context": "repeated_code_context",
    "multi-turn-same-prefix": "multi_turn_same_prefix",
    "synthetic-random-prefix-control": "synthetic_random_prefix_control",
    "fake-ci-small": "fake_ci_small",
}

PREFIX_SIZE_CHARS = {
    "small": 384,
    "medium": 2048,
    "large": 8192,
}


@dataclass(frozen=True, slots=True)
class WorkloadConfig:
    workload_name: str = "fake_ci_small"
    request_count: int = 8
    prefix_repeat_groups: int = 2
    max_tokens: int = 16
    seed: int = 1234
    prefix_length_chars: int = 384
    temperature: float = 0.0
    top_p: float = 1.0
    stop: tuple[str, ...] | None = None

    def normalized_workload(self) -> str:
        return normalize_workload_name(self.workload_name)


@dataclass(frozen=True, slots=True)
class GeneratedWorkload:
    requests: list[ServingRequest]
    summary: dict[str, object]

    def to_jsonl(self) -> str:
        return "".join(request.to_json_line() + "\n" for request in self.requests)


def normalize_workload_name(name: str) -> str:
    normalized = CLI_WORKLOAD_ALIASES.get(name, name).replace("-", "_")
    if normalized not in WORKLOADS:
        choices = ", ".join(sorted(WORKLOADS | set(CLI_WORKLOAD_ALIASES)))
        raise ValueError(f"unsupported workload {name!r}; expected one of: {choices}")
    return normalized


def parse_prefix_size(value: str) -> int:
    if value in PREFIX_SIZE_CHARS:
        return PREFIX_SIZE_CHARS[value]
    try:
        size = int(value)
    except ValueError as exc:
        raise ValueError("prefix-size must be small, medium, large, or an integer") from exc
    if size <= 0:
        raise ValueError("prefix-size integer must be positive")
    return size


def generate_workload(config: WorkloadConfig) -> GeneratedWorkload:
    workload = config.normalized_workload()
    _validate_config(config)
    rng = random.Random(config.seed)
    summary_config = config

    if workload == "fake_ci_small":
        effective = WorkloadConfig(
            workload_name=workload,
            request_count=config.request_count,
            prefix_repeat_groups=min(config.prefix_repeat_groups, max(1, config.request_count)),
            max_tokens=config.max_tokens,
            seed=config.seed,
            prefix_length_chars=min(config.prefix_length_chars, PREFIX_SIZE_CHARS["small"]),
            temperature=config.temperature,
            top_p=config.top_p,
            stop=config.stop,
        )
        requests = _generate_repeated(effective, rng, "fake_ci")
        summary_config = effective
    elif workload == "synthetic_random_prefix_control":
        requests = _generate_random_control(config, rng)
    elif workload == "multi_turn_same_prefix":
        requests = _generate_multi_turn(config, rng)
    else:
        requests = _generate_repeated(config, rng, workload)

    return GeneratedWorkload(
        requests=requests,
        summary=summarize_workload(requests, config=summary_config),
    )


def summarize_workload(
    requests: list[ServingRequest],
    *,
    config: WorkloadConfig | None = None,
) -> dict[str, object]:
    prefix_counts: dict[str, int] = {}
    repeat_groups: dict[int, int] = {}
    expected_reuse = 0
    prompt_chars = 0
    for request in requests:
        metadata = request.metadata
        prefix_counts[metadata.prefix_id] = prefix_counts.get(metadata.prefix_id, 0) + 1
        repeat_groups[metadata.repeat_group] = repeat_groups.get(metadata.repeat_group, 0) + 1
        if metadata.expected_cache_reuse:
            expected_reuse += 1
        prompt_chars += len(request.prompt)
    repeated_requests = sum(max(0, count - 1) for count in prefix_counts.values())
    request_count = len(requests)
    return {
        "schema_version": "bifrost.serving_workload_summary.v1",
        "workload_name": requests[0].metadata.workload_name if requests else (config.normalized_workload() if config else ""),
        "request_count": request_count,
        "seed": config.seed if config else None,
        "max_tokens": config.max_tokens if config else None,
        "prefix_length_chars": config.prefix_length_chars if config else None,
        "prefix_repeat_groups": config.prefix_repeat_groups if config else len(prefix_counts),
        "actual_repeat_groups": len(prefix_counts),
        "repeat_group_counts": {str(key): repeat_groups[key] for key in sorted(repeat_groups)},
        "prefix_id_counts": {key: prefix_counts[key] for key in sorted(prefix_counts)},
        "expected_cache_reuse_count": expected_reuse,
        "repeated_prefix_request_count": repeated_requests,
        "repeated_prefix_ratio": (repeated_requests / request_count) if request_count else 0.0,
        "average_prompt_chars": (prompt_chars / request_count) if request_count else 0.0,
        "requires_model": False,
        "requires_internet": False,
        "requires_gpu": False,
        "requires_tokenizer": False,
    }


def write_workload(
    workload: GeneratedWorkload,
    *,
    out: Path,
    summary_path: Path | None = None,
) -> None:
    write_jsonl(out, workload.requests)
    if summary_path is not None:
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(
            json.dumps(workload.summary, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )


def _validate_config(config: WorkloadConfig) -> None:
    config.normalized_workload()
    if config.request_count <= 0:
        raise ValueError("request_count must be positive")
    if config.prefix_repeat_groups <= 0:
        raise ValueError("prefix_repeat_groups must be positive")
    if config.max_tokens <= 0:
        raise ValueError("max_tokens must be positive")
    if config.prefix_length_chars <= 0:
        raise ValueError("prefix_length_chars must be positive")
    if config.temperature < 0:
        raise ValueError("temperature must be non-negative")
    if not 0 < config.top_p <= 1:
        raise ValueError("top_p must be in (0, 1]")


def _generate_repeated(
    config: WorkloadConfig,
    rng: random.Random,
    flavor: str,
) -> list[ServingRequest]:
    groups = min(config.prefix_repeat_groups, config.request_count)
    prefixes = [
        _make_prefix(flavor, config.seed, group, config.prefix_length_chars, rng)
        for group in range(groups)
    ]
    seen: dict[int, int] = {}
    requests: list[ServingRequest] = []
    for index in range(config.request_count):
        group = index % groups
        group_seen = seen.get(group, 0)
        seen[group] = group_seen + 1
        suffix = _suffix_for(flavor, index, group, rng)
        prompt = f"{prefixes[group]}\n\n{suffix}"
        requests.append(_request(config, index, group, group_seen > 0, prompt))
    return requests


def _generate_multi_turn(config: WorkloadConfig, rng: random.Random) -> list[ServingRequest]:
    groups = min(config.prefix_repeat_groups, config.request_count)
    prefixes = [
        _make_prefix("multi_turn_same_prefix", config.seed, group, config.prefix_length_chars, rng)
        for group in range(groups)
    ]
    seen: dict[int, list[str]] = {}
    requests: list[ServingRequest] = []
    for index in range(config.request_count):
        group = index % groups
        history = seen.setdefault(group, [])
        user_turn = _suffix_for("multi_turn_same_prefix", index, group, rng)
        prompt_parts = [prefixes[group], *history, f"User: {user_turn}", "Assistant:"]
        prompt = "\n".join(prompt_parts)
        history.extend([f"User: {user_turn}", f"Assistant: deterministic-placeholder-{index}"])
        requests.append(_request(config, index, group, len(history) > 2, prompt))
    return requests


def _generate_random_control(config: WorkloadConfig, rng: random.Random) -> list[ServingRequest]:
    requests: list[ServingRequest] = []
    for index in range(config.request_count):
        prefix = _make_prefix(
            "synthetic_random_prefix_control",
            config.seed,
            index,
            config.prefix_length_chars,
            rng,
        )
        suffix = _suffix_for("synthetic_random_prefix_control", index, index, rng)
        requests.append(_request(config, index, index, False, f"{prefix}\n\n{suffix}"))
    return requests


def _request(
    config: WorkloadConfig,
    index: int,
    group: int,
    expected_reuse: bool,
    prompt: str,
) -> ServingRequest:
    workload = config.normalized_workload()
    return ServingRequest(
        request_id=f"{workload}-{config.seed}-{index:05d}",
        prompt=prompt,
        max_tokens=config.max_tokens,
        temperature=config.temperature,
        top_p=config.top_p,
        stop=list(config.stop) if config.stop is not None else None,
        metadata=RequestMetadata(
            workload_name=workload,
            prefix_id=f"{workload}-prefix-{config.seed}-{group:04d}",
            repeat_group=group,
            expected_cache_reuse=expected_reuse,
            prompt_token_estimate=_estimate_tokens(prompt),
            phase="measured",
        ),
    )


def _make_prefix(
    flavor: str,
    seed: int,
    group: int,
    target_chars: int,
    rng: random.Random,
) -> str:
    intro = _prefix_intro(flavor, seed, group)
    words = _vocabulary(flavor)
    chunks = [intro]
    while len(" ".join(chunks)) < target_chars:
        chunks.append(words[rng.randrange(len(words))])
    return " ".join(chunks)[:target_chars]


def _prefix_intro(flavor: str, seed: int, group: int) -> str:
    if flavor in {"repeated_system_prompt", "fake_ci"}:
        return f"System policy seed={seed} group={group}: answer carefully using only the supplied context."
    if flavor == "repeated_document_qa":
        return f"Document seed={seed} section={group}: BIFROST benchmark notes follow."
    if flavor == "repeated_code_context":
        return f"Repository code context seed={seed} module={group}: def transfer_cache_block(block):"
    if flavor == "multi_turn_same_prefix":
        return f"Conversation seed={seed} group={group}: stable project context for repeated-prefix turns."
    return f"Random control seed={seed} prefix={group}: unique synthetic context."


def _vocabulary(flavor: str) -> tuple[str, ...]:
    common = (
        "cache",
        "prefix",
        "request",
        "latency",
        "deterministic",
        "opaque",
        "storage",
        "reuse",
        "validation",
        "serving",
    )
    if flavor == "repeated_document_qa":
        return common + ("document", "section", "evidence", "question", "summary")
    if flavor == "repeated_code_context":
        return common + ("function", "catalog", "payload", "exception", "test")
    if flavor == "multi_turn_same_prefix":
        return common + ("turn", "history", "assistant", "user", "state")
    if flavor == "synthetic_random_prefix_control":
        return common + ("control", "unique", "baseline", "noise", "sample")
    return common + ("policy", "instruction", "answer", "constraint", "format")


def _suffix_for(flavor: str, index: int, group: int, rng: random.Random) -> str:
    nonce = rng.randrange(1_000_000)
    if flavor in {"repeated_system_prompt", "fake_ci"}:
        return f"User question {index}: explain cache behavior for group {group}; nonce {nonce}."
    if flavor == "repeated_document_qa":
        return f"Question {index}: cite the relevant document facts for section {group}; nonce {nonce}."
    if flavor == "repeated_code_context":
        return f"Review task {index}: identify one edge case in this code path for module {group}; nonce {nonce}."
    if flavor == "multi_turn_same_prefix":
        return f"Turn {index}: continue the investigation for group {group}; nonce {nonce}."
    return f"Control question {index}: summarize this unique prefix; nonce {nonce}."


def _estimate_tokens(prompt: str) -> int:
    return max(1, (len(prompt) + 3) // 4)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate Phase 6 serving request JSONL")
    parser.add_argument(
        "--workload",
        required=True,
        choices=sorted(CLI_WORKLOAD_ALIASES),
    )
    parser.add_argument("--out", required=True, help="Output JSONL path")
    parser.add_argument("--request-count", type=int, default=8)
    parser.add_argument("--prefix-repeat-groups", type=int, default=2)
    parser.add_argument("--max-tokens", type=int, default=16)
    parser.add_argument("--seed", type=int, default=1234)
    parser.add_argument("--prefix-size", default="small", help="small, medium, large, or character count")
    parser.add_argument("--json-summary", default=None, help="Optional summary JSON path")
    args = parser.parse_args(argv)

    try:
        workload = generate_workload(
            WorkloadConfig(
                workload_name=args.workload,
                request_count=args.request_count,
                prefix_repeat_groups=args.prefix_repeat_groups,
                max_tokens=args.max_tokens,
                seed=args.seed,
                prefix_length_chars=parse_prefix_size(str(args.prefix_size)),
            )
        )
        write_workload(
            workload,
            out=Path(args.out),
            summary_path=Path(args.json_summary) if args.json_summary else None,
        )
    except Exception as exc:
        print(f"bifrost serving workload generation failed: {exc}", file=sys.stderr)
        return 2

    print(f"Wrote {len(workload.requests)} serving requests to {args.out}")
    if args.json_summary:
        print(f"Wrote workload summary to {args.json_summary}")
    return 0


__all__ = [
    "CLI_WORKLOAD_ALIASES",
    "GeneratedWorkload",
    "PREFIX_SIZE_CHARS",
    "WORKLOADS",
    "WorkloadConfig",
    "generate_workload",
    "normalize_workload_name",
    "parse_prefix_size",
    "summarize_workload",
    "write_workload",
]

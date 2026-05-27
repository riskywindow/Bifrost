"""Command-line interface for BIFROST Phase 1 KV objects."""

from __future__ import annotations

import argparse
import json
import sys
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
from typing import Any, NoReturn

from bifrost_kv import errors
from bifrost_kv.fixtures import (
    native_metadata,
    native_payload,
    native_target_profile,
)
from bifrost_kv.hashing import compute_object_identity
from bifrost_kv.result import ValidationResult
from bifrost_kv.validate import validate_object

CLI_USAGE_ERROR = 2

CORRUPTION_CASES = {
    "payload_byte_flip": errors.PAYLOAD_HASH_MISMATCH,
    "wrong_tokenizer_hash": errors.WRONG_TOKENIZER_HASH,
    "wrong_rope_hash": errors.WRONG_ROPE_HASH,
    "object_id_mismatch": errors.OBJECT_ID_MISMATCH,
}


class CliError(Exception):
    """A user-facing CLI error with argparse-compatible exit code 2."""


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for key, value in pairs:
        if key in values:
            raise ValueError(f"duplicate JSON object key: {key}")
        values[key] = value
    return values


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        return args.func(args)
    except CliError as exc:
        print(f"bifrost-kv: error: {exc}", file=sys.stderr)
        return CLI_USAGE_ERROR


def validate_command(args: argparse.Namespace) -> int:
    metadata = _read_json_object(args.meta, "metadata")
    payload = _read_bytes(args.payload, "payload")
    target_profile = (
        _read_json_object(args.target, "target profile") if args.target is not None else None
    )

    result = validate_object(metadata, payload, target_profile)
    if args.json:
        print(result.to_json())
    else:
        _print_validation_result(result)
    return 0 if result.status == "accepted" else 1


def id_command(args: argparse.Namespace) -> int:
    metadata = _read_json_object(args.meta, "metadata")
    payload = _read_bytes(args.payload, "payload")
    try:
        identity = compute_object_identity(metadata, payload)
    except (KeyError, TypeError, ValueError) as exc:
        raise CliError(f"cannot compute object identity: {exc}") from exc

    value = asdict(identity)
    if args.json:
        print(_json_dumps(value))
    else:
        _print_identity(value)
    return 0


def make_native_fixture_command(args: argparse.Namespace) -> int:
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    _write_json(out_dir / "meta.json", native_metadata())
    _write_bytes(out_dir / "payload.bin", native_payload())
    _write_json(out_dir / "target_profile.json", native_target_profile())
    return 0


def corrupt_fixture_command(args: argparse.Namespace) -> int:
    fixture_dir = Path(args.fixture)
    if not fixture_dir.is_dir():
        raise CliError(f"fixture directory not found: {fixture_dir}")

    metadata, payload, target_profile = _load_fixture_dir(fixture_dir)
    metadata, payload, target_profile = _corrupt_fixture(
        metadata, payload, target_profile, args.corruption
    )
    expected_reason = CORRUPTION_CASES[args.corruption]
    result = validate_object(metadata, payload, target_profile)
    if result.status != "rejected" or result.reason_code != expected_reason:
        raise CliError(
            f"{args.corruption}: generated {result.status}/{result.reason_code}, "
            f"expected rejected/{expected_reason}"
        )

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    _write_json(out_dir / "meta.json", metadata)
    _write_bytes(out_dir / "payload.bin", payload)
    _write_json(out_dir / "target_profile.json", target_profile)
    _write_json(out_dir / "expected_result.json", result.to_dict())
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bifrost-kv",
        description="Validate and inspect BIFROST Phase 1 KV object fixtures.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate_parser = subparsers.add_parser("validate", help="validate a KV object")
    validate_parser.add_argument("--meta", required=True, type=Path)
    validate_parser.add_argument("--payload", required=True, type=Path)
    validate_parser.add_argument("--target", type=Path)
    validate_parser.add_argument("--json", action="store_true", dest="json")
    validate_parser.set_defaults(func=validate_command)

    id_parser = subparsers.add_parser("id", help="compute KV object identity")
    id_parser.add_argument("--meta", required=True, type=Path)
    id_parser.add_argument("--payload", required=True, type=Path)
    id_parser.add_argument("--json", action="store_true", dest="json")
    id_parser.set_defaults(func=id_command)

    make_parser = subparsers.add_parser(
        "make-native-fixture", help="write one deterministic native valid fixture"
    )
    make_parser.add_argument("--out", required=True, type=Path)
    make_parser.set_defaults(func=make_native_fixture_command)

    corrupt_parser = subparsers.add_parser(
        "corrupt-fixture", help="write one deterministic invalid fixture"
    )
    corrupt_parser.add_argument("--fixture", required=True, type=Path)
    corrupt_parser.add_argument(
        "--corruption",
        required=True,
        choices=sorted(CORRUPTION_CASES),
    )
    corrupt_parser.add_argument("--out", required=True, type=Path)
    corrupt_parser.set_defaults(func=corrupt_fixture_command)

    return parser


def _read_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        with path.open(encoding="utf-8") as file:
            value = json.load(file, object_pairs_hook=_reject_duplicate_keys)
    except OSError as exc:
        raise CliError(f"cannot read {label} JSON {path}: {exc.strerror}") from exc
    except json.JSONDecodeError as exc:
        raise CliError(f"cannot parse {label} JSON {path}: {exc.msg}") from exc
    except ValueError as exc:
        raise CliError(f"cannot parse {label} JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise CliError(f"{label} JSON root must be an object: {path}")
    return value


def _read_bytes(path: Path, label: str) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise CliError(f"cannot read {label} file {path}: {exc.strerror}") from exc


def _print_validation_result(result: ValidationResult) -> None:
    if result.status == "accepted":
        print("ACCEPTED")
    else:
        print(f"REJECTED: {result.reason_code}")
    if result.object_id is not None:
        print(f"Object ID: {result.object_id}")
    if result.payload_hash is not None:
        print(f"Payload hash: {result.payload_hash}")
    if result.descriptor_hash is not None:
        print(f"Descriptor hash: {result.descriptor_hash}")
    if result.details:
        print("Details:")
        for key in sorted(result.details):
            print(f"  {key}: {result.details[key]}")


def _print_identity(value: dict[str, str]) -> None:
    print(f"Object ID: {value['object_id']}")
    print(f"Payload hash: {value['payload_hash']}")
    print(f"Descriptor hash: {value['descriptor_hash']}")


def _load_fixture_dir(path: Path) -> tuple[dict[str, Any], bytes, dict[str, Any]]:
    meta_path = _first_existing(path, "meta.json", "*.meta.json")
    payload_path = _first_existing(path, "payload.bin", "*.payload.bin")
    target_path = path / "target_profile.json"
    return (
        _read_json_object(meta_path, "metadata"),
        _read_bytes(payload_path, "payload"),
        _read_json_object(target_path, "target profile"),
    )


def _first_existing(path: Path, canonical_name: str, fallback_pattern: str) -> Path:
    canonical = path / canonical_name
    if canonical.is_file():
        return canonical
    matches = sorted(path.glob(fallback_pattern))
    if len(matches) == 1:
        return matches[0]
    if not matches:
        _die(f"fixture is missing {canonical_name} or {fallback_pattern}: {path}")
    _die(f"fixture has multiple {fallback_pattern} files: {path}")


def _corrupt_fixture(
    metadata: dict[str, Any],
    payload: bytes,
    target_profile: dict[str, Any],
    corruption: str,
) -> tuple[dict[str, Any], bytes, dict[str, Any]]:
    corrupted_meta = deepcopy(metadata)
    corrupted_payload = payload
    corrupted_target = deepcopy(target_profile)

    if corruption == "payload_byte_flip":
        if not corrupted_payload:
            raise CliError("cannot flip a byte in an empty payload")
        corrupted_payload = bytes([corrupted_payload[0] ^ 0xFF]) + corrupted_payload[1:]
    elif corruption == "wrong_tokenizer_hash":
        corrupted_target["model_profile"]["tokenizer_hash"] = "blake3:" + "1" * 64
    elif corruption == "wrong_rope_hash":
        corrupted_target["model_profile"]["rope_config_hash"] = "blake3:" + "2" * 64
    elif corruption == "object_id_mismatch":
        corrupted_meta["object_id"] = "bifrost://object/blake3/" + "9" * 64
    else:
        _die(f"unknown corruption: {corruption}")

    return corrupted_meta, corrupted_payload, corrupted_target


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(_json_dumps(value, indent=2) + "\n", encoding="utf-8")


def _write_bytes(path: Path, value: bytes) -> None:
    path.write_bytes(value)


def _json_dumps(value: dict[str, Any], indent: int | None = None) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        indent=indent,
        separators=None if indent is not None else (",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _die(message: str) -> NoReturn:
    raise CliError(message)


__all__ = ["main"]


if __name__ == "__main__":
    raise SystemExit(main())

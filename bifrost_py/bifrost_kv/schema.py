"""JSON Schema loading and validation helpers for BIFROST Phase 1."""

from __future__ import annotations

import json
from importlib import resources
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

VALIDATION_RESULT_SCHEMA_NAME = "bifrost_validation_result.v1alpha1.schema.json"


def load_schema(name: str) -> dict[str, Any]:
    """Load a JSON schema by file name from package data or the repo schemas dir."""

    schema_name = _schema_file_name(name)

    try:
        package_schema = resources.files("bifrost_kv").joinpath("schemas", schema_name)
        if package_schema.is_file():
            with package_schema.open("r", encoding="utf-8") as file:
                schema = json.load(file)
                if isinstance(schema, dict):
                    return schema
    except (FileNotFoundError, ModuleNotFoundError):
        pass

    for schema_path in _repo_schema_candidates(schema_name):
        if schema_path.is_file():
            with schema_path.open(encoding="utf-8") as file:
                schema = json.load(file)
                if isinstance(schema, dict):
                    return schema
                raise TypeError(f"{schema_name}: schema root must be an object")

    raise FileNotFoundError(f"schema not found: {schema_name}")


def validate_json_schema(instance: dict[str, Any], schema_name: str) -> list[str]:
    """Return deterministic schema validation error messages for an instance."""

    schema = load_schema(schema_name)
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    errors = sorted(
        validator.iter_errors(instance),
        key=lambda error: (list(error.absolute_path), list(error.absolute_schema_path)),
    )
    return [_format_validation_error(error) for error in errors]


def validate_validation_result(instance: dict[str, Any]) -> list[str]:
    return validate_json_schema(instance, VALIDATION_RESULT_SCHEMA_NAME)


def _schema_file_name(name: str) -> str:
    path = Path(name)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"schema name must be a file name under schemas/: {name}")
    return path.name


def _repo_schema_candidates(schema_name: str) -> tuple[Path, ...]:
    package_file = Path(__file__).resolve()
    repo_root = package_file.parents[2]
    return (
        repo_root / "schemas" / schema_name,
        Path.cwd() / "schemas" / schema_name,
    )


def _format_validation_error(error: Any) -> str:
    location = "$"
    for part in error.absolute_path:
        if isinstance(part, int):
            location += f"[{part}]"
        else:
            location += f".{part}"
    return f"{location}: {error.message}"


__all__ = [
    "VALIDATION_RESULT_SCHEMA_NAME",
    "load_schema",
    "validate_json_schema",
    "validate_validation_result",
]

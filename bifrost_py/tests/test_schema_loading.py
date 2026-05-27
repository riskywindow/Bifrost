from pathlib import Path

import pytest

from bifrost_kv.schema import (
    VALIDATION_RESULT_SCHEMA_NAME,
    load_schema,
    validate_json_schema,
)


def test_validation_result_schema_loads_from_repo_relative_path() -> None:
    schema = load_schema(VALIDATION_RESULT_SCHEMA_NAME)

    assert schema["$id"].endswith(VALIDATION_RESULT_SCHEMA_NAME)
    assert schema["properties"]["schema_version"]["const"] == (
        "bifrost.validation_result.v1alpha1"
    )


def test_schema_loading_accepts_relative_schema_path_name() -> None:
    schema = load_schema(str(Path("schemas") / VALIDATION_RESULT_SCHEMA_NAME))

    assert schema["title"] == "BIFROST Validation Result v1alpha1"


def test_schema_loading_rejects_parent_traversal() -> None:
    with pytest.raises(ValueError, match="schema name"):
        load_schema("../schemas/bifrost_validation_result.v1alpha1.schema.json")


def test_validate_json_schema_reports_errors_without_raising() -> None:
    errors = validate_json_schema(
        {"schema_version": "not-valid"},
        VALIDATION_RESULT_SCHEMA_NAME,
    )

    assert errors
    assert all(error.startswith("$") for error in errors)

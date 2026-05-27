import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator


REPO_ROOT = Path(__file__).resolve().parents[2]

EXAMPLE_SCHEMA_PAIRS = [
    (
        "schemas/bifrost_kv_object.v1alpha1.schema.json",
        "fixtures/examples/native_object.meta.json",
    ),
    (
        "schemas/bifrost_kv_object.v1alpha1.schema.json",
        "fixtures/examples/opaque_object.meta.json",
    ),
    (
        "schemas/bifrost_target_profile.v1alpha1.schema.json",
        "fixtures/examples/native_target_profile.json",
    ),
    (
        "schemas/bifrost_target_profile.v1alpha1.schema.json",
        "fixtures/examples/opaque_target_profile.json",
    ),
    (
        "schemas/bifrost_validation_result.v1alpha1.schema.json",
        "fixtures/examples/accepted_validation_result.json",
    ),
    (
        "schemas/bifrost_validation_result.v1alpha1.schema.json",
        "fixtures/examples/rejected_validation_result.json",
    ),
]


def load_json(relative_path: str) -> object:
    with (REPO_ROOT / relative_path).open(encoding="utf-8") as file:
        return json.load(file)


@pytest.mark.parametrize(("schema_path", "example_path"), EXAMPLE_SCHEMA_PAIRS)
def test_examples_validate_against_schema(schema_path: str, example_path: str) -> None:
    schema = load_json(schema_path)
    example = load_json(example_path)
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(example)

import json
from pathlib import Path

import pytest

from bifrost_kv.errors import REASON_CODES, ReasonCode
from bifrost_kv.result import ValidationResult
from bifrost_kv.schema import load_schema, validate_validation_result

REPO_ROOT = Path(__file__).resolve().parents[2]

OBJECT_ID = "bifrost://object/blake3/" + ("1" * 64)
PAYLOAD_HASH = "blake3:" + ("2" * 64)
DESCRIPTOR_HASH = "blake3:" + ("3" * 64)

EXPECTED_REASON_CODES = (
    "accepted",
    "parse_error",
    "schema_validation_failed",
    "unknown_schema_version",
    "unknown_object_type",
    "missing_required_field",
    "extra_field_rejected",
    "invalid_field_type",
    "byte_length_mismatch",
    "payload_hash_mismatch",
    "descriptor_hash_mismatch",
    "object_id_mismatch",
    "wrong_model_hash",
    "wrong_tokenizer_hash",
    "wrong_config_hash",
    "wrong_rope_hash",
    "wrong_dtype",
    "wrong_num_layers",
    "wrong_num_kv_heads",
    "wrong_head_dim",
    "wrong_engine_name",
    "wrong_engine_version",
    "wrong_attention_impl",
    "wrong_kv_layout",
    "wrong_block_size_tokens",
    "wrong_kv_cache_format",
    "wrong_prefix_hash",
    "wrong_token_range",
    "wrong_absolute_position_range",
    "invalid_layer_id",
    "invalid_kv_block_id",
    "invalid_block_token_count",
    "invalid_tensor_shape",
    "invalid_tensor_dtype",
    "invalid_tensor_layout",
    "opaque_wrong_engine_key",
    "opaque_wrong_engine_name",
    "opaque_wrong_integration_name",
    "opaque_payload_not_interpretable",
    "unsupported_compression",
    "unsupported_payload_encoding",
)


def test_accepted_result_validates_against_schema() -> None:
    result = ValidationResult.accepted(
        object_id=OBJECT_ID,
        payload_hash=PAYLOAD_HASH,
        descriptor_hash=DESCRIPTOR_HASH,
    )

    assert validate_validation_result(result.to_dict()) == []
    assert json.loads(result.to_json()) == result.to_dict()


def test_rejected_result_validates_against_schema() -> None:
    result = ValidationResult.rejected(
        "schema_validation_failed",
        details={"field": "object_type"},
    )

    assert validate_validation_result(result.to_dict()) == []


def test_from_dict_round_trips_validation_result() -> None:
    value = ValidationResult.rejected("parse_error").to_dict()

    assert ValidationResult.from_dict(value).to_dict() == value


def test_every_error_code_is_unique() -> None:
    assert len(REASON_CODES) == len(set(REASON_CODES))
    assert len(ReasonCode) == len(REASON_CODES)


def test_reason_codes_are_stable_strings() -> None:
    assert REASON_CODES == EXPECTED_REASON_CODES
    assert tuple(code.value for code in ReasonCode) == EXPECTED_REASON_CODES


def test_reason_codes_match_validation_result_schema() -> None:
    schema = load_schema("bifrost_validation_result.v1alpha1.schema.json")

    assert tuple(schema["properties"]["reason_code"]["enum"]) == REASON_CODES


def test_reason_codes_match_validation_errors_doc() -> None:
    doc = (REPO_ROOT / "docs" / "validation_errors.md").read_text(encoding="utf-8")
    stable_block = doc.split("```text\n", maxsplit=1)[1].split("\n```", maxsplit=1)[0]

    assert tuple(stable_block.splitlines()) == REASON_CODES


def test_invalid_validation_result_fails_schema() -> None:
    invalid = ValidationResult.rejected("parse_error").to_dict()
    invalid["status"] = "accepted"

    errors = validate_validation_result(invalid)

    assert errors
    assert any("accepted" in error for error in errors)


def test_rejected_result_rejects_accepted_reason_code() -> None:
    with pytest.raises(ValueError, match="accepted reason code"):
        ValidationResult.rejected("accepted")


def test_rejected_result_rejects_unknown_reason_code() -> None:
    with pytest.raises(ValueError, match="unknown validation reason code"):
        ValidationResult.rejected("not_a_reason")

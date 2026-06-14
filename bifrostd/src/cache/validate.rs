use crate::cache::errors::ReasonCode;
use crate::cache::hash::{compute_descriptor_hash, compute_object_id, compute_payload_hash};
use crate::cache::object_meta::{BifrostKvObjectDescriptor, TokenRange};
use crate::cache::target_profile::BifrostTargetProfile;
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};
use std::collections::BTreeMap;

const VALIDATION_RESULT_SCHEMA_VERSION: &str = "bifrost.validation_result.v1alpha1";
const SUPPORTED_SCHEMA_VERSION: &str = "bifrost.kv_object.v1alpha1";
const SUPPORTED_TARGET_SCHEMA_VERSION: &str = "bifrost.target_profile.v1alpha1";
const NATIVE_KV_PAGE: &str = "native_kv_page";
const OPAQUE_ENGINE_BLOB: &str = "opaque_engine_blob";

macro_rules! reject_if {
    ($expr:expr) => {
        if let Some(reason) = $expr {
            return Some(reason);
        }
    };
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ValidationResult {
    pub schema_version: String,
    pub status: String,
    pub reason_code: String,
    pub object_id: Option<String>,
    pub payload_hash: Option<String>,
    pub descriptor_hash: Option<String>,
    pub details: BTreeMap<String, Value>,
}

impl ValidationResult {
    pub fn accepted(object_id: String, payload_hash: String, descriptor_hash: String) -> Self {
        Self {
            schema_version: VALIDATION_RESULT_SCHEMA_VERSION.to_string(),
            status: "accepted".to_string(),
            reason_code: ReasonCode::Accepted.to_string(),
            object_id: Some(object_id),
            payload_hash: Some(payload_hash),
            descriptor_hash: Some(descriptor_hash),
            details: BTreeMap::new(),
        }
    }

    pub fn rejected(reason_code: ReasonCode) -> Self {
        Self::rejected_with(reason_code, None, None, None)
    }

    fn rejected_with(
        reason_code: ReasonCode,
        object_id: Option<String>,
        payload_hash: Option<String>,
        descriptor_hash: Option<String>,
    ) -> Self {
        Self {
            schema_version: VALIDATION_RESULT_SCHEMA_VERSION.to_string(),
            status: "rejected".to_string(),
            reason_code: reason_code.to_string(),
            object_id,
            payload_hash,
            descriptor_hash,
            details: BTreeMap::new(),
        }
    }
}

pub fn validate_object(
    metadata: &Value,
    payload: &[u8],
    target_profile: Option<&Value>,
) -> ValidationResult {
    let Some(root) = metadata.as_object() else {
        return ValidationResult::rejected(ReasonCode::ParseError);
    };

    match root.get("schema_version") {
        None => return ValidationResult::rejected(ReasonCode::MissingRequiredField),
        Some(Value::String(value)) if value == SUPPORTED_SCHEMA_VERSION => {}
        Some(Value::String(_)) => {
            return ValidationResult::rejected(ReasonCode::UnknownSchemaVersion)
        }
        Some(_) => return ValidationResult::rejected(ReasonCode::InvalidFieldType),
    }

    match root.get("object_type") {
        None => return ValidationResult::rejected(ReasonCode::MissingRequiredField),
        Some(Value::String(value)) if value == NATIVE_KV_PAGE || value == OPAQUE_ENGINE_BLOB => {}
        Some(Value::String(_)) => return ValidationResult::rejected(ReasonCode::UnknownObjectType),
        Some(_) => return ValidationResult::rejected(ReasonCode::InvalidFieldType),
    }

    if let Some(reason) = validate_descriptor_schema(root) {
        return ValidationResult::rejected(reason);
    }

    let descriptor = match serde_json::from_value::<BifrostKvObjectDescriptor>(metadata.clone()) {
        Ok(value) => value,
        Err(_) => return ValidationResult::rejected(ReasonCode::SchemaValidationFailed),
    };

    if descriptor.payload_profile.compression != "none" {
        return ValidationResult::rejected(ReasonCode::UnsupportedCompression);
    }
    if descriptor.payload_profile.payload_encoding != "raw_bytes" {
        return ValidationResult::rejected(ReasonCode::UnsupportedPayloadEncoding);
    }

    if descriptor.payload_profile.byte_length != payload.len() as u64 {
        return ValidationResult::rejected(ReasonCode::ByteLengthMismatch);
    }

    let payload_hash = compute_payload_hash(payload);
    if descriptor.integrity.payload_hash != payload_hash {
        return ValidationResult::rejected_with(
            ReasonCode::PayloadHashMismatch,
            Some(descriptor.object_id),
            Some(payload_hash),
            None,
        );
    }

    let descriptor_hash = match compute_descriptor_hash(metadata, &payload_hash) {
        Ok(value) => value,
        Err(_) => return ValidationResult::rejected(ReasonCode::SchemaValidationFailed),
    };
    if descriptor.integrity.descriptor_hash != descriptor_hash {
        return ValidationResult::rejected_with(
            ReasonCode::DescriptorHashMismatch,
            Some(descriptor.object_id),
            Some(payload_hash),
            Some(descriptor_hash),
        );
    }

    let object_id = compute_object_id(&descriptor_hash, &payload_hash);
    if descriptor.object_id != object_id {
        return ValidationResult::rejected_with(
            ReasonCode::ObjectIdMismatch,
            Some(object_id),
            Some(payload_hash),
            Some(descriptor_hash),
        );
    }

    if let Some(target_value) = target_profile {
        let reason = check_target_compatibility(&descriptor, target_value);
        if let Some(reason) = reason {
            return ValidationResult::rejected_with(
                reason,
                Some(object_id),
                Some(payload_hash),
                Some(descriptor_hash),
            );
        }
    }

    let reason = if descriptor.object_type == NATIVE_KV_PAGE {
        check_native_semantics(&descriptor)
    } else {
        check_opaque_semantics(&descriptor)
    };
    if let Some(reason) = reason {
        return ValidationResult::rejected_with(
            reason,
            Some(object_id),
            Some(payload_hash),
            Some(descriptor_hash),
        );
    }

    ValidationResult::accepted(object_id, payload_hash, descriptor_hash)
}

fn check_target_compatibility(
    metadata: &BifrostKvObjectDescriptor,
    target_value: &Value,
) -> Option<ReasonCode> {
    if let Some(reason) = validate_target_schema(target_value) {
        return Some(reason);
    }
    let target = match serde_json::from_value::<BifrostTargetProfile>(target_value.clone()) {
        Ok(value) => value,
        Err(_) => return Some(ReasonCode::SchemaValidationFailed),
    };
    if metadata.object_type == NATIVE_KV_PAGE {
        check_native_compatibility(metadata, &target)
    } else {
        check_opaque_compatibility(metadata, &target)
    }
}

fn check_native_compatibility(
    metadata: &BifrostKvObjectDescriptor,
    target: &BifrostTargetProfile,
) -> Option<ReasonCode> {
    if target.accepts_object_type != NATIVE_KV_PAGE {
        return Some(ReasonCode::UnknownObjectType);
    }
    let (Some(object_prefix), Some(native), Some(target_model), Some(target_prefix)) = (
        metadata.prefix_profile.as_ref(),
        metadata.native_tensor_profile.as_ref(),
        target.model_profile.as_ref(),
        target.prefix_requirements.as_ref(),
    ) else {
        return Some(ReasonCode::SchemaValidationFailed);
    };
    let object_model = &metadata.model_profile;
    let object_engine = &metadata.engine_profile;
    let target_engine = &target.engine_profile;

    first_mismatch([
        (
            object_model.model_hash == target_model.model_hash,
            ReasonCode::WrongModelHash,
        ),
        (
            object_model.tokenizer_hash == target_model.tokenizer_hash,
            ReasonCode::WrongTokenizerHash,
        ),
        (
            object_model.config_hash == target_model.config_hash,
            ReasonCode::WrongConfigHash,
        ),
        (
            object_model.rope_config_hash == target_model.rope_config_hash,
            ReasonCode::WrongRopeHash,
        ),
        (
            object_model.dtype == target_model.dtype,
            ReasonCode::WrongDtype,
        ),
        (
            object_model.num_layers == target_model.num_layers,
            ReasonCode::WrongNumLayers,
        ),
        (
            object_model.num_kv_heads == target_model.num_kv_heads,
            ReasonCode::WrongNumKvHeads,
        ),
        (
            object_model.head_dim == target_model.head_dim,
            ReasonCode::WrongHeadDim,
        ),
        (
            object_engine.engine_name == target_engine.engine_name,
            ReasonCode::WrongEngineName,
        ),
        (
            object_engine.engine_version == target_engine.engine_version,
            ReasonCode::WrongEngineVersion,
        ),
        (
            object_engine.attention_impl == target_engine.attention_impl,
            ReasonCode::WrongAttentionImpl,
        ),
        (
            object_engine.kv_layout == target_engine.kv_layout,
            ReasonCode::WrongKvLayout,
        ),
        (
            object_engine.block_size_tokens == target_engine.block_size_tokens,
            ReasonCode::WrongBlockSizeTokens,
        ),
        (
            object_engine.kv_cache_format == target_engine.kv_cache_format,
            ReasonCode::WrongKvCacheFormat,
        ),
        (
            object_prefix.prefix_hash == target_prefix.prefix_hash,
            ReasonCode::WrongPrefixHash,
        ),
        (
            object_prefix.token_hash == target_prefix.token_hash,
            ReasonCode::WrongPrefixHash,
        ),
        (
            object_prefix.tokenizer_hash == target_prefix.tokenizer_hash,
            ReasonCode::WrongTokenizerHash,
        ),
        (
            object_prefix.rope_config_hash == target_prefix.rope_config_hash,
            ReasonCode::WrongRopeHash,
        ),
        (
            object_prefix.mm_hashes == target_prefix.allow_mm_hashes,
            ReasonCode::WrongPrefixHash,
        ),
        (
            native.token_range == target_prefix.token_range,
            ReasonCode::WrongTokenRange,
        ),
        (
            object_prefix.absolute_position_range == target_prefix.absolute_position_range,
            ReasonCode::WrongAbsolutePositionRange,
        ),
    ])
}

fn check_opaque_compatibility(
    metadata: &BifrostKvObjectDescriptor,
    target: &BifrostTargetProfile,
) -> Option<ReasonCode> {
    if target.accepts_object_type != OPAQUE_ENGINE_BLOB {
        return Some(ReasonCode::UnknownObjectType);
    }
    let (Some(opaque), Some(requirements)) = (
        metadata.opaque_engine_profile.as_ref(),
        target.opaque_requirements.as_ref(),
    ) else {
        return Some(ReasonCode::SchemaValidationFailed);
    };
    let object_engine = &metadata.engine_profile;
    let target_engine = &target.engine_profile;

    first_mismatch([
        (
            object_engine.engine_name == target_engine.engine_name,
            ReasonCode::OpaqueWrongEngineName,
        ),
        (
            object_engine.integration_name == target_engine.integration_name,
            ReasonCode::OpaqueWrongIntegrationName,
        ),
        (
            object_engine.kv_cache_format == target_engine.kv_cache_format,
            ReasonCode::WrongKvCacheFormat,
        ),
        (
            opaque.engine_key_hash == requirements.engine_key_hash,
            ReasonCode::OpaqueWrongEngineKey,
        ),
    ])
}

fn check_native_semantics(metadata: &BifrostKvObjectDescriptor) -> Option<ReasonCode> {
    let (Some(prefix), Some(native)) = (
        metadata.prefix_profile.as_ref(),
        metadata.native_tensor_profile.as_ref(),
    ) else {
        return Some(ReasonCode::SchemaValidationFailed);
    };
    if metadata.opaque_engine_profile.is_some() {
        return Some(ReasonCode::SchemaValidationFailed);
    }

    let model = &metadata.model_profile;
    let engine = &metadata.engine_profile;
    let payload_profile = &metadata.payload_profile;

    if model.dtype != native.tensor_dtype {
        return Some(ReasonCode::InvalidTensorDtype);
    }
    let Some(dtype_width) = dtype_byte_width(&native.tensor_dtype) else {
        return Some(ReasonCode::InvalidTensorDtype);
    };
    if native.tensor_layout != "kv_token_head_dim" {
        return Some(ReasonCode::InvalidTensorLayout);
    }
    if native.tensor_role != "kv_pair" {
        return Some(ReasonCode::InvalidTensorShape);
    }
    if native.token_range.end <= native.token_range.start {
        return Some(ReasonCode::WrongTokenRange);
    }
    if prefix.token_range.end <= prefix.token_range.start {
        return Some(ReasonCode::WrongTokenRange);
    }
    if prefix.absolute_position_range.end <= prefix.absolute_position_range.start {
        return Some(ReasonCode::WrongAbsolutePositionRange);
    }

    let block_token_count = native.token_range.end - native.token_range.start;
    if native.block_token_count != block_token_count {
        return Some(ReasonCode::InvalidBlockTokenCount);
    }
    if block_token_count == 0 || block_token_count > native.block_size_tokens {
        return Some(ReasonCode::InvalidBlockTokenCount);
    }
    if native.block_size_tokens != engine.block_size_tokens {
        return Some(ReasonCode::WrongBlockSizeTokens);
    }
    if native.kv_block_id != native.token_range.start / native.block_size_tokens {
        return Some(ReasonCode::InvalidKvBlockId);
    }
    if native.layer_id >= model.num_layers {
        return Some(ReasonCode::InvalidLayerId);
    }

    let expected_shape = vec![2, block_token_count, model.num_kv_heads, model.head_dim];
    if native.tensor_shape != expected_shape {
        return Some(ReasonCode::InvalidTensorShape);
    }
    let expected_byte_length =
        2 * block_token_count * model.num_kv_heads * model.head_dim * dtype_width;
    if payload_profile.byte_length != expected_byte_length {
        return Some(ReasonCode::ByteLengthMismatch);
    }
    if prefix.tokenizer_hash != model.tokenizer_hash {
        return Some(ReasonCode::WrongTokenizerHash);
    }
    if prefix.rope_config_hash != model.rope_config_hash {
        return Some(ReasonCode::WrongRopeHash);
    }
    None
}

fn check_opaque_semantics(metadata: &BifrostKvObjectDescriptor) -> Option<ReasonCode> {
    if metadata.opaque_engine_profile.is_none() {
        return Some(ReasonCode::SchemaValidationFailed);
    }
    if metadata.native_tensor_profile.is_some() {
        return Some(ReasonCode::OpaquePayloadNotInterpretable);
    }
    None
}

fn first_mismatch<const N: usize>(checks: [(bool, ReasonCode); N]) -> Option<ReasonCode> {
    checks
        .into_iter()
        .find_map(|(ok, reason)| if ok { None } else { Some(reason) })
}

fn dtype_byte_width(dtype: &str) -> Option<u64> {
    match dtype {
        "float16" | "bfloat16" => Some(2),
        "float32" => Some(4),
        _ => None,
    }
}

fn validate_descriptor_schema(root: &Map<String, Value>) -> Option<ReasonCode> {
    let required = [
        "schema_version",
        "object_type",
        "object_id",
        "created_at_unix_ms",
        "created_by",
        "model_profile",
        "engine_profile",
        "prefix_profile",
        "payload_profile",
        "native_tensor_profile",
        "opaque_engine_profile",
        "integrity",
        "provenance",
    ];
    reject_if!(check_required(root, &required));
    reject_if!(check_allowed(root, &required));

    reject_if!(require_hash_like(root.get("object_id")?, true));
    reject_if!(require_non_negative_integer(
        root.get("created_at_unix_ms")?
    ));
    reject_if!(require_non_empty_string(root.get("created_by")?));
    reject_if!(validate_required_object(
        root.get("model_profile")?,
        validate_model_profile
    ));
    reject_if!(validate_required_object(
        root.get("engine_profile")?,
        validate_engine_profile
    ));
    reject_if!(validate_optional(
        root.get("prefix_profile")?,
        validate_prefix_profile
    ));
    reject_if!(validate_required_object(
        root.get("payload_profile")?,
        validate_payload_profile
    ));
    reject_if!(validate_optional(
        root.get("native_tensor_profile")?,
        validate_native_tensor_profile,
    ));
    reject_if!(validate_optional(
        root.get("opaque_engine_profile")?,
        validate_opaque_profile
    ));
    reject_if!(validate_required_object(
        root.get("integrity")?,
        validate_integrity
    ));
    reject_if!(validate_required_object(
        root.get("provenance")?,
        validate_provenance
    ));
    None
}

fn validate_target_schema(value: &Value) -> Option<ReasonCode> {
    let Some(root) = value.as_object() else {
        return Some(ReasonCode::SchemaValidationFailed);
    };
    match root.get("schema_version") {
        Some(Value::String(version)) if version == SUPPORTED_TARGET_SCHEMA_VERSION => {}
        Some(Value::String(_)) | None => return Some(ReasonCode::UnknownSchemaVersion),
        Some(_) => return Some(ReasonCode::InvalidFieldType),
    }
    let required = [
        "schema_version",
        "accepts_object_type",
        "model_profile",
        "engine_profile",
        "prefix_requirements",
        "opaque_requirements",
    ];
    reject_if!(check_required(root, &required));
    reject_if!(check_allowed(root, &required));
    match root.get("accepts_object_type") {
        Some(Value::String(value)) if value == NATIVE_KV_PAGE || value == OPAQUE_ENGINE_BLOB => {}
        Some(Value::String(_)) => return Some(ReasonCode::UnknownObjectType),
        _ => return Some(ReasonCode::InvalidFieldType),
    }
    reject_if!(validate_optional(
        root.get("model_profile")?,
        validate_model_profile
    ));
    reject_if!(validate_required_object(
        root.get("engine_profile")?,
        validate_engine_profile
    ));
    reject_if!(validate_optional(
        root.get("prefix_requirements")?,
        validate_prefix_requirements,
    ));
    reject_if!(validate_optional(
        root.get("opaque_requirements")?,
        validate_opaque_profile
    ));
    None
}

fn validate_model_profile(map: &Map<String, Value>) -> Option<ReasonCode> {
    let required = [
        "model_id",
        "model_revision",
        "model_hash",
        "tokenizer_hash",
        "config_hash",
        "rope_config_hash",
        "quantization",
        "dtype",
        "num_layers",
        "num_attention_heads",
        "num_kv_heads",
        "head_dim",
        "max_position_embeddings",
    ];
    reject_if!(check_required(map, &required));
    reject_if!(check_allowed(map, &required));
    for key in ["model_id", "model_revision", "quantization", "dtype"] {
        reject_if!(require_non_empty_string(map.get(key)?));
    }
    for key in [
        "model_hash",
        "tokenizer_hash",
        "config_hash",
        "rope_config_hash",
    ] {
        reject_if!(require_hash_like(map.get(key)?, false));
    }
    for key in [
        "num_layers",
        "num_attention_heads",
        "num_kv_heads",
        "head_dim",
        "max_position_embeddings",
    ] {
        reject_if!(require_positive_integer(map.get(key)?));
    }
    None
}

fn validate_engine_profile(map: &Map<String, Value>) -> Option<ReasonCode> {
    let required = [
        "engine_name",
        "engine_version",
        "integration_name",
        "integration_version",
        "attention_impl",
        "kv_layout",
        "block_size_tokens",
        "kv_cache_format",
    ];
    reject_if!(check_required(map, &required));
    reject_if!(check_allowed(map, &required));
    for key in [
        "engine_name",
        "engine_version",
        "integration_name",
        "integration_version",
        "attention_impl",
        "kv_layout",
        "kv_cache_format",
    ] {
        reject_if!(require_non_empty_string(map.get(key)?));
    }
    reject_if!(require_positive_integer(map.get("block_size_tokens")?));
    None
}

fn validate_prefix_profile(map: &Map<String, Value>) -> Option<ReasonCode> {
    let required = [
        "token_count",
        "token_range",
        "absolute_position_range",
        "prefix_hash",
        "token_hash",
        "tokenizer_hash",
        "rope_config_hash",
        "mm_hashes",
    ];
    reject_if!(check_required(map, &required));
    reject_if!(check_allowed(map, &required));
    reject_if!(require_non_negative_integer(map.get("token_count")?));
    reject_if!(require_range(map.get("token_range")?));
    reject_if!(require_range(map.get("absolute_position_range")?));
    for key in [
        "prefix_hash",
        "token_hash",
        "tokenizer_hash",
        "rope_config_hash",
    ] {
        reject_if!(require_hash_like(map.get(key)?, false));
    }
    reject_if!(require_hash_array(map.get("mm_hashes")?));
    None
}

fn validate_prefix_requirements(map: &Map<String, Value>) -> Option<ReasonCode> {
    let required = [
        "prefix_hash",
        "token_hash",
        "tokenizer_hash",
        "rope_config_hash",
        "token_range",
        "absolute_position_range",
        "allow_mm_hashes",
    ];
    reject_if!(check_required(map, &required));
    reject_if!(check_allowed(map, &required));
    for key in [
        "prefix_hash",
        "token_hash",
        "tokenizer_hash",
        "rope_config_hash",
    ] {
        reject_if!(require_hash_like(map.get(key)?, false));
    }
    reject_if!(require_range(map.get("token_range")?));
    reject_if!(require_range(map.get("absolute_position_range")?));
    reject_if!(require_hash_array(map.get("allow_mm_hashes")?));
    None
}

fn validate_payload_profile(map: &Map<String, Value>) -> Option<ReasonCode> {
    let required = ["byte_length", "compression", "payload_encoding"];
    reject_if!(check_required(map, &required));
    reject_if!(check_allowed(map, &required));
    reject_if!(require_non_negative_integer(map.get("byte_length")?));
    reject_if!(require_non_empty_string(map.get("compression")?));
    reject_if!(require_non_empty_string(map.get("payload_encoding")?));
    None
}

fn validate_native_tensor_profile(map: &Map<String, Value>) -> Option<ReasonCode> {
    let required = [
        "layer_id",
        "kv_block_id",
        "block_size_tokens",
        "block_token_count",
        "token_range",
        "tensor_role",
        "tensor_shape",
        "tensor_dtype",
        "tensor_layout",
    ];
    reject_if!(check_required(map, &required));
    reject_if!(check_allowed(map, &required));
    reject_if!(require_non_negative_integer(map.get("layer_id")?));
    reject_if!(require_non_negative_integer(map.get("kv_block_id")?));
    reject_if!(require_positive_integer(map.get("block_size_tokens")?));
    reject_if!(require_non_negative_integer(map.get("block_token_count")?));
    reject_if!(require_range(map.get("token_range")?));
    match map.get("tensor_role") {
        Some(Value::String(value))
            if ["key", "value", "kv", "kv_pair"].contains(&value.as_str()) => {}
        Some(Value::String(_)) => return Some(ReasonCode::SchemaValidationFailed),
        _ => return Some(ReasonCode::InvalidFieldType),
    }
    reject_if!(require_positive_integer_array(map.get("tensor_shape")?));
    reject_if!(require_non_empty_string(map.get("tensor_dtype")?));
    reject_if!(require_non_empty_string(map.get("tensor_layout")?));
    None
}

fn validate_opaque_profile(map: &Map<String, Value>) -> Option<ReasonCode> {
    let required = [
        "engine_key_hash",
        "engine_payload_type",
        "engine_key_repr_version",
    ];
    reject_if!(check_required(map, &required));
    reject_if!(check_allowed(map, &required));
    reject_if!(require_hash_like(map.get("engine_key_hash")?, false));
    reject_if!(require_non_empty_string(map.get("engine_payload_type")?));
    reject_if!(require_non_empty_string(
        map.get("engine_key_repr_version")?
    ));
    None
}

fn validate_integrity(map: &Map<String, Value>) -> Option<ReasonCode> {
    let required = [
        "descriptor_hash",
        "payload_hash",
        "object_id_algorithm",
        "chunk_size_bytes",
        "chunk_hashes",
    ];
    reject_if!(check_required(map, &required));
    reject_if!(check_allowed(map, &required));
    reject_if!(require_hash_like(map.get("descriptor_hash")?, false));
    reject_if!(require_hash_like(map.get("payload_hash")?, false));
    match map.get("object_id_algorithm") {
        Some(Value::String(value)) if value == "bifrost.object_id.v1" => {}
        Some(Value::String(_)) => return Some(ReasonCode::SchemaValidationFailed),
        _ => return Some(ReasonCode::InvalidFieldType),
    }
    reject_if!(require_positive_integer(map.get("chunk_size_bytes")?));
    reject_if!(require_hash_array(map.get("chunk_hashes")?));
    None
}

fn validate_provenance(map: &Map<String, Value>) -> Option<ReasonCode> {
    let required = ["source", "notes", "producer_commit", "producer_hostname"];
    reject_if!(check_required(map, &required));
    reject_if!(check_allowed(map, &required));
    reject_if!(require_non_empty_string(map.get("source")?));
    if !matches!(map.get("notes"), Some(Value::String(_))) {
        return Some(ReasonCode::InvalidFieldType);
    }
    reject_if!(require_non_empty_string(map.get("producer_commit")?));
    reject_if!(require_non_empty_string(map.get("producer_hostname")?));
    None
}

fn validate_optional(
    value: &Value,
    validator: fn(&Map<String, Value>) -> Option<ReasonCode>,
) -> Option<ReasonCode> {
    if value.is_null() {
        return None;
    }
    validate_required_object(value, validator)
}

fn validate_required_object(
    value: &Value,
    validator: fn(&Map<String, Value>) -> Option<ReasonCode>,
) -> Option<ReasonCode> {
    let Some(map) = value.as_object() else {
        return Some(ReasonCode::InvalidFieldType);
    };
    validator(map)
}

fn check_required(map: &Map<String, Value>, required: &[&str]) -> Option<ReasonCode> {
    for key in required {
        if !map.contains_key(*key) {
            return Some(ReasonCode::MissingRequiredField);
        }
    }
    None
}

fn check_allowed(map: &Map<String, Value>, allowed: &[&str]) -> Option<ReasonCode> {
    for key in map.keys() {
        if !allowed.contains(&key.as_str()) {
            return Some(ReasonCode::ExtraFieldRejected);
        }
    }
    None
}

fn require_non_empty_string(value: &Value) -> Option<ReasonCode> {
    match value {
        Value::String(text) if !text.is_empty() => None,
        Value::String(_) => Some(ReasonCode::SchemaValidationFailed),
        _ => Some(ReasonCode::InvalidFieldType),
    }
}

fn require_non_negative_integer(value: &Value) -> Option<ReasonCode> {
    match value.as_u64() {
        Some(_) => None,
        None if value.is_number() => Some(ReasonCode::SchemaValidationFailed),
        None => Some(ReasonCode::InvalidFieldType),
    }
}

fn require_positive_integer(value: &Value) -> Option<ReasonCode> {
    match value.as_u64() {
        Some(value) if value > 0 => None,
        Some(_) => Some(ReasonCode::SchemaValidationFailed),
        None if value.is_number() => Some(ReasonCode::SchemaValidationFailed),
        None => Some(ReasonCode::InvalidFieldType),
    }
}

fn require_range(value: &Value) -> Option<ReasonCode> {
    let Some(map) = value.as_object() else {
        return Some(ReasonCode::InvalidFieldType);
    };
    let required = ["start", "end"];
    reject_if!(check_required(map, &required));
    reject_if!(check_allowed(map, &required));
    reject_if!(require_non_negative_integer(map.get("start")?));
    reject_if!(require_non_negative_integer(map.get("end")?));
    None
}

fn require_positive_integer_array(value: &Value) -> Option<ReasonCode> {
    let Some(items) = value.as_array() else {
        return Some(ReasonCode::InvalidFieldType);
    };
    if items.is_empty() {
        return Some(ReasonCode::SchemaValidationFailed);
    }
    for item in items {
        reject_if!(require_positive_integer(item));
    }
    None
}

fn require_hash_array(value: &Value) -> Option<ReasonCode> {
    let Some(items) = value.as_array() else {
        return Some(ReasonCode::InvalidFieldType);
    };
    for item in items {
        reject_if!(require_hash_like(item, false));
    }
    None
}

fn require_hash_like(value: &Value, object_id: bool) -> Option<ReasonCode> {
    let Value::String(text) = value else {
        return Some(ReasonCode::InvalidFieldType);
    };
    let (prefix, hex) = if object_id {
        (
            "bifrost://object/blake3/",
            text.strip_prefix("bifrost://object/blake3/"),
        )
    } else {
        ("blake3:", text.strip_prefix("blake3:"))
    };
    let Some(hex) = hex else {
        let _ = prefix;
        return Some(ReasonCode::SchemaValidationFailed);
    };
    if hex.len() != 64
        || !hex
            .bytes()
            .all(|byte| byte.is_ascii_hexdigit() && !byte.is_ascii_uppercase())
    {
        return Some(ReasonCode::SchemaValidationFailed);
    }
    None
}

fn _ranges_equal(left: &TokenRange, right: &TokenRange) -> bool {
    left.start == right.start && left.end == right.end
}

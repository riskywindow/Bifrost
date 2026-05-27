use serde::{Deserialize, Serialize};
use std::fmt;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ReasonCode {
    Accepted,
    ParseError,
    SchemaValidationFailed,
    UnknownSchemaVersion,
    UnknownObjectType,
    MissingRequiredField,
    ExtraFieldRejected,
    InvalidFieldType,
    ByteLengthMismatch,
    PayloadHashMismatch,
    DescriptorHashMismatch,
    ObjectIdMismatch,
    WrongModelHash,
    WrongTokenizerHash,
    WrongConfigHash,
    WrongRopeHash,
    WrongDtype,
    WrongNumLayers,
    WrongNumKvHeads,
    WrongHeadDim,
    WrongEngineName,
    WrongEngineVersion,
    WrongAttentionImpl,
    WrongKvLayout,
    WrongBlockSizeTokens,
    WrongKvCacheFormat,
    WrongPrefixHash,
    WrongTokenRange,
    WrongAbsolutePositionRange,
    InvalidLayerId,
    InvalidKvBlockId,
    InvalidBlockTokenCount,
    InvalidTensorShape,
    InvalidTensorDtype,
    InvalidTensorLayout,
    OpaqueWrongEngineKey,
    OpaqueWrongEngineName,
    OpaqueWrongIntegrationName,
    OpaquePayloadNotInterpretable,
    UnsupportedCompression,
    UnsupportedPayloadEncoding,
}

impl ReasonCode {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Accepted => "accepted",
            Self::ParseError => "parse_error",
            Self::SchemaValidationFailed => "schema_validation_failed",
            Self::UnknownSchemaVersion => "unknown_schema_version",
            Self::UnknownObjectType => "unknown_object_type",
            Self::MissingRequiredField => "missing_required_field",
            Self::ExtraFieldRejected => "extra_field_rejected",
            Self::InvalidFieldType => "invalid_field_type",
            Self::ByteLengthMismatch => "byte_length_mismatch",
            Self::PayloadHashMismatch => "payload_hash_mismatch",
            Self::DescriptorHashMismatch => "descriptor_hash_mismatch",
            Self::ObjectIdMismatch => "object_id_mismatch",
            Self::WrongModelHash => "wrong_model_hash",
            Self::WrongTokenizerHash => "wrong_tokenizer_hash",
            Self::WrongConfigHash => "wrong_config_hash",
            Self::WrongRopeHash => "wrong_rope_hash",
            Self::WrongDtype => "wrong_dtype",
            Self::WrongNumLayers => "wrong_num_layers",
            Self::WrongNumKvHeads => "wrong_num_kv_heads",
            Self::WrongHeadDim => "wrong_head_dim",
            Self::WrongEngineName => "wrong_engine_name",
            Self::WrongEngineVersion => "wrong_engine_version",
            Self::WrongAttentionImpl => "wrong_attention_impl",
            Self::WrongKvLayout => "wrong_kv_layout",
            Self::WrongBlockSizeTokens => "wrong_block_size_tokens",
            Self::WrongKvCacheFormat => "wrong_kv_cache_format",
            Self::WrongPrefixHash => "wrong_prefix_hash",
            Self::WrongTokenRange => "wrong_token_range",
            Self::WrongAbsolutePositionRange => "wrong_absolute_position_range",
            Self::InvalidLayerId => "invalid_layer_id",
            Self::InvalidKvBlockId => "invalid_kv_block_id",
            Self::InvalidBlockTokenCount => "invalid_block_token_count",
            Self::InvalidTensorShape => "invalid_tensor_shape",
            Self::InvalidTensorDtype => "invalid_tensor_dtype",
            Self::InvalidTensorLayout => "invalid_tensor_layout",
            Self::OpaqueWrongEngineKey => "opaque_wrong_engine_key",
            Self::OpaqueWrongEngineName => "opaque_wrong_engine_name",
            Self::OpaqueWrongIntegrationName => "opaque_wrong_integration_name",
            Self::OpaquePayloadNotInterpretable => "opaque_payload_not_interpretable",
            Self::UnsupportedCompression => "unsupported_compression",
            Self::UnsupportedPayloadEncoding => "unsupported_payload_encoding",
        }
    }
}

impl fmt::Display for ReasonCode {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.as_str())
    }
}

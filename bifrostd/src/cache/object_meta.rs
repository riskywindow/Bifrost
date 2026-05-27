use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct TokenRange {
    pub start: u64,
    pub end: u64,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ModelProfile {
    pub model_id: String,
    pub model_revision: String,
    pub model_hash: String,
    pub tokenizer_hash: String,
    pub config_hash: String,
    pub rope_config_hash: String,
    pub quantization: String,
    pub dtype: String,
    pub num_layers: u64,
    pub num_attention_heads: u64,
    pub num_kv_heads: u64,
    pub head_dim: u64,
    pub max_position_embeddings: u64,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct EngineProfile {
    pub engine_name: String,
    pub engine_version: String,
    pub integration_name: String,
    pub integration_version: String,
    pub attention_impl: String,
    pub kv_layout: String,
    pub block_size_tokens: u64,
    pub kv_cache_format: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PrefixProfile {
    pub token_count: u64,
    pub token_range: TokenRange,
    pub absolute_position_range: TokenRange,
    pub prefix_hash: String,
    pub token_hash: String,
    pub tokenizer_hash: String,
    pub rope_config_hash: String,
    pub mm_hashes: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PayloadProfile {
    pub byte_length: u64,
    pub compression: String,
    pub payload_encoding: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct NativeTensorProfile {
    pub layer_id: u64,
    pub kv_block_id: u64,
    pub block_size_tokens: u64,
    pub block_token_count: u64,
    pub token_range: TokenRange,
    pub tensor_role: String,
    pub tensor_shape: Vec<u64>,
    pub tensor_dtype: String,
    pub tensor_layout: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct OpaqueEngineProfile {
    pub engine_key_hash: String,
    pub engine_payload_type: String,
    pub engine_key_repr_version: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct IntegrityProfile {
    pub descriptor_hash: String,
    pub payload_hash: String,
    pub object_id_algorithm: String,
    pub chunk_size_bytes: u64,
    pub chunk_hashes: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ProvenanceProfile {
    pub source: String,
    pub notes: String,
    pub producer_commit: String,
    pub producer_hostname: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct BifrostKvObjectDescriptor {
    pub schema_version: String,
    pub object_type: String,
    pub object_id: String,
    pub created_at_unix_ms: u64,
    pub created_by: String,
    pub model_profile: ModelProfile,
    pub engine_profile: EngineProfile,
    pub prefix_profile: Option<PrefixProfile>,
    pub payload_profile: PayloadProfile,
    pub native_tensor_profile: Option<NativeTensorProfile>,
    pub opaque_engine_profile: Option<OpaqueEngineProfile>,
    pub integrity: IntegrityProfile,
    pub provenance: ProvenanceProfile,
}

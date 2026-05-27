use crate::cache::object_meta::{EngineProfile, ModelProfile, OpaqueEngineProfile, TokenRange};
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PrefixRequirements {
    pub prefix_hash: String,
    pub token_hash: String,
    pub tokenizer_hash: String,
    pub rope_config_hash: String,
    pub token_range: TokenRange,
    pub absolute_position_range: TokenRange,
    pub allow_mm_hashes: Vec<String>,
}

pub type OpaqueRequirements = OpaqueEngineProfile;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct BifrostTargetProfile {
    pub schema_version: String,
    pub accepts_object_type: String,
    pub model_profile: Option<ModelProfile>,
    pub engine_profile: EngineProfile,
    pub prefix_requirements: Option<PrefixRequirements>,
    pub opaque_requirements: Option<OpaqueRequirements>,
}

use serde::{Deserialize, Serialize};
use std::str::FromStr;

pub const MANIFEST_ID_PREFIX: &str = "bifrost://manifest/blake3/";

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ManifestType {
    PrefixManifest,
    SessionManifest,
}

impl ManifestType {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::PrefixManifest => "prefix_manifest",
            Self::SessionManifest => "session_manifest",
        }
    }
}

impl std::fmt::Display for ManifestType {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(self.as_str())
    }
}

impl FromStr for ManifestType {
    type Err = String;

    fn from_str(value: &str) -> Result<Self, Self::Err> {
        match value {
            "prefix_manifest" | "prefix" => Ok(Self::PrefixManifest),
            "session_manifest" | "session" => Ok(Self::SessionManifest),
            other => Err(format!("unsupported manifest type: {other}")),
        }
    }
}

impl rusqlite::types::FromSql for ManifestType {
    fn column_result(value: rusqlite::types::ValueRef<'_>) -> rusqlite::types::FromSqlResult<Self> {
        let value = value.as_str()?;
        value.parse().map_err(|error: String| {
            rusqlite::types::FromSqlError::Other(Box::new(std::io::Error::new(
                std::io::ErrorKind::InvalidData,
                error,
            )))
        })
    }
}

impl rusqlite::types::ToSql for ManifestType {
    fn to_sql(&self) -> rusqlite::Result<rusqlite::types::ToSqlOutput<'_>> {
        Ok(rusqlite::types::ToSqlOutput::from(self.as_str()))
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum CompletenessState {
    Complete,
    Incomplete,
    Corrupt,
    Unknown,
}

impl CompletenessState {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Complete => "complete",
            Self::Incomplete => "incomplete",
            Self::Corrupt => "corrupt",
            Self::Unknown => "unknown",
        }
    }
}

impl std::fmt::Display for CompletenessState {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(self.as_str())
    }
}

impl FromStr for CompletenessState {
    type Err = String;

    fn from_str(value: &str) -> Result<Self, Self::Err> {
        match value {
            "complete" => Ok(Self::Complete),
            "incomplete" => Ok(Self::Incomplete),
            "corrupt" => Ok(Self::Corrupt),
            "unknown" => Ok(Self::Unknown),
            other => Err(format!("unsupported completeness state: {other}")),
        }
    }
}

impl rusqlite::types::FromSql for CompletenessState {
    fn column_result(value: rusqlite::types::ValueRef<'_>) -> rusqlite::types::FromSqlResult<Self> {
        let value = value.as_str()?;
        value.parse().map_err(|error: String| {
            rusqlite::types::FromSqlError::Other(Box::new(std::io::Error::new(
                std::io::ErrorKind::InvalidData,
                error,
            )))
        })
    }
}

impl rusqlite::types::ToSql for CompletenessState {
    fn to_sql(&self) -> rusqlite::Result<rusqlite::types::ToSqlOutput<'_>> {
        Ok(rusqlite::types::ToSqlOutput::from(self.as_str()))
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ManifestRecord {
    pub manifest_id: String,
    pub manifest_type: ManifestType,
    pub model_hash: Option<String>,
    pub tokenizer_hash: Option<String>,
    pub rope_config_hash: Option<String>,
    pub prefix_hash: String,
    pub token_range_start: i64,
    pub token_range_end: i64,
    pub completeness_state: CompletenessState,
    pub created_at_unix_ms: i64,
    pub updated_at_unix_ms: i64,
    pub pin_count: i64,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ManifestMember {
    pub manifest_id: String,
    pub object_id: String,
    pub layer_id: Option<i64>,
    pub kv_block_id: Option<i64>,
    pub token_range_start: Option<i64>,
    pub token_range_end: Option<i64>,
    pub required: bool,
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct ManifestListFilter {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub manifest_type: Option<ManifestType>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub model_hash: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub prefix_hash: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub limit: Option<i64>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ManifestExpectedMember {
    pub layer_id: i64,
    pub kv_block_id: i64,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub token_range_start: Option<i64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub token_range_end: Option<i64>,
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct ManifestExpectedCoverage {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub expected_layer_count: Option<i64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub expected_block_count: Option<i64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub expected_members: Option<Vec<ManifestExpectedMember>>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct MissingManifestMember {
    pub manifest_id: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub object_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub layer_id: Option<i64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub kv_block_id: Option<i64>,
    pub required: bool,
    pub reason: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ManifestCompletenessReport {
    pub manifest_id: String,
    pub completeness_state: CompletenessState,
    pub required_count: i64,
    pub serveable_required_count: i64,
    pub missing: Vec<MissingManifestMember>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ManifestInspection {
    pub manifest: ManifestRecord,
    pub members: Vec<ManifestMember>,
}

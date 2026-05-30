use crate::store::errors::{StoreError, StoreResult};
use rusqlite::types::{FromSql, FromSqlError, FromSqlResult, ToSqlOutput, ValueRef};
use rusqlite::ToSql;
use std::fmt;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum ObjectState {
    Staging,
    Committed,
    Verified,
    Pinned,
    Evictable,
    Evicting,
    Evicted,
    Quarantined,
    Missing,
    Corrupt,
}

impl ObjectState {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Staging => "staging",
            Self::Committed => "committed",
            Self::Verified => "verified",
            Self::Pinned => "pinned",
            Self::Evictable => "evictable",
            Self::Evicting => "evicting",
            Self::Evicted => "evicted",
            Self::Quarantined => "quarantined",
            Self::Missing => "missing",
            Self::Corrupt => "corrupt",
        }
    }

    pub fn parse(value: &str) -> StoreResult<Self> {
        match value {
            "staging" => Ok(Self::Staging),
            "committed" => Ok(Self::Committed),
            "verified" => Ok(Self::Verified),
            "pinned" => Ok(Self::Pinned),
            "evictable" => Ok(Self::Evictable),
            "evicting" => Ok(Self::Evicting),
            "evicted" => Ok(Self::Evicted),
            "quarantined" => Ok(Self::Quarantined),
            "missing" => Ok(Self::Missing),
            "corrupt" => Ok(Self::Corrupt),
            other => Err(StoreError::InvalidState(other.to_string())),
        }
    }
}

impl fmt::Display for ObjectState {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.as_str())
    }
}

impl ToSql for ObjectState {
    fn to_sql(&self) -> rusqlite::Result<ToSqlOutput<'_>> {
        Ok(ToSqlOutput::from(self.as_str()))
    }
}

impl FromSql for ObjectState {
    fn column_result(value: ValueRef<'_>) -> FromSqlResult<Self> {
        let value = value.as_str()?;
        ObjectState::parse(value).map_err(|error| FromSqlError::Other(Box::new(error)))
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ObjectRecord {
    pub object_id: String,
    pub object_type: String,
    pub schema_version: String,
    pub descriptor_hash: String,
    pub payload_hash: String,
    pub byte_length: i64,
    pub state: ObjectState,
    pub created_at_unix_ms: i64,
    pub committed_at_unix_ms: Option<i64>,
    pub verified_at_unix_ms: Option<i64>,
    pub last_accessed_unix_ms: Option<i64>,
    pub access_count: i64,
    pub pin_count: i64,
    pub ttl_expires_at_unix_ms: Option<i64>,
    pub quarantine_reason: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ObjectLocation {
    pub object_id: String,
    pub tier: String,
    pub meta_path: String,
    pub payload_path: String,
    pub bytes_on_disk: i64,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ObjectCompatibility {
    pub object_id: String,
    pub model_hash: Option<String>,
    pub tokenizer_hash: Option<String>,
    pub config_hash: Option<String>,
    pub rope_config_hash: Option<String>,
    pub dtype: Option<String>,
    pub engine_name: Option<String>,
    pub engine_version: Option<String>,
    pub integration_name: Option<String>,
    pub kv_cache_format: Option<String>,
    pub prefix_hash: Option<String>,
    pub token_range_start: Option<i64>,
    pub token_range_end: Option<i64>,
    pub layer_id: Option<i64>,
    pub kv_block_id: Option<i64>,
    pub opaque_engine_key_hash: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ObjectAccess {
    pub object_id: String,
    pub last_get_unix_ms: Option<i64>,
    pub last_put_unix_ms: Option<i64>,
    pub get_count: i64,
    pub put_count: i64,
    pub bytes_read_total: i64,
    pub bytes_written_total: i64,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct StoreEvent {
    pub event_id: i64,
    pub timestamp_unix_ms: i64,
    pub event_type: String,
    pub object_id: Option<String>,
    pub manifest_id: Option<String>,
    pub details_json: Option<String>,
}

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct ObjectListFilter {
    pub state: Option<ObjectState>,
    pub model_hash: Option<String>,
    pub prefix_hash: Option<String>,
    pub engine_name: Option<String>,
    pub opaque_engine_key_hash: Option<String>,
    pub layer_id: Option<i64>,
    pub kv_block_id: Option<i64>,
    pub limit: Option<i64>,
    pub offset: Option<i64>,
}

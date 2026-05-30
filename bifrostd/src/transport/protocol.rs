use crate::store::{
    EvictionReport, ManifestCompletenessReport, ManifestInspection, ManifestListFilter,
    ManifestRecord, ObjectCompatibility, ObjectInspection, ObjectListFilter, ObjectRecord,
    ObjectState, StoreStats,
};
use crate::transport::frame::TRANSPORT_VERSION;
use serde::{Deserialize, Serialize};

pub const PROTOCOL_VERSION: &str = TRANSPORT_VERSION;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PeerRole {
    Client,
    Daemon,
}

impl PeerRole {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Client => "client",
            Self::Daemon => "daemon",
        }
    }
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct StoreObjectFilter {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub state: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub model_hash: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub prefix_hash: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub engine_name: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub opaque_engine_key_hash: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub layer_id: Option<i64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub kv_block_id: Option<i64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub limit: Option<i64>,
}

impl StoreObjectFilter {
    pub fn to_list_filter(&self) -> anyhow::Result<ObjectListFilter> {
        Ok(ObjectListFilter {
            state: self.state.as_deref().map(ObjectState::parse).transpose()?,
            model_hash: self.model_hash.clone(),
            prefix_hash: self.prefix_hash.clone(),
            engine_name: self.engine_name.clone(),
            opaque_engine_key_hash: self.opaque_engine_key_hash.clone(),
            layer_id: self.layer_id,
            kv_block_id: self.kv_block_id,
            limit: self.limit,
            offset: None,
        })
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct StoreObjectSummary {
    pub object_id: String,
    pub object_type: String,
    pub state: String,
    pub byte_length: i64,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub model_hash: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub prefix_hash: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub engine_name: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub opaque_engine_key_hash: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub layer_id: Option<i64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub kv_block_id: Option<i64>,
    pub pin_count: i64,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub last_accessed_unix_ms: Option<i64>,
}

impl StoreObjectSummary {
    pub fn from_parts(record: &ObjectRecord, compatibility: &ObjectCompatibility) -> Self {
        Self {
            object_id: record.object_id.clone(),
            object_type: record.object_type.clone(),
            state: record.state.as_str().to_string(),
            byte_length: record.byte_length,
            model_hash: compatibility.model_hash.clone(),
            prefix_hash: compatibility.prefix_hash.clone(),
            engine_name: compatibility.engine_name.clone(),
            opaque_engine_key_hash: compatibility.opaque_engine_key_hash.clone(),
            layer_id: compatibility.layer_id,
            kv_block_id: compatibility.kv_block_id,
            pin_count: record.pin_count,
            last_accessed_unix_ms: record.last_accessed_unix_ms,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct StoreListResponse {
    pub objects: Vec<StoreObjectSummary>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct StoreInspectResponse {
    pub found: bool,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub object: Option<StoreObjectSummary>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub descriptor_hash: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub payload_hash: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub schema_version: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub created_at_unix_ms: Option<i64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub committed_at_unix_ms: Option<i64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub verified_at_unix_ms: Option<i64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub files_present: Option<bool>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub servable: Option<bool>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub bytes_on_disk: Option<i64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub reason: Option<String>,
}

impl StoreInspectResponse {
    pub fn found(inspection: &ObjectInspection) -> Self {
        Self {
            found: true,
            object: Some(StoreObjectSummary::from_parts(
                &inspection.record,
                &inspection.compatibility,
            )),
            descriptor_hash: Some(inspection.record.descriptor_hash.clone()),
            payload_hash: Some(inspection.record.payload_hash.clone()),
            schema_version: Some(inspection.record.schema_version.clone()),
            created_at_unix_ms: Some(inspection.record.created_at_unix_ms),
            committed_at_unix_ms: inspection.record.committed_at_unix_ms,
            verified_at_unix_ms: inspection.record.verified_at_unix_ms,
            files_present: Some(inspection.files_present),
            servable: Some(inspection.servable),
            bytes_on_disk: Some(inspection.location.bytes_on_disk),
            reason: None,
        }
    }

    pub fn miss(reason: impl Into<String>) -> Self {
        Self {
            found: false,
            object: None,
            descriptor_hash: None,
            payload_hash: None,
            schema_version: None,
            created_at_unix_ms: None,
            committed_at_unix_ms: None,
            verified_at_unix_ms: None,
            files_present: None,
            servable: None,
            bytes_on_disk: None,
            reason: Some(reason.into()),
        }
    }
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct StoreStatsResponse {
    pub object_count: i64,
    pub total_logical_bytes: i64,
    pub total_bytes_on_disk: i64,
    pub staging_count: i64,
    pub committed_count: i64,
    pub verified_count: i64,
    pub pinned_count: i64,
    pub evictable_count: i64,
    pub evicting_count: i64,
    pub evicted_count: i64,
    pub quarantined_count: i64,
    pub missing_count: i64,
    pub corrupt_count: i64,
    pub total_pin_count: i64,
    pub total_access_count: i64,
}

impl From<StoreStats> for StoreStatsResponse {
    fn from(stats: StoreStats) -> Self {
        Self {
            object_count: stats.object_count,
            total_logical_bytes: stats.total_logical_bytes,
            total_bytes_on_disk: stats.total_bytes_on_disk,
            staging_count: stats.staging_count,
            committed_count: stats.committed_count,
            verified_count: stats.verified_count,
            pinned_count: stats.pinned_count,
            evictable_count: stats.evictable_count,
            evicting_count: stats.evicting_count,
            evicted_count: stats.evicted_count,
            quarantined_count: stats.quarantined_count,
            missing_count: stats.missing_count,
            corrupt_count: stats.corrupt_count,
            total_pin_count: stats.total_pin_count,
            total_access_count: stats.total_access_count,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct StoreOperationResponse {
    pub object_id: String,
    pub status: String,
    pub reason: String,
}

impl StoreOperationResponse {
    pub fn ok(object_id: impl Into<String>) -> Self {
        Self {
            object_id: object_id.into(),
            status: "ok".to_string(),
            reason: String::new(),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "operation", rename_all = "snake_case")]
pub enum StoreTtlRequest {
    Set { expires_at_unix_ms: i64 },
    Clear,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "operation", rename_all = "snake_case")]
pub enum StoreLifecycleRequest {
    Quarantine { reason: String },
    MarkVerified,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct StoreEvictRequest {
    pub policy: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub target_bytes: Option<i64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub max_objects: Option<usize>,
    #[serde(default)]
    pub dry_run: bool,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub now_unix_ms: Option<i64>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct StoreEvictResponse {
    pub report: EvictionReport,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "operation", rename_all = "snake_case")]
pub enum StoreManifestRequest {
    CreatePrefix {
        #[serde(default, skip_serializing_if = "Option::is_none")]
        model_hash: Option<String>,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        tokenizer_hash: Option<String>,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        rope_config_hash: Option<String>,
        prefix_hash: String,
        token_range_start: i64,
        token_range_end: i64,
    },
    AddMember {
        manifest_id: String,
        object_id: String,
        #[serde(default = "default_required")]
        required: bool,
    },
    RemoveMember {
        manifest_id: String,
        object_id: String,
    },
    Inspect {
        manifest_id: String,
    },
    List {
        #[serde(default)]
        filter: ManifestListFilter,
    },
    Check {
        manifest_id: String,
    },
    Pin {
        manifest_id: String,
    },
    Unpin {
        manifest_id: String,
    },
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct StoreManifestResponse {
    pub status: String,
    pub reason: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub manifest: Option<ManifestInspection>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub manifests: Vec<ManifestRecord>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub completeness: Option<ManifestCompletenessReport>,
}

impl StoreManifestResponse {
    pub fn ok() -> Self {
        Self {
            status: "ok".to_string(),
            reason: String::new(),
            manifest: None,
            manifests: Vec::new(),
            completeness: None,
        }
    }
}

fn default_required() -> bool {
    true
}

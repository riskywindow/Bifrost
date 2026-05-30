use crate::store::object_record::{ObjectLocation, ObjectRecord};
use serde::{Deserialize, Serialize};
use std::str::FromStr;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum EvictionPolicy {
    Lru,
    SizeAwareLru,
    TtlExpired,
}

impl EvictionPolicy {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Lru => "lru",
            Self::SizeAwareLru => "size-aware-lru",
            Self::TtlExpired => "ttl-expired",
        }
    }
}

impl FromStr for EvictionPolicy {
    type Err = String;

    fn from_str(value: &str) -> Result<Self, Self::Err> {
        match value {
            "lru" => Ok(Self::Lru),
            "size-aware-lru" | "size_aware_lru" => Ok(Self::SizeAwareLru),
            "ttl-expired" | "ttl_expired" => Ok(Self::TtlExpired),
            other => Err(format!("unsupported eviction policy: {other}")),
        }
    }
}

impl std::fmt::Display for EvictionPolicy {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(self.as_str())
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct EvictionRequest {
    pub policy: EvictionPolicy,
    pub target_bytes: Option<i64>,
    pub max_objects: Option<usize>,
    pub dry_run: bool,
    pub now_unix_ms: i64,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct EvictionCandidate {
    pub object_id: String,
    pub state: String,
    pub bytes_on_disk: i64,
    pub byte_length: i64,
    pub last_accessed_unix_ms: Option<i64>,
    pub ttl_expires_at_unix_ms: Option<i64>,
    pub eviction_score: i128,
}

impl EvictionCandidate {
    pub fn from_record_location(
        record: &ObjectRecord,
        location: &ObjectLocation,
        eviction_score: i128,
    ) -> Self {
        Self {
            object_id: record.object_id.clone(),
            state: record.state.as_str().to_string(),
            bytes_on_disk: location.bytes_on_disk,
            byte_length: record.byte_length,
            last_accessed_unix_ms: record.last_accessed_unix_ms,
            ttl_expires_at_unix_ms: record.ttl_expires_at_unix_ms,
            eviction_score,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct EvictedObject {
    pub object_id: String,
    pub bytes_freed: i64,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct EvictionFailure {
    pub object_id: String,
    pub reason: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct EvictionReport {
    pub policy: String,
    pub dry_run: bool,
    pub target_bytes: Option<i64>,
    pub starting_bytes_on_disk: i64,
    pub final_bytes_on_disk: i64,
    pub planned_bytes: i64,
    pub freed_bytes: i64,
    pub candidates: Vec<EvictionCandidate>,
    pub evicted: Vec<EvictedObject>,
    pub failures: Vec<EvictionFailure>,
    pub protected_pinned_count: i64,
    pub skipped_unsafe_count: i64,
    pub target_reached: bool,
    pub reason: String,
}

impl EvictionReport {
    pub fn empty(request: &EvictionRequest, starting_bytes_on_disk: i64) -> Self {
        Self {
            policy: request.policy.as_str().to_string(),
            dry_run: request.dry_run,
            target_bytes: request.target_bytes,
            starting_bytes_on_disk,
            final_bytes_on_disk: starting_bytes_on_disk,
            planned_bytes: 0,
            freed_bytes: 0,
            candidates: Vec::new(),
            evicted: Vec::new(),
            failures: Vec::new(),
            protected_pinned_count: 0,
            skipped_unsafe_count: 0,
            target_reached: request
                .target_bytes
                .map(|target| starting_bytes_on_disk <= target)
                .unwrap_or(false),
            reason: String::new(),
        }
    }
}

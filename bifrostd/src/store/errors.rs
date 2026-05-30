use thiserror::Error;

pub type StoreResult<T> = Result<T, StoreError>;

#[derive(Debug, Error)]
pub enum StoreError {
    #[error("catalog error: {0}")]
    Catalog(#[from] rusqlite::Error),
    #[error("filesystem error: {0}")]
    Filesystem(#[from] std::io::Error),
    #[error("JSON error: {0}")]
    Json(#[from] serde_json::Error),
    #[error("transport error: {0}")]
    Transport(#[from] crate::transport::TransportError),
    #[error("integrity error: {0}")]
    Integrity(String),
    #[error("compatibility error: {0}")]
    Compatibility(String),
    #[error("manifest error: {0}")]
    Manifest(String),
    #[error("eviction error: {0}")]
    Eviction(String),
    #[error("fsck finding: {0}")]
    Fsck(String),
    #[error("object not found: {0}")]
    NotFound(String),
    #[error("invalid object state: {0}")]
    InvalidState(String),
    #[error("invalid lifecycle transition from {from} to {to}")]
    InvalidStateTransition { from: String, to: String },
    #[error("catalog schema version {found} is newer than supported version {supported}")]
    FutureSchemaVersion { found: i64, supported: i64 },
}

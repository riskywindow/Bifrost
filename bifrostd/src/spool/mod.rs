pub mod committed;
pub mod layout;
pub mod staging;

use crate::cache::validate_object;
use crate::transport::ChunkManifest;
use layout::{SpoolLayout, StagingPaths};
use serde_json::Value;
use std::fs::{self, File};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use thiserror::Error;

pub use committed::CommittedObjectPaths;
pub use staging::StagedPayload;

pub type SpoolResult<T> = Result<T, SpoolError>;

#[derive(Debug, Error)]
pub enum SpoolError {
    #[error("I/O error: {0}")]
    Io(#[from] std::io::Error),
    #[error("JSON error: {0}")]
    Json(#[from] serde_json::Error),
    #[error("transport error: {0}")]
    Transport(#[from] crate::transport::TransportError),
    #[error("invalid object id: {0}")]
    InvalidObjectId(String),
    #[error("invalid transfer id: {0}")]
    InvalidTransferId(String),
    #[error("transfer already exists: {0}")]
    TransferAlreadyExists(String),
    #[error("object already exists: {0}")]
    AlreadyExists(String),
    #[error("object not found: {0}")]
    NotFound(String),
    #[error("validation rejected object: {0}")]
    ValidationRejected(String),
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum CommitOutcome {
    Committed { object_id: String },
    AlreadyCommitted { object_id: String },
}

#[derive(Debug, Clone)]
pub struct Spool {
    layout: SpoolLayout,
    staging_lock: Arc<Mutex<()>>,
}

impl Spool {
    pub fn new(root: impl Into<PathBuf>) -> Self {
        Self {
            layout: SpoolLayout::new(root),
            staging_lock: Arc::new(Mutex::new(())),
        }
    }

    pub fn root(&self) -> &Path {
        self.layout.root()
    }

    pub fn create_staging_transfer(
        &self,
        transfer_id: &str,
        metadata_json: &[u8],
        manifest: &ChunkManifest,
    ) -> SpoolResult<()> {
        let _guard = self
            .staging_lock
            .lock()
            .expect("spool staging lock poisoned");
        manifest.validate_shape()?;
        let paths = self.layout.staging_paths(transfer_id)?;
        if paths.transfer_dir.exists() {
            return Err(SpoolError::TransferAlreadyExists(transfer_id.to_string()));
        }

        fs::create_dir_all(&paths.chunks_dir)?;
        write_file_sync(&paths.metadata, metadata_json)?;
        write_json_sync(&paths.manifest, manifest)?;
        fsync_dir(&paths.transfer_dir)?;
        Ok(())
    }

    pub fn write_chunk(
        &self,
        transfer_id: &str,
        chunk_index: u64,
        bytes: &[u8],
    ) -> SpoolResult<()> {
        let _guard = self
            .staging_lock
            .lock()
            .expect("spool staging lock poisoned");
        let paths = self.layout.staging_paths(transfer_id)?;
        let manifest = read_manifest(&paths)?;
        let info = manifest.chunks.get(chunk_index as usize).ok_or_else(|| {
            crate::transport::TransportError::Protocol(format!(
                "chunk index {} is out of range",
                chunk_index
            ))
        })?;
        info.verify(bytes)?;

        let path = paths.chunk_path(chunk_index);
        if path.exists() {
            let existing = fs::read(&path)?;
            if existing == bytes {
                return Ok(());
            }
            return Err(crate::transport::TransportError::Protocol(format!(
                "chunk {} conflicts with previously accepted bytes",
                chunk_index
            ))
            .into());
        }

        write_file_sync(&path, bytes)?;
        fsync_dir(&paths.chunks_dir)?;
        Ok(())
    }

    pub fn assemble_staged_payload(&self, transfer_id: &str) -> SpoolResult<Vec<u8>> {
        let paths = self.layout.staging_paths(transfer_id)?;
        staging::assemble_staged_payload(&paths).map(|payload| payload.bytes)
    }

    pub fn abort_staging_transfer(&self, transfer_id: &str) -> SpoolResult<()> {
        let _guard = self
            .staging_lock
            .lock()
            .expect("spool staging lock poisoned");
        let paths = self.layout.staging_paths(transfer_id)?;
        if paths.transfer_dir.exists() {
            fs::remove_dir_all(paths.transfer_dir)?;
        }
        Ok(())
    }

    pub fn commit_transfer(
        &self,
        transfer_id: &str,
        target_profile: Option<&Value>,
    ) -> SpoolResult<CommitOutcome> {
        let _guard = self
            .staging_lock
            .lock()
            .expect("spool staging lock poisoned");
        let paths = self.layout.staging_paths(transfer_id)?;
        let metadata_bytes = fs::read(&paths.metadata)?;
        let metadata: Value = serde_json::from_slice(&metadata_bytes)?;
        let staged = staging::assemble_staged_payload(&paths)?;
        let payload = staged.bytes;
        let validation = validate_object(&metadata, &payload, target_profile);
        if validation.status != "accepted" {
            return Err(SpoolError::ValidationRejected(validation.reason_code));
        }
        let object_id = validation
            .object_id
            .ok_or_else(|| SpoolError::ValidationRejected("missing_object_id".to_string()))?;
        let descriptor_object_id = metadata
            .get("object_id")
            .and_then(Value::as_str)
            .ok_or_else(|| SpoolError::ValidationRejected("missing_object_id".to_string()))?;
        if staged
            .manifest
            .object_id
            .as_deref()
            .is_some_and(|manifest_object_id| manifest_object_id != descriptor_object_id)
        {
            return Err(SpoolError::ValidationRejected(
                "manifest_object_id_mismatch".to_string(),
            ));
        }
        if object_id != descriptor_object_id {
            return Err(SpoolError::ValidationRejected(
                "object_id_mismatch".to_string(),
            ));
        }

        let committed = self.layout.committed_paths(&object_id)?;
        if committed.metadata.exists() || committed.payload.exists() {
            if committed.metadata.exists()
                && committed.payload.exists()
                && fs::read(&committed.metadata)? == metadata_bytes
                && fs::read(&committed.payload)? == payload
            {
                fs::remove_dir_all(paths.transfer_dir)?;
                return Ok(CommitOutcome::AlreadyCommitted { object_id });
            }
            return Err(SpoolError::AlreadyExists(object_id));
        }

        committed::atomic_commit(&committed, &metadata_bytes, &payload)?;
        fs::remove_dir_all(paths.transfer_dir)?;
        Ok(CommitOutcome::Committed { object_id })
    }

    pub fn has_object(&self, object_id: &str) -> bool {
        self.layout
            .committed_paths(object_id)
            .map(|paths| paths.metadata.exists() && paths.payload.exists())
            .unwrap_or(false)
    }

    pub fn read_metadata(&self, object_id: &str) -> SpoolResult<Vec<u8>> {
        let paths = self.layout.committed_paths(object_id)?;
        if !paths.metadata.exists() || !paths.payload.exists() {
            return Err(SpoolError::NotFound(object_id.to_string()));
        }
        fs::read(paths.metadata).map_err(Into::into)
    }

    pub fn read_payload(&self, object_id: &str) -> SpoolResult<Vec<u8>> {
        let paths = self.layout.committed_paths(object_id)?;
        if !paths.metadata.exists() || !paths.payload.exists() {
            return Err(SpoolError::NotFound(object_id.to_string()));
        }
        fs::read(paths.payload).map_err(Into::into)
    }

    pub fn get_object_paths(&self, object_id: &str) -> SpoolResult<CommittedObjectPaths> {
        self.layout.committed_paths(object_id)
    }
}

fn read_manifest(paths: &StagingPaths) -> SpoolResult<ChunkManifest> {
    Ok(serde_json::from_slice(&fs::read(&paths.manifest)?)?)
}

pub(crate) fn write_file_sync(path: &Path, bytes: &[u8]) -> SpoolResult<()> {
    let mut file = File::create(path)?;
    file.write_all(bytes)?;
    file.sync_all()?;
    Ok(())
}

pub(crate) fn write_json_sync<T: serde::Serialize>(path: &Path, value: &T) -> SpoolResult<()> {
    let bytes = serde_json::to_vec_pretty(value)?;
    write_file_sync(path, &bytes)
}

pub(crate) fn fsync_dir(path: &Path) -> SpoolResult<()> {
    let dir = File::open(path)?;
    dir.sync_all()?;
    Ok(())
}

use super::{CommittedObjectPaths, SpoolError, SpoolResult};
use crate::cache::hash::OBJECT_ID_PREFIX;
use std::path::{Path, PathBuf};

#[derive(Debug, Clone)]
pub struct SpoolLayout {
    root: PathBuf,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct StagingPaths {
    pub transfer_dir: PathBuf,
    pub metadata: PathBuf,
    pub chunks_dir: PathBuf,
    pub manifest: PathBuf,
}

impl StagingPaths {
    pub fn chunk_path(&self, chunk_index: u64) -> PathBuf {
        self.chunks_dir.join(format!("{chunk_index}.chunk"))
    }
}

impl SpoolLayout {
    pub fn new(root: impl Into<PathBuf>) -> Self {
        Self { root: root.into() }
    }

    pub fn root(&self) -> &Path {
        &self.root
    }

    pub fn staging_paths(&self, transfer_id: &str) -> SpoolResult<StagingPaths> {
        let safe_transfer_id = sanitize_transfer_id(transfer_id)?;
        let transfer_dir = self.root.join("staging").join(safe_transfer_id);
        Ok(StagingPaths {
            metadata: transfer_dir.join("meta.json"),
            chunks_dir: transfer_dir.join("chunks"),
            manifest: transfer_dir.join("manifest.json"),
            transfer_dir,
        })
    }

    pub fn committed_paths(&self, object_id: &str) -> SpoolResult<CommittedObjectPaths> {
        let sanitized = sanitize_object_id(object_id)?;
        let first = &sanitized[0..2];
        let next = &sanitized[2..4];
        let dir = self.root.join("objects").join(first).join(next);
        Ok(CommittedObjectPaths {
            metadata: dir.join(format!("{sanitized}.meta.json")),
            payload: dir.join(format!("{sanitized}.payload.bin")),
        })
    }
}

pub fn sanitize_object_id(object_id: &str) -> SpoolResult<String> {
    let suffix = object_id
        .strip_prefix(OBJECT_ID_PREFIX)
        .ok_or_else(|| SpoolError::InvalidObjectId(object_id.to_string()))?;
    if suffix.len() != 64 || !suffix.bytes().all(|byte| byte.is_ascii_hexdigit()) {
        return Err(SpoolError::InvalidObjectId(object_id.to_string()));
    }
    Ok(suffix.to_ascii_lowercase())
}

pub fn sanitize_transfer_id(transfer_id: &str) -> SpoolResult<String> {
    if transfer_id.is_empty()
        || transfer_id == "."
        || transfer_id == ".."
        || transfer_id.contains("..")
        || !transfer_id
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'_' | b'-' | b'.'))
    {
        return Err(SpoolError::InvalidTransferId(transfer_id.to_string()));
    }
    Ok(transfer_id.to_string())
}

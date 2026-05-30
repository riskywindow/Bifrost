use crate::cache::hash::OBJECT_ID_PREFIX;
use crate::store::errors::{StoreError, StoreResult};
use std::path::{Path, PathBuf};

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct StorePaths {
    pub catalog: PathBuf,
    pub objects_dir: PathBuf,
    pub staging_dir: PathBuf,
    pub quarantine_dir: PathBuf,
}

#[derive(Debug, Clone)]
pub struct StoreLayout {
    root: PathBuf,
}

impl StoreLayout {
    pub fn new(root: impl Into<PathBuf>) -> Self {
        Self { root: root.into() }
    }

    pub fn root(&self) -> &Path {
        &self.root
    }

    pub fn paths(&self) -> StorePaths {
        StorePaths {
            catalog: self.root.join("catalog.sqlite"),
            objects_dir: self.root.join("objects"),
            staging_dir: self.root.join("staging"),
            quarantine_dir: self.root.join("quarantine"),
        }
    }

    pub fn object_dir(&self, object_id: &str) -> StoreResult<PathBuf> {
        let sanitized = sanitize_object_id(object_id)?;
        Ok(self
            .root
            .join("objects")
            .join(&sanitized[0..2])
            .join(&sanitized[2..4]))
    }

    pub fn meta_path(&self, object_id: &str) -> StoreResult<PathBuf> {
        let sanitized = sanitize_object_id(object_id)?;
        Ok(self
            .object_dir(object_id)?
            .join(format!("{sanitized}.meta.json")))
    }

    pub fn payload_path(&self, object_id: &str) -> StoreResult<PathBuf> {
        let sanitized = sanitize_object_id(object_id)?;
        Ok(self
            .object_dir(object_id)?
            .join(format!("{sanitized}.payload.bin")))
    }

    pub fn staging_transfer_dir(&self, transfer_id: &str) -> StoreResult<PathBuf> {
        Ok(self
            .root
            .join("staging")
            .join(sanitize_transfer_id(transfer_id)?))
    }
}

pub fn sanitize_object_id(object_id: &str) -> StoreResult<String> {
    let suffix = object_id
        .strip_prefix(OBJECT_ID_PREFIX)
        .ok_or_else(|| StoreError::Integrity(format!("invalid object id: {object_id}")))?;
    if suffix.len() != 64 || !suffix.bytes().all(|byte| byte.is_ascii_hexdigit()) {
        return Err(StoreError::Integrity(format!(
            "invalid object id: {object_id}"
        )));
    }
    Ok(suffix.to_ascii_lowercase())
}

pub fn sanitize_transfer_id(transfer_id: &str) -> StoreResult<String> {
    if transfer_id.is_empty()
        || transfer_id == "."
        || transfer_id == ".."
        || transfer_id.contains("..")
        || !transfer_id
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'_' | b'-' | b'.'))
    {
        return Err(StoreError::Filesystem(std::io::Error::new(
            std::io::ErrorKind::InvalidInput,
            format!("invalid transfer id: {transfer_id}"),
        )));
    }
    Ok(transfer_id.to_string())
}

use crate::cache::validate_object;
use crate::store::errors::{StoreError, StoreResult};
use crate::store::locations::StoreLayout;
use crate::store::object_record::ObjectLocation;
use std::fs;
use std::path::PathBuf;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct StoredObject {
    pub object_id: String,
    pub metadata: Vec<u8>,
    pub payload: Vec<u8>,
}

#[derive(Debug, Clone)]
pub struct DiskTier {
    layout: StoreLayout,
}

impl DiskTier {
    pub fn new(root: impl Into<PathBuf>) -> Self {
        Self {
            layout: StoreLayout::new(root),
        }
    }

    pub fn layout(&self) -> &StoreLayout {
        &self.layout
    }

    pub fn location_for(&self, object_id: &str, bytes_on_disk: i64) -> StoreResult<ObjectLocation> {
        Ok(ObjectLocation {
            object_id: object_id.to_string(),
            tier: "disk".to_string(),
            meta_path: self
                .layout
                .meta_path(object_id)?
                .to_string_lossy()
                .into_owned(),
            payload_path: self
                .layout
                .payload_path(object_id)?
                .to_string_lossy()
                .into_owned(),
            bytes_on_disk,
        })
    }

    pub fn has_files(&self, object_id: &str) -> StoreResult<bool> {
        Ok(self.layout.meta_path(object_id)?.exists()
            && self.layout.payload_path(object_id)?.exists())
    }

    pub fn read_validated(&self, object_id: &str) -> StoreResult<StoredObject> {
        let metadata_path = self.layout.meta_path(object_id)?;
        let payload_path = self.layout.payload_path(object_id)?;
        if !metadata_path.exists() || !payload_path.exists() {
            return Err(StoreError::NotFound(object_id.to_string()));
        }

        let metadata = fs::read(metadata_path)?;
        let metadata_json = serde_json::from_slice(&metadata)?;
        let payload = fs::read(payload_path)?;
        let validation = validate_object(&metadata_json, &payload, None);
        if validation.status != "accepted" {
            return Err(StoreError::Integrity(validation.reason_code));
        }
        if validation.object_id.as_deref() != Some(object_id) {
            return Err(StoreError::Integrity("object_id_mismatch".to_string()));
        }
        Ok(StoredObject {
            object_id: object_id.to_string(),
            metadata,
            payload,
        })
    }

    pub fn remove_files(&self, object_id: &str) -> StoreResult<()> {
        let metadata_path = self.layout.meta_path(object_id)?;
        let payload_path = self.layout.payload_path(object_id)?;
        if metadata_path.exists() {
            fs::remove_file(metadata_path)?;
        }
        if payload_path.exists() {
            fs::remove_file(payload_path)?;
        }
        Ok(())
    }
}

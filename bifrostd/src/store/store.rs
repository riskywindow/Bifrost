use crate::cache::object_meta::BifrostKvObjectDescriptor;
use crate::cache::validate::{validate_object, ValidationResult};
use crate::spool::committed::{atomic_commit, CommittedObjectPaths};
use crate::spool::layout::SpoolLayout;
use crate::spool::staging;
use crate::spool::{Spool, SpoolError};
use crate::store::disk_tier::{DiskTier, StoredObject};
use crate::store::errors::{StoreError, StoreResult};
use crate::store::lifecycle::can_serve;
use crate::store::locations::StoreLayout;
use crate::store::object_record::{
    ObjectCompatibility, ObjectListFilter, ObjectLocation, ObjectRecord, ObjectState,
};
use crate::store::{open_catalog, StoreStats};
use crate::transport::ChunkManifest;
use serde_json::Value;
use std::fs;
use std::path::PathBuf;
use std::sync::{Arc, Mutex};
use std::time::{SystemTime, UNIX_EPOCH};

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct StagingHandle {
    pub transfer_id: String,
    pub metadata_path: PathBuf,
    pub manifest_path: PathBuf,
    pub chunks_dir: PathBuf,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ObjectInspection {
    pub record: ObjectRecord,
    pub location: ObjectLocation,
    pub compatibility: ObjectCompatibility,
    pub files_present: bool,
    pub servable: bool,
}

#[derive(Debug, Clone)]
pub struct Store {
    root: PathBuf,
    staging_lock: Arc<Mutex<()>>,
}

impl Store {
    pub fn open(root: PathBuf) -> StoreResult<Self> {
        let layout = StoreLayout::new(&root);
        let paths = layout.paths();
        fs::create_dir_all(&paths.objects_dir)?;
        fs::create_dir_all(&paths.staging_dir)?;
        fs::create_dir_all(&paths.quarantine_dir)?;
        open_catalog(&paths.catalog)?;
        Ok(Self {
            root,
            staging_lock: Arc::new(Mutex::new(())),
        })
    }

    pub fn root(&self) -> &PathBuf {
        &self.root
    }

    pub fn begin_put(
        &self,
        transfer_id: &str,
        metadata_json: &[u8],
        manifest: &ChunkManifest,
    ) -> StoreResult<StagingHandle> {
        let _guard = self
            .staging_lock
            .lock()
            .expect("store staging lock poisoned");
        let spool = Spool::new(&self.root);
        spool
            .create_staging_transfer(transfer_id, metadata_json, manifest)
            .map_err(store_error_from_spool)?;
        let paths = SpoolLayout::new(&self.root)
            .staging_paths(transfer_id)
            .map_err(store_error_from_spool)?;
        Ok(StagingHandle {
            transfer_id: transfer_id.to_string(),
            metadata_path: paths.metadata,
            manifest_path: paths.manifest,
            chunks_dir: paths.chunks_dir,
        })
    }

    pub fn write_chunk(
        &self,
        transfer_id: &str,
        chunk_index: u64,
        bytes: &[u8],
    ) -> StoreResult<()> {
        let _guard = self
            .staging_lock
            .lock()
            .expect("store staging lock poisoned");
        Spool::new(&self.root)
            .write_chunk(transfer_id, chunk_index, bytes)
            .map_err(store_error_from_spool)
    }

    pub fn commit_put(
        &self,
        transfer_id: &str,
        target_profile: Option<&Value>,
    ) -> StoreResult<ObjectRecord> {
        let _guard = self
            .staging_lock
            .lock()
            .expect("store staging lock poisoned");
        let spool_layout = SpoolLayout::new(&self.root);
        let staging_paths = spool_layout
            .staging_paths(transfer_id)
            .map_err(store_error_from_spool)?;
        let metadata_bytes = fs::read(&staging_paths.metadata)?;
        let metadata: Value = serde_json::from_slice(&metadata_bytes)?;
        let staged =
            staging::assemble_staged_payload(&staging_paths).map_err(store_error_from_spool)?;
        let validation = validate_object(&metadata, &staged.bytes, target_profile);
        if validation.status != "accepted" {
            return Err(StoreError::Integrity(validation.reason_code));
        }
        let object_id = validation
            .object_id
            .clone()
            .ok_or_else(|| StoreError::Integrity("missing_object_id".to_string()))?;
        let descriptor: BifrostKvObjectDescriptor = serde_json::from_value(metadata.clone())?;
        if descriptor.object_id != object_id {
            return Err(StoreError::Integrity("object_id_mismatch".to_string()));
        }
        if staged
            .manifest
            .object_id
            .as_deref()
            .is_some_and(|manifest_object_id| manifest_object_id != object_id)
        {
            return Err(StoreError::Integrity(
                "manifest_object_id_mismatch".to_string(),
            ));
        }

        let committed = spool_layout
            .committed_paths(&object_id)
            .map_err(store_error_from_spool)?;
        if committed.metadata.exists() || committed.payload.exists() {
            if committed.metadata.exists()
                && committed.payload.exists()
                && fs::read(&committed.metadata)? == metadata_bytes
                && fs::read(&committed.payload)? == staged.bytes
            {
                let record = self.ensure_catalog_record(
                    &descriptor,
                    &validation,
                    &committed,
                    &metadata_bytes,
                    &staged.bytes,
                )?;
                fs::remove_dir_all(staging_paths.transfer_dir)?;
                return Ok(record);
            }
            return Err(StoreError::Integrity(format!(
                "object already exists with conflicting bytes: {object_id}"
            )));
        }

        atomic_commit(&committed, &metadata_bytes, &staged.bytes)
            .map_err(store_error_from_spool)?;
        match self.ensure_catalog_record(
            &descriptor,
            &validation,
            &committed,
            &metadata_bytes,
            &staged.bytes,
        ) {
            Ok(record) => {
                fs::remove_dir_all(staging_paths.transfer_dir)?;
                Ok(record)
            }
            Err(error) => {
                // Fail closed: if the catalog write fails after atomic file commit, remove the
                // committed files so the object cannot become available without a catalog row.
                let _ = DiskTier::new(&self.root).remove_files(&object_id);
                Err(error)
            }
        }
    }

    pub fn abort_put(&self, transfer_id: &str) -> StoreResult<()> {
        let _guard = self
            .staging_lock
            .lock()
            .expect("store staging lock poisoned");
        Spool::new(&self.root)
            .abort_staging_transfer(transfer_id)
            .map_err(store_error_from_spool)
    }

    pub fn has_object(&self, object_id: &str) -> StoreResult<bool> {
        Ok(self
            .inspect_object(object_id)
            .map(|inspection| inspection.servable)
            .unwrap_or(false))
    }

    pub fn get_object(&self, object_id: &str) -> StoreResult<StoredObject> {
        self.ensure_servable(object_id)?;
        let object = DiskTier::new(&self.root).read_validated(object_id)?;
        let mut catalog = self.open_catalog()?;
        catalog.update_access_on_get(
            object_id,
            (object.metadata.len() + object.payload.len()) as i64,
        )?;
        Ok(object)
    }

    pub fn get_metadata(&self, object_id: &str) -> StoreResult<Vec<u8>> {
        self.ensure_servable(object_id)?;
        let object = DiskTier::new(&self.root).read_validated(object_id)?;
        let mut catalog = self.open_catalog()?;
        catalog.update_access_on_get(object_id, object.metadata.len() as i64)?;
        Ok(object.metadata)
    }

    pub fn get_payload(&self, object_id: &str) -> StoreResult<Vec<u8>> {
        self.ensure_servable(object_id)?;
        let object = DiskTier::new(&self.root).read_validated(object_id)?;
        let mut catalog = self.open_catalog()?;
        catalog.update_access_on_get(object_id, object.payload.len() as i64)?;
        Ok(object.payload)
    }

    pub fn inspect_object(&self, object_id: &str) -> StoreResult<ObjectInspection> {
        let catalog = self.open_catalog()?;
        let record = catalog
            .get_object_record(object_id)?
            .ok_or_else(|| StoreError::NotFound(object_id.to_string()))?;
        let location = catalog
            .get_object_location(object_id, "disk")?
            .ok_or_else(|| StoreError::NotFound(object_id.to_string()))?;
        let compatibility = catalog
            .get_object_compatibility(object_id)?
            .ok_or_else(|| StoreError::NotFound(object_id.to_string()))?;
        let disk = DiskTier::new(&self.root);
        let expected_location = disk.location_for(object_id, location.bytes_on_disk)?;
        let files_present = disk.has_files(object_id)?
            && location.meta_path == expected_location.meta_path
            && location.payload_path == expected_location.payload_path
            && disk.read_validated(object_id).is_ok();
        let servable = files_present
            && can_serve(record.state, record.pin_count)
            && record.state != ObjectState::Committed;
        Ok(ObjectInspection {
            record,
            location,
            compatibility,
            files_present,
            servable,
        })
    }

    pub fn list_objects(&self, filter: &ObjectListFilter) -> StoreResult<Vec<ObjectRecord>> {
        self.open_catalog()?.list_objects(filter)
    }

    pub fn stats(&self) -> StoreResult<StoreStats> {
        self.open_catalog()?.store_stats()
    }

    fn ensure_servable(&self, object_id: &str) -> StoreResult<()> {
        if self.inspect_object(object_id)?.servable {
            Ok(())
        } else {
            Err(StoreError::NotFound(object_id.to_string()))
        }
    }

    fn ensure_catalog_record(
        &self,
        descriptor: &BifrostKvObjectDescriptor,
        validation: &ValidationResult,
        committed: &CommittedObjectPaths,
        metadata_bytes: &[u8],
        payload: &[u8],
    ) -> StoreResult<ObjectRecord> {
        let object_id = validation
            .object_id
            .as_deref()
            .ok_or_else(|| StoreError::Integrity("missing_object_id".to_string()))?;
        let mut catalog = self.open_catalog()?;
        if let Some(existing) = catalog.get_object_record(object_id)? {
            if self.has_catalog_consistent_files(object_id)? {
                catalog.update_access_on_put(object_id, payload.len() as i64)?;
                return Ok(existing);
            }
            return Err(StoreError::Integrity(format!(
                "catalog row exists but files are not servable: {object_id}"
            )));
        }

        let now = now_unix_ms();
        let record = ObjectRecord {
            object_id: object_id.to_string(),
            object_type: descriptor.object_type.clone(),
            schema_version: descriptor.schema_version.clone(),
            descriptor_hash: validation
                .descriptor_hash
                .clone()
                .ok_or_else(|| StoreError::Integrity("missing_descriptor_hash".to_string()))?,
            payload_hash: validation
                .payload_hash
                .clone()
                .ok_or_else(|| StoreError::Integrity("missing_payload_hash".to_string()))?,
            byte_length: payload.len() as i64,
            state: ObjectState::Committed,
            created_at_unix_ms: descriptor.created_at_unix_ms as i64,
            committed_at_unix_ms: Some(now),
            verified_at_unix_ms: None,
            last_accessed_unix_ms: None,
            access_count: 0,
            pin_count: 0,
            ttl_expires_at_unix_ms: None,
            quarantine_reason: None,
        };
        let location = ObjectLocation {
            object_id: object_id.to_string(),
            tier: "disk".to_string(),
            meta_path: committed.metadata.to_string_lossy().into_owned(),
            payload_path: committed.payload.to_string_lossy().into_owned(),
            bytes_on_disk: (metadata_bytes.len() + payload.len()) as i64,
        };
        let compatibility = compatibility_from_descriptor(descriptor)?;
        catalog.insert_committed_object(&record, &location, &compatibility)?;
        catalog.transition_object_state(object_id, ObjectState::Verified, None)?;
        catalog.update_access_on_put(object_id, payload.len() as i64)?;
        catalog
            .get_object_record(object_id)?
            .ok_or_else(|| StoreError::NotFound(object_id.to_string()))
    }

    fn has_catalog_consistent_files(&self, object_id: &str) -> StoreResult<bool> {
        Ok(self
            .inspect_object(object_id)
            .map(|inspection| inspection.files_present)
            .unwrap_or(false))
    }

    fn open_catalog(&self) -> StoreResult<crate::store::Catalog> {
        open_catalog(&StoreLayout::new(&self.root).paths().catalog)
    }
}

impl From<Spool> for Store {
    fn from(spool: Spool) -> Self {
        Self {
            root: spool.root().to_path_buf(),
            staging_lock: spool.staging_lock(),
        }
    }
}

fn compatibility_from_descriptor(
    descriptor: &BifrostKvObjectDescriptor,
) -> StoreResult<ObjectCompatibility> {
    let engine = &descriptor.engine_profile;
    let mut compatibility = ObjectCompatibility {
        object_id: descriptor.object_id.clone(),
        model_hash: None,
        tokenizer_hash: None,
        config_hash: None,
        rope_config_hash: None,
        dtype: None,
        engine_name: Some(engine.engine_name.clone()),
        engine_version: Some(engine.engine_version.clone()),
        integration_name: Some(engine.integration_name.clone()),
        kv_cache_format: Some(engine.kv_cache_format.clone()),
        prefix_hash: None,
        token_range_start: None,
        token_range_end: None,
        layer_id: None,
        kv_block_id: None,
        opaque_engine_key_hash: None,
    };

    if descriptor.object_type == "native_kv_page" {
        let prefix = descriptor
            .prefix_profile
            .as_ref()
            .ok_or_else(|| StoreError::Compatibility("missing prefix_profile".to_string()))?;
        let native = descriptor.native_tensor_profile.as_ref().ok_or_else(|| {
            StoreError::Compatibility("missing native_tensor_profile".to_string())
        })?;
        compatibility.model_hash = Some(descriptor.model_profile.model_hash.clone());
        compatibility.tokenizer_hash = Some(descriptor.model_profile.tokenizer_hash.clone());
        compatibility.config_hash = Some(descriptor.model_profile.config_hash.clone());
        compatibility.rope_config_hash = Some(descriptor.model_profile.rope_config_hash.clone());
        compatibility.dtype = Some(descriptor.model_profile.dtype.clone());
        compatibility.prefix_hash = Some(prefix.prefix_hash.clone());
        compatibility.token_range_start =
            Some(u64_to_i64(native.token_range.start, "token_range.start")?);
        compatibility.token_range_end =
            Some(u64_to_i64(native.token_range.end, "token_range.end")?);
        compatibility.layer_id = Some(u64_to_i64(native.layer_id, "layer_id")?);
        compatibility.kv_block_id = Some(u64_to_i64(native.kv_block_id, "kv_block_id")?);
    } else if descriptor.object_type == "opaque_engine_blob" {
        let opaque = descriptor.opaque_engine_profile.as_ref().ok_or_else(|| {
            StoreError::Compatibility("missing opaque_engine_profile".to_string())
        })?;
        compatibility.opaque_engine_key_hash = Some(opaque.engine_key_hash.clone());
    } else {
        return Err(StoreError::Compatibility(format!(
            "unsupported object type: {}",
            descriptor.object_type
        )));
    }

    Ok(compatibility)
}

fn u64_to_i64(value: u64, field: &str) -> StoreResult<i64> {
    i64::try_from(value)
        .map_err(|_| StoreError::Compatibility(format!("{field} does not fit in i64")))
}

fn store_error_from_spool(error: SpoolError) -> StoreError {
    match error {
        SpoolError::Io(error) => StoreError::Filesystem(error),
        SpoolError::Json(error) => StoreError::Json(error),
        SpoolError::Transport(error) => StoreError::Transport(error),
        SpoolError::InvalidObjectId(value) => {
            StoreError::Integrity(format!("invalid object id: {value}"))
        }
        SpoolError::InvalidTransferId(value) => StoreError::Filesystem(std::io::Error::new(
            std::io::ErrorKind::InvalidInput,
            format!("invalid transfer id: {value}"),
        )),
        SpoolError::TransferAlreadyExists(value) => {
            StoreError::InvalidState(format!("transfer already exists: {value}"))
        }
        SpoolError::AlreadyExists(value) => {
            StoreError::Integrity(format!("object already exists: {value}"))
        }
        SpoolError::NotFound(value) => StoreError::NotFound(value),
        SpoolError::ValidationRejected(value) => StoreError::Integrity(value),
    }
}

fn now_unix_ms() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("system clock is before unix epoch")
        .as_millis() as i64
}

use crate::cache::object_meta::BifrostKvObjectDescriptor;
use crate::cache::validate::{validate_object, ValidationResult};
use crate::spool::committed::{atomic_commit, CommittedObjectPaths};
use crate::spool::layout::SpoolLayout;
use crate::spool::staging;
use crate::spool::{Spool, SpoolError};
use crate::store::disk_tier::{DiskTier, StoredObject};
use crate::store::errors::{StoreError, StoreResult};
use crate::store::eviction::{EvictedObject, EvictionFailure, EvictionReport, EvictionRequest};
use crate::store::fsck::{run_fsck, FsckMode, FsckResult};
use crate::store::lifecycle::can_serve;
use crate::store::locations::StoreLayout;
use crate::store::manifest::{
    CompletenessState, ManifestCompletenessReport, ManifestExpectedCoverage,
    ManifestExpectedMember, ManifestInspection, ManifestListFilter, ManifestMember, ManifestRecord,
    ManifestType, MissingManifestMember, MANIFEST_ID_PREFIX,
};
use crate::store::memory_tier::{MemoryTier, MemoryTierConfig};
use crate::store::object_record::{
    ObjectCompatibility, ObjectListFilter, ObjectLocation, ObjectRecord, ObjectState,
    OpaqueKeyListFilter, OpaqueKeyRecord,
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
    memory_tier: Arc<MemoryTier>,
}

impl Store {
    pub fn open(root: PathBuf) -> StoreResult<Self> {
        Self::open_with_memory_tier(root, MemoryTierConfig::disabled())
    }

    pub fn open_with_memory_tier(
        root: PathBuf,
        memory_tier_config: MemoryTierConfig,
    ) -> StoreResult<Self> {
        let layout = StoreLayout::new(&root);
        let paths = layout.paths();
        fs::create_dir_all(&paths.objects_dir)?;
        fs::create_dir_all(&paths.staging_dir)?;
        fs::create_dir_all(&paths.quarantine_dir)?;
        open_catalog(&paths.catalog)?;
        Ok(Self {
            root,
            staging_lock: Arc::new(Mutex::new(())),
            memory_tier: Arc::new(MemoryTier::new(memory_tier_config)),
        })
    }

    pub fn root(&self) -> &PathBuf {
        &self.root
    }

    pub(crate) fn invalidate_memory_tier(&self, object_id: &str) {
        self.memory_tier.invalidate(object_id);
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
        if let Some(object) = self.memory_tier.get_object(object_id) {
            let mut catalog = self.open_catalog()?;
            catalog.update_access_on_get(
                object_id,
                (object.metadata.len() + object.payload.len()) as i64,
            )?;
            return Ok(object);
        }
        let object = DiskTier::new(&self.root).read_validated(object_id)?;
        let mut catalog = self.open_catalog()?;
        catalog.update_access_on_get(
            object_id,
            (object.metadata.len() + object.payload.len()) as i64,
        )?;
        self.memory_tier
            .insert(object_id, &object.metadata, Some(&object.payload));
        Ok(object)
    }

    pub fn get_metadata(&self, object_id: &str) -> StoreResult<Vec<u8>> {
        self.ensure_servable(object_id)?;
        if let Some(metadata) = self.memory_tier.get_metadata(object_id) {
            let mut catalog = self.open_catalog()?;
            catalog.update_access_on_get(object_id, metadata.len() as i64)?;
            return Ok(metadata);
        }
        let object = DiskTier::new(&self.root).read_validated(object_id)?;
        let mut catalog = self.open_catalog()?;
        catalog.update_access_on_get(object_id, object.metadata.len() as i64)?;
        self.memory_tier.insert(object_id, &object.metadata, None);
        Ok(object.metadata)
    }

    pub fn get_payload(&self, object_id: &str) -> StoreResult<Vec<u8>> {
        self.ensure_servable(object_id)?;
        if let Some(payload) = self.memory_tier.get_payload(object_id) {
            let mut catalog = self.open_catalog()?;
            catalog.update_access_on_get(object_id, payload.len() as i64)?;
            return Ok(payload);
        }
        let object = DiskTier::new(&self.root).read_validated(object_id)?;
        let mut catalog = self.open_catalog()?;
        catalog.update_access_on_get(object_id, object.payload.len() as i64)?;
        self.memory_tier
            .insert(object_id, &object.metadata, Some(&object.payload));
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

    pub fn get_object_by_opaque_key(
        &self,
        engine_name: &str,
        integration_name: &str,
        opaque_engine_key_hash: &str,
    ) -> StoreResult<Option<ObjectInspection>> {
        let catalog = self.open_catalog()?;
        let Some(index) = catalog.get_object_by_opaque_key(
            engine_name,
            integration_name,
            opaque_engine_key_hash,
        )?
        else {
            return Ok(None);
        };
        drop(catalog);

        let inspection = match self.inspect_object(&index.object_id) {
            Ok(inspection) => inspection,
            Err(StoreError::NotFound(_)) => return Ok(None),
            Err(error) => return Err(error),
        };
        if !inspection.servable {
            return Ok(None);
        }
        if inspection.compatibility.engine_name.as_deref() != Some(engine_name)
            || inspection.compatibility.integration_name.as_deref() != Some(integration_name)
            || inspection.compatibility.opaque_engine_key_hash.as_deref()
                != Some(opaque_engine_key_hash)
        {
            return Ok(None);
        }
        let mut catalog = self.open_catalog()?;
        catalog.update_opaque_key_access(engine_name, integration_name, opaque_engine_key_hash)?;
        Ok(Some(inspection))
    }

    pub fn list_opaque_keys(
        &self,
        filter: &OpaqueKeyListFilter,
    ) -> StoreResult<Vec<OpaqueKeyRecord>> {
        let catalog = self.open_catalog()?;
        let entries = catalog.list_opaque_keys(filter)?;
        drop(catalog);

        let mut result = Vec::with_capacity(entries.len());
        for mut entry in entries {
            entry.serveable = self
                .inspect_object(&entry.object_id)
                .map(|inspection| inspection.servable)
                .unwrap_or(false);
            result.push(entry);
        }
        Ok(result)
    }

    pub fn stats(&self) -> StoreResult<StoreStats> {
        let mut stats = self.open_catalog()?.store_stats()?;
        let memory_stats = self.memory_tier.stats();
        stats.memory_tier_enabled = memory_stats.enabled;
        stats.memory_tier_bytes = memory_stats.bytes;
        stats.memory_tier_capacity_bytes = memory_stats.capacity_bytes;
        stats.memory_tier_hits = memory_stats.hits;
        stats.memory_tier_misses = memory_stats.misses;
        stats.memory_tier_evictions = memory_stats.evictions;
        Ok(stats)
    }

    pub fn fsck(&self, mode: FsckMode) -> StoreResult<FsckResult> {
        run_fsck(self, mode)
    }

    pub fn evict(&self, request: EvictionRequest) -> StoreResult<EvictionReport> {
        let catalog = self.open_catalog()?;
        let starting_bytes_on_disk = catalog.store_stats()?.total_bytes_on_disk;
        let mut report = EvictionReport::empty(&request, starting_bytes_on_disk);
        let (protected_pinned_count, skipped_unsafe_count) = catalog.eviction_skip_counts()?;
        report.protected_pinned_count = protected_pinned_count;
        report.skipped_unsafe_count = skipped_unsafe_count;

        if request
            .target_bytes
            .map(|target| starting_bytes_on_disk <= target)
            .unwrap_or(false)
        {
            report.reason = "target_already_reached".to_string();
            return Ok(report);
        }

        let all_candidates = catalog.eviction_candidates(request.policy, request.now_unix_ms)?;
        drop(catalog);

        let selected = select_eviction_candidates(
            all_candidates,
            starting_bytes_on_disk,
            request.target_bytes,
            request.max_objects,
        );
        report.planned_bytes = selected
            .iter()
            .map(|candidate| candidate.bytes_on_disk)
            .sum();
        report.candidates = selected;

        if request.dry_run {
            report.final_bytes_on_disk = starting_bytes_on_disk;
            report.target_reached = request
                .target_bytes
                .map(|target| starting_bytes_on_disk - report.planned_bytes <= target)
                .unwrap_or(false);
            report.reason = if report.candidates.is_empty() {
                "no_eligible_candidates".to_string()
            } else {
                "dry_run".to_string()
            };
            return Ok(report);
        }

        let disk = DiskTier::new(&self.root);
        for candidate in report.candidates.clone() {
            let object_id = candidate.object_id;
            if !disk.has_files(&object_id)? {
                self.memory_tier.invalidate(&object_id);
                let reason = "catalog_location_missing_files";
                let mut catalog = self.open_catalog()?;
                catalog.mark_missing_after_eviction_failure(&object_id, reason)?;
                report.failures.push(EvictionFailure {
                    object_id,
                    reason: reason.to_string(),
                });
                continue;
            }

            let mut catalog = self.open_catalog()?;
            if let Err(error) = catalog.begin_eviction(&object_id) {
                report.failures.push(EvictionFailure {
                    object_id,
                    reason: error.to_string(),
                });
                continue;
            }
            self.memory_tier.invalidate(&object_id);
            drop(catalog);

            match disk.remove_files(&object_id) {
                Ok(()) => {
                    let mut catalog = self.open_catalog()?;
                    catalog.finish_eviction(&object_id, candidate.bytes_on_disk)?;
                    report.freed_bytes += candidate.bytes_on_disk;
                    report.evicted.push(EvictedObject {
                        object_id,
                        bytes_freed: candidate.bytes_on_disk,
                    });
                }
                Err(error) => {
                    self.memory_tier.invalidate(&object_id);
                    let reason = error.to_string();
                    let mut catalog = self.open_catalog()?;
                    catalog.mark_missing_after_eviction_failure(&object_id, &reason)?;
                    report.failures.push(EvictionFailure { object_id, reason });
                }
            }
        }

        report.final_bytes_on_disk = self.open_catalog()?.store_stats()?.total_bytes_on_disk;
        report.target_reached = request
            .target_bytes
            .map(|target| report.final_bytes_on_disk <= target)
            .unwrap_or(false);
        report.reason = if !report.failures.is_empty() {
            "eviction_failed".to_string()
        } else if report.candidates.is_empty() {
            "no_eligible_candidates".to_string()
        } else if request.target_bytes.is_some() && !report.target_reached {
            "target_not_reached".to_string()
        } else {
            "ok".to_string()
        };
        Ok(report)
    }

    pub fn pin_object(&self, object_id: &str) -> StoreResult<()> {
        let mut catalog = self.open_catalog()?;
        let result = catalog.increment_pin(object_id);
        if result.is_ok() {
            self.memory_tier.invalidate(object_id);
        }
        result
    }

    pub fn unpin_object(&self, object_id: &str) -> StoreResult<()> {
        let mut catalog = self.open_catalog()?;
        let result = catalog.decrement_pin(object_id);
        if result.is_ok() {
            self.memory_tier.invalidate(object_id);
        }
        result
    }

    pub fn set_ttl(&self, object_id: &str, expires_at_unix_ms: i64) -> StoreResult<()> {
        let mut catalog = self.open_catalog()?;
        let result = catalog.set_ttl(object_id, expires_at_unix_ms);
        if result.is_ok() {
            self.memory_tier.invalidate(object_id);
        }
        result
    }

    pub fn clear_ttl(&self, object_id: &str) -> StoreResult<()> {
        let mut catalog = self.open_catalog()?;
        let result = catalog.clear_ttl(object_id);
        if result.is_ok() {
            self.memory_tier.invalidate(object_id);
        }
        result
    }

    pub fn create_prefix_manifest(
        &self,
        model_hash: Option<String>,
        tokenizer_hash: Option<String>,
        rope_config_hash: Option<String>,
        prefix_hash: String,
        token_range_start: i64,
        token_range_end: i64,
    ) -> StoreResult<ManifestRecord> {
        if prefix_hash.is_empty() {
            return Err(StoreError::Manifest("prefix_hash is required".to_string()));
        }
        if token_range_start < 0 || token_range_end < token_range_start {
            return Err(StoreError::Manifest(
                "invalid manifest token range".to_string(),
            ));
        }
        let now = now_unix_ms();
        let manifest_id = compute_manifest_id(
            ManifestType::PrefixManifest,
            model_hash.as_deref(),
            tokenizer_hash.as_deref(),
            rope_config_hash.as_deref(),
            &prefix_hash,
            token_range_start,
            token_range_end,
        );
        let manifest = ManifestRecord {
            manifest_id: manifest_id.clone(),
            manifest_type: ManifestType::PrefixManifest,
            model_hash,
            tokenizer_hash,
            rope_config_hash,
            prefix_hash,
            token_range_start,
            token_range_end,
            completeness_state: CompletenessState::Unknown,
            created_at_unix_ms: now,
            updated_at_unix_ms: now,
            pin_count: 0,
        };
        let mut catalog = self.open_catalog()?;
        if let Some(existing) = catalog.get_manifest(&manifest_id)? {
            return Ok(existing);
        }
        catalog.create_manifest(&manifest)?;
        Ok(manifest)
    }

    pub fn add_manifest_member(
        &self,
        manifest_id: &str,
        object_id: &str,
        required: bool,
    ) -> StoreResult<ManifestMember> {
        let inspection = self.inspect_object(object_id)?;
        if !inspection.servable {
            return Err(StoreError::Manifest(format!(
                "manifest member is not serveable: {object_id}"
            )));
        }

        let catalog = self.open_catalog()?;
        let manifest = catalog
            .get_manifest(manifest_id)?
            .ok_or_else(|| StoreError::NotFound(manifest_id.to_string()))?;
        let compatibility = inspection.compatibility.clone();
        let record = inspection.record.clone();
        validate_member_identity(&manifest, &compatibility)?;
        drop(catalog);

        let member = ManifestMember {
            manifest_id: manifest_id.to_string(),
            object_id: object_id.to_string(),
            layer_id: compatibility.layer_id,
            kv_block_id: compatibility.kv_block_id,
            token_range_start: compatibility.token_range_start,
            token_range_end: compatibility.token_range_end,
            required,
        };
        let mut catalog = self.open_catalog()?;
        catalog.add_manifest_member(&member)?;
        if required && manifest.pin_count > 0 {
            for _ in 0..manifest.pin_count {
                catalog.increment_pin(&record.object_id)?;
            }
        }
        Ok(member)
    }

    pub fn remove_manifest_member(&self, manifest_id: &str, object_id: &str) -> StoreResult<()> {
        let mut catalog = self.open_catalog()?;
        let manifest = catalog
            .get_manifest(manifest_id)?
            .ok_or_else(|| StoreError::NotFound(manifest_id.to_string()))?;
        let member = catalog.remove_manifest_member(manifest_id, object_id)?;
        if member.as_ref().is_some_and(|member| member.required) {
            for _ in 0..manifest.pin_count {
                catalog.decrement_pin(object_id)?;
            }
        }
        Ok(())
    }

    pub fn get_manifest(&self, manifest_id: &str) -> StoreResult<ManifestInspection> {
        let catalog = self.open_catalog()?;
        let manifest = catalog
            .get_manifest(manifest_id)?
            .ok_or_else(|| StoreError::NotFound(manifest_id.to_string()))?;
        let members = catalog.list_manifest_members(manifest_id)?;
        Ok(ManifestInspection { manifest, members })
    }

    pub fn list_manifests(&self, filter: &ManifestListFilter) -> StoreResult<Vec<ManifestRecord>> {
        self.open_catalog()?.list_manifests(filter)
    }

    pub fn check_manifest_completeness(
        &self,
        manifest_id: &str,
    ) -> StoreResult<ManifestCompletenessReport> {
        let report = self.missing_manifest_members(manifest_id, None)?;
        let state = if report.missing.iter().any(|missing| {
            matches!(
                missing.reason.as_str(),
                "object_corrupt" | "compatibility_mismatch" | "catalog_inconsistent"
            )
        }) {
            CompletenessState::Corrupt
        } else if report.missing.is_empty() {
            CompletenessState::Complete
        } else {
            CompletenessState::Incomplete
        };
        let report = ManifestCompletenessReport {
            completeness_state: state,
            ..report
        };
        let mut catalog = self.open_catalog()?;
        catalog.set_manifest_completeness(manifest_id, state)?;
        Ok(report)
    }

    pub fn missing_manifest_members(
        &self,
        manifest_id: &str,
        expected: Option<ManifestExpectedCoverage>,
    ) -> StoreResult<ManifestCompletenessReport> {
        let catalog = self.open_catalog()?;
        let manifest = catalog
            .get_manifest(manifest_id)?
            .ok_or_else(|| StoreError::NotFound(manifest_id.to_string()))?;
        let members = catalog.list_manifest_members(manifest_id)?;
        drop(catalog);

        let mut missing = Vec::new();
        let mut required_count = 0_i64;
        let mut serveable_required_count = 0_i64;
        for member in &members {
            if !member.required {
                continue;
            }
            required_count += 1;
            match self.inspect_object(&member.object_id) {
                Ok(inspection) => {
                    if !inspection.servable {
                        missing.push(missing_member(
                            member,
                            missing_reason_for_unservable_state(inspection.record.state),
                        ));
                    } else if validate_member_identity(&manifest, &inspection.compatibility)
                        .is_err()
                    {
                        missing.push(missing_member(member, "compatibility_mismatch"));
                    } else {
                        serveable_required_count += 1;
                    }
                }
                Err(StoreError::NotFound(_)) => {
                    missing.push(missing_member(member, "object_absent"));
                }
                Err(StoreError::Integrity(_)) => {
                    missing.push(missing_member(member, "object_corrupt"));
                }
                Err(_) => {
                    missing.push(missing_member(member, "catalog_inconsistent"));
                }
            }
        }

        if let Some(expected) = expected {
            add_expected_missing(manifest_id, &members, &expected, &mut missing);
        }

        let state = if missing.is_empty() {
            CompletenessState::Complete
        } else {
            CompletenessState::Incomplete
        };
        Ok(ManifestCompletenessReport {
            manifest_id: manifest_id.to_string(),
            completeness_state: state,
            required_count,
            serveable_required_count,
            missing,
        })
    }

    pub fn pin_manifest(&self, manifest_id: &str) -> StoreResult<()> {
        let mut catalog = self.open_catalog()?;
        let required_object_ids = catalog.increment_manifest_pin(manifest_id)?;
        for object_id in required_object_ids {
            catalog.increment_pin(&object_id)?;
        }
        Ok(())
    }

    pub fn unpin_manifest(&self, manifest_id: &str) -> StoreResult<()> {
        let mut catalog = self.open_catalog()?;
        let required_object_ids = catalog.decrement_manifest_pin(manifest_id)?;
        for object_id in required_object_ids {
            catalog.decrement_pin(&object_id)?;
        }
        Ok(())
    }

    pub fn mark_quarantined(&self, object_id: &str, reason: &str) -> StoreResult<()> {
        let mut catalog = self.open_catalog()?;
        let result = catalog.mark_quarantined(object_id, reason);
        if result.is_ok() {
            self.memory_tier.invalidate(object_id);
        }
        result
    }

    pub fn mark_verified(&self, object_id: &str) -> StoreResult<()> {
        let object = DiskTier::new(&self.root).read_validated(object_id)?;
        let metadata: Value = serde_json::from_slice(&object.metadata)?;
        let validation = validate_object(&metadata, &object.payload, None);
        if validation.status != "accepted" {
            return Err(StoreError::Integrity(validation.reason_code));
        }
        let catalog = self.open_catalog()?;
        let record = catalog
            .get_object_record(object_id)?
            .ok_or_else(|| StoreError::NotFound(object_id.to_string()))?;
        if Some(record.descriptor_hash.as_str()) != validation.descriptor_hash.as_deref()
            || Some(record.payload_hash.as_str()) != validation.payload_hash.as_deref()
        {
            return Err(StoreError::Integrity("catalog_hash_mismatch".to_string()));
        }
        drop(catalog);
        let mut catalog = self.open_catalog()?;
        let result = catalog.mark_verified(object_id);
        if result.is_ok() {
            self.memory_tier.invalidate(object_id);
        }
        result
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
            memory_tier: Arc::new(MemoryTier::disabled()),
        }
    }
}

pub(crate) fn compatibility_from_descriptor(
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

fn select_eviction_candidates(
    candidates: Vec<crate::store::EvictionCandidate>,
    starting_bytes_on_disk: i64,
    target_bytes: Option<i64>,
    max_objects: Option<usize>,
) -> Vec<crate::store::EvictionCandidate> {
    let mut selected = Vec::new();
    let mut projected_bytes = starting_bytes_on_disk;
    for candidate in candidates {
        if max_objects
            .map(|limit| selected.len() >= limit)
            .unwrap_or(false)
        {
            break;
        }
        if target_bytes
            .map(|target| projected_bytes <= target)
            .unwrap_or(false)
        {
            break;
        }
        projected_bytes = projected_bytes.saturating_sub(candidate.bytes_on_disk);
        selected.push(candidate);
    }
    selected
}

fn compute_manifest_id(
    manifest_type: ManifestType,
    model_hash: Option<&str>,
    tokenizer_hash: Option<&str>,
    rope_config_hash: Option<&str>,
    prefix_hash: &str,
    token_range_start: i64,
    token_range_end: i64,
) -> String {
    let material = format!(
        "{}\n{}\n{}\n{}\n{}\n{}\n{}",
        manifest_type.as_str(),
        model_hash.unwrap_or(""),
        tokenizer_hash.unwrap_or(""),
        rope_config_hash.unwrap_or(""),
        prefix_hash,
        token_range_start,
        token_range_end
    );
    format!(
        "{}{}",
        MANIFEST_ID_PREFIX,
        blake3::hash(material.as_bytes()).to_hex()
    )
}

fn validate_member_identity(
    manifest: &ManifestRecord,
    compatibility: &ObjectCompatibility,
) -> StoreResult<()> {
    if manifest.model_hash.as_deref() != compatibility.model_hash.as_deref() {
        return Err(StoreError::Manifest(
            "member model_hash mismatch".to_string(),
        ));
    }
    if manifest.tokenizer_hash.is_some()
        && manifest.tokenizer_hash.as_deref() != compatibility.tokenizer_hash.as_deref()
    {
        return Err(StoreError::Manifest(
            "member tokenizer_hash mismatch".to_string(),
        ));
    }
    if manifest.rope_config_hash.is_some()
        && manifest.rope_config_hash.as_deref() != compatibility.rope_config_hash.as_deref()
    {
        return Err(StoreError::Manifest(
            "member rope_config_hash mismatch".to_string(),
        ));
    }
    if compatibility.prefix_hash.as_deref() != Some(manifest.prefix_hash.as_str()) {
        return Err(StoreError::Manifest(
            "member prefix_hash mismatch".to_string(),
        ));
    }
    let start = compatibility
        .token_range_start
        .ok_or_else(|| StoreError::Manifest("member token_range_start missing".to_string()))?;
    let end = compatibility
        .token_range_end
        .ok_or_else(|| StoreError::Manifest("member token_range_end missing".to_string()))?;
    if start < manifest.token_range_start || end > manifest.token_range_end || end < start {
        return Err(StoreError::Manifest(
            "member token range mismatch".to_string(),
        ));
    }
    Ok(())
}

fn missing_member(member: &ManifestMember, reason: &str) -> MissingManifestMember {
    MissingManifestMember {
        manifest_id: member.manifest_id.clone(),
        object_id: Some(member.object_id.clone()),
        layer_id: member.layer_id,
        kv_block_id: member.kv_block_id,
        required: member.required,
        reason: reason.to_string(),
    }
}

fn missing_reason_for_unservable_state(state: ObjectState) -> &'static str {
    match state {
        ObjectState::Staging => "object_staging",
        ObjectState::Evicted => "object_evicted",
        ObjectState::Quarantined => "object_quarantined",
        ObjectState::Corrupt => "object_corrupt",
        ObjectState::Missing => "object_missing",
        _ => "catalog_inconsistent",
    }
}

fn add_expected_missing(
    manifest_id: &str,
    members: &[ManifestMember],
    expected: &ManifestExpectedCoverage,
    missing: &mut Vec<MissingManifestMember>,
) {
    if let Some(expected_members) = expected.expected_members.as_ref() {
        for expected in expected_members {
            if !members.iter().any(|member| {
                member.required
                    && member.layer_id == Some(expected.layer_id)
                    && member.kv_block_id == Some(expected.kv_block_id)
                    && expected
                        .token_range_start
                        .map(|start| member.token_range_start == Some(start))
                        .unwrap_or(true)
                    && expected
                        .token_range_end
                        .map(|end| member.token_range_end == Some(end))
                        .unwrap_or(true)
            }) {
                missing.push(expected_missing_member(manifest_id, expected));
            }
        }
        return;
    }

    let Some(layer_count) = expected.expected_layer_count else {
        return;
    };
    let Some(block_count) = expected.expected_block_count else {
        return;
    };
    for layer_id in 0..layer_count {
        for kv_block_id in 0..block_count {
            if !members.iter().any(|member| {
                member.required
                    && member.layer_id == Some(layer_id)
                    && member.kv_block_id == Some(kv_block_id)
            }) {
                missing.push(MissingManifestMember {
                    manifest_id: manifest_id.to_string(),
                    object_id: None,
                    layer_id: Some(layer_id),
                    kv_block_id: Some(kv_block_id),
                    required: true,
                    reason: "expected_member_missing".to_string(),
                });
            }
        }
    }
}

fn expected_missing_member(
    manifest_id: &str,
    expected: &ManifestExpectedMember,
) -> MissingManifestMember {
    MissingManifestMember {
        manifest_id: manifest_id.to_string(),
        object_id: None,
        layer_id: Some(expected.layer_id),
        kv_block_id: Some(expected.kv_block_id),
        required: true,
        reason: "expected_member_missing".to_string(),
    }
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

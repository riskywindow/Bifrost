use crate::cache::hash::OBJECT_ID_PREFIX;
use crate::cache::object_meta::BifrostKvObjectDescriptor;
use crate::cache::validate::validate_object;
use crate::store::errors::StoreResult;
use crate::store::lifecycle::can_serve;
use crate::store::locations::{sanitize_object_id, StoreLayout};
use crate::store::object_record::{ObjectLocation, ObjectRecord, ObjectState};
use crate::store::store::compatibility_from_descriptor;
use crate::store::{CompletenessState, ManifestListFilter, Store};
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum FsckMode {
    Check,
    Repair,
    Quarantine,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum FsckSeverity {
    Info,
    Warning,
    Error,
    Critical,
}

impl FsckSeverity {
    fn as_str(self) -> &'static str {
        match self {
            Self::Info => "info",
            Self::Warning => "warning",
            Self::Error => "error",
            Self::Critical => "critical",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum FsckStatus {
    Clean,
    Dirty,
    Repaired,
    Quarantined,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct FsckFinding {
    pub finding_type: String,
    pub severity: FsckSeverity,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub object_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub manifest_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub path: Option<String>,
    pub message: String,
    pub suggested_action: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct FsckMutation {
    pub mutation_type: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub object_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub manifest_id: Option<String>,
    pub message: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct FsckResult {
    pub status: FsckStatus,
    pub findings: Vec<FsckFinding>,
    pub counts_by_severity: BTreeMap<String, usize>,
    pub mutations_applied: Vec<FsckMutation>,
    pub warnings: Vec<String>,
}

#[derive(Debug, Clone, Default)]
struct FileSet {
    metadata: BTreeMap<String, PathBuf>,
    payloads: BTreeMap<String, PathBuf>,
}

pub fn run_fsck(store: &Store, mode: FsckMode) -> StoreResult<FsckResult> {
    let layout = StoreLayout::new(store.root());
    let paths = layout.paths();
    let mut result = FsckResult {
        status: FsckStatus::Clean,
        findings: Vec::new(),
        counts_by_severity: BTreeMap::new(),
        mutations_applied: Vec::new(),
        warnings: Vec::new(),
    };

    let catalog = crate::store::open_catalog(&paths.catalog)?;
    let records = catalog.list_objects(&Default::default())?;
    let locations = catalog.list_object_locations()?;
    let manifests = catalog.list_manifests(&ManifestListFilter::default())?;
    let manifest_members = catalog.list_all_manifest_members()?;
    let opaque_key_entries = catalog.list_all_opaque_key_index_entries()?;
    drop(catalog);

    let records_by_id = records
        .iter()
        .map(|record| (record.object_id.clone(), record.clone()))
        .collect::<BTreeMap<_, _>>();
    let locations_by_id = locations
        .iter()
        .filter(|location| location.tier == "disk")
        .map(|location| (location.object_id.clone(), location.clone()))
        .collect::<BTreeMap<_, _>>();
    let files = scan_committed_files(&paths.objects_dir, &mut result)?;

    for record in &records {
        let location = locations_by_id.get(&record.object_id);
        check_catalog_object(store, &layout, mode, record, location, &mut result)?;
    }

    for (object_id, path) in &files.metadata {
        if !records_by_id.contains_key(object_id) {
            finding(
                &mut result,
                "metadata_file_without_catalog_entry",
                FsckSeverity::Warning,
                Some(object_id),
                None,
                Some(path),
                "metadata file has no catalog object row",
                "repair can import it only if the paired payload validates",
            );
        }
    }
    for (object_id, path) in &files.payloads {
        if !records_by_id.contains_key(object_id) {
            finding(
                &mut result,
                "payload_file_without_catalog_entry",
                FsckSeverity::Warning,
                Some(object_id),
                None,
                Some(path),
                "payload file has no catalog object row",
                "repair can import it only if the paired metadata validates",
            );
        }
    }

    if mode == FsckMode::Repair {
        repair_orphan_objects(store, &files, &records_by_id, &mut result)?;
    }

    check_staging(mode, &paths.staging_dir, &mut result)?;
    check_manifests(
        store,
        mode,
        manifests,
        manifest_members,
        &records_by_id,
        &mut result,
    )?;
    check_opaque_key_index(store, mode, opaque_key_entries, &records_by_id, &mut result)?;

    result.counts_by_severity = count_severities(&result.findings);
    result.status = if mode == FsckMode::Quarantine && !result.mutations_applied.is_empty() {
        FsckStatus::Quarantined
    } else if mode == FsckMode::Repair && !result.mutations_applied.is_empty() {
        FsckStatus::Repaired
    } else if result.findings.is_empty() {
        FsckStatus::Clean
    } else {
        FsckStatus::Dirty
    };
    Ok(result)
}

fn check_opaque_key_index(
    store: &Store,
    _mode: FsckMode,
    entries: Vec<crate::store::OpaqueKeyRecord>,
    records_by_id: &BTreeMap<String, ObjectRecord>,
    result: &mut FsckResult,
) -> StoreResult<()> {
    for entry in entries {
        let Some(_record) = records_by_id.get(&entry.object_id) else {
            finding(
                result,
                "opaque_key_index_object_missing",
                FsckSeverity::Error,
                Some(&entry.object_id),
                None,
                None,
                "opaque key index references a missing catalog object",
                "repair should rebuild or remove stale opaque key index rows",
            );
            continue;
        };
        if store.inspect_object(&entry.object_id).is_err() {
            finding(
                result,
                "opaque_key_index_object_uninspectable",
                FsckSeverity::Error,
                Some(&entry.object_id),
                None,
                None,
                "opaque key index points at an object whose files or descriptor cannot be inspected",
                "repair or quarantine should prevent this row from satisfying cache hits",
            );
        }
    }
    Ok(())
}

fn check_catalog_object(
    store: &Store,
    layout: &StoreLayout,
    mode: FsckMode,
    record: &ObjectRecord,
    location: Option<&ObjectLocation>,
    result: &mut FsckResult,
) -> StoreResult<()> {
    let expected_meta = layout.meta_path(&record.object_id)?;
    let expected_payload = layout.payload_path(&record.object_id)?;
    let Some(location) = location else {
        finding(
            result,
            "object_location_missing",
            FsckSeverity::Error,
            Some(&record.object_id),
            None,
            None,
            "catalog object has no disk location row",
            "repair should mark the object missing unless valid files can be proven",
        );
        if mode == FsckMode::Repair {
            mark_state(
                store,
                &record.object_id,
                ObjectState::Missing,
                "object_location_missing",
                result,
            )?;
        }
        return Ok(());
    };

    if location.meta_path != expected_meta.to_string_lossy()
        || location.payload_path != expected_payload.to_string_lossy()
    {
        finding(
            result,
            "object_location_path_mismatch",
            FsckSeverity::Error,
            Some(&record.object_id),
            None,
            None,
            "catalog location path does not match deterministic object layout",
            "repair can rewrite the location row only after files validate at the deterministic path",
        );
    }

    let meta_exists = expected_meta.exists();
    let payload_exists = expected_payload.exists();
    if !meta_exists {
        finding(
            result,
            "catalog_object_missing_metadata_file",
            FsckSeverity::Error,
            Some(&record.object_id),
            None,
            Some(&expected_meta),
            "catalog object is missing its metadata file",
            "repair should mark the object missing",
        );
    }
    if !payload_exists {
        finding(
            result,
            "catalog_object_missing_payload_file",
            FsckSeverity::Error,
            Some(&record.object_id),
            None,
            Some(&expected_payload),
            "catalog object is missing its payload file",
            "repair should mark the object missing",
        );
    }
    if !meta_exists || !payload_exists {
        if mode == FsckMode::Repair {
            mark_state(
                store,
                &record.object_id,
                ObjectState::Missing,
                "catalog_object_missing_file",
                result,
            )?;
        }
        return Ok(());
    }

    let metadata_bytes = fs::read(&expected_meta)?;
    let payload = fs::read(&expected_payload)?;
    let metadata = match serde_json::from_slice::<serde_json::Value>(&metadata_bytes) {
        Ok(value) => value,
        Err(error) => {
            finding(
                result,
                "metadata_parse_failed",
                FsckSeverity::Critical,
                Some(&record.object_id),
                None,
                Some(&expected_meta),
                &format!("metadata JSON parse failed: {error}"),
                "quarantine suspect object bytes",
            );
            if mode == FsckMode::Quarantine {
                quarantine_object(store, &record.object_id, "metadata_parse_failed", result)?;
            }
            return Ok(());
        }
    };
    let validation = validate_object(&metadata, &payload, None);
    if validation.status != "accepted" {
        let finding_type = match validation.reason_code.as_str() {
            "payload_hash_mismatch" => "payload_hash_mismatch",
            "descriptor_hash_mismatch" => "metadata_hash_mismatch",
            "object_id_mismatch" => "object_id_mismatch",
            "byte_length_mismatch" => "byte_length_mismatch",
            _ => "object_validation_failed",
        };
        finding(
            result,
            finding_type,
            FsckSeverity::Critical,
            Some(&record.object_id),
            None,
            None,
            &format!(
                "Phase 1 validation rejected object: {}",
                validation.reason_code
            ),
            "quarantine suspect object bytes",
        );
        if mode == FsckMode::Quarantine {
            quarantine_object(store, &record.object_id, finding_type, result)?;
        }
        return Ok(());
    }

    if validation.object_id.as_deref() != Some(record.object_id.as_str()) {
        finding(
            result,
            "object_id_mismatch",
            FsckSeverity::Critical,
            Some(&record.object_id),
            None,
            None,
            "validated object ID does not match catalog object ID",
            "quarantine suspect object bytes",
        );
        if mode == FsckMode::Quarantine {
            quarantine_object(store, &record.object_id, "object_id_mismatch", result)?;
        }
    }
    if validation.descriptor_hash.as_deref() != Some(record.descriptor_hash.as_str()) {
        finding(
            result,
            "metadata_hash_mismatch",
            FsckSeverity::Critical,
            Some(&record.object_id),
            None,
            Some(&expected_meta),
            "validated descriptor hash does not match catalog descriptor_hash",
            "quarantine suspect object bytes",
        );
        if mode == FsckMode::Quarantine {
            quarantine_object(store, &record.object_id, "metadata_hash_mismatch", result)?;
        }
    }
    if validation.payload_hash.as_deref() != Some(record.payload_hash.as_str()) {
        finding(
            result,
            "payload_hash_mismatch",
            FsckSeverity::Critical,
            Some(&record.object_id),
            None,
            Some(&expected_payload),
            "validated payload hash does not match catalog payload_hash",
            "quarantine suspect object bytes",
        );
        if mode == FsckMode::Quarantine {
            quarantine_object(store, &record.object_id, "payload_hash_mismatch", result)?;
        }
    }
    if record.byte_length != payload.len() as i64 {
        finding(
            result,
            "byte_length_mismatch",
            FsckSeverity::Error,
            Some(&record.object_id),
            None,
            Some(&expected_payload),
            "catalog byte_length does not match payload length",
            "repair can update the derivable byte length only after full validation",
        );
    }

    let expected_bytes = (metadata_bytes.len() + payload.len()) as i64;
    let has_object_finding = result
        .findings
        .iter()
        .any(|finding| finding.object_id.as_deref() == Some(record.object_id.as_str()));
    if mode == FsckMode::Repair
        && has_object_finding
        && validation.status == "accepted"
        && validation.object_id.as_deref() == Some(record.object_id.as_str())
    {
        let mut catalog = crate::store::open_catalog(&layout.paths().catalog)?;
        if location.meta_path != expected_meta.to_string_lossy()
            || location.payload_path != expected_payload.to_string_lossy()
            || location.bytes_on_disk != expected_bytes
        {
            catalog.fsck_update_location(
                &record.object_id,
                &expected_meta.to_string_lossy(),
                &expected_payload.to_string_lossy(),
                expected_bytes,
            )?;
            result.mutations_applied.push(FsckMutation {
                mutation_type: "updated_object_location".to_string(),
                object_id: Some(record.object_id.clone()),
                manifest_id: None,
                message: "updated derivable disk location fields".to_string(),
            });
        }
        if record.byte_length != payload.len() as i64 {
            catalog.fsck_update_byte_length(&record.object_id, payload.len() as i64)?;
            result.mutations_applied.push(FsckMutation {
                mutation_type: "updated_byte_length".to_string(),
                object_id: Some(record.object_id.clone()),
                manifest_id: None,
                message: "updated derivable byte_length after validation".to_string(),
            });
        }
    }

    Ok(())
}

fn repair_orphan_objects(
    store: &Store,
    files: &FileSet,
    records_by_id: &BTreeMap<String, ObjectRecord>,
    result: &mut FsckResult,
) -> StoreResult<()> {
    let layout = StoreLayout::new(store.root());
    for object_id in files
        .metadata
        .keys()
        .filter(|object_id| files.payloads.contains_key(*object_id))
    {
        if records_by_id.contains_key(object_id) {
            continue;
        }
        let expected_meta = layout.meta_path(object_id)?;
        let expected_payload = layout.payload_path(object_id)?;
        if files.metadata[object_id] != expected_meta
            || files.payloads[object_id] != expected_payload
        {
            finding(
                result,
                "orphan_file_path_mismatch",
                FsckSeverity::Error,
                Some(object_id),
                None,
                None,
                "orphan files are not in the deterministic committed object path",
                "do not import; inspect or quarantine manually",
            );
            continue;
        }
        let metadata_bytes = fs::read(files.metadata.get(object_id).unwrap())?;
        let payload = fs::read(files.payloads.get(object_id).unwrap())?;
        let metadata: serde_json::Value = match serde_json::from_slice(&metadata_bytes) {
            Ok(value) => value,
            Err(_) => continue,
        };
        let validation = validate_object(&metadata, &payload, None);
        if validation.status != "accepted" || validation.object_id.as_deref() != Some(object_id) {
            continue;
        }
        let descriptor: BifrostKvObjectDescriptor = serde_json::from_value(metadata)?;
        let compatibility = compatibility_from_descriptor(&descriptor)?;
        let now = now_unix_ms();
        let record = ObjectRecord {
            object_id: object_id.clone(),
            object_type: descriptor.object_type.clone(),
            schema_version: descriptor.schema_version.clone(),
            descriptor_hash: validation.descriptor_hash.clone().unwrap_or_default(),
            payload_hash: validation.payload_hash.clone().unwrap_or_default(),
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
            object_id: object_id.clone(),
            tier: "disk".to_string(),
            meta_path: files.metadata[object_id].to_string_lossy().into_owned(),
            payload_path: files.payloads[object_id].to_string_lossy().into_owned(),
            bytes_on_disk: (metadata_bytes.len() + payload.len()) as i64,
        };
        let mut catalog = crate::store::open_catalog(&layout.paths().catalog)?;
        catalog.insert_committed_object(&record, &location, &compatibility)?;
        catalog.transition_object_state(object_id, ObjectState::Verified, None)?;
        result.mutations_applied.push(FsckMutation {
            mutation_type: "rebuilt_catalog_entry".to_string(),
            object_id: Some(object_id.clone()),
            manifest_id: None,
            message: "imported valid orphan object after Phase 1 validation".to_string(),
        });
    }
    Ok(())
}

fn check_staging(mode: FsckMode, staging_dir: &Path, result: &mut FsckResult) -> StoreResult<()> {
    if !staging_dir.exists() {
        return Ok(());
    }
    for entry in fs::read_dir(staging_dir)? {
        let entry = entry?;
        if !entry.file_type()?.is_dir() {
            continue;
        }
        finding(
            result,
            "staging_transfer_abandoned",
            FsckSeverity::Warning,
            None,
            None,
            Some(&entry.path()),
            "staging transfer directory remains after restart",
            "repair can remove abandoned staging directories",
        );
        if mode == FsckMode::Repair {
            fs::remove_dir_all(entry.path())?;
            result.mutations_applied.push(FsckMutation {
                mutation_type: "removed_abandoned_staging".to_string(),
                object_id: None,
                manifest_id: None,
                message: "removed abandoned staging transfer directory".to_string(),
            });
        }
    }
    Ok(())
}

fn check_manifests(
    store: &Store,
    mode: FsckMode,
    manifests: Vec<crate::store::ManifestRecord>,
    members: Vec<crate::store::ManifestMember>,
    records_by_id: &BTreeMap<String, ObjectRecord>,
    result: &mut FsckResult,
) -> StoreResult<()> {
    let complete = manifests
        .iter()
        .filter(|manifest| manifest.completeness_state == CompletenessState::Complete)
        .map(|manifest| manifest.manifest_id.clone())
        .collect::<BTreeSet<_>>();
    for member in members {
        let Some(record) = records_by_id.get(&member.object_id) else {
            finding(
                result,
                "manifest_member_object_missing",
                FsckSeverity::Error,
                Some(&member.object_id),
                Some(&member.manifest_id),
                None,
                "manifest member references a missing catalog object",
                "repair or quarantine should make the manifest incomplete",
            );
            if mode != FsckMode::Check {
                set_manifest_state(
                    store,
                    &member.manifest_id,
                    CompletenessState::Incomplete,
                    result,
                )?;
            }
            continue;
        };
        if !can_serve(record.state, record.pin_count)
            || store
                .inspect_object(&member.object_id)
                .map(|i| !i.servable)
                .unwrap_or(true)
        {
            finding(
                result,
                "manifest_member_object_not_serveable",
                FsckSeverity::Error,
                Some(&member.object_id),
                Some(&member.manifest_id),
                None,
                "manifest member is not serveable",
                "repair or quarantine should make the manifest incomplete",
            );
            if mode != FsckMode::Check {
                let state = if matches!(
                    record.state,
                    ObjectState::Corrupt | ObjectState::Quarantined
                ) {
                    CompletenessState::Corrupt
                } else {
                    CompletenessState::Incomplete
                };
                set_manifest_state(store, &member.manifest_id, state, result)?;
            }
        }
        if complete.contains(&member.manifest_id)
            && matches!(
                record.state,
                ObjectState::Quarantined | ObjectState::Corrupt
            )
        {
            finding(
                result,
                "quarantined_object_referenced_by_complete_manifest",
                FsckSeverity::Critical,
                Some(&member.object_id),
                Some(&member.manifest_id),
                None,
                "complete manifest references a corrupt or quarantined object",
                "quarantine mode should mark the manifest corrupt",
            );
            if mode == FsckMode::Quarantine {
                set_manifest_state(
                    store,
                    &member.manifest_id,
                    CompletenessState::Corrupt,
                    result,
                )?;
            }
        }
    }
    Ok(())
}

fn quarantine_object(
    store: &Store,
    object_id: &str,
    reason: &str,
    result: &mut FsckResult,
) -> StoreResult<()> {
    let layout = StoreLayout::new(store.root());
    let paths = layout.paths();
    let quarantine_dir = unique_quarantine_dir(&paths.quarantine_dir, object_id)?;
    fs::create_dir_all(&quarantine_dir)?;
    let meta = layout.meta_path(object_id)?;
    let payload = layout.payload_path(object_id)?;
    if meta.exists() {
        fs::rename(&meta, quarantine_dir.join(meta.file_name().unwrap()))?;
    }
    if payload.exists() {
        fs::rename(&payload, quarantine_dir.join(payload.file_name().unwrap()))?;
    }
    let mut catalog = crate::store::open_catalog(&paths.catalog)?;
    catalog.fsck_mark_object_state(object_id, ObjectState::Quarantined, reason)?;
    store.invalidate_memory_tier(object_id);
    result.mutations_applied.push(FsckMutation {
        mutation_type: "quarantined_object".to_string(),
        object_id: Some(object_id.to_string()),
        manifest_id: None,
        message: format!("moved suspect object files to {}", quarantine_dir.display()),
    });
    Ok(())
}

fn unique_quarantine_dir(quarantine_root: &Path, object_id: &str) -> StoreResult<PathBuf> {
    let sanitized = sanitize_object_id(object_id)?;
    let base = quarantine_root.join(&sanitized);
    if !base.exists() {
        return Ok(base);
    }
    for index in 1..=1024 {
        let candidate = quarantine_root.join(format!("{sanitized}.{index}"));
        if !candidate.exists() {
            return Ok(candidate);
        }
    }
    Err(crate::store::StoreError::Filesystem(std::io::Error::new(
        std::io::ErrorKind::AlreadyExists,
        format!("no available quarantine directory for {object_id}"),
    )))
}

fn mark_state(
    store: &Store,
    object_id: &str,
    state: ObjectState,
    reason: &str,
    result: &mut FsckResult,
) -> StoreResult<()> {
    let mut catalog = crate::store::open_catalog(&StoreLayout::new(store.root()).paths().catalog)?;
    catalog.fsck_mark_object_state(object_id, state, reason)?;
    store.invalidate_memory_tier(object_id);
    result.mutations_applied.push(FsckMutation {
        mutation_type: format!("marked_object_{}", state.as_str()),
        object_id: Some(object_id.to_string()),
        manifest_id: None,
        message: reason.to_string(),
    });
    Ok(())
}

fn set_manifest_state(
    store: &Store,
    manifest_id: &str,
    state: CompletenessState,
    result: &mut FsckResult,
) -> StoreResult<()> {
    let mut catalog = crate::store::open_catalog(&StoreLayout::new(store.root()).paths().catalog)?;
    catalog.set_manifest_completeness(manifest_id, state)?;
    result.mutations_applied.push(FsckMutation {
        mutation_type: "updated_manifest_completeness".to_string(),
        object_id: None,
        manifest_id: Some(manifest_id.to_string()),
        message: format!("set manifest completeness to {}", state.as_str()),
    });
    Ok(())
}

fn scan_committed_files(objects_dir: &Path, result: &mut FsckResult) -> StoreResult<FileSet> {
    let mut files = FileSet::default();
    if !objects_dir.exists() {
        return Ok(files);
    }
    scan_dir(objects_dir, &mut |path| {
        let Some(name) = path.file_name().and_then(|name| name.to_str()) else {
            return;
        };
        let (suffix, is_meta) = if let Some(suffix) = name.strip_suffix(".meta.json") {
            (suffix, true)
        } else if let Some(suffix) = name.strip_suffix(".payload.bin") {
            (suffix, false)
        } else {
            return;
        };
        let object_id = format!("{OBJECT_ID_PREFIX}{suffix}");
        if sanitize_object_id(&object_id).is_err() {
            finding(
                result,
                "invalid_committed_filename",
                FsckSeverity::Warning,
                None,
                None,
                Some(path),
                "committed object filename does not encode a valid object ID",
                "inspect or quarantine manually",
            );
            return;
        }
        if is_meta {
            files.metadata.insert(object_id, path.to_path_buf());
        } else {
            files.payloads.insert(object_id, path.to_path_buf());
        }
    })?;
    Ok(files)
}

fn scan_dir<F>(dir: &Path, visit: &mut F) -> StoreResult<()>
where
    F: FnMut(&Path),
{
    for entry in fs::read_dir(dir)? {
        let entry = entry?;
        let path = entry.path();
        let ty = entry.file_type()?;
        if ty.is_dir() {
            scan_dir(&path, visit)?;
        } else if ty.is_file() {
            visit(&path);
        }
    }
    Ok(())
}

fn finding(
    result: &mut FsckResult,
    finding_type: &str,
    severity: FsckSeverity,
    object_id: Option<&str>,
    manifest_id: Option<&str>,
    path: Option<&Path>,
    message: &str,
    suggested_action: &str,
) {
    result.findings.push(FsckFinding {
        finding_type: finding_type.to_string(),
        severity,
        object_id: object_id.map(ToOwned::to_owned),
        manifest_id: manifest_id.map(ToOwned::to_owned),
        path: path.map(|path| path.to_string_lossy().into_owned()),
        message: message.to_string(),
        suggested_action: suggested_action.to_string(),
    });
}

fn count_severities(findings: &[FsckFinding]) -> BTreeMap<String, usize> {
    let mut counts = BTreeMap::new();
    for finding in findings {
        *counts
            .entry(finding.severity.as_str().to_string())
            .or_insert(0) += 1;
    }
    counts
}

fn now_unix_ms() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("system clock is before unix epoch")
        .as_millis() as i64
}

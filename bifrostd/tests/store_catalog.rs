use bifrostd::store::{
    can_evict, can_serve, open_catalog, ObjectCompatibility, ObjectListFilter, ObjectLocation,
    ObjectRecord, ObjectState, StoreError,
};
use std::fs;
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

fn temp_catalog_path(test_name: &str) -> PathBuf {
    let unique = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_nanos();
    std::env::temp_dir()
        .join(format!(
            "bifrostd-store-catalog-{test_name}-{}-{unique}",
            std::process::id()
        ))
        .join("catalog.sqlite3")
}

fn cleanup(path: &Path) {
    if let Some(parent) = path.parent() {
        if parent.exists() {
            fs::remove_dir_all(parent).unwrap();
        }
    }
}

fn record(object_id: &str) -> ObjectRecord {
    ObjectRecord {
        object_id: object_id.to_string(),
        object_type: "native_kv_page".to_string(),
        schema_version: "bifrost.kv_object.v1".to_string(),
        descriptor_hash: format!("descriptor-{object_id}"),
        payload_hash: format!("payload-{object_id}"),
        byte_length: 4096,
        state: ObjectState::Committed,
        created_at_unix_ms: 1000,
        committed_at_unix_ms: Some(1100),
        verified_at_unix_ms: None,
        last_accessed_unix_ms: None,
        access_count: 0,
        pin_count: 0,
        ttl_expires_at_unix_ms: None,
        quarantine_reason: None,
    }
}

fn location(object_id: &str) -> ObjectLocation {
    ObjectLocation {
        object_id: object_id.to_string(),
        tier: "disk".to_string(),
        meta_path: format!("/store/{object_id}.json"),
        payload_path: format!("/store/{object_id}.bin"),
        bytes_on_disk: 8192,
    }
}

fn compatibility(
    object_id: &str,
    model_hash: &str,
    prefix_hash: &str,
    opaque_engine_key_hash: &str,
) -> ObjectCompatibility {
    ObjectCompatibility {
        object_id: object_id.to_string(),
        model_hash: Some(model_hash.to_string()),
        tokenizer_hash: Some("tokenizer-a".to_string()),
        config_hash: Some("config-a".to_string()),
        rope_config_hash: Some("rope-a".to_string()),
        dtype: Some("bf16".to_string()),
        engine_name: Some("bifrost-test-engine".to_string()),
        engine_version: Some("1".to_string()),
        integration_name: Some("bifrost-test".to_string()),
        kv_cache_format: Some("native".to_string()),
        prefix_hash: Some(prefix_hash.to_string()),
        token_range_start: Some(0),
        token_range_end: Some(128),
        layer_id: Some(7),
        kv_block_id: Some(3),
        opaque_engine_key_hash: Some(opaque_engine_key_hash.to_string()),
    }
}

fn insert_object(
    catalog: &mut bifrostd::store::Catalog,
    object_id: &str,
    model_hash: &str,
    prefix_hash: &str,
    opaque_engine_key_hash: &str,
) {
    catalog
        .insert_committed_object(
            &record(object_id),
            &location(object_id),
            &compatibility(object_id, model_hash, prefix_hash, opaque_engine_key_hash),
        )
        .unwrap();
}

#[test]
fn insert_and_read_committed_object() {
    let path = temp_catalog_path("insert-read");
    let mut catalog = open_catalog(&path).unwrap();

    insert_object(&mut catalog, "object-a", "model-a", "prefix-a", "opaque-a");

    let stored = catalog.get_object_record("object-a").unwrap().unwrap();
    let stored_location = catalog
        .get_object_location("object-a", "disk")
        .unwrap()
        .unwrap();
    let stored_compatibility = catalog
        .get_object_compatibility("object-a")
        .unwrap()
        .unwrap();

    assert_eq!(stored.state, ObjectState::Committed);
    assert_eq!(stored.byte_length, 4096);
    assert_eq!(stored_location.payload_path, "/store/object-a.bin");
    assert_eq!(stored_compatibility.model_hash.as_deref(), Some("model-a"));
    cleanup(&path);
}

#[test]
fn access_counters_update() {
    let path = temp_catalog_path("access-counters");
    let mut catalog = open_catalog(&path).unwrap();
    insert_object(&mut catalog, "object-a", "model-a", "prefix-a", "opaque-a");

    catalog.update_access_on_get("object-a", 128).unwrap();
    catalog.update_access_on_put("object-a", 256).unwrap();

    let access = catalog.get_object_access("object-a").unwrap().unwrap();
    let record = catalog.get_object_record("object-a").unwrap().unwrap();
    assert_eq!(access.get_count, 1);
    assert_eq!(access.put_count, 1);
    assert_eq!(access.bytes_read_total, 128);
    assert_eq!(access.bytes_written_total, 256);
    assert_eq!(record.access_count, 2);
    assert!(record.last_accessed_unix_ms.is_some());
    cleanup(&path);
}

#[test]
fn pin_increment_decrement_works_and_never_goes_below_zero() {
    let path = temp_catalog_path("pinning");
    let mut catalog = open_catalog(&path).unwrap();
    insert_object(&mut catalog, "object-a", "model-a", "prefix-a", "opaque-a");
    catalog
        .transition_object_state("object-a", ObjectState::Verified, None)
        .unwrap();

    catalog.increment_pin("object-a").unwrap();
    let pinned = catalog.get_object_record("object-a").unwrap().unwrap();
    assert_eq!(pinned.pin_count, 1);
    assert_eq!(pinned.state, ObjectState::Pinned);

    catalog.decrement_pin("object-a").unwrap();
    catalog.decrement_pin("object-a").unwrap();
    let unpinned = catalog.get_object_record("object-a").unwrap().unwrap();
    assert_eq!(unpinned.pin_count, 0);
    assert_eq!(unpinned.state, ObjectState::Verified);
    cleanup(&path);
}

#[test]
fn committed_object_cannot_be_pinned_into_servable_state() {
    let path = temp_catalog_path("committed-pin-rejected");
    let mut catalog = open_catalog(&path).unwrap();
    insert_object(&mut catalog, "object-a", "model-a", "prefix-a", "opaque-a");

    let err = catalog.increment_pin("object-a").unwrap_err();
    assert!(matches!(
        err,
        StoreError::InvalidStateTransition { from, to }
            if from == "committed" && to == "pinned"
    ));
    let record = catalog.get_object_record("object-a").unwrap().unwrap();
    assert_eq!(record.state, ObjectState::Committed);
    assert_eq!(record.pin_count, 0);
    assert!(!can_serve(record.state, record.pin_count));
    cleanup(&path);
}

#[test]
fn lifecycle_serve_and_evict_rules() {
    assert!(!can_serve(ObjectState::Committed, 0));
    assert!(can_serve(ObjectState::Verified, 0));
    assert!(can_serve(ObjectState::Pinned, 1));
    assert!(!can_evict(ObjectState::Pinned, 1));
    assert!(!can_serve(ObjectState::Evicted, 0));
    assert!(!can_serve(ObjectState::Quarantined, 0));
}

#[test]
fn invalid_lifecycle_transition_is_rejected() {
    let path = temp_catalog_path("invalid-transition");
    let mut catalog = open_catalog(&path).unwrap();
    insert_object(&mut catalog, "object-a", "model-a", "prefix-a", "opaque-a");

    let err = catalog
        .transition_object_state("object-a", ObjectState::Evicted, None)
        .unwrap_err();

    assert!(matches!(err, StoreError::InvalidStateTransition { .. }));
    cleanup(&path);
}

#[test]
fn list_by_model_hash() {
    let path = temp_catalog_path("list-model");
    let mut catalog = open_catalog(&path).unwrap();
    insert_object(&mut catalog, "object-a", "model-a", "prefix-a", "opaque-a");
    insert_object(&mut catalog, "object-b", "model-b", "prefix-a", "opaque-b");

    let rows = catalog
        .list_objects(&ObjectListFilter {
            model_hash: Some("model-a".to_string()),
            ..ObjectListFilter::default()
        })
        .unwrap();

    assert_eq!(rows.len(), 1);
    assert_eq!(rows[0].object_id, "object-a");
    cleanup(&path);
}

#[test]
fn list_by_prefix_hash() {
    let path = temp_catalog_path("list-prefix");
    let mut catalog = open_catalog(&path).unwrap();
    insert_object(&mut catalog, "object-a", "model-a", "prefix-a", "opaque-a");
    insert_object(&mut catalog, "object-b", "model-a", "prefix-b", "opaque-b");

    let rows = catalog
        .list_objects(&ObjectListFilter {
            prefix_hash: Some("prefix-b".to_string()),
            ..ObjectListFilter::default()
        })
        .unwrap();

    assert_eq!(rows.len(), 1);
    assert_eq!(rows[0].object_id, "object-b");
    cleanup(&path);
}

#[test]
fn list_by_opaque_engine_key_hash() {
    let path = temp_catalog_path("list-opaque");
    let mut catalog = open_catalog(&path).unwrap();
    insert_object(&mut catalog, "object-a", "model-a", "prefix-a", "opaque-a");
    insert_object(&mut catalog, "object-b", "model-a", "prefix-a", "opaque-b");

    let rows = catalog
        .list_objects(&ObjectListFilter {
            opaque_engine_key_hash: Some("opaque-b".to_string()),
            ..ObjectListFilter::default()
        })
        .unwrap();

    assert_eq!(rows.len(), 1);
    assert_eq!(rows[0].object_id, "object-b");
    cleanup(&path);
}

#[test]
fn stats_reflect_inserted_objects() {
    let path = temp_catalog_path("stats");
    let mut catalog = open_catalog(&path).unwrap();
    insert_object(&mut catalog, "object-a", "model-a", "prefix-a", "opaque-a");
    insert_object(&mut catalog, "object-b", "model-a", "prefix-b", "opaque-b");

    let stats = catalog.store_stats().unwrap();

    assert_eq!(stats.object_count, 2);
    assert_eq!(stats.committed_count, 2);
    assert_eq!(stats.total_logical_bytes, 8192);
    assert_eq!(stats.total_bytes_on_disk, 16384);
    cleanup(&path);
}

#[test]
fn event_log_contains_expected_events() {
    let path = temp_catalog_path("events");
    let mut catalog = open_catalog(&path).unwrap();
    insert_object(&mut catalog, "object-a", "model-a", "prefix-a", "opaque-a");
    catalog
        .transition_object_state("object-a", ObjectState::Verified, None)
        .unwrap();

    catalog.update_access_on_get("object-a", 64).unwrap();
    catalog.increment_pin("object-a").unwrap();
    catalog.decrement_pin("object-a").unwrap();
    catalog.mark_quarantined("object-a", "test_reason").unwrap();

    let events = catalog.store_events().unwrap();
    let event_types = events
        .iter()
        .map(|event| event.event_type.as_str())
        .collect::<Vec<_>>();

    assert_eq!(
        event_types,
        vec![
            "object_committed",
            "object_state_transition",
            "object_accessed",
            "object_pinned",
            "object_unpinned",
            "object_quarantined"
        ]
    );
    assert!(events
        .last()
        .unwrap()
        .details_json
        .as_deref()
        .unwrap()
        .contains("test_reason"));
    cleanup(&path);
}

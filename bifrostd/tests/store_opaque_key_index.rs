use bifrostd::store::{
    open_catalog, EvictionPolicy, EvictionRequest, FsckMode, ObjectListFilter, OpaqueKeyListFilter,
    Store, LATEST_SCHEMA_VERSION,
};
use bifrostd::transport::{chunk_bytes, iter_chunks, DEFAULT_CHUNK_SIZE};
use serde_json::Value;
use std::fs;
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

struct Fixture {
    metadata_bytes: Vec<u8>,
    metadata: Value,
    payload: Vec<u8>,
    target: Value,
}

fn repo_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("..")
}

fn opaque_fixture() -> Fixture {
    let root = repo_root().join("fixtures/opaque_valid");
    let metadata_bytes = fs::read(root.join("lmcache_blob.meta.json")).unwrap();
    let metadata = serde_json::from_slice(&metadata_bytes).unwrap();
    let payload = fs::read(root.join("lmcache_blob.payload.bin")).unwrap();
    let target =
        serde_json::from_slice(&fs::read(root.join("target_profile.json")).unwrap()).unwrap();
    Fixture {
        metadata_bytes,
        metadata,
        payload,
        target,
    }
}

fn temp_store_root(test_name: &str) -> PathBuf {
    let unique = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_nanos();
    std::env::temp_dir().join(format!(
        "bifrostd-opaque-key-index-{test_name}-{}-{unique}",
        std::process::id()
    ))
}

fn cleanup(path: &Path) {
    if path.exists() {
        fs::remove_dir_all(path).unwrap();
    }
}

fn object_id(fixture: &Fixture) -> &str {
    fixture.metadata["object_id"].as_str().unwrap()
}

fn engine_name(fixture: &Fixture) -> &str {
    fixture.metadata["engine_profile"]["engine_name"]
        .as_str()
        .unwrap()
}

fn integration_name(fixture: &Fixture) -> &str {
    fixture.metadata["engine_profile"]["integration_name"]
        .as_str()
        .unwrap()
}

fn opaque_engine_key_hash(fixture: &Fixture) -> &str {
    fixture.metadata["opaque_engine_profile"]["engine_key_hash"]
        .as_str()
        .unwrap()
}

fn put_fixture(store: &Store, transfer_id: &str, fixture: &Fixture) -> String {
    let mut manifest = chunk_bytes(&fixture.payload, DEFAULT_CHUNK_SIZE).unwrap();
    manifest.object_id = Some(object_id(fixture).to_string());
    store
        .begin_put(transfer_id, &fixture.metadata_bytes, &manifest)
        .unwrap();
    for chunk in iter_chunks(&fixture.payload, DEFAULT_CHUNK_SIZE).unwrap() {
        store
            .write_chunk(transfer_id, chunk.info.chunk_index, chunk.bytes)
            .unwrap();
    }
    store
        .commit_put(transfer_id, Some(&fixture.target))
        .unwrap()
        .object_id
}

#[test]
fn opaque_object_insert_populates_key_index() {
    let root = temp_store_root("insert");
    let store = Store::open(root.clone()).unwrap();
    let fixture = opaque_fixture();
    let object_id = put_fixture(&store, "transfer-opaque-insert", &fixture);
    let catalog = open_catalog(&root.join("catalog.sqlite")).unwrap();

    let indexed = catalog
        .get_object_by_opaque_key(
            engine_name(&fixture),
            integration_name(&fixture),
            opaque_engine_key_hash(&fixture),
        )
        .unwrap()
        .unwrap();

    assert_eq!(indexed.object_id, object_id);
    assert!(indexed.serveable);
    cleanup(&root);
}

#[test]
fn query_by_opaque_key_returns_servable_object() {
    let root = temp_store_root("query");
    let store = Store::open(root.clone()).unwrap();
    let fixture = opaque_fixture();
    let object_id = put_fixture(&store, "transfer-opaque-query", &fixture);

    let inspection = store
        .get_object_by_opaque_key(
            engine_name(&fixture),
            integration_name(&fixture),
            opaque_engine_key_hash(&fixture),
        )
        .unwrap()
        .unwrap();

    assert_eq!(inspection.record.object_id, object_id);
    assert!(inspection.servable);
    cleanup(&root);
}

#[test]
fn list_opaque_keys_returns_stable_hash_when_repr_absent() {
    let root = temp_store_root("list");
    let store = Store::open(root.clone()).unwrap();
    let fixture = opaque_fixture();
    let object_id = put_fixture(&store, "transfer-opaque-list", &fixture);

    let keys = store
        .list_opaque_keys(&OpaqueKeyListFilter {
            engine_name: Some(engine_name(&fixture).to_string()),
            integration_name: Some(integration_name(&fixture).to_string()),
            ..OpaqueKeyListFilter::default()
        })
        .unwrap();

    assert_eq!(keys.len(), 1);
    assert_eq!(keys[0].object_id, object_id);
    assert_eq!(
        keys[0].opaque_engine_key_hash,
        opaque_engine_key_hash(&fixture)
    );
    assert!(keys[0].opaque_engine_key_repr.is_none());
    cleanup(&root);
}

#[test]
fn evicted_object_is_not_returned_as_servable() {
    let root = temp_store_root("evicted");
    let store = Store::open(root.clone()).unwrap();
    let fixture = opaque_fixture();
    put_fixture(&store, "transfer-opaque-evicted", &fixture);

    let report = store
        .evict(EvictionRequest {
            policy: EvictionPolicy::Lru,
            target_bytes: Some(0),
            max_objects: Some(1),
            dry_run: false,
            now_unix_ms: i64::MAX,
        })
        .unwrap();

    assert_eq!(report.evicted.len(), 1);
    assert!(store
        .get_object_by_opaque_key(
            engine_name(&fixture),
            integration_name(&fixture),
            opaque_engine_key_hash(&fixture),
        )
        .unwrap()
        .is_none());
    assert!(
        !store
            .list_opaque_keys(&OpaqueKeyListFilter::default())
            .unwrap()[0]
            .serveable
    );
    cleanup(&root);
}

#[test]
fn quarantined_object_is_not_returned_as_servable() {
    let root = temp_store_root("quarantined");
    let store = Store::open(root.clone()).unwrap();
    let fixture = opaque_fixture();
    let object_id = put_fixture(&store, "transfer-opaque-quarantined", &fixture);

    store
        .mark_quarantined(&object_id, "test_quarantine")
        .unwrap();

    assert!(store
        .get_object_by_opaque_key(
            engine_name(&fixture),
            integration_name(&fixture),
            opaque_engine_key_hash(&fixture),
        )
        .unwrap()
        .is_none());
    assert!(
        !store
            .list_opaque_keys(&OpaqueKeyListFilter::default())
            .unwrap()[0]
            .serveable
    );
    cleanup(&root);
}

#[test]
fn fsck_catches_key_index_pointing_to_missing_object() {
    let root = temp_store_root("fsck-missing");
    let store = Store::open(root.clone()).unwrap();
    let fixture = opaque_fixture();
    put_fixture(&store, "transfer-opaque-fsck", &fixture);
    let catalog = open_catalog(&root.join("catalog.sqlite")).unwrap();
    catalog
        .connection()
        .execute_batch(
            "PRAGMA foreign_keys = OFF;
             INSERT INTO opaque_key_index(
               engine_name, integration_name, opaque_engine_key_hash,
               opaque_engine_key_repr, object_id, created_at_unix_ms,
               last_accessed_unix_ms
             ) VALUES (
               'lmcache', 'lmcache_bifrost_remote_storage',
               'blake3:missing', NULL, 'missing-object', 0, NULL
             );
             PRAGMA foreign_keys = ON;",
        )
        .unwrap();

    let result = store.fsck(FsckMode::Check).unwrap();

    assert!(result
        .findings
        .iter()
        .any(|finding| finding.finding_type == "opaque_key_index_object_missing"));
    cleanup(&root);
}

#[test]
fn migration_is_idempotent_and_backfills_opaque_index() {
    let root = temp_store_root("migration-idempotent");
    let store = Store::open(root.clone()).unwrap();
    let fixture = opaque_fixture();
    let object_id = put_fixture(&store, "transfer-opaque-migration", &fixture);
    let mut catalog = open_catalog(&root.join("catalog.sqlite")).unwrap();

    catalog.apply_migrations().unwrap();
    catalog.apply_migrations().unwrap();

    assert_eq!(
        catalog.current_schema_version().unwrap(),
        LATEST_SCHEMA_VERSION
    );
    let keys = catalog
        .list_opaque_keys(&OpaqueKeyListFilter::default())
        .unwrap();
    assert_eq!(keys.len(), 1);
    assert_eq!(keys[0].object_id, object_id);
    assert_eq!(
        store
            .list_objects(&ObjectListFilter {
                integration_name: Some(integration_name(&fixture).to_string()),
                opaque_engine_key_hash: Some(opaque_engine_key_hash(&fixture).to_string()),
                ..ObjectListFilter::default()
            })
            .unwrap()
            .len(),
        1
    );
    cleanup(&root);
}

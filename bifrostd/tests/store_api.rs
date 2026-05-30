use bifrostd::store::{ObjectListFilter, ObjectState, Store, StoreError};
use bifrostd::transport::{chunk_bytes, iter_chunks};
use serde_json::Value;
use std::fs;
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

const TEST_CHUNK_SIZE: usize = 256 * 1024;

struct Fixture {
    metadata_bytes: Vec<u8>,
    metadata: Value,
    payload: Vec<u8>,
    target: Value,
}

fn repo_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("..")
}

fn native_fixture() -> Fixture {
    let root = repo_root().join("fixtures/native_valid");
    let metadata_bytes = fs::read(root.join("tiny_gpt_layer0_block0.meta.json")).unwrap();
    let metadata = serde_json::from_slice(&metadata_bytes).unwrap();
    let payload = fs::read(root.join("tiny_gpt_layer0_block0.payload.bin")).unwrap();
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
        "bifrostd-store-api-{test_name}-{}-{unique}",
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

fn stage_fixture(store: &Store, transfer_id: &str, fixture: &Fixture) {
    let mut manifest = chunk_bytes(&fixture.payload, TEST_CHUNK_SIZE).unwrap();
    manifest.object_id = Some(object_id(fixture).to_string());
    store
        .begin_put(transfer_id, &fixture.metadata_bytes, &manifest)
        .unwrap();
    for chunk in iter_chunks(&fixture.payload, TEST_CHUNK_SIZE).unwrap() {
        store
            .write_chunk(transfer_id, chunk.info.chunk_index, chunk.bytes)
            .unwrap();
    }
}

#[test]
fn store_opens_and_initializes_catalog() {
    let root = temp_store_root("opens-and-initializes-catalog");
    let store = Store::open(root.clone()).unwrap();

    assert!(root.join("catalog.sqlite").exists());
    assert!(root.join("objects").exists());
    assert!(root.join("staging").exists());
    assert!(root.join("quarantine").exists());
    assert_eq!(store.stats().unwrap().object_count, 0);
    cleanup(&root);
}

#[test]
fn valid_object_put_commits_files_and_catalog_entries() {
    let root = temp_store_root("valid-object-put");
    let store = Store::open(root.clone()).unwrap();
    let fixture = native_fixture();
    let object_id = object_id(&fixture).to_string();

    stage_fixture(&store, "transfer-001", &fixture);
    let record = store
        .commit_put("transfer-001", Some(&fixture.target))
        .unwrap();

    assert_eq!(record.object_id, object_id);
    assert_eq!(record.state, ObjectState::Verified);
    let inspection = store.inspect_object(&object_id).unwrap();
    assert!(inspection.files_present);
    assert!(inspection.servable);
    assert_eq!(
        inspection.compatibility.model_hash.as_deref(),
        fixture.metadata["model_profile"]["model_hash"].as_str()
    );
    cleanup(&root);
}

#[test]
fn has_uses_catalog_plus_file_existence() {
    let root = temp_store_root("has-catalog-plus-files");
    let store = Store::open(root.clone()).unwrap();
    let fixture = native_fixture();
    let object_id = object_id(&fixture).to_string();

    stage_fixture(&store, "transfer-001", &fixture);
    store
        .commit_put("transfer-001", Some(&fixture.target))
        .unwrap();
    assert!(store.has_object(&object_id).unwrap());

    let payload_path = store
        .inspect_object(&object_id)
        .unwrap()
        .location
        .payload_path;
    fs::remove_file(payload_path).unwrap();

    assert!(!store.has_object(&object_id).unwrap());
    cleanup(&root);
}

#[test]
fn get_returns_exact_bytes() {
    let root = temp_store_root("get-exact-bytes");
    let store = Store::open(root.clone()).unwrap();
    let fixture = native_fixture();
    let object_id = object_id(&fixture).to_string();

    stage_fixture(&store, "transfer-001", &fixture);
    store
        .commit_put("transfer-001", Some(&fixture.target))
        .unwrap();
    let stored = store.get_object(&object_id).unwrap();

    assert_eq!(stored.metadata, fixture.metadata_bytes);
    assert_eq!(stored.payload, fixture.payload);
    cleanup(&root);
}

#[test]
fn invalid_object_does_not_create_servable_catalog_row() {
    let root = temp_store_root("invalid-object");
    let store = Store::open(root.clone()).unwrap();
    let mut fixture = native_fixture();
    let object_id = object_id(&fixture).to_string();
    fixture.payload[0] ^= 0xff;

    stage_fixture(&store, "transfer-001", &fixture);
    let error = store
        .commit_put("transfer-001", Some(&fixture.target))
        .unwrap_err();

    assert!(matches!(error, StoreError::Integrity(reason) if reason == "payload_hash_mismatch"));
    assert!(!store.has_object(&object_id).unwrap());
    assert!(store
        .list_objects(&ObjectListFilter::default())
        .unwrap()
        .is_empty());
    cleanup(&root);
}

#[test]
fn partial_staging_is_not_visible() {
    let root = temp_store_root("partial-staging");
    let store = Store::open(root.clone()).unwrap();
    let fixture = native_fixture();
    let object_id = object_id(&fixture).to_string();
    let mut manifest = chunk_bytes(&fixture.payload, TEST_CHUNK_SIZE).unwrap();
    manifest.object_id = Some(object_id.clone());

    store
        .begin_put("transfer-001", &fixture.metadata_bytes, &manifest)
        .unwrap();

    assert!(!store.has_object(&object_id).unwrap());
    assert!(store
        .list_objects(&ObjectListFilter::default())
        .unwrap()
        .is_empty());
    cleanup(&root);
}

#[test]
fn restart_store_and_object_remains_accessible() {
    let root = temp_store_root("restart-accessible");
    let fixture = native_fixture();
    let object_id = object_id(&fixture).to_string();
    {
        let store = Store::open(root.clone()).unwrap();
        stage_fixture(&store, "transfer-001", &fixture);
        store
            .commit_put("transfer-001", Some(&fixture.target))
            .unwrap();
    }

    let reopened = Store::open(root.clone()).unwrap();

    assert!(reopened.has_object(&object_id).unwrap());
    assert_eq!(reopened.get_payload(&object_id).unwrap(), fixture.payload);
    cleanup(&root);
}

#[test]
fn compatibility_fields_are_indexed_correctly() {
    let root = temp_store_root("compatibility-indexed");
    let store = Store::open(root.clone()).unwrap();
    let fixture = native_fixture();
    let object_id = object_id(&fixture).to_string();

    stage_fixture(&store, "transfer-001", &fixture);
    store
        .commit_put("transfer-001", Some(&fixture.target))
        .unwrap();
    let inspection = store.inspect_object(&object_id).unwrap();

    assert_eq!(
        inspection.compatibility.tokenizer_hash.as_deref(),
        fixture.metadata["model_profile"]["tokenizer_hash"].as_str()
    );
    assert_eq!(
        inspection.compatibility.config_hash.as_deref(),
        fixture.metadata["model_profile"]["config_hash"].as_str()
    );
    assert_eq!(inspection.compatibility.layer_id, Some(0));
    assert_eq!(inspection.compatibility.kv_block_id, Some(0));
    let rows = store
        .list_objects(&ObjectListFilter {
            model_hash: inspection.compatibility.model_hash.clone(),
            prefix_hash: inspection.compatibility.prefix_hash.clone(),
            ..ObjectListFilter::default()
        })
        .unwrap();
    assert_eq!(rows.len(), 1);
    assert_eq!(rows[0].object_id, object_id);
    cleanup(&root);
}

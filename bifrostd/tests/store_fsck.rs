use bifrostd::spool::Spool;
use bifrostd::store::{open_catalog, CompletenessState, FsckMode, FsckStatus, ObjectState, Store};
use bifrostd::transport::{chunk_bytes, iter_chunks, serve_listener};
use rusqlite::params;
use serde_json::Value;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::{SystemTime, UNIX_EPOCH};
use tokio::net::TcpListener;

const TEST_CHUNK_SIZE: usize = 256 * 1024;

struct Fixture {
    metadata_bytes: Vec<u8>,
    metadata: Value,
    payload: Vec<u8>,
}

struct Daemon {
    endpoint: String,
    task: tokio::task::JoinHandle<()>,
}

impl Drop for Daemon {
    fn drop(&mut self) {
        self.task.abort();
    }
}

fn repo_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("..")
}

fn native_fixture() -> Fixture {
    let root = repo_root().join("fixtures/native_valid");
    let metadata_bytes = fs::read(root.join("tiny_gpt_layer0_block0.meta.json")).unwrap();
    let metadata = serde_json::from_slice(&metadata_bytes).unwrap();
    let payload = fs::read(root.join("tiny_gpt_layer0_block0.payload.bin")).unwrap();
    Fixture {
        metadata_bytes,
        metadata,
        payload,
    }
}

fn object_id(fixture: &Fixture) -> &str {
    fixture.metadata["object_id"].as_str().unwrap()
}

fn temp_root(test_name: &str) -> PathBuf {
    let unique = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_nanos();
    std::env::temp_dir().join(format!(
        "bifrostd-store-fsck-{test_name}-{}-{unique}",
        std::process::id()
    ))
}

fn cleanup(path: &Path) {
    if path.exists() {
        fs::remove_dir_all(path).unwrap();
    }
}

fn put_fixture(store: &Store, transfer_id: &str, fixture: &Fixture) -> String {
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
    store.commit_put(transfer_id, None).unwrap().object_id
}

fn finding_types(store: &Store, mode: FsckMode) -> Vec<String> {
    store
        .fsck(mode)
        .unwrap()
        .findings
        .into_iter()
        .map(|finding| finding.finding_type)
        .collect()
}

fn remove_catalog_entry(root: &Path, object_id: &str) {
    let catalog = open_catalog(&root.join("catalog.sqlite")).unwrap();
    let conn = catalog.connection();
    conn.execute(
        "DELETE FROM object_access WHERE object_id = ?1",
        params![object_id],
    )
    .unwrap();
    conn.execute(
        "DELETE FROM object_compatibility WHERE object_id = ?1",
        params![object_id],
    )
    .unwrap();
    conn.execute(
        "DELETE FROM object_locations WHERE object_id = ?1",
        params![object_id],
    )
    .unwrap();
    conn.execute(
        "DELETE FROM objects WHERE object_id = ?1",
        params![object_id],
    )
    .unwrap();
}

fn insert_fake_catalog_entry(root: &Path, fake_id: &str, real_id: &str) {
    let catalog = open_catalog(&root.join("catalog.sqlite")).unwrap();
    let conn = catalog.connection();
    conn.execute(
        "INSERT INTO objects(
            object_id, object_type, schema_version, descriptor_hash, payload_hash,
            byte_length, state, created_at_unix_ms, committed_at_unix_ms,
            verified_at_unix_ms, last_accessed_unix_ms, access_count, pin_count,
            ttl_expires_at_unix_ms, quarantine_reason
        )
        SELECT ?1, object_type, schema_version, descriptor_hash, payload_hash,
               byte_length, state, created_at_unix_ms, committed_at_unix_ms,
               verified_at_unix_ms, last_accessed_unix_ms, access_count, pin_count,
               ttl_expires_at_unix_ms, quarantine_reason
        FROM objects WHERE object_id = ?2",
        params![fake_id, real_id],
    )
    .unwrap();
    conn.execute(
        "INSERT INTO object_compatibility
         SELECT ?1, model_hash, tokenizer_hash, config_hash, rope_config_hash, dtype,
                engine_name, engine_version, integration_name, kv_cache_format,
                prefix_hash, token_range_start, token_range_end, layer_id,
                kv_block_id, opaque_engine_key_hash
         FROM object_compatibility WHERE object_id = ?2",
        params![fake_id, real_id],
    )
    .unwrap();
    conn.execute(
        "INSERT INTO object_access(object_id) VALUES (?1)",
        params![fake_id],
    )
    .unwrap();
    conn.execute(
        "INSERT INTO object_locations(object_id, tier, meta_path, payload_path, bytes_on_disk)
         VALUES (?1, 'disk', '', '', 0)",
        params![fake_id],
    )
    .unwrap();
}

fn create_manifest(store: &Store, fixture: &Fixture) -> String {
    store
        .create_prefix_manifest(
            Some(
                fixture.metadata["model_profile"]["model_hash"]
                    .as_str()
                    .unwrap()
                    .to_string(),
            ),
            Some(
                fixture.metadata["model_profile"]["tokenizer_hash"]
                    .as_str()
                    .unwrap()
                    .to_string(),
            ),
            Some(
                fixture.metadata["model_profile"]["rope_config_hash"]
                    .as_str()
                    .unwrap()
                    .to_string(),
            ),
            fixture.metadata["prefix_profile"]["prefix_hash"]
                .as_str()
                .unwrap()
                .to_string(),
            fixture.metadata["native_tensor_profile"]["token_range"]["start"]
                .as_u64()
                .unwrap() as i64,
            fixture.metadata["native_tensor_profile"]["token_range"]["end"]
                .as_u64()
                .unwrap() as i64,
        )
        .unwrap()
        .manifest_id
}

async fn start_daemon(test_name: &str) -> (Daemon, PathBuf) {
    let root = temp_root(test_name);
    let spool = Spool::new(&root);
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let endpoint = listener.local_addr().unwrap().to_string();
    let task = tokio::spawn(async move {
        let _ = serve_listener(listener, spool).await;
    });
    (Daemon { endpoint, task }, root)
}

#[test]
fn clean_store_returns_clean() {
    let root = temp_root("clean");
    let store = Store::open(root.clone()).unwrap();
    let fixture = native_fixture();
    put_fixture(&store, "transfer-001", &fixture);

    let result = store.fsck(FsckMode::Check).unwrap();

    assert_eq!(result.status, FsckStatus::Clean);
    assert!(result.findings.is_empty());
    cleanup(&root);
}

#[test]
fn missing_payload_and_metadata_are_detected() {
    let root = temp_root("missing-files");
    let store = Store::open(root.clone()).unwrap();
    let fixture = native_fixture();
    let object_id = put_fixture(&store, "transfer-001", &fixture);
    let inspection = store.inspect_object(&object_id).unwrap();

    fs::remove_file(&inspection.location.payload_path).unwrap();
    assert!(finding_types(&store, FsckMode::Check)
        .contains(&"catalog_object_missing_payload_file".to_string()));

    fs::write(&inspection.location.payload_path, &fixture.payload).unwrap();
    fs::remove_file(&inspection.location.meta_path).unwrap();
    assert!(finding_types(&store, FsckMode::Check)
        .contains(&"catalog_object_missing_metadata_file".to_string()));
    cleanup(&root);
}

#[test]
fn corrupted_payload_hash_is_detected() {
    let root = temp_root("corrupt-payload");
    let store = Store::open(root.clone()).unwrap();
    let fixture = native_fixture();
    let object_id = put_fixture(&store, "transfer-001", &fixture);
    let payload_path = store
        .inspect_object(&object_id)
        .unwrap()
        .location
        .payload_path;
    let mut bytes = fs::read(&payload_path).unwrap();
    bytes[0] ^= 0xff;
    fs::write(payload_path, bytes).unwrap();

    assert!(finding_types(&store, FsckMode::Check).contains(&"payload_hash_mismatch".to_string()));
    cleanup(&root);
}

#[test]
fn object_id_mismatch_is_detected() {
    let root = temp_root("object-id-mismatch");
    let store = Store::open(root.clone()).unwrap();
    let fixture = native_fixture();
    let real_id = put_fixture(&store, "transfer-001", &fixture);
    let fake_id =
        "bifrost://object/blake3/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
    insert_fake_catalog_entry(&root, fake_id, &real_id);
    let layout = bifrostd::store::StoreLayout::new(&root);
    let fake_meta = layout.meta_path(fake_id).unwrap();
    let fake_payload = layout.payload_path(fake_id).unwrap();
    fs::create_dir_all(fake_meta.parent().unwrap()).unwrap();
    fs::write(&fake_meta, &fixture.metadata_bytes).unwrap();
    fs::write(&fake_payload, &fixture.payload).unwrap();
    let mut catalog = open_catalog(&root.join("catalog.sqlite")).unwrap();
    catalog
        .fsck_update_location(
            fake_id,
            &fake_meta.to_string_lossy(),
            &fake_payload.to_string_lossy(),
            (fixture.metadata_bytes.len() + fixture.payload.len()) as i64,
        )
        .unwrap();

    assert!(finding_types(&store, FsckMode::Check).contains(&"object_id_mismatch".to_string()));
    cleanup(&root);
}

#[test]
fn orphan_metadata_and_payload_are_detected_and_repair_imports_valid_pair() {
    let root = temp_root("orphan-import");
    let store = Store::open(root.clone()).unwrap();
    let fixture = native_fixture();
    let object_id = put_fixture(&store, "transfer-001", &fixture);
    remove_catalog_entry(&root, &object_id);

    let check = store.fsck(FsckMode::Check).unwrap();
    assert!(check
        .findings
        .iter()
        .any(|finding| finding.finding_type == "metadata_file_without_catalog_entry"));
    assert!(check
        .findings
        .iter()
        .any(|finding| finding.finding_type == "payload_file_without_catalog_entry"));

    let repair = store.fsck(FsckMode::Repair).unwrap();
    assert_eq!(repair.status, FsckStatus::Repaired);
    assert!(store.has_object(&object_id).unwrap());
    cleanup(&root);
}

#[test]
fn orphan_repair_rejects_valid_pair_in_wrong_committed_path() {
    let root = temp_root("orphan-wrong-path");
    let store = Store::open(root.clone()).unwrap();
    let fixture = native_fixture();
    let object_id = object_id(&fixture).to_string();
    let suffix = object_id
        .strip_prefix("bifrost://object/blake3/")
        .unwrap()
        .to_string();
    let wrong_dir = root.join("objects").join("ff").join("ff");
    fs::create_dir_all(&wrong_dir).unwrap();
    fs::write(
        wrong_dir.join(format!("{suffix}.meta.json")),
        &fixture.metadata_bytes,
    )
    .unwrap();
    fs::write(
        wrong_dir.join(format!("{suffix}.payload.bin")),
        &fixture.payload,
    )
    .unwrap();

    let repair = store.fsck(FsckMode::Repair).unwrap();

    assert!(repair
        .findings
        .iter()
        .any(|finding| finding.finding_type == "orphan_file_path_mismatch"));
    assert!(!store.has_object(&object_id).unwrap());
    cleanup(&root);
}

#[test]
fn abandoned_staging_detected_and_repair_removes_it() {
    let root = temp_root("staging");
    let store = Store::open(root.clone()).unwrap();
    let fixture = native_fixture();
    let mut manifest = chunk_bytes(&fixture.payload, TEST_CHUNK_SIZE).unwrap();
    manifest.object_id = Some(object_id(&fixture).to_string());
    store
        .begin_put("transfer-001", &fixture.metadata_bytes, &manifest)
        .unwrap();

    assert!(
        finding_types(&store, FsckMode::Check).contains(&"staging_transfer_abandoned".to_string())
    );
    let result = store.fsck(FsckMode::Repair).unwrap();
    assert!(result
        .mutations_applied
        .iter()
        .any(|mutation| mutation.mutation_type == "removed_abandoned_staging"));
    assert!(!root.join("staging/transfer-001").exists());
    cleanup(&root);
}

#[test]
fn quarantine_moves_corrupt_object_and_marks_not_servable() {
    let root = temp_root("quarantine");
    let store = Store::open(root.clone()).unwrap();
    let fixture = native_fixture();
    let object_id = put_fixture(&store, "transfer-001", &fixture);
    let payload_path = store
        .inspect_object(&object_id)
        .unwrap()
        .location
        .payload_path;
    let mut bytes = fs::read(&payload_path).unwrap();
    bytes[0] ^= 0xff;
    fs::write(payload_path, bytes).unwrap();

    let result = store.fsck(FsckMode::Quarantine).unwrap();

    assert_eq!(result.status, FsckStatus::Quarantined);
    assert!(!store.has_object(&object_id).unwrap());
    assert_eq!(
        store.inspect_object(&object_id).unwrap().record.state,
        ObjectState::Quarantined
    );
    assert!(root.join("quarantine").read_dir().unwrap().next().is_some());
    cleanup(&root);
}

#[test]
fn manifest_with_missing_member_is_reported_and_repair_marks_incomplete() {
    let root = temp_root("manifest-missing");
    let store = Store::open(root.clone()).unwrap();
    let fixture = native_fixture();
    let object_id = put_fixture(&store, "transfer-001", &fixture);
    let manifest_id = create_manifest(&store, &fixture);
    store
        .add_manifest_member(&manifest_id, &object_id, true)
        .unwrap();
    store.check_manifest_completeness(&manifest_id).unwrap();
    let payload_path = store
        .inspect_object(&object_id)
        .unwrap()
        .location
        .payload_path;
    fs::remove_file(payload_path).unwrap();

    assert!(finding_types(&store, FsckMode::Check)
        .contains(&"manifest_member_object_not_serveable".to_string()));
    store.fsck(FsckMode::Repair).unwrap();
    assert_eq!(
        store
            .get_manifest(&manifest_id)
            .unwrap()
            .manifest
            .completeness_state,
        CompletenessState::Incomplete
    );
    cleanup(&root);
}

#[test]
fn fsck_check_mode_does_not_mutate() {
    let root = temp_root("check-no-mutate");
    let store = Store::open(root.clone()).unwrap();
    let fixture = native_fixture();
    let object_id = put_fixture(&store, "transfer-001", &fixture);
    let payload_path = store
        .inspect_object(&object_id)
        .unwrap()
        .location
        .payload_path;
    fs::remove_file(payload_path).unwrap();

    let result = store.fsck(FsckMode::Check).unwrap();

    assert!(result.mutations_applied.is_empty());
    assert_eq!(
        store.inspect_object(&object_id).unwrap().record.state,
        ObjectState::Verified
    );
    cleanup(&root);
}

#[tokio::test]
async fn cli_fsck_json_parses() {
    let (daemon, root) = start_daemon("cli-json").await;

    let endpoint = daemon.endpoint.clone();
    let output = tokio::task::spawn_blocking(move || {
        Command::new(env!("CARGO_BIN_EXE_bifrost-store"))
            .args(["fsck", "--endpoint", &endpoint, "--json"])
            .output()
            .unwrap()
    })
    .await
    .unwrap();

    assert!(
        output.status.success(),
        "stderr={}",
        String::from_utf8_lossy(&output.stderr)
    );
    let value: Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(value["status"], "clean");
    cleanup(&root);
}

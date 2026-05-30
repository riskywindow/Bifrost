use bifrostd::spool::Spool;
use bifrostd::store::{
    open_catalog, CompletenessState, EvictionPolicy, EvictionRequest, ManifestListFilter, Store,
    StoreLayout,
};
use bifrostd::transport::{
    check_manifest, chunk_bytes, create_prefix_manifest, manifest_add_member, put_object,
    query_store_objects, serve_listener, StoreObjectFilter, DEFAULT_CHUNK_SIZE,
};
use serde_json::Value;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::{SystemTime, UNIX_EPOCH};
use tokio::net::TcpListener;

const TEST_CHUNK_SIZE: usize = 256 * 1024;
const NOW_MS: i64 = 2_000_000;

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

fn model_hash(fixture: &Fixture) -> &str {
    fixture.metadata["model_profile"]["model_hash"]
        .as_str()
        .unwrap()
}

fn tokenizer_hash(fixture: &Fixture) -> &str {
    fixture.metadata["model_profile"]["tokenizer_hash"]
        .as_str()
        .unwrap()
}

fn rope_config_hash(fixture: &Fixture) -> &str {
    fixture.metadata["model_profile"]["rope_config_hash"]
        .as_str()
        .unwrap()
}

fn prefix_hash(fixture: &Fixture) -> &str {
    fixture.metadata["prefix_profile"]["prefix_hash"]
        .as_str()
        .unwrap()
}

fn token_range_start(fixture: &Fixture) -> i64 {
    fixture.metadata["native_tensor_profile"]["token_range"]["start"]
        .as_u64()
        .unwrap() as i64
}

fn token_range_end(fixture: &Fixture) -> i64 {
    fixture.metadata["native_tensor_profile"]["token_range"]["end"]
        .as_u64()
        .unwrap() as i64
}

fn temp_root(test_name: &str) -> PathBuf {
    let unique = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_nanos();
    std::env::temp_dir().join(format!(
        "bifrostd-store-manifests-{test_name}-{}-{unique}",
        std::process::id()
    ))
}

fn cleanup(path: &Path) {
    if path.exists() {
        fs::remove_dir_all(path).unwrap();
    }
}

fn put_fixture_direct(store: &Store, transfer_id: &str, fixture: &Fixture) -> String {
    let mut manifest = chunk_bytes(&fixture.payload, TEST_CHUNK_SIZE).unwrap();
    manifest.object_id = Some(object_id(fixture).to_string());
    store
        .begin_put(transfer_id, &fixture.metadata_bytes, &manifest)
        .unwrap();
    for chunk in bifrostd::transport::iter_chunks(&fixture.payload, TEST_CHUNK_SIZE).unwrap() {
        store
            .write_chunk(transfer_id, chunk.info.chunk_index, chunk.bytes)
            .unwrap();
    }
    store.commit_put(transfer_id, None).unwrap().object_id
}

fn create_manifest(store: &Store, fixture: &Fixture) -> String {
    store
        .create_prefix_manifest(
            Some(model_hash(fixture).to_string()),
            Some(tokenizer_hash(fixture).to_string()),
            Some(rope_config_hash(fixture).to_string()),
            prefix_hash(fixture).to_string(),
            token_range_start(fixture),
            token_range_end(fixture),
        )
        .unwrap()
        .manifest_id
}

fn eviction_request() -> EvictionRequest {
    EvictionRequest {
        policy: EvictionPolicy::Lru,
        target_bytes: None,
        max_objects: Some(1),
        dry_run: false,
        now_unix_ms: NOW_MS,
    }
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

async fn put_fixture(endpoint: &str, fixture: &Fixture) -> String {
    let put = put_object(
        endpoint,
        fixture.metadata_bytes.clone(),
        fixture.payload.clone(),
        DEFAULT_CHUNK_SIZE,
        None,
    )
    .await
    .unwrap();
    assert!(put.accepted, "PUT rejected: {}", put.reason);
    put.object_id
}

#[test]
fn create_manifest_and_add_valid_member() {
    let root = temp_root("create-add");
    let store = Store::open(root.clone()).unwrap();
    let fixture = native_fixture();
    let object_id = put_fixture_direct(&store, "transfer-001", &fixture);
    let manifest_id = create_manifest(&store, &fixture);

    let member = store
        .add_manifest_member(&manifest_id, &object_id, true)
        .unwrap();

    assert_eq!(member.object_id, object_id);
    assert!(member.required);
    let inspection = store.get_manifest(&manifest_id).unwrap();
    assert_eq!(inspection.members.len(), 1);
    cleanup(&root);
}

#[test]
fn adding_missing_object_is_rejected() {
    let root = temp_root("missing-member");
    let store = Store::open(root.clone()).unwrap();
    let fixture = native_fixture();
    let manifest_id = create_manifest(&store, &fixture);

    let error = store
        .add_manifest_member(&manifest_id, "bifrost://object/blake3/missing", true)
        .unwrap_err();

    assert!(error.to_string().contains("object not found"));
    cleanup(&root);
}

#[test]
fn adding_object_with_wrong_prefix_is_rejected() {
    let root = temp_root("wrong-prefix");
    let store = Store::open(root.clone()).unwrap();
    let fixture = native_fixture();
    let object_id = put_fixture_direct(&store, "transfer-001", &fixture);
    let manifest = store
        .create_prefix_manifest(
            Some(model_hash(&fixture).to_string()),
            None,
            None,
            "blake3:wrong-prefix".to_string(),
            token_range_start(&fixture),
            token_range_end(&fixture),
        )
        .unwrap();

    let error = store
        .add_manifest_member(&manifest.manifest_id, &object_id, true)
        .unwrap_err();

    assert!(error.to_string().contains("prefix_hash mismatch"));
    cleanup(&root);
}

#[test]
fn completeness_tracks_present_and_evicted_required_members() {
    let root = temp_root("completeness");
    let store = Store::open(root.clone()).unwrap();
    let fixture = native_fixture();
    let object_id = put_fixture_direct(&store, "transfer-001", &fixture);
    let manifest_id = create_manifest(&store, &fixture);
    store
        .add_manifest_member(&manifest_id, &object_id, true)
        .unwrap();

    let complete = store.check_manifest_completeness(&manifest_id).unwrap();
    assert_eq!(complete.completeness_state, CompletenessState::Complete);
    assert!(complete.missing.is_empty());

    let eviction = store.evict(eviction_request()).unwrap();
    assert_eq!(eviction.evicted[0].object_id, object_id);
    let incomplete = store.check_manifest_completeness(&manifest_id).unwrap();
    assert_eq!(incomplete.completeness_state, CompletenessState::Incomplete);
    assert_eq!(incomplete.missing.len(), 1);
    cleanup(&root);
}

#[test]
fn expected_coverage_reports_missing_layer_block_members() {
    let root = temp_root("expected-coverage");
    let store = Store::open(root.clone()).unwrap();
    let fixture = native_fixture();
    let object_id = put_fixture_direct(&store, "transfer-001", &fixture);
    let manifest_id = create_manifest(&store, &fixture);
    store
        .add_manifest_member(&manifest_id, &object_id, true)
        .unwrap();

    let report = store
        .missing_manifest_members(
            &manifest_id,
            Some(bifrostd::store::ManifestExpectedCoverage {
                expected_layer_count: Some(1),
                expected_block_count: Some(2),
                expected_members: None,
            }),
        )
        .unwrap();

    assert_eq!(report.missing.len(), 1);
    assert_eq!(report.missing[0].reason, "expected_member_missing");
    cleanup(&root);
}

#[test]
fn pinned_manifest_protects_members_and_unpin_allows_eviction() {
    let root = temp_root("pin-eviction");
    let store = Store::open(root.clone()).unwrap();
    let fixture = native_fixture();
    let object_id = put_fixture_direct(&store, "transfer-001", &fixture);
    let manifest_id = create_manifest(&store, &fixture);
    store
        .add_manifest_member(&manifest_id, &object_id, true)
        .unwrap();

    store.pin_manifest(&manifest_id).unwrap();
    assert_eq!(
        store.inspect_object(&object_id).unwrap().record.pin_count,
        1
    );
    let protected = store.evict(eviction_request()).unwrap();
    assert!(protected.evicted.is_empty());
    assert!(store.has_object(&object_id).unwrap());

    store.unpin_manifest(&manifest_id).unwrap();
    assert_eq!(
        store.inspect_object(&object_id).unwrap().record.pin_count,
        0
    );
    let evicted = store.evict(eviction_request()).unwrap();
    assert_eq!(evicted.evicted[0].object_id, object_id);
    cleanup(&root);
}

#[test]
fn event_log_records_manifest_operations_and_prefix_list() {
    let root = temp_root("events");
    let store = Store::open(root.clone()).unwrap();
    let fixture = native_fixture();
    let object_id = put_fixture_direct(&store, "transfer-001", &fixture);
    let manifest_id = create_manifest(&store, &fixture);
    store
        .add_manifest_member(&manifest_id, &object_id, true)
        .unwrap();
    store.check_manifest_completeness(&manifest_id).unwrap();

    let catalog = open_catalog(&StoreLayout::new(&root).paths().catalog).unwrap();
    let events = catalog.store_events().unwrap();
    assert!(events
        .iter()
        .any(|event| event.event_type == "manifest_created"));
    assert!(events
        .iter()
        .any(|event| event.event_type == "manifest_member_added"));
    let manifests = store
        .list_manifests(&ManifestListFilter {
            prefix_hash: Some(prefix_hash(&fixture).to_string()),
            ..ManifestListFilter::default()
        })
        .unwrap();
    assert_eq!(manifests[0].manifest_id, manifest_id);
    cleanup(&root);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn daemon_client_and_cli_manifest_create_add_check_work() {
    let (daemon, root) = start_daemon("daemon-cli").await;
    let fixture = native_fixture();
    let object_id = put_fixture(&daemon.endpoint, &fixture).await;

    let create = create_prefix_manifest(
        &daemon.endpoint,
        Some(model_hash(&fixture).to_string()),
        Some(tokenizer_hash(&fixture).to_string()),
        Some(rope_config_hash(&fixture).to_string()),
        prefix_hash(&fixture).to_string(),
        token_range_start(&fixture),
        token_range_end(&fixture),
    )
    .await
    .unwrap();
    assert!(create.reason.is_empty());
    let manifest_id = create.response.manifest.unwrap().manifest.manifest_id;

    let add = manifest_add_member(&daemon.endpoint, &manifest_id, &object_id, true)
        .await
        .unwrap();
    assert!(add.reason.is_empty());
    let check = check_manifest(&daemon.endpoint, &manifest_id)
        .await
        .unwrap();
    assert_eq!(
        check.response.completeness.unwrap().completeness_state,
        CompletenessState::Complete
    );

    let cli_check = Command::new(env!("CARGO_BIN_EXE_bifrost-store"))
        .args([
            "manifest",
            "check",
            "--endpoint",
            &daemon.endpoint,
            "--manifest-id",
            &manifest_id,
            "--json",
        ])
        .output()
        .unwrap();
    assert!(
        cli_check.status.success(),
        "{}",
        String::from_utf8_lossy(&cli_check.stderr)
    );
    let check_json: Value = serde_json::from_slice(&cli_check.stdout).unwrap();
    assert_eq!(check_json["completeness"]["completeness_state"], "complete");

    let query = query_store_objects(
        &daemon.endpoint,
        StoreObjectFilter {
            prefix_hash: Some(prefix_hash(&fixture).to_string()),
            ..StoreObjectFilter::default()
        },
    )
    .await
    .unwrap();
    assert_eq!(query.objects[0].object_id, object_id);
    cleanup(&root);
}

use bifrostd::spool::Spool;
use bifrostd::store::{open_catalog, ObjectState, Store, StoreError, StoreLayout};
use bifrostd::transport::{
    chunk_bytes, get_object, inspect_store_object, pin_object as client_pin_object, put_object,
    quarantine_object, serve_listener, unpin_object as client_unpin_object, DEFAULT_CHUNK_SIZE,
};
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
    target: Value,
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
    let target =
        serde_json::from_slice(&fs::read(root.join("target_profile.json")).unwrap()).unwrap();
    Fixture {
        metadata_bytes,
        metadata,
        payload,
        target,
    }
}

fn temp_root(test_name: &str) -> PathBuf {
    let unique = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_nanos();
    std::env::temp_dir().join(format!(
        "bifrostd-store-pinning-{test_name}-{}-{unique}",
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
    for chunk in bifrostd::transport::iter_chunks(&fixture.payload, TEST_CHUNK_SIZE).unwrap() {
        store
            .write_chunk(transfer_id, chunk.info.chunk_index, chunk.bytes)
            .unwrap();
    }
}

fn put_fixture_direct(store: &Store, fixture: &Fixture) -> String {
    stage_fixture(store, "transfer-001", fixture);
    store
        .commit_put("transfer-001", Some(&fixture.target))
        .unwrap()
        .object_id
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
fn store_pin_unpin_ttl_and_lifecycle_events_are_recorded() {
    let root = temp_root("store-api");
    let store = Store::open(root.clone()).unwrap();
    let fixture = native_fixture();
    let object_id = put_fixture_direct(&store, &fixture);

    store.pin_object(&object_id).unwrap();
    let reopened = Store::open(root.clone()).unwrap();
    let durable_pin = reopened.inspect_object(&object_id).unwrap();
    assert_eq!(durable_pin.record.pin_count, 1);
    assert_eq!(durable_pin.record.state, ObjectState::Pinned);

    store.pin_object(&object_id).unwrap();
    let pinned = store.inspect_object(&object_id).unwrap();
    assert_eq!(pinned.record.pin_count, 2);
    assert_eq!(pinned.record.state, ObjectState::Pinned);
    assert!(pinned.servable);
    assert!(store.get_object(&object_id).is_ok());

    store.unpin_object(&object_id).unwrap();
    let once_unpinned = store.inspect_object(&object_id).unwrap();
    assert_eq!(once_unpinned.record.pin_count, 1);
    assert_eq!(once_unpinned.record.state, ObjectState::Pinned);

    store.unpin_object(&object_id).unwrap();
    store.unpin_object(&object_id).unwrap();
    let unpinned = store.inspect_object(&object_id).unwrap();
    assert_eq!(unpinned.record.pin_count, 0);
    assert_eq!(unpinned.record.state, ObjectState::Verified);

    store.set_ttl(&object_id, 1_900_000_000_000).unwrap();
    assert_eq!(
        store
            .inspect_object(&object_id)
            .unwrap()
            .record
            .ttl_expires_at_unix_ms,
        Some(1_900_000_000_000)
    );
    store.clear_ttl(&object_id).unwrap();
    assert_eq!(
        store
            .inspect_object(&object_id)
            .unwrap()
            .record
            .ttl_expires_at_unix_ms,
        None
    );

    let missing = store
        .pin_object("bifrost://object/blake3/missing")
        .unwrap_err();
    assert!(matches!(missing, StoreError::NotFound(_)));

    let catalog_path = StoreLayout::new(&root).paths().catalog;
    let catalog = open_catalog(&catalog_path).unwrap();
    let event_types = catalog
        .store_events()
        .unwrap()
        .into_iter()
        .map(|event| event.event_type)
        .collect::<Vec<_>>();
    assert!(event_types.contains(&"object_pinned".to_string()));
    assert!(event_types.contains(&"object_unpinned".to_string()));
    assert!(event_types.contains(&"object_ttl_set".to_string()));
    assert!(event_types.contains(&"object_ttl_cleared".to_string()));
    cleanup(&root);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn daemon_protocol_and_cli_pin_unpin_quarantine() {
    let (daemon, root) = start_daemon("daemon-cli").await;
    let fixture = native_fixture();
    let object_id = put_fixture(&daemon.endpoint, &fixture).await;

    let pin = client_pin_object(&daemon.endpoint, &object_id)
        .await
        .unwrap();
    assert!(pin.accepted, "pin failed: {}", pin.reason);
    let inspect = inspect_store_object(&daemon.endpoint, &object_id)
        .await
        .unwrap();
    assert!(inspect.found);
    let object = inspect.response.object.unwrap();
    assert_eq!(object.pin_count, 1);
    assert_eq!(object.state, "pinned");

    let unpin_cli = Command::new(env!("CARGO_BIN_EXE_bifrost-store"))
        .args([
            "unpin",
            "--endpoint",
            &daemon.endpoint,
            "--object-id",
            &object_id,
        ])
        .output()
        .unwrap();
    assert!(
        unpin_cli.status.success(),
        "{}",
        String::from_utf8_lossy(&unpin_cli.stderr)
    );
    let inspect = Command::new(env!("CARGO_BIN_EXE_bifrost-store"))
        .args([
            "inspect",
            "--endpoint",
            &daemon.endpoint,
            "--object-id",
            &object_id,
            "--json",
        ])
        .output()
        .unwrap();
    assert!(inspect.status.success());
    let inspect_json: Value = serde_json::from_slice(&inspect.stdout).unwrap();
    assert_eq!(inspect_json["object"]["pin_count"], 0);
    assert_eq!(inspect_json["object"]["state"], "verified");

    let pin_cli = Command::new(env!("CARGO_BIN_EXE_bifrost-store"))
        .args([
            "pin",
            "--endpoint",
            &daemon.endpoint,
            "--object-id",
            &object_id,
        ])
        .output()
        .unwrap();
    assert!(pin_cli.status.success());
    let inspect = inspect_store_object(&daemon.endpoint, &object_id)
        .await
        .unwrap();
    assert_eq!(inspect.response.object.unwrap().pin_count, 1);

    let quarantine = quarantine_object(&daemon.endpoint, &object_id, "test_quarantine")
        .await
        .unwrap();
    assert!(
        quarantine.accepted,
        "quarantine failed: {}",
        quarantine.reason
    );
    let get = get_object(&daemon.endpoint, &object_id, DEFAULT_CHUNK_SIZE)
        .await
        .unwrap();
    assert!(!get.found);
    assert_eq!(get.reason, "not_found");

    let missing_pin = client_pin_object(&daemon.endpoint, "bifrost://object/blake3/missing")
        .await
        .unwrap();
    assert!(!missing_pin.accepted);
    assert!(missing_pin.reason.contains("object not found"));

    let unpin = client_unpin_object(&daemon.endpoint, &object_id)
        .await
        .unwrap();
    assert!(unpin.accepted);
    cleanup(&root);
}

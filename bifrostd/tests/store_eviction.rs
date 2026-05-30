use bifrostd::cache::{compute_object_id, compute_object_identity, compute_payload_hash};
use bifrostd::spool::Spool;
use bifrostd::store::{
    open_catalog, DiskTier, EvictionPolicy, EvictionRequest, ObjectCompatibility, ObjectLocation,
    ObjectRecord, ObjectState, Store, StoreLayout,
};
use bifrostd::transport::{
    chunk_bytes, evict_store, get_object, put_object, serve_listener, store_stats,
    StoreEvictRequest, DEFAULT_CHUNK_SIZE,
};
use serde_json::Value;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::{SystemTime, UNIX_EPOCH};
use tokio::net::TcpListener;

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

fn opaque_fixture() -> Fixture {
    let root = repo_root().join("fixtures/opaque_valid");
    let metadata_bytes = fs::read(root.join("lmcache_blob.meta.json")).unwrap();
    let metadata = serde_json::from_slice(&metadata_bytes).unwrap();
    let payload = fs::read(root.join("lmcache_blob.payload.bin")).unwrap();
    Fixture {
        metadata_bytes,
        metadata,
        payload,
    }
}

fn variant_fixture(seed: u8) -> Fixture {
    let base = native_fixture();
    let mut metadata = base.metadata;
    let mut payload = base.payload;
    let last = payload.last_mut().unwrap();
    *last = last.wrapping_add(seed);
    let payload_hash = compute_payload_hash(&payload);
    metadata["payload_profile"]["byte_length"] = Value::Number((payload.len() as u64).into());
    metadata["integrity"]["chunk_size_bytes"] = Value::Number((payload.len() as u64).into());
    metadata["integrity"]["chunk_hashes"] = Value::Array(vec![Value::String(payload_hash.clone())]);
    metadata["integrity"]["payload_hash"] = Value::String(payload_hash);
    let identity = compute_object_identity(&metadata, &payload).unwrap();
    metadata["object_id"] = Value::String(identity.object_id);
    metadata["integrity"]["payload_hash"] = Value::String(identity.payload_hash);
    metadata["integrity"]["descriptor_hash"] = Value::String(identity.descriptor_hash);
    let metadata_bytes = serde_json::to_vec(&metadata).unwrap();
    Fixture {
        metadata_bytes,
        metadata,
        payload,
    }
}

fn temp_root(test_name: &str) -> PathBuf {
    let unique = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_nanos();
    std::env::temp_dir().join(format!(
        "bifrostd-store-eviction-{test_name}-{}-{unique}",
        std::process::id()
    ))
}

fn cleanup(path: &Path) {
    if path.exists() {
        fs::remove_dir_all(path).unwrap();
    }
}

fn object_id(index: u8) -> String {
    let descriptor_hash = format!("blake3:{index:064x}");
    let payload_hash = format!("blake3:{:064x}", index as u16 + 1);
    compute_object_id(&descriptor_hash, &payload_hash)
}

fn record(object_id: &str, bytes: i64) -> ObjectRecord {
    ObjectRecord {
        object_id: object_id.to_string(),
        object_type: "native_kv_page".to_string(),
        schema_version: "bifrost.kv_object.v1".to_string(),
        descriptor_hash: format!("descriptor-{object_id}"),
        payload_hash: format!("payload-{object_id}"),
        byte_length: bytes,
        state: ObjectState::Committed,
        created_at_unix_ms: 1_000,
        committed_at_unix_ms: Some(1_100),
        verified_at_unix_ms: None,
        last_accessed_unix_ms: None,
        access_count: 0,
        pin_count: 0,
        ttl_expires_at_unix_ms: None,
        quarantine_reason: None,
    }
}

fn compatibility(object_id: &str) -> ObjectCompatibility {
    ObjectCompatibility {
        object_id: object_id.to_string(),
        model_hash: Some("model-a".to_string()),
        tokenizer_hash: Some("tokenizer-a".to_string()),
        config_hash: Some("config-a".to_string()),
        rope_config_hash: Some("rope-a".to_string()),
        dtype: Some("bf16".to_string()),
        engine_name: Some("bifrost-test-engine".to_string()),
        engine_version: Some("1".to_string()),
        integration_name: Some("bifrost-test".to_string()),
        kv_cache_format: Some("native".to_string()),
        prefix_hash: Some("prefix-a".to_string()),
        token_range_start: Some(0),
        token_range_end: Some(128),
        layer_id: Some(0),
        kv_block_id: Some(0),
        opaque_engine_key_hash: None,
    }
}

fn insert_fake_object(
    root: &Path,
    index: u8,
    bytes_on_disk: i64,
    last_accessed_unix_ms: Option<i64>,
) -> String {
    let object_id = object_id(index);
    let disk = DiskTier::new(root);
    let location = disk.location_for(&object_id, bytes_on_disk).unwrap();
    write_fake_files(&location);
    let mut catalog = open_catalog(&StoreLayout::new(root).paths().catalog).unwrap();
    catalog
        .insert_committed_object(
            &record(&object_id, bytes_on_disk),
            &location,
            &compatibility(&object_id),
        )
        .unwrap();
    catalog
        .transition_object_state(&object_id, ObjectState::Verified, None)
        .unwrap();
    if let Some(last_accessed) = last_accessed_unix_ms {
        catalog
            .connection()
            .execute(
                "UPDATE objects SET last_accessed_unix_ms = ?2 WHERE object_id = ?1",
                rusqlite::params![object_id, last_accessed],
            )
            .unwrap();
    }
    object_id
}

fn write_fake_files(location: &ObjectLocation) {
    let meta_path = PathBuf::from(&location.meta_path);
    let payload_path = PathBuf::from(&location.payload_path);
    fs::create_dir_all(meta_path.parent().unwrap()).unwrap();
    fs::write(meta_path, b"metadata").unwrap();
    fs::write(payload_path, b"payload").unwrap();
}

fn put_fixture_direct(store: &Store, transfer_id: &str, fixture: &Fixture) -> String {
    let mut manifest = chunk_bytes(&fixture.payload, DEFAULT_CHUNK_SIZE).unwrap();
    manifest.object_id = Some(fixture.metadata["object_id"].as_str().unwrap().to_string());
    store
        .begin_put(transfer_id, &fixture.metadata_bytes, &manifest)
        .unwrap();
    for chunk in bifrostd::transport::iter_chunks(&fixture.payload, DEFAULT_CHUNK_SIZE).unwrap() {
        store
            .write_chunk(transfer_id, chunk.info.chunk_index, chunk.bytes)
            .unwrap();
    }
    store.commit_put(transfer_id, None).unwrap().object_id
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

fn request(policy: EvictionPolicy) -> EvictionRequest {
    EvictionRequest {
        policy,
        target_bytes: None,
        max_objects: Some(1),
        dry_run: false,
        now_unix_ms: NOW_MS,
    }
}

#[test]
fn lru_evicts_oldest_unpinned_object() {
    let root = temp_root("lru");
    let store = Store::open(root.clone()).unwrap();
    let old = insert_fake_object(&root, 1, 100, Some(100));
    let new = insert_fake_object(&root, 2, 100, Some(200));

    let report = store.evict(request(EvictionPolicy::Lru)).unwrap();

    assert_eq!(report.evicted[0].object_id, old);
    assert_eq!(
        store.inspect_object(&old).unwrap_err().to_string(),
        format!("object not found: {old}")
    );
    assert_eq!(
        store.inspect_object(&new).unwrap().record.state,
        ObjectState::Verified
    );
    cleanup(&root);
}

#[test]
fn size_aware_lru_uses_documented_score() {
    let root = temp_root("size-aware");
    let store = Store::open(root.clone()).unwrap();
    let old_small = insert_fake_object(&root, 3, 10, Some(NOW_MS - 900));
    let newer_large = insert_fake_object(&root, 4, 200, Some(NOW_MS - 100));

    let mut eviction = request(EvictionPolicy::SizeAwareLru);
    eviction.max_objects = None;
    eviction.dry_run = true;
    let report = store.evict(eviction).unwrap();

    assert_eq!(report.candidates[0].object_id, newer_large);
    assert!(report.candidates[0].eviction_score > report.candidates[1].eviction_score);
    let mut eviction = request(EvictionPolicy::SizeAwareLru);
    eviction.max_objects = Some(1);
    let report = store.evict(eviction).unwrap();
    assert_eq!(report.evicted[0].object_id, newer_large);
    assert_eq!(
        store.inspect_object(&old_small).unwrap().record.state,
        ObjectState::Verified
    );
    cleanup(&root);
}

#[test]
fn ttl_expired_evicts_only_expired_objects() {
    let root = temp_root("ttl");
    let store = Store::open(root.clone()).unwrap();
    let expired = insert_fake_object(&root, 5, 100, Some(100));
    let future = insert_fake_object(&root, 6, 100, Some(100));
    store.set_ttl(&expired, NOW_MS - 1).unwrap();
    store.set_ttl(&future, NOW_MS + 1).unwrap();

    let mut eviction = request(EvictionPolicy::TtlExpired);
    eviction.max_objects = None;
    let report = store.evict(eviction).unwrap();

    assert_eq!(report.evicted.len(), 1);
    assert_eq!(report.evicted[0].object_id, expired);
    assert_eq!(
        store.inspect_object(&future).unwrap().record.state,
        ObjectState::Verified
    );
    cleanup(&root);
}

#[test]
fn pinned_and_quarantined_objects_are_not_eviction_candidates() {
    let root = temp_root("protected");
    let store = Store::open(root.clone()).unwrap();
    let pinned = insert_fake_object(&root, 7, 100, Some(100));
    let quarantined = insert_fake_object(&root, 8, 100, Some(50));
    let eligible = insert_fake_object(&root, 9, 100, Some(200));
    store.pin_object(&pinned).unwrap();
    store.mark_quarantined(&quarantined, "test").unwrap();

    let mut eviction = request(EvictionPolicy::Lru);
    eviction.max_objects = None;
    eviction.dry_run = true;
    let report = store.evict(eviction).unwrap();

    assert_eq!(
        report
            .candidates
            .iter()
            .map(|candidate| candidate.object_id.as_str())
            .collect::<Vec<_>>(),
        vec![eligible.as_str()]
    );
    assert_eq!(report.protected_pinned_count, 1);
    assert!(!store.has_object(&quarantined).unwrap());
    cleanup(&root);
}

#[test]
fn dry_run_reports_candidates_without_deletion() {
    let root = temp_root("dry-run");
    let store = Store::open(root.clone()).unwrap();
    let object_id = insert_fake_object(&root, 10, 100, Some(100));

    let mut eviction = request(EvictionPolicy::Lru);
    eviction.dry_run = true;
    let report = store.evict(eviction).unwrap();

    assert_eq!(report.candidates[0].object_id, object_id);
    assert!(report.evicted.is_empty());
    assert_eq!(
        store.inspect_object(&object_id).unwrap().record.state,
        ObjectState::Verified
    );
    cleanup(&root);
}

#[test]
fn target_byte_eviction_stops_when_target_reached_and_updates_stats_events() {
    let root = temp_root("target");
    let store = Store::open(root.clone()).unwrap();
    let first = insert_fake_object(&root, 11, 100, Some(100));
    let second = insert_fake_object(&root, 12, 100, Some(200));
    let third = insert_fake_object(&root, 13, 100, Some(300));

    let mut eviction = request(EvictionPolicy::Lru);
    eviction.max_objects = None;
    eviction.target_bytes = Some(100);
    let report = store.evict(eviction).unwrap();

    assert!(report.target_reached);
    assert_eq!(report.evicted.len(), 2);
    assert_eq!(report.evicted[0].object_id, first);
    assert_eq!(report.evicted[1].object_id, second);
    assert_eq!(store.stats().unwrap().total_bytes_on_disk, 100);
    assert_eq!(
        store.inspect_object(&third).unwrap().record.state,
        ObjectState::Verified
    );
    let events = open_catalog(&StoreLayout::new(&root).paths().catalog)
        .unwrap()
        .store_events()
        .unwrap();
    assert!(events
        .iter()
        .any(|event| event.event_type == "object_evicted"));
    cleanup(&root);
}

#[test]
fn file_deletion_failure_marks_missing_and_does_not_report_success() {
    let root = temp_root("delete-failure");
    let store = Store::open(root.clone()).unwrap();
    let object_id = insert_fake_object(&root, 14, 100, Some(100));
    let payload_path = StoreLayout::new(&root).payload_path(&object_id).unwrap();
    fs::remove_file(&payload_path).unwrap();
    fs::create_dir(&payload_path).unwrap();

    let report = store.evict(request(EvictionPolicy::Lru)).unwrap();

    assert!(report.evicted.is_empty());
    assert_eq!(report.failures.len(), 1);
    assert_eq!(
        open_catalog(&StoreLayout::new(&root).paths().catalog)
            .unwrap()
            .get_object_record(&object_id)
            .unwrap()
            .unwrap()
            .state,
        ObjectState::Missing
    );
    cleanup(&root);
}

#[test]
fn evicted_real_object_cannot_be_served_through_get() {
    let root = temp_root("get-miss");
    let store = Store::open(root.clone()).unwrap();
    let object_id = put_fixture_direct(&store, "real-001", &native_fixture());

    let report = store.evict(request(EvictionPolicy::Lru)).unwrap();

    assert_eq!(report.evicted[0].object_id, object_id);
    assert!(!store.has_object(&object_id).unwrap());
    assert!(store.get_object(&object_id).is_err());
    cleanup(&root);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn daemon_and_cli_evict_dry_run_and_apply() {
    let (daemon, root) = start_daemon("daemon-cli").await;
    let first = put_fixture(&daemon.endpoint, &native_fixture()).await;
    let second = put_fixture(&daemon.endpoint, &opaque_fixture()).await;

    let dry_run = Command::new(env!("CARGO_BIN_EXE_bifrost-store"))
        .args([
            "evict",
            "--endpoint",
            &daemon.endpoint,
            "--policy",
            "lru",
            "--max-objects",
            "1",
            "--dry-run",
            "--json",
        ])
        .output()
        .unwrap();
    assert!(
        dry_run.status.success(),
        "{}",
        String::from_utf8_lossy(&dry_run.stderr)
    );
    let dry_json: Value = serde_json::from_slice(&dry_run.stdout).unwrap();
    assert_eq!(dry_json["dry_run"], true);
    assert_eq!(dry_json["candidates"].as_array().unwrap().len(), 1);
    assert!(
        get_object(&daemon.endpoint, &first, DEFAULT_CHUNK_SIZE)
            .await
            .unwrap()
            .found
    );

    let apply = evict_store(
        &daemon.endpoint,
        StoreEvictRequest {
            policy: "lru".to_string(),
            target_bytes: None,
            max_objects: Some(1),
            dry_run: false,
            now_unix_ms: Some(NOW_MS),
        },
    )
    .await
    .unwrap();
    assert!(apply.reason.is_empty());
    assert_eq!(apply.report.evicted.len(), 1);
    let evicted = &apply.report.evicted[0].object_id;
    assert!(
        !get_object(&daemon.endpoint, evicted, DEFAULT_CHUNK_SIZE)
            .await
            .unwrap()
            .found
    );
    let stats = store_stats(&daemon.endpoint).await.unwrap();
    assert_eq!(stats.stats.evicted_count, 1);
    assert!(first == *evicted || second == *evicted);
    cleanup(&root);
}

#[test]
fn target_eviction_with_real_variants_preserves_remaining_servability() {
    let root = temp_root("real-target");
    let store = Store::open(root.clone()).unwrap();
    let first = put_fixture_direct(&store, "variant-001", &variant_fixture(1));
    let second = put_fixture_direct(&store, "variant-002", &variant_fixture(2));
    store.get_object(&second).unwrap();
    let starting = store.stats().unwrap().total_bytes_on_disk;

    let mut eviction = request(EvictionPolicy::Lru);
    eviction.max_objects = None;
    eviction.target_bytes = Some(starting / 2);
    let report = store.evict(eviction).unwrap();

    assert!(report.target_reached);
    assert_eq!(report.evicted[0].object_id, first);
    assert!(!store.has_object(&first).unwrap());
    assert!(store.has_object(&second).unwrap());
    cleanup(&root);
}

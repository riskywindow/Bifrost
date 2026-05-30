use bifrostd::spool::{CommitOutcome, Spool, SpoolError};
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

fn temp_spool_root(test_name: &str) -> PathBuf {
    let unique = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_nanos();
    std::env::temp_dir().join(format!(
        "bifrostd-spool-{test_name}-{}-{unique}",
        std::process::id()
    ))
}

fn stage_fixture(spool: &Spool, transfer_id: &str, fixture: &Fixture) {
    let mut manifest = chunk_bytes(&fixture.payload, TEST_CHUNK_SIZE).unwrap();
    manifest.object_id = Some(fixture.metadata["object_id"].as_str().unwrap().to_string());
    spool
        .create_staging_transfer(transfer_id, &fixture.metadata_bytes, &manifest)
        .unwrap();
    for chunk in iter_chunks(&fixture.payload, TEST_CHUNK_SIZE).unwrap() {
        spool
            .write_chunk(transfer_id, chunk.info.chunk_index, chunk.bytes)
            .unwrap();
    }
}

fn cleanup(path: &Path) {
    if path.exists() {
        fs::remove_dir_all(path).unwrap();
    }
}

#[test]
fn valid_object_stages_and_commits() {
    let root = temp_spool_root("valid-object-stages-and-commits");
    let spool = Spool::new(&root);
    let fixture = native_fixture();
    let object_id = fixture.metadata["object_id"].as_str().unwrap();

    stage_fixture(&spool, "transfer-001", &fixture);
    assert!(!spool.has_object(object_id));

    let outcome = spool
        .commit_transfer("transfer-001", Some(&fixture.target))
        .unwrap();

    assert_eq!(
        outcome,
        CommitOutcome::Committed {
            object_id: object_id.to_string()
        }
    );
    assert!(spool.has_object(object_id));
    cleanup(&root);
}

#[test]
fn committed_object_can_be_read_back_exactly() {
    let root = temp_spool_root("committed-object-can-be-read-back-exactly");
    let spool = Spool::new(&root);
    let fixture = native_fixture();
    let object_id = fixture.metadata["object_id"].as_str().unwrap();

    stage_fixture(&spool, "transfer-001", &fixture);
    spool
        .commit_transfer("transfer-001", Some(&fixture.target))
        .unwrap();

    assert_eq!(
        spool.read_metadata(object_id).unwrap(),
        fixture.metadata_bytes
    );
    assert_eq!(spool.read_payload(object_id).unwrap(), fixture.payload);
    cleanup(&root);
}

#[test]
fn corrupted_committed_payload_is_not_servable() {
    let root = temp_spool_root("corrupted-committed-payload-is-not-servable");
    let spool = Spool::new(&root);
    let fixture = native_fixture();
    let object_id = fixture.metadata["object_id"].as_str().unwrap();

    stage_fixture(&spool, "transfer-001", &fixture);
    spool
        .commit_transfer("transfer-001", Some(&fixture.target))
        .unwrap();
    let paths = spool.get_object_paths(object_id).unwrap();
    let mut corrupted = fs::read(&paths.payload).unwrap();
    corrupted[0] ^= 0xff;
    fs::write(&paths.payload, corrupted).unwrap();

    assert!(!spool.has_object(object_id));
    assert!(matches!(
        spool.read_payload(object_id),
        Err(SpoolError::ValidationRejected(reason)) if reason == "payload_hash_mismatch"
    ));
    cleanup(&root);
}

#[test]
fn incomplete_committed_record_is_not_servable() {
    let root = temp_spool_root("incomplete-committed-record-is-not-servable");
    let spool = Spool::new(&root);
    let fixture = native_fixture();
    let object_id = fixture.metadata["object_id"].as_str().unwrap();

    stage_fixture(&spool, "transfer-001", &fixture);
    spool
        .commit_transfer("transfer-001", Some(&fixture.target))
        .unwrap();
    let paths = spool.get_object_paths(object_id).unwrap();
    fs::remove_file(&paths.metadata).unwrap();

    assert!(!spool.has_object(object_id));
    assert!(matches!(
        spool.read_payload(object_id),
        Err(SpoolError::NotFound(_))
    ));
    cleanup(&root);
}

#[test]
fn partial_transfer_does_not_commit() {
    let root = temp_spool_root("partial-transfer-does-not-commit");
    let spool = Spool::new(&root);
    let fixture = native_fixture();
    let object_id = fixture.metadata["object_id"].as_str().unwrap();
    let manifest = chunk_bytes(&fixture.payload, TEST_CHUNK_SIZE).unwrap();
    let chunks = iter_chunks(&fixture.payload, TEST_CHUNK_SIZE).unwrap();

    spool
        .create_staging_transfer("transfer-001", &fixture.metadata_bytes, &manifest)
        .unwrap();
    spool
        .write_chunk("transfer-001", chunks[0].info.chunk_index, chunks[0].bytes)
        .unwrap();

    assert!(spool
        .commit_transfer("transfer-001", Some(&fixture.target))
        .is_err());
    assert!(!spool.has_object(object_id));
    cleanup(&root);
}

#[test]
fn corrupted_chunk_does_not_commit() {
    let root = temp_spool_root("corrupted-chunk-does-not-commit");
    let spool = Spool::new(&root);
    let fixture = native_fixture();
    let object_id = fixture.metadata["object_id"].as_str().unwrap();
    let manifest = chunk_bytes(&fixture.payload, TEST_CHUNK_SIZE).unwrap();
    let chunks = iter_chunks(&fixture.payload, TEST_CHUNK_SIZE).unwrap();
    let mut corrupted = chunks[0].bytes.to_vec();
    corrupted[0] ^= 0xff;

    spool
        .create_staging_transfer("transfer-001", &fixture.metadata_bytes, &manifest)
        .unwrap();
    assert!(spool
        .write_chunk("transfer-001", chunks[0].info.chunk_index, &corrupted)
        .is_err());

    assert!(spool
        .commit_transfer("transfer-001", Some(&fixture.target))
        .is_err());
    assert!(!spool.has_object(object_id));
    cleanup(&root);
}

#[test]
fn invalid_phase1_object_does_not_commit() {
    let root = temp_spool_root("invalid-phase1-object-does-not-commit");
    let spool = Spool::new(&root);
    let mut fixture = native_fixture();
    let object_id = fixture.metadata["object_id"].as_str().unwrap().to_string();
    fixture.payload[0] ^= 0xff;

    stage_fixture(&spool, "transfer-001", &fixture);
    let err = spool
        .commit_transfer("transfer-001", Some(&fixture.target))
        .unwrap_err();

    assert!(
        matches!(err, SpoolError::ValidationRejected(reason) if reason == "payload_hash_mismatch")
    );
    assert!(!spool.has_object(&object_id));
    cleanup(&root);
}

#[test]
fn path_traversal_object_ids_are_rejected() {
    let root = temp_spool_root("path-traversal-object-ids-are-rejected");
    let spool = Spool::new(&root);

    assert!(matches!(
        spool.get_object_paths("../bad"),
        Err(SpoolError::InvalidObjectId(_))
    ));
    assert!(matches!(
        spool.read_payload("bifrost://object/blake3/../../bad"),
        Err(SpoolError::InvalidObjectId(_))
    ));
    cleanup(&root);
}

#[test]
fn abort_removes_staging() {
    let root = temp_spool_root("abort-removes-staging");
    let spool = Spool::new(&root);
    let fixture = native_fixture();
    let manifest = chunk_bytes(&fixture.payload, TEST_CHUNK_SIZE).unwrap();

    spool
        .create_staging_transfer("transfer-001", &fixture.metadata_bytes, &manifest)
        .unwrap();
    assert!(root.join("staging/transfer-001").exists());

    spool.abort_staging_transfer("transfer-001").unwrap();

    assert!(!root.join("staging/transfer-001").exists());
    cleanup(&root);
}

#[test]
fn duplicate_commit_returns_already_committed_for_identical_object() {
    let root = temp_spool_root("duplicate-commit-returns-already-committed");
    let spool = Spool::new(&root);
    let fixture = native_fixture();
    let object_id = fixture.metadata["object_id"].as_str().unwrap();

    stage_fixture(&spool, "transfer-001", &fixture);
    assert!(matches!(
        spool
            .commit_transfer("transfer-001", Some(&fixture.target))
            .unwrap(),
        CommitOutcome::Committed { .. }
    ));

    stage_fixture(&spool, "transfer-002", &fixture);
    assert_eq!(
        spool
            .commit_transfer("transfer-002", Some(&fixture.target))
            .unwrap(),
        CommitOutcome::AlreadyCommitted {
            object_id: object_id.to_string()
        }
    );
    cleanup(&root);
}

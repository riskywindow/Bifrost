use bifrostd::store::{open_catalog, StoreError, LATEST_SCHEMA_VERSION};
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
            "bifrostd-store-{test_name}-{}-{unique}",
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

fn row_count(catalog: &bifrostd::store::Catalog, sql: &str) -> i64 {
    catalog
        .connection()
        .query_row(sql, [], |row| row.get::<_, i64>(0))
        .unwrap()
}

#[test]
fn catalog_initializes() {
    let path = temp_catalog_path("catalog-initializes");

    let catalog = open_catalog(&path).unwrap();

    assert_eq!(
        catalog.current_schema_version().unwrap(),
        LATEST_SCHEMA_VERSION
    );
    cleanup(&path);
}

#[test]
fn migrations_are_idempotent() {
    let path = temp_catalog_path("migrations-are-idempotent");
    let mut catalog = open_catalog(&path).unwrap();

    catalog.apply_migrations().unwrap();
    catalog.apply_migrations().unwrap();

    assert_eq!(
        row_count(&catalog, "SELECT COUNT(*) FROM schema_migrations"),
        LATEST_SCHEMA_VERSION
    );
    cleanup(&path);
}

#[test]
fn foreign_keys_are_enabled() {
    let path = temp_catalog_path("foreign-keys-are-enabled");
    let catalog = open_catalog(&path).unwrap();

    let enabled: i64 = catalog
        .connection()
        .query_row("PRAGMA foreign_keys", [], |row| row.get(0))
        .unwrap();

    assert_eq!(enabled, 1);
    cleanup(&path);
}

#[test]
fn expected_tables_exist() {
    let path = temp_catalog_path("expected-tables-exist");
    let catalog = open_catalog(&path).unwrap();
    let tables = [
        "schema_migrations",
        "objects",
        "object_locations",
        "object_compatibility",
        "object_access",
        "prefix_manifests",
        "manifest_members",
        "store_events",
        "opaque_key_index",
    ];

    for table in tables {
        let count = row_count(
            &catalog,
            &format!(
                "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = '{table}'"
            ),
        );
        assert_eq!(count, 1, "missing table {table}");
    }
    cleanup(&path);
}

#[test]
fn expected_indexes_exist() {
    let path = temp_catalog_path("expected-indexes-exist");
    let catalog = open_catalog(&path).unwrap();
    let indexes = [
        "idx_objects_state",
        "idx_objects_last_accessed_unix_ms",
        "idx_objects_pin_count",
        "idx_object_compatibility_model_prefix",
        "idx_object_compatibility_opaque_engine_key_hash",
        "idx_object_compatibility_layer_block",
        "idx_prefix_manifests_prefix_hash",
        "idx_manifest_members_manifest_id",
        "idx_manifest_members_object_id",
        "idx_opaque_key_index_object_id",
    ];

    for index in indexes {
        let count = row_count(
            &catalog,
            &format!(
                "SELECT COUNT(*) FROM sqlite_master WHERE type = 'index' AND name = '{index}'"
            ),
        );
        assert_eq!(count, 1, "missing index {index}");
    }
    cleanup(&path);
}

#[test]
fn schema_version_is_correct() {
    let path = temp_catalog_path("schema-version-is-correct");
    let catalog = open_catalog(&path).unwrap();

    assert_eq!(
        catalog.current_schema_version().unwrap(),
        LATEST_SCHEMA_VERSION
    );
    assert_eq!(
        row_count(
            &catalog,
            "SELECT COUNT(*) FROM schema_migrations WHERE version = 1 AND name = 'initial_catalog_schema'",
        ),
        1
    );
    assert_eq!(
        row_count(
            &catalog,
            "SELECT COUNT(*) FROM schema_migrations WHERE version = 2 AND name = 'opaque_key_index'",
        ),
        1
    );
    cleanup(&path);
}

#[test]
fn reopening_catalog_preserves_version() {
    let path = temp_catalog_path("reopening-catalog-preserves-version");
    {
        let catalog = open_catalog(&path).unwrap();
        assert_eq!(
            catalog.current_schema_version().unwrap(),
            LATEST_SCHEMA_VERSION
        );
    }

    let catalog = open_catalog(&path).unwrap();

    assert_eq!(
        catalog.current_schema_version().unwrap(),
        LATEST_SCHEMA_VERSION
    );
    cleanup(&path);
}

#[test]
fn applying_migrations_twice_does_not_duplicate_rows() {
    let path = temp_catalog_path("applying-migrations-twice");
    let mut catalog = open_catalog(&path).unwrap();

    catalog.apply_migrations().unwrap();
    let before = row_count(&catalog, "SELECT COUNT(*) FROM schema_migrations");
    catalog.apply_migrations().unwrap();
    let after = row_count(&catalog, "SELECT COUNT(*) FROM schema_migrations");

    assert_eq!(before, LATEST_SCHEMA_VERSION);
    assert_eq!(after, LATEST_SCHEMA_VERSION);
    cleanup(&path);
}

#[test]
fn future_schema_version_is_rejected() {
    let path = temp_catalog_path("future-schema-version-is-rejected");
    {
        let catalog = open_catalog(&path).unwrap();
        catalog
            .connection()
            .execute(
                "INSERT INTO schema_migrations(version, name, applied_at_unix_ms) VALUES (999, 'future', 1)",
                [],
            )
            .unwrap();
    }

    let err = open_catalog(&path).unwrap_err();

    assert!(matches!(
        err,
        StoreError::FutureSchemaVersion {
            found: 999,
            supported: LATEST_SCHEMA_VERSION
        }
    ));
    cleanup(&path);
}

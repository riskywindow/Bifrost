use crate::store::errors::{StoreError, StoreResult};
use crate::store::schema::{
    CREATE_SCHEMA_MIGRATIONS, LATEST_SCHEMA_VERSION, MIGRATION_V1_NAME, MIGRATION_V1_SQL,
};
use rusqlite::{params, Connection, OptionalExtension};
use std::time::{SystemTime, UNIX_EPOCH};

#[derive(Debug, Clone, Copy)]
pub struct Migration {
    pub version: i64,
    pub name: &'static str,
    pub sql: &'static str,
}

pub const MIGRATIONS: &[Migration] = &[Migration {
    version: 1,
    name: MIGRATION_V1_NAME,
    sql: MIGRATION_V1_SQL,
}];

pub fn init_schema(conn: &mut Connection) -> StoreResult<()> {
    conn.execute_batch(CREATE_SCHEMA_MIGRATIONS)?;
    Ok(())
}

pub fn apply_migrations(conn: &mut Connection) -> StoreResult<()> {
    let current = current_schema_version(conn)?;
    if current > LATEST_SCHEMA_VERSION {
        return Err(StoreError::FutureSchemaVersion {
            found: current,
            supported: LATEST_SCHEMA_VERSION,
        });
    }

    for migration in MIGRATIONS {
        if migration.version <= current {
            continue;
        }
        apply_migration(conn, migration)?;
    }
    Ok(())
}

pub fn current_schema_version(conn: &Connection) -> StoreResult<i64> {
    let version = conn
        .query_row("SELECT MAX(version) FROM schema_migrations", [], |row| {
            row.get::<_, Option<i64>>(0)
        })
        .optional()?
        .flatten()
        .unwrap_or(0);
    Ok(version)
}

fn apply_migration(conn: &mut Connection, migration: &Migration) -> StoreResult<()> {
    let tx = conn.transaction()?;
    tx.execute_batch(migration.sql)?;
    tx.execute(
        "INSERT INTO schema_migrations(version, name, applied_at_unix_ms) VALUES (?1, ?2, ?3)",
        params![migration.version, migration.name, now_unix_ms()],
    )?;
    tx.commit()?;
    Ok(())
}

fn now_unix_ms() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("system clock is before unix epoch")
        .as_millis() as i64
}

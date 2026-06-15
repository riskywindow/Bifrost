pub const LATEST_SCHEMA_VERSION: i64 = 2;

pub const CREATE_SCHEMA_MIGRATIONS: &str = r#"
CREATE TABLE IF NOT EXISTS schema_migrations (
  version INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  applied_at_unix_ms INTEGER NOT NULL
);
"#;

pub const MIGRATION_V1_NAME: &str = "initial_catalog_schema";
pub const MIGRATION_V2_NAME: &str = "opaque_key_index";

pub const MIGRATION_V1_SQL: &str = r#"
CREATE TABLE objects (
  object_id TEXT PRIMARY KEY,
  object_type TEXT NOT NULL,
  schema_version TEXT NOT NULL,
  descriptor_hash TEXT NOT NULL,
  payload_hash TEXT NOT NULL,
  byte_length INTEGER NOT NULL,
  state TEXT NOT NULL,
  created_at_unix_ms INTEGER NOT NULL,
  committed_at_unix_ms INTEGER,
  verified_at_unix_ms INTEGER,
  last_accessed_unix_ms INTEGER,
  access_count INTEGER NOT NULL DEFAULT 0,
  pin_count INTEGER NOT NULL DEFAULT 0,
  ttl_expires_at_unix_ms INTEGER,
  quarantine_reason TEXT
);

CREATE TABLE object_locations (
  object_id TEXT NOT NULL,
  tier TEXT NOT NULL,
  meta_path TEXT NOT NULL,
  payload_path TEXT NOT NULL,
  bytes_on_disk INTEGER NOT NULL,
  PRIMARY KEY(object_id, tier),
  FOREIGN KEY(object_id) REFERENCES objects(object_id)
);

CREATE TABLE object_compatibility (
  object_id TEXT PRIMARY KEY,
  model_hash TEXT,
  tokenizer_hash TEXT,
  config_hash TEXT,
  rope_config_hash TEXT,
  dtype TEXT,
  engine_name TEXT,
  engine_version TEXT,
  integration_name TEXT,
  kv_cache_format TEXT,
  prefix_hash TEXT,
  token_range_start INTEGER,
  token_range_end INTEGER,
  layer_id INTEGER,
  kv_block_id INTEGER,
  opaque_engine_key_hash TEXT,
  FOREIGN KEY(object_id) REFERENCES objects(object_id)
);

CREATE TABLE object_access (
  object_id TEXT PRIMARY KEY,
  last_get_unix_ms INTEGER,
  last_put_unix_ms INTEGER,
  get_count INTEGER NOT NULL DEFAULT 0,
  put_count INTEGER NOT NULL DEFAULT 0,
  bytes_read_total INTEGER NOT NULL DEFAULT 0,
  bytes_written_total INTEGER NOT NULL DEFAULT 0,
  FOREIGN KEY(object_id) REFERENCES objects(object_id)
);

CREATE TABLE prefix_manifests (
  manifest_id TEXT PRIMARY KEY,
  manifest_type TEXT NOT NULL,
  model_hash TEXT,
  tokenizer_hash TEXT,
  rope_config_hash TEXT,
  prefix_hash TEXT NOT NULL,
  token_range_start INTEGER NOT NULL,
  token_range_end INTEGER NOT NULL,
  completeness_state TEXT NOT NULL,
  created_at_unix_ms INTEGER NOT NULL,
  updated_at_unix_ms INTEGER NOT NULL,
  pin_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE manifest_members (
  manifest_id TEXT NOT NULL,
  object_id TEXT NOT NULL,
  layer_id INTEGER,
  kv_block_id INTEGER,
  token_range_start INTEGER,
  token_range_end INTEGER,
  required INTEGER NOT NULL DEFAULT 1,
  PRIMARY KEY(manifest_id, object_id),
  FOREIGN KEY(manifest_id) REFERENCES prefix_manifests(manifest_id),
  FOREIGN KEY(object_id) REFERENCES objects(object_id)
);

CREATE TABLE store_events (
  event_id INTEGER PRIMARY KEY AUTOINCREMENT,
  timestamp_unix_ms INTEGER NOT NULL,
  event_type TEXT NOT NULL,
  object_id TEXT,
  manifest_id TEXT,
  details_json TEXT
);

CREATE INDEX idx_objects_state ON objects(state);
CREATE INDEX idx_objects_last_accessed_unix_ms ON objects(last_accessed_unix_ms);
CREATE INDEX idx_objects_pin_count ON objects(pin_count);
CREATE INDEX idx_object_compatibility_model_prefix ON object_compatibility(model_hash, prefix_hash);
CREATE INDEX idx_object_compatibility_opaque_engine_key_hash ON object_compatibility(opaque_engine_key_hash);
CREATE INDEX idx_object_compatibility_layer_block ON object_compatibility(layer_id, kv_block_id);
CREATE INDEX idx_prefix_manifests_prefix_hash ON prefix_manifests(prefix_hash);
CREATE INDEX idx_manifest_members_manifest_id ON manifest_members(manifest_id);
CREATE INDEX idx_manifest_members_object_id ON manifest_members(object_id);
"#;

pub const MIGRATION_V2_SQL: &str = r#"
CREATE TABLE IF NOT EXISTS opaque_key_index (
  engine_name TEXT NOT NULL,
  integration_name TEXT NOT NULL,
  opaque_engine_key_hash TEXT NOT NULL,
  opaque_engine_key_repr TEXT,
  object_id TEXT NOT NULL,
  created_at_unix_ms INTEGER NOT NULL,
  last_accessed_unix_ms INTEGER,
  PRIMARY KEY(engine_name, integration_name, opaque_engine_key_hash),
  FOREIGN KEY(object_id) REFERENCES objects(object_id)
);

CREATE INDEX IF NOT EXISTS idx_opaque_key_index_object_id ON opaque_key_index(object_id);

INSERT OR IGNORE INTO opaque_key_index(
  engine_name, integration_name, opaque_engine_key_hash, opaque_engine_key_repr,
  object_id, created_at_unix_ms, last_accessed_unix_ms
)
SELECT
  c.engine_name, c.integration_name, c.opaque_engine_key_hash, NULL,
  c.object_id, o.created_at_unix_ms, o.last_accessed_unix_ms
FROM object_compatibility c
INNER JOIN objects o ON o.object_id = c.object_id
WHERE o.object_type = 'opaque_engine_blob'
  AND c.engine_name IS NOT NULL
  AND c.integration_name IS NOT NULL
  AND c.opaque_engine_key_hash IS NOT NULL;
"#;

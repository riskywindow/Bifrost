# Phase 3 Catalog Schema

Last verified: 2026-05-30

## Purpose

The Phase 3 catalog is a local SQLite database for durable store metadata. It
indexes committed and verified KV objects without changing Phase 1 immutable
object identity.

The initial Rust implementation uses `rusqlite` and a synchronous migration
framework. It creates schema version 1 and records applied migrations in
`schema_migrations`.

SQLite connection settings:

1. `foreign_keys = ON`.
2. `busy_timeout = 5000ms`.
3. `journal_mode = WAL` for local concurrent readers and crash recovery.
4. `synchronous = FULL`. This is conservative for correctness tests and avoids
   unsafe durability optimizations.

## Table: schema_migrations

Tracks catalog schema versions.

```sql
CREATE TABLE schema_migrations (
  version INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  applied_at_unix_ms INTEGER NOT NULL
);
```

Rules:

1. Migrations run inside transactions.
2. The daemon rejects unknown future schema versions by default.
3. Version 1 is named `initial_catalog_schema`.

## Table: objects

One row per immutable KV object known to the local store.

```sql
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
```

`state` uses the lifecycle states from `docs/phase3_lifecycle.md`. A row does
not make an object servable unless file-level integrity, descriptor hash,
payload hash, object ID, and compatibility validation all succeed.

## Table: object_locations

Tracks local filesystem and tier locations for object bytes.

```sql
CREATE TABLE object_locations (
  object_id TEXT NOT NULL,
  tier TEXT NOT NULL,
  meta_path TEXT NOT NULL,
  payload_path TEXT NOT NULL,
  bytes_on_disk INTEGER NOT NULL,
  PRIMARY KEY(object_id, tier),
  FOREIGN KEY(object_id) REFERENCES objects(object_id)
);
```

`tier` is initially `disk`; `memory` may be added for optional memory-tier
metadata. Staging locations must not be inserted as available locations.

## Table: object_compatibility

Indexes target compatibility fields for lookup.

```sql
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
```

The values are derived from validated descriptors. They are lookup aids, not a
replacement for Phase 1 compatibility validation before serving.

## Table: object_access

Tracks deterministic access metadata used by stats and eviction.

```sql
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
```

Tests should inject clock values for future access updates rather than relying
on wall-clock ordering.

## Table: prefix_manifests

Defines a named or content-addressed set of KV objects representing a reusable
prefix.

```sql
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
```

`manifest_type` is initially expected to distinguish prefix and optional
session manifests.

## Table: manifest_members

Maps manifests to member KV objects.

```sql
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
```

A manifest is complete only when all required members are committed, verified,
catalog-consistent, and file-present.

## Table: store_events

Append-oriented event log for store decisions and fsck findings.

```sql
CREATE TABLE store_events (
  event_id INTEGER PRIMARY KEY AUTOINCREMENT,
  timestamp_unix_ms INTEGER NOT NULL,
  event_type TEXT NOT NULL,
  object_id TEXT,
  manifest_id TEXT,
  details_json TEXT
);
```

Events should be specific enough to distinguish catalog errors, filesystem
errors, integrity errors, compatibility errors, manifest errors, eviction
decisions, and fsck actions.

## Initial indexes

```sql
CREATE INDEX idx_objects_state ON objects(state);
CREATE INDEX idx_objects_last_accessed_unix_ms ON objects(last_accessed_unix_ms);
CREATE INDEX idx_objects_pin_count ON objects(pin_count);
CREATE INDEX idx_object_compatibility_model_prefix
  ON object_compatibility(model_hash, prefix_hash);
CREATE INDEX idx_object_compatibility_opaque_engine_key_hash
  ON object_compatibility(opaque_engine_key_hash);
CREATE INDEX idx_object_compatibility_layer_block
  ON object_compatibility(layer_id, kv_block_id);
CREATE INDEX idx_prefix_manifests_prefix_hash ON prefix_manifests(prefix_hash);
CREATE INDEX idx_manifest_members_manifest_id ON manifest_members(manifest_id);
CREATE INDEX idx_manifest_members_object_id ON manifest_members(object_id);
```

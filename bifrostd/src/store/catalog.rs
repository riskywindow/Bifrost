use crate::store::errors::{StoreError, StoreResult};
use crate::store::eviction::{EvictionCandidate, EvictionPolicy};
use crate::store::lifecycle::{can_evict, ensure_valid_state_transition};
use crate::store::manifest::{
    CompletenessState, ManifestListFilter, ManifestMember, ManifestRecord,
};
use crate::store::migrations;
use crate::store::object_record::{
    ObjectAccess, ObjectCompatibility, ObjectListFilter, ObjectLocation, ObjectRecord, ObjectState,
    StoreEvent,
};
use crate::store::stats::StoreStats;
use rusqlite::types::Value;
use rusqlite::{params, params_from_iter, Connection, OptionalExtension, Row};
use serde_json::json;
use std::fs;
use std::path::Path;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

#[derive(Debug)]
pub struct Catalog {
    conn: Connection,
}

pub fn open_catalog(path: &Path) -> StoreResult<Catalog> {
    if let Some(parent) = path
        .parent()
        .filter(|parent| !parent.as_os_str().is_empty())
    {
        fs::create_dir_all(parent)?;
    }
    let mut conn = Connection::open(path)?;
    configure_connection(&conn)?;
    migrations::init_schema(&mut conn)?;
    migrations::apply_migrations(&mut conn)?;
    Ok(Catalog { conn })
}

impl Catalog {
    pub fn init_schema(&mut self) -> StoreResult<()> {
        migrations::init_schema(&mut self.conn)
    }

    pub fn apply_migrations(&mut self) -> StoreResult<()> {
        migrations::apply_migrations(&mut self.conn)
    }

    pub fn current_schema_version(&self) -> StoreResult<i64> {
        migrations::current_schema_version(&self.conn)
    }

    pub fn connection(&self) -> &Connection {
        &self.conn
    }

    pub fn insert_committed_object(
        &mut self,
        record: &ObjectRecord,
        location: &ObjectLocation,
        compatibility: &ObjectCompatibility,
    ) -> StoreResult<()> {
        if record.object_id != location.object_id || record.object_id != compatibility.object_id {
            return Err(StoreError::Catalog(rusqlite::Error::InvalidParameterName(
                "object_id mismatch".to_string(),
            )));
        }
        if record.state != ObjectState::Committed {
            return Err(StoreError::InvalidState(format!(
                "insert_committed_object requires committed, got {}",
                record.state
            )));
        }

        let tx = self.conn.transaction()?;
        tx.execute(
            "INSERT INTO objects(
                object_id, object_type, schema_version, descriptor_hash, payload_hash,
                byte_length, state, created_at_unix_ms, committed_at_unix_ms,
                verified_at_unix_ms, last_accessed_unix_ms, access_count, pin_count,
                ttl_expires_at_unix_ms, quarantine_reason
            ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14, ?15)",
            params![
                record.object_id,
                record.object_type,
                record.schema_version,
                record.descriptor_hash,
                record.payload_hash,
                record.byte_length,
                record.state,
                record.created_at_unix_ms,
                record.committed_at_unix_ms,
                record.verified_at_unix_ms,
                record.last_accessed_unix_ms,
                record.access_count,
                record.pin_count,
                record.ttl_expires_at_unix_ms,
                record.quarantine_reason,
            ],
        )?;
        tx.execute(
            "INSERT INTO object_locations(object_id, tier, meta_path, payload_path, bytes_on_disk)
             VALUES (?1, ?2, ?3, ?4, ?5)",
            params![
                location.object_id,
                location.tier,
                location.meta_path,
                location.payload_path,
                location.bytes_on_disk
            ],
        )?;
        tx.execute(
            "INSERT INTO object_compatibility(
                object_id, model_hash, tokenizer_hash, config_hash, rope_config_hash,
                dtype, engine_name, engine_version, integration_name, kv_cache_format,
                prefix_hash, token_range_start, token_range_end, layer_id, kv_block_id,
                opaque_engine_key_hash
            ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14, ?15, ?16)",
            params![
                compatibility.object_id,
                compatibility.model_hash,
                compatibility.tokenizer_hash,
                compatibility.config_hash,
                compatibility.rope_config_hash,
                compatibility.dtype,
                compatibility.engine_name,
                compatibility.engine_version,
                compatibility.integration_name,
                compatibility.kv_cache_format,
                compatibility.prefix_hash,
                compatibility.token_range_start,
                compatibility.token_range_end,
                compatibility.layer_id,
                compatibility.kv_block_id,
                compatibility.opaque_engine_key_hash,
            ],
        )?;
        tx.execute(
            "INSERT INTO object_access(object_id) VALUES (?1)",
            params![record.object_id],
        )?;
        log_event(
            &tx,
            "object_committed",
            Some(&record.object_id),
            None,
            Some(json!({"state": record.state.as_str()})),
        )?;
        tx.commit()?;
        Ok(())
    }

    pub fn get_object_record(&self, object_id: &str) -> StoreResult<Option<ObjectRecord>> {
        self.conn
            .query_row(
                "SELECT * FROM objects WHERE object_id = ?1",
                params![object_id],
                object_record_from_row,
            )
            .optional()
            .map_err(StoreError::from)
    }

    pub fn get_object_location(
        &self,
        object_id: &str,
        tier: &str,
    ) -> StoreResult<Option<ObjectLocation>> {
        self.conn
            .query_row(
                "SELECT object_id, tier, meta_path, payload_path, bytes_on_disk
                 FROM object_locations WHERE object_id = ?1 AND tier = ?2",
                params![object_id, tier],
                object_location_from_row,
            )
            .optional()
            .map_err(StoreError::from)
    }

    pub fn get_object_compatibility(
        &self,
        object_id: &str,
    ) -> StoreResult<Option<ObjectCompatibility>> {
        self.conn
            .query_row(
                "SELECT * FROM object_compatibility WHERE object_id = ?1",
                params![object_id],
                object_compatibility_from_row,
            )
            .optional()
            .map_err(StoreError::from)
    }

    pub fn get_object_access(&self, object_id: &str) -> StoreResult<Option<ObjectAccess>> {
        self.conn
            .query_row(
                "SELECT object_id, last_get_unix_ms, last_put_unix_ms, get_count,
                        put_count, bytes_read_total, bytes_written_total
                 FROM object_access WHERE object_id = ?1",
                params![object_id],
                object_access_from_row,
            )
            .optional()
            .map_err(StoreError::from)
    }

    pub fn update_access_on_get(&mut self, object_id: &str, bytes_read: i64) -> StoreResult<()> {
        let now = now_unix_ms();
        let tx = self.conn.transaction()?;
        let changed = tx.execute(
            "UPDATE object_access
             SET last_get_unix_ms = ?2,
                 get_count = get_count + 1,
                 bytes_read_total = bytes_read_total + ?3
             WHERE object_id = ?1",
            params![object_id, now, bytes_read],
        )?;
        if changed == 0 {
            return Err(StoreError::NotFound(object_id.to_string()));
        }
        tx.execute(
            "UPDATE objects
             SET last_accessed_unix_ms = ?2, access_count = access_count + 1
             WHERE object_id = ?1",
            params![object_id, now],
        )?;
        log_event(
            &tx,
            "object_accessed",
            Some(object_id),
            None,
            Some(json!({"operation": "get", "bytes_read": bytes_read})),
        )?;
        tx.commit()?;
        Ok(())
    }

    pub fn update_access_on_put(&mut self, object_id: &str, bytes_written: i64) -> StoreResult<()> {
        let now = now_unix_ms();
        let tx = self.conn.transaction()?;
        let changed = tx.execute(
            "UPDATE object_access
             SET last_put_unix_ms = ?2,
                 put_count = put_count + 1,
                 bytes_written_total = bytes_written_total + ?3
             WHERE object_id = ?1",
            params![object_id, now, bytes_written],
        )?;
        if changed == 0 {
            return Err(StoreError::NotFound(object_id.to_string()));
        }
        tx.execute(
            "UPDATE objects
             SET last_accessed_unix_ms = ?2, access_count = access_count + 1
             WHERE object_id = ?1",
            params![object_id, now],
        )?;
        log_event(
            &tx,
            "object_accessed",
            Some(object_id),
            None,
            Some(json!({"operation": "put", "bytes_written": bytes_written})),
        )?;
        tx.commit()?;
        Ok(())
    }

    pub fn increment_pin(&mut self, object_id: &str) -> StoreResult<()> {
        let record = self
            .get_object_record(object_id)?
            .ok_or_else(|| StoreError::NotFound(object_id.to_string()))?;
        if record.pin_count == 0 {
            ensure_valid_state_transition(record.state, ObjectState::Pinned)?;
        }

        let tx = self.conn.transaction()?;
        let new_state = if record.pin_count == 0 {
            ObjectState::Pinned
        } else {
            record.state
        };
        tx.execute(
            "UPDATE objects SET pin_count = pin_count + 1, state = ?2 WHERE object_id = ?1",
            params![object_id, new_state],
        )?;
        log_event(
            &tx,
            "object_pinned",
            Some(object_id),
            None,
            Some(json!({"pin_count": record.pin_count + 1})),
        )?;
        tx.commit()?;
        Ok(())
    }

    pub fn decrement_pin(&mut self, object_id: &str) -> StoreResult<()> {
        let record = self
            .get_object_record(object_id)?
            .ok_or_else(|| StoreError::NotFound(object_id.to_string()))?;
        if record.pin_count == 0 {
            return Ok(());
        }

        let next_pin_count = record.pin_count - 1;
        let next_state = if next_pin_count == 0 && record.state == ObjectState::Pinned {
            ensure_valid_state_transition(record.state, ObjectState::Verified)?;
            ObjectState::Verified
        } else {
            record.state
        };

        let tx = self.conn.transaction()?;
        tx.execute(
            "UPDATE objects SET pin_count = ?2, state = ?3 WHERE object_id = ?1",
            params![object_id, next_pin_count, next_state],
        )?;
        log_event(
            &tx,
            "object_unpinned",
            Some(object_id),
            None,
            Some(json!({"pin_count": next_pin_count})),
        )?;
        tx.commit()?;
        Ok(())
    }

    pub fn transition_object_state(
        &mut self,
        object_id: &str,
        new_state: ObjectState,
        reason: Option<&str>,
    ) -> StoreResult<()> {
        let record = self
            .get_object_record(object_id)?
            .ok_or_else(|| StoreError::NotFound(object_id.to_string()))?;
        ensure_valid_state_transition(record.state, new_state)?;

        let now = now_unix_ms();
        let verified_at = if new_state == ObjectState::Verified {
            Some(now)
        } else {
            record.verified_at_unix_ms
        };
        let tx = self.conn.transaction()?;
        tx.execute(
            "UPDATE objects
             SET state = ?2, verified_at_unix_ms = ?3, quarantine_reason = ?4
             WHERE object_id = ?1",
            params![
                object_id,
                new_state,
                verified_at,
                if new_state == ObjectState::Quarantined {
                    reason
                } else {
                    None
                }
            ],
        )?;
        log_event(
            &tx,
            event_type_for_transition(new_state),
            Some(object_id),
            None,
            Some(
                json!({"from": record.state.as_str(), "to": new_state.as_str(), "reason": reason}),
            ),
        )?;
        tx.commit()?;
        Ok(())
    }

    pub fn mark_quarantined(&mut self, object_id: &str, reason: &str) -> StoreResult<()> {
        self.transition_object_state(object_id, ObjectState::Quarantined, Some(reason))
    }

    pub fn mark_verified(&mut self, object_id: &str) -> StoreResult<()> {
        self.transition_object_state(object_id, ObjectState::Verified, None)
    }

    pub fn set_ttl(&mut self, object_id: &str, expires_at_unix_ms: i64) -> StoreResult<()> {
        self.get_object_record(object_id)?
            .ok_or_else(|| StoreError::NotFound(object_id.to_string()))?;
        let tx = self.conn.transaction()?;
        tx.execute(
            "UPDATE objects SET ttl_expires_at_unix_ms = ?2 WHERE object_id = ?1",
            params![object_id, expires_at_unix_ms],
        )?;
        log_event(
            &tx,
            "object_ttl_set",
            Some(object_id),
            None,
            Some(json!({"expires_at_unix_ms": expires_at_unix_ms})),
        )?;
        tx.commit()?;
        Ok(())
    }

    pub fn clear_ttl(&mut self, object_id: &str) -> StoreResult<()> {
        self.get_object_record(object_id)?
            .ok_or_else(|| StoreError::NotFound(object_id.to_string()))?;
        let tx = self.conn.transaction()?;
        tx.execute(
            "UPDATE objects SET ttl_expires_at_unix_ms = NULL WHERE object_id = ?1",
            params![object_id],
        )?;
        log_event(
            &tx,
            "object_ttl_cleared",
            Some(object_id),
            None,
            Some(json!({})),
        )?;
        tx.commit()?;
        Ok(())
    }

    pub fn mark_evicted(&mut self, object_id: &str) -> StoreResult<()> {
        let record = self
            .get_object_record(object_id)?
            .ok_or_else(|| StoreError::NotFound(object_id.to_string()))?;
        if record.state != ObjectState::Evicting && !can_evict(record.state, record.pin_count) {
            return Err(StoreError::Eviction(format!(
                "object {object_id} in state {} with pin_count {} cannot be evicted",
                record.state, record.pin_count
            )));
        }
        if record.state == ObjectState::Evicting {
            ensure_valid_state_transition(record.state, ObjectState::Evicted)?;
        }

        let tx = self.conn.transaction()?;
        tx.execute(
            "UPDATE objects SET state = ?2 WHERE object_id = ?1",
            params![object_id, ObjectState::Evicted],
        )?;
        log_event(
            &tx,
            "object_evicted",
            Some(object_id),
            None,
            Some(json!({"from": record.state.as_str()})),
        )?;
        tx.commit()?;
        Ok(())
    }

    pub fn create_manifest(&mut self, manifest: &ManifestRecord) -> StoreResult<()> {
        let tx = self.conn.transaction()?;
        tx.execute(
            "INSERT INTO prefix_manifests(
                manifest_id, manifest_type, model_hash, tokenizer_hash, rope_config_hash,
                prefix_hash, token_range_start, token_range_end, completeness_state,
                created_at_unix_ms, updated_at_unix_ms, pin_count
            ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12)",
            params![
                manifest.manifest_id,
                manifest.manifest_type,
                manifest.model_hash,
                manifest.tokenizer_hash,
                manifest.rope_config_hash,
                manifest.prefix_hash,
                manifest.token_range_start,
                manifest.token_range_end,
                manifest.completeness_state,
                manifest.created_at_unix_ms,
                manifest.updated_at_unix_ms,
                manifest.pin_count,
            ],
        )?;
        log_event(
            &tx,
            "manifest_created",
            None,
            Some(&manifest.manifest_id),
            Some(json!({
                "manifest_type": manifest.manifest_type.as_str(),
                "prefix_hash": manifest.prefix_hash,
            })),
        )?;
        tx.commit()?;
        Ok(())
    }

    pub fn get_manifest(&self, manifest_id: &str) -> StoreResult<Option<ManifestRecord>> {
        self.conn
            .query_row(
                "SELECT manifest_id, manifest_type, model_hash, tokenizer_hash,
                        rope_config_hash, prefix_hash, token_range_start, token_range_end,
                        completeness_state, created_at_unix_ms, updated_at_unix_ms, pin_count
                 FROM prefix_manifests WHERE manifest_id = ?1",
                params![manifest_id],
                manifest_record_from_row,
            )
            .optional()
            .map_err(StoreError::from)
    }

    pub fn list_manifests(&self, filter: &ManifestListFilter) -> StoreResult<Vec<ManifestRecord>> {
        let mut sql = String::from(
            "SELECT manifest_id, manifest_type, model_hash, tokenizer_hash,
                    rope_config_hash, prefix_hash, token_range_start, token_range_end,
                    completeness_state, created_at_unix_ms, updated_at_unix_ms, pin_count
             FROM prefix_manifests",
        );
        let mut clauses = Vec::new();
        let mut values = Vec::new();
        if let Some(manifest_type) = filter.manifest_type {
            clauses.push("manifest_type = ?");
            values.push(Value::Text(manifest_type.as_str().to_string()));
        }
        if let Some(model_hash) = filter.model_hash.as_ref() {
            clauses.push("model_hash = ?");
            values.push(Value::Text(model_hash.clone()));
        }
        if let Some(prefix_hash) = filter.prefix_hash.as_ref() {
            clauses.push("prefix_hash = ?");
            values.push(Value::Text(prefix_hash.clone()));
        }
        if !clauses.is_empty() {
            sql.push_str(" WHERE ");
            sql.push_str(&clauses.join(" AND "));
        }
        sql.push_str(" ORDER BY manifest_id ASC");
        if let Some(limit) = filter.limit {
            sql.push_str(" LIMIT ?");
            values.push(Value::Integer(limit));
        }
        let mut stmt = self.conn.prepare(&sql)?;
        let rows = stmt.query_map(params_from_iter(values.iter()), manifest_record_from_row)?;
        rows.collect::<Result<Vec<_>, _>>()
            .map_err(StoreError::from)
    }

    pub fn add_manifest_member(&mut self, member: &ManifestMember) -> StoreResult<()> {
        self.get_manifest(&member.manifest_id)?
            .ok_or_else(|| StoreError::NotFound(member.manifest_id.clone()))?;
        self.get_object_record(&member.object_id)?
            .ok_or_else(|| StoreError::NotFound(member.object_id.clone()))?;

        let tx = self.conn.transaction()?;
        tx.execute(
            "INSERT OR REPLACE INTO manifest_members(
                manifest_id, object_id, layer_id, kv_block_id, token_range_start,
                token_range_end, required
            ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)",
            params![
                member.manifest_id,
                member.object_id,
                member.layer_id,
                member.kv_block_id,
                member.token_range_start,
                member.token_range_end,
                if member.required { 1 } else { 0 },
            ],
        )?;
        tx.execute(
            "UPDATE prefix_manifests
             SET completeness_state = ?2, updated_at_unix_ms = ?3
             WHERE manifest_id = ?1",
            params![
                member.manifest_id,
                CompletenessState::Unknown,
                now_unix_ms()
            ],
        )?;
        log_event(
            &tx,
            "manifest_member_added",
            Some(&member.object_id),
            Some(&member.manifest_id),
            Some(json!({
                "required": member.required,
                "layer_id": member.layer_id,
                "kv_block_id": member.kv_block_id,
            })),
        )?;
        tx.commit()?;
        Ok(())
    }

    pub fn remove_manifest_member(
        &mut self,
        manifest_id: &str,
        object_id: &str,
    ) -> StoreResult<Option<ManifestMember>> {
        let member = self.get_manifest_member(manifest_id, object_id)?;
        let tx = self.conn.transaction()?;
        tx.execute(
            "DELETE FROM manifest_members WHERE manifest_id = ?1 AND object_id = ?2",
            params![manifest_id, object_id],
        )?;
        tx.execute(
            "UPDATE prefix_manifests
             SET completeness_state = ?2, updated_at_unix_ms = ?3
             WHERE manifest_id = ?1",
            params![manifest_id, CompletenessState::Unknown, now_unix_ms()],
        )?;
        log_event(
            &tx,
            "manifest_member_removed",
            Some(object_id),
            Some(manifest_id),
            Some(json!({})),
        )?;
        tx.commit()?;
        Ok(member)
    }

    pub fn get_manifest_member(
        &self,
        manifest_id: &str,
        object_id: &str,
    ) -> StoreResult<Option<ManifestMember>> {
        self.conn
            .query_row(
                "SELECT manifest_id, object_id, layer_id, kv_block_id, token_range_start,
                        token_range_end, required
                 FROM manifest_members WHERE manifest_id = ?1 AND object_id = ?2",
                params![manifest_id, object_id],
                manifest_member_from_row,
            )
            .optional()
            .map_err(StoreError::from)
    }

    pub fn list_manifest_members(&self, manifest_id: &str) -> StoreResult<Vec<ManifestMember>> {
        let mut stmt = self.conn.prepare(
            "SELECT manifest_id, object_id, layer_id, kv_block_id, token_range_start,
                    token_range_end, required
             FROM manifest_members WHERE manifest_id = ?1
             ORDER BY layer_id IS NULL ASC, layer_id ASC,
                      kv_block_id IS NULL ASC, kv_block_id ASC,
                      object_id ASC",
        )?;
        let rows = stmt.query_map(params![manifest_id], manifest_member_from_row)?;
        rows.collect::<Result<Vec<_>, _>>()
            .map_err(StoreError::from)
    }

    pub fn set_manifest_completeness(
        &mut self,
        manifest_id: &str,
        state: CompletenessState,
    ) -> StoreResult<()> {
        let tx = self.conn.transaction()?;
        let changed = tx.execute(
            "UPDATE prefix_manifests
             SET completeness_state = ?2, updated_at_unix_ms = ?3
             WHERE manifest_id = ?1",
            params![manifest_id, state, now_unix_ms()],
        )?;
        if changed == 0 {
            return Err(StoreError::NotFound(manifest_id.to_string()));
        }
        log_event(
            &tx,
            "manifest_completeness_checked",
            None,
            Some(manifest_id),
            Some(json!({"completeness_state": state.as_str()})),
        )?;
        tx.commit()?;
        Ok(())
    }

    pub fn increment_manifest_pin(&mut self, manifest_id: &str) -> StoreResult<Vec<String>> {
        self.get_manifest(manifest_id)?
            .ok_or_else(|| StoreError::NotFound(manifest_id.to_string()))?;
        let members = self.list_manifest_members(manifest_id)?;
        let required_object_ids = members
            .into_iter()
            .filter(|member| member.required)
            .map(|member| member.object_id)
            .collect::<Vec<_>>();

        let tx = self.conn.transaction()?;
        tx.execute(
            "UPDATE prefix_manifests
             SET pin_count = pin_count + 1, updated_at_unix_ms = ?2
             WHERE manifest_id = ?1",
            params![manifest_id, now_unix_ms()],
        )?;
        log_event(
            &tx,
            "manifest_pinned",
            None,
            Some(manifest_id),
            Some(json!({"required_member_count": required_object_ids.len()})),
        )?;
        tx.commit()?;
        Ok(required_object_ids)
    }

    pub fn decrement_manifest_pin(&mut self, manifest_id: &str) -> StoreResult<Vec<String>> {
        let manifest = self
            .get_manifest(manifest_id)?
            .ok_or_else(|| StoreError::NotFound(manifest_id.to_string()))?;
        if manifest.pin_count == 0 {
            return Ok(Vec::new());
        }
        let members = self.list_manifest_members(manifest_id)?;
        let required_object_ids = members
            .into_iter()
            .filter(|member| member.required)
            .map(|member| member.object_id)
            .collect::<Vec<_>>();

        let tx = self.conn.transaction()?;
        tx.execute(
            "UPDATE prefix_manifests
             SET pin_count = pin_count - 1, updated_at_unix_ms = ?2
             WHERE manifest_id = ?1 AND pin_count > 0",
            params![manifest_id, now_unix_ms()],
        )?;
        log_event(
            &tx,
            "manifest_unpinned",
            None,
            Some(manifest_id),
            Some(json!({"required_member_count": required_object_ids.len()})),
        )?;
        tx.commit()?;
        Ok(required_object_ids)
    }

    pub fn eviction_candidates(
        &self,
        policy: EvictionPolicy,
        now_unix_ms: i64,
    ) -> StoreResult<Vec<EvictionCandidate>> {
        let mut sql = String::from(
            "SELECT o.*, l.tier, l.meta_path, l.payload_path, l.bytes_on_disk
             FROM objects o
             INNER JOIN object_locations l ON l.object_id = o.object_id AND l.tier = 'disk'
             WHERE o.pin_count = 0
               AND o.state IN ('committed', 'verified', 'evictable')",
        );
        if policy == EvictionPolicy::TtlExpired {
            sql.push_str(
                " AND o.ttl_expires_at_unix_ms IS NOT NULL
                  AND o.ttl_expires_at_unix_ms <= ?1",
            );
        }
        sql.push_str(match policy {
            EvictionPolicy::Lru | EvictionPolicy::SizeAwareLru => {
                " ORDER BY o.last_accessed_unix_ms IS NOT NULL ASC,
                          o.last_accessed_unix_ms ASC,
                          o.object_id ASC"
            }
            EvictionPolicy::TtlExpired => {
                " ORDER BY o.ttl_expires_at_unix_ms ASC,
                          o.last_accessed_unix_ms IS NOT NULL ASC,
                          o.last_accessed_unix_ms ASC,
                          o.object_id ASC"
            }
        });

        let mut stmt = self.conn.prepare(&sql)?;
        let rows = if policy == EvictionPolicy::TtlExpired {
            stmt.query_map(params![now_unix_ms], |row| {
                eviction_candidate_from_row(row, now_unix_ms, policy)
            })?
            .collect::<Result<Vec<_>, _>>()?
        } else {
            stmt.query_map([], |row| {
                eviction_candidate_from_row(row, now_unix_ms, policy)
            })?
            .collect::<Result<Vec<_>, _>>()?
        };

        let mut candidates = rows;
        if policy == EvictionPolicy::SizeAwareLru {
            candidates.sort_by(|left, right| {
                right
                    .eviction_score
                    .cmp(&left.eviction_score)
                    .then_with(|| {
                        access_sort_key(left.last_accessed_unix_ms)
                            .cmp(&access_sort_key(right.last_accessed_unix_ms))
                    })
                    .then_with(|| right.bytes_on_disk.cmp(&left.bytes_on_disk))
                    .then_with(|| left.object_id.cmp(&right.object_id))
            });
        }
        Ok(candidates)
    }

    pub fn eviction_skip_counts(&self) -> StoreResult<(i64, i64)> {
        let protected_pinned_count = self.conn.query_row(
            "SELECT COUNT(*) FROM objects o
             INNER JOIN object_locations l ON l.object_id = o.object_id AND l.tier = 'disk'
             WHERE o.pin_count > 0",
            [],
            |row| row.get(0),
        )?;
        let skipped_unsafe_count = self.conn.query_row(
            "SELECT COUNT(*) FROM objects o
             LEFT JOIN object_locations l ON l.object_id = o.object_id AND l.tier = 'disk'
             WHERE o.pin_count = 0
               AND (l.object_id IS NULL
                    OR o.state NOT IN ('committed', 'verified', 'evictable'))",
            [],
            |row| row.get(0),
        )?;
        Ok((protected_pinned_count, skipped_unsafe_count))
    }

    pub fn begin_eviction(&mut self, object_id: &str) -> StoreResult<()> {
        let record = self
            .get_object_record(object_id)?
            .ok_or_else(|| StoreError::NotFound(object_id.to_string()))?;
        if !can_evict(record.state, record.pin_count) {
            return Err(StoreError::Eviction(format!(
                "object {object_id} in state {} with pin_count {} cannot be evicted",
                record.state, record.pin_count
            )));
        }
        // TODO(manifests): skip objects that are required members of pinned manifests.
        ensure_valid_state_transition(record.state, ObjectState::Evicting)?;

        let tx = self.conn.transaction()?;
        tx.execute(
            "UPDATE objects SET state = ?2 WHERE object_id = ?1 AND pin_count = 0",
            params![object_id, ObjectState::Evicting],
        )?;
        log_event(
            &tx,
            "object_eviction_started",
            Some(object_id),
            None,
            Some(json!({"from": record.state.as_str()})),
        )?;
        tx.commit()?;
        Ok(())
    }

    pub fn finish_eviction(&mut self, object_id: &str, bytes_freed: i64) -> StoreResult<()> {
        let record = self
            .get_object_record(object_id)?
            .ok_or_else(|| StoreError::NotFound(object_id.to_string()))?;
        ensure_valid_state_transition(record.state, ObjectState::Evicted)?;

        let tx = self.conn.transaction()?;
        tx.execute(
            "DELETE FROM object_locations WHERE object_id = ?1 AND tier = 'disk'",
            params![object_id],
        )?;
        tx.execute(
            "UPDATE objects SET state = ?2 WHERE object_id = ?1",
            params![object_id, ObjectState::Evicted],
        )?;
        log_event(
            &tx,
            "object_evicted",
            Some(object_id),
            None,
            Some(json!({"from": record.state.as_str(), "bytes_freed": bytes_freed})),
        )?;
        tx.commit()?;
        Ok(())
    }

    pub fn mark_missing_after_eviction_failure(
        &mut self,
        object_id: &str,
        reason: &str,
    ) -> StoreResult<()> {
        let record = self
            .get_object_record(object_id)?
            .ok_or_else(|| StoreError::NotFound(object_id.to_string()))?;
        let tx = self.conn.transaction()?;
        tx.execute(
            "UPDATE objects SET state = ?2, quarantine_reason = ?3 WHERE object_id = ?1",
            params![object_id, ObjectState::Missing, reason],
        )?;
        log_event(
            &tx,
            "object_eviction_failed",
            Some(object_id),
            None,
            Some(json!({"from": record.state.as_str(), "to": "missing", "reason": reason})),
        )?;
        tx.commit()?;
        Ok(())
    }

    pub fn list_objects(&self, filter: &ObjectListFilter) -> StoreResult<Vec<ObjectRecord>> {
        let mut sql = String::from(
            "SELECT o.* FROM objects o
             LEFT JOIN object_compatibility c ON c.object_id = o.object_id",
        );
        let mut clauses = Vec::new();
        let mut values = Vec::new();

        push_text_filter(
            &mut clauses,
            &mut values,
            "o.state",
            filter.state.map(|s| s.as_str()),
        );
        push_text_filter(
            &mut clauses,
            &mut values,
            "c.model_hash",
            filter.model_hash.as_deref(),
        );
        push_text_filter(
            &mut clauses,
            &mut values,
            "c.prefix_hash",
            filter.prefix_hash.as_deref(),
        );
        push_text_filter(
            &mut clauses,
            &mut values,
            "c.engine_name",
            filter.engine_name.as_deref(),
        );
        push_text_filter(
            &mut clauses,
            &mut values,
            "c.opaque_engine_key_hash",
            filter.opaque_engine_key_hash.as_deref(),
        );
        push_i64_filter(&mut clauses, &mut values, "c.layer_id", filter.layer_id);
        push_i64_filter(
            &mut clauses,
            &mut values,
            "c.kv_block_id",
            filter.kv_block_id,
        );

        if !clauses.is_empty() {
            sql.push_str(" WHERE ");
            sql.push_str(&clauses.join(" AND "));
        }
        sql.push_str(" ORDER BY o.object_id ASC");
        match (filter.limit, filter.offset) {
            (Some(limit), _) => {
                sql.push_str(" LIMIT ?");
                values.push(Value::Integer(limit));
            }
            (None, Some(_)) => {
                sql.push_str(" LIMIT -1");
            }
            (None, None) => {}
        }
        if let Some(offset) = filter.offset {
            sql.push_str(" OFFSET ?");
            values.push(Value::Integer(offset));
        }

        let mut stmt = self.conn.prepare(&sql)?;
        let rows = stmt.query_map(params_from_iter(values.iter()), object_record_from_row)?;
        rows.collect::<Result<Vec<_>, _>>()
            .map_err(StoreError::from)
    }

    pub fn store_stats(&self) -> StoreResult<StoreStats> {
        let mut stats = StoreStats::default();
        let mut stmt = self.conn.prepare(
            "SELECT state, COUNT(*), COALESCE(SUM(byte_length), 0),
                    COALESCE(SUM(pin_count), 0), COALESCE(SUM(access_count), 0)
             FROM objects GROUP BY state",
        )?;
        let mut rows = stmt.query([])?;
        while let Some(row) = rows.next()? {
            let state: ObjectState = row.get(0)?;
            let count: i64 = row.get(1)?;
            stats.object_count += count;
            stats.total_logical_bytes += row.get::<_, i64>(2)?;
            stats.total_pin_count += row.get::<_, i64>(3)?;
            stats.total_access_count += row.get::<_, i64>(4)?;
            match state {
                ObjectState::Staging => stats.staging_count = count,
                ObjectState::Committed => stats.committed_count = count,
                ObjectState::Verified => stats.verified_count = count,
                ObjectState::Pinned => stats.pinned_count = count,
                ObjectState::Evictable => stats.evictable_count = count,
                ObjectState::Evicting => stats.evicting_count = count,
                ObjectState::Evicted => stats.evicted_count = count,
                ObjectState::Quarantined => stats.quarantined_count = count,
                ObjectState::Missing => stats.missing_count = count,
                ObjectState::Corrupt => stats.corrupt_count = count,
            }
        }
        stats.total_bytes_on_disk = self.conn.query_row(
            "SELECT COALESCE(SUM(bytes_on_disk), 0) FROM object_locations",
            [],
            |row| row.get(0),
        )?;
        Ok(stats)
    }

    pub fn store_events(&self) -> StoreResult<Vec<StoreEvent>> {
        let mut stmt = self.conn.prepare(
            "SELECT event_id, timestamp_unix_ms, event_type, object_id, manifest_id, details_json
             FROM store_events ORDER BY event_id ASC",
        )?;
        let rows = stmt.query_map([], store_event_from_row)?;
        rows.collect::<Result<Vec<_>, _>>()
            .map_err(StoreError::from)
    }

    pub fn list_object_locations(&self) -> StoreResult<Vec<ObjectLocation>> {
        let mut stmt = self.conn.prepare(
            "SELECT object_id, tier, meta_path, payload_path, bytes_on_disk
             FROM object_locations ORDER BY object_id ASC, tier ASC",
        )?;
        let rows = stmt.query_map([], object_location_from_row)?;
        rows.collect::<Result<Vec<_>, _>>()
            .map_err(StoreError::from)
    }

    pub fn list_all_manifest_members(&self) -> StoreResult<Vec<ManifestMember>> {
        let mut stmt = self.conn.prepare(
            "SELECT manifest_id, object_id, layer_id, kv_block_id, token_range_start,
                    token_range_end, required
             FROM manifest_members
             ORDER BY manifest_id ASC, object_id ASC",
        )?;
        let rows = stmt.query_map([], manifest_member_from_row)?;
        rows.collect::<Result<Vec<_>, _>>()
            .map_err(StoreError::from)
    }

    pub fn fsck_mark_object_state(
        &mut self,
        object_id: &str,
        state: ObjectState,
        reason: &str,
    ) -> StoreResult<()> {
        let record = self
            .get_object_record(object_id)?
            .ok_or_else(|| StoreError::NotFound(object_id.to_string()))?;
        let tx = self.conn.transaction()?;
        tx.execute(
            "UPDATE objects SET state = ?2, quarantine_reason = ?3 WHERE object_id = ?1",
            params![object_id, state, reason],
        )?;
        log_event(
            &tx,
            "fsck_object_state",
            Some(object_id),
            None,
            Some(json!({"from": record.state.as_str(), "to": state.as_str(), "reason": reason})),
        )?;
        tx.commit()?;
        Ok(())
    }

    pub fn fsck_update_location(
        &mut self,
        object_id: &str,
        meta_path: &str,
        payload_path: &str,
        bytes_on_disk: i64,
    ) -> StoreResult<()> {
        let tx = self.conn.transaction()?;
        let changed = tx.execute(
            "UPDATE object_locations
             SET meta_path = ?2, payload_path = ?3, bytes_on_disk = ?4
             WHERE object_id = ?1 AND tier = 'disk'",
            params![object_id, meta_path, payload_path, bytes_on_disk],
        )?;
        if changed == 0 {
            return Err(StoreError::NotFound(object_id.to_string()));
        }
        log_event(
            &tx,
            "fsck_location_updated",
            Some(object_id),
            None,
            Some(json!({"bytes_on_disk": bytes_on_disk})),
        )?;
        tx.commit()?;
        Ok(())
    }

    pub fn fsck_update_byte_length(
        &mut self,
        object_id: &str,
        byte_length: i64,
    ) -> StoreResult<()> {
        let tx = self.conn.transaction()?;
        let changed = tx.execute(
            "UPDATE objects SET byte_length = ?2 WHERE object_id = ?1",
            params![object_id, byte_length],
        )?;
        if changed == 0 {
            return Err(StoreError::NotFound(object_id.to_string()));
        }
        log_event(
            &tx,
            "fsck_byte_length_updated",
            Some(object_id),
            None,
            Some(json!({"byte_length": byte_length})),
        )?;
        tx.commit()?;
        Ok(())
    }
}

fn configure_connection(conn: &Connection) -> StoreResult<()> {
    conn.pragma_update(None, "foreign_keys", "ON")?;
    conn.busy_timeout(Duration::from_secs(5))?;
    conn.pragma_update(None, "journal_mode", "WAL")?;
    conn.pragma_update(None, "synchronous", "FULL")?;
    Ok(())
}

fn log_event(
    tx: &rusqlite::Transaction<'_>,
    event_type: &str,
    object_id: Option<&str>,
    manifest_id: Option<&str>,
    details: Option<serde_json::Value>,
) -> StoreResult<()> {
    let details_json = details.map(|value| value.to_string());
    tx.execute(
        "INSERT INTO store_events(timestamp_unix_ms, event_type, object_id, manifest_id, details_json)
         VALUES (?1, ?2, ?3, ?4, ?5)",
        params![now_unix_ms(), event_type, object_id, manifest_id, details_json],
    )?;
    Ok(())
}

fn event_type_for_transition(state: ObjectState) -> &'static str {
    match state {
        ObjectState::Quarantined => "object_quarantined",
        ObjectState::Evicted => "object_evicted",
        _ => "object_state_transition",
    }
}

fn push_text_filter(
    clauses: &mut Vec<&'static str>,
    values: &mut Vec<Value>,
    column: &'static str,
    value: Option<&str>,
) {
    if let Some(value) = value {
        clauses.push(match column {
            "o.state" => "o.state = ?",
            "c.model_hash" => "c.model_hash = ?",
            "c.prefix_hash" => "c.prefix_hash = ?",
            "c.engine_name" => "c.engine_name = ?",
            "c.opaque_engine_key_hash" => "c.opaque_engine_key_hash = ?",
            _ => unreachable!("unsupported text filter"),
        });
        values.push(Value::Text(value.to_string()));
    }
}

fn push_i64_filter(
    clauses: &mut Vec<&'static str>,
    values: &mut Vec<Value>,
    column: &'static str,
    value: Option<i64>,
) {
    if let Some(value) = value {
        clauses.push(match column {
            "c.layer_id" => "c.layer_id = ?",
            "c.kv_block_id" => "c.kv_block_id = ?",
            _ => unreachable!("unsupported integer filter"),
        });
        values.push(Value::Integer(value));
    }
}

fn object_record_from_row(row: &Row<'_>) -> rusqlite::Result<ObjectRecord> {
    Ok(ObjectRecord {
        object_id: row.get("object_id")?,
        object_type: row.get("object_type")?,
        schema_version: row.get("schema_version")?,
        descriptor_hash: row.get("descriptor_hash")?,
        payload_hash: row.get("payload_hash")?,
        byte_length: row.get("byte_length")?,
        state: row.get("state")?,
        created_at_unix_ms: row.get("created_at_unix_ms")?,
        committed_at_unix_ms: row.get("committed_at_unix_ms")?,
        verified_at_unix_ms: row.get("verified_at_unix_ms")?,
        last_accessed_unix_ms: row.get("last_accessed_unix_ms")?,
        access_count: row.get("access_count")?,
        pin_count: row.get("pin_count")?,
        ttl_expires_at_unix_ms: row.get("ttl_expires_at_unix_ms")?,
        quarantine_reason: row.get("quarantine_reason")?,
    })
}

fn object_location_from_row(row: &Row<'_>) -> rusqlite::Result<ObjectLocation> {
    Ok(ObjectLocation {
        object_id: row.get("object_id")?,
        tier: row.get("tier")?,
        meta_path: row.get("meta_path")?,
        payload_path: row.get("payload_path")?,
        bytes_on_disk: row.get("bytes_on_disk")?,
    })
}

fn object_compatibility_from_row(row: &Row<'_>) -> rusqlite::Result<ObjectCompatibility> {
    Ok(ObjectCompatibility {
        object_id: row.get("object_id")?,
        model_hash: row.get("model_hash")?,
        tokenizer_hash: row.get("tokenizer_hash")?,
        config_hash: row.get("config_hash")?,
        rope_config_hash: row.get("rope_config_hash")?,
        dtype: row.get("dtype")?,
        engine_name: row.get("engine_name")?,
        engine_version: row.get("engine_version")?,
        integration_name: row.get("integration_name")?,
        kv_cache_format: row.get("kv_cache_format")?,
        prefix_hash: row.get("prefix_hash")?,
        token_range_start: row.get("token_range_start")?,
        token_range_end: row.get("token_range_end")?,
        layer_id: row.get("layer_id")?,
        kv_block_id: row.get("kv_block_id")?,
        opaque_engine_key_hash: row.get("opaque_engine_key_hash")?,
    })
}

fn object_access_from_row(row: &Row<'_>) -> rusqlite::Result<ObjectAccess> {
    Ok(ObjectAccess {
        object_id: row.get("object_id")?,
        last_get_unix_ms: row.get("last_get_unix_ms")?,
        last_put_unix_ms: row.get("last_put_unix_ms")?,
        get_count: row.get("get_count")?,
        put_count: row.get("put_count")?,
        bytes_read_total: row.get("bytes_read_total")?,
        bytes_written_total: row.get("bytes_written_total")?,
    })
}

fn store_event_from_row(row: &Row<'_>) -> rusqlite::Result<StoreEvent> {
    Ok(StoreEvent {
        event_id: row.get("event_id")?,
        timestamp_unix_ms: row.get("timestamp_unix_ms")?,
        event_type: row.get("event_type")?,
        object_id: row.get("object_id")?,
        manifest_id: row.get("manifest_id")?,
        details_json: row.get("details_json")?,
    })
}

fn manifest_record_from_row(row: &Row<'_>) -> rusqlite::Result<ManifestRecord> {
    Ok(ManifestRecord {
        manifest_id: row.get("manifest_id")?,
        manifest_type: row.get("manifest_type")?,
        model_hash: row.get("model_hash")?,
        tokenizer_hash: row.get("tokenizer_hash")?,
        rope_config_hash: row.get("rope_config_hash")?,
        prefix_hash: row.get("prefix_hash")?,
        token_range_start: row.get("token_range_start")?,
        token_range_end: row.get("token_range_end")?,
        completeness_state: row.get("completeness_state")?,
        created_at_unix_ms: row.get("created_at_unix_ms")?,
        updated_at_unix_ms: row.get("updated_at_unix_ms")?,
        pin_count: row.get("pin_count")?,
    })
}

fn manifest_member_from_row(row: &Row<'_>) -> rusqlite::Result<ManifestMember> {
    let required: i64 = row.get("required")?;
    Ok(ManifestMember {
        manifest_id: row.get("manifest_id")?,
        object_id: row.get("object_id")?,
        layer_id: row.get("layer_id")?,
        kv_block_id: row.get("kv_block_id")?,
        token_range_start: row.get("token_range_start")?,
        token_range_end: row.get("token_range_end")?,
        required: required != 0,
    })
}

fn eviction_candidate_from_row(
    row: &Row<'_>,
    now_unix_ms: i64,
    policy: EvictionPolicy,
) -> rusqlite::Result<EvictionCandidate> {
    let record = object_record_from_row(row)?;
    let location = ObjectLocation {
        object_id: record.object_id.clone(),
        tier: row.get("tier")?,
        meta_path: row.get("meta_path")?,
        payload_path: row.get("payload_path")?,
        bytes_on_disk: row.get("bytes_on_disk")?,
    };
    let eviction_score = match policy {
        EvictionPolicy::SizeAwareLru => {
            let last_access = record.last_accessed_unix_ms.unwrap_or(0);
            let age_ms = now_unix_ms.saturating_sub(last_access).max(0) as i128;
            age_ms * location.bytes_on_disk.max(0) as i128
        }
        EvictionPolicy::Lru | EvictionPolicy::TtlExpired => 0,
    };
    Ok(EvictionCandidate::from_record_location(
        &record,
        &location,
        eviction_score,
    ))
}

fn access_sort_key(value: Option<i64>) -> (bool, i64) {
    (value.is_some(), value.unwrap_or(i64::MIN))
}

fn now_unix_ms() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("system clock is before unix epoch")
        .as_millis() as i64
}

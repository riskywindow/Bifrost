# Phase 3 Manifest Model

Last verified: 2026-05-30

## Purpose

Manifests describe groups of local KV objects that together represent a
reusable prefix or session. They are local store metadata and do not alter Phase
1 object identity.

## Manifest records

The Rust store persists manifests in `prefix_manifests`. The table name is
historical; rows are distinguished by `manifest_type`.

Supported manifest types:

1. `prefix_manifest`.
2. `session_manifest`.

Manifest fields:

1. `manifest_id`.
2. `manifest_type`.
3. `model_hash`.
4. `tokenizer_hash`.
5. `rope_config_hash`.
6. `prefix_hash`.
7. `token_range_start`.
8. `token_range_end`.
9. `completeness_state`.
10. `created_at_unix_ms`.
11. `updated_at_unix_ms`.
12. `pin_count`.

`manifest_id` is a deterministic local identifier derived from the manifest
type, model and prefix identity fields, and token range. It is local store
metadata and is not part of immutable KV object identity.

## Prefix manifest

A prefix manifest is an ordered set of member object references for a reusable
prompt prefix, document prefix, retrieval block, or synthetic benchmark prefix.

The prefix identity is `model_hash`, optional `tokenizer_hash`, optional
`rope_config_hash`, `prefix_hash`, and the manifest token range. Member objects
must match those fields when the field is present on the manifest and their
token range must be inside the manifest token range.

## Session manifest

A session manifest extends the same stored model to a session-scoped set of KV
objects by using `manifest_type = session_manifest`.

Session manifests are still local metadata. They must not imply real model KV
extraction, real KV injection, or integration with inference engines.

## Manifest completeness

A manifest is complete only when every required member is:

1. Present in the catalog.
2. In `verified`, `pinned`, or otherwise servable state.
3. Present in a committed location.
4. File-level integrity checked.
5. Compatible with the manifest target profile.

Optional members may be absent without making the manifest incomplete, but
queries must distinguish missing required members from missing optional members.

Completeness states are:

1. `complete`
2. `incomplete`
3. `corrupt`
4. `unknown`

For Phase 3 the deterministic check is:

1. Without expected coverage, every declared required member must be present,
   servable through the same catalog/filesystem checks used by GET, and
   compatible with the manifest identity.
2. With expected layer/block counts or explicit expected members, every
   expected required layer/block member must also be declared.
3. If a required member is corrupt or incompatible, the manifest is reported as
   `corrupt`; if required members are absent, evicted, missing, quarantined, or
   otherwise unavailable, the manifest is `incomplete`.

Completeness must be recomputed or invalidated when object state changes,
object files move, objects are evicted, objects are quarantined, pins change
availability policy, or fsck changes catalog state.

## Member object references

Manifest members reference objects by immutable `object_id` plus local ordering
metadata:

```text
manifest_id
object_id
layer_id
kv_block_id
token_range_start
token_range_end
required
```

Member order is deterministic by `layer_id`, `kv_block_id`, then `object_id`.

The member table must not copy mutable local file paths as identity. File paths
belong in object location records.

The implementation rejects member insertion unless the referenced object is
currently serveable through the same catalog and filesystem checks used by
GET. This keeps new manifests fail-closed: quarantined, corrupt, missing,
evicted, staging, or catalog-inconsistent objects cannot become declared
members. Existing members that later become unavailable remain declared but
make completeness checks and missing-block queries report deterministic
missing reasons.

## Manifest pins

`pin_manifest` increments the manifest `pin_count` and also increments
`pin_count` on every required member object. `unpin_manifest` decrements both
the manifest pin count and those required member object pin counts. This reuses
the existing eviction rule that objects with `pin_count > 0` are not candidates
under any eviction policy.

If a required member is added to an already-pinned manifest, the object receives
the current manifest pin count. Removing a required member from a pinned
manifest reverses that protection for the removed object.

## Missing block queries

The store supports a query that reports which manifest members are not currently
servable:

```text
manifest_missing(manifest_id) -> [
  { object_id, layer_id, kv_block_id, required, reason }
]
```

Reasons are deterministic and specific:

1. `object_absent`
2. `object_staging`
3. `object_missing`
4. `object_corrupt`
5. `object_quarantined`
6. `object_evicted`
7. `compatibility_mismatch`
8. `catalog_inconsistent`
9. `file_integrity_unknown`
10. `expected_member_missing`

Missing-block queries must fail closed. If the store cannot prove that a member
is servable, the member is reported missing or suspect.

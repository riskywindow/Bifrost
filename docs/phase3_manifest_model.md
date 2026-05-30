# Phase 3 Manifest Model

Last verified: 2026-05-30

## Purpose

Manifests describe groups of local KV objects that together represent a
reusable prefix or session. They are local store metadata and do not alter Phase
1 object identity.

## Prefix manifest

A prefix manifest is an ordered set of member object references for a reusable
prompt prefix, document prefix, retrieval block, or synthetic benchmark prefix.

Required fields:

1. `manifest_id`.
2. `prefix_key`.
3. `target_profile_hash`.
4. `manifest_kind = prefix`.
5. Ordered member references.
6. Completeness flag or derived completeness query.

The prefix key must be deterministic for tests. It may be a caller-provided key
or a hash of canonical prefix metadata, but it must not depend on mutable local
paths or access counters.

## Session manifest

A session manifest is optional in Phase 3. It extends the same model to a
session-scoped set of KV objects.

Required fields if implemented:

1. `manifest_id`.
2. `session_key`.
3. `target_profile_hash`.
4. `manifest_kind = session`.
5. Ordered member references.
6. Completeness flag or derived completeness query.

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

Completeness must be recomputed or invalidated when object state changes,
object files move, objects are evicted, objects are quarantined, pins change
availability policy, or fsck changes catalog state.

## Member object references

Manifest members reference objects by immutable `object_id` plus local ordering
metadata:

```text
manifest_id
member_index
object_id
required
role
byte_start
byte_end
```

`member_index` defines deterministic order. `role` should be a small explicit
string such as `prefix_block`, `session_block`, or `benchmark_block`.

The member table must not copy mutable local file paths as identity. File paths
belong in object location records.

## Missing block queries

The store should support a query that reports which manifest members are not
currently servable:

```text
manifest_missing(manifest_id) -> [
  { member_index, object_id, required, reason }
]
```

Reasons should be deterministic and specific:

1. `object_absent`
2. `object_staging`
3. `object_missing`
4. `object_corrupt`
5. `object_quarantined`
6. `object_evicted`
7. `compatibility_mismatch`
8. `catalog_inconsistent`
9. `file_integrity_unknown`

Missing-block queries must fail closed. If the store cannot prove that a member
is servable, the member is reported missing or suspect.

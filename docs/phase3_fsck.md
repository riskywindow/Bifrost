# Phase 3 fsck

Last verified: 2026-05-30

## Purpose

`fsck` reconciles the Phase 3 catalog with the local filesystem. It detects
catalog drift, missing files, orphan files, corrupt objects, and manifest
inconsistency. It must fail closed: suspect objects are unavailable until full
validation proves they are safe.

## Catalog vs filesystem reconciliation

The reconciliation walk compares:

1. `objects` catalog rows.
2. `object_locations` catalog rows.
3. Descriptor files under the committed object layout.
4. Payload files under the committed object layout.
5. Quarantine directories.
6. Staging directories.
7. Manifest membership rows.

Expected matches:

1. Every servable object has an `objects` row.
2. Every servable object has a present disk `object_locations` row.
3. Descriptor and payload files exist at the recorded paths.
4. File lengths match catalog lengths.
5. File hashes match catalog hashes.
6. Phase 1 validation succeeds for descriptor, payload, object ID, and target
   compatibility.

Any uncertainty makes the object unavailable.

## Corruption detection

An object is corrupt when bytes are present but fail one or more checks:

1. Descriptor file hash mismatch.
2. Payload file hash mismatch.
3. Descriptor parse failure.
4. Descriptor hash mismatch.
5. Payload hash mismatch.
6. Object ID mismatch.
7. Target compatibility validation failure.
8. Conflicting catalog rows for the same object ID.

Corrupt bytes must not be served. fsck should quarantine the bytes or mark the
object `corrupt` with a deterministic reason.

## Orphan detection

An orphan is a committed-looking descriptor or payload file with no consistent
catalog row.

Examples:

1. Descriptor and payload exist but no `objects` row exists.
2. A payload file exists without a descriptor file.
3. A descriptor file exists without a payload file.
4. Files exist under an unexpected object ID path.
5. Files exist in staging after restart.

Orphans are not cache hits. Repair may import an orphan only if descriptor and
payload files form a complete valid Phase 1 object and the derived object ID
matches the committed path. Otherwise fsck must quarantine or ignore them.

## Missing object detection

An object is missing when catalog rows reference files that are absent.

fsck should identify:

1. Missing descriptor file.
2. Missing payload file.
3. Missing location row.
4. Present location row with `present = 0`.
5. Manifest members referencing missing or unavailable objects.

Missing objects must not be served. Manifest missing-block queries should report
required missing members.

## Quarantine behavior

Quarantine is for suspect bytes and records that should be preserved for
inspection but excluded from serving.

Quarantine actions should:

1. Move suspect files out of committed paths where practical.
2. Record the deterministic reason in the catalog and store events.
3. Preserve enough metadata to debug the finding.
4. Avoid overwriting an existing quarantine directory.
5. Never create a servable object as a side effect.

If a file cannot be moved safely, fsck should mark the object unavailable and
record the failed quarantine action.

## Repair behavior

Repair must be conservative and explicit. A dry-run mode should report planned
actions without mutation.

Allowed repairs:

1. Mark missing objects unavailable.
2. Mark corrupt objects unavailable and quarantine suspect bytes.
3. Remove stale location rows for files that no longer exist.
4. Recompute access-derived stats that do not affect identity.
5. Import a fully valid orphan committed object after Phase 1 validation.
6. Refresh file-level hashes and checked timestamps after validation.
7. Recompute manifest completeness from member availability.

Disallowed repairs:

1. Trust catalog hashes over file bytes.
2. Recreate missing payload bytes.
3. Rewrite immutable descriptors to fit catalog state.
4. Mark an object verified without Phase 1 validation.
5. Serve from staging.
6. Evict or delete pinned objects as a repair shortcut.

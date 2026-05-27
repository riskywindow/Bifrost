# Phase 2 Spool

Last verified: 2026-05-27

## Purpose

The Phase 2 spool is a minimal local object spool for synthetic KV transport
tests. It stores objects received by the Phase 2 daemon after reassembly and
Phase 1 Rust validation.

The spool is not a production object store. It has no eviction policy, no
pinning, no tiering, no cache scoring, and no distributed metadata service.

## Minimal layout

A spool root contains separate staging and committed areas:

```text
spool/
  staging/
    {request_id}/
      descriptor.json
      payload.part
      chunks/
        {chunk_index}.chunk
      transfer.json
  objects/
    {object_id}/
      descriptor.json
      payload.bin
      record.json
  quarantine/
    {request_id-or-object_id}/
      reason.txt
```

`transfer.json` is mutable local transfer state. `record.json` is mutable local
spool state for a committed object. Neither file is part of immutable Phase 1
object identity.

## Staging vs committed objects

Staging contains incomplete or untrusted transfer state.

Objects in staging are not servable. `HAS` must return false for staged objects,
and `GET` must return a miss or rejection without reading staged payload bytes
as a valid object.

Committed objects have passed:

1. Complete chunk receipt.
2. Reassembly to the declared payload length.
3. Full payload hash validation.
4. Descriptor hash validation.
5. Object ID validation.
6. Target compatibility validation using the Phase 1 Rust validator.
7. Atomic move into the committed object path.

Only committed objects are servable.

## Atomic commit rule

Commit is a transition from staging to `objects/{object_id}`.

The daemon must not create a committed object path until all chunks and full
object validation pass. The commit operation should write final descriptor,
payload, and record files in a temporary location under the spool root, fsync
where the platform supports it, then use an atomic rename into the committed
object path.

If the final object path already exists, the daemon must verify that the
existing committed descriptor and payload match the same object identity. If
they match, the PUT may be reported as already committed. If they do not match,
the daemon must reject the PUT and quarantine the conflicting staging state.

## Crash and partial-transfer behavior

After restart, the daemon must scan `staging/` before accepting requests.

Incomplete staging records may be removed or quarantined. They must not be
promoted without reassembly and Phase 1 validation.

Committed records should be treated conservatively. If a committed object is
missing its descriptor, payload, or record, or if validation cannot prove the
object identity, the object must not be served. The daemon may quarantine or
ignore the entry.

Partial transfers are never cache hits.

## No cache policy in Phase 2

Phase 2 does not implement:

1. Eviction.
2. Pinning.
3. Expiration.
4. Tiering.
5. LRU or LFU metadata.
6. Admission policy.
7. Remote replication policy.

Metrics may count committed bytes and object counts, but they must not imply a
production cache policy.

# Phase 3 Eviction

Last verified: 2026-05-30

## Purpose

Phase 3 introduces deterministic local eviction for the durable KV object store.
Eviction frees local tier capacity without serving partial or wrong KV state.

Eviction is local metadata and file management. It must not change immutable
object identity.

## Common rules

All eviction policies must follow these rules:

1. Never evict pinned objects.
2. Never select staging objects as normal cache victims.
3. Never serve an object while its bytes are partially removed.
4. Use transaction boundaries so catalog state and filesystem state reconcile.
5. Emit store events for eviction start, failures, and final evicted state.
6. Use injected clock values in tests.
7. Break ties deterministically by `object_id`.

Objects that fail during eviction move to a conservative unavailable state. The
current implementation marks deletion failures as `missing` and records an
`object_eviction_failed` event.

Phase 3 deletes local descriptor and payload files and removes the disk
`object_locations` row after successful deletion. The object row remains as an
`evicted` tombstone so events and stats can account for the object without
making it servable.

## LRU

LRU evicts the least recently accessed eligible objects first.

Eligibility:

1. Object state is `committed`, `verified`, or `evictable`.
2. Object is not pinned.
3. Object is present in the target tier.
4. Object is not already evicting, quarantined, missing, or corrupt.
5. Object is not staging or already evicted.

Future manifest work must extend eligibility so objects that are required
members of pinned manifests are skipped.

Ordering:

```text
last_access_unix_ms ASC NULLS FIRST, object_id ASC
```

The implementation must define how never-accessed objects sort. The default is
to evict never-accessed objects before accessed objects.

## Size-aware LRU

Size-aware LRU prefers old large objects using a fixed score:

```text
score = max(0, policy_now_unix_ms - last_access_unix_ms_or_0) * bytes_on_disk
```

Ordering:

```text
score DESC,
last_access_unix_ms ASC NULLS FIRST,
bytes_on_disk DESC,
object_id ASC
```

Never-accessed objects use `0` as the last-access timestamp for score
calculation, making them older than accessed objects for any normal positive
policy clock.

## TTL expiration

TTL expiration evicts objects whose `ttl_expires_at_unix_ms` is less than or
equal to the policy clock.

Ordering:

```text
expires_at_unix_ms ASC, last_access_unix_ms ASC NULLS FIRST, object_id ASC
```

Expired pinned objects must be skipped and reported as protected. Expiry alone
must not override pinning.

## Target-byte eviction

Target-byte eviction evicts in the selected policy order until
`total_bytes_on_disk <= target_bytes`, `max_objects` is reached, or no eligible
objects remain.

Inputs:

1. Policy name.
2. Target tier.
3. Optional target bytes.
4. Fixed clock value.
5. Optional dry-run flag.

Outputs:

1. Candidate count.
2. Victim object IDs in deterministic order.
3. Bytes planned.
4. Bytes actually freed.
5. Objects skipped because pinned.
6. Objects skipped because unsafe or unavailable.
7. Final reason if the target could not be reached.

Dry runs must not mutate files or catalog rows. They return the same candidate
ordering and planned bytes that an apply run would use with the same catalog
state and clock input.

## Pinned object protection

Pinned protection is absolute for disk eviction in Phase 3.

Required tests:

1. LRU skips pinned objects.
2. Size-aware LRU skips pinned objects even when they are large.
3. TTL skips expired pinned objects.
4. Target-byte eviction reports shortfall rather than evicting pinned objects.
5. Unpinning makes an otherwise eligible object available to eviction.
6. Pinning an object in `corrupt`, `missing`, or `quarantined` state does not
   make it servable.

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
5. Emit store events for victim selection, deletion, failures, and final state.
6. Use injected clock values in tests.
7. Break ties deterministically by `object_id`.

Objects that fail during eviction should move to a conservative state such as
`quarantined`, `missing`, or `corrupt`.

## LRU

LRU evicts the least recently accessed eligible objects first.

Eligibility:

1. Object is committed and verified.
2. Object is not pinned.
3. Object is present in the target tier.
4. Object is not already evicting, quarantined, missing, or corrupt.

Ordering:

```text
last_access_unix_ms ASC NULLS FIRST, object_id ASC
```

The implementation must define how never-accessed objects sort. The default is
to evict never-accessed objects before accessed objects.

## Size-aware LRU

Size-aware LRU uses the same recency ordering but accounts for bytes freed.

The simplest deterministic form is:

1. Order eligible victims by LRU order.
2. Select victims until `target_bytes` would be reached or no more eligible
   objects exist.
3. Report selected bytes and remaining bytes.

An alternate score-based policy may be added later, but it must document the
score formula, tie-breakers, and fixed clock inputs before implementation.

## TTL expiration

TTL expiration evicts objects whose `expires_at_unix_ms` is less than or equal
to the policy clock.

Ordering:

```text
expires_at_unix_ms ASC, last_access_unix_ms ASC NULLS FIRST, object_id ASC
```

Expired pinned objects must be skipped and reported as protected. Expiry alone
must not override pinning.

## Target-byte eviction

Target-byte eviction attempts to free at least a requested number of bytes from
a target tier.

Inputs:

1. Policy name.
2. Target tier.
3. Target bytes.
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

Dry runs must not mutate files or catalog rows.

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

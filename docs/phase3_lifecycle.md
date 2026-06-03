# Phase 3 Object Lifecycle

Last verified: 2026-05-30

## Purpose

The lifecycle model defines local store states. These states describe local
records and must never be included in Phase 1 immutable object identity.

## States

### staging

The object is incomplete, untrusted, or still owned by transfer/import staging.

Rules:

1. Never serve.
2. Never list as a cache hit.
3. Never include as an available manifest member.
4. May be removed or quarantined after restart or timeout.

### committed

The object bytes have been atomically moved into the committed disk layout.

Rules:

1. Commit requires complete bytes and Phase 1 validation.
2. Commit does not remove the need for later integrity checks.
3. A committed object with missing catalog or file evidence is unavailable.
4. A committed catalog row is not servable and cannot be pinned into
   availability; it must first complete the verified transition.

### verified

The committed object currently has valid descriptor bytes, payload bytes,
file-level hashes, descriptor hash, payload hash, object ID, and target
compatibility.

Rules:

1. Only verified objects may be served.
2. Verification may be refreshed by GET, fsck, startup scan, or explicit CLI.
3. Verification is invalidated by missing files, changed file hashes, catalog
   drift, or compatibility uncertainty.

### pinned

The object is protected from disk eviction.

Rules:

1. Pinned objects must never be eviction victims.
2. Pinning does not make an invalid object servable.
3. Unpinning returns the object to normal eligibility based on state, expiry,
   and policy.

### evictable

The object is verified, unpinned, and eligible for a configured eviction policy.

Rules:

1. Eligibility must be recomputed from catalog state and policy inputs.
2. Evictable objects remain servable until they enter `evicting`.
3. Victim selection must be deterministic and testable.

### evicting

The object has been selected for eviction and is being removed from one or more
tiers.

Rules:

1. New GET responses should report miss or retryable unavailable status.
2. Eviction must not remove pinned objects.
3. Partial eviction failures must leave a conservative state such as
   `quarantined`, `missing`, or `corrupt`.

### evicted

The object is no longer present in the local tier from which it was evicted.

Rules:

1. GET and HAS report miss.
2. The catalog may retain a tombstone or event history.
3. Manifest missing-block queries should report required evicted members as
   missing.

### quarantined

The object is isolated because the store found a suspect, conflicting, partial,
or unsafe state.

Rules:

1. Never serve.
2. Record a deterministic reason.
3. Repair may restore the object only after full validation succeeds.

### missing

The catalog references object bytes that are not present where expected.

Rules:

1. Never serve.
2. fsck should record missing descriptor and payload paths separately.
3. Repair may remove stale catalog rows, mark a tombstone, or restore from a
   validated committed copy if one exists.

### corrupt

The object bytes are present but fail integrity, identity, or compatibility
validation.

Rules:

1. Never serve.
2. Quarantine suspect bytes.
3. Do not repair by trusting catalog metadata over file contents.

## Valid transitions

Typical successful path:

```text
staging -> committed -> verified -> evictable -> evicting -> evicted
```

Pinning overlays a verified local object:

```text
verified -> pinned -> verified
```

Failure paths:

```text
staging -> quarantined
committed -> missing
committed -> corrupt
verified -> missing
verified -> corrupt
evicting -> quarantined
```

Any transition back to `verified` requires full object validation and catalog
consistency checks.

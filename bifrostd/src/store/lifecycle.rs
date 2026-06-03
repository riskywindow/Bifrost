use crate::store::errors::{StoreError, StoreResult};
use crate::store::object_record::ObjectState;

pub fn valid_state_transition(from: ObjectState, to: ObjectState) -> bool {
    if from == to {
        return true;
    }

    matches!(
        (from, to),
        (ObjectState::Staging, ObjectState::Committed)
            | (ObjectState::Staging, ObjectState::Quarantined)
            | (ObjectState::Committed, ObjectState::Verified)
            | (ObjectState::Committed, ObjectState::Evicting)
            | (ObjectState::Committed, ObjectState::Missing)
            | (ObjectState::Committed, ObjectState::Corrupt)
            | (ObjectState::Committed, ObjectState::Quarantined)
            | (ObjectState::Verified, ObjectState::Pinned)
            | (ObjectState::Verified, ObjectState::Evictable)
            | (ObjectState::Verified, ObjectState::Evicting)
            | (ObjectState::Verified, ObjectState::Missing)
            | (ObjectState::Verified, ObjectState::Corrupt)
            | (ObjectState::Verified, ObjectState::Quarantined)
            | (ObjectState::Pinned, ObjectState::Verified)
            | (ObjectState::Pinned, ObjectState::Quarantined)
            | (ObjectState::Evictable, ObjectState::Pinned)
            | (ObjectState::Evictable, ObjectState::Evicting)
            | (ObjectState::Evictable, ObjectState::Missing)
            | (ObjectState::Evictable, ObjectState::Corrupt)
            | (ObjectState::Evictable, ObjectState::Quarantined)
            | (ObjectState::Evicting, ObjectState::Evicted)
            | (ObjectState::Evicting, ObjectState::Missing)
            | (ObjectState::Evicting, ObjectState::Quarantined)
            | (ObjectState::Missing, ObjectState::Quarantined)
            | (ObjectState::Missing, ObjectState::Verified)
            | (ObjectState::Corrupt, ObjectState::Quarantined)
            | (ObjectState::Corrupt, ObjectState::Verified)
            | (ObjectState::Quarantined, ObjectState::Verified)
    )
}

pub fn ensure_valid_state_transition(from: ObjectState, to: ObjectState) -> StoreResult<()> {
    if valid_state_transition(from, to) {
        Ok(())
    } else {
        Err(StoreError::InvalidStateTransition {
            from: from.to_string(),
            to: to.to_string(),
        })
    }
}

pub fn can_serve(state: ObjectState, pin_count: i64) -> bool {
    pin_count >= 0
        && matches!(
            state,
            ObjectState::Verified | ObjectState::Pinned | ObjectState::Evictable
        )
}

pub fn can_evict(state: ObjectState, pin_count: i64) -> bool {
    pin_count == 0
        && matches!(
            state,
            ObjectState::Committed | ObjectState::Verified | ObjectState::Evictable
        )
}

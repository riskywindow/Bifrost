from __future__ import annotations

from dataclasses import dataclass

import pytest

from lmcache_bifrost.errors import KeyCodecError
from lmcache_bifrost.key_codec import opaque_engine_key_hash, stable_key_repr


@dataclass(frozen=True)
class FakeCacheEngineKey:
    model_id: str
    block_hash: str
    tokens: tuple[int, ...]


def test_fake_key_repr_is_stable() -> None:
    key = FakeCacheEngineKey("tiny", "abc", (1, 2, 3))

    assert stable_key_repr(key) == stable_key_repr(
        FakeCacheEngineKey("tiny", "abc", (1, 2, 3))
    )


def test_key_hash_changes_when_key_changes() -> None:
    left = FakeCacheEngineKey("tiny", "abc", (1, 2, 3))
    right = FakeCacheEngineKey("tiny", "abc", (1, 2, 4))

    assert opaque_engine_key_hash(left) != opaque_engine_key_hash(right)


def test_key_hash_does_not_include_memory_addresses() -> None:
    key = object()

    with pytest.raises(KeyCodecError):
        stable_key_repr(key)


def test_public_fields_are_sorted_for_dataclass_like_key() -> None:
    class DataclassLikeKey:
        beta: int
        alpha: str

        def __init__(self) -> None:
            self.beta = 2
            self.alpha = "a"

    first = stable_key_repr(DataclassLikeKey())
    second = stable_key_repr(DataclassLikeKey())

    assert first == second
    assert first.index("alpha") < first.index("beta")

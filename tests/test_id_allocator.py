"""Contract tests for IDAllocator.

Tests verify observable behaviour and public contracts only — no
re-implementation of the allocation logic, no inspection of private state.
"""

import pytest

from canon.persistence.id_allocator import IDAllocator

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def npc_only() -> IDAllocator:
    return IDAllocator(bases={"npc": 1000})


@pytest.fixture()
def two_types() -> IDAllocator:
    return IDAllocator(bases={"npc": 1000, "item": 2000})


@pytest.fixture()
def full_mazeworld() -> IDAllocator:
    return IDAllocator(
        bases={
            "npc": 1000,
            "item": 2000,
            "event": 3000,
            "quest": 4000,
            "monster": 5000,
            "class": 6000,
        }
    )


# ---------------------------------------------------------------------------
# Sequential ID allocation
# ---------------------------------------------------------------------------

class TestSequentialAllocation:
    def test_first_id_equals_base(self, npc_only: IDAllocator) -> None:
        assert npc_only.next("npc") == 1000

    def test_three_calls_return_contiguous_ids(self, npc_only: IDAllocator) -> None:
        ids = [npc_only.next("npc") for _ in range(3)]
        assert ids == [1000, 1001, 1002]

    def test_second_call_increments_by_one(self, npc_only: IDAllocator) -> None:
        first = npc_only.next("npc")
        second = npc_only.next("npc")
        assert second == first + 1


# ---------------------------------------------------------------------------
# Independence of different types
# ---------------------------------------------------------------------------

class TestTypeIndependence:
    def test_npc_and_item_have_different_bases(self, two_types: IDAllocator) -> None:
        npc_id = two_types.next("npc")
        item_id = two_types.next("item")
        assert npc_id == 1000
        assert item_id == 2000

    def test_advancing_one_type_does_not_affect_other(self, two_types: IDAllocator) -> None:
        # Advance npc several times
        for _ in range(5):
            two_types.next("npc")
        # item counter should still be at its base
        assert two_types.next("item") == 2000

    def test_all_mazeworld_bases_are_independent(self, full_mazeworld: IDAllocator) -> None:
        expected_first = {
            "npc": 1000,
            "item": 2000,
            "event": 3000,
            "quest": 4000,
            "monster": 5000,
            "class": 6000,
        }
        for entity_type, expected_id in expected_first.items():
            assert full_mazeworld.next(entity_type) == expected_id


# ---------------------------------------------------------------------------
# Unknown type → KeyError
# ---------------------------------------------------------------------------

class TestUnknownType:
    def test_next_unknown_raises_key_error(self, npc_only: IDAllocator) -> None:
        with pytest.raises(KeyError):
            npc_only.next("dragon")

    def test_key_error_message_mentions_unknown_type(self, npc_only: IDAllocator) -> None:
        with pytest.raises(KeyError, match="dragon"):
            npc_only.next("dragon")

    def test_key_error_message_mentions_registered_types(self, npc_only: IDAllocator) -> None:
        with pytest.raises(KeyError, match="npc"):
            npc_only.next("dragon")

    def test_empty_bases_any_next_raises(self) -> None:
        allocator = IDAllocator(bases={})
        with pytest.raises(KeyError):
            allocator.next("npc")


# ---------------------------------------------------------------------------
# peek — does not advance counter
# ---------------------------------------------------------------------------

class TestPeek:
    def test_peek_returns_same_as_next_would(self, npc_only: IDAllocator) -> None:
        peeked = npc_only.peek("npc")
        actual = npc_only.next("npc")
        assert peeked == actual

    def test_peek_does_not_advance_counter(self, npc_only: IDAllocator) -> None:
        npc_only.peek("npc")
        npc_only.peek("npc")
        # First next() should still return 1000
        assert npc_only.next("npc") == 1000

    def test_peek_after_several_nexts(self, npc_only: IDAllocator) -> None:
        npc_only.next("npc")  # 1000
        npc_only.next("npc")  # 1001
        assert npc_only.peek("npc") == 1002

    def test_peek_unknown_type_raises_key_error(self, npc_only: IDAllocator) -> None:
        with pytest.raises(KeyError):
            npc_only.peek("ghost")


# ---------------------------------------------------------------------------
# reset — single type
# ---------------------------------------------------------------------------

class TestReset:
    def test_reset_returns_counter_to_base(self, npc_only: IDAllocator) -> None:
        npc_only.next("npc")
        npc_only.next("npc")
        npc_only.reset("npc")
        assert npc_only.next("npc") == 1000

    def test_reset_one_type_does_not_affect_other(self, two_types: IDAllocator) -> None:
        two_types.next("npc")  # advance npc to 1001
        two_types.next("item")  # advance item to 2001
        two_types.reset("npc")
        # npc is back to 1000; item is still at 2001
        assert two_types.next("npc") == 1000
        assert two_types.next("item") == 2001

    def test_reset_unknown_type_raises_key_error(self, npc_only: IDAllocator) -> None:
        with pytest.raises(KeyError):
            npc_only.reset("ghost")


# ---------------------------------------------------------------------------
# reset_all — all types
# ---------------------------------------------------------------------------

class TestResetAll:
    def test_reset_all_resets_every_type(self, two_types: IDAllocator) -> None:
        two_types.next("npc")
        two_types.next("item")
        two_types.reset_all()
        assert two_types.next("npc") == 1000
        assert two_types.next("item") == 2000

    def test_reset_all_on_empty_bases_is_safe(self) -> None:
        allocator = IDAllocator(bases={})
        allocator.reset_all()  # should not raise

    def test_reset_all_followed_by_sequential_ids(self, full_mazeworld: IDAllocator) -> None:
        # Advance each type a few times
        for entity_type in ("npc", "item", "event", "quest", "monster", "class"):
            for _ in range(3):
                full_mazeworld.next(entity_type)
        full_mazeworld.reset_all()
        # After reset_all, every type starts from its base again
        assert full_mazeworld.next("npc") == 1000
        assert full_mazeworld.next("event") == 3000
        assert full_mazeworld.next("class") == 6000

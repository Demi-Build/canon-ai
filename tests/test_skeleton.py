"""Tests for the declarative skeleton system.

Covers ``SkeletonField`` validation (exactly-one-mode, depends_on rules),
``SkeletonSpec`` ordering checks, ``roll_skeleton`` mechanics
(``choices`` / ``range`` / ``lookup``), determinism under a seeded RNG,
and the ``post`` callback escape hatch.
"""

from __future__ import annotations

import random

import pytest

from canon.skeleton import SkeletonField, SkeletonSpec, roll_skeleton

# ---------------------------------------------------------------------------
# SkeletonField construction-time validation
# ---------------------------------------------------------------------------


def test_field_with_only_choices_is_valid():
    field = SkeletonField(choices=[("a", 1), ("b", 2)])
    assert field.choices == [("a", 1), ("b", 2)]
    assert field.range is None
    assert field.lookup is None


def test_field_with_only_range_is_valid():
    field = SkeletonField(range=(1, 10))
    assert field.range == (1, 10)


def test_field_with_lookup_requires_depends_on():
    with pytest.raises(ValueError, match="depends_on"):
        SkeletonField(lookup={"a": 1, "b": 2})


def test_field_with_depends_on_requires_lookup():
    with pytest.raises(ValueError, match="depends_on"):
        SkeletonField(choices=[("a", 1)], depends_on="other")


def test_field_with_lookup_and_depends_on_is_valid():
    field = SkeletonField(lookup={"a": 1, "b": 2}, depends_on="kind")
    assert field.lookup == {"a": 1, "b": 2}
    assert field.depends_on == "kind"


def test_field_with_choices_and_range_raises():
    with pytest.raises(ValueError, match="exactly one"):
        SkeletonField(choices=[("a", 1)], range=(1, 5))


def test_field_with_all_three_modes_raises():
    with pytest.raises(ValueError, match="exactly one"):
        SkeletonField(
            choices=[("a", 1)],
            range=(1, 5),
            lookup={"a": 1},
            depends_on="x",
        )


def test_field_with_no_mode_raises():
    with pytest.raises(ValueError, match="exactly one"):
        SkeletonField()


def test_field_with_empty_choices_raises():
    with pytest.raises(ValueError, match="empty"):
        SkeletonField(choices=[])


def test_field_with_inverted_range_raises():
    with pytest.raises(ValueError, match="low <= high"):
        SkeletonField(range=(10, 1))


def test_field_with_equal_range_endpoints_is_valid():
    field = SkeletonField(range=(5, 5))
    assert field.range == (5, 5)


# ---------------------------------------------------------------------------
# SkeletonSpec construction-time validation (depends_on ordering)
# ---------------------------------------------------------------------------


def test_spec_with_valid_dependency_order_is_valid():
    spec = SkeletonSpec(
        entity_type="weapon",
        fields={
            "kind": SkeletonField(choices=[("light", 1), ("heavy", 1)]),
            "damage_die": SkeletonField(
                lookup={"light": "d6", "heavy": "d10"},
                depends_on="kind",
            ),
        },
    )
    assert list(spec.fields) == ["kind", "damage_die"]


def test_spec_with_depends_on_referring_to_nonexistent_field_raises():
    with pytest.raises(ValueError, match="no such field"):
        SkeletonSpec(
            entity_type="weapon",
            fields={
                "damage_die": SkeletonField(
                    lookup={"light": "d6"},
                    depends_on="kind",  # 'kind' is not a field anywhere
                ),
            },
        )


def test_spec_with_depends_on_referring_to_later_field_raises():
    with pytest.raises(ValueError, match="declared \\*after\\*"):
        SkeletonSpec(
            entity_type="weapon",
            fields={
                "damage_die": SkeletonField(
                    lookup={"light": "d6", "heavy": "d10"},
                    depends_on="kind",
                ),
                "kind": SkeletonField(choices=[("light", 1), ("heavy", 1)]),
            },
        )


def test_spec_with_self_referencing_depends_on_raises():
    with pytest.raises(ValueError, match="depends on itself"):
        SkeletonSpec(
            entity_type="weapon",
            fields={
                "kind": SkeletonField(lookup={"a": 1}, depends_on="kind"),
            },
        )


# ---------------------------------------------------------------------------
# roll_skeleton — choices
# ---------------------------------------------------------------------------


def test_roll_choices_only_is_reproducible_with_seeded_rng():
    spec = SkeletonSpec(
        entity_type="tower",
        fields={
            "kind": SkeletonField(
                choices=[("archer", 3), ("mage", 2), ("trap", 1)],
            ),
            "tier": SkeletonField(choices=[("bronze", 3), ("silver", 2), ("gold", 1)]),
        },
    )

    first = roll_skeleton(spec, random.Random(42))
    second = roll_skeleton(spec, random.Random(42))
    third = roll_skeleton(spec, random.Random(42))

    assert first == second == third
    assert set(first) == {"kind", "tier"}
    assert first["kind"] in {"archer", "mage", "trap"}
    assert first["tier"] in {"bronze", "silver", "gold"}


def test_roll_choices_respects_weights_over_many_samples():
    # With weights heavily skewed toward "common", we expect the rolled
    # population to lean that way over many samples. Not a tight statistical
    # test — just a sanity check that weights are actually used.
    spec = SkeletonSpec(
        entity_type="loot",
        fields={
            "rarity": SkeletonField(
                choices=[("common", 100), ("rare", 1)],
            ),
        },
    )
    rng = random.Random(7)
    counts = {"common": 0, "rare": 0}
    for _ in range(500):
        rolled = roll_skeleton(spec, rng)
        counts[rolled["rarity"]] += 1
    assert counts["common"] > counts["rare"] * 5


# ---------------------------------------------------------------------------
# roll_skeleton — range
# ---------------------------------------------------------------------------


def test_roll_range_stays_within_bounds_inclusive():
    spec = SkeletonSpec(
        entity_type="monster",
        fields={
            "hp": SkeletonField(range=(8, 40)),
        },
    )
    rng = random.Random(123)
    saw_low = False
    saw_high = False
    for _ in range(2000):
        rolled = roll_skeleton(spec, rng)
        assert 8 <= rolled["hp"] <= 40
        if rolled["hp"] == 8:
            saw_low = True
        if rolled["hp"] == 40:
            saw_high = True
    # Inclusive at both endpoints — over 2000 samples on a 33-value range we
    # should hit both ends at least once.
    assert saw_low, "range low endpoint should be reachable (inclusive)"
    assert saw_high, "range high endpoint should be reachable (inclusive)"


def test_roll_range_singleton_is_constant():
    spec = SkeletonSpec(
        entity_type="constant",
        fields={"value": SkeletonField(range=(7, 7))},
    )
    for _ in range(20):
        assert roll_skeleton(spec, random.Random(0))["value"] == 7


# ---------------------------------------------------------------------------
# roll_skeleton — lookup
# ---------------------------------------------------------------------------


def test_roll_lookup_keys_off_prior_field():
    spec = SkeletonSpec(
        entity_type="weapon",
        fields={
            "kind": SkeletonField(choices=[("light", 1), ("heavy", 1), ("sacred", 1)]),
            "damage_die": SkeletonField(
                lookup={"light": "d6", "heavy": "d10", "sacred": "d8"},
                depends_on="kind",
            ),
        },
    )
    expected_pairs = {
        "light": "d6",
        "heavy": "d10",
        "sacred": "d8",
    }
    rng = random.Random(0)
    for _ in range(50):
        rolled = roll_skeleton(spec, rng)
        assert rolled["damage_die"] == expected_pairs[rolled["kind"]]


def test_roll_lookup_with_missing_key_raises_clear_keyerror():
    # If `kind` rolls to "sacred" but the lookup table only has "light"/"heavy",
    # roll_skeleton should raise KeyError naming the offending field and key.
    # We force the rng to roll "sacred" by making it the only weighted option.
    spec = SkeletonSpec(
        entity_type="weapon",
        fields={
            "kind": SkeletonField(choices=[("sacred", 1)]),
            "damage_die": SkeletonField(
                lookup={"light": "d6", "heavy": "d10"},  # no "sacred"
                depends_on="kind",
            ),
        },
    )
    with pytest.raises(KeyError, match="damage_die") as excinfo:
        roll_skeleton(spec, random.Random(0))
    # Message should mention both the field and the missing key.
    assert "sacred" in str(excinfo.value)


# ---------------------------------------------------------------------------
# roll_skeleton — post callback
# ---------------------------------------------------------------------------


def test_post_callback_runs_and_replaces_dict():
    def post(rolled: dict) -> dict:
        rolled["derived"] = rolled["base"] * 2
        return rolled

    spec = SkeletonSpec(
        entity_type="thing",
        fields={"base": SkeletonField(range=(5, 5))},
        post=post,
    )
    rolled = roll_skeleton(spec, random.Random(0))
    assert rolled == {"base": 5, "derived": 10}


def test_post_callback_can_fully_rewrite_dict():
    def post(rolled: dict) -> dict:
        return {"only_field": "replaced"}

    spec = SkeletonSpec(
        entity_type="thing",
        fields={"original": SkeletonField(choices=[("a", 1)])},
        post=post,
    )
    assert roll_skeleton(spec, random.Random(0)) == {"only_field": "replaced"}


# ---------------------------------------------------------------------------
# Determinism / reproducibility
# ---------------------------------------------------------------------------


def test_same_seed_produces_identical_output():
    spec = SkeletonSpec(
        entity_type="mixed",
        fields={
            "kind": SkeletonField(choices=[("a", 1), ("b", 1), ("c", 1)]),
            "size": SkeletonField(range=(1, 100)),
            "die": SkeletonField(
                lookup={"a": "d4", "b": "d6", "c": "d8"},
                depends_on="kind",
            ),
        },
    )
    a = roll_skeleton(spec, random.Random(2026))
    b = roll_skeleton(spec, random.Random(2026))
    assert a == b


def test_different_seeds_produce_different_output_eventually():
    spec = SkeletonSpec(
        entity_type="mixed",
        fields={
            "kind": SkeletonField(
                choices=[(c, 1) for c in "abcdefghij"],
            ),
            "size": SkeletonField(range=(1, 1000)),
        },
    )
    # Two different seeds should diverge within a single roll the vast
    # majority of the time; checking a handful of seeds makes the test stable.
    a = [roll_skeleton(spec, random.Random(i)) for i in range(5)]
    b = [roll_skeleton(spec, random.Random(i + 1000)) for i in range(5)]
    assert a != b


# ---------------------------------------------------------------------------
# Field insertion-order preservation
# ---------------------------------------------------------------------------


def test_field_insertion_order_preserved_in_output():
    spec = SkeletonSpec(
        entity_type="ordered",
        fields={
            "first": SkeletonField(choices=[("alpha", 1)]),
            "second": SkeletonField(range=(2, 2)),
            "third": SkeletonField(range=(3, 3)),
            "fourth": SkeletonField(
                lookup={"alpha": "looked-up"},
                depends_on="first",
            ),
        },
    )
    rolled = roll_skeleton(spec, random.Random(0))
    assert list(rolled) == ["first", "second", "third", "fourth"]
    assert rolled["fourth"] == "looked-up"


def test_chained_lookups_resolve_in_order():
    # third depends on second, second depends on first — exercises that
    # rolled values are visible to later lookups, not just the immediate next.
    spec = SkeletonSpec(
        entity_type="chain",
        fields={
            "first": SkeletonField(choices=[("x", 1)]),
            "second": SkeletonField(lookup={"x": "y"}, depends_on="first"),
            "third": SkeletonField(lookup={"y": "z"}, depends_on="second"),
        },
    )
    assert roll_skeleton(spec, random.Random(0)) == {
        "first": "x",
        "second": "y",
        "third": "z",
    }


# ---------------------------------------------------------------------------
# MazeWorld-style weapon spec (smoke test from spike §1.8)
# ---------------------------------------------------------------------------


def test_mazeworld_style_weapon_spec_with_post_callback():
    """Approximates MazeWorld's `roll_weapon_skeleton` declaratively.

    The imperative MazeWorld function combines weighted choices, a lookup
    table keyed off weapon_type, and a 10% probability gate for
    ``magic_element``. v0.1 expresses the probability gate via the ``post``
    callback (v0.2 will support it declaratively — see TODOs in core.py).

    The ``post`` closure here captures a *separate* RNG. This is a known
    v0.1 limitation: ``post`` is invoked with only the rolled dict, so any
    randomness inside it must be sourced from the caller's scope. Tests
    just verify the callback runs.
    """
    post_rng = random.Random(11)

    def post(rolled: dict) -> dict:
        # 10% probability gate, mirroring weapon `magic_element`.
        if post_rng.random() < 0.10:
            rolled["magic_element"] = post_rng.choice(["fire", "ice", "lightning"])
        else:
            rolled["magic_element"] = None
        # Derived stat_modifier ← weapon_type (also a v0.2 candidate for
        # declarative computed-field support).
        rolled["stat_modifier"] = {
            "heavy": "STR",
            "light": "DEX",
            "sacred": "WIS",
        }[rolled["weapon_type"]]
        return rolled

    weapon_spec = SkeletonSpec(
        entity_type="weapon",
        fields={
            "weapon_type": SkeletonField(
                choices=[("heavy", 2), ("light", 3), ("sacred", 1)],
            ),
            "damage_die": SkeletonField(
                lookup={"heavy": "d10", "light": "d6", "sacred": "d8"},
                depends_on="weapon_type",
            ),
        },
        post=post,
    )

    rolled = roll_skeleton(weapon_spec, random.Random(99))
    # post ran (it always sets these two keys)
    assert "magic_element" in rolled
    assert "stat_modifier" in rolled
    assert rolled["stat_modifier"] in {"STR", "DEX", "WIS"}
    assert rolled["weapon_type"] in {"heavy", "light", "sacred"}
    assert rolled["damage_die"] in {"d10", "d6", "d8"}
    # Determinism: same outer seed + same post_rng state would reproduce, but
    # since post_rng is module-scoped to this test we just verify no crash.


# ---------------------------------------------------------------------------
# roll_skeleton — context seam (depends_on_context + context-aware post)
# ---------------------------------------------------------------------------


def test_lookup_keys_off_context_value():
    """A lookup field resolves its key from the outer context dict."""
    spec = SkeletonSpec(
        entity_type="monster",
        fields={
            "damage_die": SkeletonField(
                lookup={1: "1d4", 2: "1d6", 3: "1d8"},
                depends_on_context="room_level",
            ),
        },
    )
    assert roll_skeleton(spec, random.Random(0), context={"room_level": 1})["damage_die"] == "1d4"
    assert roll_skeleton(spec, random.Random(0), context={"room_level": 3})["damage_die"] == "1d8"


def test_context_lookup_missing_context_key_raises():
    spec = SkeletonSpec(
        entity_type="monster",
        fields={
            "band": SkeletonField(
                lookup={1: "low"}, depends_on_context="room_level"
            ),
        },
    )
    with pytest.raises(KeyError, match="room_level"):
        roll_skeleton(spec, random.Random(0), context={})


def test_context_lookup_missing_table_key_raises():
    spec = SkeletonSpec(
        entity_type="monster",
        fields={
            "band": SkeletonField(
                lookup={1: "low"}, depends_on_context="room_level"
            ),
        },
    )
    with pytest.raises(KeyError, match="room_level"):
        roll_skeleton(spec, random.Random(0), context={"room_level": 99})


def test_depends_on_and_depends_on_context_together_raises():
    with pytest.raises(ValueError, match="exactly one key source"):
        SkeletonField(
            lookup={1: "x"}, depends_on="kind", depends_on_context="room_level"
        )


def test_depends_on_context_without_lookup_raises():
    with pytest.raises(ValueError, match="only meaningful for lookup"):
        SkeletonField(range=(1, 6), depends_on_context="room_level")


def test_post_receives_context_when_it_declares_two_params():
    """A post(rolled, context) callback scales using the outer context."""
    def post(rolled: dict, context: dict) -> dict:
        level = context.get("room_level", 1)
        rolled["hp"] = 10 * level
        return rolled

    spec = SkeletonSpec(
        entity_type="monster",
        fields={"tier": SkeletonField(choices=[("minion", 1)])},
        post=post,
    )
    rolled = roll_skeleton(spec, random.Random(0), context={"room_level": 4})
    assert rolled["hp"] == 40


def test_legacy_single_arg_post_still_works_with_context_present():
    """Back-compat: post(rolled) is unaffected even when context is passed."""
    def post(rolled: dict) -> dict:
        rolled["tag"] = "ok"
        return rolled

    spec = SkeletonSpec(
        entity_type="x",
        fields={"v": SkeletonField(range=(1, 1))},
        post=post,
    )
    rolled = roll_skeleton(spec, random.Random(0), context={"room_level": 3})
    assert rolled["tag"] == "ok"
    assert rolled["v"] == 1


def test_context_defaults_to_empty_and_does_not_break_plain_specs():
    """Specs with no context dependence roll identically with/without context."""
    spec = SkeletonSpec(
        entity_type="x",
        fields={"v": SkeletonField(choices=[("a", 1), ("b", 1)])},
    )
    a = roll_skeleton(spec, random.Random(7))
    b = roll_skeleton(spec, random.Random(7), context={"room_level": 5})
    assert a == b

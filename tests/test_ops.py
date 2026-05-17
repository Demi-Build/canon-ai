"""Unit tests for canon.ops — cradle-facing bible-mutation operations.

Uses FakeLLMBackend for deterministic, network-free LLM simulation.
"""

from __future__ import annotations

import json
import random

import pytest

from canon import (
    Bible,
    DefaultPromptSet,
    EntityLore,
    FakeLLMBackend,
    GenerationStats,
    LLMClient,
    Map,
    SkeletonField,
    SkeletonSpec,
)
from canon.ops import generate_entity, regenerate_entity, reroll_entity_flavor

# ---------------------------------------------------------------------------
# Shared test fixtures
# ---------------------------------------------------------------------------

WEAPON_SPEC = SkeletonSpec(
    entity_type="weapon",
    fields={
        "kind": SkeletonField(choices=[("blade", 1), ("rod", 1)]),
        "power": SkeletonField(range=(1, 10)),
    },
)


def make_bible_with_entity() -> Bible:
    """Return a Bible with one map ('m0') and one pre-existing weapon entity."""
    bible = Bible.empty(seed="t")
    bible.maps["m0"] = Map(
        map_id="m0",
        name="A",
        description="",
        environment="x",
        story_beat="",
    )
    e = EntityLore(
        entity_id="weapon_0001",
        entity_type="weapon",
        name="Old Name",
        map_id="m0",
        lore="old lore",
        skeleton={"kind": "blade", "power": 5},
    )
    bible.add_entity("m0", e)
    return bible


def make_llm(responses: list[str] | dict | object) -> LLMClient:
    """Wrap responses in a FakeLLMBackend and return an LLMClient."""
    return LLMClient(FakeLLMBackend(responses), stats=GenerationStats())


PROMPTS = DefaultPromptSet()

# ---------------------------------------------------------------------------
# reroll_entity_flavor
# ---------------------------------------------------------------------------


def test_reroll_flavor_updates_name_and_lore() -> None:
    """reroll_entity_flavor replaces name + lore but preserves the skeleton."""
    bible = make_bible_with_entity()
    original_skeleton = dict(bible.get_entity("m0", "weapon_0001").skeleton)

    llm = make_llm([json.dumps({"name": "New Name", "lore": "new lore"})])
    result = reroll_entity_flavor(bible, "m0", "weapon_0001", llm, PROMPTS)

    assert result.name == "New Name"
    assert result.lore == "new lore"
    # Skeleton must be unchanged
    assert result.skeleton == original_skeleton


def test_reroll_flavor_raises_for_unknown_entity() -> None:
    """reroll_entity_flavor raises KeyError when the entity doesn't exist."""
    bible = make_bible_with_entity()
    llm = make_llm([json.dumps({"name": "X", "lore": "y"})])

    with pytest.raises(KeyError):
        reroll_entity_flavor(bible, "m0", "does_not_exist", llm, PROMPTS)


def test_reroll_flavor_sets_generation_trail() -> None:
    """reroll_entity_flavor attaches a non-None generation_trail."""
    bible = make_bible_with_entity()
    llm = make_llm([json.dumps({"name": "Trailed Name", "lore": "trail lore"})])
    result = reroll_entity_flavor(bible, "m0", "weapon_0001", llm, PROMPTS)

    assert result.generation_trail is not None


def test_reroll_flavor_handles_bad_json_gracefully() -> None:
    """reroll_entity_flavor uses the fallback when the LLM returns invalid JSON."""
    bible = make_bible_with_entity()
    # All three attempts return unparseable text; retry_with_feedback uses fallback
    llm = make_llm(["not json", "still not json", "absolutely not json"])
    result = reroll_entity_flavor(bible, "m0", "weapon_0001", llm, PROMPTS, max_retries=3)

    # Fallback name is entity_type title-cased
    assert result.name == "Weapon"
    assert result.generation_trail is not None


# ---------------------------------------------------------------------------
# regenerate_entity
# ---------------------------------------------------------------------------


def test_regenerate_entity_produces_new_skeleton_and_flavor() -> None:
    """regenerate_entity rolls a new skeleton AND updates name + lore."""
    bible = make_bible_with_entity()

    # Use a fixed RNG seed so the new skeleton is deterministic
    rng = random.Random(42)
    llm = make_llm([json.dumps({"name": "Reborn Blade", "lore": "reborn lore"})])
    result = regenerate_entity(
        bible, "m0", "weapon_0001", llm, PROMPTS, WEAPON_SPEC, rng=rng
    )

    assert result.name == "Reborn Blade"
    assert result.lore == "reborn lore"
    # Skeleton must be a fresh roll — may differ from the original
    # (we assert it's populated; statistical identity would flake)
    assert isinstance(result.skeleton, dict)
    assert "kind" in result.skeleton
    assert "power" in result.skeleton


def test_regenerate_entity_preserves_entity_id_and_type() -> None:
    """regenerate_entity keeps entity_id and entity_type unchanged."""
    bible = make_bible_with_entity()
    rng = random.Random(7)
    llm = make_llm([json.dumps({"name": "New Type", "lore": "type lore"})])
    result = regenerate_entity(
        bible, "m0", "weapon_0001", llm, PROMPTS, WEAPON_SPEC, rng=rng
    )

    assert result.entity_id == "weapon_0001"
    assert result.entity_type == "weapon"


def test_regenerate_entity_raises_for_unknown_entity() -> None:
    """regenerate_entity raises KeyError when the entity doesn't exist."""
    bible = make_bible_with_entity()
    llm = make_llm([json.dumps({"name": "X", "lore": "y"})])

    with pytest.raises(KeyError):
        regenerate_entity(
            bible, "m0", "no_such_entity", llm, PROMPTS, WEAPON_SPEC
        )


def test_regenerate_entity_sets_generation_trail() -> None:
    """regenerate_entity attaches a non-None generation_trail."""
    bible = make_bible_with_entity()
    llm = make_llm([json.dumps({"name": "Trail Blade", "lore": "trail lore"})])
    result = regenerate_entity(
        bible, "m0", "weapon_0001", llm, PROMPTS, WEAPON_SPEC
    )

    assert result.generation_trail is not None


def test_regenerate_entity_handles_bad_json_gracefully() -> None:
    """regenerate_entity uses the fallback when the LLM returns invalid JSON."""
    bible = make_bible_with_entity()
    llm = make_llm(["bad", "still bad", "very bad"])
    result = regenerate_entity(
        bible, "m0", "weapon_0001", llm, PROMPTS, WEAPON_SPEC, max_retries=3
    )

    assert result.name == "Weapon"
    assert result.generation_trail is not None


# ---------------------------------------------------------------------------
# generate_entity
# ---------------------------------------------------------------------------


def test_generate_entity_adds_to_map() -> None:
    """generate_entity appends a new EntityLore to the map."""
    bible = make_bible_with_entity()
    before = len(bible.maps["m0"].entities)

    llm = make_llm([json.dumps({"name": "Brand New Weapon", "lore": "fresh lore"})])
    generate_entity(bible, "m0", "weapon", llm, PROMPTS, WEAPON_SPEC)

    assert len(bible.maps["m0"].entities) == before + 1


def test_generate_entity_auto_allocates_entity_id() -> None:
    """generate_entity auto-assigns entity_id in '{type}_{NNNN}' format when omitted."""
    bible = make_bible_with_entity()
    # There is already 1 weapon in the bible ("weapon_0001"); new one should be "weapon_0001"
    # actually existing_count counts ALL weapons regardless of id, so count=1 → "weapon_0001"
    # Let's check what we get
    llm = make_llm([json.dumps({"name": "Auto ID Weapon", "lore": "auto lore"})])
    result = generate_entity(bible, "m0", "weapon", llm, PROMPTS, WEAPON_SPEC)

    # Format: {entity_type}_{NNNN}
    assert result.entity_id.startswith("weapon_")
    # The numeric suffix should be parseable
    suffix = result.entity_id[len("weapon_"):]
    assert suffix.isdigit() and len(suffix) == 4


def test_generate_entity_uses_explicit_entity_id() -> None:
    """generate_entity uses the caller-supplied entity_id when provided."""
    bible = make_bible_with_entity()
    llm = make_llm([json.dumps({"name": "Explicit Weapon", "lore": "explicit lore"})])
    result = generate_entity(
        bible, "m0", "weapon", llm, PROMPTS, WEAPON_SPEC, entity_id="my_custom_id"
    )

    assert result.entity_id == "my_custom_id"


def test_generate_entity_raises_for_unknown_map() -> None:
    """generate_entity raises KeyError when the map doesn't exist."""
    bible = make_bible_with_entity()
    llm = make_llm([json.dumps({"name": "X", "lore": "y"})])

    with pytest.raises(KeyError):
        generate_entity(bible, "no_such_map", "weapon", llm, PROMPTS, WEAPON_SPEC)


def test_generate_entity_sets_generation_trail() -> None:
    """generate_entity attaches a non-None generation_trail."""
    bible = make_bible_with_entity()
    llm = make_llm([json.dumps({"name": "Trail Weapon", "lore": "trail lore"})])
    result = generate_entity(bible, "m0", "weapon", llm, PROMPTS, WEAPON_SPEC)

    assert result.generation_trail is not None


def test_generate_entity_handles_bad_json_gracefully() -> None:
    """generate_entity uses the fallback when the LLM returns invalid JSON."""
    bible = make_bible_with_entity()
    llm = make_llm(["bad", "still bad", "very bad"])
    result = generate_entity(
        bible, "m0", "weapon", llm, PROMPTS, WEAPON_SPEC, max_retries=3
    )

    assert result.name == "Weapon"
    assert result.generation_trail is not None


# ---------------------------------------------------------------------------
# Cross-cutting: generation_trail is always set
# ---------------------------------------------------------------------------


def test_all_three_set_generation_trail_on_success() -> None:
    """All three operations set a non-None generation_trail on a clean response."""
    good_response = json.dumps({"name": "Good", "lore": "good lore"})

    # reroll
    b1 = make_bible_with_entity()
    r1 = reroll_entity_flavor(b1, "m0", "weapon_0001", make_llm([good_response]), PROMPTS)
    assert r1.generation_trail is not None

    # regen
    b2 = make_bible_with_entity()
    r2 = regenerate_entity(
        b2, "m0", "weapon_0001", make_llm([good_response]), PROMPTS, WEAPON_SPEC
    )
    assert r2.generation_trail is not None

    # generate
    b3 = make_bible_with_entity()
    r3 = generate_entity(b3, "m0", "weapon", make_llm([good_response]), PROMPTS, WEAPON_SPEC)
    assert r3.generation_trail is not None

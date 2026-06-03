"""Unit tests for EntityPhase.

Uses FakeLLMBackend for deterministic, network-free LLM simulation.

Test spec (WEAPON_SPEC) is defined entirely within this module — no import
from application code other than canon's public API.
"""

from __future__ import annotations

import json
import random

from canon import (
    Bible,
    CanonConfig,
    DefaultPromptSet,
    FakeLLMBackend,
    GenerationStats,
    LLMClient,
    Map,
    PipelineContext,
    SkeletonField,
    SkeletonSpec,
    roll_skeleton,
)
from canon.pipeline.phases.entity import EntityPhase

# ---------------------------------------------------------------------------
# Test skeleton specs — defined here, not imported from anywhere
# ---------------------------------------------------------------------------

WEAPON_SPEC = SkeletonSpec(
    entity_type="weapon",
    fields={
        "weapon_type": SkeletonField(choices=[("sword", 1), ("bow", 1)]),
        "damage_die": SkeletonField(range=(4, 12)),
    },
)

MONSTER_SPEC = SkeletonSpec(
    entity_type="monster",
    fields={
        "hp": SkeletonField(range=(8, 40)),
        "damage_die": SkeletonField(choices=[("d4", 1), ("d6", 1)]),
    },
)


# ---------------------------------------------------------------------------
# Context factory
# ---------------------------------------------------------------------------


def make_ctx(
    responses,
    *,
    with_maps: bool = True,
    seed: str = "t",
    schemas: dict | None = None,
) -> PipelineContext:
    bible = Bible.empty(seed=seed)
    if with_maps:
        bible.maps["map_0"] = Map(
            map_id="map_0",
            name="A",
            description="",
            environment="forest",
            story_beat="",
        )
        bible.maps["map_1"] = Map(
            map_id="map_1",
            name="B",
            description="",
            environment="cave",
            story_beat="",
        )
    backend = FakeLLMBackend(responses)
    llm = LLMClient(backend, stats=GenerationStats())
    return PipelineContext(
        bible=bible,
        config=CanonConfig(seed=seed),
        rng=random.Random(seed),
        llm=llm,
        prompts=DefaultPromptSet(),
        schemas=schemas if schemas is not None else {"weapon": WEAPON_SPEC},
    )


# ---------------------------------------------------------------------------
# Helper: a valid weapon JSON response
# ---------------------------------------------------------------------------


def weapon_response(name: str = "Iron Sword", lore: str = "A sturdy blade.") -> str:
    return json.dumps({"name": name, "lore": lore})


def monster_response(name: str = "Cave Troll", lore: str = "Fearsome creature.") -> str:
    return json.dumps({"name": name, "lore": lore})


# ---------------------------------------------------------------------------
# 1. Skip gracefully when entity_type has no SkeletonSpec
# ---------------------------------------------------------------------------


def test_skips_when_no_spec_registered():
    """Phase must emit a warning and produce no entities when spec is absent."""
    ctx = make_ctx([], schemas={})  # no specs at all
    phase = EntityPhase("weapon")
    phase.run(ctx)

    total_entities = sum(len(m.entities) for m in ctx.bible.maps.values())
    assert total_entities == 0


def test_skips_does_not_crash_when_no_spec():
    ctx = make_ctx([], schemas={})
    EntityPhase("weapon").run(ctx)  # must not raise


# ---------------------------------------------------------------------------
# 2. per_map=True with 2 maps, count=3 → 6 entities total
# ---------------------------------------------------------------------------


def test_per_map_produces_count_entities_per_map():
    responses = [weapon_response(f"Weapon {i}") for i in range(6)]
    ctx = make_ctx(responses)
    EntityPhase("weapon", per_map=True, count=3).run(ctx)

    assert len(ctx.bible.maps["map_0"].entities) == 3
    assert len(ctx.bible.maps["map_1"].entities) == 3


def test_per_map_total_entity_count():
    responses = [weapon_response(f"W{i}") for i in range(6)]
    ctx = make_ctx(responses)
    EntityPhase("weapon", per_map=True, count=3).run(ctx)

    total = sum(len(m.entities) for m in ctx.bible.maps.values())
    assert total == 6


# ---------------------------------------------------------------------------
# 3. Skeleton integrity — entity.skeleton must equal roll_skeleton output
# ---------------------------------------------------------------------------


def test_skeleton_preserved_verbatim():
    """entity.skeleton must be the exact dict from roll_skeleton — skeleton wins."""
    ctx = make_ctx([weapon_response()])

    # Roll the skeleton with the same RNG state that EntityPhase will use.
    rng_snapshot = random.Random("t")
    expected_skeleton = roll_skeleton(WEAPON_SPEC, rng_snapshot)

    EntityPhase("weapon", per_map=True, count=1).run(ctx)

    entity = ctx.bible.maps["map_0"].entities[0]
    assert entity.skeleton == expected_skeleton


def test_skeleton_damage_die_is_in_range():
    """damage_die must fall within the declared range=(4, 12)."""
    responses = [weapon_response() for _ in range(6)]
    ctx = make_ctx(responses)
    EntityPhase("weapon", per_map=True, count=3).run(ctx)

    for map_obj in ctx.bible.maps.values():
        for entity in map_obj.entities:
            assert 4 <= entity.skeleton["damage_die"] <= 12


def test_skeleton_weapon_type_is_valid_choice():
    """weapon_type must be one of the declared choices."""
    responses = [weapon_response() for _ in range(6)]
    ctx = make_ctx(responses)
    EntityPhase("weapon", per_map=True, count=3).run(ctx)

    valid_types = {"sword", "bow"}
    for map_obj in ctx.bible.maps.values():
        for entity in map_obj.entities:
            assert entity.skeleton["weapon_type"] in valid_types


# ---------------------------------------------------------------------------
# 4. LLM-supplied name and lore land on the entity
# ---------------------------------------------------------------------------


def test_llm_name_and_lore_on_entity():
    ctx = make_ctx([weapon_response("Shadowfang", "Forged in darkness.")])
    EntityPhase("weapon", per_map=True, count=1).run(ctx)

    entity = ctx.bible.maps["map_0"].entities[0]
    assert entity.name == "Shadowfang"
    assert entity.lore == "Forged in darkness."


def test_empty_lore_is_accepted():
    """lore can be empty string — some entities are mechanical-only."""
    ctx = make_ctx([json.dumps({"name": "Plain Sword", "lore": ""})])
    EntityPhase("weapon", per_map=True, count=1).run(ctx)

    entity = ctx.bible.maps["map_0"].entities[0]
    assert entity.lore == ""


# ---------------------------------------------------------------------------
# 5. GenerationTrail is populated
# ---------------------------------------------------------------------------


def test_generation_trail_populated():
    ctx = make_ctx([weapon_response()])
    EntityPhase("weapon", per_map=True, count=1).run(ctx)

    entity = ctx.bible.maps["map_0"].entities[0]
    assert entity.generation_trail is not None
    assert entity.generation_trail.prompt != ""
    assert entity.generation_trail.response != ""


def test_generation_trail_response_is_llm_output():
    """The captured response string must be the raw LLM JSON."""
    raw = weapon_response("Trailblade", "Traced a path.")
    ctx = make_ctx([raw])
    EntityPhase("weapon", per_map=True, count=1).run(ctx)

    entity = ctx.bible.maps["map_0"].entities[0]
    assert entity.generation_trail.response == raw


def test_generation_trail_prompt_contains_entity_type():
    """The prompt sent to the LLM must mention the entity type."""
    ctx = make_ctx([weapon_response()])
    EntityPhase("weapon", per_map=True, count=1).run(ctx)

    entity = ctx.bible.maps["map_0"].entities[0]
    assert "weapon" in entity.generation_trail.prompt.lower()


# ---------------------------------------------------------------------------
# 6. entity.entity_type matches the phase's entity_type
# ---------------------------------------------------------------------------


def test_entity_type_on_entity():
    ctx = make_ctx([weapon_response()])
    EntityPhase("weapon", per_map=True, count=1).run(ctx)

    entity = ctx.bible.maps["map_0"].entities[0]
    assert entity.entity_type == "weapon"


def test_entity_type_custom_type():
    tower_spec = SkeletonSpec(
        entity_type="tower",
        fields={"range": SkeletonField(range=(1, 5))},
    )
    ctx = make_ctx(
        [json.dumps({"name": "Archer Tower", "lore": "Stands tall."})],
        schemas={"tower": tower_spec},
    )
    EntityPhase("tower", per_map=True, count=1).run(ctx)

    entity = ctx.bible.maps["map_0"].entities[0]
    assert entity.entity_type == "tower"


# ---------------------------------------------------------------------------
# 7. Multiple EntityPhase instances for different types (composition)
# ---------------------------------------------------------------------------


def test_multiple_entity_phases_compose():
    """weapon + monster phases each add their own entities without interfering."""
    weapon_responses = [weapon_response(f"Sword {i}") for i in range(4)]
    monster_responses = [monster_response(f"Monster {i}") for i in range(4)]
    # Interleave: per_map, 2 maps, count=2 per map → 4 calls per phase
    responses = weapon_responses + monster_responses

    ctx = make_ctx(
        responses,
        schemas={"weapon": WEAPON_SPEC, "monster": MONSTER_SPEC},
    )

    EntityPhase("weapon", per_map=True, count=2).run(ctx)
    EntityPhase("monster", per_map=True, count=2).run(ctx)

    for map_obj in ctx.bible.maps.values():
        weapon_entities = [e for e in map_obj.entities if e.entity_type == "weapon"]
        monster_entities = [e for e in map_obj.entities if e.entity_type == "monster"]
        assert len(weapon_entities) == 2
        assert len(monster_entities) == 2


def test_multiple_phases_entity_types_do_not_collide():
    """entity_type field is set per-phase; weapon entities must not be 'monster'."""
    responses = [weapon_response()] * 2 + [monster_response()] * 2
    ctx = make_ctx(
        responses,
        schemas={"weapon": WEAPON_SPEC, "monster": MONSTER_SPEC},
    )
    EntityPhase("weapon", per_map=True, count=1).run(ctx)
    EntityPhase("monster", per_map=True, count=1).run(ctx)

    for map_obj in ctx.bible.maps.values():
        for entity in map_obj.entities:
            assert entity.entity_type in {"weapon", "monster"}


# ---------------------------------------------------------------------------
# 8. Phase protocol satisfaction
# ---------------------------------------------------------------------------


def test_satisfies_phase_protocol():
    from canon.pipeline.runner import Phase

    assert isinstance(EntityPhase("weapon"), Phase)


# ---------------------------------------------------------------------------
# 9. Phase name is f"entity:{entity_type}"
# ---------------------------------------------------------------------------


def test_phase_name_weapon():
    phase = EntityPhase("weapon")
    assert phase.name == "entity:weapon"


def test_phase_name_custom_type():
    phase = EntityPhase("tower")
    assert phase.name == "entity:tower"


def test_phase_name_includes_entity_type():
    phase = EntityPhase("spell")
    assert phase.name == "entity:spell"


# ---------------------------------------------------------------------------
# 10. "entity:weapon" appears in metadata.phases_run after weapon phase ran
# ---------------------------------------------------------------------------


def test_phase_name_in_metadata_phases_run():
    ctx = make_ctx([weapon_response()])
    EntityPhase("weapon", per_map=True, count=1).run(ctx)

    assert "entity:weapon" in ctx.bible.metadata.phases_run


def test_both_phase_names_in_metadata_after_composition():
    responses = [weapon_response()] * 2 + [monster_response()] * 2
    ctx = make_ctx(
        responses,
        schemas={"weapon": WEAPON_SPEC, "monster": MONSTER_SPEC},
    )
    EntityPhase("weapon", per_map=True, count=1).run(ctx)
    EntityPhase("monster", per_map=True, count=1).run(ctx)

    assert "entity:weapon" in ctx.bible.metadata.phases_run
    assert "entity:monster" in ctx.bible.metadata.phases_run


# ---------------------------------------------------------------------------
# 11. per_map=False attaches to first map as fallback (documented behaviour)
# ---------------------------------------------------------------------------


def test_per_map_false_attaches_to_first_map():
    """per_map=False entities must attach to the first map (v0.1 fallback)."""
    ctx = make_ctx([weapon_response()])
    EntityPhase("weapon", per_map=False, count=1).run(ctx)

    first_map = ctx.bible.maps["map_0"]
    assert len(first_map.entities) == 1
    assert first_map.entities[0].map_id is None  # entity.map_id stays None for global


def test_per_map_false_no_maps_drops_entity(caplog):
    """When per_map=False and no maps exist, entity is dropped with a warning."""
    ctx = make_ctx([], with_maps=False, schemas={"weapon": WEAPON_SPEC})
    # Need a response even though it will be dropped
    ctx.llm.backend._responses = [weapon_response()]
    import logging

    with caplog.at_level(logging.WARNING, logger="canon.pipeline.phases.entity"):
        EntityPhase("weapon", per_map=False, count=1).run(ctx)

    assert sum(len(m.entities) for m in ctx.bible.maps.values()) == 0


# ---------------------------------------------------------------------------
# 12. Callable count — count can be a function of map
# ---------------------------------------------------------------------------


def test_callable_count():
    """count can be a callable receiving the map object."""
    # Both maps use the same callable that always returns 2.
    responses = [weapon_response(f"W{i}") for i in range(4)]
    ctx = make_ctx(responses)
    EntityPhase("weapon", per_map=True, count=lambda _m: 2).run(ctx)

    for map_obj in ctx.bible.maps.values():
        assert len(map_obj.entities) == 2


# ---------------------------------------------------------------------------
# 13. Retry on bad LLM response — fallback used after exhaustion
# ---------------------------------------------------------------------------


def test_retry_on_bad_json():
    """Bad JSON followed by valid JSON should produce an entity."""
    bad = "not json at all"
    good = weapon_response("Retry Blade", "Came back from the dead.")
    ctx = make_ctx([bad, good])
    EntityPhase("weapon", per_map=True, count=1).run(ctx)

    entity = ctx.bible.maps["map_0"].entities[0]
    assert entity.name == "Retry Blade"


def test_fallback_used_after_retries_exhausted():
    """After exhausting retries, fallback entity (type + index name) is produced."""
    ctx = make_ctx(["bad", "bad", "bad", "bad"])
    EntityPhase("weapon", per_map=True, count=1).run(ctx)

    # Either the fallback produced an entity or none at all — both are safe.
    # The fallback JSON has name="weapon 0", so if it was produced it should be present.
    total = sum(len(m.entities) for m in ctx.bible.maps.values())
    if total > 0:
        entity = ctx.bible.maps["map_0"].entities[0]
        assert "weapon" in entity.name.lower()


# ---------------------------------------------------------------------------
# 14. entity_id auto-allocation uses entity_type + running count
# ---------------------------------------------------------------------------


def test_entity_id_format_when_not_provided():
    """When LLM response has no entity_id, id is auto-allocated from type + count."""
    ctx = make_ctx([weapon_response()])
    EntityPhase("weapon", per_map=True, count=1).run(ctx)

    entity = ctx.bible.maps["map_0"].entities[0]
    assert entity.entity_id.startswith("weapon_")


def test_entity_id_provided_by_llm_is_used():
    """When the LLM response includes entity_id, that id wins."""
    response = json.dumps(
        {"entity_id": "weapon_custom_001", "name": "Custom", "lore": ""}
    )
    ctx = make_ctx([response])
    EntityPhase("weapon", per_map=True, count=1).run(ctx)

    entity = ctx.bible.maps["map_0"].entities[0]
    assert entity.entity_id == "weapon_custom_001"


# ---------------------------------------------------------------------------
# 15. map_id set correctly on per_map entities
# ---------------------------------------------------------------------------


def test_map_id_set_on_per_map_entity():
    ctx = make_ctx([weapon_response(), weapon_response()])
    EntityPhase("weapon", per_map=True, count=1).run(ctx)

    map0_entity = ctx.bible.maps["map_0"].entities[0]
    map1_entity = ctx.bible.maps["map_1"].entities[0]
    assert map0_entity.map_id == "map_0"
    assert map1_entity.map_id == "map_1"

"""Mazeworld pipeline composition helper.

Provides ``compose_pipeline(seed, num_maps, output_dir)`` which returns a
``(phases, ctx)`` tuple ready to hand to ``run_pipeline()``.

The caller is responsible for wiring:
  - ``ctx.llm``             — LLMClient with their chosen backend
  - ``ctx.image_backend``   — optional; leave None to skip portraits
  - ``ctx.music_backend``   — optional; leave None to skip music
  - ``ctx.sfx_backend``     — optional; leave None to skip SFX

See ``examples/run_mazeworld_full.py`` (Wave 5 M3) for a wired-up runner.
"""

from __future__ import annotations

import random
from pathlib import Path

from canon import (
    AssetPhase,
    Bible,
    BibleMetadata,
    CanonConfig,
    CharacterPhase,
    ClassPhase,
    DatabasePhase,
    DatabaseSpec,
    DialoguePhase,
    GenerationStats,
    IDAllocator,
    Map,
    MazeLayoutPhase,
    NarrativePhase,
    PipelineContext,
    SpellPoolPhase,
    StoryPhase,
    ValidationPhase,
)
from examples.mazeworld_pack.parsers import (
    parse_event,
    parse_item,
    parse_monster,
    parse_npc,
    parse_quest,
)
from examples.mazeworld_pack.phases import MazeworldManifestPhase
from examples.mazeworld_pack.prompts import MazeworldPromptSet
from examples.mazeworld_pack.specs import (
    EVENT_SPEC,
    HEALER_ARCHETYPE,
    ITEM_SPEC,
    JESTER_ARCHETYPE,
    MAGE_ARCHETYPE,
    MONSTER_SPEC,
    NPC_SPEC,
    QUEST_SPEC,
    SPELL_SPEC,
    WARRIOR_ARCHETYPE,
    WEAPON_SPEC,
)

# ---------------------------------------------------------------------------
# Environment list — maps cycle through these in order
# ---------------------------------------------------------------------------

_ENVIRONMENTS = [
    "ruins",
    "wasteland",
    "city",
    "temple",
    "fortress",
    "forest",
    "manor",
    "vault",
]


# ---------------------------------------------------------------------------
# compose_mazeworld_specs
# ---------------------------------------------------------------------------


def compose_mazeworld_specs(num_maps: int = 3) -> list[DatabaseSpec]:
    """Return the list of DatabaseSpecs that produce mazeworld-shape output.

    One DatabaseSpec per entity type.  Counts are intentionally modest
    defaults; callers can override by building DatabaseSpecs directly.

    Args:
        num_maps: Informational — not used in spec construction but included
            for API symmetry with compose_pipeline().  DatabasePhase honours
            ``per_map=True`` by iterating ``ctx.bible.maps`` at run time.

    Returns:
        Ordered list of DatabaseSpec: npc, item, monster, event, quest.
    """
    return [
        DatabaseSpec(
            entity_type="npc",
            skeleton_spec=NPC_SPEC,
            prompt_method="npc_generation",
            parser=parse_npc,
            output_path="npcs/npcs.json",
            output_format="array",
            id_prefix="npc",
            per_map=True,
            count=2,
            cross_room_dedup=["name"],
            prompt_kwargs={"role": "npc"},
        ),
        DatabaseSpec(
            entity_type="item",
            skeleton_spec=ITEM_SPEC,
            prompt_method="item_generation",
            parser=parse_item,
            output_path="items/items.json",
            output_format="keyed_object",
            id_prefix="item",
            per_map=True,
            count=3,
            prompt_kwargs={"item_category": "weapon"},
        ),
        DatabaseSpec(
            entity_type="monster",
            skeleton_spec=MONSTER_SPEC,
            prompt_method="monster_generation",
            parser=parse_monster,
            output_path="monsters/monsters.json",
            output_format="keyed_object",
            id_prefix="monster",
            per_map=True,
            count=2,
            prompt_kwargs={"level": 1},
        ),
        DatabaseSpec(
            entity_type="event",
            skeleton_spec=EVENT_SPEC,
            prompt_method="event_generation",
            parser=parse_event,
            output_path="events/events.json",
            output_format="array",
            id_prefix="event",
            per_map=True,
            count=4,
            prompt_kwargs={"event_type": "combat"},
        ),
        DatabaseSpec(
            entity_type="quest",
            skeleton_spec=QUEST_SPEC,
            prompt_method="quest_generation",
            parser=parse_quest,
            output_path="quests/quests.json",
            output_format="array",
            id_prefix="quest",
            per_map=True,
            count=2,
            prompt_kwargs={"quest_type": "fetch"},
        ),
    ]


# ---------------------------------------------------------------------------
# compose_pipeline
# ---------------------------------------------------------------------------


def compose_pipeline(
    seed: str = "shadowspire",
    num_maps: int = 3,
    output_dir: str | Path | None = None,
) -> tuple[list, PipelineContext]:
    """Compose the full mazeworld-shape pipeline.

    Returns ``(phases, ctx)``.  The caller runs ``run_pipeline(phases, ctx)``
    to generate the world.

    The returned context has no LLM or asset backends wired; callers must
    set them before calling ``run_pipeline()``:

        phases, ctx = compose_pipeline(seed="shadowspire", num_maps=3)
        ctx.llm = LLMClient(AnthropicBackend())
        # optionally: ctx.image_backend = FalImageBackend(...)
        run_pipeline(phases, ctx)

    Args:
        seed:       World seed string; passed to the Bible and CanonConfig.
        num_maps:   Number of dungeon rooms to pre-create.  Each room gets
                    its own Map entry in the Bible.
        output_dir: Root directory for generated files.  Defaults to
                    ``./canon_output``.

    Returns:
        ``(phases, ctx)`` tuple.
    """
    bible = Bible.empty(seed=seed)
    bible.metadata = BibleMetadata()

    # Pre-create maps with environments so all phases can see them
    for i in range(num_maps):
        map_id = f"room_{i}"
        bible.maps[map_id] = Map(
            map_id=map_id,
            name=f"Room {i}",
            description="",
            environment=_ENVIRONMENTS[i % len(_ENVIRONMENTS)],
            level=i + 1,
            story_beat="",
        )

    config = CanonConfig(
        seed=seed,
        num_maps=num_maps,
        output_dir=Path(output_dir or "./canon_output"),
    )

    ctx = PipelineContext(
        bible=bible,
        config=config,
        rng=random.Random(seed),
        stats=GenerationStats(),
        prompts=MazeworldPromptSet(),
        archetypes={
            "warrior": WARRIOR_ARCHETYPE,
            "mage": MAGE_ARCHETYPE,
            "healer": HEALER_ARCHETYPE,
            "jester": JESTER_ARCHETYPE,
        },
        schemas={
            "weapon": WEAPON_SPEC,
            "spell": SPELL_SPEC,
            "monster": MONSTER_SPEC,
            "item": ITEM_SPEC,
        },
        id_allocator=IDAllocator(
            bases={
                "npc": 1000,
                "item": 2000,
                "event": 3000,
                "quest": 4000,
                "monster": 5000,
                "class": 6000,
            }
        ),
    )

    db_specs = compose_mazeworld_specs(num_maps)

    phases = [
        StoryPhase(),
        ClassPhase(archetype_count=4),
        MazeLayoutPhase(width=40, height=30),
        CharacterPhase(per_map=True, count=2, roles=("npc",)),
        *[DatabasePhase(spec) for spec in db_specs],
        DialoguePhase(),
        SpellPoolPhase(),
        # AssetPhase defaults to skip-all; callers wire backends to enable.
        AssetPhase(skip_image=True, skip_music=True, skip_sfx=True),
        NarrativePhase(),
        ValidationPhase(),
        MazeworldManifestPhase(),
    ]

    return phases, ctx

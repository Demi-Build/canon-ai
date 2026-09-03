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
    ClassPhase,
    DatabasePhase,
    DatabaseSpec,
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
from canon.packs.dungeon.dialogue import MazeworldDialoguePhase
from canon.packs.dungeon.parsers import (
    parse_event,
    parse_item,
    parse_monster,
    parse_npc,
    parse_quest,
)
from canon.packs.dungeon.phases import MazeworldManifestPhase
from canon.packs.dungeon.placement import MazeworldPlacementPhase
from canon.packs.dungeon.prompts import MazeworldPromptSet
from canon.packs.dungeon.specs import (
    EVENT_SPEC,
    HEALER_ARCHETYPE,
    ITEM_SPEC,
    JESTER_ARCHETYPE,
    MAGE_ARCHETYPE,
    MAZEWORLD_CLASS_LOADOUTS,
    MAZEWORLD_POOL_SPECS,
    MONSTER_SPEC,
    NPC_SPEC,
    QUEST_SPEC,
    SPELL_SPEC,
    WARRIOR_ARCHETYPE,
    WEAPON_SPEC,
)
from canon.packs.dungeon.validators import mazeworld_validators

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

# Evocative per-environment place names so rooms read as locations, not "Room 0".
# Pack data (cosmetic, UI-facing); NarrativePhase still rewrites descriptions.
_ENV_PLACE_NAMES = {
    "ruins": "The Sunken Ruins",
    "wasteland": "The Ashen Waste",
    "city": "The Hollow City",
    "temple": "The Veiled Temple",
    "fortress": "The Iron Bastion",
    "forest": "The Whispering Wood",
    "manor": "The Shrouded Manor",
    "vault": "The Sealed Vault",
}


# ---------------------------------------------------------------------------
# compose_mazeworld_specs
# ---------------------------------------------------------------------------


# Pack-owned default counts. Core canon holds NO game-specific counts — the
# mazeworld pack declares its own. Keys: per-map entity counts ("npc"/"item"/
# "monster"/"event"/"quest") consumed here, plus "class" (consumed by
# compose_pipeline as ClassPhase.archetype_count). A different game's pack would
# declare different keys (e.g. a shooter: "weapon"/"enemy"/"objective").
MAZEWORLD_DEFAULT_COUNTS: dict[str, int] = {
    "npc": 2,
    "item": 3,
    "monster": 2,
    "event": 4,
    "quest": 2,
    "class": 4,
}


def compose_mazeworld_specs(
    num_maps: int = 3, counts: dict[str, int] | None = None
) -> list[DatabaseSpec]:
    """Return the list of DatabaseSpecs that produce mazeworld-shape output.

    One DatabaseSpec per entity type.

    Args:
        num_maps: Informational — not used in spec construction but included
            for API symmetry with compose_pipeline().  DatabasePhase honours
            ``per_map=True`` by iterating ``ctx.bible.maps`` at run time.
        counts: Per-entity-type counts.  Missing keys fall back to
            ``MAZEWORLD_DEFAULT_COUNTS``.  The ``"class"`` key is consumed by
            ``compose_pipeline`` (ClassPhase), not here.

    Returns:
        Ordered list of DatabaseSpec: item, monster, npc, event, quest.

        Order matters for cross-references: items + monsters generate before
        NPCs (so merchant shops can stock real items) and before events (loot /
        puzzle tools / monster_ids); NPCs before quests (quest givers); events
        before quests (combat-quest targets).
    """
    c = {**MAZEWORLD_DEFAULT_COUNTS, **(counts or {})}
    return [
        DatabaseSpec(
            entity_type="item",
            skeleton_spec=ITEM_SPEC,
            prompt_method="item_generation",
            parser=parse_item,
            output_path="items/items.json",
            output_format="keyed_object",
            id_prefix="item",
            per_map=True,
            count=c["item"],
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
            count=c["monster"],
            prompt_kwargs={"level": 1},
        ),
        DatabaseSpec(
            entity_type="npc",
            skeleton_spec=NPC_SPEC,
            prompt_method="npc_generation",
            parser=parse_npc,
            output_path="npcs/npcs.json",
            output_format="array",
            id_prefix="npc",
            per_map=True,
            count=c["npc"],
            cross_room_dedup=["name"],
            prompt_kwargs={"role": "npc"},
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
            count=c["event"],
            # No event_type here on purpose: the EVENT_SPEC skeleton rolls
            # combat/puzzle/event per entity, and event_generation reads that
            # rolled value. Hard-coding a type here would desync the prompt
            # from the parser's skeleton-driven branch.
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
            count=c["quest"],
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
    counts: dict[str, int] | None = None,
    model: str | None = None,
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
        counts:     Per-entity-type generation counts (keys: npc/item/monster/
                    event/quest/class). Missing keys fall back to
                    ``MAZEWORLD_DEFAULT_COUNTS``.  Recorded on ``CanonConfig``.
        model:      Optional LLM model id; recorded on ``CanonConfig`` for the
                    runner to apply when it builds the LLM backend.

    Returns:
        ``(phases, ctx)`` tuple.
    """
    resolved_counts = {**MAZEWORLD_DEFAULT_COUNTS, **(counts or {})}

    bible = Bible.empty(seed=seed)
    bible.metadata = BibleMetadata()

    # Pre-create maps with environments so all phases can see them
    for i in range(num_maps):
        map_id = f"room_{i}"
        env = _ENVIRONMENTS[i % len(_ENVIRONMENTS)]
        place = _ENV_PLACE_NAMES.get(env, env.title())
        # Disambiguate when the environment list wraps (num_maps > 8).
        name = place if i < len(_ENVIRONMENTS) else f"{place} {i // len(_ENVIRONMENTS) + 1}"
        bible.maps[map_id] = Map(
            map_id=map_id,
            name=name,
            description="",
            environment=env,
            level=i + 1,
            story_beat="",
        )

    config = CanonConfig(
        seed=seed,
        num_maps=num_maps,
        output_dir=Path(output_dir or "./canon_output"),
        counts=(counts or {}),
        model=model,
    )

    # The registry id rides the context so MazeworldManifestPhase mirrors it
    # into manifest.json.pack_type (P0 paper P.4.1). Function-level import:
    # spec.py builds its seed FROM this module's counts + DatabaseSpecs.
    from canon.packs.dungeon.spec import PACK_SPEC

    ctx = PipelineContext(
        bible=bible,
        config=config,
        rng=random.Random(seed),
        stats=GenerationStats(),
        prompts=MazeworldPromptSet(),
        pack_type=PACK_SPEC.pack_type,
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

    # Register the pack's referential-integrity / solvability validators so
    # ValidationPhase actively guards against hallucinated ids, unsolvable
    # puzzles, and uncompletable quests (it is a no-op without these). They read
    # the persisted JSON under output_dir, which is fully written by the time
    # ValidationPhase runs.
    ctx.validators = mazeworld_validators(config.output_dir)

    db_specs = compose_mazeworld_specs(num_maps, resolved_counts)

    phases = [
        StoryPhase(),
        # Loadout mode: generate full classes (stats fixed to budget + spells +
        # abilities) from the pack's archetype specs. The class count slices how
        # many archetypes to generate (warrior/mage first for the trial config).
        ClassPhase(loadout_specs=MAZEWORLD_CLASS_LOADOUTS[: resolved_counts["class"]]),
        MazeLayoutPhase(width=40, height=30),
        # NPCs are generated once, by DatabasePhase(npc) — the single NPC
        # source. MazeworldDialoguePhase then generates a dialogue tree for
        # each and inlines it into npcs.json (Option A: no duplicate
        # CharacterPhase NPCs, no orphaned trees in bible.dialogues).
        *[DatabasePhase(spec) for spec in db_specs],
        MazeworldDialoguePhase(),
        SpellPoolPhase(pool_specs=MAZEWORLD_POOL_SPECS),
        # AssetPhase defaults to skip-all; callers wire backends to enable.
        AssetPhase(skip_image=True, skip_music=True, skip_sfx=True),
        NarrativePhase(),
        # Placement runs after maze + entities exist; rewrites each maze.json
        # with npc_positions / event_positions / item_placements / quest_ids.
        MazeworldPlacementPhase(),
        ValidationPhase(),
        MazeworldManifestPhase(),
    ]

    return phases, ctx

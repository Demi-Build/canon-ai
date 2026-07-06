"""Pydantic data models for the canon Bible — the single source of truth.

A Bible is the accumulated, persisted record of a generated world. Pipeline
phases populate it incrementally; downstream consumers (cradle, the runtime
game) read from it. Empty collections are the rule — not every game uses every
field.

These models are deliberately game-agnostic. MazeWorld's "room" is a Map; a
Destiny destination is a Map; a tower defense level is a Map. Domain-specific
fields live on `extra: dict` escape hatches.

Forward reference note: `Bible.dialogues` is typed as `dict[str, DialogueTree]`,
but `DialogueTree` lives in `canon.dialogue.models` and is built by a parallel
agent. We use `TYPE_CHECKING` + a string annotation here to avoid an import
cycle, then call `Bible.model_rebuild()` opportunistically at module footer.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Literal

from pydantic import BaseModel, Field

# `canon.layout` depends only on pydantic — no circular import with canon.bible.
# Import at runtime so the discriminated union resolves correctly and Pydantic
# serializes the full subclass schema (not just the base Layout fields).
from canon.bible.artifacts import PhaseStatus  # noqa: E402
from canon.bible.platformer import (  # noqa: E402
    BossDefinition,
    EnemyDefinition,
    Level,
    Stage,
    Tileset,
    World,
)
from canon.layout import MazeLayout  # noqa: E402

# ---------------------------------------------------------------------------
# 1.0 Ability + Spell typed models (A8)
# ---------------------------------------------------------------------------


class Ability(BaseModel):
    """A typed combat ability.

    ``stat`` should be one of the standard RPG stats (STR, DEX, CON, INT,
    WIS, CHA, LUCK) but is kept as ``str`` to remain extensible across game
    types.
    """

    name: str
    description: str = ""
    stat: str  # "STR" | "DEX" | "CON" | "INT" | "WIS" | "CHA" | "LUCK"
    stamina_cost: int = 0


class Spell(BaseModel):
    """A typed magical spell.

    ``spell_type`` categorises the mechanical effect (damage, heal, buff, etc.).
    ``element`` is the thematic element (fire, frost, light, dark, …).
    Both are kept as ``str`` to remain extensible.
    """

    name: str
    spell_type: str  # "damage_single" | "damage_multi" | "buff_stat" | "heal" | ...
    element: str  # "fire" | "frost" | "light" | "dark" | ...
    stat: str
    targets: str = "single"
    num_dice: int = 1
    die_sides: int = 6
    heal_amount: int = 0
    buff_stat: str | None = None
    buff_value: int = 0
    buff_duration: int = 0
    stamina_cost: int = 0
    description: str = ""

if TYPE_CHECKING:
    # Imported only for type-checkers; at runtime we keep the reference as a
    # forward string so canon.bible can be used independently of canon.dialogue.
    from canon.dialogue.models import DialogueTree

# Discriminated-union alias for Map.layout.  Pydantic v2 uses the `layout_type`
# Literal discriminator to serialize the full subclass schema and deserialize
# to the correct concrete type.  New Layout subclasses must be added here.
_LayoutUnion = Annotated[MazeLayout, Field(discriminator="layout_type")]


# ---------------------------------------------------------------------------
# 1.1 BibleMetadata
# ---------------------------------------------------------------------------


class BibleMetadata(BaseModel):
    """Metadata stamped onto every persisted Bible."""

    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    model_stack: dict[str, str] = Field(default_factory=dict)
    phases_run: list[str] = Field(default_factory=list)
    total_cost: float = 0.0
    total_llm_calls: int = 0
    # Coarse per-phase status (PRD §6.1), alongside the fine per-artifact
    # ArtifactMeta.status. Populated by the Phase 2 orchestrator; empty for
    # MazeWorld runs.
    phase_status: dict[str, PhaseStatus] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# 1.3 StoryArc / Faction / StoryBeat
# ---------------------------------------------------------------------------


class Faction(BaseModel):
    """A faction within the world's narrative.

    `factions` is plural on `StoryArc` — a single-faction world uses a
    one-element list (Destiny has six; MazeWorld has one).
    """

    faction_id: str
    name: str
    description: str
    history: str = ""
    leader: str = ""
    threat_level: int | None = None
    aesthetic: str | None = None


class StoryBeat(BaseModel):
    """One map's role in the escalation arc.

    `boss_name` and `boss_lore` are optional — not every map has a boss.
    """

    map_id: str
    beat: str
    boss_name: str | None = None
    boss_lore: str | None = None


class StoryArc(BaseModel):
    """Opinionated narrative model — generic enough for non-conflict-driven
    stories (factions and escalation_arc may be empty), but shaped for the
    common case of protagonists working toward a climax.

    `key_character_names` is included per verification doc §1.1 — used in
    MazeWorld to avoid duplicate naming during character generation.
    """

    title: str
    synopsis: str
    seed: str
    factions: list[Faction] = Field(default_factory=list)
    primary_antagonist_faction_id: str | None = None
    escalation_arc: list[str] = Field(default_factory=list)
    climax: str | None = None
    final_entity_id: str | None = None
    final_entity_lore: str | None = None
    beats: list[StoryBeat] = Field(default_factory=list)
    key_character_names: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 1.6 EntityLore + GenerationTrail
# ---------------------------------------------------------------------------


class GenerationTrail(BaseModel):
    """Per-entity record of how it was produced. Cradle surfaces this on the
    'Generation trail' tab.
    """

    prompt: str = ""
    response: str = ""
    validation_history: list[dict] = Field(default_factory=list)
    retry_count: int = 0
    cost: float | None = None
    model: str | None = None


class EntityLore(BaseModel):
    """Universal lore container for non-Character entities.

    Per verification doc §1.4: `name` is required, `lore` defaults to empty
    string (some entities are mechanical-only), `skeleton` defaults to empty
    dict.
    """

    entity_id: str
    entity_type: str
    name: str
    map_id: str | None = None
    lore: str = ""
    skeleton: dict = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)
    generation_trail: GenerationTrail | None = None
    extra: dict = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# 1.2 Map and Zone
# ---------------------------------------------------------------------------


class Zone(BaseModel):
    """Optional sub-region within a Map."""

    zone_id: str
    name: str
    description: str = ""
    parent_map_id: str
    entities: list[EntityLore] = Field(default_factory=list)


class Map(BaseModel):
    """A spatial unit. MazeWorld's 'room' is a Map; a Destiny destination is
    a Map; a tower defense level is a Map.

    Entities live in a single flat list — `entity_type` discriminates. This
    is a deliberate simplification of MazeWorld's per-type buckets (npcs,
    items, monsters).

    `layout` carries the spatial structure of the map (maze grid, platformer
    tilemaps, etc.) as a forward-string reference to `Layout`.  It defaults
    to `None` so all existing Map constructions remain valid.  Resolved via
    `Map.model_rebuild()` at module footer.
    """

    map_id: str
    name: str
    description: str = ""
    environment: str = ""
    level: int | None = None
    story_beat: str = ""
    zones: list[Zone] = Field(default_factory=list)
    entities: list[EntityLore] = Field(default_factory=list)
    connections: list[str] = Field(default_factory=list)
    layout: _LayoutUnion | None = None
    extra: dict = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# 1.4 Character + CharacterClass + CharacterRole
# ---------------------------------------------------------------------------


CharacterRole = Literal[
    "npc",
    "pc",
    "merchant",
    "hostile",
    "follower",
    "boss",
    "rival",
    "villager",
    "trainer",
    "neutral",
]


class CharacterClass(BaseModel):
    """Optional mechanical data for combat-relevant characters.

    Stats are a generic `dict[str, int]` so canon doesn't bake in any
    particular stat scheme. Abilities and spells are typed inline objects
    (not reference IDs) so the full mechanical definition is self-contained.
    """

    archetype_id: str
    stats: dict[str, int] = Field(default_factory=dict)
    abilities: list[Ability] = Field(default_factory=list)
    spells: list[Spell] = Field(default_factory=list)
    equipment: list[str] = Field(default_factory=list)
    skeleton: dict = Field(default_factory=dict)


class Character(BaseModel):
    """Unified model for every named actor canon generates.

    An NPC is a Character with role='npc'. A Stardew villager is
    role='villager'. A Pokémon trainer is role='trainer'. A PC-eligible
    character is role='pc' with class_data populated.

    `primary_map_id` is optional — Destiny-scale characters (e.g. a Tower
    vendor relevant everywhere) aren't bound to a single map.

    The new A7 fields (job, hobby, opening_greeting, portrait_prompt,
    personality_notes, exhausted_dialogue, traits, and the three additional
    dialogue tree variant IDs) all default so existing Character constructions
    remain valid without change.
    """

    character_id: str
    name: str
    role: CharacterRole
    primary_map_id: str | None = None
    lore: str = ""
    personality: str | None = None
    appearance: str | None = None
    faction_id: str | None = None
    class_data: CharacterClass | None = None
    dialogue_tree_id: str | None = None
    portrait_path: str | None = None
    generation_trail: GenerationTrail | None = None
    extra: dict = Field(default_factory=dict)
    # A7 extension — universal RPG/sim/VN fields
    job: str | None = None
    hobby: str | None = None
    opening_greeting: str | None = None
    portrait_prompt: str | None = None
    personality_notes: list[str] = Field(default_factory=list)
    exhausted_dialogue: str | None = None
    traits: dict[str, Any] = Field(default_factory=dict)
    # A7 — additional dialogue tree variant refs
    dialogue_tree_complete_id: str | None = None
    dialogue_tree_failed_id: str | None = None
    dialogue_tree_incomplete_id: str | None = None


# ---------------------------------------------------------------------------
# 1.5 ClassArchetype
# ---------------------------------------------------------------------------


class ClassArchetype(BaseModel):
    """Declarative spec for a character class, job, or archetype.

    Canon doesn't know what these are — the user defines them. `stat_roles`
    is included per verification doc §1.6 — maps "primary"/"secondary"/"dump"
    to stat-name lists, which generalizes MazeWorld's ARCHETYPE_STAT_ROLES.

    A8 extension: typed Ability/Spell lists replace string ID lists; new
    cosmetic/flavour fields added to match mazeworld's classes.json shape.
    ``archetype`` is a short human-readable archetype string (e.g. "warrior",
    "mage") independent of ``archetype_id`` — mazeworld carries both.
    ``abilities`` and ``spells`` are the *chosen* loadout for this archetype;
    ``ability_pool`` / ``spell_pool`` are the full pool from which they're drawn.
    """

    archetype_id: str
    archetype: str = ""
    name: str
    description: str = ""
    flavor_text: str | None = None
    starting_weapon: str | None = None
    portrait_prompt: str | None = None
    portrait_path: str | None = None
    category: str | None = None
    stat_template: dict[str, int] = Field(default_factory=dict)
    stat_budget: int | None = None
    stat_roles: dict[str, list[str]] = Field(default_factory=dict)
    role_tags: list[str] = Field(default_factory=list)
    abilities: list[Ability] = Field(default_factory=list)
    spells: list[Spell] = Field(default_factory=list)
    ability_pool: list[Ability] = Field(default_factory=list)
    spell_pool: list[Spell] = Field(default_factory=list)
    starting_equipment: list[str] = Field(default_factory=list)
    lore: str | None = None
    extra: dict = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# 1.1 Bible — the top-level container
# ---------------------------------------------------------------------------


class Bible(BaseModel):
    """Single source of truth for a generated world.

    Accumulated incrementally by pipeline phases; persisted as JSON; used as
    context for every LLM call after the first.

    `dialogues` uses a forward reference to `DialogueTree` (lives in
    `canon.dialogue.models`, owned by a different agent). The forward
    reference resolves once both modules are imported by the package's
    top-level `__init__.py`.
    """

    canon_version: str = "0.1"
    seed: str
    story: StoryArc
    maps: dict[str, Map] = Field(default_factory=dict)
    characters: list[Character] = Field(default_factory=list)
    class_archetypes: dict[str, ClassArchetype] = Field(default_factory=dict)
    # Forward string reference so this module is usable even when
    # canon.dialogue is not importable. Resolved lazily via model_rebuild().
    dialogues: dict[str, DialogueTree] = Field(default_factory=dict)
    metadata: BibleMetadata = Field(default_factory=BibleMetadata)

    # ------------------------------------------------------------------ #
    # Platformer entities (PRD §6.1) — additive; empty for MazeWorld.
    # Keyed by their own IDs (stage_id, enemy_id, …); artifact IDs live on
    # each entity's ArtifactMeta.artifact_id. A game populates the fields
    # its pack uses and leaves the rest empty (maps vs stages coexist).
    # ------------------------------------------------------------------ #
    world: World | None = None
    stages: dict[str, Stage] = Field(default_factory=dict)
    levels: dict[str, Level] = Field(default_factory=dict)
    enemy_definitions: dict[str, EnemyDefinition] = Field(default_factory=dict)
    boss_definitions: dict[str, BossDefinition] = Field(default_factory=dict)
    tilesets: dict[str, Tileset] = Field(default_factory=dict)

    # ------------------------------------------------------------------ #
    # Factories
    # ------------------------------------------------------------------ #

    @classmethod
    def empty(cls, seed: str) -> Bible:
        """Build an empty Bible seeded with `seed`.

        `canon_version` defaults to "0.1". Story is a minimal StoryArc
        carrying the same seed; everything else is empty.
        """

        return cls(
            seed=seed,
            story=StoryArc(title="", synopsis="", seed=seed),
            metadata=BibleMetadata(),
        )

    # ------------------------------------------------------------------ #
    # Context assembly (LLM-facing)
    #
    # Implementation lives in `canon.bible.context`. We import lazily
    # inside the methods to avoid a circular import (the context module
    # type-hints `Bible`).
    # ------------------------------------------------------------------ #

    def get_context(self, map_id: str) -> str:
        from canon.bible.context import get_context

        return get_context(self, map_id)

    def get_cumulative_context(self, map_id: str, max_chars: int | None = None) -> str:
        from canon.bible.context import get_cumulative_context

        return get_cumulative_context(self, map_id, max_chars=max_chars)

    # ------------------------------------------------------------------ #
    # Convenience accessors
    #
    # All accessors raise KeyError on a missing ID. KeyError is the natural
    # choice given the underlying dict-keyed and id-keyed lookups, and it
    # keeps consistent with stdlib expectations.
    # ------------------------------------------------------------------ #

    def get_map(self, map_id: str) -> Map:
        """Return the Map with the given id. Raises KeyError if missing."""

        if map_id not in self.maps:
            raise KeyError(f"map_id not found: {map_id!r}")
        return self.maps[map_id]

    def get_character(self, character_id: str) -> Character:
        """Return the Character with the given id. Raises KeyError if missing."""

        for character in self.characters:
            if character.character_id == character_id:
                return character
        raise KeyError(f"character_id not found: {character_id!r}")

    def get_entity(self, map_id: str, entity_id: str) -> EntityLore:
        """Return the EntityLore on `map_id` with the given id.

        Raises KeyError if either the map or the entity cannot be found.
        """

        the_map = self.get_map(map_id)
        for entity in the_map.entities:
            if entity.entity_id == entity_id:
                return entity
        raise KeyError(f"entity_id not found on map {map_id!r}: {entity_id!r}")

    def get_dialogue(self, character_id: str) -> DialogueTree | None:
        """Return the dialogue tree associated with `character_id`, or None.

        We return None instead of raising — many characters have no dialogue
        at all (boss enemies, lookalikes), and callers commonly branch on
        presence.
        """

        return self.dialogues.get(character_id)

    # ------------------------------------------------------------------ #
    # Mutation
    # ------------------------------------------------------------------ #

    def add_character(self, character: Character) -> None:
        """Append a Character to the bible."""

        self.characters.append(character)

    def add_entity(self, map_id: str, entity: EntityLore) -> None:
        """Append an EntityLore to a Map's entity list. Raises KeyError if
        the map doesn't exist."""

        the_map = self.get_map(map_id)
        the_map.entities.append(entity)

    def add_dialogue(self, dialogue: DialogueTree) -> None:
        """Register a dialogue tree, keyed by its `character_id`."""

        # `character_id` is part of the DialogueTree contract owned by
        # canon.dialogue.models. We access it as an attribute rather than
        # importing the type at runtime.
        self.dialogues[dialogue.character_id] = dialogue

    def update_entity(self, map_id: str, entity_id: str, **fields: Any) -> None:
        """Update fields on an EntityLore in place. Raises KeyError if the
        entity is missing."""

        entity = self.get_entity(map_id, entity_id)
        for key, value in fields.items():
            setattr(entity, key, value)

    def remove_entity(self, map_id: str, entity_id: str) -> None:
        """Remove an EntityLore from a Map. Raises KeyError if missing."""

        the_map = self.get_map(map_id)
        for index, entity in enumerate(the_map.entities):
            if entity.entity_id == entity_id:
                del the_map.entities[index]
                return
        raise KeyError(f"entity_id not found on map {map_id!r}: {entity_id!r}")

    # ------------------------------------------------------------------ #
    # Persistence
    # ------------------------------------------------------------------ #

    def persist(self, path: str | Path) -> None:
        """Serialize to JSON on disk with 2-space indent."""

        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        # `mode="json"` ensures datetimes and Path values become strings.
        target.write_text(
            json.dumps(self.model_dump(mode="json"), indent=2),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, path: str | Path) -> Bible:
        """Load a Bible from JSON on disk."""

        return cls.model_validate_json(Path(path).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Forward-reference resolution
# ---------------------------------------------------------------------------
#
# `Bible.dialogues` references `DialogueTree`, which lives in
# `canon.dialogue.models`. Pydantic v2 refuses to instantiate a model with
# an unresolved forward reference, so we import the real type once and
# rebuild. The dialogue module is now part of canon's public surface, so
# the previous placeholder fallback is no longer required.
#
# `Map.layout` references `Layout` from `canon.layout`. We rebuild `Map`
# first (Layout is simpler — no further forward refs) then `Bible`.

from canon.dialogue.models import DialogueTree  # noqa: E402, F401

Bible.model_rebuild()

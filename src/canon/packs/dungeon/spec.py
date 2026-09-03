"""The dungeon template's registry seed — ``PACK_SPEC`` for pack_type
``"dungeon"`` (P0 paper P.1, P.3, P.4, P.5.3, row P0-3).

The nine ``EntityKind`` entries are the P.1.1–P.1.7 and P.5.3 JSON blocks
copied as data (every list is an open vocabulary; on-disk names stay —
``maze_ref``, ``type``, the ``_NPC_TYPE_MAP`` class names — with the
skeleton→disk renames carried as ``renames`` maps the writer applies and the
loader inverts, never a disk rename: master §2, decision 2.3.4). The module
keeps its ``mazeworld`` name; the registry id it declares is ``dungeon``
(W2.0's rename pass, not this row's).

Seed-only generation callables (parser / prompt_method / prompt_kwargs /
per-room dedup) are JOINED from ``compose_mazeworld_specs()`` — the
``DatabaseSpec`` list the pipeline already runs — so the registry entry and
the generator cannot drift (tests assert the layouts agree).

Row P0-5 (W1 P2 read) fills the ``loader`` slot P0-3 left empty: every kind
binds ``loaders.load_rows`` to itself (seed-only, never stamped — P.3.1), so
``pack info``'s row counts and the read-back rows come from ONE ``layout``
datum; the room ``GridKind``'s ``cell_vocab`` names the shipped tile registry
(``tiles.json``, P.6.3) that ``adapters.dungeon_read`` synthesises a tileset
from.

Row P0-10 fills the three slots P0-3 left open: ``phase_labels`` (the §3.0-E
phase-id → label map — the dungeon gets its labels from DATA the same way the
platformer now does, never a second hardcoded list), the wizard's ``ranges``
(P.9 R8), and ``runner`` (how ``world new --template dungeon`` spawns
``canon.packs.dungeon.run_world`` — the module that finally attaches a
StepLog, W2's second wiring blocker).

Deliberately absent, by row ownership: ``schemas/<kind>.json`` files
(P.1.11's authoring plan — the entries name the path P0-6's dynamic models
will read); the ``tuning_vocabulary`` (none — the dungeon's constants live in
the external engine until the W2.0 pull-in, P.4.5).
"""

from __future__ import annotations

from functools import partial
from pathlib import Path

from canon.dialogue.models import DialogueTree
from canon.packs.dungeon.compose import (
    MAZEWORLD_DEFAULT_COUNTS,
    compose_mazeworld_specs,
    compose_pipeline,
)
from canon.packs.dungeon.estimate import ESTIMATOR as _ESTIMATOR
from canon.packs.dungeon.loaders import load_rows
from canon.packs.dungeon.prompts import MazeworldPromptSet
from canon.packs.dungeon.specs import (
    HEALER_ARCHETYPE,
    ITEM_SPEC,
    JESTER_ARCHETYPE,
    MAGE_ARCHETYPE,
    MAZEWORLD_CLASS_LOADOUTS,
    MONSTER_SPEC,
    SPELL_SPEC,
    WARRIOR_ARCHETYPE,
    WEAPON_SPEC,
)
from canon.packs.dungeon.validators import mazeworld_validators
from canon.packs.spec import EntityKind, GridKind, PackSpec, default_dialogue

PACK_TYPE = "dungeon"

_TEMPLATE_DIR = Path(__file__).parent

# ---------------------------------------------------------------------------
# P.1.1–P.1.7 + P.5.3 — the nine registry entries, as data
# ---------------------------------------------------------------------------

_ENTITY_DATA: dict[str, dict] = {
    # P.1.1 — the one canonical full entry
    "npc": {
        "label": "NPCs",
        "layout": {"mode": "collection", "path": "npcs/npcs.json", "format": "array"},
        "id_field": "id", "id_alloc": {"base": 1000}, "schema": "schemas/npc.json",
        "renames": {"behavior_type": "type"},
        "llm_fields": [
            "name", "job", "hobby", "personality", "backstory", "opening_greeting",
            "portrait_prompt", "exhausted_dialogue", "personality_notes",
        ],
        "code_fields": [
            "id", "type", "environment", "environment_name", "x", "y", "color", "selected",
            "is_story_npc", "max_dialogue_turns", "quest_id", "quest_type", "shop_inventory",
            "npc_monster", "profile_image", "dialogue_tree", "dialogue_tree_incomplete",
            "dialogue_tree_complete", "dialogue_tree_failed",
        ],
        "user_fields": ["availability", "description"],
        "hidden": ["selected", "quest_target_tile"],
        "decorative": ["quest_type", "is_story_npc"],
        "nesting": {"item_id": "shop_inventory"},
        "containers": ["shop_inventory", "npc_monster"],
        "protected": ["id", "profile_image", "selected"],
        "routed": {
            "x": "grid", "y": "grid",
            "dialogue_tree": "dialogue", "dialogue_tree_incomplete": "dialogue",
            "dialogue_tree_complete": "dialogue", "dialogue_tree_failed": "dialogue",
            "dialogue_trees": "dialogue",
        },
        "refs": {"quest_id": "quest.id", "shop_inventory[].item_id": "item.id"},
        "phase_label": "db:npc", "per_map": True, "count_key": "npc", "dedup": ["name"],
        "asset": {"field": "profile_image", "kinds": ["image"], "targets": ["npc:<id>"]},
        "vocab": {},
    },
    # P.1.2
    "monster": {
        "label": "Monsters",
        "layout": {"mode": "collection", "path": "monsters/monsters.json", "format": "keyed_object"},
        "id_field": "id", "id_alloc": {"base": 5000}, "schema": "schemas/monster.json",
        "llm_fields": [
            "name", "species", "description", "backstory", "damage_type", "elemental_affinity",
            "weakness", "abilities", "portrait_prompt",
        ],
        "code_fields": [
            "id", "hp_range", "ac_range", "damage_die", "level", "is_boss", "environment",
            "profile_image",
        ],
        "decorative": ["tier", "level", "damage_die", "weakness", "is_boss", "environment"],
        "containers": ["abilities"],
        "protected": ["id", "profile_image"],
        "phase_label": "db:monster", "per_map": True, "count_key": "monster",
    },
    # P.1.3
    "item": {
        "label": "Items",
        "layout": {"mode": "collection", "path": "items/items.json", "format": "keyed_object"},
        "id_field": "id", "id_alloc": {"base": 2000}, "schema": "schemas/item.json",
        "renames": {
            "item_kind": "category", "physical_type": "damage_type",
            "weapon_stat": "item_stats.stat_modifier", "tool_attribute": "item_stats.attribute",
        },
        "llm_fields": ["name", "desc", "portrait_prompt"],
        "code_fields": [
            "id", "room_level", "magic_element", "attack_dice", "price", "health_value",
            "stamina_value", "uses", "tags", "profile_image",
        ],
        "user_fields": ["spell_effect"],
        "decorative": ["rarity", "portrait_prompt", "tags"],
        "nesting": {
            "attack_dice": "item_stats", "stat_modifier": "item_stats", "price": "item_stats",
            "health_value": "item_stats", "stamina_value": "item_stats", "uses": "item_stats",
            "attribute": "item_stats",
        },
        "containers": ["item_stats"],
        "protected": ["id", "profile_image"],
        "phase_label": "db:item", "per_map": True, "count_key": "item",
    },
    # P.1.4
    "quest": {
        "label": "Quests",
        "layout": {"mode": "collection", "path": "quests/quests.json", "format": "array"},
        "id_field": "id", "id_alloc": {"base": 4000}, "schema": "schemas/quest.json",
        "renames": {"quest_type": "type"},
        "llm_fields": [
            "title", "description", "success_dialogue", "failure_dialogue", "reward.xp",
            "failure_penalty.hp_damage",
        ],
        "code_fields": [
            "id", "giver_npc_id", "room_id", "is_story_quest", "reward.item_id", "escort_npc_id",
            "target_zone", "destination_room", "target_items", "target_event_id",
            "target_monster_name", "is_complete", "is_failed",
        ],
        "user_fields": [
            "reward.money", "story_info", "door_reveal", "failure_penalty.stamina_damage",
            "time_gate",
        ],
        "hidden": ["is_complete", "is_failed"],
        "decorative": ["success_dialogue", "failure_dialogue"],
        "nesting": {
            "xp": "reward", "item_id": "reward", "money": "reward",
            "hp_damage": "failure_penalty", "stamina_damage": "failure_penalty",
        },
        "containers": ["reward", "failure_penalty", "target_items"],
        "protected": ["id", "profile_image"],
        "refs": {
            "giver_npc_id": "npc.id", "prerequisite_quest_id": "quest.id",
            "reward.item_id": "item.id", "escort_npc_id": "npc.id",
            "target_items[].item_id": "item.id", "target_event_id": "event.id",
        },
        "phase_label": "db:quest", "per_map": True, "count_key": "quest",
    },
    # P.1.5 (incl. the new `scene` type — a value of `type`, shared id space, P.9 S7)
    "event": {
        "label": "Events",
        "layout": {"mode": "collection", "path": "events/events.json", "format": "array"},
        "id_field": "id", "id_alloc": {"base": 3000}, "schema": "schemas/event.json",
        "renames": {"event_type": "type"},
        "llm_fields": [
            "name", "description", "money_drop", "loot_table", "failure_damage_type",
            "portrait_prompt",
        ],
        "code_fields": [
            "id", "difficulty", "x", "y", "room_level", "time_gate", "monster_ids", "is_gate",
            "is_climax_boss", "choices", "correct_tool", "correct_ability", "correct_spell",
            "failure_damage_range", "profile_image",
        ],
        "user_fields": ["reward_chance", "reward_item_id", "required_tools", "ability_text", "spell_text"],
        "decorative": ["monster_count"],
        "containers": ["loot_table", "choices", "monster_ids"],
        "protected": ["id", "profile_image"],
        "routed": {
            "x": "grid", "y": "grid",
            "title": "scene", "actors": "scene", "settings": "scene", "trigger": "scene",
            "once": "scene", "on_finish": "scene", "lines": "scene",
        },
        "refs": {
            "loot_table[].item_id": "item.id", "monster_ids[]": "monster.id",
            "reward_item_id": "item.id", "actors[].character_id": "npc.id",
        },
        "phase_label": "db:event", "per_map": True, "count_key": "event",
    },
    # P.1.6 — id_field `archetype` (P.9 S2); spell_pools.json is a container, not a tenth kind (S3)
    "class": {
        "label": "Classes",
        "layout": {"mode": "collection", "path": "classes/classes.json", "format": "array_positional"},
        "id_field": "archetype", "id_alloc": None, "schema": "schemas/class.json",
        "llm_fields": [
            "name", "description", "lore", "flavor_text", "role_tags", "category", "portrait_prompt",
            "abilities[].name", "abilities[].description", "spells[].name", "spells[].description",
        ],
        "code_fields": [
            "archetype", "starting_weapon", "stat_template", "stats", "stat_budget", "stat_roles",
            "environment", "starting_equipment", "portrait_path",
        ],
        "hidden": ["starting_equipment", "extra"],
        "decorative": [
            "description", "lore", "role_tags", "category", "stat_template", "stat_budget",
            "stat_roles",
        ],
        "containers": ["stats", "stat_template", "abilities", "ability_pool", "spells", "spell_pool"],
        "protected": ["archetype", "portrait_path"],
        "phase_label": "db:class", "per_map": False, "count_key": "class",
        "asset": {"field": "portrait_path", "kinds": ["image"], "targets": ["class:<archetype>"]},
    },
    # P.1.7 — index row; the grid file is `grids.room` (P.3.2), not a second layout
    "room": {
        "label": "Rooms",
        # P.1.7's write targets, as DATA on the layout (the P0-8 carry-over
        # fix): `rooms/rooms.json[id]` is the row, and the bible entry, the
        # manifest entry and the grid file's two copied fields are mirrors
        # written in the same batch, one journal event each (the P.7.3 mirror
        # pattern `world update` already uses). `row_source` marks the mirror
        # `db update` may resolve the row FROM when the index is absent: the
        # legacy trees (both demos, the reference fixture) predate
        # `rooms/rooms.json` and carry the bible copy only — a read-both shim,
        # never a migration, and this writer synthesizes no index (master §2).
        # `fields` bounds a partial mirror; without one a mirror is kept
        # consistent in the keys it ALREADY carries and never grown new ones.
        "layout": {
            "mode": "collection", "path": "rooms/rooms.json", "format": "keyed_object",
            "mirrors": [
                {"file": "world_bible.json", "path": "rooms", "format": "keyed_object",
                 "artifact": "world_bible", "row_source": True},
                {"file": "manifest.json", "path": "rooms", "format": "array",
                 "id_field": "room_id", "artifact": "manifest"},
                {"file": "rooms/{id}/maze.json", "format": "document",
                 "fields": ["environment", "environment_name"], "artifact": "room:{id}/grid"},
            ],
        },
        "id_field": "id", "id_alloc": None, "schema": "schemas/room.json",
        "llm_fields": ["environment_name", "story_beat", "boss_name", "boss_lore"],
        "code_fields": ["id", "maze_ref", "npcs", "items", "monsters", "encounters", "quests"],
        "user_fields": ["environment", "level"],
        "routed": {
            "grid": "grid", "door_position": "grid", "door_revealed": "grid",
            "gate_encounter_id": "grid", "player_start": "grid", "npc_positions": "grid",
            "event_positions": "grid", "item_placements": "grid", "quest_ids": "grid",
            "width": "grid", "height": "grid",
        },
        "protected": ["id", "maze_ref"],
        "refs": {"encounters[]": "event.id", "quests[]": "quest.id"},
        "phase_label": "layout:maze", "per_map": True, "count_key": "rooms",
    },
    # P.5.3 — net-new rows (P.1.8–9); `loader` seed-only, never stamped
    "music": {
        "label": "Music",
        "layout": {"mode": "collection", "path": "music/music.json", "format": "array"},
        "schema": "schemas/music.json", "id_field": "track_id",
        "llm_fields": ["title", "brief"],
        "code_fields": ["track_id", "binding", "file", "file_hash", "duration_measured_s"],
        "user_fields": ["tags", "notes", "loop_start_s", "loop_end_s"],
        "protected": [
            "track_id", "artifact_id", "file", "file_hash", "duration_measured_s",
            "provenance_hash", "parents", "status", "review_status", "library_ref",
        ],
        "containers": ["binding"],
        "asset": {"field": "file", "hash_field": "file_hash", "kinds": ["audio"], "targets": ["music:<track_id>"]},
        "vocab": {"binding_kinds": ["environment", "state", "screen"]},
        "phase_label": "audio:music", "count_key": None,
    },
    "sfx": {
        "label": "Sound effects",
        "layout": {"mode": "collection", "path": "sfx/sfx.json", "format": "array"},
        "schema": "schemas/sfx.json", "id_field": "sfx_id",
        "llm_fields": ["title", "brief"],
        "code_fields": ["sfx_id", "trigger", "file", "file_hash", "duration_measured_s"],
        "user_fields": ["tags", "notes"],
        "protected": [
            "sfx_id", "artifact_id", "file", "file_hash", "duration_measured_s",
            "provenance_hash", "parents", "status", "review_status", "library_ref",
        ],
        "containers": ["trigger"],
        "asset": {"field": "file", "hash_field": "file_hash", "kinds": ["audio"], "targets": ["sfx:<sfx_id>"]},
        "vocab": {"trigger_kinds": ["event", "environment"]},
        "phase_label": "audio:sfx", "count_key": None,
    },
}


def _generation_seed() -> dict[str, dict]:
    """Seed-only generation callables per kind, joined from the DatabaseSpecs
    the pipeline runs (``compose_mazeworld_specs``) — the generator is the one
    source for parser / prompt_method / prompt_kwargs."""
    out: dict[str, dict] = {}
    for spec in compose_mazeworld_specs():
        out[spec.entity_type] = {
            "parser": spec.parser,
            "prompt_method": spec.prompt_method,
            "prompt_kwargs": dict(spec.prompt_kwargs or {}),
        }
    return out


def _build_entities() -> dict[str, EntityKind]:
    gen = _generation_seed()
    entities: dict[str, EntityKind] = {}
    for kind, data in _ENTITY_DATA.items():
        entity = EntityKind(kind=kind, **data, **gen.get(kind, {}))
        # P.3.1 / §8.2 — the read-back inverse, bound to this kind's own
        # layout (seed-only; ``stamped()`` drops it). ``skeleton_view`` is the
        # rename inverse on top of it.
        entity.loader = partial(load_rows, entity=entity)
        entities[kind] = entity
    return entities


ENTITIES: dict[str, EntityKind] = _build_entities()

# ---------------------------------------------------------------------------
# P.3.2 — the room GridKind (+ the P.6 placements block)
# ---------------------------------------------------------------------------

ROOM_GRID = GridKind(
    kind="room",
    ref_field="maze_ref",
    path_template="rooms/{map_id}/maze.json",
    file="maze.json",
    steps={"grid": "maze.json"},
    dense=["grid"],
    sparse=[],
    placements={
        "npc_positions": {
            "kind": "npc", "wire": "entities", "shape": "dict", "id": "id",
            "grid_stamp": None, "journal_kind": "npc_move",
        },
        "event_positions": {
            "kind": "event", "wire": "triggers", "shape": "list", "id": "event_id",
            "grid_stamp": -1, "journal_kind": "event_move",
        },
        "item_placements": {
            "kind": "item", "wire": "items", "shape": "list", "id": "item_id",
            "grid_stamp": "id", "journal_kind": "item_move",
        },
    },
    points=["player_start", "door_position"],
    dims={"width_field": "width", "height_field": "height", "default": [40, 30]},
    # P.6.3 — the tile registry is template data beside this module (the
    # sibling of the platformer's ``tile_types.json``), resolved against
    # ``template_dir`` by the room reader.
    cell_vocab="tiles.json",
    derived=[],
    restorable=[],
    artifact_id="room:{map_id}/{step}",
)

# ---------------------------------------------------------------------------
# P.3.3 — DialogueSpec (selector model) + the P.2.4 evaluability seed
# ---------------------------------------------------------------------------

#: P.2.4 — what the dungeon ``pygame`` engine evaluates TODAY, per scope.
#: Explicit empties are the point: absent ≠ empty, and "absent = all
#: supported" is superseded (doctrine 10 — every gate amber until engine work).
PYGAME_EVALUABLE_NAMESPACES: dict[str, dict] = {
    "tree": {},
    "selector": {"quest": {"states": ["completed", "failed"]}},
    "scene": {},
    "effects": {},
    "music": {},
}

#: P.5.3 — binding / trigger kinds the engine honours (a sibling of the
#: namespaces block: binding kinds are not condition namespaces, P.9 C1).
PYGAME_EVALUABLE_BINDINGS: dict[str, list[str]] = {
    "music": ["environment", "state", "screen"],
    "sfx": ["event", "environment"],
}

#: P.3.3 — the core's ``default_dialogue()`` block (the one condition
#: grammar, §3.0-G; row P0-6 moved the data there so ``registry set`` can
#: seed it into a platformer-descended pack), extended with this engine's
#: evaluability seed and the tree model.
DIALOGUE = default_dialogue(
    engine_evaluable_seed={"pygame": PYGAME_EVALUABLE_NAMESPACES},
    tree_model=DialogueTree,
)

# ---------------------------------------------------------------------------
# P.4.3 worked entry 2 — the pygame engine seed (nothing launches it until W2.0)
# ---------------------------------------------------------------------------

PYGAME_ENGINE: dict = {
    "id": "pygame",
    "template": {"ref": "dungeon_engine", "version": None},
    "launch": {
        "cmd": "{python}",
        "args": ["-m", "mazeworld", "--data-dir", "{pack}"],
        "env": {
            "GAME_MODE": "offline_static",
            "LLM_BACKEND": "local",
            "MUSIC_BACKEND": "none",
            "IMAGE_BACKEND": "local",
        },
    },
    "live_channel": {"kind": "none", "protocol": None},
    "artifacts": [],
    "exports": [],
    "primary": True,
    "evaluable_namespaces": PYGAME_EVALUABLE_NAMESPACES,
    "evaluable_bindings": PYGAME_EVALUABLE_BINDINGS,
}

# ---------------------------------------------------------------------------
# P.7.1 dungeon rows — the `world update` field table
# ---------------------------------------------------------------------------


def _story_field(path: str) -> dict:
    return {
        "file": "world_bible.json",
        "path": f"story.{path}",
        "mirrors": [{"file": "story/story.json", "path": path}],
    }


def _beat_field(name: str) -> dict:
    return {
        "file": "world_bible.json",
        "path": f"story.beats[room_id=<room_id>].{name}",
        "mirrors": [{"file": "story/story.json", "path": f"beats[room_id=<room_id>].{name}"}],
    }


WORLD_FIELDS: dict[str, dict] = {
    "story.title": {
        "file": "world_bible.json",
        "path": "story.title",
        "mirrors": [
            {"file": "story/story.json", "path": "title"},
            {"file": "manifest.json", "path": "story_title"},
        ],
    },
    **{
        f"story.{name}": _story_field(name)
        for name in (
            "synopsis", "climax", "escalation_arc", "final_boss_name", "final_boss_lore",
            "key_npc_names",
        )
    },
    "story.faction.name": {
        "file": "world_bible.json",
        "path": "story.faction.name",
        "mirrors": [
            {"file": "story/story.json", "path": "faction.name"},
            {"file": "manifest.json", "path": "faction_name"},
        ],
    },
    **{
        f"story.faction.{name}": _story_field(f"faction.{name}")
        for name in ("description", "history", "leader", "aesthetic", "threat_level")
    },
    **{
        f"story.beats.<room_id>.{name}": _beat_field(name)
        for name in ("summary", "faction_presence", "escalation", "boss_name", "boss_lore")
    },
    **{
        f"narrative.{name}": {"file": "narrative.json", "path": name, "mirrors": []}
        for name in ("synopsis", "game_over", "victory", "room_intro_<room_id>")
    },
}

# ---------------------------------------------------------------------------
# P.3.4 — the envelope
# ---------------------------------------------------------------------------

#: ``compose_pipeline`` defaults: ``num_maps`` + ``MAZEWORLD_DEFAULT_COUNTS``.
DEFAULT_COUNTS: dict[str, int] = {"rooms": 3, **MAZEWORLD_DEFAULT_COUNTS}

#: P.9 R8 — the wizard's numeric bands, authored at P0-10 from today's
#: defaults (no ranges existed in code). ``rooms`` takes the design package's
#: room stepper band (1–24); the per-room entity counts get a band wide enough
#: to be useful and narrow enough that the $30/3-room anchor stays legible;
#: ``class`` is capped at the four archetypes ``MAZEWORLD_CLASS_LOADOUTS``
#: ships (a fifth would need a fifth loadout, not a wider band).
DEFAULT_RANGES: dict[str, list[int]] = {
    "rooms": [1, 24],
    "npc": [0, 8],
    "monster": [0, 8],
    "item": [0, 8],
    "event": [0, 8],
    "quest": [0, 8],
    "class": [1, len(MAZEWORLD_CLASS_LOADOUTS)],
}

#: §3.0-E — the phase-id → label map, keyed by the ids this pipeline's phases
#: emit (``run_pipeline`` logs ``phase:<Phase.name>``; the ``db:<kind>`` ids
#: come from ``DatabasePhase``). The dungeon gets labels from data exactly the
#: way the platformer does — master S5: no second hardcoded list anywhere.
PHASE_LABELS: dict[str, str] = {
    "story": "Story & world",
    "classes": "Player classes",
    "maze_layout": "Room layouts",
    "db:item": "Items",
    "db:monster": "Monsters",
    "db:npc": "NPCs",
    "db:event": "Encounters",
    "db:quest": "Quests",
    "mazeworld_dialogue": "Dialogue",
    "spell_pool": "Spells & abilities",
    "assets": "Portraits & audio",
    "narrative": "Narrative",
    "mazeworld_placement": "Placing entities",
    "validation": "Validation",
    "manifest": "Manifest",
}

#: Row P0-10 — how ``world new --template dungeon`` spawns this template's
#: runner (``PackSpec.runner``). No ``orchestrate`` key: the dungeon has one
#: linear pipeline, so the flag is reported as unsupported rather than
#: silently accepted (doctrine 4). No ``vlm`` backend either — animation
#: authoring is a platformer generator.
RUNNER: dict = {
    "module": "canon.packs.dungeon.run_world",
    "output": "--output-dir",
    "seed": "--seed",
    "model": "--model",
    "counts": {
        "rooms": "--num-maps",
        "npc": "--npcs",
        "monster": "--monsters",
        "item": "--items",
        "event": "--events",
        "quest": "--quests",
        "class": "--classes",
    },
    "backends": {
        "llm": "--backend",
        "image": "--image-backend",
        "music": "--music-backend",
        "sfx": "--sfx-backend",
    },
    "extra": [],
}

PACK_SPEC = PackSpec(
    pack_type=PACK_TYPE,
    label="Dungeon crawler",
    description="Rooms of encounters, NPCs and loot tables.",
    # W2.1.1 — the wizard does not speak a structure the manifest lacks: the
    # emitted tree has ROOMS, never floors, so no surface names one.
    vocab=["rooms", "encounters", "loot"],
    entities=ENTITIES,
    grids={"room": ROOM_GRID},
    dialogue=DIALOGUE,
    capabilities=["grid", "dialogue", "per_step_roll"],
    counts=dict(DEFAULT_COUNTS),
    # P.4.4 — the dungeon card ships un-badged (W2.1.4); ``engine`` is W2.4
    # axis data, not rendered in Phase 0; ``advanced`` is W2.1.1's split
    # (Rooms + NPCs/Monsters/Items primary, the rest under Advanced).
    # ``distribution`` is DERIVED from engines[*].exports by `pack templates`.
    wizard={
        "id": PACK_TYPE,
        "label": "Dungeon crawler",
        "description": "Rooms of encounters, NPCs and loot tables.",
        "vocab": ["rooms", "encounters", "loot"],
        "defaults": dict(DEFAULT_COUNTS),
        "ranges": dict(DEFAULT_RANGES),
        "advanced": ["event", "quest", "class"],
        "engine": ["pygame"],
        "dimension": "2D",
        "distribution": [],
        "beta": False,
        "phase_labels": dict(PHASE_LABELS),
    },
    engines=[PYGAME_ENGINE],
    tuning_vocabulary=None,
    world_fields=WORLD_FIELDS,
    phase_labels=dict(PHASE_LABELS),
    runner=dict(RUNNER),
    data_files={},
    compose=compose_pipeline,
    estimator=_ESTIMATOR,
    prompts=MazeworldPromptSet,
    validators=mazeworld_validators,
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
    template_dir=_TEMPLATE_DIR,
)

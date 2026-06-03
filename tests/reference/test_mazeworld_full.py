"""End-to-end shape verification test for the full mazeworld pipeline.

Runs ``compose_pipeline()`` against ``FakeLLMBackend`` (callable mode) and
verifies every output file exists at the expected path with the expected shape.

This is the v0.2 acceptance bar in test form.  A passing run means:
- Every phase completed without error.
- Every file was written at the correct path under tmp_path.
- Every file's top-level structure matches what mazeworld and cradle expect.
- Cross-reference invariants hold (quest giver IDs reference real NPCs, etc.).

Performance: the pipeline runs once per class via a class-scoped fixture.
Individual sub-tests reuse the fixture output to keep the suite fast.
Total wall time is typically <2 s (no network, all in-process).

Marked ``@pytest.mark.slow`` so tight CI loops can skip with ``-m "not slow"``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Make sure examples/ is importable when running from the repo root
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).parents[3]  # canon-ai/
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


# ---------------------------------------------------------------------------
# Callable-mode FakeLLMBackend responder
# ---------------------------------------------------------------------------


def _npc_response(n: int) -> str:
    """Minimal NPC JSON that parse_npc will accept."""
    return json.dumps({
        "name": f"NPC_{n}",
        "job": "wanderer",
        "hobby": "watching",
        "personality": "stoic and quiet",
        "backstory": f"A figure from room {n} with a troubled past.",
        "opening_greeting": "Greetings, traveler.",
        "portrait_prompt": f"a stoic figure standing guard, NPC {n}",
    })


def _item_response(n: int) -> str:
    """Minimal item JSON that parse_item will accept (potion variant)."""
    return json.dumps({
        "name": f"Elixir_{n}",
        "desc": f"A small vial of shimmering liquid, item {n}.",
        "item_stats": {
            "stamina_value": 5,
            "health_value": 10,
            "uses": 1,
            "price": 15,
        },
    })


def _monster_response(n: int) -> str:
    """Minimal monster JSON that parse_monster will accept."""
    return json.dumps({
        "name": f"Shadow_{n}",
        "species": "shadow creature",
        "description": f"A creature of darkness, monster {n}.",
        "backstory": "Born of the void.",
        "hp_range": [10, 25],
        "ac_range": [10, 14],
        "damage_type": "slashing",
        "physical_type": "beast",
        "elemental_affinity": "dark",
        "weakness": "light",
        "abilities": [
            {
                "name": "Shadow Claw",
                "effect_type": "damage",
                "damage_dice": "1d6",
                "chance": 0.8,
            }
        ],
        "portrait_prompt": f"a shadowy beast, monster {n}",
    })


def _event_response(n: int, event_type: str = "combat") -> str:
    """Minimal event JSON for either combat or puzzle type."""
    if event_type == "puzzle":
        return json.dumps({
            "name": f"Puzzle_{n}",
            "description": f"An ancient lock, event {n}.",
            "choices": [
                {
                    "text": "Force it open",
                    "stat_check": "STR",
                    "dc": 14,
                    "auto_success": False,
                    "success_text": "You break it open with brute force.",
                }
            ],
            "correct_tool": None,
            "failure_damage_type": "bludgeoning",
            "failure_damage_range": [1, 6],
        })
    return json.dumps({
        "name": f"Combat_{n}",
        "description": f"Enemies lurk ahead, event {n}.",
        "money_drop": [10, 30],
        "loot_table": [],
    })


def _quest_response(n: int) -> str:
    """Minimal quest JSON that parse_quest will accept."""
    return json.dumps({
        "title": f"Quest_{n}",
        "description": f"A task awaits, quest {n}.",
        "success_dialogue": "Well done.",
        "failure_dialogue": "You have failed.",
    })


def _archetype_response(archetype_id: str, name: str) -> str:
    """Minimal class archetype JSON that ClassPhase will accept."""
    return json.dumps({
        "archetype_id": archetype_id,
        "archetype": archetype_id,
        "name": name,
        "description": f"A {name} archetype.",
        "flavor_text": f"{name}s are known for their prowess.",
        "starting_weapon": f"{name} Blade",
        "portrait_prompt": f"a {name.lower()} character portrait",
        "category": archetype_id,
        "stat_template": {
            "STR": 12, "DEX": 12, "CON": 12,
            "INT": 12, "WIS": 12, "CHA": 12, "LUCK": 10,
        },
        "stat_roles": {
            "primary": ["STR"],
            "secondary": ["DEX"],
            "dump": ["INT"],
        },
        "role_tags": [archetype_id],
        "abilities": [
            {
                "name": f"{name} Strike",
                "description": f"A powerful {name.lower()} attack.",
                "stat": "STR",
                "stamina_cost": 4,
            }
        ],
        "spells": [],
        "ability_pool": [
            {
                "name": f"{name} Reserve",
                "description": f"A secondary {name.lower()} ability.",
                "stat": "CON",
                "stamina_cost": 2,
            }
        ],
        "spell_pool": [],
        "lore": f"{name}s have walked this world for centuries.",
    })


def _dialogue_response() -> str:
    """Minimal dialogue tree JSON."""
    return json.dumps({
        "entry_node_id": "start",
        "nodes": {
            "start": {
                "prompt": "Greetings, traveler. What brings you here?",
                "choices": [
                    {"text": "Just passing through.", "next_node_id": "farewell"},
                    {"text": "Tell me about this place.", "next_node_id": "info"},
                ],
            },
            "info": {
                "prompt": "This place holds many secrets. Tread carefully.",
                "choices": [
                    {"text": "Thank you.", "next_node_id": "farewell"},
                ],
            },
            "farewell": {
                "prompt": "Safe travels.",
                "choices": [],
            },
        },
    })


def _story_response() -> str:
    """Story JSON that StoryPhase will accept."""
    return json.dumps({
        "title": "Test World",
        "synopsis": "A world of shadow and ruin where ancient powers stir.",
        "factions": [
            {
                "faction_id": "shadow_order",
                "name": "The Shadow Order",
                "description": "Ancient cult seeking dominion.",
                "history": "Sealed for a thousand years.",
                "leader": "The Whisper",
                "threat_level": 5,
            }
        ],
        "primary_antagonist_faction_id": "shadow_order",
        "escalation_arc": ["arrival", "investigation", "confrontation", "climax"],
        "climax": "The final battle with The Whisper.",
        "final_entity_id": "boss_whisper",
        "final_entity_lore": "The Whisper commands the void.",
        "beats": [
            {
                "map_id": "room_0",
                "beat": "The hero arrives at the ruined city.",
                "boss_name": "Cult Watchman",
                "boss_lore": "A scout for the Order.",
            },
            {
                "map_id": "room_1",
                "beat": "Descent into the wasteland.",
                "boss_name": "Wasteland Brute",
                "boss_lore": "A corrupted guardian.",
            },
            {
                "map_id": "room_2",
                "beat": "Final confrontation in the city.",
                "boss_name": "The Whisper",
                "boss_lore": "The prime enemy.",
            },
        ],
        "key_character_names": ["Hero", "Innkeeper Marta", "Captain Ren"],
    })


def _character_response(n: int) -> str:
    """Character JSON that CharacterPhase will accept."""
    return json.dumps({
        "name": f"Character_{n}",
        "role": "npc",
        "lore": f"Character {n} has a long and storied history.",
        "personality": "reserved and watchful",
        "appearance": f"A weathered figure with grey eyes, character {n}.",
        "job": "guard",
        "hobby": "carving",
        "opening_greeting": "Can I help you?",
        "portrait_prompt": f"a weathered guard character, {n}",
    })


def make_responder(num_maps: int = 3) -> Any:
    """Return a callable for FakeLLMBackend that produces shape-correct JSON
    for whatever entity type the request is asking about.

    Uses counters captured in closure to distinguish sequential calls of
    the same type.  Inspects user_message keywords to route to the right
    shape — robust against prompt changes as long as the keywords remain.
    """
    # Per-type counters for disambiguation
    counters: dict[str, int] = {
        "npc": 0,
        "item": 0,
        "monster": 0,
        "event": 0,
        "quest": 0,
        "character": 0,
    }

    def respond(request: Any) -> str:  # noqa: PLR0911 (branchy by necessity)
        msg = request.user_message.lower()

        # ------------------------------------------------------------------
        # Story phase  — matches "world seed"
        # ------------------------------------------------------------------
        if "world seed" in msg:
            return _story_response()

        # ------------------------------------------------------------------
        # Class archetype  — matches "class or archetype"
        # ------------------------------------------------------------------
        if "class or archetype" in msg or "archetype" in msg and "class" in msg:
            # Cycle through the four archetypes
            _arch_ids = ["warrior", "mage", "healer", "jester"]
            _arch_names = ["Warrior", "Mage", "Healer", "Jester"]
            idx = counters.get("_class_idx", 0)
            counters["_class_idx"] = idx + 1
            return _archetype_response(_arch_ids[idx % 4], _arch_names[idx % 4])

        # ------------------------------------------------------------------
        # NPC  — matches the npc_generation prompt keywords
        # ------------------------------------------------------------------
        if "generate a character with role" in msg and (
            "job" in msg or "opening_greeting" in msg or "hobby" in msg
        ):
            n = counters["npc"]
            counters["npc"] += 1
            return _npc_response(n)

        # ------------------------------------------------------------------
        # Character phase  — character_generation prompt (has "lore" expected field)
        # ------------------------------------------------------------------
        if "generate a character" in msg and "lore" in msg and "personality" in msg:
            n = counters["character"]
            counters["character"] += 1
            return _character_response(n)

        # ------------------------------------------------------------------
        # Monster  — monster_generation prompt
        # ------------------------------------------------------------------
        if "adversarial creature" in msg or (
            "creature" in msg and "challenge level" in msg
        ):
            n = counters["monster"]
            counters["monster"] += 1
            # Detect event_type from skeleton in message for combat vs puzzle
            return _monster_response(n)

        # ------------------------------------------------------------------
        # Event  — event_generation prompt. Checked BEFORE Item: event prompts
        # contain "item"/"generate"/"skeleton" (from the loot_table shape and
        # instruction) and would otherwise be served as items. "money_drop" is
        # unique to the event JSON shape, so it's a reliable, context-immune
        # marker; "stat_check" distinguishes puzzle/skill (needs choices) from
        # combat.
        # ------------------------------------------------------------------
        if "money_drop" in msg:
            n = counters["event"]
            counters["event"] += 1
            event_type = "puzzle" if "stat_check" in msg else "combat"
            return _event_response(n, event_type)

        # ------------------------------------------------------------------
        # Item  — item_generation prompt
        # ------------------------------------------------------------------
        if (
            "item" in msg
            and ("generate" in msg or "design" in msg)
            and "skeleton" in msg
            and "stamina" not in msg  # disambiguate from ability
        ):
            n = counters["item"]
            counters["item"] += 1
            return _item_response(n)


        # ------------------------------------------------------------------
        # Quest  — quest_generation prompt
        # ------------------------------------------------------------------
        if "quest" in msg and ("generate" in msg or "create" in msg) and "skeleton" in msg:
            n = counters["quest"]
            counters["quest"] += 1
            return _quest_response(n)

        # ------------------------------------------------------------------
        # Dialogue  — dialogue_tree_generation prompt
        # ------------------------------------------------------------------
        if "dialogue tree" in msg or (
            "dialogue" in msg and "nodes" in msg
        ):
            return _dialogue_response()

        # ------------------------------------------------------------------
        # Narrative — synopsis, map intro, victory, defeat
        # ------------------------------------------------------------------
        if "polished narrative synopsis" in msg or (
            "synopsis" in msg and "write" in msg
        ):
            return json.dumps({"synopsis": "The world holds its breath as shadow and ruin converge."})

        if "introductory text" in msg or "intro_text" in msg or (
            "entering this location" in msg
        ):
            return json.dumps({"intro_text": "You step into a world of shadows and ancient stone."})

        if "victory message" in msg or "victory_text" in msg:
            return json.dumps({"victory_text": "You have triumphed over the darkness."})

        if "defeat message" in msg or "defeat_text" in msg:
            return json.dumps({"defeat_text": "The shadows consume you. The world falls silent."})

        # ------------------------------------------------------------------
        # Fallback — return generic JSON so nothing crashes
        # ------------------------------------------------------------------
        return json.dumps({"name": "Unnamed Entity", "lore": "A mysterious entity."})

    return respond


# ---------------------------------------------------------------------------
# Class-scoped fixture: run the pipeline exactly once for all sub-tests
# ---------------------------------------------------------------------------


@pytest.fixture(scope="class")
def pipeline_run(tmp_path_factory):
    """Run the full mazeworld pipeline once; return (tmp_path, ctx, bible)."""
    from canon import FakeLLMBackend, LLMClient
    from canon.pipeline.runner import run_pipeline
    from examples.mazeworld_pack.compose import compose_pipeline

    tmp = tmp_path_factory.mktemp("mazeworld_full")

    phases, ctx = compose_pipeline(seed="test", num_maps=3, output_dir=tmp)

    backend = FakeLLMBackend(make_responder(num_maps=3))
    ctx.llm = LLMClient(backend, stats=ctx.stats)

    # Asset backends wired but skip_* flags are already True in compose.py
    # so no real assets are generated — the phase still runs and stamps metadata.

    bible = run_pipeline(phases, ctx)

    return {"tmp": tmp, "ctx": ctx, "bible": bible}


# ---------------------------------------------------------------------------
# Helpers for loading JSON files produced by the pipeline
# ---------------------------------------------------------------------------


def _load(path: Path) -> Any:
    """Load a JSON file; raises AssertionError with path on failure."""
    assert path.exists(), f"Expected output file not found: {path}"
    return json.loads(path.read_text())


# ---------------------------------------------------------------------------
# TestMazeworldFullPipeline
# ---------------------------------------------------------------------------


@pytest.mark.slow
class TestMazeworldFullPipeline:
    """End-to-end shape verification for the full mazeworld pipeline.

    The heavy fixture (``pipeline_run``) runs once for the whole class.
    Each test method verifies one output file's structure independently.
    """

    # ------------------------------------------------------------------
    # Pipeline-level assertions
    # ------------------------------------------------------------------

    def test_pipeline_completes_and_returns_bible(self, pipeline_run):
        bible = pipeline_run["bible"]
        assert bible is not None, "run_pipeline() returned None"
        assert hasattr(bible, "story"), "Bible missing .story"

    def test_phases_run_recorded(self, pipeline_run):
        bible = pipeline_run["bible"]
        phases_run = list(bible.metadata.phases_run)
        # Required phase names. NPCs come from a single source —
        # DatabasePhase(npc) — so there is no separate "characters" phase;
        # dialogue + placement are mazeworld-pack phases.
        required = {
            "story",
            "classes",
            "maze_layout",
            "db:npc",
            "db:item",
            "db:monster",
            "db:event",
            "db:quest",
            "mazeworld_dialogue",
            "spell_pool",
            "assets",
            "narrative",
            "mazeworld_placement",
            "validation",
            "manifest",
        }
        missing = required - set(phases_run)
        assert not missing, f"Missing phases in metadata.phases_run: {missing}"

    # ------------------------------------------------------------------
    # File existence
    # ------------------------------------------------------------------

    def test_expected_files_exist(self, pipeline_run):
        tmp = pipeline_run["tmp"]
        expected = [
            "world_bible.json",
            "manifest.json",
            "narrative.json",
            "generation_stats.json",
            "story/story.json",
            "classes/classes.json",
            "classes/spell_pools.json",
            "npcs/npcs.json",
            "items/items.json",
            "monsters/monsters.json",
            "events/events.json",
            "quests/quests.json",
            "rooms/room_0/maze.json",
            "rooms/room_1/maze.json",
            "rooms/room_2/maze.json",
        ]
        missing = [f for f in expected if not (tmp / f).exists()]
        assert not missing, f"Missing output files: {missing}"

    # ------------------------------------------------------------------
    # world_bible.json
    # ------------------------------------------------------------------

    def test_world_bible_shape(self, pipeline_run):
        tmp = pipeline_run["tmp"]
        wb = _load(tmp / "world_bible.json")

        assert isinstance(wb, dict), "world_bible.json must be a JSON object"
        assert "story" in wb, "world_bible.json missing 'story' key"
        assert "rooms" in wb, "world_bible.json missing 'rooms' key"
        assert isinstance(wb["rooms"], dict), "'rooms' must be a dict"

    def test_world_bible_rooms_have_required_keys(self, pipeline_run):
        tmp = pipeline_run["tmp"]
        wb = _load(tmp / "world_bible.json")

        # MazeworldManifestPhase renames "events" → "encounters" to match
        # mazeworld's loader convention.  Accept either key so the test remains
        # valid whether the pipeline uses the base or mazeworld subclass.
        for room_id, room_data in wb["rooms"].items():
            required_room_keys = {
                "environment", "environment_name", "level", "story_beat",
                "npcs", "items", "monsters", "quests",
            }
            # Accept either "events" (base ManifestPhase) or "encounters" (mazeworld)
            has_events = "events" in room_data or "encounters" in room_data
            missing = required_room_keys - set(room_data.keys())
            assert not missing, (
                f"world_bible.json room '{room_id}' missing keys: {missing}"
            )
            assert has_events, (
                f"world_bible.json room '{room_id}' missing 'events' or 'encounters' key"
            )

    def test_world_bible_matches_mazeworld_roombible_shape(self, pipeline_run):
        """world_bible.json must satisfy mazeworld's WorldBible/RoomBible model:
        encounters/quests are list[str] of ids; npcs/items/monsters and
        player_classes are EntityLore stubs carrying a required entity_type.
        (Regression: the pack writer used to emit dict stubs / full archetype
        dumps, failing WorldBible.model_validate with 7 errors.)
        """
        tmp = pipeline_run["tmp"]
        wb = _load(tmp / "world_bible.json")

        for room_id, room in wb["rooms"].items():
            for key in ("encounters", "quests"):
                vals = room.get(key, [])
                assert all(isinstance(v, str) for v in vals), (
                    f"room {room_id} {key} must be list[str] of ids, got {vals!r}"
                )
            for key in ("npcs", "items", "monsters"):
                for stub in room.get(key, []):
                    assert stub.get("entity_type"), (
                        f"room {room_id} {key} stub missing entity_type: {stub!r}"
                    )

        for pc in wb.get("player_classes", []):
            assert pc.get("entity_type") == "player_class", (
                f"player_class stub missing/!=player_class entity_type: {pc!r}"
            )

    def test_world_bible_has_all_three_rooms(self, pipeline_run):
        tmp = pipeline_run["tmp"]
        wb = _load(tmp / "world_bible.json")
        assert "room_0" in wb["rooms"], "world_bible.json missing room_0"
        assert "room_1" in wb["rooms"], "world_bible.json missing room_1"
        assert "room_2" in wb["rooms"], "world_bible.json missing room_2"

    # ------------------------------------------------------------------
    # manifest.json
    # ------------------------------------------------------------------

    def test_manifest_shape(self, pipeline_run):
        tmp = pipeline_run["tmp"]
        mf = _load(tmp / "manifest.json")

        required_keys = {
            "seed", "num_rooms", "environments", "environment_names",
            "npc_count", "quest_count", "event_count", "class_count",
            "rooms", "validation_report", "generation_stats", "music", "sfx",
        }
        missing = required_keys - set(mf.keys())
        assert not missing, f"manifest.json missing keys: {missing}"

    def test_manifest_num_rooms_matches_rooms_length(self, pipeline_run):
        tmp = pipeline_run["tmp"]
        mf = _load(tmp / "manifest.json")
        assert mf["num_rooms"] == 3, f"Expected num_rooms=3, got {mf['num_rooms']}"
        assert len(mf["rooms"]) == mf["num_rooms"], (
            f"manifest rooms list length ({len(mf['rooms'])}) != num_rooms ({mf['num_rooms']})"
        )

    def test_manifest_seed_is_test(self, pipeline_run):
        tmp = pipeline_run["tmp"]
        mf = _load(tmp / "manifest.json")
        # MazeworldManifestPhase coerces the seed string to an int (MD5-derived)
        # so that mazeworld's random.seed() call receives the type it expects.
        # Accept either: the raw string "test" (base ManifestPhase) or a stable
        # int derived from "test" (MazeworldManifestPhase).
        seed_val = mf["seed"]
        assert seed_val == "test" or isinstance(seed_val, int), (
            f"Expected seed='test' or an int derived from 'test', got {seed_val!r}"
        )

    # ------------------------------------------------------------------
    # narrative.json
    # ------------------------------------------------------------------

    def test_narrative_shape(self, pipeline_run):
        tmp = pipeline_run["tmp"]
        narr = _load(tmp / "narrative.json")

        assert isinstance(narr, dict), "narrative.json must be a JSON object"
        for key in ("synopsis", "game_over", "victory"):
            assert key in narr, f"narrative.json missing required key '{key}'"
            assert isinstance(narr[key], str), f"narrative.json['{key}'] must be a string"

        for i in range(3):
            key = f"room_intro_room_{i}"
            assert key in narr, f"narrative.json missing key '{key}'"
            assert isinstance(narr[key], str), f"narrative.json['{key}'] must be a string"

    # ------------------------------------------------------------------
    # story/story.json
    # ------------------------------------------------------------------

    def test_story_json_shape(self, pipeline_run):
        tmp = pipeline_run["tmp"]
        story = _load(tmp / "story/story.json")

        assert isinstance(story, dict), "story/story.json must be a JSON object"
        for key in ("title", "synopsis", "seed"):
            assert key in story, f"story/story.json missing key '{key}'"

    # ------------------------------------------------------------------
    # npcs/npcs.json
    # ------------------------------------------------------------------

    def test_npcs_json_is_array(self, pipeline_run):
        tmp = pipeline_run["tmp"]
        npcs = _load(tmp / "npcs/npcs.json")
        assert isinstance(npcs, list), "npcs/npcs.json must be a JSON array"

    def test_npcs_json_count(self, pipeline_run):
        tmp = pipeline_run["tmp"]
        npcs = _load(tmp / "npcs/npcs.json")
        # 2 npcs × 3 maps = 6
        assert len(npcs) == 6, f"Expected 6 NPCs (2×3 maps), got {len(npcs)}"

    def test_npcs_json_entry_shape(self, pipeline_run):
        tmp = pipeline_run["tmp"]
        npcs = _load(tmp / "npcs/npcs.json")
        required_npc_keys = {
            "id", "type", "name", "job", "hobby", "personality",
            "backstory", "opening_greeting", "portrait_prompt",
            "dialogue_tree", "quest_id", "x", "y", "color", "selected",
        }
        for i, npc in enumerate(npcs):
            missing = required_npc_keys - set(npc.keys())
            assert not missing, (
                f"npcs.json entry #{i} missing keys: {missing}"
            )
            assert isinstance(npc["id"], int), f"NPC #{i} 'id' must be int, got {type(npc['id'])}"
            assert isinstance(npc["color"], list), f"NPC #{i} 'color' must be list"
            assert len(npc["color"]) == 3, f"NPC #{i} 'color' must be [R, G, B]"

    def test_npc_ids_start_from_1000(self, pipeline_run):
        tmp = pipeline_run["tmp"]
        npcs = _load(tmp / "npcs/npcs.json")
        for npc in npcs:
            assert npc["id"] >= 1000, (
                f"NPC id {npc['id']} < 1000 (expected IDAllocator base)"
            )

    # ------------------------------------------------------------------
    # items/items.json
    # ------------------------------------------------------------------

    def test_items_json_is_keyed_object(self, pipeline_run):
        tmp = pipeline_run["tmp"]
        items = _load(tmp / "items/items.json")
        assert isinstance(items, dict), "items/items.json must be a JSON object (keyed)"

    def test_items_json_count(self, pipeline_run):
        tmp = pipeline_run["tmp"]
        items = _load(tmp / "items/items.json")
        # 3 items × 3 maps = 9
        assert len(items) == 9, f"Expected 9 items (3×3 maps), got {len(items)}"

    def test_items_json_keys_are_numeric_strings_from_2000(self, pipeline_run):
        tmp = pipeline_run["tmp"]
        items = _load(tmp / "items/items.json")
        for key in items:
            assert key.isdigit(), f"item key {key!r} is not a numeric string"
            assert int(key) >= 2000, f"item key {key} < 2000 (expected IDAllocator base)"

    def test_items_json_entry_shape(self, pipeline_run):
        tmp = pipeline_run["tmp"]
        items = _load(tmp / "items/items.json")
        for key, item in items.items():
            assert "category" in item, f"items[{key!r}] missing 'category'"
            assert "name" in item, f"items[{key!r}] missing 'name'"
            assert "desc" in item, f"items[{key!r}] missing 'desc'"
            assert "item_stats" in item, f"items[{key!r}] missing 'item_stats'"

    # ------------------------------------------------------------------
    # monsters/monsters.json
    # ------------------------------------------------------------------

    def test_monsters_json_is_keyed_object(self, pipeline_run):
        tmp = pipeline_run["tmp"]
        monsters = _load(tmp / "monsters/monsters.json")
        assert isinstance(monsters, dict), "monsters/monsters.json must be a JSON object"

    def test_monsters_json_count(self, pipeline_run):
        tmp = pipeline_run["tmp"]
        monsters = _load(tmp / "monsters/monsters.json")
        # 2 monsters × 3 maps = 6
        assert len(monsters) == 6, f"Expected 6 monsters (2×3 maps), got {len(monsters)}"

    def test_monsters_json_entry_shape(self, pipeline_run):
        tmp = pipeline_run["tmp"]
        monsters = _load(tmp / "monsters/monsters.json")
        for key, monster in monsters.items():
            assert "id" in monster, f"monsters[{key!r}] missing 'id'"
            assert "name" in monster, f"monsters[{key!r}] missing 'name'"
            assert "hp_range" in monster, f"monsters[{key!r}] missing 'hp_range'"
            assert "ac_range" in monster, f"monsters[{key!r}] missing 'ac_range'"
            assert "abilities" in monster, f"monsters[{key!r}] missing 'abilities'"
            hp = monster["hp_range"]
            ac = monster["ac_range"]
            assert isinstance(hp, list) and len(hp) == 2, (
                f"monsters[{key!r}] hp_range must be [min, max]"
            )
            assert isinstance(ac, list) and len(ac) == 2, (
                f"monsters[{key!r}] ac_range must be [min, max]"
            )

    # ------------------------------------------------------------------
    # events/events.json
    # ------------------------------------------------------------------

    def test_events_json_is_array(self, pipeline_run):
        tmp = pipeline_run["tmp"]
        events = _load(tmp / "events/events.json")
        assert isinstance(events, list), "events/events.json must be a JSON array"

    def test_events_json_count(self, pipeline_run):
        tmp = pipeline_run["tmp"]
        events = _load(tmp / "events/events.json")
        # 4 events × 3 maps = 12
        assert len(events) == 12, f"Expected 12 events (4×3 maps), got {len(events)}"

    def test_events_json_entry_type_field(self, pipeline_run):
        tmp = pipeline_run["tmp"]
        events = _load(tmp / "events/events.json")
        for i, evt in enumerate(events):
            assert "type" in evt, f"events[{i}] missing 'type'"
            assert evt["type"] in ("combat", "puzzle", "event"), (
                f"events[{i}] type {evt['type']!r} not in (combat, puzzle, event)"
            )

    def test_events_json_combat_has_monster_count(self, pipeline_run):
        tmp = pipeline_run["tmp"]
        events = _load(tmp / "events/events.json")
        combat_events = [e for e in events if e.get("type") == "combat"]
        for evt in combat_events:
            assert "monster_count" in evt, (
                f"combat event {evt.get('id')} missing 'monster_count'"
            )

    def test_events_json_interactive_events_are_solvable(self, pipeline_run):
        """Puzzle/skill events must carry NON-EMPTY, well-formed choices.

        A puzzle/event with an empty ``choices`` list is unsolvable in-game,
        which is the bug this guards against (the prompt must be framed for the
        rolled event type so the LLM returns real choices). Asserting the key
        merely *exists* would pass even when choices is ``[]`` — so we check
        substance: at least one choice, each with text + a resolution path
        (stat_check or auto_success), plus an ability/spell solution hook.
        """
        tmp = pipeline_run["tmp"]
        events = _load(tmp / "events/events.json")
        interactive = [e for e in events if e.get("type") in ("puzzle", "event")]
        # The reference world generates a mix; there should be at least one.
        assert interactive, "expected at least one puzzle/event-type event"
        for evt in interactive:
            choices = evt.get("choices")
            assert choices, (
                f"{evt['type']} event {evt.get('id')} has empty/missing choices "
                "— it would be unsolvable in-game"
            )
            for ch in choices:
                assert ch.get("text"), f"choice in event {evt.get('id')} missing text"
            # Every puzzle MUST be completable: a guaranteed walk-away
            # auto-success path, regardless of the player's class/inventory.
            assert any(c.get("auto_success") for c in choices), (
                f"{evt['type']} event {evt.get('id')} has no walk-away — unsolvable"
            )
            # Ability/spell solution hooks present (real generated names or null).
            assert "correct_ability" in evt and "correct_spell" in evt, (
                f"{evt['type']} event {evt.get('id')} missing ability/spell solution keys"
            )

    # ------------------------------------------------------------------
    # quests/quests.json
    # ------------------------------------------------------------------

    def test_quests_json_is_array(self, pipeline_run):
        tmp = pipeline_run["tmp"]
        quests = _load(tmp / "quests/quests.json")
        assert isinstance(quests, list), "quests/quests.json must be a JSON array"

    def test_quests_json_count(self, pipeline_run):
        tmp = pipeline_run["tmp"]
        quests = _load(tmp / "quests/quests.json")
        # 2 quests × 3 maps = 6
        assert len(quests) == 6, f"Expected 6 quests (2×3 maps), got {len(quests)}"

    def test_quests_json_entry_shape(self, pipeline_run):
        tmp = pipeline_run["tmp"]
        quests = _load(tmp / "quests/quests.json")
        required_quest_keys = {"id", "type", "title", "reward", "failure_penalty"}
        for i, quest in enumerate(quests):
            missing = required_quest_keys - set(quest.keys())
            assert not missing, f"quests[{i}] missing keys: {missing}"
            assert isinstance(quest["id"], int), f"quests[{i}] 'id' must be int"
            assert isinstance(quest["reward"], dict), f"quests[{i}] 'reward' must be dict"
            assert isinstance(quest["failure_penalty"], dict), (
                f"quests[{i}] 'failure_penalty' must be dict"
            )

    def test_quest_ids_start_from_4000(self, pipeline_run):
        tmp = pipeline_run["tmp"]
        quests = _load(tmp / "quests/quests.json")
        for q in quests:
            assert q["id"] >= 4000, (
                f"Quest id {q['id']} < 4000 (expected IDAllocator base)"
            )

    # ------------------------------------------------------------------
    # classes/classes.json
    # ------------------------------------------------------------------

    def test_classes_json_is_array_of_four(self, pipeline_run):
        tmp = pipeline_run["tmp"]
        classes = _load(tmp / "classes/classes.json")
        assert isinstance(classes, list), "classes/classes.json must be a JSON array"
        assert len(classes) == 4, (
            f"Expected 4 classes (warrior/mage/healer/jester), got {len(classes)}"
        )

    def test_classes_json_entry_shape(self, pipeline_run):
        tmp = pipeline_run["tmp"]
        classes = _load(tmp / "classes/classes.json")
        required_class_keys = {"name", "abilities", "spells"}
        for i, cls in enumerate(classes):
            missing = required_class_keys - set(cls.keys())
            assert not missing, f"classes[{i}] missing keys: {missing}"
            assert isinstance(cls["abilities"], list), (
                f"classes[{i}] 'abilities' must be a list"
            )
            assert isinstance(cls["spells"], list), (
                f"classes[{i}] 'spells' must be a list"
            )

    def test_classes_json_has_stat_template(self, pipeline_run):
        tmp = pipeline_run["tmp"]
        classes = _load(tmp / "classes/classes.json")
        for i, cls in enumerate(classes):
            assert "stat_template" in cls, f"classes[{i}] missing 'stat_template'"
            assert isinstance(cls["stat_template"], dict), (
                f"classes[{i}] 'stat_template' must be a dict"
            )

    # ------------------------------------------------------------------
    # classes/spell_pools.json
    # ------------------------------------------------------------------

    def test_spell_pools_json_is_object(self, pipeline_run):
        tmp = pipeline_run["tmp"]
        sp = _load(tmp / "classes/spell_pools.json")
        assert isinstance(sp, dict), "classes/spell_pools.json must be a JSON object"

    # ------------------------------------------------------------------
    # rooms/room_*/maze.json
    # ------------------------------------------------------------------

    def test_maze_json_has_required_keys(self, pipeline_run):
        tmp = pipeline_run["tmp"]
        required_maze_keys = {"grid", "width", "height", "door_position", "player_start"}
        for i in range(3):
            maze = _load(tmp / f"rooms/room_{i}/maze.json")
            missing = required_maze_keys - set(maze.keys())
            assert not missing, f"rooms/room_{i}/maze.json missing keys: {missing}"

    def test_flat_rooms_index_for_cradle(self, pipeline_run):
        """A flat rooms/rooms.json keyed index exists for Cradle's 'rooms' tab,
        alongside (not replacing) the per-room rooms/<id>/maze.json that
        MazeWorld's game loader reads. Each entry is keyed by room id and carries
        an id + maze_ref back to its maze file.
        """
        tmp = pipeline_run["tmp"]
        index = _load(tmp / "rooms" / "rooms.json")
        assert isinstance(index, dict) and index, "rooms/rooms.json must be a non-empty object"
        for room_id, room in index.items():
            assert room.get("id") == room_id
            assert room.get("maze_ref") == f"rooms/{room_id}/maze.json"
            # the maze file it points at must still exist (MazeWorld reads it)
            assert (tmp / f"rooms/{room_id}/maze.json").exists()

    def test_maze_json_grid_is_2d(self, pipeline_run):
        tmp = pipeline_run["tmp"]
        for i in range(3):
            maze = _load(tmp / f"rooms/room_{i}/maze.json")
            grid = maze["grid"]
            assert isinstance(grid, list), f"room_{i}/maze.json 'grid' must be a list"
            assert len(grid) > 0, f"room_{i}/maze.json 'grid' must be non-empty"
            assert isinstance(grid[0], list), f"room_{i}/maze.json 'grid' rows must be lists"

    def test_maze_json_dimensions_match(self, pipeline_run):
        tmp = pipeline_run["tmp"]
        for i in range(3):
            maze = _load(tmp / f"rooms/room_{i}/maze.json")
            assert maze["width"] == 40, f"room_{i}/maze.json width must be 40"
            assert maze["height"] == 30, f"room_{i}/maze.json height must be 30"
            assert len(maze["grid"]) == maze["height"], (
                f"room_{i}/maze.json grid row count ({len(maze['grid'])}) != height ({maze['height']})"
            )
            assert len(maze["grid"][0]) == maze["width"], (
                f"room_{i}/maze.json grid col count != width"
            )

    # ------------------------------------------------------------------
    # generation_stats.json
    # ------------------------------------------------------------------

    def test_generation_stats_json_is_object(self, pipeline_run):
        tmp = pipeline_run["tmp"]
        stats = _load(tmp / "generation_stats.json")
        assert isinstance(stats, dict), "generation_stats.json must be a JSON object"

    def test_generation_stats_has_llm_calls(self, pipeline_run):
        tmp = pipeline_run["tmp"]
        stats = _load(tmp / "generation_stats.json")
        assert "llm_calls" in stats, "generation_stats.json missing 'llm_calls'"
        assert isinstance(stats["llm_calls"], int), "'llm_calls' must be int"
        assert stats["llm_calls"] > 0, "Expected > 0 LLM calls after full pipeline"

    # ------------------------------------------------------------------
    # Cross-reference sanity
    # ------------------------------------------------------------------

    def test_manifest_rooms_count_matches_num_rooms(self, pipeline_run):
        tmp = pipeline_run["tmp"]
        mf = _load(tmp / "manifest.json")
        assert len(mf["rooms"]) == mf["num_rooms"], (
            "manifest.json 'rooms' list length must equal 'num_rooms'"
        )

    def test_npc_ids_are_unique(self, pipeline_run):
        tmp = pipeline_run["tmp"]
        npcs = _load(tmp / "npcs/npcs.json")
        ids = [n["id"] for n in npcs]
        assert len(ids) == len(set(ids)), (
            f"NPC IDs are not unique: duplicates={[x for x in ids if ids.count(x) > 1]}"
        )

    def test_quest_ids_are_unique(self, pipeline_run):
        tmp = pipeline_run["tmp"]
        quests = _load(tmp / "quests/quests.json")
        ids = [q["id"] for q in quests]
        assert len(ids) == len(set(ids)), (
            f"Quest IDs are not unique: duplicates={[x for x in ids if ids.count(x) > 1]}"
        )

    # ------------------------------------------------------------------
    # Determinism: same seed → same manifest seed value
    # ------------------------------------------------------------------

    def test_manifest_seed_is_deterministic(self, pipeline_run, tmp_path):
        """Running compose_pipeline with the same seed produces the same seed in manifest."""
        from canon import FakeLLMBackend, LLMClient
        from canon.pipeline.runner import run_pipeline
        from examples.mazeworld_pack.compose import compose_pipeline

        phases, ctx = compose_pipeline(seed="test", num_maps=3, output_dir=tmp_path)
        ctx.llm = LLMClient(FakeLLMBackend(make_responder(num_maps=3)), stats=ctx.stats)
        run_pipeline(phases, ctx)

        mf = _load(tmp_path / "manifest.json")
        seed_val = mf["seed"]
        # The seed should be the same type every run; accept either string or int
        # (MazeworldManifestPhase coerces to int, base phase emits string).
        assert seed_val == "test" or isinstance(seed_val, int), (
            f"Second run with seed='test' produced seed={seed_val!r}"
        )
        # Verify determinism: the first run should have produced the same value
        first_run_seed = _load(pipeline_run["tmp"] / "manifest.json")["seed"]
        assert seed_val == first_run_seed, (
            f"Seed is not deterministic: first run={first_run_seed!r}, second run={seed_val!r}"
        )

    # ------------------------------------------------------------------
    # Config-driven counts flow through to generated output
    # ------------------------------------------------------------------

    def test_counts_override_flows_to_output(self, tmp_path):
        """``compose_pipeline(counts=...)`` must drive per-type generation counts.

        Guards the config-driven scope feature: overriding counts changes how
        many entities/classes are generated. With 1 room, per-map count ==
        total count, so the assertions are exact.
        """
        from canon import FakeLLMBackend, LLMClient
        from canon.pipeline.runner import run_pipeline
        from examples.mazeworld_pack.compose import compose_pipeline

        counts = {"npc": 1, "item": 1, "monster": 1, "event": 2, "quest": 1, "class": 2}
        phases, ctx = compose_pipeline(
            seed="test", num_maps=1, output_dir=tmp_path, counts=counts
        )
        ctx.llm = LLMClient(FakeLLMBackend(make_responder(num_maps=1)), stats=ctx.stats)
        run_pipeline(phases, ctx)

        # _load handles both array (npc/event/quest) and keyed-object
        # (item/monster) files; len() is the entry count for either.
        assert len(_load(tmp_path / "npcs/npcs.json")) == 1
        assert len(_load(tmp_path / "items/items.json")) == 1
        assert len(_load(tmp_path / "monsters/monsters.json")) == 1
        assert len(_load(tmp_path / "events/events.json")) == 2
        assert len(_load(tmp_path / "quests/quests.json")) == 1
        assert len(_load(tmp_path / "classes/classes.json")) == 2
        # The config records the overrides (for introspection / stats).
        assert ctx.config.counts == counts

    # ------------------------------------------------------------------
    # Dialogue trees wired onto NPCs (DialoguePhase integration)
    # ------------------------------------------------------------------

    def test_bible_has_dialogues(self, pipeline_run):
        bible = pipeline_run["bible"]
        assert len(bible.dialogues) > 0, (
            "Bible has no dialogue trees after DialoguePhase ran"
        )

    def test_characters_have_dialogue_tree_id(self, pipeline_run):
        bible = pipeline_run["bible"]
        chars_without = [
            c.character_id
            for c in bible.characters
            if c.role == "npc" and not c.dialogue_tree_id
        ]
        assert not chars_without, (
            f"Characters missing dialogue_tree_id: {chars_without}"
        )

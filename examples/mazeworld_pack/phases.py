"""Mazeworld-specific phase overrides.

Each class subclasses a canon phase and applies mazeworld-shape conventions
(field renames, type coercions, additional top-level keys) at write time.
No mazeworld-specific logic leaks into src/canon/.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from canon.persistence import write_array_db, write_singleton
from canon.pipeline.phases.manifest import ManifestPhase as _BaseManifestPhase


def _entity_lore_stub(entity: Any, map_id: str) -> dict:
    """An EntityLore-shaped dict for world_bible rooms (npcs/items/monsters).

    Matches mazeworld's EntityLore: entity_type is required; room_id, lore, and
    tags round-trip through WorldBible.model_validate.
    """
    return {
        "entity_type": entity.entity_type,
        "entity_id": entity.entity_id,
        "name": entity.name,
        "room_id": entity.map_id or map_id,
        "lore": entity.lore,
        "tags": list(entity.tags) if entity.tags else [],
    }


class MazeworldManifestPhase(_BaseManifestPhase):
    """ManifestPhase variant that emits mazeworld-shape output.

    Changes vs the base phase:
    - ``manifest.json``: coerces ``seed`` to ``int`` so mazeworld's
      ``random.seed(manifest["seed"])`` call never receives a string.
    - ``world_bible.json``: applies field renames expected by mazeworld's
      loader (factions → faction, final_entity_id → final_boss_name, etc.),
      adds ``entity_index``, ``player_classes``, and populates room buckets
      using ``encounters`` (not ``events``).
    """

    # ------------------------------------------------------------------
    # Run override — also emit a mazeworld-shape story/story.json
    # ------------------------------------------------------------------

    def run(self, ctx: Any) -> None:
        """Run the base manifest assembly, then rewrite story/story.json.

        Canon's StoryPhase writes story/story.json in the native canon shape
        (``factions``, ``beats[].map_id``/``beat``, ``final_entity_id``…).
        Mazeworld's ``OverarchingStory`` loader requires the renamed shape
        (``faction``, ``beats[].room_id``/``summary``, ``final_boss_name``…),
        so we overwrite it here with the same transform applied to
        world_bible.json.
        """
        super().run(ctx)
        output_dir = Path(getattr(ctx.config, "output_dir", "."))
        output_paths = getattr(ctx.config, "output_paths", {})
        self._write_story_json(ctx, output_dir, output_paths)
        self._augment_classes_json(ctx, output_dir, output_paths)

    def _augment_classes_json(
        self, ctx: Any, output_dir: Path, output_paths: dict
    ) -> None:
        """Map canon's ClassArchetype dump to mazeworld's PlayerClass shape.

        ClassPhase loadout mode now generates complete classes (stats fixed to
        budget, spells, abilities, pools, weapon), so this only: (1) exposes the
        fixed stat block under mazeworld's ``stats`` key (canon stores it on
        ``stat_template``); (2) stamps the room environment for parity; (3)
        coerces optional null strings PlayerClass requires non-null.
        """
        path = output_dir / output_paths.get("classes", "classes/classes.json")
        if not path.exists():
            return
        classes = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(classes, list):
            return

        default_env = ""
        if getattr(ctx.bible, "maps", None):
            first_map = next(iter(ctx.bible.maps.values()), None)
            default_env = getattr(first_map, "environment", "") or ""

        for cls in classes:
            if not cls.get("stats") and cls.get("stat_template"):
                cls["stats"] = cls["stat_template"]
            if not cls.get("environment"):
                cls["environment"] = default_env
            if not cls.get("flavor_text"):
                cls["flavor_text"] = cls.get("description") or cls.get("lore") or ""
            # mazeworld's PlayerClass requires non-null strings here.
            for str_field in ("flavor_text", "starting_weapon"):
                if cls.get(str_field) is None:
                    cls[str_field] = ""

        write_array_db(path, classes)

    def _write_story_json(
        self, ctx: Any, output_dir: Path, output_paths: dict
    ) -> None:
        story_obj = self._mazeworld_story_dict(ctx.bible)
        path = output_dir / output_paths.get("story", "story/story.json")
        write_singleton(path, story_obj)

    # ------------------------------------------------------------------
    # Shared story transform — canon shape → mazeworld OverarchingStory shape
    # ------------------------------------------------------------------

    @staticmethod
    def _mazeworld_story_dict(bible: Any) -> dict:
        """Return ``bible.story`` transformed to mazeworld's OverarchingStory
        shape.  Used for both world_bible.json's story block and story.json.

        Transformations:
        - ``factions`` (list) → ``faction`` (first element, or ``{}``)
        - ``final_entity_id`` → ``final_boss_name``
        - ``final_entity_lore`` → ``final_boss_lore``
        - ``key_character_names`` → ``key_npc_names``
        - ``beats[N].map_id`` → ``beats[N].room_id``
        - ``beats[N].beat`` → ``beats[N].summary``
        - add ``beats[N].faction_presence`` / ``escalation`` defaults
        - add ``story_npcs`` / ``story_items`` / ``story_monsters`` stubs
        """
        story_obj: dict[str, Any] = (
            bible.story.model_dump(mode="json") if bible.story else {}
        )
        if not story_obj:
            return story_obj

        factions = story_obj.pop("factions", [])
        faction = factions[0] if factions else {}
        # mazeworld's faction model requires a non-null int threat_level;
        # canon leaves it None when the LLM doesn't supply one.
        if isinstance(faction, dict) and faction.get("threat_level") is None:
            faction["threat_level"] = 0
        story_obj["faction"] = faction

        if "final_entity_id" in story_obj:
            story_obj["final_boss_name"] = story_obj.pop("final_entity_id") or ""
        if "final_entity_lore" in story_obj:
            story_obj["final_boss_lore"] = story_obj.pop("final_entity_lore") or ""
        if "key_character_names" in story_obj:
            story_obj["key_npc_names"] = story_obj.pop("key_character_names")

        # Bind beats to the canonical map ids by order. The LLM emits prose
        # slugs (e.g. "drowned_threshold") in map_id, but every other dataset
        # keys rooms as "room_0"/"room_1"/…; mazeworld zips beats to rooms by
        # that key, so an unmatched slug silently drops the beat's lore.
        map_ids = list(bible.maps.keys())
        new_beats = []
        for i, b in enumerate(story_obj.get("beats", [])):
            b = dict(b)  # shallow copy so we can mutate
            b.pop("map_id", None)  # discard the LLM slug; remap by order below
            new_b = {
                "room_id": map_ids[i] if i < len(map_ids) else f"room_{i}",
                "summary": b.pop("beat", b.pop("summary", "")),
                "faction_presence": b.pop("faction_presence", ""),
                "escalation": b.pop("escalation", i + 1),
                "boss_name": b.pop("boss_name", "") or "",
                "boss_lore": b.pop("boss_lore", "") or "",
            }
            new_b.update(b)  # carry any remaining fields through
            new_beats.append(new_b)
        story_obj["beats"] = new_beats

        story_obj.setdefault("story_npcs", [])
        story_obj.setdefault("story_items", [])
        story_obj.setdefault("story_monsters", [])
        return story_obj

    # ------------------------------------------------------------------
    # Fix 1 — seed coercion
    # ------------------------------------------------------------------

    def _resolve_seed(self, ctx: Any) -> int:
        """Return the seed as an int for mazeworld's RNG initialiser."""
        raw = getattr(ctx.config, "seed", "")
        return self._coerce_seed_to_int(raw)

    @staticmethod
    def _coerce_seed_to_int(seed: Any) -> int:
        """Coerce any seed value to a stable int.

        - ``int`` → returned as-is.
        - Numeric string (``"1234"``) → ``int("1234")``.
        - Non-numeric string → stable MD5-derived int so seeds like
          ``"shadowspire_001"`` always map to the same number.
        - Anything else → ``0``.
        """
        if isinstance(seed, int):
            return seed
        if isinstance(seed, str):
            if seed.isdigit():
                return int(seed)
            return int(hashlib.md5(seed.encode("utf-8")).hexdigest()[:8], 16)
        return 0

    # ------------------------------------------------------------------
    # Fix 5 — world_bible.json field renames + entity_index + player_classes
    # ------------------------------------------------------------------

    def _write_world_bible(self, ctx: Any, output_dir: Path, output_paths: dict) -> None:
        """Write a mazeworld-shape world_bible.json.

        Applies the following transformations on top of the canon shape:
        - ``rooms[N].events`` → ``rooms[N].encounters``
        - ``story.factions`` (list) → ``story.faction`` (first element)
        - ``story.final_entity_id`` → ``story.final_boss_name``
        - ``story.final_entity_lore`` → ``story.final_boss_lore``
        - ``story.key_character_names`` → ``story.key_npc_names``
        - ``story.beats[N].map_id`` → ``story.beats[N].room_id``
        - ``story.beats[N].beat`` → ``story.beats[N].summary``
        - Add ``story.beats[N].faction_presence`` and ``story.beats[N].escalation``
        - Add top-level ``entity_index`` dict
        - Add top-level ``player_classes`` list (from class_archetypes)
        - Add story-level ``story_npcs``, ``story_items``, ``story_monsters`` stubs
        """
        bible = ctx.bible

        # ------------------------------------------------------------------
        # Build rooms with encounters (not events)
        # ------------------------------------------------------------------
        rooms: dict[str, Any] = {}
        all_entity_index: dict[str, dict] = {}

        for map_id, m in bible.maps.items():
            # mazeworld's RoomBible: npcs/items/monsters are list[EntityLore]
            # (full stubs); encounters/quests are list[str] of entity ids.
            buckets: dict[str, list] = {
                "npcs": [],
                "items": [],
                "monsters": [],
                "encounters": [],  # mazeworld calls events "encounters"
                "quests": [],
            }
            for entity in m.entities:
                if entity.entity_type == "event":
                    buckets["encounters"].append(entity.entity_id)
                elif entity.entity_type == "quest":
                    buckets["quests"].append(entity.entity_id)
                else:
                    bucket_key = {
                        "npc": "npcs",
                        "item": "items",
                        "monster": "monsters",
                    }.get(entity.entity_type)
                    if bucket_key:
                        buckets[bucket_key].append(_entity_lore_stub(entity, map_id))
                # Build entity_index entry
                all_entity_index[entity.entity_id] = {
                    "name": entity.name,
                    "type": entity.entity_type,
                }

            rooms[map_id] = {
                "environment": m.environment,
                "environment_name": m.name,
                "level": m.level,
                "story_beat": m.story_beat,
                "boss_name": getattr(m, "boss_name", "") or "",
                "boss_lore": getattr(m, "boss_lore", "") or "",
                "maze_ref": "",
                **buckets,
            }

        # ------------------------------------------------------------------
        # Build story dict with field renames (shared with story.json)
        # ------------------------------------------------------------------
        story_obj = self._mazeworld_story_dict(bible)

        # ------------------------------------------------------------------
        # player_classes: list[EntityLore] (mazeworld WorldBible field) — slim
        # stubs, NOT full archetype dumps (those lack the required entity_type
        # and fail WorldBible.model_validate).
        # ------------------------------------------------------------------
        player_classes = [
            {
                "entity_type": "player_class",
                "entity_id": arch.archetype_id,
                "name": arch.name,
                "room_id": "",
                "lore": arch.lore or arch.description or "",
                "tags": list(arch.role_tags) if arch.role_tags else [],
            }
            for arch in bible.class_archetypes.values()
        ]

        # entity_index is left empty to match MazeWorld production parity.
        # MazeWorld declares this field as "Legacy compat" and never populates
        # it in the production pipeline (verified by inspection — Wave 5.7).
        # Cradle and mazeworld both treat the per-room entity lists as the
        # authoritative entity catalog.
        world_bible = {
            "story": story_obj,
            "rooms": rooms,
            "player_classes": player_classes,
            "entity_index": {},
        }

        path = output_dir / output_paths.get("world_bible", "world_bible.json")
        write_singleton(path, world_bible)

        # Flat rooms/rooms.json index so Cradle's "rooms" entity tab resolves.
        # Cradle reads <type>/<type>.json first, so this keyed index is found
        # before (and instead of) listing the per-room rooms/<id>/maze.json
        # subdirs (which MazeWorld's game loader reads). Additive and
        # mazeworld-ignored — does NOT touch the maze files or world_bible.
        rooms_index = {
            rid: {"id": rid, **room, "maze_ref": f"rooms/{rid}/maze.json"}
            for rid, room in rooms.items()
        }
        write_singleton(output_dir / "rooms" / "rooms.json", rooms_index)

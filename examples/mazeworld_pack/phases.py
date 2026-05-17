"""Mazeworld-specific phase overrides.

Each class subclasses a canon phase and applies mazeworld-shape conventions
(field renames, type coercions, additional top-level keys) at write time.
No mazeworld-specific logic leaks into src/canon/.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from canon.persistence import write_singleton
from canon.pipeline.phases.manifest import ManifestPhase as _BaseManifestPhase


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
            buckets: dict[str, list] = {
                "npcs": [],
                "items": [],
                "monsters": [],
                "encounters": [],  # mazeworld calls events "encounters"
                "quests": [],
            }
            for entity in m.entities:
                stub = {
                    "entity_type": entity.entity_type,
                    "entity_id": entity.entity_id,
                    "name": entity.name,
                    "lore": entity.lore,
                    "tags": list(entity.tags) if entity.tags else [],
                    "room_id": entity.map_id or map_id,
                }
                bucket_key = {
                    "npc": "npcs",
                    "item": "items",
                    "monster": "monsters",
                    "event": "encounters",
                    "quest": "quests",
                }.get(entity.entity_type)
                if bucket_key:
                    buckets[bucket_key].append(stub)
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
                "player_classes": [],
            }

        # ------------------------------------------------------------------
        # Build story dict with field renames
        # ------------------------------------------------------------------
        story_obj: dict[str, Any] = (
            bible.story.model_dump(mode="json") if bible.story else {}
        )

        if story_obj:
            # factions list → single faction object
            factions = story_obj.pop("factions", [])
            story_obj["faction"] = factions[0] if factions else {}

            # final_entity_id → final_boss_name
            if "final_entity_id" in story_obj:
                story_obj["final_boss_name"] = story_obj.pop("final_entity_id") or ""
            if "final_entity_lore" in story_obj:
                story_obj["final_boss_lore"] = story_obj.pop("final_entity_lore") or ""

            # key_character_names → key_npc_names
            if "key_character_names" in story_obj:
                story_obj["key_npc_names"] = story_obj.pop("key_character_names")

            # Beat field renames
            new_beats = []
            for i, b in enumerate(story_obj.get("beats", [])):
                b = dict(b)  # shallow copy so we can mutate
                new_b = {
                    "room_id": b.pop("map_id", f"room_{i}"),
                    "summary": b.pop("beat", b.pop("summary", "")),
                    "faction_presence": b.pop("faction_presence", ""),
                    "escalation": b.pop("escalation", i + 1),
                    "boss_name": b.pop("boss_name", "") or "",
                    "boss_lore": b.pop("boss_lore", "") or "",
                }
                new_b.update(b)  # carry any remaining fields through
                new_beats.append(new_b)
            story_obj["beats"] = new_beats

            # Story-level stubs
            story_obj.setdefault("story_npcs", [])
            story_obj.setdefault("story_items", [])
            story_obj.setdefault("story_monsters", [])

        # ------------------------------------------------------------------
        # player_classes: array of class archetype dicts
        # ------------------------------------------------------------------
        player_classes = [
            arch.model_dump(mode="json")
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

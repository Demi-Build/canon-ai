"""ManifestPhase — final non-LLM phase. Reads on-disk per-type files,
computes counts, assembles manifest.json + world_bible.json from
in-memory bible state, writes generation_stats.json.

Runs LAST in any pipeline. No LLM calls. Idempotent.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from canon.bible.models import BibleMetadata

logger = logging.getLogger(__name__)

_AUDIO_EXTS: tuple[str, ...] = (".mp3", ".wav", ".ogg")


class ManifestPhase:
    """Assembles data/manifest.json + data/world_bible.json + data/generation_stats.json
    from the in-memory bible and on-disk phase outputs.

    Output shape matches mazeworld's manifest.json exactly so cradle and
    mazeworld load it unchanged.
    """

    name: str = "manifest"

    def run(self, ctx: Any) -> None:
        output_dir = Path(getattr(ctx.config, "output_dir", "."))
        output_paths = getattr(ctx.config, "output_paths", {})

        # 1. world_bible.json — {"story": ..., "rooms": {...}}
        self._write_world_bible(ctx, output_dir, output_paths)

        # 2. manifest.json — top-level index
        self._write_manifest(ctx, output_dir, output_paths)

        # 3. generation_stats.json — standalone copy of ctx.stats
        self._write_stats(ctx, output_dir, output_paths)

        # 4. Stamp metadata
        if not isinstance(getattr(ctx.bible, "metadata", None), BibleMetadata):
            ctx.bible.metadata = BibleMetadata()
        ctx.bible.metadata.phases_run.append(self.name)

    # ------------------------------------------------------------------
    # Extension hooks — subclasses can override these
    # ------------------------------------------------------------------

    def _resolve_seed(self, ctx: Any) -> Any:
        """Return the seed value to write into manifest.json.

        Default: returns the raw ``ctx.config.seed`` value unchanged.
        Subclasses can override to coerce the seed (e.g. string → int).
        """
        return getattr(ctx.config, "seed", "")

    def _scan_audio_dir(
        self,
        output_dir: Path,
        subdir: str,
        exts: tuple[str, ...] = _AUDIO_EXTS,
    ) -> dict[str, str]:
        """Scan an audio sub-directory and return ``{stem: absolute_path}``.

        Returns an empty dict when the directory does not exist or is empty.
        """
        audio_dir = Path(output_dir) / subdir
        if not audio_dir.exists():
            return {}
        return {
            p.stem: str(p.resolve())
            for p in audio_dir.iterdir()
            if p.is_file() and p.suffix.lower() in exts
        }

    def _write_world_bible(self, ctx: Any, output_dir: Path, output_paths: dict) -> None:
        path = output_paths.get("world_bible", "world_bible.json")
        bible = ctx.bible
        # Build the {"story": ..., "rooms": {...}} shape mazeworld expects
        rooms = {}
        for map_id, m in bible.maps.items():
            # Per-room entries with entity-reference stubs
            # (entity_type, entity_id, name, lore, tags)
            entities_by_type: dict[str, list] = {
                "npcs": [],
                "items": [],
                "monsters": [],
                "events": [],
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
                bucket = {
                    "npc": "npcs",
                    "item": "items",
                    "monster": "monsters",
                    "event": "events",
                    "quest": "quests",
                }.get(entity.entity_type)
                if bucket:
                    entities_by_type[bucket].append(stub)
            rooms[map_id] = {
                "environment": m.environment,
                "environment_name": m.name,
                "level": m.level,
                "story_beat": m.story_beat,
                "boss_name": getattr(m, "boss_name", "") or "",
                "boss_lore": getattr(m, "boss_lore", "") or "",
                "maze_ref": "",  # mazeworld uses this; canon leaves empty
                **entities_by_type,
                "player_classes": [],
            }
        world_bible = {
            "story": bible.story.model_dump(mode="json") if bible.story else {},
            "rooms": rooms,
        }
        ctx.adapter.write_json_singleton(path, world_bible)

    def _write_manifest(self, ctx: Any, output_dir: Path, output_paths: dict) -> None:
        path = output_paths.get("manifest", "manifest.json")
        bible = ctx.bible

        # Counts
        npc_count = len([c for c in bible.characters if c.role == "npc"])
        # If characters list is empty, count NPCs from per-room entities
        all_entities = [e for m in bible.maps.values() for e in m.entities]
        if npc_count == 0:
            npc_count = len([e for e in all_entities if e.entity_type == "npc"])
        quest_count = len([e for e in all_entities if e.entity_type == "quest"])
        event_count = len([e for e in all_entities if e.entity_type == "event"])
        class_count = len(bible.class_archetypes)

        environments = [m.environment for m in bible.maps.values()]
        environment_names = [m.name for m in bible.maps.values()]

        rooms_index = []
        for m in bible.maps.values():
            room_npcs = [e for e in m.entities if e.entity_type == "npc"]
            room_events = [e for e in m.entities if e.entity_type == "event"]
            room_quests = [e for e in m.entities if e.entity_type == "quest"]
            rooms_index.append({
                "room_id": m.map_id,
                "environment": m.environment,
                "environment_name": m.name,
                "npc_count": len(room_npcs),
                "event_count": len(room_events),
                "quest_count": len(room_quests),
                "environment_portrait": getattr(m, "environment_portrait", None),
            })

        # Asset paths: scan output dirs for generated audio files.
        # ctx.artifacts["music_paths"] / "sfx_paths" are kept as overrides;
        # if absent, fall back to directory scanning.
        music_dir_rel = output_paths.get("music_dir", "music")
        sfx_dir_rel = output_paths.get("sfx_dir", "sfx")
        music_paths = ctx.artifacts.get("music_paths") or self._scan_audio_dir(output_dir, music_dir_rel)
        sfx_paths = ctx.artifacts.get("sfx_paths") or self._scan_audio_dir(output_dir, sfx_dir_rel)

        # Validation report from ctx.artifacts
        validation_report = ctx.artifacts.get("validation_report", None)
        if validation_report and hasattr(validation_report, "to_dict"):
            validation_report = validation_report.to_dict()

        # Generation stats
        stats_dict = ctx.stats.to_dict() if ctx.stats else {}

        # Metadata — check whether AssetPhase ran
        phases_run = (
            bible.metadata.phases_run
            if isinstance(getattr(bible, "metadata", None), BibleMetadata)
            else []
        )

        manifest = {
            "seed": self._resolve_seed(ctx),
            "num_rooms": len(bible.maps),
            "environments": environments,
            "environment_names": environment_names,
            "maze_width": ctx.artifacts.get("maze_width", 40),
            "maze_height": ctx.artifacts.get("maze_height", 30),
            "generated_at": datetime.now(UTC).isoformat(),
            "npc_count": npc_count,
            "quest_count": quest_count,
            "event_count": event_count,
            "class_count": class_count,
            "portraits_generated": "assets" in phases_run,
            "player_portrait": ctx.artifacts.get("player_portrait", ""),
            "gameover_portrait": ctx.artifacts.get("gameover_portrait", ""),
            "victory_portrait": ctx.artifacts.get("victory_portrait", ""),
            "start_portrait": ctx.artifacts.get("start_portrait", ""),
            "game_mode": ctx.artifacts.get("game_mode", "offline_static"),
            "story_title": bible.story.title if bible.story else "",
            "faction_name": (
                bible.story.factions[0].name
                if bible.story and bible.story.factions
                else ""
            ),
            "story_seed": bible.seed,
            "rooms": rooms_index,
            "validation_report": validation_report or {},
            "generation_stats": stats_dict,
            "music": music_paths,
            "sfx": sfx_paths,
        }
        ctx.adapter.write_json_singleton(path, manifest)

    def _write_stats(self, ctx: Any, output_dir: Path, output_paths: dict) -> None:
        path = output_paths.get("generation_stats", "generation_stats.json")
        ctx.adapter.write_json_singleton(path, ctx.stats.to_dict() if ctx.stats else {})

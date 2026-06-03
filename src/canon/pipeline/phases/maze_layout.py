"""MazeLayoutPhase — generates per-map maze grids and writes
data/rooms/<map_id>/maze.json for each map.

This is the v0.2 concrete Layout phase. Future games (platformer, VN)
ship sibling phases like PlatformerLayoutPhase.
"""
from __future__ import annotations

import logging
import random
from pathlib import Path
from typing import Any

from canon.bible.models import BibleMetadata
from canon.layout import MazeLayout
from canon.layout.maze import generate_maze
from canon.persistence import write_per_map_file

logger = logging.getLogger(__name__)


class MazeLayoutPhase:
    """Generates a MazeLayout for every Map in the bible and persists
    each as data/rooms/<map_id>/maze.json.

    The generated layouts are skeletal: grid + dimensions + player_start +
    door_position. NPC/item/event placements are populated by later phases
    (DatabasePhase NPC writes npc_positions, EntityPhase placements update
    item_placements, etc.) and re-flushed by ManifestPhase.

    Args:
        width: maze width in tiles (default 40, matches mazeworld)
        height: maze height in tiles (default 30, matches mazeworld)
        path_template: format string with ``{map_id}`` for output path.
            Defaults to ``ctx.config.output_paths["maze"]`` if available,
            otherwise ``"rooms/{map_id}/maze.json"``.
    """

    name: str = "maze_layout"

    def __init__(
        self,
        width: int = 40,
        height: int = 30,
        path_template: str | None = None,
    ) -> None:
        self.width = width
        self.height = height
        self.path_template = path_template

    def run(self, ctx: Any) -> None:
        if not ctx.bible.maps:
            logger.warning("MazeLayoutPhase: no maps in bible; nothing to do.")
            self._stamp_metadata(ctx)
            return

        path_template = self.path_template or self._default_path_template(ctx)
        output_dir = getattr(ctx.config, "output_dir", ".")

        for map_id, map_obj in ctx.bible.maps.items():
            # Each map gets a sub-rng derived from ctx.rng + map_id so that
            # per-map generation is reproducible even if maps are reordered.
            seed = self._derive_map_seed(ctx.rng, map_id)
            map_rng = random.Random(seed)

            layout: MazeLayout = generate_maze(
                width=self.width,
                height=self.height,
                rng=map_rng,
                player_start=(1, 1),
                door_position=(self.width - 2, self.height - 2),
            )
            layout.environment = getattr(map_obj, "environment", "") or ""
            layout.environment_name = getattr(map_obj, "name", "") or ""

            map_obj.layout = layout

            # Write per-map file: combine output_dir + relative path template
            full_template = str(Path(output_dir) / path_template)
            write_per_map_file(full_template, map_id, layout)

        self._stamp_metadata(ctx)
        logger.info(
            "MazeLayoutPhase generated %d maze layouts.", len(ctx.bible.maps)
        )

    @staticmethod
    def _derive_map_seed(rng: random.Random, map_id: str) -> int:
        """Mix the parent rng's state with map_id so different maps differ
        but the whole world remains reproducible from one seed."""
        return rng.getrandbits(64) ^ hash(map_id)

    @staticmethod
    def _default_path_template(ctx: Any) -> str:
        """Return the maze path template from ctx.config if available.

        Checks ``ctx.config.output_paths`` (a plain dict attribute, not a
        Pydantic model field — set dynamically by the coordinator pass once
        it lands) and falls back to the mazeworld default.
        """
        output_paths = getattr(ctx.config, "output_paths", {})
        return output_paths.get("maze", "rooms/{map_id}/maze.json")

    def _stamp_metadata(self, ctx: Any) -> None:
        if not isinstance(getattr(ctx.bible, "metadata", None), BibleMetadata):
            ctx.bible.metadata = BibleMetadata()
        ctx.bible.metadata.phases_run.append(self.name)

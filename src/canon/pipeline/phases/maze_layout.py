"""MazeLayoutPhase — generates per-map maze grids and writes
data/rooms/<map_id>/maze.json for each map.

This is the v0.2 concrete Layout phase. Future games (platformer, VN)
ship sibling phases like PlatformerLayoutPhase.
"""
from __future__ import annotations

import logging
from typing import Any

from canon.bible.models import BibleMetadata
from canon.layout import MazeLayout
from canon.layout.maze import generate_maze
from canon.pipeline.rng import derive_rng
from canon.pipeline.steplog import step

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

        config_seed = getattr(ctx.config, "seed", "")
        # Row P0-10 (§3.0-E/D): one item per room, through the one emitter.
        for map_index, (map_id, map_obj) in enumerate(ctx.bible.maps.items(), start=1):
            step(ctx, self.name, map_id, map_index, len(ctx.bible.maps))
            # Each map's rng is a pure function of (config seed, phase,
            # map_id): reproducible across processes (no hash() salt) and
            # independent of map iteration order — Phase 2 can regenerate a
            # single map's layout in isolation and get the same maze.
            map_rng = derive_rng(config_seed, self.name, map_id)

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

            # Write per-map file: the adapter resolves the relative template
            # against output_dir before formatting {map_id}.
            ctx.adapter.write_per_map(path_template, map_id, layout)

        self._stamp_metadata(ctx)
        logger.info(
            "MazeLayoutPhase generated %d maze layouts.", len(ctx.bible.maps)
        )

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

"""Combat/level-picks arc (K3): the hazard-immune variant's occupancy
gates. (Bounce v2 is a consumer mechanic proven by PLAT_TRAJ A/B runs:
hold-jump apex ~= full jump height, damped otherwise, both surfaces
0.001 — see the arc memory.)"""

from __future__ import annotations

import numpy as np

from canon.packs.platformer.rules import GameRules
from canon.packs.platformer.tiles import DEFAULT_TILES
from canon.packs.platformer.validate import (
    check_placements,
    hazard_stand_cells,
)
from canon.packs.platformer.variants import DEFAULT_VARIANTS

SPIKE = 10


def _spike_grid(width: int = 24, height: int = 12):
    """Floor + a footed spike strip at (10..12, 9) (spikes ON the floor;
    standing row is 9, ground row 10)."""
    grid = np.zeros((height, width), dtype=np.int8)
    grid[10, :] = 1
    grid[11, :] = 1
    for x in (10, 11, 12):
        grid[9, x] = SPIKE
    return grid


ENEMIES = {
    "imp": {"archetype": "patroller", "size": 1.0},
    "fish": {"archetype": "swimmer", "size": 1.0, "swim_style": "within"},
}


def _check(placements):
    return check_placements(
        _spike_grid(), placements, (2, 8), ENEMIES,
        rules=GameRules(), tiles=DEFAULT_TILES, variants=DEFAULT_VARIANTS,
    )


class TestEmberborn:
    def test_pack_ships_the_variant_capped(self) -> None:
        v = DEFAULT_VARIANTS.by_name["emberborn"]
        assert v.behavior.get("hazard_immune") is True
        assert GameRules().variant_caps.get("emberborn") == 1

    def test_hazard_stand_cells_need_footing(self) -> None:
        grid = _spike_grid()
        cells = hazard_stand_cells(grid, DEFAULT_TILES)
        assert (11, 9) in cells
        grid[9, 15] = SPIKE  # a floating spike with no support below
        assert (15, 8) not in hazard_stand_cells(grid, DEFAULT_TILES)

    def test_emberborn_stands_on_spikes_others_do_not(self) -> None:
        accepted, problems, _ = _check(
            [{"enemy_id": "imp", "x": 11, "y": 9, "variant": "emberborn"}]
        )
        assert not problems and len(accepted) == 1
        _, problems, _ = _check(
            [{"enemy_id": "imp", "x": 11, "y": 9, "variant": "elite"}]
        )
        assert problems  # a non-immune variant is rejected on the spikes
        _, problems, _ = _check(
            [{"enemy_id": "imp", "x": 11, "y": 9}]
        )
        assert problems  # and so is the base creature

    def test_immunity_is_not_a_wall_pass(self) -> None:
        grid = _spike_grid()
        grid[9, 18] = 1  # a solid block on the standing row
        accepted, problems, _ = check_placements(
            grid,
            [{"enemy_id": "imp", "x": 18, "y": 9, "variant": "emberborn"}],
            (2, 8), ENEMIES,
            rules=GameRules(), tiles=DEFAULT_TILES,
            variants=DEFAULT_VARIANTS,
        )
        assert problems  # solids still block an emberborn anchor

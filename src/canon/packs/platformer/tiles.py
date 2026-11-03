"""Tile registry — per-GAME tile vocabulary, as data (Phase 3b).

The GameRules split applied to tiles: **values in data, categories in
code**. A game's tiles load from a per-game JSON file (``tile_types.json``
next to the schemas; ``--tiles`` on the runner). Each entry is
``{id, name, category, color_role, params}``. The CATEGORIES are the
code-enforced interpreters — adding a new tile of an existing category
(swimmable lava, a laser hazard, mud) is a data edit, zero new code:

- ``empty``:   traversable air; exactly one, id 0 (grids init to zeros).
- ``solid``:   blocks from every side.
- ``one_way``: platform — lands from above, passes otherwise.
- ``hazard``:  touch-kill/damage. ``params.damage`` reserved for HP games.
- ``volume``:  swimmable/wadeable region. ``params``: ``speed_factor``
  (horizontal multiplier inside), ``gravity`` (sink accel), ``impulse``
  (swim-stroke velocity), optional ``damage_per_second`` (water = benign,
  lava = damaging, mud = slow). Consumers read these off tileset slots.

Numbering bands stay (Appendix E.1) and are enforced at load: empty/
solid/one_way < 10, hazards 10-19, volumes >= 20. Ids must fit int8
(collision grids). Names are DSL identifiers — generic ops take them
verbatim (``volume(lava, 4, 9, 12)``), and the ergonomic aliases
(``water(...)``, ``spike(...)``) resolve those names through this
registry. Structural ops need ``floor``/``platform``/``wall`` to exist,
so every registry must define them.

``color_role`` picks the placeholder palette swatch (tileset.py); the
Phase 3b-deferred style-guide agent will map roles to generated palettes
at this same seam.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

#: The pack's default game — the template other games copy and edit.
DEFAULT_TILES_PATH = Path(__file__).parent / "tile_types.json"

Category = Literal["empty", "solid", "one_way", "hazard", "volume"]

#: Load-time band contract per category (lo inclusive, hi inclusive).
_BANDS: dict[str, tuple[int, int]] = {
    "empty": (0, 0),
    "solid": (1, 9),
    "one_way": (1, 9),
    "hazard": (10, 19),
    "volume": (20, 127),  # int8 grids cap ids at 127
}

#: Structural DSL ops stamp these by NAME; every game must define them.
REQUIRED_NAMES = ("floor", "platform", "wall")


class TileDef(BaseModel):
    """One registry entry. ``params`` is open data interpreted by the
    tile's category; unknown keys ride along inert (open carriage)."""

    id: int
    name: str
    category: Category
    color_role: str = ""
    params: dict[str, Any] = Field(default_factory=dict)


class TileRegistry(BaseModel):
    model_config = ConfigDict(extra="allow")  # _doc etc. ride through

    tiles: list[TileDef]

    @model_validator(mode="after")
    def _check_contract(self) -> TileRegistry:
        problems: list[str] = []
        ids = [t.id for t in self.tiles]
        names = [t.name for t in self.tiles]
        if len(set(ids)) != len(ids):
            problems.append(f"duplicate tile ids in {sorted(ids)}")
        if len(set(names)) != len(names):
            problems.append(f"duplicate tile names in {sorted(names)}")
        for tile in self.tiles:
            lo, hi = _BANDS[tile.category]
            if not lo <= tile.id <= hi:
                problems.append(
                    f"tile {tile.name!r} (id {tile.id}) is outside the "
                    f"{tile.category} band {lo}..{hi} — bands keep grids "
                    "debuggable: empty=0, solids/one_way 1-9, hazards "
                    "10-19, volumes 20-127"
                )
            if not tile.name.replace("_", "").isalnum() or not tile.name[
                :1
            ].isalpha() or tile.name != tile.name.lower():
                problems.append(
                    f"tile name {tile.name!r} is not a DSL identifier "
                    "(lowercase letters/digits/underscore, starts with a "
                    "letter)"
                )
        empties = [t for t in self.tiles if t.category == "empty"]
        if len(empties) != 1 or (empties and empties[0].id != 0):
            problems.append(
                "exactly one 'empty' tile with id 0 is required (collision "
                "grids initialize to zeros)"
            )
        by_name = {t.name for t in self.tiles}
        for required in REQUIRED_NAMES:
            if required not in by_name:
                problems.append(
                    f"missing required structural tile {required!r} — the "
                    f"DSL's floor/platform/wall ops stamp it by name"
                )
        if problems:
            raise ValueError("tile registry invalid: " + "; ".join(problems))
        return self

    # -- lookups (consumers and the DSL resolve everything through these) --

    @property
    def by_name(self) -> dict[str, TileDef]:
        return {t.name: t for t in self.tiles}

    @property
    def by_id(self) -> dict[int, TileDef]:
        return {t.id: t for t in self.tiles}

    def ids(self, *categories: str) -> set[int]:
        return {t.id for t in self.tiles if t.category in categories}

    def named(self, *categories: str) -> list[TileDef]:
        return [t for t in self.tiles if t.category in categories]

    @property
    def empty_id(self) -> int:
        return 0  # contract-enforced above


def load_tiles(path: str | Path = DEFAULT_TILES_PATH) -> TileRegistry:
    """Load a game's tile registry. Category/band contract validated at
    load — a broken registry fails loudly before any generation runs."""
    return TileRegistry.model_validate(json.loads(Path(path).read_text()))


DEFAULT_TILES = load_tiles()

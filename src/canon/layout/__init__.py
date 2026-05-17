"""Layout abstraction — base class and concrete subclasses for map playable spaces.

`Layout` is the discriminated-union base.  Concrete subclasses set
`layout_type` as a `Literal` so JSON can be deserialized into the right
subclass without extra bookkeeping.

Current concrete subclasses:
  - `MazeLayout` — tile-grid layout for dungeon-crawl-style games

Future game types (platformer levels, tower-defense maps, VN scenes) ship
sibling `Layout` subclasses without modifying this base or the `Map` model.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class Layout(BaseModel):
    """Abstract base.  Concrete subclasses define a map's playable space.

    `layout_type` is a discriminator.  Concrete subclasses set it as a
    `Literal` so JSON deserialization can re-hydrate the right subclass.

    Future games (platformer, tower defense, VN) ship sibling Layout
    subclasses without modifying this base or the Map model.
    """

    layout_type: str
    extra: dict = Field(default_factory=dict)


class MazeLayout(Layout):
    """Tile-grid layout for dungeon-crawl-style games.

    Mirrors mazeworld's ``data/rooms/<id>/maze.json`` shape exactly so the
    file persists round-trips through cradle and mazeworld unchanged.

    Grid encoding (matching mazeworld):
      - 0   = open path
      - 1   = wall
      - -1  = event tile
      - -2  = door
      - integers >= 2000 = item ID placed on that cell

    The ``npc_positions``, ``item_placements``, ``event_positions``, and
    ``quest_ids`` fields are populated by later pipeline phases; they
    default to empty so a freshly-generated ``MazeLayout`` is valid even
    before entity placement has run.
    """

    layout_type: Literal["maze"] = "maze"
    grid: list[list[int]]
    width: int
    height: int
    door_position: tuple[int, int]
    door_revealed: bool = False
    gate_encounter_id: int | None = None
    npc_positions: dict[str, tuple[int, int]] = Field(default_factory=dict)
    item_placements: list[dict] = Field(default_factory=list)
    event_positions: list[dict] = Field(default_factory=list)
    quest_ids: list[int] = Field(default_factory=list)
    player_start: tuple[int, int]
    environment: str = ""
    environment_name: str = ""


__all__ = ["Layout", "MazeLayout"]

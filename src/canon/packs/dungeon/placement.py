"""Mazeworld entity placement.

``MazeworldPlacementPhase`` takes the carved maze (``map.layout``) plus the
entities recorded in ``bible.maps[*].entities`` and writes the position maps
mazeworld reads at room load: ``npc_positions``, ``event_positions``,
``item_placements``, ``quest_ids``, and the grid tile encodings.

Why this is a separate phase (and lives in the pack, not src/canon):

MazeWorld's pipeline interleaves maze carving with entity placement — the maze
reserves tiles, entity generation fills them, and positions are written back.
Canon keeps maze generation (game-agnostic) and entity generation (declarative
DatabaseSpecs) as independent phases, so a placement step reconnects them.  The
grid-encoding conventions (events → ``-1``, items → ``item_id``, NPCs stored in
``npc_positions``) are mazeworld-specific, so the logic belongs here.

maze.json is authoritative for placement at load time: mazeworld reads NPC/event
positions from it and ignores the entities' own ``x``/``y`` when these maps are
present (see ``MazeWorld/main.py`` room setup).  So this phase only rewrites
maze.json — it does not touch the per-type entity files.

Row P0-8 lifted the per-kind branches of ``_place_one`` out as module-level
functions (``open_cells`` / ``place_entities``) so the per-step 🎲 rolls
(``canon.packs.dungeon.rolls``) re-run the SAME sampling on an existing room
instead of a second implementation (master §1 doctrine 2). The phase keeps its
behaviour exactly: one shuffled open-cell list consumed NPCs → events → items,
one cursor, the same break-when-exhausted rule — so the generated tree stays
byte-identical.
"""

from __future__ import annotations

import json
import logging
import random
from collections.abc import Collection, Iterable, Sequence
from pathlib import Path
from typing import Any

from canon.bible.models import BibleMetadata
from canon.pipeline.rng import derive_seed
from canon.pipeline.steplog import step

logger = logging.getLogger(__name__)

# Mazeworld grid encoding (see MazeWorld/src/models/maze.py).
_EVENT_TILE_ID = -1

#: The order ``_place_one`` consumes the shuffled cell list in (P.6.1: "one
#: shuffled open-cell list … consumed NPCs → events → items"). Data, not a
#: literal in the loop: the roll entry points pass a SUBSET of it.
PLACEMENT_ORDER: tuple[str, ...] = ("npc", "event", "item")

#: Per-kind maze.json key + grid stamp, mirroring the ``GridKind.placements``
#: block the registry stamps (P.3.2). ``None`` = no grid encoding (NPCs);
#: ``"id"`` = the cell carries the row id (items); an int = a bare marker.
PLACEMENT_KEYS: dict[str, str] = {
    "npc": "npc_positions",
    "event": "event_positions",
    "item": "item_placements",
}


def open_cells(
    grid: list[list[int]], *, exclude: Collection[tuple[int, int]] = ()
) -> list[tuple[int, int]]:
    """Every walkable cell of *grid* (value ``0``) that is not in *exclude* —
    the candidate list ``_place_one`` shuffles. ``exclude`` carries the
    player start, the door, and (for a single-kind re-roll) the cells the
    OTHER kinds already occupy."""
    blocked = {c for c in exclude if c is not None}
    return [
        (x, y)
        for y, row in enumerate(grid)
        for x, value in enumerate(row)
        if value == 0 and (x, y) not in blocked
    ]


def place_entities(
    grid: list[list[int]],
    entities: Iterable[Any],
    rng: random.Random,
    *,
    exclude: Collection[tuple[int, int]] = (),
    kinds: Sequence[str] = PLACEMENT_ORDER,
) -> dict[str, Any]:
    """Sample cells for every entity stub of the requested *kinds* and stamp
    the grid — the body of ``_place_one``, callable on any grid.

    Returns only the keys the requested kinds own (``npc_positions`` /
    ``event_positions`` / ``item_placements``), so a single-kind roll leaves
    the others untouched. *grid* is mutated in place (event cells become
    ``-1``, item cells the item id); NPCs carry no grid encoding.
    """
    cells = open_cells(grid, exclude=exclude)
    rng.shuffle(cells)
    cursor = 0

    def take() -> tuple[int, int] | None:
        nonlocal cursor
        if cursor >= len(cells):
            return None
        cell = cells[cursor]
        cursor += 1
        return cell

    stubs = list(entities)
    out: dict[str, Any] = {}

    if "npc" in kinds:
        # MazeLayout.npc_positions is typed tuple[int, int]; tuples serialize
        # to JSON arrays ([x, y]) which is what mazeworld reads.
        npc_positions: dict[str, tuple[int, int]] = {}
        for stub in stubs:
            if stub.entity_type != "npc":
                continue
            nid = _as_int_id(stub.entity_id)
            if nid is None:
                continue
            cell = take()
            if cell is None:
                break
            npc_positions[str(nid)] = (cell[0], cell[1])
        out["npc_positions"] = npc_positions

    if "event" in kinds:
        event_positions: list[dict] = []
        for stub in stubs:
            if stub.entity_type != "event":
                continue
            eid = _as_int_id(stub.entity_id)
            if eid is None:
                continue
            cell = take()
            if cell is None:
                break
            x, y = cell
            grid[y][x] = _EVENT_TILE_ID
            event_positions.append({"x": x, "y": y, "event_id": eid})
        out["event_positions"] = event_positions

    if "item" in kinds:
        item_placements: list[dict] = []
        for stub in stubs:
            if stub.entity_type != "item":
                continue
            iid = _as_int_id(stub.entity_id)
            if iid is None:
                continue
            cell = take()
            if cell is None:
                break
            x, y = cell
            grid[y][x] = iid
            item_placements.append(
                {
                    "x": x,
                    "y": y,
                    "item_id": iid,
                    "name": stub.name or f"item_{iid}",
                    "portrait_prompt": "",
                    "profile_image": "",
                }
            )
        out["item_placements"] = item_placements

    return out


def _as_int_id(entity_id: Any) -> int | None:
    """Return the int form of an entity_id, or None if it isn't numeric.

    DatabasePhase stubs use ``str(allocated_id)`` (e.g. ``"1000"``) when an
    IDAllocator prefix is configured, which is always the case in the mazeworld
    pipeline.  A non-numeric id (the ``f"{type}_{index}"`` fallback) is skipped
    rather than crashing ``int()``.
    """
    try:
        return int(entity_id)
    except (TypeError, ValueError):
        return None


class MazeworldPlacementPhase:
    """Populate each map's maze.json with entity positions.

    Run order: AFTER ``MazeLayoutPhase`` (grid carved, ``map.layout`` set) and
    AFTER every ``DatabasePhase`` (entities recorded in
    ``bible.maps[*].entities``).  Compose it just before the manifest phase.
    """

    name: str = "mazeworld_placement"

    def __init__(self, path_template: str | None = None) -> None:
        self.path_template = path_template

    def run(self, ctx: Any) -> None:
        if not getattr(ctx.bible, "maps", None):
            logger.warning("MazeworldPlacementPhase: no maps in bible; nothing to do.")
            self._stamp_metadata(ctx)
            return

        output_dir = getattr(ctx.config, "output_dir", ".")
        path_template = self.path_template or self._default_path_template(ctx)

        # Load generated events + boss monster ids so we can designate a gate
        # (boss combat tile guarding the door) per room. These files are written
        # by the DatabasePhases that run before placement.
        events_by_id = self._load_events_by_id(output_dir)
        boss_ids = self._load_boss_monster_ids(output_dir)
        map_ids = list(ctx.bible.maps.keys())
        final_map_id = map_ids[-1] if map_ids else None
        gate_flags: dict[int, dict] = {}  # event_id -> {is_climax_boss, boss_id}

        placed = 0
        # Row P0-10 (§3.0-E/D): one item per room placed, through the one emitter.
        for map_index, (map_id, map_obj) in enumerate(ctx.bible.maps.items(), start=1):
            step(ctx, self.name, map_id, map_index, len(ctx.bible.maps))
            layout = getattr(map_obj, "layout", None)
            if layout is None or not getattr(layout, "grid", None):
                logger.warning(
                    "MazeworldPlacementPhase: map %s has no maze layout; skipping.",
                    map_id,
                )
                continue
            self._place_one(ctx, map_id, map_obj, layout)
            gate = self._designate_gate(
                map_obj, layout, events_by_id, boss_ids,
                is_final=(map_id == final_map_id),
            )
            if gate is not None:
                gate_flags[gate["event_id"]] = gate
            ctx.adapter.write_per_map(path_template, map_id, layout)
            placed += 1

        # Persist the gate flags onto the events file (is_gate / is_climax_boss /
        # ensure the boss is among the gate event's monsters) — the runtime reads
        # these event attributes to gate the door and stage the boss fight.
        if gate_flags:
            self._flag_gate_events(ctx, output_dir, gate_flags)

        self._stamp_metadata(ctx)
        logger.info(
            "MazeworldPlacementPhase placed entities in %d maps; %d gate bosses.",
            placed, len(gate_flags),
        )

    # ------------------------------------------------------------------
    # Per-map placement
    # ------------------------------------------------------------------

    def _place_one(self, ctx: Any, map_id: str, map_obj: Any, layout: Any) -> None:
        grid = layout.grid
        player_start = tuple(layout.player_start) if layout.player_start else None
        door_position = tuple(layout.door_position) if layout.door_position else None
        entities = list(getattr(map_obj, "entities", []))

        placed = place_entities(
            grid,
            entities,
            random.Random(self._map_seed(ctx, map_id)),
            exclude=[c for c in (player_start, door_position) if c is not None],
        )

        # --- Quests: id list only ------------------------------------------
        quest_ids: list[int] = []
        for stub in entities:
            if stub.entity_type != "quest":
                continue
            qid = _as_int_id(stub.entity_id)
            if qid is not None:
                quest_ids.append(qid)

        layout.npc_positions = placed["npc_positions"]
        layout.event_positions = placed["event_positions"]
        layout.item_placements = placed["item_placements"]
        layout.quest_ids = quest_ids
        # gate_encounter_id is set later by _designate_gate (run loop), which
        # picks the boss combat tile guarding the door.

    # ------------------------------------------------------------------
    # Gate / boss designation
    # ------------------------------------------------------------------

    @staticmethod
    def _load_events_by_id(output_dir: Any) -> dict[int, dict]:
        path = Path(output_dir) / "events" / "events.json"
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
        out: dict[int, dict] = {}
        for e in data if isinstance(data, list) else []:
            eid = _as_int_id(e.get("id"))
            if eid is not None:
                out[eid] = e
        return out

    @staticmethod
    def _load_boss_monster_ids(output_dir: Any) -> set[int]:
        path = Path(output_dir) / "monsters" / "monsters.json"
        if not path.exists():
            return set()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return set()
        vals = data.values() if isinstance(data, dict) else data
        return {
            _as_int_id(m.get("id"))
            for m in vals
            if m.get("is_boss") and _as_int_id(m.get("id")) is not None
        }

    def _designate_gate(
        self,
        map_obj: Any,
        layout: Any,
        events_by_id: dict[int, dict],
        boss_ids: set[int],
        *,
        is_final: bool,
    ) -> dict | None:
        """Pick this room's gate: the combat event guarding the door, hosting the
        room's boss. Sets layout.gate_encounter_id and nudges the door adjacent
        to the gate tile. Returns gate flag info, or None if no combat event."""
        event_positions = list(getattr(layout, "event_positions", []) or [])
        if not event_positions:
            return None

        # This room's boss monster id (a room monster that is a boss).
        room_monster_ids = {
            _as_int_id(s.entity_id)
            for s in getattr(map_obj, "entities", [])
            if s.entity_type == "monster"
        }
        room_boss = next((b for b in boss_ids if b in room_monster_ids), None)

        # Combat events placed in this room.
        combat = [
            ep for ep in event_positions
            if events_by_id.get(ep.get("event_id"), {}).get("type") == "combat"
        ]
        if not combat:
            return None

        door = tuple(layout.door_position) if getattr(layout, "door_position", None) else None

        def _dist(ep: dict) -> int:
            if door is None:
                return 0
            return abs(ep["x"] - door[0]) + abs(ep["y"] - door[1])

        # Prefer a combat event that already hosts the boss; else the combat
        # event nearest the door (the boss is added to it below).
        boss_combat = [
            ep for ep in combat
            if room_boss is not None
            and room_boss in (events_by_id.get(ep["event_id"], {}).get("monster_ids") or [])
        ]
        gate_ep = min(boss_combat or combat, key=_dist)
        gate_id = gate_ep["event_id"]

        layout.gate_encounter_id = gate_id
        self._move_door_adjacent(layout, gate_ep["x"], gate_ep["y"])

        return {"event_id": gate_id, "is_climax_boss": is_final, "boss_id": room_boss}

    @staticmethod
    def _move_door_adjacent(layout: Any, gx: int, gy: int) -> None:
        """Move the door to an open cell adjacent to the gate tile, so the player
        must pass the boss to leave (mirrors MazeWorld _enrich_tile_meta)."""
        grid = layout.grid
        h = len(grid)
        w = len(grid[0]) if h else 0
        for nx, ny in ((gx + 1, gy), (gx - 1, gy), (gx, gy + 1), (gx, gy - 1)):
            if 0 <= nx < w and 0 <= ny < h and grid[ny][nx] == 0:
                layout.door_position = (nx, ny)
                return

    def _flag_gate_events(
        self, ctx: Any, output_dir: Any, gate_flags: dict[int, dict]
    ) -> None:
        """Rewrite events.json: set is_gate (and is_climax_boss on the final
        room's gate) and ensure the boss is among the gate event's monster_ids.

        Read-modify-write: the read is a direct filesystem load (events.json
        was written by an earlier DatabasePhase); only the write routes
        through ctx.adapter. Keeping events in the Bible instead of
        round-tripping through disk is Phase 1 Bible-completeness work.
        """
        path = Path(output_dir) / "events" / "events.json"
        if not path.exists():
            return
        try:
            events = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        if not isinstance(events, list):
            return
        for e in events:
            eid = _as_int_id(e.get("id"))
            flag = gate_flags.get(eid)
            if flag is None:
                continue
            e["is_gate"] = True
            e["is_climax_boss"] = bool(flag["is_climax_boss"])
            boss_id = flag["boss_id"]
            if boss_id is not None:
                mids = e.get("monster_ids") or []
                if boss_id not in mids:
                    e["monster_ids"] = [boss_id, *mids]
        ctx.adapter.write_json_singleton("events/events.json", events)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _map_seed(ctx: Any, map_id: str) -> int:
        """Deterministic per-map seed so placement is reproducible.

        Uses a stable hash — the builtin ``hash()`` is salted per process
        (PYTHONHASHSEED), which made placement differ run to run.
        """
        base = str(getattr(ctx.config, "seed", "0"))
        return derive_seed(base, "placement", map_id)

    @staticmethod
    def _default_path_template(ctx: Any) -> str:
        output_paths = getattr(ctx.config, "output_paths", {})
        return output_paths.get("maze", "rooms/{map_id}/maze.json")

    def _stamp_metadata(self, ctx: Any) -> None:
        if not isinstance(getattr(ctx.bible, "metadata", None), BibleMetadata):
            ctx.bible.metadata = BibleMetadata()
        ctx.bible.metadata.phases_run.append(self.name)

"""Run the platformer vertical slice end-to-end.

    # deterministic, $0
    uv run --extra platformer python examples/run_platformer_slice.py \
        --backend fake --output-dir /tmp/plat_slice --seed emberfall_001

    # real content
    ANTHROPIC_API_KEY=sk-... uv run --extra platformer --extra anthropic \
        python examples/run_platformer_slice.py --backend anthropic \
        --output-dir /tmp/plat_slice_real

    # a DIFFERENT game from the same template — data only, no code
    uv run --extra platformer python examples/run_platformer_slice.py \
        --backend fake --output-dir /tmp/lava_slice \
        --rules examples/lava_world/game_rules.json \
        --tiles examples/lava_world/tile_types.json

Then review PNGs under <output-dir>/review/ and play a level:

    uv run --extra platformer --extra play \
        python examples/platformer_play.py <output-dir> l1
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT))

from canon import CanonConfig, FakeLLMBackend, LLMClient, run_pipeline  # noqa: E402
from canon.bible.models import Bible  # noqa: E402
from canon.pipeline.runner import PipelineContext  # noqa: E402
from examples.platformer_pack import PlatformerPrompts, compose_pipeline  # noqa: E402

# ---------------------------------------------------------------------------
# Canned fake responses — deterministic, matched to prompt markers.
# Layouts are GENERATED against the advertised grid (dims are schema-rolled
# RANGES now — no fixed coords can fit every roll), verified by the same
# validators real output faces. Rows are relative to the ground row, columns
# to the right edge. Exercises the full op set incl. the design-variety ops:
# pool, raised basin, pit, ledge TIER STACK with a carve notch, platform,
# hazard strip, checkpoint. The {vol}/{haz} names come from the prompt's
# registry-driven vocabulary, so the SAME responder plays any game.
# ---------------------------------------------------------------------------


def _fake_layout(
    width: int, height: int, vol: str, haz: str, difficulty: int = 1
) -> str:
    g = height - 2  # ground row; players stand on g-1
    right = width - 1
    lines = [
        f"floor(0,{right})",
        # Sunken pool, flush with the ground (contained by its banks).
        f"pool({vol},5,7)",
    ]
    if difficulty >= 2:
        lines.append("pit(11,12)")
    lines += [
        # Stepped slope (slopes v1) ramping toward the tier stack —
        # 1-riser stairs the flat physics climbs with ordinary jumps.
        "stairs_up(13,14)",
        # Tier stack with a carved notch — irregular multi-level shape.
        f"ledge(15,21,{g - 3})",
        f"ledge(17,22,{g - 6})",
        f"carve(18,{g - 3},18,{g - 3})",
        # Raised basin: walls form the lip, water fills two rows.
        f"wall(25,{g - 2},{g - 1})",
        f"volume({vol},26,30,{g - 2})",
        f"wall(31,{g - 2},{g - 1})",
        f"platform(34,{g - 3},3)",
    ]
    if difficulty >= 3:
        # Water-as-a-FEATURE ops (free volume, containment-exempt): a
        # swim-up wall standing on the ground and a floating pocket —
        # the whimsy shapes the prompt teaches, exercised at $0. The
        # registry-generic spellings play any game (lava worlds too).
        lines += [
            f"volume_wall({vol},{right - 9},{right - 8},{g - 6})",
            f"volume_block({vol},10,2,12,3)",
        ]
    lines += [
        f"hazard_strip({haz},{right - 6},{right - 5})",
        f"checkpoint({right - 4})",
        "spawn(2)",
        f"exit({right})",
    ]
    return "\n".join(lines)


def _fake_section(
    width: int,
    height: int,
    archetype: str,
    index: int,
    total: int,
    vol: str,
    haz: str,
    has_breakable: bool = False,
    has_water_cloud: bool = False,
    has_reward: bool = False,
) -> str:
    """Deterministic per-SECTION layout for the $0 fake run (the sectioned
    ``stamp_level_collision`` calls the Layout Agent once PER section). Sized
    to the section's LOCAL dims; the FIRST section plants the spawn, NO section
    declares the exit (the stitcher places it). Each archetype draws a
    recognisably different shape so a stitched level's block render shows its
    seams. All terrain stays jump-reachable, so the whole level composes clean
    without bridges (the bridge path has its own test).

    ``climb`` is VERTICAL: ``width`` is the shaft width, ``height`` is the
    section's own ROW-extent, and it builds a staggered ladder of platforms
    rising UP (no floor except the bottom section) — spawn at the bottom, exit
    (summit) placed by the stitcher at the top."""
    g = height - 2  # ground row; players stand on g-1
    right = width - 1
    mid = width // 2

    def _water(x1: int, y1: int, x2: int, y2: int) -> str:
        """A floating volume FEATURE, in the REGISTRY-GENERIC spelling so it
        plays any game (water in the default world, lava in a lava world): a
        puffy swim-up cloud (Chunk E) when the prompt advertises the cloud op,
        else the older rectangular block. Both take the volume tile NAME."""
        op = "volume_cloud" if has_water_cloud else "volume_block"
        return f"{op}({vol},{x1},{y1},{x2},{y2})"

    if archetype == "climb":
        lines = []
        if index == 0:
            # The base: a ground floor + spawn at the very bottom.
            lines += [f"floor(0,{right})", "spawn(2)"]
        # A staggered climbing ladder: platforms every 2 rows, x alternating
        # 2/3 (dx 1, rise 2 — every step jumpable), from near the bottom up.
        y = g - 2
        i = 0
        while y >= 1:
            lines.append(f"platform({2 + i % 2},{y},3)")
            y -= 2
            i += 1
        # A floating water cloud on the RIGHT of the shaft (clear of the
        # left-hand ladder) — a swim-up pocket you rise through mid-climb, and
        # the swimmable water every level is expected to carry.
        if height >= 12 and right - 5 >= 6:
            my = height // 2
            lines.append(_water(right - 5, my - 1, right - 1, my + 1))
        elif height >= 12 and right - 2 >= 0:
            my = height // 2
            lines.append(_water(max(0, right - 3), my - 1, right - 1, my))
        return "\n".join(lines)
    lines = [f"floor(0,{right})"]
    if index == 0:
        lines.append("spawn(2)")
    # Each interior archetype gets a recognisably different shape AND a floating
    # water cloud (a puffy 5x3 pocket at cols 2-6, floating a couple rows above
    # the floor — a swim-up feature clear of the archetype's air features and
    # inside the section's own owned columns, so it survives the seam overlap).
    # It is 3 rows tall (swimmers need a 2x2 body) and containment-exempt (free
    # water FEATURE), exercising the registry-driven volume vocabulary at $0
    # (water in the default world, lava in a lava world) — every non-runway
    # section carries one, so every level has swimmable water.
    cx2 = min(6, right - 1)
    if cx2 - 2 >= 3 and g - 5 >= 1:
        cloud = [_water(2, g - 5, cx2, g - 3)]
    else:
        cloud = [_water(4, g - 4, 6, g - 3)]
    if archetype == "gauntlet" and width >= 16:
        # A short crumbling stretch on the ground (breakable floor, Chunk D —
        # only if the registry has the tile) + floating platform tiers.
        # breakable is a solid foothold, so the level stays reachable; a
        # lingering player would drop through it in play.
        crumble = [f"breakable({mid - 3},{mid - 1})"] if has_breakable else []
        lines += crumble + [
            f"platform({mid},{g - 3},3)",
            f"platform({mid + 4},{g - 5},3)",
        ] + cloud
    elif archetype == "cave" and width >= 14:
        # Ceiling stalactites hanging from the top — enclosed, ground clear.
        lines += [
            f"wall({mid - 3},1,{g - 6})",
            f"wall({mid + 3},1,{g - 6})",
        ] + cloud
        if has_reward and g - 6 >= 1:
            # A SECRET ALCOVE (Chunk E): a reward tucked into a hidden niche
            # near the ceiling between the stalactites — a solid ledge roof
            # over a walled-back pocket, open on the right so a curious player
            # can jump in. Off the floor path (never blocks traversal).
            ax = mid - 1
            lines += [
                f"ledge({ax},{ax + 1},2)",
                f"wall({ax},3,4)",
                f"reward({ax + 1},3)",
            ]
    elif archetype == "islands" and width >= 18:
        # A small walk-jumpable gap + a stepping platform, cloud on the left.
        lines += [
            f"gap({mid + 2},{mid + 3})",
            f"platform({mid + 5},{g - 3},3)",
        ] + cloud
    # Checkpoints are STITCHER-placed from the blueprint now (count scales with
    # the section count, reachable by construction) — sections never emit them.
    return "\n".join(lines)


#: Reference dims per level id (the OLD fixed schema dims) — what the
#: direct-stamp unit tests render against. The live run rolls dims from
#: the schema's difficulty bands and generates layouts to fit.
_REFERENCE_DIMS = {"l1": (48, 16, 1), "l2": (56, 16, 2), "l3": (64, 18, 3)}

#: Rendered for the pack's default game — what tests stamp directly.
_FAKE_LAYOUTS = {
    level_id: _fake_layout(w, h, "water", "spike", d)
    for level_id, (w, h, d) in _REFERENCE_DIMS.items()
}


def _parse_summary_cells(summary: str) -> list[tuple[int, int]]:
    """Invert prompts' cells-summary format ("y=13: x 2-9, 14; y=8: x 3")
    back into cells — the canned responder places enemies the same way a
    real model does: from the prompt, not from hand-tuned coordinates."""
    cells: list[tuple[int, int]] = []
    for part in summary.split(";"):
        m = re.match(r"\s*y=(\d+): x (.+)", part.strip())
        if not m:
            continue
        y = int(m.group(1))
        for rng in m.group(2).split(","):
            rng = rng.strip()
            span = re.match(r"(\d+)-(\d+)$", rng)
            if span:
                cells.extend(
                    (x, y)
                    for x in range(int(span.group(1)), int(span.group(2)) + 1)
                )
            elif rng.isdigit():
                cells.append((int(rng), y))
    return cells


def _fake_spots(msg: str) -> dict[str, list[tuple[int, int]]]:
    """Deterministic land/water placement spots parsed from the placement
    prompt's standable/volume summaries, spread across the level and kept
    clear of the spawn column. Water spots come categorized the way the
    validator judges swim styles: "water" deepest-first (within-style
    bodies), "surface" = top-row cells (surface riders), "pocket" =
    cells inside a 2x2 all-water block (floaters)."""
    stand_m = re.search(r"y from top\): (.+)\n", msg)
    vol_m = re.search(r"swimmers ONLY go here\): (.+)\n", msg)
    spawn_m = re.search(r"Player spawn: \[(\d+), (\d+)\]", msg)
    spawn_x = int(spawn_m.group(1)) if spawn_m else 0

    land_cells = sorted(
        c
        for c in (_parse_summary_cells(stand_m.group(1)) if stand_m else [])
        if abs(c[0] - spawn_x) >= 5
    )
    land: list[tuple[int, int]] = []
    if land_cells:
        seen_x: set[int] = set()
        for idx in (len(land_cells) // 5, len(land_cells) // 2,
                    (4 * len(land_cells)) // 5, 0, len(land_cells) - 1):
            cell = land_cells[idx]
            if cell[0] not in seen_x:
                seen_x.add(cell[0])
                land.append(cell)
            if len(land) == 3:
                break

    # Airborne anchors for flyers (parsed from the placement prompt's
    # open-air hover-line, same as land/water spots), spread and spawn-clear.
    air_m = re.search(r"FLYERS hover here, above the ground\): (.+)\n", msg)
    air_cells = sorted(
        c
        for c in (_parse_summary_cells(air_m.group(1)) if air_m else [])
        if abs(c[0] - spawn_x) >= 5
    )
    air: list[tuple[int, int]] = []
    if air_cells:
        seen_ax: set[int] = set()
        for idx in (len(air_cells) // 5, len(air_cells) // 2,
                    (4 * len(air_cells)) // 5, 0, len(air_cells) - 1):
            cell = air_cells[idx]
            if cell[0] not in seen_ax:
                seen_ax.add(cell[0])
                air.append(cell)
            if len(air) == 3:
                break

    water_cells: list[tuple[int, int]] = []
    if vol_m and vol_m.group(1).strip() != "none":
        for tile_part in vol_m.group(1).split(" | "):
            _name, _, rest = tile_part.partition(": ")
            water_cells.extend(_parse_summary_cells(rest))
    water_set = set(water_cells)

    def _depth(cell: tuple[int, int]) -> int:
        x, y = cell
        d = 1
        while (x, y - d) in water_set:
            d += 1
        return d

    def _distinct_columns(cells: list[tuple[int, int]], count: int) -> list:
        out: list[tuple[int, int]] = []
        for cell in cells:
            if all(cell[0] != w[0] for w in out):
                out.append(cell)
            if len(out) == count:
                break
        return out

    deep_first = sorted(water_set, key=lambda c: (-_depth(c), c))
    # A SURFACE anchor is the top of a water column that also has a same-row
    # neighbour whose top is ALSO open — i.e. a >=2-wide FLAT top a 2-wide
    # surface swimmer (the widest the fake places) actually fits on. This
    # excludes the single-cell "shoulders" a rounded water_cloud exposes (a
    # trimmed corner leaves a lone top cell with submerged neighbours), which
    # the validator's full-footprint check would reject.
    def _flat_surface(c: tuple[int, int]) -> bool:
        # A 2-wide surface body anchors here and extends RIGHT, so the cell AND
        # its right neighbour must both be top-of-water (open air above).
        x, y = c
        return (
            (x, y - 1) not in water_set
            and (x + 1, y) in water_set
            and (x + 1, y - 1) not in water_set
        )

    surface_cells = sorted(c for c in water_set if _flat_surface(c))
    pocket_cells = [
        c
        for c in deep_first
        if any(
            all(
                (px_, py_) in water_set
                for px_ in (bx, bx + 1)
                for py_ in (by, by + 1)
            )
            for bx in (c[0] - 1, c[0])
            for by in (c[1] - 1, c[1])
        )
    ]
    return {
        "land": land,
        "air": air,
        "water": _distinct_columns(deep_first, 2),
        "surface": _distinct_columns(surface_cells, 2),
        "pocket": _distinct_columns(pocket_cells, 2),
    }

def _fake_decor(width: int, height: int) -> list[dict]:
    """Dims-adaptive decor — ceiling pieces near the top, growth near the
    ground, spread across whatever grid was rolled."""
    g = height - 3
    return [
        {"x": 5, "y": 1, "type": "stalactite"},
        {"x": max(1, width // 2), "y": 2, "type": "crystal"},
        {"x": max(1, width // 3), "y": g, "type": "vine"},
        {"x": max(1, width - 6), "y": g, "type": "moss"},
    ]


#: World plan pool — the first N stages are served for --num-stages N;
#: index 0 alone reproduces the classic single-stage ashen_depths tree.
_FAKE_STAGES = [
    {
        "stage_id": "ashen_depths",
        "biome": "caverns",
        "brief": (
            "Collapsed lava tubes below a dead volcano; warm ash drifts "
            "through cold stone corridors."
        ),
        "theme": "ashen lava tubes",
    },
    {
        "stage_id": "bloom_terraces",
        "biome": "meadow",
        "brief": (
            "Sunlit terraces of wildflowers stacked up an old caldera "
            "rim; pollen glows in the light."
        ),
        "theme": "sunlit flower terraces",
    },
    {
        "stage_id": "frostspire_peaks",
        "biome": "peaks",
        "brief": (
            "Wind-scoured ice spires above the clouds; the light is "
            "thin and blue."
        ),
        "theme": "wind-scoured ice spires",
    },
]


def _fake_stage_plans(count: int) -> list[dict]:
    plans = [dict(entry) for entry in _FAKE_STAGES[:count]]
    for i in range(len(plans), count):
        plans.append(
            {
                "stage_id": f"far_wilds_{i + 1}",
                "biome": f"wilds{i + 1}",
                "brief": "Uncharted wilds past the mapped world.",
                "theme": "uncharted wilds",
            }
        )
    return plans


def _rotate_hex(color: str, steps: int) -> str:
    """Deterministic per-stage palette variation: rotate the RGB channels
    — luminance stays in the same ballpark (the contrast/warmth repairs
    are code anyway), but each biome reads distinctly."""
    raw = color.lstrip("#")
    r, g, b = (raw[i : i + 2] for i in (0, 2, 4))
    channels = [r, g, b]
    steps %= 3
    rotated = channels[steps:] + channels[:steps]
    return "#" + "".join(rotated)


_FAKE_ENEMY_NAMES = [
    "Cinder Beetle", "Ash Wraith", "Slag Sentry", "Vent Skimmer",
    "Bloom Hopper", "Frost Urchin", "Gale Drifter",
]

#: Canned style palette, keyed by color_role — ember-dusk hues that pass
#: the contrast/warm-hazard validators for BOTH shipped registries. Roles
#: the prompt asks for that aren't listed get a readable neutral.
_FAKE_PALETTE = {
    "background": "#2b2331",
    "ground": "#6e5a4e",
    "platform": "#b8804a",
    "wall": "#5b4d5e",
    "breakable": "#cdb36a",  # pale cracked ochre — distinct from ground/platform
    "danger": "#e0453a",
    "water": "#3a6ea5",
    "lava": "#e8722c",
    "basalt": "#5a4f5c",
    "mud": "#6b5640",
    "ice": "#bcd8e8",
}


def make_fake_responder():
    def respond(request) -> str:
        msg = request.user_message
        task_match = re.search(r"### TASK: (\w+)", msg)
        task = task_match.group(1) if task_match else ""

        if task == "world":
            stages_match = re.search(r"exactly (\d+) biome STAGE", msg)
            count = int(stages_match.group(1)) if stages_match else 1
            return json.dumps(
                {
                    "title": "Emberfall Hollows",
                    "stages": [
                        {
                            "stage_id": plan["stage_id"],
                            "biome": plan["biome"],
                            "brief": plan["brief"],
                        }
                        for plan in _fake_stage_plans(count)
                    ],
                }
            )
        if task == "stage":
            count_match = re.search(r"exactly (\d+) strings", msg)
            n = int(count_match.group(1)) if count_match else 3
            stage_match = re.search(r"### STAGE: (\w+)", msg)
            sid = stage_match.group(1) if stage_match else "ashen_depths"
            number_match = re.search(r"Stage (\d+) of (\d+)", msg)
            is_last_stage = (
                number_match.group(1) == number_match.group(2)
                if number_match
                else True
            )
            theme = next(
                (p["theme"] for p in _fake_stage_plans(9) if p["stage_id"] == sid),
                "uncharted wilds",
            )
            briefs = [
                "A gentle descent teaching jumps over low ledges.",
                "Broken ground: a collapsed bridge over a glowing chasm.",
                "The deep vents: spike fields and crumbling footholds.",
            ]
            briefs = (briefs * ((n // 3) + 1))[:n]
            # Deliberate framing exception on the WORLD finale only — the
            # rest stay standard (scale is consistent within a game).
            views = ["standard"] * n
            if n and is_last_stage:
                views[-1] = "vista"
            return json.dumps(
                {
                    "theme": theme,
                    "level_briefs": briefs,
                    "level_views": views,
                    "roster_brief": "Ash-crusted vermin and ember constructs.",
                    "effects": [
                        {
                            "name": "particles_falling",
                            "params": {
                                "density": 30, "speed": 40, "size": 2,
                                "drift": 18, "color": "#d8cfc4",
                            },
                        }
                    ],
                }
            )
        if task == "enemy":
            index_match = re.search(r"### INDEX: (\d+)", msg)
            i = int(index_match.group(1)) if index_match else 0
            name = (
                _FAKE_ENEMY_NAMES[i]
                if i < len(_FAKE_ENEMY_NAMES)
                else f"Ember Drone {i}"
            )
            return json.dumps(
                {"name": name, "flavor": f"A {name.lower()} of the ashen depths."}
            )
        if task == "enemy_flavor":
            name_match = re.search(r"### NAME: (.+)", msg)
            name = name_match.group(1) if name_match else "it"
            return json.dumps(
                {"flavor": f"A {name.lower()} remade by the regen winds."}
            )
        if task == "style":
            roles_match = re.search(r"### ROLES: ([a-z_,]+)", msg)
            roles = roles_match.group(1).split(",") if roles_match else []
            stage_match = re.search(r"### STAGE: (\w+)", msg)
            sid = stage_match.group(1) if stage_match else ""
            plans = [p["stage_id"] for p in _fake_stage_plans(9)]
            steps = plans.index(sid) if sid in plans else 0
            # Structural + background hues rotate per biome (distinct
            # looks); hazard/volume roles keep their canonical readable
            # hues everywhere.
            rotate = {"background", "ground", "platform", "wall"}
            return json.dumps(
                {
                    "palette": {
                        role: (
                            _rotate_hex(_FAKE_PALETTE.get(role, "#8a8a8a"), steps)
                            if role in rotate
                            else _FAKE_PALETTE.get(role, "#8a8a8a")
                        )
                        for role in roles
                    }
                }
            )
        if task == "layout":
            # The prompt advertises the game's registry vocabulary AND the
            # rolled grid — parse both, so the same canned generator plays
            # emberfall (water/spike), a lava world, and any rolled dims.
            vol_match = re.search(r"Volume tiles for volume\(\): (\w+)", msg)
            haz_match = re.search(r"Hazard tiles for hazard_strip\(\): (\w+)", msg)
            grid_match = re.search(r"Grid: (\d+) wide x (\d+) tall", msg)
            diff_match = re.search(r'"difficulty": (\d+)', msg)
            width, height = (
                (int(grid_match.group(1)), int(grid_match.group(2)))
                if grid_match
                else (48, 16)
            )
            vol = vol_match.group(1) if vol_match else "water"
            haz = haz_match.group(1) if haz_match else "spike"
            # Sectioned levels: one layout prompt PER section carries a
            # "### SECTION: <archetype> (<i> of <n>)" marker + LOCAL dims.
            # Emit a per-section sub-layout so the whole stitched level is
            # exercised at $0.
            sec_match = re.search(
                r"### SECTION: (\w+) \((\d+) of (\d+)\)", msg
            )
            if sec_match:
                return _fake_section(
                    width, height,
                    archetype=sec_match.group(1),
                    index=int(sec_match.group(2)) - 1,
                    total=int(sec_match.group(3)),
                    vol=vol, haz=haz,
                    # Only emit an op when the prompt advertises it (the
                    # vocabulary gates it) — a game without a breakable tile
                    # (e.g. the lava world) must not receive breakable(); a
                    # game without volume tiles gets no cloud. The cloud op is
                    # advertised as water_cloud (water game) OR volume_cloud
                    # (lava game) — accept either spelling.
                    has_breakable="breakable(x1,x2)" in msg,
                    has_water_cloud=(
                        "water_cloud(x1,y1,x2,y2)" in msg
                        or "volume_cloud(name,x1,y1,x2,y2)" in msg
                    ),
                    has_reward="reward(x,y)" in msg,
                )
            return _fake_layout(
                width, height, vol=vol, haz=haz,
                difficulty=int(diff_match.group(1)) if diff_match else 1,
            )
        if task == "placement":
            roster_match = re.search(
                r"roster \(id, archetype, size, rarity, behavior\): (\[.*?\])\n",
                msg,
            )
            roster = json.loads(roster_match.group(1)) if roster_match else []
            # Variant vocabulary comes from the prompt (data-driven): mark
            # the first placements with the offered names, elite/champion
            # first for the default game's canonical look.
            offer_match = re.search(r'"variant": one of \[(.*?)\]', msg)
            offered = re.findall(r"'(\w+)'", offer_match.group(1)) if offer_match else []
            order = [n for n in ("elite", "champion") if n in offered]
            order += sorted(set(offered) - set(order))
            # Rarity caps come from the prompt too ("at most 1 'rare' per
            # level") — the canned agent respects them like a real one.
            caps = {
                tier: int(cap)
                for cap, tier in re.findall(r"at most (\d+) '(\w+)' per level", msg)
            }
            rarity_used: dict[str, int] = {}
            spots = _fake_spots(msg)
            land = list(spots["land"])
            air = list(spots["air"])
            pools = {
                "": list(spots["water"]),
                "within": list(spots["water"]),
                "surface": list(spots["surface"]),
                "float": list(spots["pocket"]),
            }
            placements = []
            # Archetype-aware: swimmers into the water spots their SWIM
            # STYLE validates against (surface riders on the top row,
            # floaters in a 2x2 pocket, within-bodies deepest-first);
            # everyone else on land — big bodies take the ground-row spot
            # (open air above), like the prompt teaches; earliest
            # placements take the variant vocabulary.
            for entry in roster:
                rarity = str(entry.get("rarity", "") or "")
                cap = caps.get(rarity)
                if cap is not None and rarity_used.get(rarity, 0) >= cap:
                    continue
                if entry["archetype"] == "swimmer":
                    style = str(
                        (entry.get("behavior") or {}).get("swim_style", "")
                    )
                    pool = pools.get(style, pools[""])
                elif entry["archetype"] == "flyer":
                    pool = air  # airborne anchors above the ground
                else:
                    pool = land
                if not pool:
                    continue
                if pool is land and float(entry.get("size", 1.0)) > 1.0:
                    spot = max(pool, key=lambda c: (c[1], -c[0]))
                    pool.remove(spot)
                    x, y = spot
                else:
                    x, y = pool.pop(0)
                if rarity:
                    rarity_used[rarity] = rarity_used.get(rarity, 0) + 1
                placement = {"enemy_id": entry["id"], "x": x, "y": y}
                if len(placements) < len(order):
                    placement["variant"] = order[len(placements)]
                placements.append(placement)
            return json.dumps({"placements": placements})
        if task == "decor":
            grid_match = re.search(r"Grid: (\d+) wide x (\d+) tall", msg)
            width, height = (
                (int(grid_match.group(1)), int(grid_match.group(2)))
                if grid_match
                else (48, 16)
            )
            return json.dumps({"decor": _fake_decor(width, height)})
        raise ValueError(f"Fake responder: unrecognized prompt task {task!r}.")

    return respond


def build_backend(kind: str, model: str | None):
    if kind == "fake":
        return FakeLLMBackend(make_fake_responder())
    if kind == "anthropic":
        from canon.backends.anthropic import AnthropicBackend

        return AnthropicBackend(model=model) if model else AnthropicBackend()
    raise SystemExit(f"Unknown --backend: {kind!r}")


def main() -> None:
    # INFO-level logging so successful generations are visible, not just
    # failures (mirrors run_mazeworld_full.py); quiet the HTTP chatter.
    import logging

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("anthropic").setLevel(logging.WARNING)

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=["fake", "anthropic"], default="fake")
    parser.add_argument("--model", default=None)
    parser.add_argument("--output-dir", default="./plat_slice_output")
    parser.add_argument("--seed", default="emberfall_001")
    parser.add_argument(
        "--num-stages", type=int, default=3,
        help="Biome stages in the world (each = a world-map area whose "
        "levels share palette/tileset/backdrop/music/props). Levels are "
        "numbered globally across stages (l1..lN); in-game they display "
        "as stage-local names (1-1..3-3).",
    )
    parser.add_argument(
        "--num-levels", type=int, default=3,
        help="Levels PER STAGE.",
    )
    parser.add_argument(
        "--num-enemies", type=int, default=7,
        help="Size of the WORLD enemy pool (ecology: each definition "
        "rolls rarity and habitats; stage rosters filter the pool by "
        "biome).",
    )
    parser.add_argument(
        "--engine", choices=["json", "godot"], default="json",
        help="godot: use GodotOutputAdapter and emit a playable Godot "
        "project into the output dir (open it in Godot 4.3+).",
    )
    parser.add_argument(
        "--rules", default=None,
        help="Path to a game_rules.json (defaults to the pack's). Copy the "
        "pack file and edit it to make a different game.",
    )
    parser.add_argument(
        "--tiles", default=None,
        help="Path to a tile_types.json registry (defaults to the pack's). "
        "New volumes/hazards are data entries here, not code.",
    )
    parser.add_argument(
        "--variants", default=None,
        help="Path to a variants.json enemy-variant vocabulary (defaults "
        "to the pack's).",
    )
    parser.add_argument(
        "--combat", default=None,
        help="Path to a combat.json tuning file — hearts, stomp damage, "
        "bounce, i-frames, spawn-safety radius as per-game data "
        "(defaults to the pack's). Combat POLICY toggles (checkpoint "
        "enemy reset, spawn grace) live in game_rules.json.",
    )
    parser.add_argument(
        "--image-backend", choices=["none", "fake", "fal", "local"],
        default="none",
        help="Tilesheet art source (default none = deterministic "
        "placeholder squares). fal/local generate one texture per tile "
        "seeded by style/<stage>/style.json — fal is PAID and only ever "
        "used when this flag says so; fake exercises the diffusion path "
        "deterministically at $0.",
    )
    parser.add_argument(
        "--image-model", default=None,
        help="Model id for the image backend (default: the backend's, "
        "e.g. fal-ai/nano-banana).",
    )
    parser.add_argument(
        "--music-backend", choices=["none", "fake", "lyria"],
        default="none",
        help="Stage music theme source (default none = silent). lyria is "
        "PAID (GOOGLE_API_KEY) and only ever used when this flag says "
        "so; fake exercises the audio path deterministically at $0.",
    )
    parser.add_argument(
        "--sfx-backend", choices=["none", "fake", "elevenlabs"],
        default="none",
        help="Sound-effect source for the closed event set (jump/"
        "checkpoint/death/win). elevenlabs is PAID (ELEVENLABS_API_KEY) "
        "and only ever used when this flag says so.",
    )
    parser.add_argument(
        "--vlm-backend", choices=["none", "fake", "anthropic"],
        default="none",
        help="Vision judge for the end-of-pipeline QA loop (default none "
        "= no QA). Per level it compares the block render (analytic "
        "truth) against the skinned render and writes structured "
        "fidelity/readability/style verdicts to review/<stage>/"
        "qa_report.json; failing verdicts become manifest warnings. It "
        "NEVER regenerates anything — it may suggest mark-only targets. "
        "anthropic is PAID (ANTHROPIC_API_KEY) and only ever used when "
        "this flag says so; fake exercises the whole loop "
        "deterministically at $0.",
    )
    parser.add_argument(
        "--vlm-model", default=None,
        help="Model id for the VLM judge (default: the backend's).",
    )
    parser.add_argument(
        "--graphics", default=None,
        help="Path to a graphics.json spec — target resolution + art "
        "style as per-game data (defaults to the pack's 32px crisp "
        "pixel art). Examples proving the swap: "
        "examples/graphics_specs/{snes_pixel,rendered_hd}.json.",
    )
    parser.add_argument(
        "--orchestrate", action="store_true",
        help="Run through the Phase 2 DAG orchestrator instead of the "
        "sequential pipeline: persists bible.json into the output tree "
        "(node state lives there), skips DONE nodes on re-run, and "
        "re-runs exactly the stale steps after you hand-edit a layer "
        "file (per-step regen).",
    )
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    config = CanonConfig(seed=args.seed, output_dir=output_dir)

    adapter = None
    if args.engine == "godot":
        from canon.adapters import GodotOutputAdapter

        adapter = GodotOutputAdapter(output_dir)

    ctx = PipelineContext(
        bible=Bible.empty(seed=args.seed),
        config=config,
        rng=random.Random(args.seed),
        llm=LLMClient(build_backend(args.backend, args.model)),
        prompts=PlatformerPrompts(),
        adapter=adapter,
    )
    from examples.platformer_pack.combat import load_combat
    from examples.platformer_pack.rules import load_rules
    from examples.platformer_pack.tiles import load_tiles
    from examples.platformer_pack.variants import load_variants

    rules = load_rules(args.rules) if args.rules else load_rules()
    tiles = load_tiles(args.tiles) if args.tiles else load_tiles()
    variants = load_variants(args.variants) if args.variants else load_variants()
    combat = load_combat(args.combat) if args.combat else load_combat()
    from examples.platformer_pack.audio_phases import (
        build_music_producer,
        build_sfx_producer,
    )
    from examples.platformer_pack.graphics import load_graphics
    from examples.platformer_pack.tileset_art import build_image_producer
    from examples.platformer_pack.vlm_qa import build_vlm_judge

    image_producer = build_image_producer(args.image_backend, args.image_model)
    music_producer = build_music_producer(args.music_backend)
    sfx_producer = build_sfx_producer(args.sfx_backend)
    vlm_judge = build_vlm_judge(args.vlm_backend, args.vlm_model)
    graphics = load_graphics(args.graphics) if args.graphics else load_graphics()
    if args.orchestrate:
        from canon.pipeline.orchestrator import detect_edits
        from examples.platformer_pack.dag import run_orchestrated

        bible_path = output_dir / "bible.json"
        if bible_path.exists():
            # Resume/regen: reload state, flag hand-edited layers so the
            # scheduler re-runs exactly their stale descendants.
            ctx.bible = Bible.load(bible_path)
            edits = detect_edits(ctx.bible, output_dir)
            if edits.user_edited:
                print(f"Edited (kept as-is): {edits.user_edited}")
                print(f"Stale (regenerating): {edits.stale}")
        report = run_orchestrated(
            ctx, persist_path=bible_path,
            num_levels=args.num_levels, num_enemies=args.num_enemies,
            num_stages=args.num_stages,
            engine=args.engine, rules=rules, tiles=tiles, variants=variants,
            image_producer=image_producer, graphics=graphics,
            music_producer=music_producer, sfx_producer=sfx_producer,
            combat=combat, vlm_judge=vlm_judge,
        )
        print(
            f"\nOrchestrated: {len(report.done)} node(s) ran, "
            f"{len(report.skipped)} skipped"
            + (f", ESCALATED: {report.escalated}" if report.escalated else "")
        )
        print(
            "Per-step regen: hand-edit a layer file (e.g. "
            f"{output_dir}/level/<stage>/l2/collision.npz), re-run this "
            "same command — only that level's stale steps regenerate."
        )
    else:
        phases = compose_pipeline(
            num_levels=args.num_levels, num_enemies=args.num_enemies,
            num_stages=args.num_stages,
            engine=args.engine, rules=rules, tiles=tiles, variants=variants,
            image_producer=image_producer, graphics=graphics,
            music_producer=music_producer, sfx_producer=sfx_producer,
            combat=combat, vlm_judge=vlm_judge,
        )
        run_pipeline(phases, ctx)

    warnings = ctx.artifacts.get("slice_warnings", [])
    if warnings:
        print(f"\n!! {len(warnings)} generation warning(s) — content fell "
              "back or was dropped (also in manifest.json):")
        for message in warnings:
            print(f"   - {message}")

    print(f"\nSlice generated at {output_dir}/")
    print(f"  Review PNGs:  {output_dir}/review/")
    if args.engine == "godot":
        # A paste-able command (absolute path) — no leaving the canon
        # dir, no hunting for project.godot in a file dialog.
        print(f"  Godot:        godot --path {output_dir.resolve()}")
        print(
            f"                (one level: PLAT_LEVEL=l2 godot --path "
            f"{output_dir.resolve()})"
        )
    print(
        "  Pygame:       uv run --extra platformer --extra play "
        f"python examples/platformer_play.py {output_dir} l1"
    )


if __name__ == "__main__":
    main()

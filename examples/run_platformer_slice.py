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
        f"hazard_strip({haz},{right - 6},{right - 5})",
        f"checkpoint({right - 4})",
        "spawn(2)",
        f"exit({right})",
    ]
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
    clear of the spawn column. Water spots come DEEPEST-FIRST (a sized
    swimmer needs rows of water for its whole body — what the prompt now
    teaches); the placement loop hands ground-row spots to big-bodied
    land enemies for the same reason."""
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

    deep_first = sorted(water_set, key=lambda c: (-_depth(c), c))
    water: list[tuple[int, int]] = []
    for cell in deep_first:
        if all(cell[0] != w[0] for w in water):
            water.append(cell)
        if len(water) == 2:
            break
    return {"land": land, "water": water}

_FAKE_DECOR = {
    "l1": [
        {"x": 6, "y": 2, "type": "stalactite"},
        {"x": 22, "y": 8, "type": "crystal"},
        {"x": 35, "y": 11, "type": "vine"},
    ],
    "l2": [
        {"x": 10, "y": 3, "type": "stalactite"},
        {"x": 33, "y": 10, "type": "vine"},
        {"x": 50, "y": 10, "type": "moss"},
    ],
    "l3": [
        {"x": 8, "y": 4, "type": "stalactite"},
        {"x": 42, "y": 11, "type": "crystal"},
        {"x": 57, "y": 13, "type": "vine"},
    ],
}

_FAKE_ENEMY_NAMES = ["Cinder Beetle", "Ash Wraith", "Slag Sentry", "Vent Skimmer"]

#: Canned style palette, keyed by color_role — ember-dusk hues that pass
#: the contrast/warm-hazard validators for BOTH shipped registries. Roles
#: the prompt asks for that aren't listed get a readable neutral.
_FAKE_PALETTE = {
    "background": "#2b2331",
    "ground": "#6e5a4e",
    "platform": "#b8804a",
    "wall": "#5b4d5e",
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
        level_match = re.search(r"### LEVEL: (\w+)", msg)
        level_id = level_match.group(1) if level_match else "l1"

        if task == "world":
            return json.dumps(
                {
                    "title": "Emberfall Hollows",
                    "stage_id": "ashen_depths",
                    "stage_brief": (
                        "Collapsed lava tubes below a dead volcano; warm ash "
                        "drifts through cold stone corridors."
                    ),
                }
            )
        if task == "stage":
            count_match = re.search(r"exactly (\d+) strings", msg)
            n = int(count_match.group(1)) if count_match else 3
            briefs = [
                "A gentle descent teaching jumps over low ledges.",
                "Broken ground: a collapsed bridge over a glowing chasm.",
                "The deep vents: spike fields and crumbling footholds.",
            ]
            briefs = (briefs * ((n // 3) + 1))[:n]
            # Deliberate framing exception on the finale only — the rest
            # stay standard (scale is consistent within a game).
            views = ["standard"] * (n - 1) + ["vista"] if n else []
            return json.dumps(
                {
                    "theme": "ashen lava tubes",
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
            return json.dumps(
                {
                    "palette": {
                        role: _FAKE_PALETTE.get(role, "#8a8a8a")
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
            return _fake_layout(
                width, height,
                vol=vol_match.group(1) if vol_match else "water",
                haz=haz_match.group(1) if haz_match else "spike",
                difficulty=int(diff_match.group(1)) if diff_match else 1,
            )
        if task == "placement":
            roster_match = re.search(
                r"roster \(id, archetype, size, behavior\): (\[.*?\])\n", msg
            )
            roster = json.loads(roster_match.group(1)) if roster_match else []
            # Variant vocabulary comes from the prompt (data-driven): mark
            # the first placements with the offered names, elite/champion
            # first for the default game's canonical look.
            offer_match = re.search(r'"variant": one of \[(.*?)\]', msg)
            offered = re.findall(r"'(\w+)'", offer_match.group(1)) if offer_match else []
            order = [n for n in ("elite", "champion") if n in offered]
            order += sorted(set(offered) - set(order))
            spots = _fake_spots(msg)
            land = list(spots["land"])
            water = list(spots["water"])
            placements = []
            # Archetype-aware: swimmers into volume spots (deepest
            # first), everyone else on land — big bodies take the
            # ground-row spot (open air above), like the prompt teaches;
            # earliest placements take the variant vocabulary.
            for entry in roster:
                pool = water if entry["archetype"] == "swimmer" else land
                if not pool:
                    continue
                if pool is land and float(entry.get("size", 1.0)) > 1.0:
                    spot = max(pool, key=lambda c: (c[1], -c[0]))
                    pool.remove(spot)
                    x, y = spot
                else:
                    x, y = pool.pop(0)
                placement = {"enemy_id": entry["id"], "x": x, "y": y}
                if len(placements) < len(order):
                    placement["variant"] = order[len(placements)]
                placements.append(placement)
            return json.dumps({"placements": placements})
        if task == "decor":
            return json.dumps({"decor": _FAKE_DECOR.get(level_id, [])})
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
    parser.add_argument("--num-levels", type=int, default=3)
    parser.add_argument("--num-enemies", type=int, default=4)
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

    image_producer = build_image_producer(args.image_backend, args.image_model)
    music_producer = build_music_producer(args.music_backend)
    sfx_producer = build_sfx_producer(args.sfx_backend)
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
            engine=args.engine, rules=rules, tiles=tiles, variants=variants,
            image_producer=image_producer, graphics=graphics,
            music_producer=music_producer, sfx_producer=sfx_producer,
            combat=combat,
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
            engine=args.engine, rules=rules, tiles=tiles, variants=variants,
            image_producer=image_producer, graphics=graphics,
            music_producer=music_producer, sfx_producer=sfx_producer,
            combat=combat,
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

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
# Layouts are hand-verified against the movement spec (jump 3 up / 4 across;
# volumes are swimmable) and against the schema dims: l1 48x16, l2 56x16,
# l3 64x18. Each exercises the 3a+3b feature set: volume pool, ledge tier,
# checkpoint, variable dims. The {vol}/{haz} slots take the game's tile
# names (parsed from the prompt's registry-driven vocabulary), so the SAME
# responder plays any game the template can express — that's the point.
# ---------------------------------------------------------------------------

_FAKE_LAYOUT_TEMPLATES = {
    # Pools are CONTAINED (GameRules.water_containment): flanking walls
    # form the basin lip — jump over, swim across, climb out.
    "l1": (
        "floor(0,47)\npool({vol},5,7)\nplatform(10,11,4)\nledge(16,21,9)\n"
        "wall(29,12,13)\nwall(37,12,13)\nvolume({vol},30,36,12)\n"
        "hazard_strip({haz},40,41)\ncheckpoint(25)\nspawn(2)\nexit(45)"
    ),
    "l2": (
        "floor(0,20)\nplatform(22,11,2)\nfloor(25,55)\n"
        "wall(29,11,13)\nwall(39,11,13)\nvolume({vol},30,38,11)\n"
        "hazard_strip({haz},46,47)\nledge(48,51,11)\ncheckpoint(43)\n"
        "spawn(2)\nexit(53)"
    ),
    "l3": (
        "floor(0,10)\npit(11,13)\nfloor(14,30)\nhazard_strip({haz},20,22)\n"
        "wall(23,14,15)\nwall(30,14,15)\nvolume({vol},24,29,14)\n"
        "floor(35,63)\nplatform(32,13,2)\nplatform(37,14,2)\n"
        "ledge(40,46,12)\nhazard_strip({haz},50,52)\n"
        "wall(54,15,15)\nwall(61,15,15)\nvolume({vol},55,60,15)\n"
        "checkpoint(36)\nspawn(3)\nexit(62)"
    ),
}

#: Rendered for the pack's default game — what tests stamp directly.
_FAKE_LAYOUTS = {
    level_id: template.format(vol="water", haz="spike")
    for level_id, template in _FAKE_LAYOUT_TEMPLATES.items()
}

#: Hand-verified spots per level: land (standable) and volume cells.
_FAKE_SPOTS = {
    "l1": {"land": [(14, 13), (18, 8), (43, 13)], "water": [(33, 12), (32, 13)]},
    "l2": {"land": [(10, 13), (27, 13), (49, 10)], "water": [(34, 12), (36, 11)]},
    "l3": {"land": [(17, 15), (41, 11), (48, 15)], "water": [(26, 14), (57, 15)]},
}

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
            return json.dumps(
                {
                    "theme": "ashen lava tubes",
                    "level_briefs": briefs,
                    "roster_brief": "Ash-crusted vermin and ember constructs.",
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
            # The prompt advertises the game's registry vocabulary — pick
            # the first volume/hazard name it offers, so the same canned
            # layouts play emberfall (water/spike) or a lava world.
            vol_match = re.search(r"Volume tiles for volume\(\): (\w+)", msg)
            haz_match = re.search(r"Hazard tiles for hazard_strip\(\): (\w+)", msg)
            template = _FAKE_LAYOUT_TEMPLATES.get(
                level_id, _FAKE_LAYOUT_TEMPLATES["l1"]
            )
            return template.format(
                vol=vol_match.group(1) if vol_match else "water",
                haz=haz_match.group(1) if haz_match else "spike",
            )
        if task == "placement":
            roster_match = re.search(
                r"roster \(id, archetype, behavior\): (\[.*?\])\n", msg
            )
            roster = json.loads(roster_match.group(1)) if roster_match else []
            # Variant vocabulary comes from the prompt (data-driven): mark
            # the first placements with the offered names, elite/champion
            # first for the default game's canonical look.
            offer_match = re.search(r'"variant": one of \[(.*?)\]', msg)
            offered = re.findall(r"'(\w+)'", offer_match.group(1)) if offer_match else []
            order = [n for n in ("elite", "champion") if n in offered]
            order += sorted(set(offered) - set(order))
            spots = _FAKE_SPOTS.get(level_id, _FAKE_SPOTS["l1"])
            land = list(spots["land"])
            water = list(spots["water"])
            placements = []
            # Archetype-aware: swimmers into volume spots, everyone else on
            # land; earliest placements take the variant vocabulary.
            for entry in roster:
                pool = water if entry["archetype"] == "swimmer" else land
                if not pool:
                    continue
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
    from examples.platformer_pack.rules import load_rules
    from examples.platformer_pack.tiles import load_tiles
    from examples.platformer_pack.variants import load_variants

    rules = load_rules(args.rules) if args.rules else load_rules()
    tiles = load_tiles(args.tiles) if args.tiles else load_tiles()
    variants = load_variants(args.variants) if args.variants else load_variants()
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
        print(
            f"  Godot:        open {output_dir}/project.godot in "
            "Godot 4.3+ and press Play"
        )
    print(
        "  Pygame:       uv run --extra platformer --extra play "
        f"python examples/platformer_play.py {output_dir} l1"
    )


if __name__ == "__main__":
    main()

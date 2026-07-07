"""Run the platformer vertical slice end-to-end.

    # deterministic, $0
    uv run --extra platformer python examples/run_platformer_slice.py \
        --backend fake --output-dir /tmp/plat_slice --seed emberfall_001

    # real content
    ANTHROPIC_API_KEY=sk-... uv run --extra platformer --extra anthropic \
        python examples/run_platformer_slice.py --backend anthropic \
        --output-dir /tmp/plat_slice_real

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
# Layouts are hand-verified against the movement spec (jump 3 up / 4 across).
# ---------------------------------------------------------------------------

_FAKE_LAYOUTS = {
    "l1": (
        "floor(0,47)\nplatform(10,11,4)\nplatform(18,9,4)\n"
        "spike(30,32)\nspawn(2)\nexit(45)"
    ),
    "l2": (
        "floor(0,18)\nplatform(20,11,2)\nfloor(23,47)\ngap(19,22)\n"
        "spike(35,36)\nspawn(2)\nexit(44)"
    ),
    "l3": (
        "floor(0,8)\npit(9,11)\nfloor(12,28)\nspike(20,22)\npit(29,32)\n"
        "floor(33,47)\nplatform(30,11,2)\nplatform(38,10,3)\n"
        "spawn(3)\nexit(45)"
    ),
}

_FAKE_PLACEMENT_SPOTS = {
    "l1": [(14, 13), (19, 8), (40, 13)],
    "l2": [(10, 13), (26, 13), (40, 13)],
    "l3": [(15, 13), (30, 10), (40, 13)],
}

_FAKE_ENEMY_NAMES = ["Cinder Beetle", "Ash Wraith", "Slag Sentry"]


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
        if task == "layout":
            return _FAKE_LAYOUTS.get(level_id, _FAKE_LAYOUTS["l1"])
        if task == "placement":
            roster_match = re.search(r"roster \(id, archetype, behavior\): (\[.*?\])\n", msg)
            ids = [e["id"] for e in json.loads(roster_match.group(1))] if roster_match else []
            spots = _FAKE_PLACEMENT_SPOTS.get(level_id, _FAKE_PLACEMENT_SPOTS["l1"])
            placements = [
                {"enemy_id": ids[i % len(ids)], "x": x, "y": y}
                for i, (x, y) in enumerate(spots)
                if ids
            ]
            return json.dumps({"placements": placements})
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
    parser.add_argument("--num-enemies", type=int, default=3)
    parser.add_argument(
        "--engine", choices=["json", "godot"], default="json",
        help="godot: use GodotOutputAdapter and emit a playable Godot "
        "project into the output dir (open it in Godot 4.3+).",
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
    phases = compose_pipeline(
        num_levels=args.num_levels, num_enemies=args.num_enemies,
        engine=args.engine,
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

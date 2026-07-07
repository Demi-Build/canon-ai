"""Platformer prompt set (slice).

Every prompt carries a machine-readable ``### TASK: <name>`` marker line so
the FakeLLMBackend responder can dispatch on it, and (where relevant) a
``### LEVEL: <id>`` marker. Agents read their constraints as part of the
prompt (invariant I1) and the Layout Agent emits DSL, never cells (I3).
"""

from __future__ import annotations

import json

from canon.llm.request import LLMRequest
from examples.platformer_pack.movement import PlayerMovementSpec
from examples.platformer_pack.rules import DEFAULT_RULES, GameRules

_SYSTEM = (
    "You are a level and content designer for a 2D side-scrolling platformer. "
    "You respond ONLY in the exact format requested — JSON object or DSL "
    "lines — with no prose, no markdown fences, no commentary."
)


class PlatformerPrompts:
    def world_generation(self, pitch: str, seed: str) -> LLMRequest:
        return LLMRequest(
            system=_SYSTEM,
            user_message=(
                "### TASK: world\n"
                f"Pitch: {pitch}\nSeed flavor: {seed}\n\n"
                "Design the world for a small platformer with exactly ONE "
                "stage. Return a JSON object:\n"
                '{"title": str, "stage_id": str (short snake_case), '
                '"stage_brief": str (2-3 sentences of theme + mood)}'
            ),
            max_tokens=512,
        )

    def stage_generation(
        self, world_title: str, stage_brief: str, num_levels: int, num_enemies: int
    ) -> LLMRequest:
        return LLMRequest(
            system=_SYSTEM,
            user_message=(
                "### TASK: stage\n"
                f"World: {world_title}\nStage brief: {stage_brief}\n\n"
                f"Plan this stage. Return a JSON object:\n"
                '{"theme": str (short), '
                f'"level_briefs": [exactly {num_levels} strings, one sentence '
                "each, escalating difficulty], "
                f'"roster_brief": str (what kinds of {num_enemies} enemies '
                "inhabit this stage)}"
            ),
            max_tokens=768,
        )

    def enemy_generation(
        self, skeleton: dict, theme: str, roster_brief: str, index: int,
        used_names: list[str] | None = None,
        feedback: list[str] | None = None,
    ) -> LLMRequest:
        fb = f"\nPrior attempt rejected: {'; '.join(feedback)}\n" if feedback else ""
        # Each enemy call is independent — the model can't know what it
        # already named unless we tell it (learned from wraith_moth_x).
        used = (
            f"Names already taken (do NOT reuse or lightly vary): "
            f"{', '.join(used_names)}\n"
            if used_names
            else ""
        )
        return LLMRequest(
            system=_SYSTEM,
            user_message=(
                "### TASK: enemy\n"
                f"### INDEX: {index}\n"
                f"Stage theme: {theme}\nRoster brief: {roster_brief}\n"
                f"{used}"
                f"Mechanics (already rolled, do NOT change them): "
                f"{json.dumps(skeleton)}\n{fb}\n"
                "Name and flavor this enemy to fit the theme and its rolled "
                "archetype. Return a JSON object:\n"
                '{"name": str (unique, 1-3 words), "flavor": str (one sentence)}'
            ),
            max_tokens=256,
        )

    def layout_generation(
        self,
        level_id: str,
        brief: str,
        knobs: dict,
        width: int,
        height: int,
        movement: PlayerMovementSpec,
        rules: GameRules = DEFAULT_RULES,
        previous: str | None = None,
        feedback: list[str] | None = None,
    ) -> LLMRequest:
        # Repair, don't re-roll: on retry the model sees its own rejected
        # output next to the diagnosis, so it can patch one design instead
        # of rolling a fresh (differently broken) one each attempt.
        fb = ""
        if feedback:
            prev = (
                f"\nYour previous layout attempt:\n{previous}\n"
                if previous
                else "\n"
            )
            fb = (
                f"{prev}It was rejected because:\n- "
                + "\n- ".join(feedback)
                + "\nReturn a corrected layout, changing as little as possible.\n"
            )
        return LLMRequest(
            system=_SYSTEM,
            user_message=(
                "### TASK: layout\n"
                f"### LEVEL: {level_id}\n"
                f"Brief: {brief}\n"
                f"Difficulty knobs (rolled, treat as targets): {json.dumps(knobs)}\n"
                f"Grid: {width} wide x {height} tall; row 0 is the TOP; the "
                f"ground floor row is {height - 2}; players stand one row "
                "above the surface they walk on.\n"
                f"Player physics: max jump rise {movement.jump_height} cells, "
                f"max jump distance {movement.jump_width} cells. Every gap "
                "and platform step MUST be clearable. Concretely: a gap or "
                f"pit wider than {movement.jump_width - 1} columns is "
                "IMPOSSIBLE without a stepping platform over it; each "
                f"platform must sit within {movement.jump_height} rows above "
                f"and {movement.jump_width} columns of the previous "
                "foothold. Water is swimmable: the player crosses pools "
                "slowly and can exit water with a normal jump.\n"
                f"{fb}\n"
                "Emit the level as DSL ops, one per line, nothing else:\n"
                "floor(x1,x2)  gap(x1,x2)  pit(x1,x2)  platform(x,y,len)  "
                "ledge(x1,x2,y)  wall(x,y1,y2)  spike(x1,x2)  "
                "water(x1,x2,y_surface)  spawn(x)  exit(x)\n"
                f"Rules: start from floor(0,{width - 1}) then carve; exactly "
                "one spawn() near the left and one exit() near the right; "
                "spikes need floor under them; platform/ledge rows are ABOVE "
                "the ground (smaller y = higher); water fills DOWN from its "
                "surface row and needs solid floor beneath (never pour it "
                "over a gap or pit)"
                + (
                    "; every pool must be CONTAINED — put wall() columns at "
                    "both sides of the water (a basin lip the player jumps "
                    "over), unless the pool reaches the level edge"
                    if rules.water_containment == "contained"
                    else ""
                )
                + "; keep the spawn and exit columns clear — "
                "no platform, wall, spike, water, or gap may cover them or "
                "remove the floor beneath them."
            ),
            max_tokens=512,
        )

    def placement_generation(
        self,
        level_id: str,
        brief: str,
        roster: list[dict],
        standable_summary: str,
        max_enemies: int,
        spawn: tuple[int, int] | None = None,
        water_summary: str = "none",
        previous: str | None = None,
        feedback: list[str] | None = None,
    ) -> LLMRequest:
        fb = ""
        if feedback:
            prev = (
                f"\nYour previous placements attempt:\n{previous}\n"
                if previous
                else "\n"
            )
            fb = (
                f"{prev}It was rejected because:\n- "
                + "\n- ".join(feedback)
                + "\nReturn corrected placements, changing as little as possible.\n"
            )
        return LLMRequest(
            system=_SYSTEM,
            user_message=(
                "### TASK: placement\n"
                f"### LEVEL: {level_id}\n"
                f"Brief: {brief}\n"
                f"Enemy roster (id, archetype, behavior): {json.dumps(roster)}\n"
                f"Standable cells (x, y are grid coords, y from top): "
                f"{standable_summary}\n"
                f"Water cells (swimmers ONLY go here): {water_summary}\n"
                f"Player spawn: {list(spawn) if spawn else 'unknown'}\n{fb}\n"
                f"Place 1..{max_enemies} enemies to fit the brief. Spread "
                "them out; put slower enemies on patrol routes and ranged/"
                "sentry types guarding key jumps. Swimmer-archetype enemies "
                "MUST be placed in water cells; every other archetype MUST "
                "be on standable land. Keep every enemy at least 4 columns "
                "away from the player spawn. You may mark AT MOST ONE "
                'placement as elite ("elite": true) — a tougher variant of '
                "its definition. Return a JSON object:\n"
                '{"placements": [{"enemy_id": str, "x": int, "y": int, '
                '"elite": bool (optional)}, ...]}'
            ),
            max_tokens=512,
        )

    def decor_generation(
        self,
        level_id: str,
        brief: str,
        width: int,
        height: int,
        decor_types: tuple[str, ...],
        max_decor: int,
        feedback: list[str] | None = None,
    ) -> LLMRequest:
        fb = (
            "\nPrior decor rejected:\n- " + "\n- ".join(feedback) + "\n"
            if feedback
            else ""
        )
        return LLMRequest(
            system=_SYSTEM,
            user_message=(
                "### TASK: decor\n"
                f"### LEVEL: {level_id}\n"
                f"Brief: {brief}\n"
                f"Grid: {width} wide x {height} tall (y from top).\n{fb}\n"
                f"Scatter 2..{max_decor} foreground decorations to set the "
                "mood — the player passes in front of or behind them; they "
                "never affect gameplay. Put ceiling pieces near the top, "
                "growth near surfaces. Types (closed set): "
                f"{list(decor_types)}. Return a JSON object:\n"
                '{"decor": [{"x": int, "y": int, "type": str}, ...]}'
            ),
            max_tokens=384,
        )

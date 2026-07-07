"""Platformer prompt set (slice).

Every prompt carries a machine-readable ``### TASK: <name>`` marker line so
the FakeLLMBackend responder can dispatch on it, and (where relevant) a
``### LEVEL: <id>`` marker. Agents read their constraints as part of the
prompt (invariant I1) and the Layout Agent emits DSL, never cells (I3).

Since 3b the layout prompt's op vocabulary and the placement prompt's
variant offer are built from the game's tile registry / variant set —
prompts are as data-driven as the validators that enforce them.
"""

from __future__ import annotations

import json

from canon.llm.request import LLMRequest
from examples.platformer_pack.movement import PlayerMovementSpec, max_dx_for_rise
from examples.platformer_pack.rules import DEFAULT_RULES, GameRules
from examples.platformer_pack.tiles import DEFAULT_TILES, TileRegistry
from examples.platformer_pack.variants import DEFAULT_VARIANTS, VariantSet

_SYSTEM = (
    "You are a level and content designer for a 2D side-scrolling platformer. "
    "You respond ONLY in the exact format requested — JSON object or DSL "
    "lines — with no prose, no markdown fences, no commentary."
)


def _volume_blurb(tile) -> str:
    """One human line per volume tile, from its registry params."""
    params = tile.params
    traits = []
    dps = float(params.get("damage_per_second", 0) or 0)
    if dps > 0:
        traits.append(f"DAMAGES the player ({dps}/s — quick dips only)")
    else:
        traits.append("harmless to cross")
    factor = params.get("speed_factor")
    if factor is not None:
        traits.append(f"movement x{factor} inside")
    return f"{tile.name} ({', '.join(traits)})"


class PlatformerPrompts:
    def world_generation(self, pitch: str, seed: str) -> LLMRequest:
        return LLMRequest(
            system=_SYSTEM,
            user_message=(
                "### TASK: world\n"
                f"Pitch: {pitch}\nSeed flavor: {seed}\n\n"
                "Design the world for a small platformer with exactly ONE "
                "stage. Return a JSON object:\n"
                '{"title": str, "stage_id": str (short snake_case name '
                "derived from the stage's THEME — never echo the seed), "
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
                "archetype. The archetype is mechanical truth: a swimmer "
                "LIVES INSIDE liquid pools (not air, not land), a sentry "
                "never moves — the name and flavor must fit that behavior. "
                "Return a JSON object:\n"
                '{"name": str (unique, 1-3 words), "flavor": str (one sentence)}'
            ),
            max_tokens=256,
        )

    def enemy_flavor(
        self,
        name: str,
        archetype: str,
        theme: str,
        current_flavor: str,
        feedback: list[str] | None = None,
    ) -> LLMRequest:
        """Field-level regen (parts of rows): a fresh flavor sentence for
        an EXISTING definition — name and mechanics are locked."""
        fb = (
            "\nPrior attempt rejected:\n- " + "\n- ".join(feedback) + "\n"
            if feedback
            else ""
        )
        return LLMRequest(
            system=_SYSTEM,
            user_message=(
                "### TASK: enemy_flavor\n"
                f"### NAME: {name}\n"
                f"Stage theme: {theme}\n"
                f"Enemy: {name} ({archetype} — mechanics locked, name "
                "locked).\n"
                f"Current flavor (write something DIFFERENT): "
                f"{current_flavor}\n{fb}\n"
                "Write one replacement flavor sentence that fits the name, "
                "archetype behavior, and theme. Return a JSON object:\n"
                '{"flavor": str (one sentence)}'
            ),
            max_tokens=256,
        )

    def style_generation(
        self,
        world_title: str,
        theme: str,
        stage_brief: str,
        role_specs: list[dict],
        background_role: str,
        feedback: list[str] | None = None,
    ) -> LLMRequest:
        fb = (
            "\nPrior palette rejected:\n- " + "\n- ".join(feedback) + "\n"
            if feedback
            else ""
        )
        roles_line = ",".join(spec["role"] for spec in role_specs)
        described = "; ".join(
            f"{spec['role']} (paints {'/'.join(spec['tiles'])}, "
            f"{'/'.join(spec['categories'])})"
            for spec in role_specs
        )
        return LLMRequest(
            system=_SYSTEM,
            user_message=(
                "### TASK: style\n"
                f"### ROLES: {roles_line}\n"
                f"World: {world_title}\nStage theme: {theme}\n"
                f"Stage brief: {stage_brief}\n\n"
                "Design this game's tile color palette — one #rrggbb hex "
                "per role, cohesive with the theme. Roles: "
                f"{described}.\n"
                f"Hard constraints: every role present; {background_role!r} "
                "is the empty-air backdrop, keep it muted; every other "
                "role must clearly contrast it (luminance distance >= 40 "
                "of 255 — an unreadable level is a failed palette); "
                "hazard roles must look dangerous at a glance (warm, red "
                f"over blue).\n{fb}\n"
                'Return a JSON object: {"palette": {"<role>": "#rrggbb", ...}}'
            ),
            max_tokens=512,
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
        tiles: TileRegistry = DEFAULT_TILES,
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

        # Op vocabulary from the game's tile registry — a game with lava
        # and lasers advertises those, one without water never mentions it.
        volumes = tiles.named("volume")
        hazards = tiles.named("hazard")
        ops = [
            "floor(x1,x2)", "gap(x1,x2)", "pit(x1,x2)", "platform(x,y,len)",
            "ledge(x1,x2,y)", "wall(x,y1,y2)", "checkpoint(x)",
            "spawn(x)", "exit(x)",
        ]
        if hazards:
            ops.append("hazard_strip(name,x1,x2)")
        if volumes:
            ops.append("volume(name,x1,x2,y_surface)")
            ops.append("pool(name,x1,x2)")
        vocab_lines = []
        if volumes:
            vocab_lines.append(
                "Volume tiles for volume(): "
                + "; ".join(_volume_blurb(t) for t in volumes)
                + ". The player swims through volumes slowly and exits "
                "with a normal jump."
            )
            # The top real-model failures: pouring a surface ON the
            # occupied ground row, and pouring over pits. Give both
            # correct constructions up front.
            ground = height - 2
            vocab_lines.append(
                f"Pool recipe — two correct shapes: (1) SUNKEN, flush "
                f"with the ground: pool({volumes[0].name},20,25) on solid "
                "floor — the easiest; never pour volume() over a gap or "
                "pit. (2) RAISED basin on top of the floor: the surface "
                f"row must be OPEN AIR (row {ground} is the ground floor "
                f"itself and is occupied): wall(19,{ground - 2},{ground - 1})  "
                f"volume({volumes[0].name},20,25,{ground - 1})  "
                f"wall(26,{ground - 2},{ground - 1})"
            )
        if hazards:
            vocab_lines.append(
                "Hazard tiles for hazard_strip(): "
                + ", ".join(t.name for t in hazards)
                + " (touching one kills — they sit on floor)."
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
                f"max jump distance {movement.jump_width} cells — and RISING "
                "COSTS RANGE: at full rise "
                f"{movement.jump_height} the player clears only "
                f"{max_dx_for_rise(movement, movement.jump_height)} columns "
                "sideways, so high steps must be nearly overhead. Every gap "
                "and platform step MUST be clearable. Concretely: a gap or "
                f"pit wider than {movement.jump_width - 1} columns is "
                "IMPOSSIBLE without a stepping platform over it; each "
                f"platform must sit within {movement.jump_height} rows above "
                "the previous foothold, and the higher the step the closer "
                "it must be.\n"
                + "\n".join(vocab_lines)
                + f"\n{fb}\n"
                "Emit the level as DSL ops, one per line, nothing else:\n"
                + "  ".join(ops) + "\n"
                f"Rules: start from floor(0,{width - 1}) then carve; exactly "
                "one spawn() near the left and one exit() near the right; "
                "place ONE checkpoint(x) on clear floor near the middle "
                "(the player respawns there after dying past it); hazards "
                "need floor under them; platform/ledge rows are ABOVE "
                "the ground (smaller y = higher); volumes fill DOWN from "
                "their surface row and need solid floor beneath (never pour "
                "one over a gap or pit)"
                + (
                    "; every pool must be CONTAINED — put wall() columns at "
                    "both sides of the volume (a basin lip the player jumps "
                    "over), unless the pool reaches the level edge"
                    if rules.water_containment == "contained" and volumes
                    else ""
                )
                + "; keep the spawn, exit, and checkpoint columns clear — "
                "no platform, wall, hazard, volume, or gap may cover them or "
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
        volume_summary: str = "none",
        variants: VariantSet = DEFAULT_VARIANTS,
        rules: GameRules = DEFAULT_RULES,
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
        # Variant offer from the game's vocabulary + GameRules caps.
        variant_offer = ""
        if variants.variants:
            described = []
            for v in variants.variants:
                mults = ", ".join(
                    f"{k} x{m}" for k, m in sorted(v.stat_mults.items())
                )
                cap = rules.variant_caps.get(v.name)
                cap_note = f"at most {cap} per level" if cap else "uncapped"
                described.append(f"{v.name} ({mults or 'cosmetic'}; {cap_note})")
            variant_offer = (
                'You may mark a placement with "variant": one of '
                f"{sorted(variants.by_name)} — tougher versions of their "
                f"definition: {'; '.join(described)}. A champion guarding a "
                "chokepoint reads as a mini-boss. "
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
                f"Volume cells by tile (swimmers ONLY go here): {volume_summary}\n"
                f"Player spawn: {list(spawn) if spawn else 'unknown'}\n{fb}\n"
                f"Place 1..{max_enemies} enemies to fit the brief. Spread "
                "them out; put slower enemies on patrol routes and ranged/"
                "sentry types guarding key jumps. Swimmer-archetype enemies "
                "MUST be placed in volume cells; every other archetype MUST "
                "be on standable land. Keep every enemy at least 4 columns "
                f"away from the player spawn. {variant_offer}"
                "Return a JSON object:\n"
                '{"placements": [{"enemy_id": str, "x": int, "y": int, '
                '"variant": str (optional)}, ...]}'
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

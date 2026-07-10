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
from examples.platformer_pack.combat import DEFAULT_COMBAT, CombatSpec
from examples.platformer_pack.effects import describe_vocabulary as _effects_vocabulary
from examples.platformer_pack.movement import PlayerMovementSpec, max_dx_for_rise
from examples.platformer_pack.rules import DEFAULT_RULES, GameRules
from examples.platformer_pack.tiles import DEFAULT_TILES, TileRegistry
from examples.platformer_pack.variants import DEFAULT_VARIANTS, VariantSet

# Per-task system prompts: every generator is TOLD what artifact it is
# producing and its exact field/arg contract, so the model stops inventing
# extra fields or arguments (the first paid run lost l8 to a 4-arg wall()
# and l4 to water poured on terrain). Kept as constants so a generator's
# system prompt never changes between its base and feedback calls.
_SYSTEM_BASE = (
    "You are a level and content designer for a 2D side-scrolling platformer. "
    "You respond ONLY in the exact format the task requests — a JSON object or "
    "DSL lines — with no prose, no markdown fences, and no commentary."
)
_SYSTEM_WORLD = _SYSTEM_BASE + (
    " This task designs the WORLD and its stages. Return ONLY the JSON keys "
    "the task names — invent no extra fields."
)
_SYSTEM_STAGE = _SYSTEM_BASE + (
    " This task plans ONE stage and its levels. Return ONLY the JSON keys the "
    "task names — invent no extra fields."
)
_SYSTEM_ENEMY = _SYSTEM_BASE + (
    " This task NAMES and FLAVORS one creature whose mechanics are already "
    "rolled and fixed. Return ONLY the JSON keys the task names — never change "
    "or restate the rolled stats, and add no other fields."
)
_SYSTEM_STYLE = _SYSTEM_BASE + (
    " This task chooses a color palette. Return ONLY the JSON keys the task "
    "names — one hex per role, and no extra fields."
)
_SYSTEM_LAYOUT = _SYSTEM_BASE + (
    " This task emits a LEVEL as DSL op lines, one per line. Every op has a "
    "FIXED name and argument COUNT: emit exactly the arguments its signature "
    "lists — never more, never fewer — and never invent an op or an argument "
    "the vocabulary does not define."
)
_SYSTEM_PLACEMENT = _SYSTEM_BASE + (
    " This task PLACES entities on an already-built level. Return ONLY the "
    "JSON the task names, using only the ids and columns it offers."
)
_SYSTEM_DECOR = _SYSTEM_BASE + (
    " This task PLACES decorative pieces. Return ONLY the JSON the task names, "
    "using only the offered kinds and cells."
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
    def world_generation(
        self, pitch: str, seed: str, num_stages: int = 1
    ) -> LLMRequest:
        return LLMRequest(
            system=_SYSTEM_WORLD,
            user_message=(
                "### TASK: world\n"
                f"Pitch: {pitch}\nSeed flavor: {seed}\n\n"
                f"Design the world for a platformer with exactly "
                f"{num_stages} biome STAGE(S), ordered easy to hard — a "
                "world map the player travels stage by stage. Each stage "
                "is one biome (its levels share a palette, tileset, and "
                "music). Make the biomes visually and thematically "
                "DISTINCT from each other. Return a JSON object:\n"
                '{"title": str, "stages": [exactly '
                f"{num_stages} objects, in play order: "
                '{"stage_id": str (short snake_case name derived from the '
                "stage's THEME — never echo the seed), "
                '"biome": str (one lowercase word: forest, caves, peaks, '
                "ruins, shore, ...), "
                '"brief": str (2-3 sentences of theme + mood)}]}'
            ),
            max_tokens=768,
        )

    def stage_generation(
        self, world_title: str, stage_id: str, stage_brief: str,
        num_levels: int, num_enemies: int,
        stage_number: int = 1, num_stages: int = 1,
    ) -> LLMRequest:
        return LLMRequest(
            system=_SYSTEM_STAGE,
            user_message=(
                "### TASK: stage\n"
                f"### STAGE: {stage_id}\n"
                f"World: {world_title}\n"
                f"Stage {stage_number} of {num_stages}.\n"
                f"Stage brief: {stage_brief}\n\n"
                f"Plan this stage. Return a JSON object:\n"
                '{"theme": str (short), '
                f'"level_briefs": [exactly {num_levels} strings, one sentence '
                "each, escalating difficulty. Give each level its own "
                "IDENTITY within the biome — a long run, a vertical climb, "
                "a jump gauntlet, an inside-to-outside journey], "
                f'"level_views": [exactly {num_levels} strings from '
                '"standard" | "intimate" | "vista" — camera framing per '
                'level. Almost always "standard": scale stays consistent '
                'within a game. Use "vista" ONLY where zooming out is meant '
                'to inspire (a big reveal), "intimate" ONLY for tight, '
                "claustrophobic moments], "
                f'"roster_brief": str (what kinds of creatures inhabit this '
                "biome), "
                '"effects": [0-2 ambient effect records '
                '{"name": str, "params": {...}} fitting the theme, from: '
                f"{_effects_vocabulary()}]}}"
            ),
            max_tokens=768,
        )

    def enemy_generation(
        self, skeleton: dict, theme: str, roster_brief: str, index: int,
        used_names: list[str] | None = None,
        feedback: list[str] | None = None,
        rarity: str = "",
        habitat_desc: str = "",
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
        ecology = ""
        if rarity:
            ecology += f"Rarity: {rarity.upper()}"
            if rarity == "common":
                ecology += " (an everyday creature the player meets constantly)"
            elif rarity == "rare":
                ecology += " (memorable and distinctive — a rare sighting)"
            ecology += "\n"
        if habitat_desc:
            ecology += f"Habitat: {habitat_desc}\n"
        return LLMRequest(
            system=_SYSTEM_ENEMY,
            user_message=(
                "### TASK: enemy\n"
                f"### INDEX: {index}\n"
                f"Home theme: {theme}\nRoster brief: {roster_brief}\n"
                f"{ecology}"
                f"{used}"
                f"Mechanics (already rolled, do NOT change them): "
                f"{json.dumps(skeleton)}\n{fb}\n"
                "Name and flavor this enemy to fit its habitat and rolled "
                "archetype. The archetype is mechanical truth: a swimmer "
                "LIVES IN liquid pools (surface-riders skim the top, "
                "floaters drift through the body), a sentry never moves — "
                "the name and flavor must fit that behavior. Return a "
                "JSON object:\n"
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
            system=_SYSTEM_ENEMY,
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
        stage_id: str = "",
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
        stage_marker = f"### STAGE: {stage_id}\n" if stage_id else ""
        return LLMRequest(
            system=_SYSTEM_STYLE,
            user_message=(
                "### TASK: style\n"
                f"{stage_marker}"
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
            "ledge(x1,x2,y)", "wall(x,y1,y2)", "carve(x1,y1,x2,y2)",
            "stairs_up(x1,x2)", "stairs_down(x1,x2)", "pyramid(x1,x2)",
            "checkpoint(x)", "spawn(x)", "exit(x)",
        ]
        if hazards:
            ops.append("hazard_strip(name,x1,x2)")
        if volumes:
            ops.append("volume(name,x1,x2,y_surface)")
            ops.append("pool(name,x1,x2)")
            ops.append("water_wall(x1,x2,y_top)")
            ops.append("water_block(x1,y1,x2,y2)")
        vocab_lines = [
            # The model conflated wall (3 args) with the 4-arg rectangle ops
            # (l8 emitted a 4-arg wall three times into fallback): show wall
            # ISOLATED with a worked example and the explicit contrast.
            "wall(x, y1, y2): ONE solid column x filled from row y1 to row "
            "y2 — exactly THREE numbers (a single column, two row bounds). "
            "It is NOT a rectangle: carve and water_block take four numbers, "
            "wall takes three. Example: wall(19, 12, 13) fills column 19 at "
            "rows 12-13, a 2-tall pillar.",
            # Shape variety is op work, not coaching: carve notches
            # silhouettes, ledge stacks build tiers — both fully validated.
            "carve(x1,y1,x2,y2): clears a rectangle back to empty air "
            f"(rows 0..{height - 3} only — it cannot cut the ground row). "
            "Use 1-3 cell notches to break up long ledges and vary "
            "silhouettes.",
            "Tiers: stack 2-3 ledge(...) strips of DIFFERENT lengths, "
            "3-4 rows apart, offset horizontally — multi-level structures "
            "the player climbs through beat single flat runs.",
            "Stepped slopes: stairs_up(x1,x2) climbs one block per column "
            "(stairs_down descends, pyramid(x1,x2) rises then falls) — "
            "stamped ON existing ground floor; every step is a jumpable "
            "1-riser. Use them to ramp up to ledges or break up flat runs.",
        ]
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
            # Water-as-a-FEATURE (playtest direction): big deliberate
            # shapes, not obligatory puddles — and fully optional.
            vocab_lines.append(
                "Water is OPTIONAL — a mostly-airborne level (jump "
                "gauntlet, canopy hop) is BETTER with no water than with "
                "a forced pool; treat the rolled pool count as a maximum, "
                "not a quota. When water fits, prefer BIG deliberate "
                "features over puddles: water_wall(x1,x2,y_top) drops a "
                "full column of water from y_top down to the terrain — a "
                "waterfall/shaft the player swims UP and leaps out of "
                "(1-3 columns wide, several rows tall; over a pit it runs "
                "out the bottom and sinking too deep is a fall death — a "
                "deliberate spout hazard). water_block(x1,y1,x2,y2) "
                "floats a pocket of water in open air (whimsical worlds "
                "float their water; make it at least 2x2 so it reads as "
                "a feature). Both are exempt from the basin/containment "
                "rules — they are free-standing by design."
            )
        if hazards:
            vocab_lines.append(
                "Hazard tiles for hazard_strip(): "
                + ", ".join(t.name for t in hazards)
                + " (touching one kills — they sit on floor)."
            )
        return LLMRequest(
            system=_SYSTEM_LAYOUT,
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

    @staticmethod
    def _rarity_caps_line(rules: GameRules) -> str:
        caps = getattr(rules, "rarity_caps", {}) or {}
        if not caps:
            return "no per-level rarity caps"
        return ", ".join(
            f"at most {cap} {tier!r} per level"
            for tier, cap in sorted(caps.items())
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
        combat: CombatSpec = DEFAULT_COMBAT,
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
            system=_SYSTEM_PLACEMENT,
            user_message=(
                "### TASK: placement\n"
                f"### LEVEL: {level_id}\n"
                f"Brief: {brief}\n"
                f"Enemy roster (id, archetype, size, rarity, behavior): "
                f"{json.dumps(roster)}\n"
                f"Standable cells (x, y are grid coords, y from top): "
                f"{standable_summary}\n"
                f"Volume cells by tile (swimmers ONLY go here): {volume_summary}\n"
                f"Player spawn: {list(spawn) if spawn else 'unknown'}\n{fb}\n"
                f"Place 1..{max_enemies} enemies to fit the brief. Spread "
                "them out; put slower enemies on patrol routes and ranged/"
                "sentry types guarding key jumps. Swimmer-archetype enemies "
                "MUST be placed in volume cells — and their behavior's "
                'swim_style matters: "surface" riders go on the water\'s '
                'TOP row (open air above), "float" drifters need a deep '
                '2x2 pocket, "within" swimmers just need their body in '
                "water. Every other archetype MUST be on standable land. "
                "Sizes are in cells: a size-2.0 body "
                "occupies TWO columns (x and x+1 both standable) and two "
                "rows of clearance; size 1.5 needs one column but two rows "
                "of headroom — put big enemies on wide open ground. Keep "
                f"every enemy at least {combat.spawn_safety_columns + 1} "
                "columns away from the player spawn. Respect rarity: "
                f"{self._rarity_caps_line(rules)} — commons are your "
                "filler, rares are a highlight. "
                f"{variant_offer}"
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
            system=_SYSTEM_DECOR,
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

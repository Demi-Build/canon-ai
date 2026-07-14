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


def _physics_guidance(movement: PlayerMovementSpec) -> str:
    """The RUN-UP MOMENTUM physics paragraph shared by the whole-level and
    per-section layout prompts (ends with a newline)."""
    return (
        f"Player physics (RUN-UP MOMENTUM): max jump rise "
        f"{movement.jump_height} cells (RISING COSTS RANGE — at full "
        f"rise the player clears only "
        f"{max_dx_for_rise(movement, movement.jump_height)} columns "
        "sideways, so high steps must be nearly overhead). A "
        f"standing/walking jump clears a gap up to "
        f"{movement.jump_width - 1} columns. A RUNNING jump (the "
        f"player builds speed over ~{int(movement.run_up_cells)} clear "
        "flat columns) clears up to ~6 columns — but ONLY with that "
        "runway: a wide gap right after a wall, ledge, or narrow "
        "platform (no room to build speed) is UNBEATABLE. So keep "
        f"MOST gaps at most {movement.jump_width - 1} columns (ordinary "
        "jumps); you MAY sprinkle in the occasional WIDE (4-6 column) "
        "gap as a deliberate run-and-jump challenge, but ALWAYS give "
        f"~{int(movement.run_up_cells)} clear flat columns of runway "
        "right before it. Any gap wider than ~6 columns needs a "
        "stepping platform. Each platform step must sit within "
        f"{movement.jump_height} rows above the previous foothold, and "
        "the higher the step the closer it must be.\n"
    )


#: A feature_bias FAMILY -> the concrete DSL ops it favours. The archetype
#: vocab (sections.json) speaks in families (floor/platform/gap/...); the
#: section prompt translates them to the ops the agent actually emits, so a
#: cave leans on wall/carve and a gauntlet on platform/gap — distinct
#: character, same legal vocabulary (any op stays available).
_FEATURE_OPS = {
    "floor": "floor",
    "platform": "platform, ledge",
    "gap": "gap, pit",
    "hazard": "hazard_strip",
    "wall": "wall, carve",
    "stairs": "stairs_up, stairs_down, pyramid",
    "water": "pool, water_wall, water_block",
    "cloud": "water_cloud",
    "ledge": "ledge",
    "breakable": "breakable",
    "secret": "reward",
}

_INTENSITY_HINT = {
    "low": "a breather — keep it sparse, easy, and readable",
    "medium": "a steady rhythm of moderate challenges",
    "high": "dense and demanding — pack the challenges close together",
}

_WATER_HINT = {
    "dry": "This section is DRY — place NO water.",
    "optional": "Water is OPTIONAL here — use it only where it fits the shape.",
    "submerged": "This section is largely SUBMERGED — water fills most of it.",
}


def _bias_guidance(feature_bias: dict[str, float]) -> str:
    """Turn a section archetype's ``feature_bias`` weights into a prescriptive
    prompt line: the BACKBONE families (weight >= 3, mapped to their ops), the
    minor accents (0 < weight < 3), and the families to AVOID (weight <= 0).
    Steers the mix without overriding the vocabulary — every op stays legal."""
    if not feature_bias:
        return ""
    ranked = sorted(feature_bias.items(), key=lambda kv: (-kv[1], kv[0]))
    backbone = [k for k, w in ranked if w >= 3]
    minor = [k for k, w in ranked if 0 < w < 3]
    avoid = [k for k, w in ranked if w <= 0]
    ops = lambda fams: ", ".join(_FEATURE_OPS.get(f, f) for f in fams)  # noqa: E731
    parts = []
    if backbone:
        parts.append(f"build it mostly from {ops(backbone)}")
    if minor:
        parts.append(f"sprinkle in {ops(minor)}")
    if avoid:
        parts.append(f"do NOT use {ops(avoid)}")
    if not parts:
        return ""
    return "Feature mix — " + "; ".join(parts) + ".\n"


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

    def _ops_and_vocab(
        self,
        tiles: TileRegistry,
        rules: GameRules,
        height: int,
        width: int = 48,
    ) -> tuple[list[str], list[str]]:
        """The op VOCABULARY and its guidance lines, from the game's tile
        registry — shared by the whole-level :meth:`layout_generation` and the
        per-section :meth:`section_layout` (both advertise the same features;
        a game with lava/lasers offers those, one without water never mentions
        it). Worked-example COLUMNS are anchored to ``width`` so they stay
        in-bounds on a narrow (vertical shaft) section — hardcoded cols 19-26
        overran a 16-26-wide climb and the model copied them into a DslError."""
        volumes = tiles.named("volume")
        hazards = tiles.named("hazard")
        # A safe left anchor for illustrative multi-column examples: the pool
        # recipe spans ex..ex+7, so keep ex+7 < width.
        ex = max(1, min(width // 2, width - 9))
        # Free-water FEATURE ops come in two spellings: the ergonomic water_*
        # aliases (which resolve the "water" tile) and the registry-generic
        # volume_*(name,...) forms. Advertise the ergonomic ones ONLY when the
        # game actually HAS a water tile; a lava world (or any water-less
        # volume game) is offered the generic forms with its own liquid — being
        # told to emit water_* it can't stamp is the bug this guards.
        water_tile = tiles.by_name.get("water")
        has_water = water_tile is not None and water_tile.category == "volume"
        liquid = "water" if has_water else (volumes[0].name if volumes else "water")
        _pfx, _nm = ("water", "") if has_water else ("volume", "name,")
        wall_op = f"{_pfx}_wall({_nm}x1,x2,y_top)"
        block_op = f"{_pfx}_block({_nm}x1,y1,x2,y2)"
        cloud_op = f"{_pfx}_cloud({_nm}x1,y1,x2,y2)"
        ops = [
            "floor(x1,x2)", "gap(x1,x2)", "pit(x1,x2)", "platform(x,y,len)",
            "ledge(x1,x2,y)", "wall(x,y1,y2)", "carve(x1,y1,x2,y2)",
            "stairs_up(x1,x2)", "stairs_down(x1,x2)", "pyramid(x1,x2)",
            "checkpoint(x)", "reward(x,y)", "spawn(x)", "exit(x)",
        ]
        if "breakable" in tiles.by_name:
            ops.append("breakable(x1,x2)")
        if hazards:
            ops.append("hazard_strip(name,x1,x2)")
        if volumes:
            ops.append("volume(name,x1,x2,y_surface)")
            ops.append("pool(name,x1,x2)")
            ops.append(wall_op)
            ops.append(block_op)
            ops.append(cloud_op)
        vocab_lines = [
            # The model conflated wall (3 args) with the 4-arg rectangle ops
            # (l8 emitted a 4-arg wall three times into fallback): show wall
            # ISOLATED with a worked example and the explicit contrast.
            "wall(x, y1, y2): ONE solid column x filled from row y1 to row "
            "y2 — exactly THREE numbers (a single column, two row bounds). "
            f"It is NOT a rectangle: carve and {_pfx}_block take four numbers, "
            f"wall takes three. Example: wall({ex}, {height - 4}, {height - 3}) "
            f"fills column {ex} at rows {height - 4}-{height - 3}, a 2-tall "
            "pillar.",
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
                f"with the ground: pool({volumes[0].name},{ex + 1},{ex + 6}) on "
                "solid floor — the easiest; never pour volume() over a gap or "
                "pit. (2) RAISED basin on top of the floor: the surface "
                f"row must be OPEN AIR (row {ground} is the ground floor "
                f"itself and is occupied): wall({ex},{ground - 2},{ground - 1})  "
                f"volume({volumes[0].name},{ex + 1},{ex + 6},{ground - 1})  "
                f"wall({ex + 7},{ground - 2},{ground - 1})"
            )
            # Water-as-a-FEATURE (playtest direction): big deliberate
            # shapes, not obligatory puddles — and fully optional. The op
            # spelling + liquid word track the registry (water_* + "water"
            # for a water game, volume_* + the liquid name for a lava game).
            vocab_lines.append(
                f"{liquid.capitalize()} is OPTIONAL — a mostly-airborne level "
                "(jump gauntlet, canopy hop) is BETTER with no "
                f"{liquid} than with a forced pool; treat the rolled pool "
                "count as a maximum, not a quota. When "
                f"{liquid} fits, prefer BIG deliberate features over puddles: "
                f"{wall_op} drops a full column of {liquid} from y_top down to "
                "the terrain — a waterfall/shaft the player swims UP and leaps "
                "out of (1-3 columns wide, several rows tall; over a pit it "
                "runs out the bottom and sinking too deep is a fall death — a "
                f"deliberate spout hazard). {block_op} floats a pocket of "
                f"{liquid} in open air (whimsical worlds float their {liquid}; "
                "make it at least 2x2 so it reads as a feature). Both are "
                "exempt from the basin/containment rules — they are "
                "free-standing by design."
            )
            # Floating CLOUDS (Chunk E): the swim-up pocket, used LIBERALLY
            # where the section wants it (climbs, islands, caves).
            vocab_lines.append(
                f"{cloud_op}: a floating CLOUD of {liquid} hanging in open air "
                "— a SWIM-UP POCKET. The player jumps into it from below and "
                "swims UP through it to reach heights a jump alone can't, then "
                "leaps out the top; over a gap it also floats them across. It "
                "puffs into a rounded cloud shape (make it at least 4 wide x 3 "
                "tall so it reads as a cloud and holds a swimmable body). Use "
                "SEVERAL, stacked up a climb or strung over open space, so the "
                f"player rises or crosses through them. Containment-exempt like "
                f"{_pfx}_wall/{_pfx}_block."
            )
        if hazards:
            vocab_lines.append(
                "Hazard tiles for hazard_strip(): "
                + ", ".join(t.name for t in hazards)
                + " (touching one kills — they sit on floor)."
            )
        if "breakable" in tiles.by_name:
            vocab_lines.append(
                "breakable(x1,x2): a CRUMBLING FLOOR — solid to stand on, but "
                "it gives way a moment after the player steps on it and drops "
                "them into the pit below (a fall). Keep spans SHORT (2-4 "
                "columns) so a moving player can cross before it breaks; use it "
                "for tension, never as the only footing over a wide gap."
            )
        # Secret alcoves (Chunk E) — reward the curious explorer. Built from
        # existing terrain ops (wall/carve/ledge), marked with reward().
        vocab_lines.append(
            "Secret alcoves: tuck a HIDDEN niche into the terrain — carve a "
            "small recess behind a wall, under a ledge, or into a hill/ceiling "
            "(a pocket of open air the eye skips over) — and drop a reward(x,y) "
            "marker inside it (a placeholder for a future collectible; the cell "
            "must be OPEN AIR). Leave a small opening so a thorough player can "
            "jump in and reach it. Keep them RARE and OFF the main path — a "
            "bonus for exploring, never a required step."
        )
        return ops, vocab_lines

    @staticmethod
    def _layout_feedback(previous: str | None, feedback: list[str] | None) -> str:
        """Retry preamble shared by whole-level and per-section layout: show
        the model its own rejected attempt beside the diagnosis so it patches
        one design instead of re-rolling a fresh (differently broken) one."""
        if not feedback:
            return ""
        prev = (
            f"\nYour previous layout attempt:\n{previous}\n" if previous else "\n"
        )
        return (
            f"{prev}It was rejected because:\n- "
            + "\n- ".join(feedback)
            + "\nReturn a corrected layout, changing as little as possible.\n"
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
        fb = self._layout_feedback(previous, feedback)
        volumes = tiles.named("volume")
        ops, vocab_lines = self._ops_and_vocab(tiles, rules, height, width)
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
                + _physics_guidance(movement)
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

    def section_layout(
        self,
        level_id: str,
        brief: str,
        section_name: str,
        flavor: str,
        feature_bias: dict[str, float],
        index: int,
        total: int,
        width: int,
        height: int,
        movement: PlayerMovementSpec,
        rules: GameRules = DEFAULT_RULES,
        tiles: TileRegistry = DEFAULT_TILES,
        seam: str | None = None,
        intensity: str = "medium",
        water: str = "optional",
        axis: str = "horizontal",
        previous: str | None = None,
        feedback: list[str] | None = None,
    ) -> LLMRequest:
        """Prompt for ONE section of a stitched level. Same op vocabulary and
        physics as :meth:`layout_generation`, framed for a bounded sub-region:
        the archetype's CHARACTER steers what it builds, a seam summary
        describes the hand-off from the neighbour, and the markers depend on
        position — only the FIRST section spawns, NO section declares the exit
        (the stitcher places it). ``axis`` picks the framing: horizontal
        sections tile left→right (exit at the right edge); vertical (climb)
        sections stack bottom→top (the FIRST is the base with a ground floor +
        spawn, the exit is the summit). Local ``width``/``height`` are the
        SECTION's own dims — every coordinate is section-local."""
        fb = self._layout_feedback(previous, feedback)
        volumes = tiles.named("volume")
        ops, vocab_lines = self._ops_and_vocab(tiles, rules, height, width)
        character = (
            f"Section character — {section_name}: {flavor}\n"
            + _bias_guidance(feature_bias)
            + f"Pacing ({intensity}): {_INTENSITY_HINT.get(intensity, '')}.\n"
            + (_WATER_HINT.get(water, "") + "\n" if water in _WATER_HINT else "")
        )
        first = index == 0
        last = index == total - 1
        right = width - 1

        if axis == "vertical":
            stitch_line = (
                "This is ONE SECTION of a taller CLIMB, stacked BOTTOM-to-TOP. "
                f"Design ONLY this {height}-row stretch; it joins to the "
                "sections below and above.\n"
            )
            if first:
                marker_rule = (
                    f"This is the FIRST (BOTTOM) section: lay a ground "
                    f"floor(0,{right}) at the bottom and place exactly ONE "
                    "spawn(x) on it near the left (e.g. spawn(2)). Then build a "
                    "stack of STAGGERED platforms/ledges rising UP from it."
                )
            else:
                marker_rule = (
                    "Do NOT place a spawn — the player climbs up from below. "
                    "Build a stack of STAGGERED platforms/ledges the player "
                    "jumps UP through. Do NOT lay a full-width floor (it would "
                    "wall off the climb). This section has NO ground floor, so "
                    f"build ONLY from platform/ledge/wall/carve/free-water "
                    f"(valid columns 0..{right}, ledge/platform rows "
                    f"1..{height - 3}); do NOT use floor/pool/hazard_strip/"
                    "stairs, which need solid ground beneath them."
                )
            if last:
                marker_rule += (
                    " This is the FINAL (TOP) section: put a reachable "
                    "standable platform near the TOP (rows 1-3) — the exit "
                    "(summit) is placed there automatically."
                )
            seam_line = ""
            if seam and not first:
                seam_line = (
                    "Entering from BELOW — the section under you offers these "
                    f"top footholds: {seam}. Put your LOWEST platforms within "
                    f"{movement.jump_height} rows of them so the climb "
                    "continues.\n"
                )
            tail_rule = (
                f"Rules: {marker_rule} platforms/ledges are the backbone "
                "(smaller y = HIGHER); each platform must sit within "
                f"{movement.jump_height} rows of a lower foothold AND close "
                "horizontally (rising costs range — high steps go nearly "
                "overhead); hazards need floor/platform under them; keep the "
                "spawn column clear. Do NOT place checkpoints."
            )
        else:
            stitch_line = (
                "This is ONE SECTION of a larger level, stitched together "
                f"left-to-right. Design ONLY this {width}-column stretch; "
                "it joins to its neighbours at the left and right edges.\n"
            )
            if first:
                marker_rule = (
                    "This is the FIRST section: place exactly ONE spawn(x) "
                    "near the LEFT on clear flat floor (e.g. spawn(2)). Do NOT "
                    "place an exit — the level's exit is added automatically at "
                    "the far right of the WHOLE level."
                )
            else:
                marker_rule = (
                    "Do NOT place a spawn — the player arrives from the "
                    "previous section. Do NOT place an exit or a checkpoint — "
                    "the level's exit and its checkpoints are added "
                    "automatically at the right cells."
                )
            if last:
                marker_rule += (
                    f" This is the FINAL section: keep the far-RIGHT columns "
                    f"(around {right}) solid FLAT ground so the exit lands "
                    "cleanly there."
                )
            seam_line = ""
            if seam and not first:
                seam_line = (
                    "Entering from the LEFT — the previous section hands the "
                    f"player off here: {seam}. Continue the ground so the "
                    "player crosses the left seam WITHOUT dropping into an "
                    "unintended gap.\n"
                )
            tail_rule = (
                f"Rules: start from floor(0,{right}) then carve; {marker_rule} "
                "hazards need floor under them; platform/ledge rows are ABOVE "
                "the ground (smaller y = higher); volumes fill DOWN from their "
                "surface row and need solid floor beneath (never pour one over "
                "a gap or pit)"
                + (
                    "; every pool must be CONTAINED — put wall() columns at "
                    "both sides of the volume (a basin lip the player jumps "
                    "over), unless the pool reaches the section edge"
                    if rules.water_containment == "contained" and volumes
                    else ""
                )
                + "; keep the spawn column clear — no platform, wall, hazard, "
                "volume, or gap may cover it or remove the floor beneath it."
            )

        return LLMRequest(
            system=_SYSTEM_LAYOUT,
            user_message=(
                "### TASK: layout\n"
                f"### LEVEL: {level_id}\n"
                f"### SECTION: {section_name} ({index + 1} of {total})\n"
                f"Brief: {brief}\n"
                + character
                + stitch_line
                + f"Grid: {width} wide x {height} tall; row 0 is the TOP; the "
                f"ground floor row is {height - 2}; players stand one row "
                "above the surface they walk on.\n"
                + seam_line
                + _physics_guidance(movement)
                + "\n".join(vocab_lines)
                + f"\n{fb}\n"
                "Emit the section as DSL ops, one per line, nothing else:\n"
                + "  ".join(ops) + "\n"
                + tail_rule
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
        air_summary: str = "none",
        encounter_summary: str = "",
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
                f"Open-air cells (FLYERS hover here, above the ground): "
                f"{air_summary}\n"
                f"Player spawn: {list(spawn) if spawn else 'unknown'}\n"
                + (
                    "Section map (concentrate enemies in COMBAT/MIXED "
                    "stretches, keep TRAVERSAL stretches lighter): "
                    f"{encounter_summary}\n"
                    if encounter_summary
                    else ""
                )
                + f"{fb}\n"
                f"Place 1..{max_enemies} enemies to fit the brief. Spread "
                "them out; put slower enemies on patrol routes and ranged/"
                "sentry types guarding key jumps. Swimmer-archetype enemies "
                "MUST be placed in volume cells — and their behavior's "
                'swim_style matters: "surface" riders go on the water\'s '
                'TOP row (open air above), "float" drifters need a deep '
                '2x2 pocket, "within" swimmers just need their body in '
                "water. FLYER-archetype enemies MUST be placed in open-air "
                "cells — they hover above the ground and never stand on it. "
                "Every other archetype MUST be on standable land. "
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

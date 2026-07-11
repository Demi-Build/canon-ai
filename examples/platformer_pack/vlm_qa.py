"""VLM QA loop v1 — a vision judge over the review renders, AT THE VERY
END of the pipeline (after art, audio, and the renders it judges).

Per level, the phase sends the block render (analytic truth) + skinned
render (what the game looks like) + roster legend to a vision model and
collects STRUCTURED verdicts on three dimensions:

- ``fidelity`` — the skinned render matches the block truth: every
  placement present, effective sizes right, nothing missing or extra.
- ``readability`` — player/enemies/hazards are distinguishable against
  tiles and backdrop.
- ``style_coherence`` — palette adherence; sprites match the tileset's
  art style.

The report is ``review/<stage>/qa_report.json`` — deterministic shape, no
timestamps (the byte-identical fake-run bar covers it). Failing verdicts
become manifest WARNINGS via :func:`derive_qa_warnings`, which the
manifest re-derives from the on-disk report so an always-node rebuild
never erases them (the layout_fallback pattern). The report may SUGGEST
mark-only regen targets; it never regenerates anything — invalidation
stays user-controlled (PRD §6.3).

code-not-LLM guardrail: everything computable is a CODE check feeding the
same report — missing sprite files, sprite opaque-bbox vs its canvas
(a sprite hugging a corner renders smaller than the body the validator
footprinted), tile-region palette conformance. The VLM judges only what
code can't: does it *read* right.

Backends per house rules: only ever constructed from an explicit flag
(``--vlm-backend none|fake|anthropic`` or ``CANON_PLAT_VLM_BACKEND``),
anthropic fails fast without ``ANTHROPIC_API_KEY``, and the fake is a
deterministic canned judge that exercises the entire loop at $0 —
including one failing verdict so the warning path is covered.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from canon.llm.parsing import extract_json_object
from canon.pipeline.retry import retry_with_feedback
from examples.platformer_pack.combat import effective_size
from examples.platformer_pack.phases import _stamp_metadata, warn
from examples.platformer_pack.tiles import DEFAULT_TILES, TileRegistry
from examples.platformer_pack.variants import DEFAULT_VARIANTS, VariantSet

logger = logging.getLogger(__name__)

#: The closed verdict vocabulary — report keys, prompt sections, and
#: warning derivation all iterate this in order.
DIMENSIONS: tuple[str, ...] = ("fidelity", "readability", "style_coherence")

#: A sprite whose opaque bounding box spans less than this fraction of its
#: canvas (either axis' max) renders visibly smaller than the hitbox the
#: placement validator footprinted — consumers scale the WHOLE canvas onto
#: effective_size × actor_scale cells.
SPRITE_MIN_FILL = 0.5

#: Max RGB-euclidean distance between a tile region's mean color and its
#: palette role hex. conform_to_palette lands the brightness mean exactly
#: and the hue mean approximately, so a conformed sheet sits well inside
#: this; placeholder squares sit at 0.
PALETTE_TOLERANCE = 48.0

#: Free-text fields are clamped in code — a rambling model can't bloat the
#: report or the manifest warnings.
NOTES_MAX_CHARS = 300

# --- Animation authoring (B2) ------------------------------------------------

#: The closed animation-state vocabulary. Both play surfaces ALREADY track
#: each of these: ``walk`` while moving, ``hurt`` while hurt_t>0, ``death``
#: when !alive, else ``idle``. The VLM spec and frames.json share these keys.
ANIMATION_STATES: tuple[str, ...] = ("idle", "walk", "hurt", "death")

#: The PLAYER's states. The player jumps (enemies don't) and never plays a
#: death cycle (it respawns), so its set differs from the enemy set. Both
#: surfaces track: airborne → ``jump``, moving on ground → ``walk``, else idle.
PLAYER_ANIMATION_STATES: tuple[str, ...] = ("idle", "walk", "jump")

#: One-line meaning of each state, woven into the authoring prompt so motion
#: is grounded in what the state actually is (a flyer's ``walk`` is flight).
_STATE_BRIEF: dict[str, str] = {
    "idle": "at rest / holding position (a flyer hovers, a sentry barely stirs)",
    "walk": "actively moving along its path (for a flyer/swimmer: flight/swim)",
    "hurt": "recoiling from a hit — a brief flinch",
    "death": "defeated — collapse, tumble, or fade",
    "jump": "a jump arc — crouch, launch, rise, hang at the peak, then fall",
}

#: Per-state frame counts are clamped here. The image model ignores exact
#: counts anyway (the animation phase records whatever it segments); this only
#: bounds the REQUEST so a rambling model can't ask for a 40-frame sheet, and
#: every state gets at least a 2-frame cycle. The player gets a higher ceiling
#: for a SMOOTHER hero cycle (~9 frames), enemies stay tighter/cheaper.
ANIM_FRAMES_MIN, ANIM_FRAMES_MAX = 2, 6
PLAYER_ANIM_FRAMES_MAX = 9

#: Fallback frame count when the model omits/garbles a state — chosen to read
#: right: idle barely stirs, a walk/flight cycle is the fullest.
ANIM_DEFAULT_FRAMES: dict[str, int] = {
    "idle": 2, "walk": 4, "hurt": 2, "death": 4, "jump": 6,
}

#: Motion phrases are clamped like the QA notes.
ANIM_MOTION_MAX_CHARS = 200

# --- Animation QA (B5) -------------------------------------------------------

#: What the VLM judges about a GENERATED animation set — the visual questions
#: code can't answer. (Frame existence/count/emptiness are code checks.)
ANIMATION_QA_DIMENSIONS: tuple[str, ...] = ("consistency", "motion", "readability")

#: The global animation-QA report (enemies are world-global, not per-stage) —
#: warnings re-derive from it on disk, the durable qa_report pattern.
ANIM_QA_REPORT_REL = "review/animation_qa.json"

#: A sliced animation frame whose opaque area falls below this is blank — a
#: botched edit or a segmentation that grabbed empty canvas.
ANIM_FRAME_MIN_OPAQUE = 0.01


def qa_report_rel(stage_id: str) -> str:
    return f"review/{stage_id}/qa_report.json"


# ---------------------------------------------------------------------------
# Code checks — the computable half of the report (code-not-LLM).
# ---------------------------------------------------------------------------


def _mean_rgb(region: Any) -> tuple[int, int, int]:
    from PIL import Image

    if region.mode == "RGBA":
        lo, hi = region.getchannel("A").getextrema()
        if lo == 0 and hi > 0:
            # Partially transparent (a cut-out hazard): average only the
            # VISIBLE pixels, so the discarded backdrop under alpha 0 does
            # not drag the region mean off the palette hex. Fully-opaque
            # regions fall through to the fast path (byte-identical).
            px = list(region.get_flattened_data())
            opaque = [(r, g, b) for r, g, b, a in px if a > 0]
            n = len(opaque)
            return tuple(sum(c[i] for c in opaque) // n for i in range(3))
    return region.convert("RGB").resize((1, 1), Image.BILINEAR).getpixel((0, 0))


def _hex_rgb(color: str) -> tuple[int, int, int]:
    raw = color.lstrip("#")
    return tuple(int(raw[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def _sprite_checks(ctx: Any, target: str, sprite_path: str) -> list[dict]:
    """File-exists + opaque-bbox-fill checks for one sprite artifact."""
    from PIL import Image

    path = ctx.adapter.resolve_path(sprite_path)
    exists = path.exists()
    checks = [
        {
            "check": "sprite_file",
            "target": target,
            "subject": sprite_path,
            "passed": exists,
            "detail": sprite_path if exists else f"{sprite_path} missing on disk",
        }
    ]
    if not exists:
        return checks
    img = Image.open(path).convert("RGBA")
    bbox = img.getchannel("A").getbbox()
    if bbox is None:
        fill = 0.0
    else:
        width, height = img.size
        fill = max((bbox[2] - bbox[0]) / width, (bbox[3] - bbox[1]) / height)
    checks.append(
        {
            "check": "sprite_bbox",
            "target": target,
            "subject": sprite_path,
            "passed": fill >= SPRITE_MIN_FILL,
            "detail": (
                f"opaque bbox spans {fill:.2f} of the canvas "
                f"(min {SPRITE_MIN_FILL:.2f}) — consumers scale the whole "
                f"canvas onto the body's cells"
            ),
        }
    )
    return checks


def run_code_checks(
    ctx: Any, stage_id: str, tiles: TileRegistry = DEFAULT_TILES
) -> list[dict]:
    """Everything computable about the stage's art, as report records.

    Order is fixed (tileset slots by index, enemies sorted, player last)
    so the report is byte-deterministic.
    """
    from PIL import Image

    checks: list[dict] = []
    # A fallback level must fail its QA report too — the VLM verdicts
    # only judge render truth, and an empty fallback level renders
    # faithfully; without this echo the report reads all-green for a
    # level that shipped no generated content (sunlit-run finding).
    stage = ctx.bible.stages.get(stage_id)
    for level_id in (stage.level_ids if stage else []):
        level = ctx.bible.levels.get(level_id)
        if level is None or not getattr(level, "layout_fallback", False):
            continue
        checks.append(
            {
                "check": "layout_fallback",
                "target": level_id,
                "subject": "layout",
                "passed": False,
                "detail": (
                    f"level is the flat FALLBACK layout, not generated "
                    f"content (attempt trace: review/{stage_id}/"
                    f"{level_id}_layout_attempts.json)"
                ),
            }
        )
    tileset = ctx.bible.tilesets.get(stage_id)
    if tileset is not None and tileset.tilesheet_path:
        sheet_path = ctx.adapter.resolve_path(tileset.tilesheet_path)
        if sheet_path.exists():
            sheet = Image.open(sheet_path).convert("RGBA")
            by_name = {t.name: t for t in tiles.tiles}
            for slot in tileset.slots:
                tile = by_name.get(slot.name)
                role_hex = tileset.palette.get(tile.color_role) if tile else None
                if not role_hex or slot.px_region is None:
                    continue
                x, y, w, h = slot.px_region
                mean = _mean_rgb(sheet.crop((x, y, x + w, y + h)))
                target_rgb = _hex_rgb(role_hex)
                dist = sum((a - b) ** 2 for a, b in zip(mean, target_rgb)) ** 0.5
                checks.append(
                    {
                        "check": "palette_conformance",
                        "target": f"tileset:{stage_id}",
                        "subject": slot.name,
                        "passed": dist <= PALETTE_TOLERANCE,
                        "detail": (
                            f"tile {slot.name!r} region mean "
                            f"#{mean[0]:02x}{mean[1]:02x}{mean[2]:02x} vs "
                            f"palette {role_hex} (distance {dist:.0f}, "
                            f"tolerance {PALETTE_TOLERANCE:.0f})"
                        ),
                    }
                )
        else:
            checks.append(
                {
                    "check": "tilesheet_file",
                    "target": f"tileset:{stage_id}",
                    "subject": tileset.tilesheet_path,
                    "passed": False,
                    "detail": f"{tileset.tilesheet_path} missing on disk",
                }
            )

    for enemy_id in sorted(ctx.bible.enemy_definitions):
        enemy = ctx.bible.enemy_definitions[enemy_id]
        if enemy.sprite_path:
            checks.extend(_sprite_checks(ctx, f"enemy:{enemy_id}", enemy.sprite_path))
    player = getattr(ctx.bible, "player", None)
    if player is not None and player.sprite_path:
        checks.extend(_sprite_checks(ctx, "player", player.sprite_path))
    stage_props = getattr(ctx.bible, "props", {}).get(stage_id)
    if stage_props is not None:
        for _name, rel in sorted(stage_props.prop_paths.items()):
            checks.extend(_sprite_checks(ctx, f"props:{stage_id}", rel))
    return checks


# ---------------------------------------------------------------------------
# The judgment prompt + verdict handling
# ---------------------------------------------------------------------------


def _valid_targets(ctx: Any, stage_id: str, level: Any) -> list[str]:
    """The mark-only vocabulary the judge may suggest from — the artifacts
    whose re-roll could plausibly fix THIS level's look. Suggestions
    outside this list are dropped in code."""
    placed = sorted(
        {p.ref for p in level.entities if p.ref.startswith("enemy:")}
    )
    targets = [level.level_id, *placed, "player", f"tileset:{stage_id}"]
    if stage_id in getattr(ctx.bible, "backdrops", {}):
        targets.append(f"backdrop:{stage_id}")
    if stage_id in getattr(ctx.bible, "props", {}):
        targets.append(f"props:{stage_id}")
    return targets


def qa_prompt(
    level: Any,
    stage: Any,
    enemies: dict[str, Any],
    palette: dict[str, str],
    targets: list[str],
    variants: VariantSet = DEFAULT_VARIANTS,
) -> str:
    """The per-level judgment instructions. Text carries the analytic
    facts (placements, markers, counts) so the judge compares the skinned
    render against stated truth, not just against the block image."""
    placements = []
    for p in level.entities:
        enemy_id = p.ref.split(":", 1)[1]
        enemy = enemies.get(enemy_id)
        variant_name = str(p.overrides.get("variant", ""))
        variant = variants.by_name.get(variant_name)
        eff = effective_size(
            float(getattr(enemy, "size", 1.0) or 1.0) if enemy else 1.0,
            variant.size if variant else 1.0,
        )
        desc = (
            f"{(enemy.name if enemy else enemy_id)} ({p.ref}) at "
            f"[{p.pos[0]}, {p.pos[1]}], body {eff:g} cell(s) tall"
        )
        if variant is not None:
            desc += f", variant {variant_name!r}"
        placements.append(desc)
    checkpoints = sorted(
        t.x for t in level.triggers if t.type == "checkpoint"
    )
    palette_desc = ", ".join(f"{k}: {v}" for k, v in sorted(palette.items()))

    return (
        f"### TASK: vlm_qa\n"
        f"### LEVEL: {level.level_id}\n\n"
        f"You are the visual QA judge for one generated 2D platformer "
        f"level (stage theme: {stage.theme!r}). Attached images, in "
        f"order:\n"
        f"1. BLOCK render — the analytic ground truth: flat colored "
        f"cells, enemy bodies as colored rectangles at their exact "
        f"validated positions and sizes, white=spawn, green=exit, "
        f"amber=checkpoint.\n"
        f"2. SKINNED render — the same level composited with the real "
        f"tile art, sprites, and parallax backdrop. This is what the "
        f"player sees. The exit marker appears here as a goal object "
        f"(doorway/flag) and each checkpoint as a small flag — those are "
        f"correct, not extra content.\n"
        f"3. ROSTER LEGEND — enemy names, placeholder colors, sizes, and "
        f"stats.\n\n"
        f"Level facts (the truth the skinned render must match):\n"
        f"- grid {level.grid_width}x{level.grid_height} cells; spawn "
        f"{list(level.spawn) if level.spawn else '?'}; exit "
        f"{list(level.exit) if level.exit else '?'}\n"
        f"- placements: {'; '.join(placements) or 'none'}\n"
        f"- hazard cells: {len(level.hazards)}; checkpoint columns: "
        f"{checkpoints or 'none'}; decor records: {len(level.foreground)}\n"
        f"- palette (color_role: hex): {palette_desc}\n\n"
        f"Judge these three dimensions strictly:\n"
        f'1. "fidelity" — does the skinned render match the block truth? '
        f"Every placement present at the right spot and effective size, "
        f"terrain shapes identical, nothing missing, nothing extra.\n"
        f'2. "readability" — are the player spawn, enemies, and hazards '
        f"clearly distinguishable from the tiles and the backdrop at a "
        f"glance?\n"
        f'3. "style_coherence" — does the art hold together: tiles adhere '
        f"to the palette, sprites match the tileset's art style?\n\n"
        f"Note on water: this game may use water as large deliberate "
        f"FEATURES — full-height walls/waterfalls to swim up, or floating "
        f"pockets in open air. Those are legitimate design, not errors; "
        f"only flag water that reads as accidental (a stray puddle "
        f"clipping through terrain, water hiding a required path).\n\n"
        f"### TARGETS: {', '.join(targets)}\n\n"
        f"Respond with a bare JSON object, no prose, no fences:\n"
        f'{{"fidelity": {{"passed": true|false, "notes": "<short>"}}, '
        f'"readability": {{"passed": true|false, "notes": "<short>"}}, '
        f'"style_coherence": {{"passed": true|false, "notes": "<short>"}}, '
        f'"notes": "<short overall>", '
        f'"suggested_regen_targets": ["<id>"]}}\n'
        f"Only suggest targets from the TARGETS list, and only when a "
        f"dimension failed."
    )


def _validate_verdict(content: str) -> tuple[bool, list[str]]:
    obj = extract_json_object(content)
    if obj is None:
        return False, ["Response must be a bare JSON object — no prose or fences."]
    problems = []
    for dim in DIMENSIONS:
        value = obj.get(dim)
        if not isinstance(value, dict) or not isinstance(value.get("passed"), bool):
            problems.append(
                f'"{dim}" must be an object with a boolean "passed" and '
                f'short string "notes".'
            )
    return (not problems, problems)


def _sanitize_verdict(obj: dict, targets: list[str]) -> dict:
    """Coerce the validated verdict into the report's fixed shape. All
    free text is clamped; suggestions are filtered to the offered target
    vocabulary and dropped entirely when every dimension passed (the
    contract the prompt states, enforced in code)."""
    verdicts = {
        dim: {
            "passed": bool(obj[dim].get("passed")),
            "notes": str(obj[dim].get("notes", ""))[:NOTES_MAX_CHARS],
        }
        for dim in DIMENSIONS
    }
    any_failed = any(not v["passed"] for v in verdicts.values())
    raw = obj.get("suggested_regen_targets") or []
    suggested: list[str] = []
    if any_failed and isinstance(raw, list):
        for item in raw:
            if isinstance(item, str) and item in targets and item not in suggested:
                suggested.append(item)
    return {
        "verdicts": verdicts,
        "notes": str(obj.get("notes", ""))[:NOTES_MAX_CHARS],
        "suggested_regen_targets": suggested,
    }


# ---------------------------------------------------------------------------
# Animation authoring (B2) — the VLM interrogates the ACTUAL sprite and
# describes, per state, how THIS creature moves. Reuses the same judge
# machinery as qa_prompt (build_vlm_judge, extract_json_object,
# retry_with_feedback); the spec persists on the enemy as stats["animation"]
# and the animation phase (B3) consumes it to build one sheet per state.
# ---------------------------------------------------------------------------


def enemy_animation_subject(enemy: Any) -> str:
    """The enemy-specific description block for :func:`animate_prompt` —
    archetype is a HINT (appearance is LLM-authored), flavor grounds it."""
    name = enemy.name or enemy.enemy_id
    archetype = enemy.archetype or "patroller"
    size = float(getattr(enemy, "size", 1.0) or 1.0)
    flavor = str((getattr(enemy, "stats", None) or {}).get("flavor", "")).strip()
    return (
        f"Creature: {name!r} — locomotion archetype {archetype!r} (a HINT, not "
        f"a lock: describe motion that fits the actual sprite; a 'flyer' may be "
        f"a wingless floating fish). Body ~{size:g} cell(s) tall.\n"
        f"Flavor: {flavor or 'n/a'}"
    )


def animate_prompt(
    actor_id: str,
    subject: str,
    states: tuple[str, ...] = ANIMATION_STATES,
    frames_max: int = ANIM_FRAMES_MAX,
) -> str:
    """Instructions for authoring a per-state motion spec for ONE actor
    (enemy or player), grounded in its ACTUAL generated sprite (attached as
    the sole image). ``subject`` is the actor description block; ``frames_max``
    bounds the per-state frame count (higher = smoother, e.g. the player).

    Parallel to :func:`qa_prompt`: the text carries the facts, the attached
    sprite carries what's really drawn, and the model returns bare JSON."""
    state_lines = "\n".join(f'  - "{s}": {_STATE_BRIEF[s]}' for s in states)
    example = ", ".join(
        f'"{s}": {{"frames": <int>, "motion": "<short>"}}' for s in states
    )
    return (
        f"### TASK: plat_animate\n"
        f"### ACTOR: {actor_id}\n\n"
        f"You are a 2D sprite animator. The attached image is the ACTUAL "
        f"generated sprite for this character — study how it is really drawn "
        f"(limbs, wings, fins, body, tail) and describe how THIS specific "
        f"character moves in each state. Ground every motion in the drawing.\n\n"
        f"{subject}\n\n"
        f"States to author (use these exact keys):\n{state_lines}\n\n"
        f"For each state provide:\n"
        f'  - "frames": an integer {ANIM_FRAMES_MIN}-{frames_max} — how many '
        f"frames the cycle needs. A subtle idle → few; a full walk/jump cycle → "
        f"more (smoother). Ask only for what the motion needs.\n"
        f'  - "motion": one short phrase for the per-frame motion, specific to '
        f"this sprite (e.g. 'legs cycle front-to-back, body bobs' / 'crouch, "
        f"spring up, tuck at the peak, reach for the landing').\n\n"
        f"Respond with a bare JSON object, no prose, no fences:\n"
        f"{{{example}}}"
    )


def _validate_animation_spec(
    content: str,
    states: tuple[str, ...] = ANIMATION_STATES,
    frames_max: int = ANIM_FRAMES_MAX,
) -> tuple[bool, list[str]]:
    """retry_with_feedback validator: every state present as an object with a
    numeric ``frames`` and a non-empty ``motion`` string. Problems are fed
    verbatim back to the model."""
    obj = extract_json_object(content)
    if obj is None:
        return False, ["Response must be a bare JSON object — no prose or fences."]
    problems: list[str] = []
    for state in states:
        value = obj.get(state)
        if not isinstance(value, dict):
            problems.append(
                f'"{state}" must be an object with an integer "frames" '
                f'({ANIM_FRAMES_MIN}-{frames_max}) and a short "motion" string.'
            )
            continue
        frames = value.get("frames")
        if not isinstance(frames, (int, float)) or isinstance(frames, bool):
            problems.append(f'"{state}.frames" must be a number.')
        motion = value.get("motion")
        if not isinstance(motion, str) or not motion.strip():
            problems.append(f'"{state}.motion" must be a non-empty string.')
    return (not problems, problems)


def _sanitize_animation_spec(
    obj: dict,
    states: tuple[str, ...] = ANIMATION_STATES,
    frames_max: int = ANIM_FRAMES_MAX,
) -> dict:
    """Coerce a validated spec into the fixed persisted shape: frame counts
    rounded + clamped to [MIN, frames_max], motion clamped, every state present
    (defaults fill any the model garbled). Deterministic key order."""
    spec: dict[str, dict] = {}
    for state in states:
        value = obj.get(state) if isinstance(obj.get(state), dict) else {}
        raw = value.get("frames")
        try:
            frames = int(round(float(raw)))
        except (TypeError, ValueError):
            frames = ANIM_DEFAULT_FRAMES.get(state, ANIM_FRAMES_MIN)
        frames = max(ANIM_FRAMES_MIN, min(frames_max, frames))
        motion = str(value.get("motion", "")).strip()[:ANIM_MOTION_MAX_CHARS]
        spec[state] = {"frames": frames, "motion": motion or _STATE_BRIEF[state]}
    return spec


def author_animation_spec(
    judge: Any,
    actor_id: str,
    subject: str,
    sprite_bytes: bytes,
    *,
    states: tuple[str, ...] = ANIMATION_STATES,
    frames_max: int = ANIM_FRAMES_MAX,
    max_retries: int = 3,
) -> dict | None:
    """Run the VLM to author a per-state motion spec for one actor (enemy or
    player) from its ACTUAL sprite. Mirrors :meth:`VlmQaPhase._judge_level`'s
    retry loop.

    Returns the sanitized spec (persist it as the actor's animation manifest),
    or ``None`` when the verdict never validates — the caller then keeps the
    static sprite (the loud-fallback contract)."""
    prompt = animate_prompt(actor_id, subject, states, frames_max)

    def generate(feedback: list[str] | None = None) -> str:
        text = prompt
        if feedback:
            text += "\n\nYour previous response was rejected:\n" + "\n".join(
                f"- {reason}" for reason in feedback
            )
        return judge.judge(text, [sprite_bytes])

    raw = retry_with_feedback(
        generate_fn=generate,
        validate_fn=lambda content: _validate_animation_spec(
            content, states, frames_max
        ),
        fallback="",
        max_retries=max_retries,
        label=f"plat:animate:{actor_id}",
    )
    obj = extract_json_object(raw) if raw else None
    if obj is None:
        return None
    return _sanitize_animation_spec(obj, states, frames_max)


# ---------------------------------------------------------------------------
# Warning derivation — shared by the phase (this run) and the manifest
# (re-derived from the on-disk report, so an always-node rebuild never
# erases QA findings: the layout_fallback durability pattern).
# ---------------------------------------------------------------------------


def derive_qa_warnings(report: dict) -> list[str]:
    """Failing report entries → manifest warning strings. Deterministic:
    the phase warns exactly these, and the manifest re-derives exactly
    these, so dedup is plain string membership."""
    rel = qa_report_rel(str(report.get("stage_id", "")))
    messages: list[str] = []
    for check in report.get("code_checks", []):
        if check.get("passed"):
            continue
        if check.get("check") == "layout_fallback":
            # Report-only echo: the durable layout warning (re-derived
            # from Level.layout_fallback by the manifest) already owns
            # this signal — a second manifest line would be noise.
            continue
        messages.append(
            f"vlm_qa code-check {check.get('check')} FAILED for "
            f"{check.get('target')}: {check.get('detail')} (report: {rel})"
        )
    for level_id, entry in report.get("levels", {}).items():
        if "error" in entry:
            messages.append(
                f"vlm_qa {level_id}: no verdict — {entry['error']} "
                f"(report: {rel})"
            )
            continue
        suggested = entry.get("suggested_regen_targets") or []
        hint = (
            f"; suggested mark-only targets: {', '.join(suggested)}"
            if suggested
            else ""
        )
        for dim in DIMENSIONS:
            verdict = entry.get("verdicts", {}).get(dim, {})
            if verdict.get("passed", True):
                continue
            messages.append(
                f"vlm_qa {level_id}: {dim} FAILED — "
                f"{verdict.get('notes', '')}{hint} (report: {rel}; "
                f"regen stays user-controlled)"
            )
    return messages


# ---------------------------------------------------------------------------
# Animation QA (B5) — the VLM reviews the GENERATED animation (after B3),
# closing the authoring→generation→REVIEW loop. Code checks the computable
# half (frames exist, counts match, no blank frames); the VLM judges what code
# can't (same creature across frames, coherent motion, reads-as-its-state).
# Warn-only per the QA doctrine — NO auto-regen; a failure suggests a mark-only
# enemy re-roll. Global (enemies are world-wide), gated on --vlm-backend.
# ---------------------------------------------------------------------------


#: Canonical state order for the contact sheet + prompt — covers every actor's
#: states (enemy idle/walk/hurt/death AND player idle/walk/jump).
_STATE_ORDER: tuple[str, ...] = ("idle", "walk", "jump", "hurt", "death")


def _animation_checks(ctx: Any, actor_id: str, animation: dict) -> list[dict]:
    """Computable checks over one actor's generated animation: each state strip
    exists, its width matches frame_width x frames, and no sliced frame is
    blank. code-not-LLM — the VLM verdict handles only the visual read."""
    from PIL import Image

    checks: list[dict] = []
    target = actor_id
    for state, meta in sorted((animation.get("states") or {}).items()):
        rel = str(meta.get("path", ""))
        n = int(meta.get("frames", 0))
        fw = int(meta.get("frame_width", 0))
        path = ctx.adapter.resolve_path(rel)
        if not path.exists():
            checks.append({
                "check": "animation_strip", "target": target, "subject": state,
                "passed": False, "detail": f"{rel} missing on disk",
            })
            continue
        img = Image.open(path).convert("RGBA")
        width_ok = n > 0 and img.width == fw * n
        checks.append({
            "check": "animation_strip", "target": target, "subject": state,
            "passed": width_ok,
            "detail": (
                f"{state}: {n} frame(s) of {fw}px" if width_ok
                else f"{state}: {img.width}px wide, expected {fw}x{n}={fw * n}"
            ),
        })
        if not width_ok:
            continue
        blank = []
        for i in range(n):
            frame = img.crop((i * fw, 0, (i + 1) * fw, img.height))
            alpha = list(frame.getchannel("A").get_flattened_data())
            if sum(1 for a in alpha if a > 0) / len(alpha) < ANIM_FRAME_MIN_OPAQUE:
                blank.append(i)
        checks.append({
            "check": "animation_frames", "target": target, "subject": state,
            "passed": not blank,
            "detail": (
                f"{state}: all {n} frame(s) have content" if not blank
                else f"{state}: frame(s) {blank} are (near-)blank"
            ),
        })
    return checks


def _animation_contact_sheet(ctx: Any, animation: dict, scale: int = 4) -> bytes | None:
    """One image for the VLM: each state's strip on its own row (top→bottom in
    ANIMATION_STATES order), upscaled so small sprites read. None if nothing
    loads."""
    import io

    from PIL import Image

    states = animation.get("states") or {}
    rows = []
    for state in _STATE_ORDER:
        meta = states.get(state)
        if not meta:
            continue
        path = ctx.adapter.resolve_path(str(meta.get("path", "")))
        if not path.exists():
            continue
        strip = Image.open(path).convert("RGBA")
        rows.append(
            strip.resize((strip.width * scale, strip.height * scale), Image.NEAREST)
        )
    if not rows:
        return None
    width = max(r.width for r in rows)
    height = sum(r.height for r in rows) + 2 * (len(rows) - 1)
    sheet = Image.new("RGBA", (width, height), (60, 60, 70, 255))
    y = 0
    for r in rows:
        sheet.paste(r, (0, y), r)
        y += r.height + 2
    buf = io.BytesIO()
    sheet.save(buf, format="PNG")
    return buf.getvalue()


def animate_qa_prompt(actor_id: str, name: str, states_present: list[str]) -> str:
    """Review instructions for one actor's generated animation (parallel to
    qa_prompt). The attached contact sheet has one ROW per state."""
    order = [s for s in _STATE_ORDER if s in states_present]
    return (
        f"### TASK: plat_animate_qa\n"
        f"### ACTOR: {actor_id}\n\n"
        f"You are the animation QA judge for one generated 2D game character: "
        f"{name!r}. The attached image is a CONTACT SHEET — one row per "
        f"animation state, top to bottom: {', '.join(order)}. Within a row the "
        f"frames run left→right in play order.\n\n"
        f"Judge these three dimensions strictly:\n"
        f'1. "consistency" — is it unmistakably the SAME character in every '
        f"frame: stable design, size, and colors (no morphing, no stray extra "
        f"characters)?\n"
        f'2. "motion" — do the frames of each row form a COHERENT cycle — '
        f"progressive, distinct poses — rather than identical duplicates or "
        f"garbled noise?\n"
        f'3. "readability" — does each state read as its motion (walk = a '
        f"locomotion stride, idle = a subtle stir, hurt = a recoil)?\n\n"
        f"Respond with a bare JSON object, no prose, no fences:\n"
        f'{{"consistency": {{"passed": true|false, "notes": "<short>"}}, '
        f'"motion": {{"passed": true|false, "notes": "<short>"}}, '
        f'"readability": {{"passed": true|false, "notes": "<short>"}}, '
        f'"notes": "<short overall>"}}'
    )


def _validate_animation_verdict(content: str) -> tuple[bool, list[str]]:
    obj = extract_json_object(content)
    if obj is None:
        return False, ["Response must be a bare JSON object — no prose or fences."]
    problems = []
    for dim in ANIMATION_QA_DIMENSIONS:
        value = obj.get(dim)
        if not isinstance(value, dict) or not isinstance(value.get("passed"), bool):
            problems.append(
                f'"{dim}" must be an object with a boolean "passed" and '
                f'short string "notes".'
            )
    return (not problems, problems)


def _sanitize_animation_verdict(obj: dict) -> dict:
    return {
        "verdicts": {
            dim: {
                "passed": bool(obj[dim].get("passed")),
                "notes": str(obj[dim].get("notes", ""))[:NOTES_MAX_CHARS],
            }
            for dim in ANIMATION_QA_DIMENSIONS
        },
        "notes": str(obj.get("notes", ""))[:NOTES_MAX_CHARS],
    }


def _animated_actors(ctx: Any) -> list[tuple[str, str, dict]]:
    """Every actor with a generated animation, as (actor_id, display_name,
    manifest) — enemies (from stats["animation"]) then the player (from
    PlayerDefinition.animation). The single source B5 iterates."""
    actors: list[tuple[str, str, dict]] = []
    for enemy_id in sorted(ctx.bible.enemy_definitions):
        enemy = ctx.bible.enemy_definitions[enemy_id]
        anim = (getattr(enemy, "stats", None) or {}).get("animation")
        if anim and anim.get("states"):
            actors.append((f"enemy:{enemy_id}", enemy.name or enemy_id, anim))
    player = getattr(ctx.bible, "player", None)
    anim = getattr(player, "animation", None) if player is not None else None
    if anim and anim.get("states"):
        actors.append(("player", "the player", anim))
    return actors


def review_animations(ctx: Any, judge: Any, max_retries: int = 3) -> dict:
    """Global per-ACTOR animation review (enemies + player). Code checks run
    for every animated actor; the VLM verdict runs when a judge is present.
    Returns the report dict (write to ANIM_QA_REPORT_REL); it NEVER regenerates
    — a failing verdict becomes a durable warning only."""
    reviewed: dict[str, dict] = {}
    for actor_id, name, animation in _animated_actors(ctx):
        entry: dict[str, Any] = {
            "code_checks": _animation_checks(ctx, actor_id, animation),
        }
        sheet = _animation_contact_sheet(ctx, animation) if judge is not None else None
        if sheet is not None:
            prompt = animate_qa_prompt(actor_id, name, list(animation["states"]))
            raw = retry_with_feedback(
                generate_fn=lambda feedback=None, p=prompt, s=sheet: judge.judge(
                    p if not feedback
                    else p + "\n\nYour previous response was rejected:\n"
                    + "\n".join(f"- {r}" for r in feedback),
                    [s],
                ),
                validate_fn=_validate_animation_verdict,
                fallback="",
                max_retries=max_retries,
                label=f"plat:animate_qa:{actor_id}",
            )
            obj = extract_json_object(raw) if raw else None
            if obj is None:
                entry["error"] = "verdict never validated after retries"
            else:
                entry.update(_sanitize_animation_verdict(obj))
        reviewed[actor_id] = entry
    return {
        "vlm_model": (
            str(getattr(judge, "model", type(judge).__name__))
            if judge is not None else "none"
        ),
        "actors": reviewed,
    }


def derive_animation_qa_warnings(report: dict) -> list[str]:
    """Failing animation-QA entries → manifest warnings, re-derived from the
    on-disk report. Suggests a mark-only re-roll of the actor (regen user-run)."""
    messages: list[str] = []
    for actor_id, entry in sorted((report.get("actors") or {}).items()):
        for check in entry.get("code_checks", []):
            if check.get("passed"):
                continue
            messages.append(
                f"animation_qa code-check {check.get('check')} FAILED for "
                f"{actor_id} ({check.get('subject')}): "
                f"{check.get('detail')} (report: {ANIM_QA_REPORT_REL})"
            )
        if "error" in entry:
            messages.append(
                f"animation_qa {actor_id}: no verdict — {entry['error']} "
                f"(report: {ANIM_QA_REPORT_REL})"
            )
            continue
        for dim in ANIMATION_QA_DIMENSIONS:
            verdict = entry.get("verdicts", {}).get(dim, {})
            if verdict.get("passed", True):
                continue
            messages.append(
                f"animation_qa {actor_id}: {dim} FAILED — "
                f"{verdict.get('notes', '')}; suggested mark-only target: "
                f"{actor_id} (report: {ANIM_QA_REPORT_REL}; regen stays "
                f"user-controlled)"
            )
    return messages


# ---------------------------------------------------------------------------
# Backends — explicit-flag wiring, same rules as image/music/sfx.
# ---------------------------------------------------------------------------


def make_fake_vlm_responder():
    """Canned deterministic verdicts keyed off the prompt's markers. Serves
    BOTH VLM tasks from one fake judge:

    - ``plat_animate`` (B2) → a canned per-state motion spec, with a
      deliberately over-count ``death`` state so the frame clamp is exercised.
    - ``vlm_qa`` → all dimensions pass except READABILITY on l2, which fails
      and suggests the first enemy target — exercising the failing-verdict
      warning path and target filtering.
    """

    def respond(prompt: str, images: list[bytes]) -> str:
        # NB: check plat_animate_qa BEFORE plat_animate — the former contains
        # the latter as a substring.
        if "### TASK: plat_animate_qa" in prompt:
            return json.dumps(
                {
                    dim: {"passed": True, "notes": f"canned fake pass ({dim})"}
                    for dim in ANIMATION_QA_DIMENSIONS
                }
                | {"notes": "canned fake animation review"}
            )
        if "### TASK: plat_animate" in prompt:
            # State-aware canned spec: return exactly the states the prompt
            # asks for (enemies get idle/walk/hurt/death, the player gets
            # idle/walk/jump). ``death`` sits ABOVE the enemy clamp ceiling
            # (9 → 6) so _sanitize_animation_spec's clamp runs in a fake run.
            canned_frames = {
                "idle": 2, "walk": 4, "hurt": 2, "death": 9, "jump": 5,
            }
            asked = [
                s for s in re.findall(r'"(\w+)":', prompt) if s in canned_frames
            ]
            return json.dumps(
                {
                    s: {"frames": canned_frames[s], "motion": f"canned {s}: motion"}
                    for s in dict.fromkeys(asked)  # dedup, keep order
                }
            )
        level_match = re.search(r"### LEVEL: (\w+)", prompt)
        level_id = level_match.group(1) if level_match else ""
        targets_match = re.search(r"### TARGETS: (.+)", prompt)
        targets = (
            [t.strip() for t in targets_match.group(1).split(",")]
            if targets_match
            else []
        )
        verdict: dict[str, Any] = {
            dim: {"passed": True, "notes": f"canned fake pass ({dim})"}
            for dim in DIMENSIONS
        }
        verdict["notes"] = "canned fake verdict"
        verdict["suggested_regen_targets"] = []
        if level_id == "l2":
            enemy_target = next(
                (t for t in targets if t.startswith("enemy:")), ""
            )
            verdict["readability"] = {
                "passed": False,
                "notes": (
                    "canned fake failure: enemy bodies blend into the "
                    "mid backdrop band at skinned scale"
                ),
            }
            verdict["suggested_regen_targets"] = (
                [enemy_target] if enemy_target else []
            )
        return json.dumps(verdict)

    return respond


def build_vlm_judge(kind: str | None, model: str | None = None):
    """CLI/env wiring: a vlm-backend name → judge, or ``None`` for no QA.
    Paid backends only from an explicit flag; missing keys die at launch,
    before any generation money is spent."""
    if not kind or kind == "none":
        return None
    if kind == "fake":
        from canon.backends.testing import FakeVLMBackend

        return FakeVLMBackend(make_fake_vlm_responder())
    if kind == "anthropic":
        import os

        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise ValueError(
                "--vlm-backend anthropic needs ANTHROPIC_API_KEY in the "
                "environment. A .env file is NOT read automatically — "
                "export it first, e.g.: set -a; source .env; set +a"
            )
        from canon.backends.vlm_anthropic import AnthropicVLMBackend

        return AnthropicVLMBackend(model) if model else AnthropicVLMBackend()
    raise ValueError(f"unknown vlm backend {kind!r} (none|fake|anthropic).")


# ---------------------------------------------------------------------------
# The phase
# ---------------------------------------------------------------------------


class VlmQaPhase:
    """Judge every level's block-vs-skinned pair; write the QA report;
    surface failures as durable manifest warnings. NO auto-regen, ever.

    v1 cadence: runs only when a judge was built from an explicit flag —
    an `always` node in the DAG, a plain no-op stamp otherwise. There is
    deliberately no staleness/caching story yet; each flagged run
    re-judges (and rewrites the report), each unflagged run leaves the
    last report — and its warnings — standing.
    """

    name = "plat:vlm_qa"

    def __init__(
        self,
        judge: Any = None,
        tiles: TileRegistry = DEFAULT_TILES,
        variants: VariantSet = DEFAULT_VARIANTS,
    ) -> None:
        self.judge = judge
        self.tiles = tiles
        self.variants = variants

    def run(self, ctx: Any) -> None:
        if self.judge is None:
            logger.info(
                "VlmQaPhase: no VLM judge (explicit --vlm-backend required) "
                "— QA skipped; any existing report stands."
            )
            _stamp_metadata(ctx, self.name)
            return

        for stage_id, stage in ctx.bible.stages.items():
            checks = run_code_checks(ctx, stage_id, tiles=self.tiles)
            levels: dict[str, dict] = {}
            for level_id in stage.level_ids:
                level = ctx.bible.levels.get(level_id)
                if level is None:
                    continue
                levels[level_id] = self._judge_level(ctx, stage, level)
            report = {
                "stage_id": stage_id,
                "vlm_model": str(
                    getattr(self.judge, "model", type(self.judge).__name__)
                ),
                "code_checks": checks,
                "levels": levels,
            }
            ctx.adapter.write_json_singleton(qa_report_rel(stage_id), report)
            for message in derive_qa_warnings(report):
                warn(ctx, message)
            failed = sum(
                1
                for entry in levels.values()
                for dim in DIMENSIONS
                if not entry.get("verdicts", {}).get(dim, {}).get("passed", True)
            )
            logger.info(
                "VlmQaPhase judged %d level(s) for stage %s: %d failing "
                "verdict(s), %d/%d code check(s) passed.",
                len(levels), stage_id, failed,
                sum(1 for c in checks if c["passed"]), len(checks),
            )

        # Animation QA (B5): review every animated enemy ONCE (enemies are
        # world-global, not per-stage) — code checks + a VLM verdict → a global
        # report + durable warnings. Warn-only, never regenerates.
        anim_report = review_animations(ctx, self.judge)
        if anim_report["actors"]:
            ctx.adapter.write_json_singleton(ANIM_QA_REPORT_REL, anim_report)
            for message in derive_animation_qa_warnings(anim_report):
                warn(ctx, message)
            failed = sum(
                1
                for entry in anim_report["actors"].values()
                for dim in ANIMATION_QA_DIMENSIONS
                if not entry.get("verdicts", {}).get(dim, {}).get("passed", True)
            )
            logger.info(
                "VlmQaPhase reviewed %d animated actor(s): %d failing "
                "verdict(s).", len(anim_report["actors"]), failed,
            )
        _stamp_metadata(ctx, self.name)

    def _judge_level(self, ctx: Any, stage: Any, level: Any) -> dict:
        """One level's report entry: the render pair + legend to the
        judge, retry-validated JSON back; an entry with ``error`` (loud
        via derive_qa_warnings) when images are missing or the verdict
        never validates."""
        stage_id = stage.stage_id
        image_rels = {
            "block": f"review/{stage_id}/{level.level_id}.png",
            "skinned": f"review/{stage_id}/{level.level_id}_skinned.png",
            "legend": "review/legend.png",
        }
        missing = [
            rel
            for rel in image_rels.values()
            if not ctx.adapter.resolve_path(rel).exists()
        ]
        entry: dict[str, Any] = {
            "images": {
                "block": image_rels["block"],
                "skinned": image_rels["skinned"],
            }
        }
        if missing:
            entry["error"] = f"review render(s) missing: {', '.join(missing)}"
            return entry
        images = [
            ctx.adapter.resolve_path(rel).read_bytes()
            for rel in image_rels.values()
        ]

        tileset = ctx.bible.tilesets.get(stage_id)
        targets = _valid_targets(ctx, stage_id, level)
        prompt = qa_prompt(
            level, stage, ctx.bible.enemy_definitions,
            tileset.palette if tileset else {}, targets,
            variants=self.variants,
        )

        def generate(feedback: list[str] | None = None) -> str:
            text = prompt
            if feedback:
                text += "\n\nYour previous response was rejected:\n" + "\n".join(
                    f"- {reason}" for reason in feedback
                )
            return self.judge.judge(text, images)

        raw = retry_with_feedback(
            generate_fn=generate,
            validate_fn=_validate_verdict,
            fallback="",
            max_retries=getattr(ctx.config, "max_retries", 3),
            label=f"{self.name}:{level.level_id}",
        )
        obj = extract_json_object(raw) if raw else None
        if obj is None:
            entry["error"] = "verdict never validated after retries"
            return entry
        entry.update(_sanitize_verdict(obj, targets))
        return entry

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

import hashlib
import io
import json
import logging
import re
from typing import Any

from canon.llm.parsing import extract_json_object
from canon.pipeline.retry import retry_with_feedback
from examples.platformer_pack import color as color_math
from examples.platformer_pack.art_phases import VFX_PROP_NAMES
from examples.platformer_pack.combat import effective_size, occupancy
from examples.platformer_pack.graphics import DEFAULT_GRAPHICS, GraphicsSpec
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

#: Relaxed min-fill for the one-shot VFX props (VFX_PROP_NAMES) — wispy
#: dust/splash/sparkle art is mostly empty space by design and has no
#: hitbox to under-render.
VFX_MIN_FILL = 0.08

#: Max RGB-euclidean distance between a backdrop band's first and last
#: pixel columns — bands wrap horizontally in every consumer, so a
#: mismatched edge pair is a visible scrolling seam.
BACKDROP_SEAM_TOLERANCE = 40.0

#: Max RGB-euclidean distance between a tile region's mean color and its
#: palette role hex. conform_to_palette lands the brightness mean exactly
#: and the hue mean approximately, so a conformed sheet sits well inside
#: this; placeholder squares sit at 0.
PALETTE_TOLERANCE = 48.0

#: Pre-conform drift SPIKE (ticket 6): conform_to_palette repairs any
#: mis-aim, so the meter flags only a raw mean sitting far enough off the
#: role hex to suggest the repair mushed real texture (the three paid runs'
#: spike tiles sat 85-155 off vs tolerance 48). Advisory — warns, never
#: gates (name-skipped in _flip_review_status).
PALETTE_DRIFT_SPIKE = 2.0 * PALETTE_TOLERANCE

#: Composite-readability floors (RB3): a placement is flagged for HUMAN
#: review when its rendered surroundings sit under the luminance floor
#: AND under the hue-separation floor (or either side is colorless).
#: ADVISORY-ONLY by user lock — the check never fails, never blocks,
#: never warns the manifest; it only informs the logs + report.
COMPOSITE_MIN_LUMA = 30.0
COMPOSITE_MIN_HUE_SEP = 40.0

#: Free-text fields are clamped in code — a rambling model can't bloat the
#: report or the manifest warnings.
NOTES_MAX_CHARS = 300

# --- Animation authoring (B2) ------------------------------------------------

#: The closed animation-state vocabulary. Both play surfaces ALREADY track
#: each of these: ``walk`` while moving, ``hurt`` while hurt_t>0, ``death``
#: when !alive, else ``idle``. The VLM spec and frames.json share these keys.
#: This is the BASE enemy set — :func:`enemy_animation_states` widens it per
#: archetype (a hopper adds ``jump`` for its airborne arc).
ANIMATION_STATES: tuple[str, ...] = ("idle", "walk", "hurt", "death")

#: The PLAYER's states. The player jumps (enemies don't) and never plays a
#: death cycle (it respawns), so its set differs from the enemy set. Both
#: surfaces track: rising airborne → ``jump``, descending → ``fall``, the
#: brief touch-down window → ``land``, braking against carried momentum →
#: ``skid``, moving on ground → ``walk``, else idle.
PLAYER_ANIMATION_STATES: tuple[str, ...] = (
    "idle", "walk", "jump", "fall", "land", "skid",
)

#: One-line meaning of each state, woven into the authoring prompt so motion
#: is grounded in what the state actually is (a flyer's ``walk`` is flight).
_STATE_BRIEF: dict[str, str] = {
    "idle": "at rest / holding position (a flyer hovers, a sentry barely stirs)",
    "walk": "actively moving along its path (for a flyer/swimmer: flight/swim)",
    "hurt": "recoiling from a hit — a brief flinch",
    "death": "defeated — collapse, tumble, or fade",
    # Transient states carry an explicit SILHOUETTE contract so a generator
    # can't collapse them into near-identical standing frames (postmortem
    # ticket 8): each names a distinct outline the others don't share.
    "jump": "the RISING launch — a compact crouch then push-off, body "
            "gathered and tucked, moving UP; a rounded rising silhouette, "
            "clearly NOT the stretched trailing-limb fall",
    "fall": "the DESCENT past the peak — body stretched tall and vertical, "
            "arms, limbs and hair trailing UPWARD; a tall narrow silhouette",
    "land": "the touchdown SQUASH — body compressed low and WIDE, legs fully "
            "folded, an implied dust puff; a short wide silhouette, clearly "
            "flatter than idle",
    "skid": "braking against carried momentum — torso leaned BACK, legs "
            "thrust FORWARD and braced; a diagonal leaning silhouette, "
            "distinct from an upright walk",
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
    "fall": 3, "land": 2, "skid": 2,
}

#: How each state's cycle plays back on both surfaces: ``loop`` wraps
#: forever, ``once`` holds the last frame (a death collapse must not
#: restart mid-linger). Unknown states fall back to ``loop``.
STATE_LOOP_MODES: dict[str, str] = {
    "idle": "loop", "walk": "loop", "jump": "once", "fall": "loop",
    "land": "once", "skid": "loop", "hurt": "once", "death": "once",
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


def _flip_review_status(
    ctx: Any, checks: list[dict], level_entries: dict[str, dict] | None = None
) -> None:
    """QA is the review gate (graphics arc): each art entity's
    ``review_status`` flips draft→approved when every code check naming
    it passed, needs-rework otherwise. Recomputed every QA run
    (idempotent); a REJECTED status is a human verdict and never
    overwritten here.

    ``level_entries`` (postmortem ticket 4) adds the VLM verdict as a
    second demotion input: a failing per-level verdict that names an
    asset in its sanitized ``suggested_regen_targets`` demotes that asset
    (any single failing verdict is enough), merged with the code checks.
    Verdict dimensions are a disjoint namespace from check names, so the
    coverage/composite_contrast skips below (incl. the RB3 advisory lock)
    are structurally unaffected."""
    by_target: dict[str, bool] = {}
    for record in checks:
        if record.get("check") in (
            "coverage", "composite_contrast", "palette_drift",
        ):
            # coverage is the informational ledger; composite_contrast is
            # ADVISORY-ONLY (RB3); palette_drift is the ticket-6 upstream
            # METER (warns on a spike, never gates — conform already
            # repaired the output it measures).
            continue
        target = str(record.get("target", ""))
        by_target[target] = by_target.get(target, True) and bool(
            record.get("passed")
        )
    for entry in (level_entries or {}).values():
        if "error" in entry:
            continue
        verdicts = entry.get("verdicts", {})
        if all(v.get("passed", True) for v in verdicts.values()):
            continue
        # A failing verdict forces the blamed asset False regardless of its
        # code checks (False is absorbing → order-independent, deterministic).
        for target in entry.get("suggested_regen_targets") or []:
            by_target[str(target)] = False

    def _entity(target: str) -> Any:
        kind, _, ident = target.partition(":")
        if kind == "enemy":
            return ctx.bible.enemy_definitions.get(ident)
        if kind == "item":
            return getattr(ctx.bible, "items", {}).get(ident)
        if kind == "tileset":
            return ctx.bible.tilesets.get(ident)
        if kind == "props":
            return getattr(ctx.bible, "props", {}).get(ident)
        if kind == "backdrop":
            return getattr(ctx.bible, "backdrops", {}).get(ident)
        if target == "player":
            return getattr(ctx.bible, "player", None)
        return None

    for target, passed in by_target.items():
        entity = _entity(target)
        if entity is None or entity.review_status == "rejected":
            continue
        entity.review_status = "approved" if passed else "needs-rework"


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
            # Version tolerance: get_flattened_data (its replacement)
            # doesn't exist at the declared Pillow>=10.0 floor, while
            # getdata is deprecated for removal in Pillow 14. Same
            # (r,g,b,a)-tuple shape from both.
            getter = getattr(region, "get_flattened_data", region.getdata)
            px = list(getter())
            opaque = [(r, g, b) for r, g, b, a in px if a > 0]
            n = len(opaque)
            return tuple(sum(c[i] for c in opaque) // n for i in range(3))
    return region.convert("RGB").resize((1, 1), Image.BILINEAR).getpixel((0, 0))


def _hex_rgb(color: str) -> tuple[int, int, int]:
    raw = color.lstrip("#")
    return tuple(int(raw[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def _sprite_checks(
    ctx: Any, target: str, sprite_path: str,
    min_fill: float = SPRITE_MIN_FILL,
) -> list[dict]:
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
            "passed": fill >= min_fill,
            "detail": (
                f"opaque bbox spans {fill:.2f} of the canvas "
                f"(min {min_fill:.2f}) — consumers scale the whole "
                f"canvas onto the body's cells"
            ),
        }
    )
    return checks


def _stage_level_and_room_ids(ctx: Any, stage: Any) -> list[str]:
    """A stage's level ids with each level's SECRET ROOMS interleaved
    right after their parent (multi-room arc) — rooms get review renders
    and QA rows like any level; the order matches the build order."""
    out: list[str] = []
    for level_id in (stage.level_ids if stage else []):
        out.append(level_id)
        level = ctx.bible.levels.get(level_id)
        if level is not None:
            out.extend(getattr(level, "secret_rooms", None) or [])
    return out


def _composite_contrast_checks(
    ctx: Any, stage_id: str, tiles: TileRegistry, graphics: Any,
    variants: VariantSet,
) -> list[dict]:
    """ADVISORY-ONLY composite readability (RB3, user-locked): sample
    each placement's RENDERED surroundings in the skinned PNG and flag
    camouflage — under the luminance floor AND without hue separation.
    Every record ships ``passed: True`` by construction (the coverage
    precedent): nothing here can fail a build, flip review status, or
    become a manifest warning — "inform the logs that a user should
    review the level/area" is the entire contract.

    The sampled sprite box mirrors render_level_skinned's actor draw
    (px from slot 0, effective_size x occupancy x actor_scale,
    feet-anchored); the background patch is the patrol strip minus that
    box (items and sentries get a 1-cell ring), area-weighted over the
    left/right sub-crops, falling back one-sided at level edges and to
    one tile-row above the box when both sides vanish. Entity color is
    the sprite's visible-pixel mean when its art resolves, else the
    placeholder actually drawn — honest in both trees. Fixed iteration
    order + float formats keep the report byte-deterministic."""
    from PIL import Image

    gfx = graphics if graphics is not None else DEFAULT_GRAPHICS
    tileset = ctx.bible.tilesets.get(stage_id)
    px = (
        tileset.slots[0].px_region[2]
        if tileset is not None and tileset.slots
        else 32
    )
    records: list[dict] = []
    sampled = flagged = levels_seen = 0
    missing: list[str] = []
    stage = ctx.bible.stages.get(stage_id)
    for level_id in _stage_level_and_room_ids(ctx, stage):
        level = ctx.bible.levels.get(level_id)
        if level is None:
            continue
        skinned = ctx.adapter.resolve_path(
            f"review/{stage_id}/{level_id}_skinned.png"
        )
        if not skinned.exists():
            missing.append(level_id)
            continue
        levels_seen += 1
        img = Image.open(skinned).convert("RGB")
        grid_w = max(int(level.grid_width), 1)
        placements = [
            *((p, "enemy") for p in level.entities),
            *((p, "item") for p in getattr(level, "items", []) or []),
        ]
        for placement, kind in placements:
            x, y = placement.pos
            ident = placement.ref.split(":", 1)[1]
            if kind == "enemy":
                enemy = ctx.bible.enemy_definitions.get(ident)
                variant = variants.by_name.get(
                    str(placement.overrides.get("variant", ""))
                )
                eff = effective_size(
                    float(getattr(enemy, "size", 1.0) or 1.0) if enemy else 1.0,
                    variant.size if variant else 1.0,
                )
                cols, _rows = occupancy(eff)
                vis = max(1, round((px - 4) * eff * gfx.actor_scale))
                left = round((x + cols / 2.0) * px - vis / 2.0)
                feet = (y + 1) * px - 2
                box = (left, feet - vis, left + vis, feet)
                # Sentries hold position — their ring is 1 cell like
                # items; everyone else strips their real patrol reach.
                if enemy is not None and enemy.archetype == "sentry":
                    margin = 1
                else:
                    behavior = enemy.behavior if enemy else {}
                    margin = max(
                        int(float(behavior.get("patrol_range", 0) or 0)), 1
                    )
                sprite_rel = enemy.sprite_path if enemy else ""
                stats = (enemy.stats if enemy else {}) or {}
                fallback_hex = stats.get("placeholder_color") or "#ff00ff"
            else:
                item = getattr(ctx.bible, "items", {}).get(ident)
                cols, margin = 1, 1
                box = (x * px, y * px, (x + 1) * px, (y + 1) * px)
                sprite_rel = item.sprite_path if item else ""
                stats = (item.stats if item else {}) or {}
                fallback_hex = stats.get("placeholder_color") or "#ffd700"
            sprite_path = (
                ctx.adapter.resolve_path(sprite_rel) if sprite_rel else None
            )
            if sprite_path is not None and sprite_path.exists():
                entity_rgb = _mean_rgb(Image.open(sprite_path).convert("RGBA"))
            else:
                entity_rgb = _hex_rgb(fallback_hex)

            c0 = max(0, x - margin)
            c1 = min(grid_w - 1, x + cols - 1 + margin)
            top = max(box[1], 0)
            bottom = min(box[3], img.height)
            patches = [
                crop
                for crop in (
                    (c0 * px, top, min(max(box[0], 0), (c1 + 1) * px), bottom),
                    (max(box[2], c0 * px), top, (c1 + 1) * px, bottom),
                )
                if crop[2] > crop[0] and crop[3] > crop[1]
            ]
            if not patches:
                # Degenerate strip (box swallows it): one tile-row above.
                above = (
                    max(box[0], 0), max(top - px, 0),
                    min(box[2], img.width), top,
                )
                if above[2] > above[0] and above[3] > above[1]:
                    patches = [above]
            sampled += 1
            if not patches:
                continue
            weighted = [0.0, 0.0, 0.0]
            area_total = 0
            for crop in patches:
                area = (crop[2] - crop[0]) * (crop[3] - crop[1])
                mean = _mean_rgb(img.crop(crop))
                for i in range(3):
                    weighted[i] += mean[i] * area
                area_total += area
            bg_rgb = tuple(round(v / area_total) for v in weighted)

            entity_hex = color_math.rgb_to_hex(entity_rgb)
            bg_hex = color_math.rgb_to_hex(bg_rgb)  # type: ignore[arg-type]
            delta_l = abs(
                color_math.luminance(entity_hex) - color_math.luminance(bg_hex)
            )
            entity_hue = color_math.hue_of(entity_hex)
            bg_hue = color_math.hue_of(bg_hex)
            colorless = entity_hue is None or bg_hue is None
            hue_sep = (
                None if colorless
                else color_math.hue_distance(entity_hue, bg_hue)
            )
            if delta_l >= COMPOSITE_MIN_LUMA or (
                hue_sep is not None and hue_sep >= COMPOSITE_MIN_HUE_SEP
            ):
                continue
            flagged += 1
            detail = (
                f"ADVISORY — review {level_id} near ({x},{y}): "
                f"{placement.ref} vs surroundings deltaL={delta_l:.1f} "
                f"(floor {COMPOSITE_MIN_LUMA:.0f}), hue_sep="
                f"{'n/a (colorless)' if colorless else f'{hue_sep:.1f}'} "
                f"(floor {COMPOSITE_MIN_HUE_SEP:.0f}); patrol strip "
                f"x in [{c0},{c1}] cells"
            )
            records.append(
                {
                    "check": "composite_contrast",
                    "target": level_id,
                    "subject": f"{placement.ref}@({x},{y})",
                    "passed": True,  # ADVISORY by construction, never fails
                    "detail": detail,
                }
            )
            logger.info("composite_contrast: %s", detail)

    ledger = (
        f"ADVISORY (never blocks): sampled {sampled} placement(s) across "
        f"{levels_seen} level(s); {flagged} flagged"
    )
    if missing:
        ledger += "; skinned missing for: " + ", ".join(missing)
    records.append(
        {
            "check": "composite_contrast",
            "target": f"stage:{stage_id}",
            "subject": "composite readability ledger",
            "passed": True,  # informational ledger, never a failure
            "detail": ledger,
        }
    )
    if flagged:
        logger.warning(
            "composite_contrast stage %s: %d of %d sampled placement(s) "
            "flagged for review (report: %s) — advisory-only, nothing "
            "blocked or regenerated.",
            stage_id, flagged, sampled, qa_report_rel(stage_id),
        )
    return records


def run_code_checks(
    ctx: Any, stage_id: str, tiles: TileRegistry = DEFAULT_TILES,
    graphics: Any = None, variants: VariantSet = DEFAULT_VARIANTS,
) -> list[dict]:
    """Everything computable about the stage's art, as report records.

    Order is fixed (tileset slots by index, enemies sorted, player last)
    so the report is byte-deterministic. ``graphics`` (when given)
    enables the crisp-lane GRID-CONFORMANCE gate: on a crisp lane every
    generated alpha must be binarized (0/255 — grid_snap's discipline;
    fringe half-pixels are the off-grid smear the lane forbids).
    ``variants`` feeds the ADVISORY composite-contrast block appended
    last — always ``passed: True``, never a warning (RB3 user lock).
    """
    from PIL import Image

    checks: list[dict] = []
    # A fallback level must fail its QA report too — the VLM verdicts
    # only judge render truth, and an empty fallback level renders
    # faithfully; without this echo the report reads all-green for a
    # level that shipped no generated content (sunlit-run finding).
    stage = ctx.bible.stages.get(stage_id)
    for level_id in _stage_level_and_room_ids(ctx, stage):
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
                    f"level shipped FALLBACK layout content — whole or "
                    f"partial, not fully generated design (attempt trace: "
                    f"review/{stage_id}/{level_id}_layout_attempts.json)"
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
                mask = (slot.params or {}).get("autotile_mask")
                if mask is not None and int(mask) != 0:
                    # DEDUPE (ticket 6): the 16 autotile floor variants share
                    # one generated square with mean-preserving shading — 16
                    # structurally-identical records were noise. One record
                    # per tile NAME (the mask-0 base) carries the signal.
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

    # PALETTE DRIFT METER (ticket 6): the tileset phase snapshots how far
    # each RAW generated tile's mean sat from the role hex BEFORE
    # conform_to_palette repaired it (review/<stage>/palette_drift.json,
    # real backends only). A spike means the backend badly mis-aimed and
    # the repair may have mushed texture — ADVISORY: it warns, and
    # _flip_review_status name-skips it so it never gates approval.
    drift_path = ctx.adapter.resolve_path(
        f"review/{stage_id}/palette_drift.json"
    )
    if drift_path.exists():
        try:
            drift_map = json.loads(drift_path.read_text())
        except (OSError, ValueError):
            drift_map = {}
        for name in sorted(drift_map):
            dist = float(drift_map[name])
            checks.append(
                {
                    "check": "palette_drift",
                    "target": f"tileset:{stage_id}",
                    "subject": name,
                    "passed": dist <= PALETTE_DRIFT_SPIKE,
                    "detail": (
                        f"tile {name!r} raw pre-conform mean was {dist:.0f} "
                        f"off palette (spike at {PALETTE_DRIFT_SPIKE:.0f}) — "
                        "conform repaired it; a spike = backend mis-aim"
                    ),
                }
            )

    # BACKDROP TILING: every consumer wraps bands horizontally, so the
    # left/right edge columns must meet without a visible seam. Compares
    # the per-channel mean abs diff of column 0 vs column w-1.
    backdrop = getattr(ctx.bible, "backdrops", {}).get(stage_id)
    for rel in (backdrop.band_paths if backdrop is not None else []):
        band_path = ctx.adapter.resolve_path(rel)
        if not band_path.exists():
            continue
        band = Image.open(band_path).convert("RGB")
        w, h = band.size
        # Same Pillow version tolerance as _mean_rgb (getdata deprecated,
        # get_flattened_data missing at the declared floor).
        cols = [band.crop((0, 0, 1, h)), band.crop((w - 1, 0, w, h))]
        left, right = (
            list(getattr(c, "get_flattened_data", c.getdata)()) for c in cols
        )
        means = [
            sum(abs(a[c] - b[c]) for a, b in zip(left, right)) / h
            for c in range(3)
        ]
        dist = sum(m * m for m in means) ** 0.5
        checks.append(
            {
                "check": "backdrop_tiling",
                "target": f"backdrop:{stage_id}",
                "subject": rel,
                "passed": dist <= BACKDROP_SEAM_TOLERANCE,
                "detail": (
                    f"left/right edge columns differ by {dist:.0f} "
                    f"(tolerance {BACKDROP_SEAM_TOLERANCE:.0f}) — bands "
                    f"tile horizontally in every consumer"
                ),
            }
        )

    for enemy_id in sorted(ctx.bible.enemy_definitions):
        enemy = ctx.bible.enemy_definitions[enemy_id]
        if enemy.sprite_path:
            checks.extend(_sprite_checks(ctx, f"enemy:{enemy_id}", enemy.sprite_path))
    for item_id in sorted(getattr(ctx.bible, "items", {})):
        item = ctx.bible.items[item_id]
        if item.sprite_path:
            checks.extend(
                _sprite_checks(ctx, f"item:{item_id}", item.sprite_path)
            )
    player = getattr(ctx.bible, "player", None)
    if player is not None and player.sprite_path:
        checks.extend(_sprite_checks(ctx, "player", player.sprite_path))
    stage_props = getattr(ctx.bible, "props", {}).get(stage_id)
    if stage_props is not None:
        for name, rel in sorted(stage_props.prop_paths.items()):
            checks.extend(
                _sprite_checks(
                    ctx, f"props:{stage_id}", rel,
                    min_fill=(
                        VFX_MIN_FILL
                        if name in VFX_PROP_NAMES
                        else SPRITE_MIN_FILL
                    ),
                )
            )

    # grid_conformance was DROPPED here (postmortem ticket 6): it measured
    # binarized alpha AFTER grid_snap had already binarized it — a gate that
    # structurally could not fail. Backend-quality drift is now surfaced by
    # the UPSTREAM meters (palette_drift below, animation_wander) that
    # measure BEFORE the code correctors run.

    # COVERAGE (the asset-inventory checklist as one ledger record):
    # every class's generated-vs-placeholder count, so gaps are VISIBLE
    # in the report instead of silently placeholder.
    enemies_done = sum(
        1 for e in ctx.bible.enemy_definitions.values() if e.sprite_path
    )
    items_all = getattr(ctx.bible, "items", {})
    items_done = sum(1 for i in items_all.values() if i.sprite_path)
    props_done = len(stage_props.prop_paths) if stage_props else 0
    checks.append(
        {
            "check": "coverage",
            "target": f"stage:{stage_id}",
            "subject": "asset inventory",
            "passed": True,  # informational ledger, never a failure
            "detail": (
                f"enemies {enemies_done}/{len(ctx.bible.enemy_definitions)}"
                f", items {items_done}/{len(items_all)}"
                f", player {'yes' if player and player.sprite_path else 'placeholder'}"
                f", props {props_done}"
                " — accepted v1 gaps: HUD font, world-map art"
            ),
        }
    )
    checks.extend(
        _composite_contrast_checks(ctx, stage_id, tiles, graphics, variants)
    )
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
    placed_items = sorted(
        {
            p.ref
            for p in getattr(level, "items", []) or []
            if p.ref.startswith("item:")
        }
    )
    targets = [
        level.level_id, *placed, *placed_items, "player",
        f"tileset:{stage_id}",
    ]
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
    items: dict[str, Any] | None = None,
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
    item_facts = []
    for p in getattr(level, "items", []) or []:
        item_id = p.ref.split(":", 1)[1]
        item = (items or {}).get(item_id)
        source = str(p.overrides.get("source", "trail"))
        item_facts.append(
            f"{(item.name if item else item_id)} "
            f"({getattr(item, 'kind', '?')}) at {list(p.pos)}"
            + (" in a box" if source == "box" else "")
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
        f"player sees. Each checkpoint appears as a small flag — that is "
        f"correct, not extra content. The exit has no marker here; the "
        f"level simply ends at the right edge / summit.\n"
        f"3. ROSTER LEGEND — enemy names, placeholder colors, sizes, and "
        f"stats.\n"
        f"4. PLAY-SCALE SPAWN VIEW — the skinned render cropped to the "
        f"camera's actual framing around the spawn: what the player "
        f"really sees at play zoom.\n"
        f"5. PLAY-SCALE EXIT VIEW — the same framing around the exit.\n\n"
        f"Level facts (the truth the skinned render must match):\n"
        f"- grid {level.grid_width}x{level.grid_height} cells; spawn "
        f"{list(level.spawn) if level.spawn else '?'}; exit "
        f"{list(level.exit) if level.exit else '?'}\n"
        f"- placements: {'; '.join(placements) or 'none'}\n"
        f"- items (colored circles; a bronze tile = an item BOX the "
        f"player opens): {'; '.join(item_facts) or 'none'}\n"
        f"- hazard cells: {len(level.hazards)}; checkpoint columns: "
        f"{checkpoints or 'none'}; decor records: {len(level.foreground)}\n"
        f"- palette (color_role: hex): {palette_desc}\n\n"
        f"Judge these three dimensions strictly:\n"
        f'1. "fidelity" — does the skinned render match the block truth? '
        f"Every placement present at the right spot and effective size, "
        f"terrain shapes identical, nothing missing, nothing extra.\n"
        f'2. "readability" — are the player spawn, enemies, and hazards '
        f"clearly distinguishable from the tiles and the backdrop at a "
        f"glance? Weigh the PLAY-SCALE views heavily: a level readable "
        f"zoomed-out can still fail at the zoom the player actually "
        f"plays at.\n"
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


def enemy_animation_states(enemy: Any) -> tuple[str, ...]:
    """The state set ONE enemy authors/generates: the base vocabulary, plus
    ``jump`` for a hopper (both surfaces track its airborne arc via
    ``e_grounded``). Same archetype lookup as
    :func:`enemy_animation_subject`."""
    archetype = enemy.archetype or "patroller"
    if archetype == "hopper":
        return (*ANIMATION_STATES, "jump")
    return ANIMATION_STATES


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
        if check.get("check") == "composite_contrast":
            # ADVISORY-ONLY (RB3, user lock): never a manifest warning —
            # even a hand-corrupted passed=False record stays log-only.
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


#: Canonical state order for the contact sheet + prompt — the union of every
#: actor's states (enemy idle/walk/hurt/death + hopper jump AND player
#: idle/walk/jump/fall/land/skid). A state missing here silently drops off
#: the QA contact sheet.
_STATE_ORDER: tuple[str, ...] = (
    "idle", "walk", "jump", "fall", "land", "skid", "hurt", "death",
)

#: PRE-normalize baseline-wander tolerance as a fraction of the generated
#: sheet's height (postmortem ticket 6). The old post-normalize registration
#: check measured jitter AFTER normalize_frames had re-anchored every frame —
#: structurally always ~zero. The wander meter reads the spread of the RAW
#: crops' content bottoms (recorded by the animation phase at generation
#: time) — how far the backend actually drifted before code corrected it.
#: ADVISORY: a spike warns; it never gates approval.
ANIM_WANDER_MAX_FRAC = 0.20

#: Sibling-state silhouette near-dupe threshold (postmortem ticket 8): two
#: states whose representative-frame alpha masks overlap at or above this IoU
#: read as the same pose (transient fall/land/skid + hopper jump are the
#: usual offenders). ADVISORY — warns so the signal reaches a run WITHOUT a
#: VLM backend that would otherwise catch it; never flips review_status.
ANIM_SILHOUETTE_IOU_MAX = 0.70


def _silhouette_mask(crops: list, size: int = 24) -> list:
    """Downsampled boolean alpha mask of a strip's representative (middle)
    frame — the silhouette, for sibling-state near-dupe comparison."""
    frame = crops[len(crops) // 2]
    alpha = frame.getchannel("A").resize((size, size))
    return [px > 32 for px in alpha.get_flattened_data()]


def _animation_checks(ctx: Any, actor_id: str, animation: dict) -> list[dict]:
    """Computable checks over one actor's generated animation: each state strip
    exists, its width matches frame_width x frames, no sliced frame is blank,
    the recorded PRE-normalize baseline wander stays under tolerance (the
    upstream registration meter, ticket 6), sibling states keep distinct
    silhouettes (ticket 8), and the packed atlas (when present) matches the
    manifest. code-not-LLM — the VLM verdict handles only the visual read."""
    from PIL import Image

    checks: list[dict] = []
    target = actor_id
    masks: dict[str, list] = {}
    states = animation.get("states") or {}
    for state, meta in sorted(states.items()):
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
        crops = [img.crop((i * fw, 0, (i + 1) * fw, img.height)) for i in range(n)]
        masks[state] = _silhouette_mask(crops)
        blank = []
        for i, frame in enumerate(crops):
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
        # WANDER METER (ticket 6, replaces the tautological registration
        # check): the animation phase records the PRE-normalize baseline
        # spread of the raw segmented crops; a spike means the backend's
        # frames wandered before normalize_frames re-anchored them. Old
        # trees without the key stay silent (the atlas-check precedent).
        wander = meta.get("raw_bottom_wander_frac")
        if wander is not None:
            wf = float(wander)
            checks.append({
                "check": "animation_wander", "target": target,
                "subject": state,
                "passed": wf <= ANIM_WANDER_MAX_FRAC,
                "detail": (
                    f"{state}: pre-normalize baseline wander {wf * 100:.0f}% "
                    f"of sheet height (max {ANIM_WANDER_MAX_FRAC * 100:.0f}%)"
                    " — measured upstream of the normalize re-anchor"
                ),
            })
    # Sibling-state silhouette near-dupes (ticket 8): transient states can
    # render as the same pose. Only NEAR-DUPE pairs are recorded (a distinct
    # set adds nothing → byte-clean), and each is advisory — it warns but
    # never flips review_status.
    named = sorted(masks.items())
    for i in range(len(named)):
        for j in range(i + 1, len(named)):
            sa, ma = named[i]
            sb, mb = named[j]
            inter = sum(1 for a, b in zip(ma, mb) if a and b)
            union = sum(1 for a, b in zip(ma, mb) if a or b)
            iou = inter / union if union else 0.0
            if iou >= ANIM_SILHOUETTE_IOU_MAX:
                checks.append({
                    "check": "animation_distinct", "target": target,
                    "subject": f"{sa}|{sb}", "passed": False,
                    "detail": (
                        f"{sa} and {sb} silhouettes overlap at IoU "
                        f"{iou:.2f} (>= {ANIM_SILHOUETTE_IOU_MAX:.2f}) — "
                        "they read as the same pose"
                    ),
                })
    atlas_record = _atlas_check(ctx, target, states)
    if atlas_record is not None:
        checks.append(atlas_record)
    return checks


def _atlas_check(ctx: Any, target: str, states: dict) -> dict | None:
    """One packed-atlas record per actor: atlas.png present, every manifest
    state in atlas.json at the same frame count, every rect inside the atlas
    bounds. None (no record) when no atlas.json sits beside the strips — old
    trees without a packed atlas stay silent."""
    from PIL import Image

    first_rel = next(
        (str(m.get("path", "")) for m in states.values() if m.get("path")), ""
    )
    if not first_rel:
        return None
    sprite_dir = first_rel.rsplit("/", 1)[0]
    meta_path = ctx.adapter.resolve_path(f"{sprite_dir}/atlas.json")
    if not meta_path.exists():
        return None
    record = {"check": "animation_atlas", "target": target, "subject": "atlas"}
    try:
        atlas_meta = json.loads(meta_path.read_text())
    except (OSError, ValueError):
        return record | {"passed": False, "detail": "atlas.json unreadable"}
    atlas_rel = str(atlas_meta.get("path", f"{sprite_dir}/atlas.png"))
    atlas_path = ctx.adapter.resolve_path(atlas_rel)
    if not atlas_path.exists():
        return record | {"passed": False, "detail": f"{atlas_rel} missing on disk"}
    atlas_img = Image.open(atlas_path)
    atlas_states = atlas_meta.get("states") or {}
    problems: list[str] = []
    for state, meta in sorted(states.items()):
        entry = atlas_states.get(state)
        if not isinstance(entry, dict):
            problems.append(f"{state} absent from atlas.json")
            continue
        rects = entry.get("frames") or []
        if len(rects) != int(meta.get("frames", 0)):
            problems.append(
                f"{state}: {len(rects)} atlas frame(s) vs manifest "
                f"{int(meta.get('frames', 0))}"
            )
        for rect in (*rects, *(entry.get("frames_left") or [])):
            x, y = int(rect.get("x", 0)), int(rect.get("y", 0))
            w, h = int(rect.get("w", 0)), int(rect.get("h", 0))
            if x < 0 or y < 0 or x + w > atlas_img.width or y + h > atlas_img.height:
                problems.append(f"{state}: rect ({x},{y},{w},{h}) outside atlas")
                break
    return record | {
        "passed": not problems,
        "detail": (
            f"atlas covers {len(states)} state(s)" if not problems
            else "; ".join(problems)
        ),
    }


def _animation_contact_sheet(
    ctx: Any, animation: dict, scale: int = 4
) -> tuple[bytes | None, list[str]]:
    """One image for the VLM: each state's strip on its own row (top→bottom in
    _STATE_ORDER), upscaled so small sprites read. Returns ``(png, rendered)``
    where ``rendered`` is the states actually drawn, in row order — so the
    prompt can name EXACTLY the rows present and a dropped strip can't shift
    the per-row verdicts under the labels (postmortem ticket 5e). ``(None, [])``
    if nothing loads."""
    import io

    from PIL import Image

    states = animation.get("states") or {}
    rows = []
    rendered: list[str] = []
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
        rendered.append(state)
    if not rows:
        return None, []
    width = max(r.width for r in rows)
    height = sum(r.height for r in rows) + 2 * (len(rows) - 1)
    sheet = Image.new("RGBA", (width, height), (60, 60, 70, 255))
    y = 0
    for r in rows:
        sheet.paste(r, (0, y), r)
        y += r.height + 2
    buf = io.BytesIO()
    sheet.save(buf, format="PNG")
    return buf.getvalue(), rendered


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
        sheet, rendered = (
            _animation_contact_sheet(ctx, animation)
            if judge is not None else (None, [])
        )
        if sheet is not None:
            # Cross-check the sheet's rows against the DECLARED state set: a
            # strip missing on disk is skipped in the sheet but the actor still
            # declares it, so per-row verdicts would shift under the labels.
            # Record the mismatch so it warns before the verdict is trusted
            # (postmortem ticket 5e). Only appended on a real drop → the
            # healthy path is byte-unchanged.
            declared = [
                s for s in _STATE_ORDER if s in (animation.get("states") or {})
            ]
            if rendered != declared:
                entry["code_checks"].append({
                    "check": "animation_state_coverage", "target": actor_id,
                    "subject": actor_id, "passed": False,
                    "detail": (
                        f"declared rows {declared} but only {rendered} rendered "
                        "into the QA sheet — per-row verdicts are row-shifted"
                    ),
                })
            prompt = animate_qa_prompt(actor_id, name, rendered)
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
            # asks for (enemies get idle/walk/hurt/death + a hopper's jump,
            # the player gets idle/walk/jump/fall/land/skid). ``death`` sits
            # ABOVE the enemy clamp ceiling (9 → 6) so
            # _sanitize_animation_spec's clamp runs in a fake run. A state
            # missing here is filtered from the reply → the spec never
            # validates → the WHOLE actor stays static in fake runs.
            canned_frames = {
                "idle": 2, "walk": 4, "hurt": 2, "death": 9, "jump": 5,
                "fall": 3, "land": 2, "skid": 2,
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


def write_play_scale_crops(
    ctx: Any, stage_id: str, level: Any, graphics: GraphicsSpec
) -> dict[str, str]:
    """Deterministic ``view_cells`` x ``view_rows`` crops of the SKINNED
    render, centred on the spawn and the exit (clamped to the grid) —
    the closest headless stand-in for what the player sees at camera
    zoom (Godot cannot screenshot headless on macOS/MoltenVK). Pure
    function of the skinned PNG + level dims + the graphics spec, so
    re-runs rewrite identical bytes. Returns {name: rel} of what was
    written; empty when the skinned render doesn't exist (the caller's
    missing-image path stays loud)."""
    from PIL import Image

    skinned = ctx.adapter.resolve_path(
        f"review/{stage_id}/{level.level_id}_skinned.png"
    )
    if not skinned.exists():
        return {}
    grid_w = max(int(level.grid_width), 1)
    grid_h = max(int(level.grid_height), 1)
    # Per-level view overrides (intimate/vista presets) are what the
    # consumers actually frame with — the crop must match THAT zoom.
    view_cells = int(getattr(level, "view_cells", None) or graphics.view_cells)
    view_rows = int(getattr(level, "view_rows", None) or graphics.view_rows)
    anchors = {
        "play_spawn": tuple(level.spawn) if level.spawn else (2, grid_h - 2),
        "play_exit": tuple(level.exit) if level.exit else (grid_w - 2, 2),
    }
    rels: dict[str, str] = {}
    with Image.open(skinned) as img:
        img.load()
        width, height = img.size
        px = max(width // grid_w, 1)
        win_w = min(view_cells, grid_w) * px
        win_h = min(view_rows, grid_h) * px
        for name, (ax, ay) in anchors.items():
            cx = (float(ax) + 0.5) * px
            cy = (float(ay) + 0.5) * px
            left = int(min(max(cx - win_w / 2, 0), max(width - win_w, 0)))
            top = int(min(max(cy - win_h / 2, 0), max(height - win_h, 0)))
            crop = img.crop((left, top, left + win_w, top + win_h))
            buffer = io.BytesIO()
            crop.save(buffer, format="PNG")
            rel = f"review/{stage_id}/{level.level_id}_{name}.png"
            ctx.adapter.write_binary(rel, buffer.getvalue())
            rels[name] = rel
    return rels


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
    """Judge every level's render set; write the QA report; surface
    failures as durable manifest warnings. NO auto-regen, ever.

    Cadence (v2): runs only when a judge was built from an explicit flag —
    an `always` node in the DAG, a plain no-op stamp otherwise — but
    re-judging is STALENESS-AWARE: each level entry records sha256 hashes
    of exactly what the judge saw (``judged_inputs``), and a re-run whose
    inputs and judge model are unchanged CARRIES the previous entry
    byte-identically without a VLM call (the skip is logged, never
    written into the report — reports stay deterministic). Code checks
    still recompute every run ($0). An unflagged run leaves the last
    report — and its warnings — standing. Animation QA keeps the v1
    always-re-judge cadence (small, per-enemy).
    """

    name = "plat:vlm_qa"

    def __init__(
        self,
        judge: Any = None,
        tiles: TileRegistry = DEFAULT_TILES,
        variants: VariantSet = DEFAULT_VARIANTS,
        graphics: GraphicsSpec = DEFAULT_GRAPHICS,
    ) -> None:
        self.judge = judge
        self.tiles = tiles
        self.variants = variants
        self.graphics = graphics

    def _model_id(self) -> str:
        return str(getattr(self.judge, "model", type(self.judge).__name__))

    def _previous_levels(self, ctx: Any, stage_id: str) -> dict:
        """The prior on-disk report's level entries, usable for carrying
        only when the judge model matches (a model change re-judges
        everything)."""
        path = ctx.adapter.resolve_path(qa_report_rel(stage_id))
        if not path.exists():
            return {}
        try:
            report = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError, ValueError):
            return {}
        if str(report.get("vlm_model")) != self._model_id():
            return {}
        levels = report.get("levels")
        return levels if isinstance(levels, dict) else {}

    def run(self, ctx: Any) -> None:
        if self.judge is None:
            logger.info(
                "VlmQaPhase: no VLM judge (explicit --vlm-backend required) "
                "— QA skipped; any existing report stands."
            )
            _stamp_metadata(ctx, self.name)
            return

        all_checks: list[dict] = []
        all_levels: dict[str, dict] = {}
        for stage_id, stage in ctx.bible.stages.items():
            checks = run_code_checks(
                ctx, stage_id, tiles=self.tiles, graphics=self.graphics,
                variants=self.variants,
            )
            previous = self._previous_levels(ctx, stage_id)
            levels: dict[str, dict] = {}
            carried = 0
            for level_id in _stage_level_and_room_ids(ctx, stage):
                level = ctx.bible.levels.get(level_id)
                if level is None:
                    continue
                entry = self._judge_level(
                    ctx, stage, level, previous=previous.get(level_id)
                )
                if entry is previous.get(level_id):
                    carried += 1
                levels[level_id] = entry
            report = {
                "stage_id": stage_id,
                "vlm_model": self._model_id(),
                "code_checks": checks,
                "levels": levels,
            }
            ctx.adapter.write_json_singleton(qa_report_rel(stage_id), report)
            for message in derive_qa_warnings(report):
                warn(ctx, message)
            all_checks.extend(checks)
            all_levels.update(levels)
            failed = sum(
                1
                for entry in levels.values()
                for dim in DIMENSIONS
                if not entry.get("verdicts", {}).get(dim, {}).get("passed", True)
            )
            logger.info(
                "VlmQaPhase stage %s: %d level(s) — %d judged, %d carried "
                "(inputs unchanged): %d failing verdict(s), %d/%d code "
                "check(s) passed.",
                stage_id, len(levels), len(levels) - carried, carried,
                failed, sum(1 for c in checks if c["passed"]), len(checks),
            )
        # Flip the review gate ONCE across all stages: verdict blame is
        # genuinely stage-scoped, so a per-stage flip is last-stage-wins and
        # would silently re-approve an earlier stage's demotion. World-global
        # code checks are identical per stage → the AND-fold makes duplicates
        # a no-op. (postmortem ticket 4)
        _flip_review_status(ctx, all_checks, all_levels)

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

    def _judge_level(
        self, ctx: Any, stage: Any, level: Any, previous: dict | None = None
    ) -> dict:
        """One level's report entry: the render pair + legend + play-scale
        crops to the judge, retry-validated JSON back; an entry with
        ``error`` (loud via derive_qa_warnings) when images are missing or
        the verdict never validates.

        Staleness: when *previous* (the prior report's entry) hashes to
        exactly the current inputs, it is returned AS-IS — same bytes in
        the report, no VLM call. The crops regenerate first regardless
        (idempotent bytes, $0), so the hash comparison always covers what
        the judge would see."""
        stage_id = stage.stage_id
        image_rels = {
            "block": f"review/{stage_id}/{level.level_id}.png",
            "skinned": f"review/{stage_id}/{level.level_id}_skinned.png",
            "legend": "review/legend.png",
        }
        image_rels.update(
            write_play_scale_crops(ctx, stage_id, level, self.graphics)
        )
        missing = [
            rel
            for rel in image_rels.values()
            if not ctx.adapter.resolve_path(rel).exists()
        ]
        entry: dict[str, Any] = {
            "images": {
                name: rel
                for name, rel in image_rels.items()
                if name != "legend"
            }
        }
        if missing:
            entry["error"] = f"review render(s) missing: {', '.join(missing)}"
            return entry
        images = [
            ctx.adapter.resolve_path(rel).read_bytes()
            for rel in image_rels.values()
        ]
        entry["judged_inputs"] = {
            name: "sha256:" + hashlib.sha256(data).hexdigest()
            for name, data in zip(image_rels, images)
        }
        if (
            previous is not None
            and "error" not in previous
            and previous.get("judged_inputs") == entry["judged_inputs"]
        ):
            logger.info(
                "vlm_qa %s: judged inputs unchanged — verdict carried, "
                "no VLM call.", level.level_id,
            )
            return previous

        tileset = ctx.bible.tilesets.get(stage_id)
        targets = _valid_targets(ctx, stage_id, level)
        prompt = qa_prompt(
            level, stage, ctx.bible.enemy_definitions,
            tileset.palette if tileset else {}, targets,
            variants=self.variants,
            items=getattr(ctx.bible, "items", {}),
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

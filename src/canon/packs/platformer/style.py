"""StyleGuidePhase — the palette agent (3b art track, first LLM art step).

The tile registry gave every tile a ``color_role`` and 3b-1 built the
seam: consumers resolve appearance by sampling the tilesheet through
slots, so ONLY the tileset tool needs colors. This phase asks the LLM to
design a per-game palette for exactly the roles the registry uses —
theme in, ``{role: "#rrggbb"}`` out — and the placeholder tileset paints
with it. ``PLACEHOLDER_PALETTE`` remains the loud fallback. When
diffusion tilesets arrive they seed from the same style guide
(``style/<stage>/style.json``), at the same seam.

Enforced constraints (validator messages are prompts):
- every registry color_role present, valid ``#rrggbb`` hex;
- every role on a non-empty tile must contrast the background role
  (the empty tile's role) by a minimum luminance distance — an
  unreadable level is a failed palette, not a moody one;
- hazard-category roles must be warmer than the background (danger
  should read at a glance).
"""

from __future__ import annotations

import logging
from typing import Any

from canon.packs.platformer import color as color_math
from canon.packs.platformer.phases import _stamp_metadata, llm_json, warn
from canon.packs.platformer.tiles import DEFAULT_TILES, TileRegistry

logger = logging.getLogger(__name__)

#: Minimum |luminance| distance (0..255 scale) from the background role
#: for any role that colors a non-empty tile.
MIN_CONTRAST = 40.0


# Delegating aliases — the math lives in the leaf ``color`` module so
# phases.py can share it without importing style (cycle).
hex_to_rgb = color_math.hex_to_rgb
_luminance = color_math.luminance


def _valid_hex(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 7
        and value.startswith("#")
        and all(c in "0123456789abcdefABCDEF" for c in value[1:])
    )


def role_specs(tiles: TileRegistry) -> list[dict]:
    """The registry's color roles, each with the tiles/categories it
    paints — ordered by first tile id so prompts are deterministic."""
    by_role: dict[str, dict] = {}
    for tile in sorted(tiles.tiles, key=lambda t: t.id):
        if not tile.color_role:
            continue
        spec = by_role.setdefault(
            tile.color_role,
            {"role": tile.color_role, "tiles": [], "categories": []},
        )
        spec["tiles"].append(tile.name)
        if tile.category not in spec["categories"]:
            spec["categories"].append(tile.category)
    return list(by_role.values())


def background_role(tiles: TileRegistry) -> str:
    """The empty tile's role — the canvas everything must read against."""
    empty = next(t for t in tiles.tiles if t.category == "empty")
    return empty.color_role


def check_palette(
    palette: Any, tiles: TileRegistry, contrast: bool = True
) -> list[str]:
    """Problem strings (empty = valid), written to be fed back verbatim.

    ``contrast=False`` checks only the DESIGN concerns (coverage, hex
    form, hazard warmth) — the phase runs that way because contrast is
    arithmetic and :func:`enforce_contrast` repairs it in code instead
    of round-tripping the model (code-for-computation)."""
    if not isinstance(palette, dict):
        return ['"palette" must be an object of {"<role>": "#rrggbb"}.']
    problems: list[str] = []
    specs = role_specs(tiles)
    for spec in specs:
        role = spec["role"]
        value = palette.get(role)
        if value is None:
            problems.append(
                f"palette is missing role {role!r} (colors tiles "
                f"{spec['tiles']!r}) — every role must be present."
            )
        elif not _valid_hex(value):
            problems.append(
                f"palette role {role!r} is {value!r} — colors must be "
                '"#rrggbb" hex.'
            )
    if problems:
        return problems

    bg_role = background_role(tiles)
    bg = palette.get(bg_role)
    if bg is None:
        return [f"palette is missing the background role {bg_role!r}."]
    bg_lum = _luminance(bg)
    for spec in specs:
        role = spec["role"]
        if role == bg_role:
            continue
        if contrast:
            distance = abs(_luminance(palette[role]) - bg_lum)
            if distance < MIN_CONTRAST:
                problems.append(
                    f"role {role!r} ({palette[role]}) is nearly invisible "
                    f"against the background {bg_role!r} ({bg}) — luminance "
                    f"distance {distance:.0f} is below {MIN_CONTRAST:.0f}; "
                    f"lighten or darken {role!r} so tiles stay readable."
                )
        if "hazard" in spec["categories"]:
            r, _g, b = hex_to_rgb(palette[role])
            if r <= b:
                problems.append(
                    f"hazard role {role!r} ({palette[role]}) doesn't read "
                    "as dangerous — make it warmer (red channel above "
                    "blue) so players spot it instantly."
                )
    return problems


_shift_luminance = color_math.shift_luminance


def enforce_contrast(
    palette: dict[str, str], tiles: TileRegistry
) -> tuple[dict[str, str], dict[str, str]]:
    """Repair TOOL for the readability bar: any role too close to the
    background is shifted to exactly the required luminance distance,
    away from the background on whichever side it already leans (hue
    kept, lightness computed). A real model looped for three retries
    nudging dark water against a dark dusk background — this is
    arithmetic, so code does it. Returns (palette, {role: old_hex})."""
    bg_role = background_role(tiles)
    bg_lum = _luminance(palette[bg_role])
    # +2 margin absorbs integer rounding so the checked distance holds.
    up = min(bg_lum + MIN_CONTRAST + 2, 255.0)
    down = max(bg_lum - MIN_CONTRAST - 2, 0.0)
    adjusted: dict[str, str] = {}
    out = dict(palette)
    for spec in role_specs(tiles):
        role = spec["role"]
        if role == bg_role:
            continue
        lum = _luminance(out[role])
        if abs(lum - bg_lum) >= MIN_CONTRAST:
            continue
        target = up if lum >= bg_lum else down
        # A background too near an edge leaves no room on that side.
        if abs(target - bg_lum) < MIN_CONTRAST:
            target = up if target == down else down
        adjusted[role] = out[role]
        out[role] = _shift_luminance(out[role], target)
    return out, adjusted


#: Minimum luminance distance BETWEEN structural roles (ground/platform/
#: wall). The first real palette put all three within ~15 of each other —
#: readable against the background, indistinguishable from one another.
MIN_ROLE_SEPARATION = 22.0


def separate_structural_roles(
    palette: dict[str, str], tiles: TileRegistry
) -> tuple[dict[str, str], dict[str, str]]:
    """Repair TOOL: push STRUCTURAL roles (solid + one_way tile roles)
    apart to ``MIN_ROLE_SEPARATION`` luminance, hue kept — walkable
    surfaces must be tellable apart at a glance, and pairwise spacing is
    arithmetic, not a design choice. Shifts away from the background's
    side where possible so the readability bar survives; run
    :func:`enforce_contrast` after to re-assert it. Returns
    ``(palette, {role: old_hex})``."""
    bg_role = background_role(tiles)
    structural = [
        spec["role"]
        for spec in role_specs(tiles)
        if spec["role"] != bg_role
        and any(c in ("solid", "one_way") for c in spec["categories"])
    ]
    if len(structural) < 2:
        return palette, {}
    out = dict(palette)
    adjusted: dict[str, str] = {}
    bg_lum = _luminance(out[bg_role])
    # Walk roles from nearest-to-background outward, pushing each at
    # least MIN_ROLE_SEPARATION past the previous one, away from the bg.
    ordered = sorted(structural, key=lambda r: abs(_luminance(out[r]) - bg_lum))
    direction = 1.0 if _luminance(out[ordered[0]]) >= bg_lum else -1.0
    previous = _luminance(out[ordered[0]])
    for role in ordered[1:]:
        lum = _luminance(out[role])
        if abs(lum - previous) >= MIN_ROLE_SEPARATION:
            previous = lum
            continue
        target = previous + direction * MIN_ROLE_SEPARATION
        if not 0.0 <= target <= 255.0:  # no room on that side: flip
            target = previous - direction * MIN_ROLE_SEPARATION
        adjusted[role] = out[role]
        out[role] = _shift_luminance(out[role], min(255.0, max(0.0, target)))
        previous = _luminance(out[role])
    return out, adjusted


#: Minimum hue separation (degrees) between a SAFE (non-damaging) volume
#: role and any hazard role. The first paid run's fire biome gave
#: swimmable water a lava hue 6° from `danger` — a safe liquid must
#: never wear the hazard family's colors.
MIN_VOLUME_HAZARD_HUE_SEP = 40.0

#: Where a colliding safe volume's hue snaps to: the blue-cyan band
#: every player reads as "liquid, not lethal".
SAFE_VOLUME_HUE = 215.0


def separate_safe_volumes_from_hazards(
    palette: dict[str, str], tiles: TileRegistry
) -> tuple[dict[str, str], dict[str, str]]:
    """Repair TOOL: a NON-damaging volume role (swimmable water) whose hue
    sits within ``MIN_VOLUME_HAZARD_HUE_SEP`` of any hazard role snaps to
    ``SAFE_VOLUME_HUE`` (saturation/value kept, original luminance
    restored). Damaging volumes (lava) are deliberately untouched — warm
    hue = harmful is correct there. Returns ``(palette, {role: old_hex})``."""
    import colorsys

    damaging = {"damage", "damage_per_second"}
    safe_volume_roles: list[str] = []
    hazard_roles: list[str] = []
    for spec in role_specs(tiles):
        role_tiles = [t for t in tiles.tiles if t.color_role == spec["role"]]
        if "hazard" in spec["categories"]:
            hazard_roles.append(spec["role"])
        if "volume" in spec["categories"] and not any(
            t.params.get(k) for t in role_tiles for k in damaging
            if t.category == "volume"
        ):
            safe_volume_roles.append(spec["role"])
    if not safe_volume_roles or not hazard_roles:
        return palette, {}

    out = dict(palette)
    adjusted: dict[str, str] = {}
    hazard_hues = [
        h for r in hazard_roles
        if r in out and (h := color_math.hue_of(out[r])) is not None
    ]
    for role in safe_volume_roles:
        if role not in out:
            continue
        hue = color_math.hue_of(out[role])
        if hue is None or all(
            color_math.hue_distance(hue, h) >= MIN_VOLUME_HAZARD_HUE_SEP
            for h in hazard_hues
        ):
            continue
        r, g, b = (c / 255.0 for c in color_math.hex_to_rgb(out[role]))
        _h, s, v = colorsys.rgb_to_hsv(r, g, b)
        target_hue, original_lum = SAFE_VOLUME_HUE, _luminance(out[role])
        for _step in range(1 + 6):  # bounded walk if 215° is also taken
            candidate = color_math.rgb_to_hex(
                tuple(
                    round(c * 255)
                    for c in colorsys.hsv_to_rgb(target_hue / 360.0, s, v)
                )
            )
            cand_hue = color_math.hue_of(candidate)
            if cand_hue is None or all(
                color_math.hue_distance(cand_hue, h)
                >= MIN_VOLUME_HAZARD_HUE_SEP
                for h in hazard_hues
            ):
                break
            target_hue = (target_hue + 20.0) % 360.0
        adjusted[role] = out[role]
        out[role] = _shift_luminance(candidate, original_lum)
    return out, adjusted


def fallback_palette(tiles: TileRegistry) -> dict[str, str]:
    """The hardcoded placeholder palette, as hex, for this registry's
    roles — the loud fallback when the LLM never validates."""
    from canon.packs.platformer.tileset import PLACEHOLDER_PALETTE

    out: dict[str, str] = {}
    for spec in role_specs(tiles):
        rgba = PLACEHOLDER_PALETTE.get(spec["role"])
        if rgba is not None:
            out[spec["role"]] = "#{:02x}{:02x}{:02x}".format(*rgba[:3])
    return out


class StyleGuidePhase:
    """Theme → palette, PER STAGE (each biome owns its look). Palettes
    land in ``ctx.artifacts["palettes"][stage_id]`` (consumed by the
    tileset phase, recorded on each Tileset artifact) and in
    ``style/<stage>/style.json`` (the diffusion seed)."""

    name = "plat:style"

    def __init__(self, tiles: TileRegistry = DEFAULT_TILES) -> None:
        self.tiles = tiles

    def run(self, ctx: Any) -> None:
        ctx.artifacts.setdefault("palettes", {})
        for stage_id in ctx.artifacts.get(
            "stage_ids", list(ctx.bible.stages)
        ):
            self._run_stage(ctx, stage_id)
        _stamp_metadata(ctx, self.name)

    def _run_stage(self, ctx: Any, stage_id: str) -> None:
        stage = ctx.bible.stages[stage_id]
        specs = role_specs(self.tiles)
        fallback = {"palette": fallback_palette(self.tiles)}

        # Coverage/hex/hazard-warmth are the model's to get right
        # (design); contrast is arithmetic and repaired in code below —
        # a real model looped three retries nudging dark water against a
        # dark dusk sky before this split (code-for-computation).
        data = llm_json(
            ctx,
            f"{self.name}:{stage_id}",
            lambda fb: ctx.prompts.style_generation(
                ctx.bible.world.title if ctx.bible.world else "",
                stage.theme,
                ctx.artifacts.get("stage_briefs", {}).get(
                    stage_id, ctx.artifacts.get("stage_brief", "")
                ),
                specs,
                background_role(self.tiles),
                feedback=fb,
                stage_id=stage_id,
            ),
            required_keys=("palette",),
            fallback=fallback,
            validate_obj=lambda obj: check_palette(
                obj.get("palette"), self.tiles, contrast=False
            ),
        )
        palette = {
            spec["role"]: str(data["palette"][spec["role"]]).lower()
            for spec in specs
            if spec["role"] in data["palette"]
        }
        if palette == {k: v.lower() for k, v in fallback["palette"].items()}:
            warn(
                ctx,
                f"style: LLM palette for stage {stage_id!r} never "
                "validated; tiles use the PLACEHOLDER palette, not "
                "generated style.",
            )
        palette, safe_vols = separate_safe_volumes_from_hazards(
            palette, self.tiles
        )
        if safe_vols:
            logger.info(
                "StyleGuidePhase volume-hue tool rotated %d role(s) for "
                "%s: %s (luminance preserved, hue snapped to the "
                "safe-liquid band).",
                len(safe_vols), stage_id,
                ", ".join(
                    f"{role} {old}->{palette[role]}"
                    for role, old in safe_vols.items()
                ),
            )
        palette, separated = separate_structural_roles(palette, self.tiles)
        if separated:
            logger.info(
                "StyleGuidePhase separation tool spread %d structural "
                "role(s) for %s: %s (hue kept, spacing computed).",
                len(separated), stage_id,
                ", ".join(
                    f"{role} {old}->{palette[role]}"
                    for role, old in separated.items()
                ),
            )
        palette, adjusted = enforce_contrast(palette, self.tiles)
        if adjusted:
            logger.info(
                "StyleGuidePhase readability tool adjusted %d role(s) "
                "for %s: %s (hue kept, lightness computed).",
                len(adjusted), stage_id,
                ", ".join(
                    f"{role} {old}->{palette[role]}"
                    for role, old in adjusted.items()
                ),
            )

        # The diffusion-seed artifact: what to paint, and with what.
        ctx.adapter.write_json_singleton(
            f"style/{stage_id}/style.json",
            {"stage_id": stage_id, "palette": palette, "roles": specs},
        )
        ctx.artifacts["palettes"][stage_id] = palette
        logger.info(
            "StyleGuidePhase palette for %s (%r): %s",
            stage_id, stage.theme,
            ", ".join(f"{role}={hex_}" for role, hex_ in palette.items()),
        )

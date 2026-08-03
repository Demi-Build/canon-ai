"""Diffusion tilesheet producer — the art track's first real asset phase.

``DiffusionSheetProducer`` plugs into ``PlaceholderTilesetPhase``'s sheet
seam: one generated texture per registry tile, seeded by the style-guide
palette (``style/<stage>/style.json``). The phase still owns slots,
hashes, provenance, and the manifest — this module only produces pixels.

Split of responsibilities (code-for-computation):

- The DIFFUSION MODEL owns texture design — what cracked basalt or
  rippling water looks like.
- CODE owns palette fidelity: :func:`conform_to_palette` re-colors every
  generated tile so its hue/saturation are exactly the role's style hex
  and its MEAN brightness matches, keeping only the texture's per-pixel
  brightness variation. The style agent's readability bar
  (``enforce_contrast``) was computed on role hexes; conforming means it
  keeps holding after art lands, and the average-color samples the flat
  consumers take resolve to the palette, not to model whim.

Determinism: real diffusion is not reproducible; the deterministic paths
are the placeholder sheet (no producer) and ``FakeImageBackend`` (fixed
bytes per call). Provenance still folds in the image model + config seed
via the phase's stamp, so a backend/model bump invalidates correctly.
"""

from __future__ import annotations

import colorsys
import io
import logging
from typing import Any

logger = logging.getLogger(__name__)


#: Per-CATEGORY art direction — what a tile of this physics kind must
#: READ AS when repeated in a row. First real run: the platform tile came
#: back as a centered clump on flat color, which tiled into dotted mush
#: instead of planks; category semantics are code's to state (values in
#: data, categories in code — including their look).
CATEGORY_ART = {
    "empty": (
        "open air / distant atmosphere, soft and very low contrast — it "
        "is the backdrop everything else must stay readable against"
    ),
    "solid": (
        "solid walkable terrain fill with a clearly lit top edge and "
        "denser material below"
    ),
    "one_way": (
        "a thin horizontal platform seen side-on — plank or slab with a "
        "bright walkable top surface and open air beneath, spanning the "
        "full tile width so copies connect into one continuous platform"
    ),
    "hazard": "a dangerous sharp hazard, high contrast, unmistakably harmful",
    "volume": (
        "a liquid surface with gentle ripples at the top and depth below, "
        "spanning the full tile width so copies read as one body"
    ),
}

#: Categories the generator draws as an OBJECT on an invented backdrop (not
#: a seamless fill). Cut that backdrop to transparency so the tile sits ON
#: the terrain instead of an opaque square (playtest: 'spikes have a yellow
#: box'). Fill categories must NEVER be cut — flood-keying a seamless
#: texture would eat the whole tile and punch holes in the level.
_CUTOUT_CATEGORIES = {"hazard"}

#: Below this visible fraction the cut ate the subject too (a hazard the
#: same color as its backdrop) — keep the original opaque tile instead.
MIN_TILE_OPAQUE_FRACTION = 0.04


def png_bytes(img: Any, provenance: dict[str, str] | None = None) -> bytes:
    """Encode ``img`` as PNG bytes, stamping ``provenance`` as tEXt.

    The READABLE provenance layer (graphics arc G6): every producer-
    generated PNG carries its generation inputs as ``canon:*`` tEXt
    chunks — keys added in SORTED order, ``canon:generator`` always
    present — so any PNG reader answers "where did this art come from"
    without the Bible. Deterministic by contract: NO timestamp (byte-
    determinism forbids wall-clock values) and NO literal prompt
    (prompts are deterministic functions of the stamped inputs — asset,
    models, seeds, gfx digest, prompt version). Durable/C2PA layer
    deferred — readable tEXt + Bible content hashes are the v1
    provenance; c2pa-python is the named upgrade path. Empty/None
    ``provenance`` encodes bare (placeholder + derived writes keep
    their old bytes)."""
    buffer = io.BytesIO()
    if provenance:
        from PIL.PngImagePlugin import PngInfo

        meta = PngInfo()
        stamp = {**provenance, "canon:generator": "canon-ai harness"}
        for key in sorted(stamp):
            full = key if key.startswith("canon:") else f"canon:{key}"
            meta.add_text(full, stamp[key])
        img.save(buffer, format="PNG", pnginfo=meta)
    else:
        img.save(buffer, format="PNG")
    return buffer.getvalue()


def art_provenance(
    *,
    asset: str,
    producer: Any = None,
    graphics: Any = None,
    pipeline_seed: str = "",
) -> dict[str, str]:
    """The standard ``canon:*`` stamp keys for one generated asset —
    exactly the provenance-hash inputs (§6.3), readable in the file:
    asset address, image backend/model, the backend's image seed (only
    seed-capable backends carry one), the pipeline seed, the graphics
    digest, and the prompt version. ``canon:provenance`` names the
    layer version ("readable-v1" — see :func:`png_bytes` for the
    deferred durable layer)."""
    from examples.platformer_pack.phases import PROMPT_VERSION

    backend = getattr(producer, "backend", None)
    # Prefer the seed the backend reported for its last response; fall back
    # to a pinned .seed (Retro/PixelLab). request_id is fal's audit handle
    # (postmortem ticket 5c — both were discarded, so canon:image-seed always
    # shipped empty). Fakes carry neither → both stay "" (byte-identical).
    image_seed = getattr(backend, "last_seed", None)
    if image_seed is None:
        image_seed = getattr(backend, "seed", None)
    request_id = getattr(backend, "last_request_id", None)
    return {
        "canon:asset": asset,
        "canon:backend-model": (
            str(getattr(producer, "model", "")) if producer is not None else ""
        ),
        "canon:image-request-id": "" if request_id is None else str(request_id),
        "canon:image-seed": "" if image_seed is None else str(image_seed),
        "canon:seed": pipeline_seed,
        "canon:gfx": graphics.digest() if graphics is not None else "",
        "canon:prompt-version": PROMPT_VERSION,
        "canon:provenance": "readable-v1",
    }


#: The visible watermark's two tones — a 2px-cell checker no organic
#: generation lands on exactly, so "is this marked?" is a pixel check.
_WATERMARK_TONES = ((32, 32, 32), (224, 224, 224))


def apply_watermark(img: Any) -> Any:
    """Stamp the visible generated-content mark IN PLACE: a
    deterministic 4x4 px two-tone checker (2px cells), 2px in from the
    bottom-right corner, alpha-respecting on RGBA (the mark is opaque
    so it reads over transparency). Images too small to carry it come
    back untouched. Applied only when ``graphics.visible_watermark``
    and never to tiles (their region means are palette-checked)."""
    w, h = img.size
    if w < 6 or h < 6:
        return img
    px = img.load()
    x0, y0 = w - 6, h - 6
    for dy in range(4):
        for dx in range(4):
            tone = _WATERMARK_TONES[(dx // 2 + dy // 2) % 2]
            px[x0 + dx, y0 + dy] = (
                (*tone, 255) if img.mode == "RGBA" else tone
            )
    return img


def tile_prompt(
    tile: Any, role_hex: str, theme: str, world_title: str, graphics: Any
) -> str:
    """Deterministic per-tile prompt, built from registry data + the
    style seed + the game's GraphicsSpec. Names the tile, its physics
    category (with the category's art direction), the stage theme, the
    target art style, and the palette hex the art must center on."""
    direction = CATEGORY_ART.get(tile.category, "")
    return (
        f"Seamless tileable texture for a 2D platformer tile: "
        f"'{tile.name}' ({tile.category}) in the stage '{theme}' of the "
        f"game '{world_title}'. It must read as {direction}. "
        f"Art style: {graphics.effective_art_style()}. "
        f"Dominant color {role_hex}. Flat orthographic texture fill "
        f"designed to repeat in a grid, legible at "
        f"{graphics.tile_px}x{graphics.tile_px}. No text, no borders, "
        f"no objects, no characters."
    )


def conform_to_palette(img: Any, role_hex: str, levels: int | None = None) -> Any:
    """Repair TOOL for palette fidelity: SHIFT the tile's mean hue,
    saturation, and brightness onto the role hex — per-pixel variation
    in all three survives, so plank tans stay tan against brown instead
    of being flattened to mono-hue noise (second real run's "everything
    is the same mush" finding). The brightness mean lands exactly, which
    is what the readability bar is computed on; hue/saturation means
    land approximately (clamping). Colorless sources (fakes, grayscale)
    degrade to the old force-everything behavior. Returns RGBA.

    ``levels`` posterizes brightness to that many steps first (crisp
    graphics specs — quantized shading reads as real pixel art), then a
    constant shift recenters the mean exactly on the target, so the
    average-color guarantee holds with or without posterization."""
    import math

    from PIL import Image

    hexv = role_hex.lstrip("#")
    tr, tg, tb = (int(hexv[i : i + 2], 16) for i in (0, 2, 4))
    th, ts, tv = colorsys.rgb_to_hsv(tr / 255, tg / 255, tb / 255)

    rgb = img.convert("RGB")
    pixels = list(rgb.get_flattened_data())
    alphas = (
        list(img.getchannel("A").get_flattened_data())
        if img.mode == "RGBA"
        else [255] * len(pixels)
    )
    hsv = [colorsys.rgb_to_hsv(r / 255, g / 255, b / 255) for r, g, b in pixels]
    values = [v for _h, _s, v in hsv]
    # Aim the recolour using only VISIBLE pixels: a cut-out hazard keeps
    # its discarded backdrop's RGB under alpha 0, which must not drag the
    # means. Fully-opaque tiles (every seamless fill) use every pixel, so
    # their output stays byte-identical to before this alpha support.
    vis = [i for i, a in enumerate(alphas) if a > 0] or list(range(len(pixels)))
    mean_v = sum(values[i] for i in vis) / len(vis)
    if mean_v <= 0:  # all-black generation: nothing to scale, fill flat
        flat = Image.new("RGBA", img.size, (tr, tg, tb, 255))
        if img.mode == "RGBA":
            flat.putalpha(img.getchannel("A"))
        return flat

    # Mean hue is CIRCULAR, weighted by chroma (gray pixels carry no hue
    # information); a colorless source leaves hue/sat forced to target.
    sin_sum = sum(math.sin(hsv[i][0] * math.tau) * hsv[i][1] * hsv[i][2] for i in vis)
    cos_sum = sum(math.cos(hsv[i][0] * math.tau) * hsv[i][1] * hsv[i][2] for i in vis)
    weight = math.hypot(sin_sum, cos_sum)
    if weight < 1.0:
        hues = [th] * len(hsv)
        sats = [ts] * len(hsv)
    else:
        mean_h = (math.atan2(sin_sum, cos_sum) / math.tau) % 1.0
        dh = th - mean_h
        mean_s = sum(hsv[i][1] for i in vis) / len(vis)
        ds = ts - mean_s
        hues = [(h + dh) % 1.0 for h, _s, _v in hsv]
        sats = [min(1.0, max(0.0, s + ds)) for _h, s, _v in hsv]

    # Fixed-point search for the brightness scale: clamping at 1.0 (and
    # posterization steps) pull the mean off bright targets on a single
    # pass, so re-aim until the realized mean sits on the target; keep
    # the best pass — quantization can make the last step oscillate.
    scale = tv / mean_v
    best, best_err = values, float("inf")
    for _ in range(16):
        scaled = [min(1.0, v * scale) for v in values]
        if levels:
            scaled = [round(v * (levels - 1)) / (levels - 1) for v in scaled]
        mean = sum(scaled[i] for i in vis) / len(vis)
        err = abs(mean - tv)
        if err < best_err:
            best, best_err = scaled, err
        if err < 0.5 / 255 or mean <= 0:
            break
        scale *= tv / mean

    # Recenter exactly: a constant brightness shift keeps the posterized
    # level structure (shifted, not re-spread) and lands the mean on tv.
    delta = tv - sum(best[i] for i in vis) / len(vis)
    final = [min(1.0, max(0.0, v + delta)) for v in best]

    data = [
        [round(c * 255) for c in colorsys.hsv_to_rgb(h, s, v)]
        for h, s, v in zip(hues, sats, final)
    ]
    # FINAL RGB-MEAN RECENTER (the recurring paid-run spike-tile failure):
    # the HSV shifts land the hue/sat/brightness MEANS, but the QA
    # code-check scores the ARITHMETIC RGB mean (vlm_qa._mean_rgb) — a
    # nonlinear mapping, so conformed tiles still failed the euclidean bar
    # by 85-155 vs tolerance 48 in three consecutive paid runs. Shift every
    # VISIBLE pixel by the constant per-channel delta that puts the region
    # mean exactly on the role hex; per-pixel variation survives untouched.
    # A few passes, because clamping at 0/255 can pull the mean off again.
    target = (tr, tg, tb)
    for _ in range(8):
        means = [sum(data[i][ch] for i in vis) / len(vis) for ch in range(3)]
        deltas = [target[ch] - means[ch] for ch in range(3)]
        if all(abs(d) < 0.5 for d in deltas):
            break
        for i in vis:
            for ch in range(3):
                data[i][ch] = min(255, max(0, round(data[i][ch] + deltas[ch])))

    out = Image.new("RGBA", img.size)
    out.putdata([tuple(px) + (a,) for px, a in zip(data, alphas)])
    return out


# ---------------------------------------------------------------------------
# Sprite tools (enemies + player). Sprites keep their internal detail —
# no hue-forcing like tiles; the prompt names the assigned color and a
# CODE CHECK verifies the dominant hue, warn-not-force.
# ---------------------------------------------------------------------------


def sprite_prompt(
    name: str, descriptor: str, color_hex: str, theme: str,
    world_title: str, graphics: Any,
) -> str:
    return (
        f"Single game sprite: {name}, {descriptor}, from the stage "
        f"'{theme}' of the 2D platformer '{world_title}'. Art style: "
        f"{graphics.effective_art_style()}. Dominant color {color_hex}. Full body, "
        f"side view facing right, centered, isolated on a plain solid "
        f"white background. No shadow, no ground, no text, no border."
    )


def _opaque_fraction(img: Any) -> float:
    """Fraction of pixels still visible after a background cut."""
    if img.mode != "RGBA":
        return 1.0
    alpha = list(img.getchannel("A").get_flattened_data())
    return sum(1 for a in alpha if a > 0) / len(alpha)


def _bottom_align(img: Any) -> Any:
    """Slide the cut-out subject down so its feet sit on the frame bottom.

    Generated sprites frame the creature with empty space BELOW it (the fal
    art centers the body), but the consumers bottom-anchor the sprite frame
    to the floor — so that gap made enemies HOVER above the ground
    (playtest, plat_kingdom3). Re-seat the content x-centered at the frame
    bottom; the empty margin moves to the TOP, above the head, out of play.
    Runs at full generation resolution before downscaling, like the cut."""
    from PIL import Image

    if img.mode != "RGBA":
        return img
    bbox = img.getchannel("A").getbbox()
    if bbox is None:  # fully transparent (empty fake) — nothing to seat
        return img
    content = img.crop(bbox)
    framed = Image.new("RGBA", img.size, (0, 0, 0, 0))
    framed.paste(
        content, ((img.width - content.width) // 2, img.height - content.height)
    )
    return framed


def remove_background(img: Any) -> Any:
    """Code tool: cut the prompt's solid backdrop to transparency by
    flood-filling from the image edges (never punches holes inside the
    subject). The backdrop color is sampled from the corners. Returns
    RGBA. Run BEFORE downscaling — resizing first bleeds backdrop into
    the sprite's silhouette (halos)."""
    rgb = img.convert("RGB")
    w, h = rgb.size
    pixels = list(rgb.get_flattened_data())
    corners = [pixels[0], pixels[w - 1], pixels[(h - 1) * w], pixels[h * w - 1]]
    bg = tuple(sum(c[i] for c in corners) // 4 for i in range(3))

    def near_bg(p: tuple) -> bool:
        return sum((a - b) ** 2 for a, b in zip(p, bg)) < 60**2

    from collections import deque

    cut = bytearray(w * h)
    queue = deque(
        i
        for i in (
            *range(w),  # top row
            *range((h - 1) * w, h * w),  # bottom row
            *(y * w for y in range(h)),  # left col
            *(y * w + w - 1 for y in range(h)),  # right col
        )
        if near_bg(pixels[i])
    )
    for i in queue:
        cut[i] = 1
    while queue:
        i = queue.popleft()
        x, y = i % w, i // w
        for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
            if 0 <= nx < w and 0 <= ny < h:
                j = ny * w + nx
                if not cut[j] and near_bg(pixels[j]):
                    cut[j] = 1
                    queue.append(j)

    from PIL import Image

    out = Image.new("RGBA", (w, h))
    out.putdata(
        [
            (0, 0, 0, 0) if cut[i] else (*pixels[i], 255)
            for i in range(w * h)
        ]
    )
    return out


# --- Fake-transparency gate (first-paid-run ticket 1) ---------------------
# The general model PAINTS transparency instead of emitting alpha: two of
# three paid foreground bands shipped as literal checkerboard pixels (89%
# and 71% opaque; the grays at luminance ~240/224 were the two dominant
# "colors"), and hazard tiles arrived on a baked white slab that
# conform_to_palette then re-hued into an opaque box behind the spikes.
# remove_background missed all of it because it aims its flood at the
# CORNER-average color — and the fg-band prompt asks for occluders that
# FRAME the edges, so the corners were subject, not backdrop.
#
# The gate audits every alpha-requiring class after its background cut:
# opaque fraction against per-class bounds, then the painted-backdrop
# signature. The remedy ORDER is deliberate: auto-key first ($0, and the
# paid failures are cleanly keyable — the checker grays are near-neutral
# and far from every subject palette), paid regeneration only when keying
# cannot land in bounds. Deterministic code throughout; images inside
# their bounds are NEVER modified (the player sprite is #f0f0f0 — global
# white-keying would eat it).

#: Per-class opaque-fraction bounds: (lo, hi, soft, soft_checker_only).
#: Below ``lo`` the cut ate the subject (retry — keying cannot help);
#: above ``hi`` the backdrop survived (key, then retry). ``soft`` arms
#: keying INSIDE bounds when the light-mode signature is present — a
#: 71%-opaque checkerboard band must not slip under a lenient cap — and
#: ``soft_checker_only`` restricts that to the unambiguous two-tone
#: pattern (a flat pale field on an in-bounds band could be legitimate
#: foam/ice art; a checkerboard never is). Soft-region keying must LAND
#: in bounds or the original is kept untouched.
ALPHA_GATE_BOUNDS: dict[str, tuple[float, float, float | None, bool]] = {
    "sprite": (0.02, 0.90, None, False),
    "tile_cutout": (MIN_TILE_OPAQUE_FRACTION, 0.95, 0.60, False),
    "band_fg": (0.01, 0.60, 0.35, True),
}

#: Regenerations the gate may burn per asset — mirrors the pipeline's
#: ``max_retries`` default (the budget every other generation loop uses).
ALPHA_GATE_RETRIES = 3

#: Deterministic retry addendum (a function of attempt>0, like the
#: sanitized content-policy retry): name the failure mode to the model.
#: It must not forbid white outright — sprites are DELIBERATELY prompted
#: onto solid white for the corner-flood cut.
ALPHA_RETRY_SUFFIX = (
    " IMPORTANT: the background behind the subject must be ONE flat "
    "uniform solid color edge to edge — never a checkerboard or grid "
    "pattern, never a painted 'transparency' texture."
)

#: A "colorless" pixel: channel spread this small reads as gray. The
#: checker grays are exactly neutral; subject art in every style palette
#: is tinted, so AA blends at the subject edge fall out of the family.
_NEUTRAL_SPREAD = 24
#: Light-family floor (min channel): the painted checker/white grays sit
#: at ~224-240; tinted pale subjects (icy blue-white) miss the spread bar.
_LIGHT_FLOOR = 160
#: Dark-family ceiling (max channel) for a painted flat-black field.
_DARK_CEIL = 64
_LIGHT_SIG_FRACTION = 0.20  # of visible pixels — arms the light signature
_DARK_SIG_FRACTION = 0.30  # higher: dark neutrals are common in outlines
_CHECKER_TONE_FRACTION = 0.08  # each checker tone's minimum share
_CHECKER_TONE_GAP = 16  # luminance separation of the two checker tones


def detect_painted_backdrop(img: Any) -> dict | None:
    """The painted-transparency signature among VISIBLE pixels: the
    near-neutral very-light family (checkerboard grays / flat white) or
    a flat near-black field. Returns ``{"mode": "light"|"dark",
    "pattern": "checkerboard"|"flat", "fraction": share_of_visible}`` or
    ``None``. Checkerboard = two light tones ≥ ``_CHECKER_TONE_GAP``
    luminance apart, each carrying ≥ ``_CHECKER_TONE_FRACTION`` of the
    visible pixels (the paid scorched band: 44% + 27% at 240/224); one
    smeared tone alone reads as a flat painted field."""
    rgba = img.convert("RGBA")
    visible = light = dark = 0
    buckets: dict[int, int] = {}
    for r, g, b, a in rgba.get_flattened_data():
        if a == 0:
            continue
        visible += 1
        low, high = min(r, g, b), max(r, g, b)
        if high - low > _NEUTRAL_SPREAD:
            continue
        if low >= _LIGHT_FLOOR:
            light += 1
            key = ((r + g + b) // 3) // 8
            buckets[key] = buckets.get(key, 0) + 1
        elif high <= _DARK_CEIL:
            dark += 1
    if not visible:
        return None
    if light / visible >= _LIGHT_SIG_FRACTION:
        tones = sorted(
            k for k, n in buckets.items()
            if n / visible >= _CHECKER_TONE_FRACTION
        )
        checker = (
            len(tones) >= 2 and (tones[-1] - tones[0]) * 8 >= _CHECKER_TONE_GAP
        )
        return {
            "mode": "light",
            "pattern": "checkerboard" if checker else "flat",
            "fraction": light / visible,
        }
    if dark / visible >= _DARK_SIG_FRACTION:
        return {"mode": "dark", "pattern": "flat", "fraction": dark / visible}
    return None


def key_painted_backdrop(img: Any, mode: str) -> Any:
    """Cut a DETECTED painted backdrop to real alpha: flood from the
    frame border and from existing transparency (the earlier corner cut
    opens channels) through pixels of the detected family, zeroing their
    alpha. Border-connected only — a light region INSIDE the subject
    (the player's #f0f0f0 body, a white-hot flame core) is unreachable
    and survives. Unlike :func:`remove_background` this never aims at
    the corner color, so edge-framing subjects can't mis-aim it.
    Returns RGBA."""
    from collections import deque

    from PIL import Image

    rgba = img.convert("RGBA")
    w, h = rgba.size
    pixels = list(rgba.get_flattened_data())

    def matches(i: int) -> bool:
        r, g, b, a = pixels[i]
        if a == 0:
            return False
        low, high = min(r, g, b), max(r, g, b)
        if high - low > _NEUTRAL_SPREAD:
            return False
        return low >= _LIGHT_FLOOR if mode == "light" else high <= _DARK_CEIL

    cut = bytearray(w * h)
    queue: deque[int] = deque()
    for i in range(w * h):
        if not matches(i):
            continue
        x, y = i % w, i // w
        seed = x == 0 or y == 0 or x == w - 1 or y == h - 1
        if not seed:
            # Interior pixel: all four neighbours exist (border pixels
            # seeded above), so no row-wrap hazard on i-1 / i+1.
            for j in (i - 1, i + 1, i - w, i + w):
                if pixels[j][3] == 0:
                    seed = True
                    break
        if seed:
            cut[i] = 1
            queue.append(i)
    while queue:
        i = queue.popleft()
        x, y = i % w, i // w
        for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
            if 0 <= nx < w and 0 <= ny < h:
                j = ny * w + nx
                if not cut[j] and matches(j):
                    cut[j] = 1
                    queue.append(j)
    out = Image.new("RGBA", (w, h))
    out.putdata(
        [
            (px[0], px[1], px[2], 0) if cut[i] else px
            for i, px in enumerate(pixels)
        ]
    )
    return out


def alpha_gate(img: Any, kind: str) -> tuple[Any, bool, str]:
    """Judge one cut asset: ``(image_to_use, ok, note)``. ``ok=False``
    asks the caller to burn a regeneration retry. In-bounds images pass
    UNTOUCHED unless the soft trigger's signature is present — and even
    then the keyed result must itself land in bounds to replace the
    original (a pale-but-legitimate subject survives a false fire)."""
    lo, hi, soft, soft_checker_only = ALPHA_GATE_BOUNDS[kind]
    frac = _opaque_fraction(img)
    if frac < lo:
        return img, False, (
            f"opaque {frac:.1%} under the {kind} floor {lo:.0%} — "
            f"the background cut ate the subject"
        )
    hard = frac > hi
    if not hard and not (soft is not None and frac > soft):
        return img, True, ""
    sig = detect_painted_backdrop(img)
    if not hard and (
        sig is None
        or sig["mode"] != "light"
        or (soft_checker_only and sig["pattern"] != "checkerboard")
    ):
        return img, True, ""
    if sig is None:
        return img, False, (
            f"opaque {frac:.1%} over the {kind} cap {hi:.0%} with no "
            f"keyable painted-backdrop signature"
        )
    keyed = key_painted_backdrop(img, sig["mode"])
    kfrac = _opaque_fraction(keyed)
    note = (
        f"auto-keyed a painted {sig['pattern']} backdrop "
        f"({sig['fraction']:.0%} of visible pixels): opaque "
        f"{frac:.1%} -> {kfrac:.1%}"
    )
    if lo <= kfrac <= hi:
        return keyed, True, note
    if not hard:
        return img, True, ""  # in bounds, keying didn't land — keep as-is

    def violation(f: float) -> float:
        return max(0.0, f - hi, lo - f)

    cand = keyed if violation(kfrac) < violation(frac) else img
    return cand, False, note + f" — still outside [{lo:.0%}, {hi:.0%}]"


# --- Sprite-sheet frame segmentation (B3 sprite animation) ---------------
# The img2img model does NOT honor an exact cell count (a spike asked for 4
# frames and got 5, evenly spaced but off any fixed grid), so frames are
# recovered by CONTENT, not by slicing a grid: cut the background, project
# opaque pixels onto the x-axis, split on vertical whitespace gaps, and crop
# each blob to its own box. Deterministic — robust to whatever N the model draws.


def build_sheet_reference(base: Any, n_frames: int, cell_px: int) -> Any:
    """The img2img seed (proven in the B0 spike): the base sprite composited
    onto white and NEAREST-upscaled to a ``cell_px`` cell, placed as frame 1
    (leftmost) of an ``n_frames``-wide white canvas — the rest blank for the
    model to fill. Returns RGB."""
    from PIL import Image

    white = Image.new("RGBA", base.size, (255, 255, 255, 255))
    flat = Image.alpha_composite(white, base.convert("RGBA")).convert("RGB")
    cell = flat.resize((cell_px, cell_px), Image.NEAREST)
    ref = Image.new("RGB", (cell_px * max(1, n_frames), cell_px), (255, 255, 255))
    ref.paste(cell, (0, 0))
    return ref


def segment_frames(
    sheet: Any,
    *,
    min_gap_frac: float = 0.01,
    min_blob_frac: float = 0.02,
    min_area_frac: float = 0.15,
) -> list:
    """Split a generated sprite SHEET into per-frame RGBA crops, left→right.
    Background is cut first (white → transparent); opaque columns are grouped
    into blobs, runs closer than ``min_gap_frac`` of the width are merged, and
    blobs narrower than ``min_blob_frac`` are dropped. Fraction thresholds keep
    it resolution-independent.

    Crops whose opaque AREA falls below ``min_area_frac`` of the median crop's
    are dropped as artifacts: a stray full-height streak (real case — the
    player's ``fall`` sheet came back as a head plus a 3px vertical stick)
    otherwise survives as a "frame" and inflates the state's frame square,
    shrinking every real frame beside it."""
    keyed = remove_background(sheet.convert("RGB"))  # RGBA, bg → transparent
    w, h = keyed.size
    alpha = list(keyed.getchannel("A").get_flattened_data())
    col = [0] * w
    for i, a in enumerate(alpha):
        if a > 0:
            col[i % w] += 1
    min_gap = max(1, int(w * min_gap_frac))
    min_blob = max(1, int(w * min_blob_frac))

    runs: list[list[int]] = []
    x = 0
    while x < w:
        if col[x] > 0:
            x0 = x
            while x < w and col[x] > 0:
                x += 1
            runs.append([x0, x])
        else:
            x += 1
    merged: list[list[int]] = []
    for run in runs:
        if merged and run[0] - merged[-1][1] < min_gap:
            merged[-1][1] = run[1]
        else:
            merged.append(run)

    crops = []
    for x0, x1 in merged:
        if (x1 - x0) < min_blob:
            continue
        strip = keyed.crop((x0, 0, x1, h))
        bbox = strip.getchannel("A").getbbox()
        crops.append(strip.crop(bbox) if bbox else strip)
    if len(crops) > 2:
        # Drop artifact slivers by opaque area against the MEDIAN crop (robust
        # to one outlier in either direction). Only with >2 crops, so a real
        # 2-frame cycle is never halved.
        areas = [_opaque_area(c) for c in crops]
        median = sorted(areas)[len(areas) // 2]
        if median > 0:
            crops = [
                c for c, a in zip(crops, areas) if a >= median * min_area_frac
            ]
    return crops


def _opaque_area(img: Any) -> int:
    """Count of visible pixels — the artifact test that a bounding box can't
    make (a 3px full-height streak has a tall bbox but almost no area)."""
    rgba = img.convert("RGBA")
    return sum(1 for a in rgba.getchannel("A").get_flattened_data() if a > 0)


def frame_side(crops: list, *, pad: int = 4) -> int:
    """The square canvas size that seats every crop in ``crops`` — max content
    dimension + pad. Split out from ``normalize_frames`` so a caller holding
    SEVERAL states' crops can compute ONE side across all of them and pass it
    back in; sizing per state instead stretches each state's largest dimension
    to fill the cell, so an actor whose idle is wide and whose jump is tall
    renders at two different scales (the 'half the frame vanishes in the jump'
    bug). Empty input → 0."""
    if not crops:
        return 0
    return max(max(c.width for c in crops), max(c.height for c in crops)) + pad


def normalize_frames(crops: list, *, pad: int = 4, side: int | None = None) -> list:
    """Seat every crop in a common SQUARE canvas, x-centered and bottom-aligned
    (feet on the frame floor, matching ``_bottom_align`` and the static
    base.png framing). One shared canvas size → the creature holds a consistent
    size across the cycle.

    ``side`` pins that canvas explicitly — pass the ``frame_side`` of an
    actor's WHOLE crop set so every state shares one scale. Omitted, the side
    is computed from ``crops`` alone (per-state sizing; correct only when the
    caller has a single state). Crops larger than ``side`` are scaled down to
    fit rather than cropped, so a pinned side never clips content.
    Empty input → []."""
    from PIL import Image

    if not crops:
        return []
    if side is None:
        side = frame_side(crops, pad=pad)
    out = []
    for c in crops:
        if c.width > side or c.height > side:
            fit = min(side / c.width, side / c.height)
            c = c.resize(
                (max(1, round(c.width * fit)), max(1, round(c.height * fit))),
                Image.LANCZOS,
            )
        frame = Image.new("RGBA", (side, side), (0, 0, 0, 0))
        frame.paste(c, ((side - c.width) // 2, side - c.height))
        out.append(frame)
    return out


def frames_to_strip(frames: list) -> Any:
    """Concatenate uniform square frames into one horizontal strip (RGBA);
    consumers slice by index at play time (frame width = strip width // N).
    Empty input → None."""
    from PIL import Image

    if not frames:
        return None
    fw, fh = frames[0].size
    strip = Image.new("RGBA", (fw * len(frames), fh), (0, 0, 0, 0))
    for i, frame in enumerate(frames):
        strip.paste(frame, (i * fw, 0))
    return strip


# --- Atlas packing (G4 animation shipping manifest) ----------------------
# Every animation frame is conceptually an UNTRIMMED frame_size square
# anchored at its bottom-center; the atlas stores only each frame's
# alpha-trimmed crop plus the (ox, oy) offset of that crop inside the
# square, so consumers reconstitute full squares and draw math is
# unchanged while the shipped PNG stays dense.


def _trim_frame(img: Any) -> tuple[Any, int, int]:
    """Alpha-bbox trim: ``(crop, ox, oy)`` with (ox, oy) the crop's
    top-left inside the original frame. A fully transparent frame comes
    back whole (ox=oy=0) so it still occupies a valid atlas rect."""
    rgba = img.convert("RGBA")
    bbox = rgba.getchannel("A").getbbox()
    if not bbox:
        return rgba, 0, 0
    return rgba.crop(bbox), bbox[0], bbox[1]


def pack_atlas(
    state_frames: dict[str, list], *, max_width: int = 1024, pad: int = 1
) -> tuple[Any, dict[str, list[dict]]]:
    """Shelf-pack alpha-trimmed animation frames into one RGBA atlas.

    DETERMINISTIC: states iterate in ``sorted`` order, frames in list
    order; placement is pure shelf packing (left→right with ``pad`` px
    spacing, wrap when the next crop would pass ``max_width``, row
    height = tallest crop in the row, atlas = (max row right edge, sum
    of row heights + pads)) — no randomness, no timestamps, so the same
    frames always yield byte-identical atlas pixels and an equal
    manifest.

    All frames share one UNTRIMMED square size (``frame_size``), whose
    anchor is its bottom-center ``(frame_size[0] / 2, frame_size[1])``.
    Each manifest rect ``{"x", "y", "w", "h", "ox", "oy"}`` records the
    trimmed crop's atlas position plus its top-left offset inside the
    untrimmed square; consumers rebuild the square via (ox, oy) — see
    :func:`reconstitute_frame` — so draw math is unchanged. Left-facing
    lists may ride under ``"<state>__left"`` keys and pack like ordinary
    states (the CALLER maps them to ``frames_left`` in atlas.json).
    Returns ``(atlas_image, {state: [rect, ...]})``."""
    from PIL import Image

    placed: list[tuple[Any, int, int]] = []
    rects: dict[str, list[dict]] = {}
    x = y = row_h = atlas_w = 0
    for state in sorted(state_frames):
        rects[state] = []
        for frame in state_frames[state]:
            crop, ox, oy = _trim_frame(frame)
            if x > 0 and x + crop.width > max_width:
                y += row_h + pad
                x = row_h = 0
            placed.append((crop, x, y))
            rects[state].append(
                {"x": x, "y": y, "w": crop.width, "h": crop.height, "ox": ox, "oy": oy}
            )
            atlas_w = max(atlas_w, x + crop.width)
            x += crop.width + pad
            row_h = max(row_h, crop.height)
    atlas = Image.new("RGBA", (max(1, atlas_w), max(1, y + row_h)), (0, 0, 0, 0))
    for crop, cx, cy in placed:
        atlas.paste(crop, (cx, cy))
    return atlas, rects


def reconstitute_frame(atlas: Any, rec: dict, frame_size: tuple[int, int]) -> Any:
    """Inverse of the pack: paste the trimmed crop from ``rec``'s atlas
    rect back at (ox, oy) on a transparent ``frame_size`` canvas,
    recovering the untrimmed frame pixel-for-pixel. Reference
    implementation shared by the pygame consumer and the packer tests."""
    from PIL import Image

    crop = atlas.crop((rec["x"], rec["y"], rec["x"] + rec["w"], rec["y"] + rec["h"]))
    frame = Image.new("RGBA", (int(frame_size[0]), int(frame_size[1])), (0, 0, 0, 0))
    frame.paste(crop, (int(rec["ox"]), int(rec["oy"])))
    return frame


def dominant_hue(img: Any) -> float | None:
    """Saturation-weighted dominant hue (degrees) over opaque pixels;
    None when the sprite is effectively colorless or empty."""
    rgba = img.convert("RGBA")
    total = weight_sum = 0.0
    for r, g, b, a in rgba.get_flattened_data():
        if a < 128:
            continue
        hue, sat, val = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
        weight = sat * val * (a / 255)
        total += hue * weight
        weight_sum += weight
    if weight_sum < 1.0:
        return None
    return (total / weight_sum) * 360.0


def hue_distance(a: float, b: float) -> float:
    d = abs(a - b) % 360.0
    return min(d, 360.0 - d)


def tint_to_color(img: Any, color_hex: str, saturation: float = 0.45) -> Any:
    """Repair TOOL for COLORLESS sprites: give every opaque pixel the
    assigned color's hue at a moderate saturation, keeping per-pixel
    brightness (shading survives). The pale skeletal hound slipped past
    the dominant-hue check precisely BECAUSE it was colorless — a gray
    sprite can't satisfy the hue reservations that keep enemies readable,
    so code assigns the hue it was rolled. Returns RGBA."""
    from PIL import Image

    raw = color_hex.lstrip("#")
    tr, tg, tb = (int(raw[i : i + 2], 16) / 255 for i in (0, 2, 4))
    th, _ts, _tv = colorsys.rgb_to_hsv(tr, tg, tb)
    rgba = img.convert("RGBA")
    out = Image.new("RGBA", rgba.size)
    out.putdata(
        [
            (
                pixel
                if pixel[3] == 0
                else tuple(
                    round(c * 255)
                    for c in colorsys.hsv_to_rgb(
                        th,
                        saturation,
                        colorsys.rgb_to_hsv(
                            pixel[0] / 255, pixel[1] / 255, pixel[2] / 255
                        )[2],
                    )
                )
                + (pixel[3],)
            )
            for pixel in rgba.get_flattened_data()
        ]
    )
    return out


# ---------------------------------------------------------------------------
# Backdrop tools (parallax scenery bands).
# ---------------------------------------------------------------------------

#: Band descriptors far → near; index into this by band number.
BAND_DESCRIPTORS = (
    "far-distance scenery silhouettes on the horizon",
    "mid-distance scenery and landscape features",
    "near-distance scenery framing the playfield",
)

#: The FOREGROUND occlusion band (depth > 1, drawn in front of gameplay):
#: sparse occluders on mostly empty/transparent space, never a scene.
BAND_FOREGROUND_DESCRIPTOR = (
    "sparse foreground occluders — vines, branches, rock edges framing "
    "the play area, mostly empty space"
)


def backdrop_prompt(
    band: int, theme: str, world_title: str, palette: dict[str, str],
    graphics: Any, foreground: bool = False,
) -> str:
    swatch = ", ".join(sorted(palette.values())[:4]) or "muted dusk tones"
    if foreground:
        return (
            f"Wide seamless parallax foreground layer for a 2D platformer: "
            f"{BAND_FOREGROUND_DESCRIPTOR}, stage '{theme}' of the game "
            f"'{world_title}'. Art style: {graphics.effective_art_style()}. "
            f"Colors harmonizing with {swatch}. Horizontally tileable, "
            f"isolated occluder elements on a plain background. Do not "
            f"paint a sky or a complete scene — this is ONE isolated "
            f"parallax layer on its own. No characters, no creatures, "
            f"no text."
        )
    # Band 0 owns the horizon; nearer bands must NOT bake a second sky
    # into the layer stack (each is one isolated scroll plane).
    sky = (
        "sky at the top fading out"
        if band == 0
        else "no sky. Do not paint a sky or a complete scene — this is "
        "ONE isolated parallax layer on its own"
    )
    return (
        f"Wide seamless parallax background layer for a 2D platformer: "
        f"{BAND_DESCRIPTORS[min(band, len(BAND_DESCRIPTORS) - 1)]}, stage "
        f"'{theme}' of the game '{world_title}'. Art style: "
        f"{graphics.effective_art_style()}. Colors harmonizing with {swatch}. "
        f"Horizontally tileable, {sky}. No "
        f"characters, no creatures, no text, no foreground objects."
    )


def atmosphere_blend(img: Any, bg_hex: str, strength: float = 0.35) -> Any:
    """Code tool for playfield readability: blend scenery toward the
    style palette's background color, pushing it visually 'back' so
    tiles/enemies (contrast-enforced against that color) stay legible
    over any backdrop the model painted. Returns RGB."""
    from PIL import Image

    hexv = bg_hex.lstrip("#")
    bg = tuple(int(hexv[i : i + 2], 16) for i in (0, 2, 4))
    overlay = Image.new("RGB", img.size, bg)
    return Image.blend(img.convert("RGB"), overlay, strength)


class DiffusionSheetProducer:
    """Per-tile texture source backed by a ``canon.backends`` ImageBackend.

    ``tile_image`` raises on any backend/decode failure — the tileset
    phase catches per tile, warns loudly, and falls back to the
    placeholder square, so one bad generation never sinks the run.
    """

    def __init__(self, backend: Any, edit_backend: Any = None) -> None:
        self.backend = backend
        # The img2img (animation) backend can be DIFFERENT from the sprite
        # backend (postmortem ticket 7): a native-pixel backend like PixelLab
        # generates the character sheets but has no edit endpoint, so nano
        # (fal) animates them. Defaults to the same backend.
        self.edit_backend = edit_backend if edit_backend is not None else backend
        # PRE-conform palette drift per generated tile name (ticket 6): how
        # far the RAW backend mean sat from the role hex BEFORE
        # conform_to_palette repaired it — the honest backend-aim meter the
        # post-conform palette check structurally can't see. Real backends
        # only (the fake's canned squares aren't a signal); the tileset phase
        # snapshots this per stage into review/<stage>/palette_drift.json.
        self.last_palette_drift: dict[str, float] = {}

    @property
    def model(self) -> str:
        """Image model identity, folded into provenance by the phase."""
        return str(getattr(self.backend, "model", type(self.backend).__name__))

    def _generate(
        self, prompt: str, sanitized: str, width: int, height: int
    ) -> bytes:
        """One generation with a single sanitized retry on a provider
        content-policy flag. Real-run finding: fal's checker false-
        positived on ONE tile prompt while five siblings with identical
        style text passed — the evocative world/stage flavor is the
        variable, so the retry drops it and keeps the functional spec.
        Any other error (and a second flag) propagates to the phase's
        loud per-asset fallback."""
        try:
            return self.backend.generate(prompt, width, height)
        except Exception as e:  # noqa: BLE001 — provider-specific types
            if "content_policy" not in str(e):
                raise
            logger.warning(
                "image prompt flagged by the provider's content checker; "
                "retrying once without flavor text: %r", sanitized,
            )
            return self.backend.generate(sanitized, width, height)

    def _alpha_gated(
        self,
        kind: str,
        label: str,
        prompt: str,
        sanitized: str,
        width: int,
        height: int,
        prep: Any,
    ) -> Any:
        """Generate -> ``prep`` (decode + background cut) -> alpha gate,
        for every alpha-requiring asset class. The remedy order is
        key-first ($0), regeneration last: a rejected attempt retries
        with :data:`ALPHA_RETRY_SUFFIX` up to :data:`ALPHA_GATE_RETRIES`
        times, and exhaustion ships the least-violating attempt behind a
        loud warning. Trusted-alpha backends (the fake twins' canned
        placeholder bytes) skip the gate wholesale — byte-identical by
        contract. Seed-pinned backends get no retries (same seed, same
        pixels): straight to keyed-or-warn."""
        if getattr(self.backend, "trusted_alpha", False):
            return prep(self._generate(prompt, sanitized, width, height))
        from canon.backends.base import backend_capabilities

        retries = ALPHA_GATE_RETRIES
        if "seeds" in backend_capabilities(self.backend) and (
            getattr(self.backend, "seed", None) is not None
        ):
            retries = 0
        lo, hi = ALPHA_GATE_BOUNDS[kind][0], ALPHA_GATE_BOUNDS[kind][1]
        best = best_note = None
        best_err = float("inf")
        for attempt in range(retries + 1):
            suffix = ALPHA_RETRY_SUFFIX if attempt else ""
            raw = self._generate(
                prompt + suffix, sanitized + suffix, width, height
            )
            img, ok, note = alpha_gate(prep(raw), kind)
            if ok:
                if note:
                    logger.warning("alpha gate: %s — %s.", label, note)
                return img
            frac = _opaque_fraction(img)
            err = max(0.0, frac - hi, lo - frac)
            if err < best_err:
                best, best_err, best_note = img, err, note
            logger.warning(
                "alpha gate: %s attempt %d/%d rejected — %s.%s",
                label, attempt + 1, retries + 1, note,
                " Retrying with the anti-checkerboard prompt."
                if attempt < retries
                else "",
            )
        logger.warning(
            "alpha gate: %s FAILED all %d attempt(s); shipping the "
            "least-bad image (%s) — eyeball this asset.",
            label, retries + 1, best_note,
        )
        return best

    def tile_image(
        self, tile: Any, role_hex: str, theme: str, world_title: str, graphics: Any
    ) -> Any:
        from PIL import Image

        prompt = tile_prompt(tile, role_hex, theme, world_title, graphics)
        sanitized = (
            f"Seamless tileable video-game texture tile: {tile.name} "
            f"({tile.category}). It must read as "
            f"{CATEGORY_ART.get(tile.category, '')}. Art style: "
            f"{graphics.effective_art_style()}. Dominant color {role_hex}. Flat "
            f"texture fill designed to repeat in a grid. No text, no "
            f"borders, no objects."
        )
        if tile.category in _CUTOUT_CATEGORIES:

            def prep(raw: bytes) -> Any:
                # Cut the invented backdrop at full resolution (halo-
                # safe), keep the cut only if the subject survived; the
                # gate then audits what the corner-sampled cut missed
                # (the baked-white hazard slab conform re-hues into an
                # opaque box). conform preserves the alpha; the QA
                # sampler measures only visible pixels.
                img = Image.open(io.BytesIO(raw)).convert("RGB")
                keyed = remove_background(img)
                if _opaque_fraction(keyed) >= MIN_TILE_OPAQUE_FRACTION:
                    return keyed
                return img

        gw, gh = self._request_dims(
            graphics.gen_px, graphics.gen_px,
            graphics.tile_px, graphics.tile_px,
        )
        if tile.category in _CUTOUT_CATEGORIES:
            img = self._alpha_gated(
                "tile_cutout", f"tile {tile.name!r}", prompt, sanitized,
                gw, gh, prep,
            )
        else:
            # Fill categories are NEVER gated or cut: keying a seamless
            # texture would eat the whole tile.
            raw = self._generate(prompt, sanitized, gw, gh)
            img = Image.open(io.BytesIO(raw)).convert("RGB")
        resample = (
            Image.NEAREST if graphics.render_filter == "crisp" else Image.LANCZOS
        )
        img = img.resize((graphics.tile_px, graphics.tile_px), resample)
        if not getattr(self.backend, "trusted_alpha", False):
            # Ticket 6: measure the backend's aim BEFORE conform repairs it.
            from PIL import ImageStat

            mean = ImageStat.Stat(img.convert("RGB")).mean
            hexv = role_hex.lstrip("#")
            target = [int(hexv[i : i + 2], 16) for i in (0, 2, 4)]
            self.last_palette_drift[tile.name] = round(
                sum((a - b) ** 2 for a, b in zip(mean, target)) ** 0.5, 1
            )
        img = conform_to_palette(img, role_hex, levels=graphics.posterize_levels)
        return self._maybe_grid_snap(img, graphics)

    def sprite_image(
        self,
        name: str,
        descriptor: str,
        color_hex: str,
        theme: str,
        world_title: str,
        graphics: Any,
        size: tuple[int, int],
        prompt_override: str | None = None,
    ) -> Any:
        """Transparent RGBA sprite at ``size``. Background is cut at full
        generation resolution (halo avoidance) before downscaling.

        ``prompt_override`` replaces the built prompt for this ONE call (the
        per-call "edit prompt" feature); the sanitized content-policy retry
        prompt is left alone so the fallback stays safe. None = default."""
        from PIL import Image

        prompt = (prompt_override or "").strip() or sprite_prompt(
            name, descriptor, color_hex, theme, world_title, graphics
        )
        sanitized = (
            f"Single video-game creature sprite: {name}. Art style: "
            f"{graphics.effective_art_style()}. Dominant color {color_hex}. Full body, "
            f"side view facing right, centered, isolated on a plain solid "
            f"white background. No shadow, no text."
        )

        def prep(raw: bytes) -> Any:
            return remove_background(Image.open(io.BytesIO(raw)))

        gw, gh = self._request_dims(graphics.gen_px, graphics.gen_px, *size)
        img = self._alpha_gated(
            "sprite", f"sprite {name!r}", prompt, sanitized, gw, gh, prep,
        )
        img = _bottom_align(img)  # feet on the frame bottom, not floating
        resample = (
            Image.NEAREST if graphics.render_filter == "crisp" else Image.LANCZOS
        )
        return self._maybe_grid_snap(img.resize(size, resample), graphics)

    def _request_dims(
        self,
        gen_w: int,
        gen_h: int,
        target_w: int | None = None,
        target_h: int | None = None,
    ) -> tuple[int, int]:
        """The generation-request size for one asset. General diffusion
        backends get the high-res ``gen`` canvas (generate big, conform
        down — their native scale). ``native_pixels`` backends draw at
        EXACTLY the requested resolution, so they get the TRUE art size
        (or the gen dims when no target exists, e.g. backdrops), clamped
        aspect-preserving into the backend's declared native envelope —
        a 512px diffusion canvas is both over PixelLab's 400px cap and
        the wrong question to ask a pixel-art model."""
        from canon.backends.base import backend_capabilities

        if "native_pixels" not in backend_capabilities(self.backend):
            return gen_w, gen_h
        w = target_w or gen_w
        h = target_h or gen_h
        hi = getattr(self.backend, "max_native_dim", None)
        lo = getattr(self.backend, "min_native_dim", None)
        scale = 1.0
        if hi is not None and max(w, h) * scale > hi:
            scale = hi / max(w, h)
        if lo is not None and min(w, h) * scale < lo:
            scale = lo / min(w, h)
        w, h = round(w * scale), round(h * scale)
        if hi is not None:
            w, h = min(w, hi), min(h, hi)
        if lo is not None:
            w, h = max(w, lo), max(h, lo)
        return w, h

    def _maybe_grid_snap(self, img: Any, graphics: Any) -> Any:
        """The MANDATORY pixel-discipline post-process for GENERAL models
        on crisp lanes (graphics arc): general diffusion output drifts
        off-grid — the muddy-NES root cause. Backends declaring
        ``native_pixels`` (true pixel-art generators) skip it; smooth
        lanes skip it (quantized edges would fight the look)."""
        from canon.backends.base import backend_capabilities

        if graphics.render_filter != "crisp":
            return img
        if "native_pixels" in backend_capabilities(self.backend):
            return img
        return grid_snap(img)

    def backdrop_image(
        self,
        band: int,
        theme: str,
        world_title: str,
        palette: dict[str, str],
        bg_hex: str,
        graphics: Any,
        foreground: bool = False,
    ) -> Any:
        """One parallax band (RGB, gen_px × gen_px/2), atmosphere-blended
        toward the palette background for playfield readability. The
        ``foreground`` occlusion band instead comes back RGBA with real
        transparency (background cut, no atmosphere blend — that
        converts to RGB) so consumers can draw it over the world."""
        from PIL import Image

        prompt = backdrop_prompt(
            band, theme, world_title, palette, graphics, foreground=foreground
        )
        descriptor = (
            BAND_FOREGROUND_DESCRIPTOR
            if foreground
            else BAND_DESCRIPTORS[min(band, len(BAND_DESCRIPTORS) - 1)]
        )
        sanitized = (
            f"Wide seamless parallax background layer for a 2D platformer: "
            f"{descriptor}. "
            f"Art style: {graphics.effective_art_style()}. Horizontally tileable. "
            f"No characters, no text."
        )
        # No fixed art-size target for bands (they scale to level height
        # at load) — native backends just clamp the gen canvas into
        # their envelope, aspect preserved.
        gw, gh = self._request_dims(graphics.gen_px, graphics.gen_px // 2)
        if foreground:

            def prep(raw: bytes) -> Any:
                return remove_background(Image.open(io.BytesIO(raw)))

            return self._alpha_gated(
                "band_fg", f"foreground band ({theme})", prompt, sanitized,
                gw, gh, prep,
            )
        raw = self._generate(prompt, sanitized, gw, gh)
        img = Image.open(io.BytesIO(raw)).convert("RGB")
        return atmosphere_blend(img, bg_hex)

    def splash_image(
        self,
        title: str,
        themes: list[str],
        palette: dict[str, str],
        graphics: Any,
    ) -> Any:
        """The world's boot-screen key art (RGB, 16:9 at gen_px wide).
        The engine draws the title as a Label over it — the art itself
        must carry no lettering, so both prompts forbid text."""
        from PIL import Image

        swatch = ", ".join(sorted(palette.values())[:4]) or "muted dusk tones"
        theme_words = "; ".join(t for t in themes if t) or "a varied adventure"
        prompt = (
            f"Full-screen key-art style splash illustration for the 2D "
            f"platformer '{title}': one sweeping scenic vista evoking its "
            f"worlds — {theme_words}. Art style: "
            f"{graphics.effective_art_style()}. Colors harmonizing with "
            f"{swatch}. Cinematic wide composition, no characters in "
            f"close-up. No text, no letters, no logos."
        )
        sanitized = (
            f"Full-screen key-art style splash illustration for a 2D "
            f"platformer video game: a sweeping scenic landscape vista. "
            f"Art style: {graphics.effective_art_style()}. No text, no "
            f"letters, no logos."
        )
        gw, gh = self._request_dims(
            graphics.gen_px, graphics.gen_px * 9 // 16
        )
        raw = self._generate(prompt, sanitized, gw, gh)
        return Image.open(io.BytesIO(raw)).convert("RGB")


def grid_snap(img: Any, palette: list[str] | None = None) -> Any:
    """Snap an image onto true pixel discipline (graphics arc §3):
    BINARIZE alpha (>=128 opaque, else fully transparent — no fringe
    half-pixels off the grid) and, when an explicit ``palette`` color
    list is given, hard-QUANTIZE every opaque pixel to its nearest
    member (membership only — no per-sprite color caps, deferred).
    Deterministic, backend-agnostic; the resize-to-target NEAREST step
    upstream already made one drawn cell one true pixel."""
    from PIL import Image

    rgba = img.convert("RGBA")
    r, g, b, a = rgba.split()
    a = a.point(lambda v: 255 if v >= 128 else 0)
    rgba = Image.merge("RGBA", (r, g, b, a))
    if palette:
        colors = [
            tuple(int(h.lstrip("#")[i : i + 2], 16) for i in (0, 2, 4))
            for h in palette
        ]
        px = rgba.load()
        w, h = rgba.size
        for y in range(h):
            for x in range(w):
                pr, pg, pb, pa = px[x, y]
                if pa == 0:
                    continue
                nearest = min(
                    colors,
                    key=lambda c: (c[0] - pr) ** 2
                    + (c[1] - pg) ** 2
                    + (c[2] - pb) ** 2,
                )
                px[x, y] = (*nearest, pa)
    return rgba


def _build_backend(
    kind: str | None,
    model: str | None = None,
    edit_model: str | None = None,
    seed: int | None = None,
):
    """Construct ONE raw image backend from a name (or ``None``). Paid
    backends are only ever built here, from an explicit flag — never implied
    by the LLM backend choice."""
    if not kind or kind == "none":
        return None
    if kind == "fake":
        from canon.backends.testing import FakeImageBackend

        return FakeImageBackend()
    if kind == "fal":
        import os

        # Fail fast, BEFORE any paid LLM work: fal defers its credential
        # check to the first generate call, which on the first real run
        # burned a full generation and then warned 13 times.
        if not (
            os.environ.get("FAL_KEY")
            or (os.environ.get("FAL_KEY_ID") and os.environ.get("FAL_KEY_SECRET"))
        ):
            raise ValueError(
                "fal image backend needs FAL_KEY (or FAL_KEY_ID + "
                "FAL_KEY_SECRET) in the environment. A .env file is NOT "
                "read automatically — export it first, e.g.: "
                "set -a; source .env; set +a"
            )
        from canon.backends.image_fal import FalImageBackend

        kwargs: dict = {}
        if model:
            kwargs["model"] = model
        if edit_model:
            # The img2img (animation-sheet) model is its OWN lever.
            kwargs["edit_model"] = edit_model
        return FalImageBackend(**kwargs)
    if kind == "retro":
        import os

        if not os.environ.get("RD_API_KEY"):
            raise ValueError(
                "retro image backend needs RD_API_KEY in the environment. "
                "A .env file is NOT read automatically — export it first: "
                "set -a; source .env; set +a"
            )
        from canon.backends.image_retro_diffusion import RetroDiffusionBackend

        rd_kwargs: dict = {}
        if model:
            rd_kwargs["prompt_style"] = model
        if seed is not None:
            rd_kwargs["seed"] = seed
        return RetroDiffusionBackend(**rd_kwargs)
    if kind == "pixellab":
        import os

        if not (
            os.environ.get("PIXELLAB_SECRET")
            or os.environ.get("PIXELLAB_API_KEY")
        ):
            raise ValueError(
                "pixellab image backend needs PIXELLAB_SECRET (or "
                "PIXELLAB_API_KEY) in the environment. A .env file is NOT "
                "read automatically — export it first: "
                "set -a; source .env; set +a"
            )
        from canon.backends.image_pixellab import PixelLabBackend

        pl_kwargs: dict = {}
        if model:
            pl_kwargs["model"] = model
        if seed is not None:
            pl_kwargs["seed"] = seed
        return PixelLabBackend(**pl_kwargs)
    if kind == "local":
        from canon.backends.image_local import LocalImageBackend

        return LocalImageBackend(model)
    raise ValueError(
        f"Unknown image backend {kind!r} — expected none, fake, fal, "
        "retro, pixellab, or local."
    )


def build_image_producer(
    kind: str | None,
    model: str | None = None,
    edit_model: str | None = None,
    seed: int | None = None,
    edit_kind: str | None = None,
):
    """CLI/env wiring: an image-backend name -> producer, or ``None`` for the
    placeholder path. When ``edit_kind`` is given (and differs from ``kind``)
    the ANIMATION img2img runs on THAT backend instead of the sprite backend —
    e.g. PixelLab generates the character sheets and nano (fal) animates them
    (postmortem ticket 7). The edit backend takes the same ``edit_model`` /
    ``seed`` levers."""
    backend = _build_backend(kind, model, edit_model, seed)
    if backend is None:
        return None
    edit_backend = None
    if edit_kind and edit_kind not in ("none", kind):
        edit_backend = _build_backend(edit_kind, None, edit_model, seed)
    return DiffusionSheetProducer(backend, edit_backend)

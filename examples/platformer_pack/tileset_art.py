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
        f"Art style: {graphics.art_style}. "
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

    out = Image.new("RGBA", img.size)
    out.putdata(
        [
            tuple(round(c * 255) for c in colorsys.hsv_to_rgb(h, s, v)) + (a,)
            for h, s, v, a in zip(hues, sats, final, alphas)
        ]
    )
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
        f"{graphics.art_style}. Dominant color {color_hex}. Full body, "
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


def backdrop_prompt(
    band: int, theme: str, world_title: str, palette: dict[str, str],
    graphics: Any,
) -> str:
    swatch = ", ".join(sorted(palette.values())[:4]) or "muted dusk tones"
    return (
        f"Wide seamless parallax background layer for a 2D platformer: "
        f"{BAND_DESCRIPTORS[min(band, len(BAND_DESCRIPTORS) - 1)]}, stage "
        f"'{theme}' of the game '{world_title}'. Art style: "
        f"{graphics.art_style}. Colors harmonizing with {swatch}. "
        f"Horizontally tileable, sky at the top fading out. No "
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

    def __init__(self, backend: Any) -> None:
        self.backend = backend

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

    def tile_image(
        self, tile: Any, role_hex: str, theme: str, world_title: str, graphics: Any
    ) -> Any:
        from PIL import Image

        prompt = tile_prompt(tile, role_hex, theme, world_title, graphics)
        sanitized = (
            f"Seamless tileable video-game texture tile: {tile.name} "
            f"({tile.category}). It must read as "
            f"{CATEGORY_ART.get(tile.category, '')}. Art style: "
            f"{graphics.art_style}. Dominant color {role_hex}. Flat "
            f"texture fill designed to repeat in a grid. No text, no "
            f"borders, no objects."
        )
        raw = self._generate(prompt, sanitized, graphics.gen_px, graphics.gen_px)
        img = Image.open(io.BytesIO(raw)).convert("RGB")
        if tile.category in _CUTOUT_CATEGORIES:
            # Cut the invented backdrop at full resolution (halo-safe),
            # then keep it only if the subject actually survived. conform
            # preserves this alpha; the QA sampler measures only the
            # visible pixels.
            keyed = remove_background(img)
            if _opaque_fraction(keyed) >= MIN_TILE_OPAQUE_FRACTION:
                img = keyed
        resample = (
            Image.NEAREST if graphics.render_filter == "crisp" else Image.LANCZOS
        )
        img = img.resize((graphics.tile_px, graphics.tile_px), resample)
        return conform_to_palette(img, role_hex, levels=graphics.posterize_levels)

    def sprite_image(
        self,
        name: str,
        descriptor: str,
        color_hex: str,
        theme: str,
        world_title: str,
        graphics: Any,
        size: tuple[int, int],
    ) -> Any:
        """Transparent RGBA sprite at ``size``. Background is cut at full
        generation resolution (halo avoidance) before downscaling."""
        from PIL import Image

        prompt = sprite_prompt(
            name, descriptor, color_hex, theme, world_title, graphics
        )
        sanitized = (
            f"Single video-game creature sprite: {name}. Art style: "
            f"{graphics.art_style}. Dominant color {color_hex}. Full body, "
            f"side view facing right, centered, isolated on a plain solid "
            f"white background. No shadow, no text."
        )
        raw = self._generate(prompt, sanitized, graphics.gen_px, graphics.gen_px)
        img = Image.open(io.BytesIO(raw))
        img = remove_background(img)
        img = _bottom_align(img)  # feet on the frame bottom, not floating
        resample = (
            Image.NEAREST if graphics.render_filter == "crisp" else Image.LANCZOS
        )
        return img.resize(size, resample)

    def backdrop_image(
        self,
        band: int,
        theme: str,
        world_title: str,
        palette: dict[str, str],
        bg_hex: str,
        graphics: Any,
    ) -> Any:
        """One parallax band (RGB, gen_px × gen_px/2), atmosphere-blended
        toward the palette background for playfield readability."""
        from PIL import Image

        prompt = backdrop_prompt(band, theme, world_title, palette, graphics)
        sanitized = (
            f"Wide seamless parallax background layer for a 2D platformer: "
            f"{BAND_DESCRIPTORS[min(band, len(BAND_DESCRIPTORS) - 1)]}. "
            f"Art style: {graphics.art_style}. Horizontally tileable. "
            f"No characters, no text."
        )
        raw = self._generate(
            prompt, sanitized, graphics.gen_px, graphics.gen_px // 2
        )
        img = Image.open(io.BytesIO(raw)).convert("RGB")
        return atmosphere_blend(img, bg_hex)


def build_image_producer(kind: str | None, model: str | None = None):
    """CLI/env wiring: an image-backend name → producer, or ``None`` for
    the placeholder path. Paid backends are only ever constructed here,
    from an explicit flag — never implied by the LLM backend choice."""
    if not kind or kind == "none":
        return None
    if kind == "fake":
        from canon.backends.testing import FakeImageBackend

        return DiffusionSheetProducer(FakeImageBackend())
    if kind == "fal":
        import os

        # Fail fast, BEFORE any paid LLM work: fal defers its credential
        # check to the first generate call, which on the first real run
        # burned a full generation and then warned 13 times. Missing
        # credentials are known at launch — die at launch.
        if not (
            os.environ.get("FAL_KEY")
            or (os.environ.get("FAL_KEY_ID") and os.environ.get("FAL_KEY_SECRET"))
        ):
            raise ValueError(
                "--image-backend fal needs FAL_KEY (or FAL_KEY_ID + "
                "FAL_KEY_SECRET) in the environment. A .env file is NOT "
                "read automatically — export it first, e.g.: "
                "set -a; source .env; set +a"
            )
        from canon.backends.image_fal import FalImageBackend

        backend = FalImageBackend(model) if model else FalImageBackend()
        return DiffusionSheetProducer(backend)
    if kind == "local":
        from canon.backends.image_local import LocalImageBackend

        return DiffusionSheetProducer(LocalImageBackend(model))
    raise ValueError(
        f"Unknown image backend {kind!r} — expected none, fake, fal, or local."
    )

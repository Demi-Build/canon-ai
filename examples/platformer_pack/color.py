"""Shared color arithmetic — a LEAF module (no pack imports) so both
``style`` (palette repair tools) and ``phases`` (placeholder assignment)
can use the same luminance/hue math without an import cycle.
"""

from __future__ import annotations

import colorsys

#: Below this HSV saturation a color has no meaningful hue — treat it as
#: colorless rather than trusting a noisy angle.
MIN_HUE_SATURATION = 0.10


def hex_to_rgb(color: str) -> tuple[int, int, int]:
    color = color.lstrip("#")
    return tuple(int(color[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def rgb_to_hex(rgb: tuple[int, int, int]) -> str:
    r, g, b = (max(0, min(255, round(c))) for c in rgb)
    return f"#{r:02x}{g:02x}{b:02x}"


def luminance(color: str) -> float:
    """Rec.709 luminance on the 0..255 scale."""
    r, g, b = hex_to_rgb(color)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def shift_luminance(color: str, target: float) -> str:
    """Blend toward white (or black) to hit ``target`` luminance exactly —
    hue-preserving lightness arithmetic. lum(lerp(c, white, t)) is linear
    in t, so both directions have closed forms."""
    r, g, b = hex_to_rgb(color)
    lum = luminance(color)
    if target > lum:
        t = (target - lum) / (255.0 - lum) if lum < 255 else 0.0
        r, g, b = (round(c + t * (255 - c)) for c in (r, g, b))
    else:
        t = (lum - target) / lum if lum > 0 else 0.0
        r, g, b = (round(c * (1 - t)) for c in (r, g, b))
    return f"#{r:02x}{g:02x}{b:02x}"


def hue_of(color: str) -> float | None:
    """The color's HSV hue in degrees, or ``None`` for (near-)neutrals
    whose hue angle is noise."""
    r, g, b = (c / 255.0 for c in hex_to_rgb(color))
    h, s, _v = colorsys.rgb_to_hsv(r, g, b)
    if s < MIN_HUE_SATURATION:
        return None
    return h * 360.0


def hue_distance(a: float, b: float) -> float:
    """Shortest angular distance between two hues in degrees (0..180)."""
    d = abs(a - b) % 360.0
    return min(d, 360.0 - d)

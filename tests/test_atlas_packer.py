"""Atlas packer tests — pack_atlas/_trim_frame/reconstitute_frame
(tileset_art.py, G4 shipping manifest): byte-identical repacks, lossless
round-trip through trimmed rects, shelf geometry + forced wrap,
transparent frames packing full-square, and __left keys as ordinary
states."""

from __future__ import annotations

import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from PIL import Image  # noqa: E402

from examples.platformer_pack.tileset_art import (  # noqa: E402
    _trim_frame,
    pack_atlas,
    reconstitute_frame,
)

FRAME = 48  # shared untrimmed square side for every synthetic frame


def _frame(w: int, h: int, ox: int, oy: int, seed: int) -> Image.Image:
    """An untrimmed FRAME-square holding a w x h gradient box at (ox, oy);
    a transparent pinhole inside the box proves interior alpha survives
    the trim/reconstitute round-trip."""
    box = Image.new("RGBA", (w, h))
    box.putdata(
        [
            ((x * 7 + seed) % 256, (y * 11 + seed * 3) % 256, (x + y) % 256, 255)
            for y in range(h)
            for x in range(w)
        ]
    )
    box.putpixel((w // 2, h // 2), (0, 0, 0, 0))
    frame = Image.new("RGBA", (FRAME, FRAME), (0, 0, 0, 0))
    frame.paste(box, (ox, oy))
    return frame


def _state_frames() -> dict[str, list[Image.Image]]:
    # idle 2 + walk 4 with varying box sizes/positions → varying trims.
    return {
        "idle": [_frame(20, 30, 14, 18, 1), _frame(24, 22, 12, 26, 2)],
        "walk": [
            _frame(30, 40, 9, 8, 3),
            _frame(16, 44, 16, 4, 4),
            _frame(28, 20, 10, 28, 5),
            _frame(22, 36, 13, 12, 6),
        ],
    }


def _png_bytes(img: Image.Image) -> bytes:
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return buffer.getvalue()


class TestPackAtlas:
    def test_deterministic_bytes_and_manifest(self):
        atlas_a, rects_a = pack_atlas(_state_frames())
        atlas_b, rects_b = pack_atlas(_state_frames())
        assert _png_bytes(atlas_a) == _png_bytes(atlas_b)
        assert rects_a == rects_b

    def test_round_trip_pixel_identical(self):
        frames = _state_frames()
        atlas, rects = pack_atlas(frames)
        for state, originals in frames.items():
            assert len(rects[state]) == len(originals)
            for rec, orig in zip(rects[state], originals):
                rebuilt = reconstitute_frame(atlas, rec, (FRAME, FRAME))
                assert rebuilt.tobytes() == orig.tobytes()

    def test_shelf_geometry_wrap_and_pad(self):
        pad = 3
        atlas, rects = pack_atlas(_state_frames(), max_width=70, pad=pad)
        flat = [r for recs in rects.values() for r in recs]
        assert all(set(r) == {"x", "y", "w", "h", "ox", "oy"} for r in flat)
        assert atlas.width <= 70
        for r in flat:
            assert 0 <= r["x"] and r["x"] + r["w"] <= atlas.width
            assert 0 <= r["y"] and r["y"] + r["h"] <= atlas.height
        rows: dict[int, list[dict]] = {}
        for r in flat:
            rows.setdefault(r["y"], []).append(r)
        assert len(rows) >= 2  # max_width small enough to force a wrap
        for recs in rows.values():
            recs.sort(key=lambda r: r["x"])
            for prev, nxt in zip(recs, recs[1:]):
                assert nxt["x"] >= prev["x"] + prev["w"] + pad
        tops = sorted(rows)
        for prev, nxt in zip(tops, tops[1:]):
            assert nxt >= prev + max(r["h"] for r in rows[prev]) + pad

    def test_transparent_frame_packs_full_square(self):
        blank = Image.new("RGBA", (FRAME, FRAME), (0, 0, 0, 0))
        crop, ox, oy = _trim_frame(blank)
        assert (crop.size, ox, oy) == ((FRAME, FRAME), 0, 0)
        atlas, rects = pack_atlas({"idle": [_frame(20, 30, 14, 18, 1), blank]})
        rec = rects["idle"][1]
        assert (rec["w"], rec["h"], rec["ox"], rec["oy"]) == (FRAME, FRAME, 0, 0)
        rebuilt = reconstitute_frame(atlas, rec, (FRAME, FRAME))
        assert rebuilt.tobytes() == blank.tobytes()

    def test_left_keys_pack_as_ordinary_states(self):
        frames = {
            "walk": [_frame(30, 40, 9, 8, 3), _frame(22, 36, 13, 12, 6)],
            "walk__left": [_frame(30, 40, 9, 8, 7), _frame(22, 36, 13, 12, 8)],
        }
        atlas, rects = pack_atlas(frames)
        assert set(rects) == {"walk", "walk__left"}
        assert len(rects["walk__left"]) == 2
        for rec, orig in zip(rects["walk__left"], frames["walk__left"]):
            rebuilt = reconstitute_frame(atlas, rec, (FRAME, FRAME))
            assert rebuilt.tobytes() == orig.tobytes()

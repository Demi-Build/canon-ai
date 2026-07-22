"""Art SAMPLE mode — one exemplar, fanned across wired image backends.

The sample→lock→batch loop's SAMPLE step: generate a single exemplar
subject (the player sprite, one registry tile, or the placeholder
palette) through every backend the caller names, side by side, for a
HUMAN to judge — locking a look and batch generation never happen here.
$0 with the fake backend; paid backends render only when the user has
wired their keys and names them explicitly. Claude never runs paid
generation.

Subject grammar (one exemplar per run — the comparison stays legible):

- ``player``      — the hero sprite at its GraphicsSpec art footprint.
- ``tile:<name>`` — one registry tile (``tile:floor``); role hex from
  the placeholder palette.
- ``palette``     — no generation: a labeled swatch grid of
  ``PLACEHOLDER_PALETTE``. The palette test is a look-at artifact.

Two artifacts gate the judgment:

- ``contact_sheet.png`` — every backend's output side by side, labeled.
- ``<backend>_griddrop.png`` — the asset composited into a synthetic
  level crop at NATIVE scale with the art-canvas box (cyan), the
  one-world-tile hitbox box (red), and the bottom-center anchor tick
  drawn on: passing image QA is NOT the same as working in the grid.

A backend whose key is not wired records an error entry and the fan-out
continues — one missing key never sinks the other backends. All output
names are fixed (no timestamps); with fakes the run is byte-identical.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from examples.platformer_pack.graphics import GraphicsSpec
from examples.platformer_pack.tiles import DEFAULT_TILES
from examples.platformer_pack.tileset import PLACEHOLDER_PALETTE
from examples.platformer_pack.tileset_art import (
    apply_watermark,
    art_provenance,
    build_image_producer,
    png_bytes,
)

#: Neutral sprite hex for the sample hero — near the placeholder
#: palette's grays so the judgment is about the backend's rendering, not
#: a color roll.
NEUTRAL_SPRITE_HEX = "#e0e0e0"

#: Grid-drop overlay colors: art canvas (the asset's full frame), the
#: one-world-tile physics hitbox, and the bottom-center anchor tick.
_CANVAS_BOX = (0, 255, 255, 255)
_HITBOX_BOX = (255, 0, 0, 255)
_ANCHOR_TICK = (255, 255, 0, 255)

#: Grid-drop mock size in world tiles; rows grow if the asset is taller.
_MOCK_COLS = 6
_MOCK_ROWS = 4

_SHEET_BG = (24, 24, 32, 255)
_SHEET_FG = (230, 230, 230, 255)
_PAD = 8
_LABEL_H = 14


def _slug(subject: str) -> str:
    return subject.replace(":", "-")


def _role_hex(role: str) -> str:
    # Unknown color_role keeps tileset.py's loud-magenta convention.
    rgba = PLACEHOLDER_PALETTE.get(role, (255, 0, 255, 255))
    return "#{:02x}{:02x}{:02x}".format(*rgba[:3])


def _parse_subject(subject: str) -> tuple[str, str | None]:
    """``subject`` → (kind, tile name). Bad grammar fails loudly here,
    before any backend is constructed."""
    if subject in ("player", "palette"):
        return subject, None
    if subject.startswith("tile:"):
        name = subject.split(":", 1)[1]
        if name not in DEFAULT_TILES.by_name:
            raise ValueError(
                f"unknown registry tile {name!r} — expected one of "
                f"{sorted(DEFAULT_TILES.by_name)}"
            )
        return "tile", name
    raise ValueError(
        f"unknown sample subject {subject!r} — expected 'player', "
        "'tile:<registry tile name>', or 'palette'"
    )


def _generate_asset(
    producer, kind: str, tile_name: str | None, graphics: GraphicsSpec,
    seed_theme: str, world_title: str,
):
    if kind == "player":
        fw, fh = graphics.player_footprint
        return producer.sprite_image(
            name="hero",
            descriptor="friendly mascot protagonist, full body",
            color_hex=NEUTRAL_SPRITE_HEX,
            theme=seed_theme,
            world_title=world_title,
            graphics=graphics,
            size=(fw * graphics.base_cell, fh * graphics.base_cell),
        )
    tile = DEFAULT_TILES.by_name[tile_name]
    return producer.tile_image(
        tile, _role_hex(tile.color_role), seed_theme, world_title, graphics
    )


def _grid_drop(asset, kind: str, graphics: GraphicsSpec):
    """The in-grid gate: the asset seated in a synthetic level crop at
    NATIVE scale — a floor row (the sampled tile itself, or neutral gray
    for the player) under sky, asset bottom-anchored on the floor, with
    the art-canvas and hitbox boxes plus the anchor tick drawn on."""
    tile_px = graphics.tile_px
    asset = asset.convert("RGBA")
    aw, ah = asset.size
    rows = max(_MOCK_ROWS, -(-ah // tile_px) + 1)  # asset always fits above the floor
    w, h = _MOCK_COLS * tile_px, rows * tile_px
    mock = Image.new("RGBA", (w, h), PLACEHOLDER_PALETTE["background"])

    if kind == "tile":
        floor_tile = asset  # seams/edges must read when the tile repeats
    else:
        floor_tile = Image.new(
            "RGBA", (tile_px, tile_px), PLACEHOLDER_PALETTE["ground"]
        )
    floor_top = (rows - 1) * tile_px
    for col in range(_MOCK_COLS):
        mock.alpha_composite(floor_tile, (col * tile_px, floor_top))

    ax, ay = (w - aw) // 2, floor_top - ah
    mock.alpha_composite(asset, (ax, ay))

    draw = ImageDraw.Draw(mock)
    # Art canvas vs hitbox: for a tile they coincide (canvas IS the world
    # tile — red covers cyan); the player's overdraw is the visible gap.
    draw.rectangle([ax, ay, ax + aw - 1, ay + ah - 1], outline=_CANVAS_BOX)
    hit = graphics.cells_per_tile * graphics.base_cell  # 1 world tile
    cx = ax + aw // 2
    draw.rectangle(
        [cx - hit // 2, floor_top - hit, cx - hit // 2 + hit - 1, floor_top - 1],
        outline=_HITBOX_BOX,
    )
    draw.line([cx, floor_top - 4, cx, floor_top + 3], fill=_ANCHOR_TICK)
    draw.line([cx - 3, floor_top - 1, cx + 3, floor_top - 1], fill=_ANCHOR_TICK)
    return mock


def _palette_swatch():
    """Labeled swatch grid of the placeholder palette, in its authored
    order (tileset.py groups the roles semantically)."""
    roles = list(PLACEHOLDER_PALETTE.items())
    cols, sw, label_h = 4, 72, 26  # two label lines: role, then hex
    grid_rows = -(-len(roles) // cols)
    img = Image.new(
        "RGBA",
        (_PAD + cols * (sw + _PAD), _PAD + grid_rows * (sw + label_h + _PAD)),
        _SHEET_BG,
    )
    draw = ImageDraw.Draw(img)
    for i, (role, rgba) in enumerate(roles):
        x = _PAD + (i % cols) * (sw + _PAD)
        y = _PAD + (i // cols) * (sw + label_h + _PAD)
        draw.rectangle([x, y, x + sw - 1, y + sw - 1], fill=rgba, outline=_SHEET_FG)
        draw.text((x, y + sw + 2), role, fill=_SHEET_FG)
        draw.text((x, y + sw + 14), _role_hex(role), fill=_SHEET_FG)
    return img


def _contact_sheet(panels: list[tuple[str, Image.Image | None]], path: Path) -> None:
    """Every backend side by side, name labeled under each; None (an
    unwired backend) renders as an empty labeled panel so the human sees
    which legs were missing. Small assets get an integer NEAREST upscale
    for judgeability — native scale is the grid-drop's job."""
    sides = [max(img.size) for _name, img in panels if img is not None]
    scale = max(1, -(-96 // max(sides))) if sides else 1
    panel_w = max((img.width * scale for _n, img in panels if img is not None), default=96)
    panel_h = max((img.height * scale for _n, img in panels if img is not None), default=96)

    sheet = Image.new(
        "RGBA",
        (_PAD + max(1, len(panels)) * (panel_w + _PAD), panel_h + _LABEL_H + 2 * _PAD),
        _SHEET_BG,
    )
    draw = ImageDraw.Draw(sheet)
    for i, (name, img) in enumerate(panels):
        x = _PAD + i * (panel_w + _PAD)
        draw.rectangle(
            [x, _PAD, x + panel_w - 1, _PAD + panel_h - 1], fill=(40, 40, 52, 255)
        )
        if img is not None:
            scaled = img.convert("RGBA").resize(
                (img.width * scale, img.height * scale), Image.NEAREST
            )
            sheet.alpha_composite(
                scaled,
                (x + (panel_w - scaled.width) // 2, _PAD + (panel_h - scaled.height) // 2),
            )
        else:
            draw.text((x + 4, _PAD + panel_h // 2 - 5), "unavailable", fill=_SHEET_FG)
        draw.text((x, _PAD + panel_h + 3), name, fill=_SHEET_FG)
    sheet.save(path)


def run_art_sample(
    subject: str,
    backend_names: list[str],
    graphics: GraphicsSpec,
    out_dir: Path,
    *,
    model: str | None = None,
    seed_theme: str = "sample biome",
    world_title: str = "Art Sample",
) -> dict:
    """Fan one exemplar ``subject`` across ``backend_names`` into
    ``out_dir/<subject-slug>/``: per-backend ``<backend>.png`` +
    ``<backend>_griddrop.png``, and one labeled ``contact_sheet.png``.
    ``palette`` ignores the backends (code-rendered swatch grid).
    Returns ``{subject, results: [{backend, files|error}], contact_sheet}``.
    """
    kind, tile_name = _parse_subject(subject)
    subject_dir = Path(out_dir) / _slug(subject)
    subject_dir.mkdir(parents=True, exist_ok=True)

    results: list[dict] = []
    panels: list[tuple[str, Image.Image | None]] = []

    if kind == "palette":
        swatch = _palette_swatch()
        asset_path = subject_dir / "palette.png"
        swatch.save(asset_path)
        results.append({"backend": "code", "files": {"asset": str(asset_path)}})
        panels.append(("code", swatch))
    else:
        for name in backend_names:
            try:
                producer = build_image_producer(name, model)
            except ValueError as e:  # missing key / unknown backend: record, keep fanning
                results.append({"backend": name, "error": str(e)})
                panels.append((name, None))
                continue
            if producer is None:  # "none" is the placeholder path — nothing to sample
                results.append(
                    {"backend": name, "error": "backend produces no pixels — nothing to sample"}
                )
                panels.append((name, None))
                continue
            try:
                asset = _generate_asset(
                    producer, kind, tile_name, graphics, seed_theme, world_title
                )
            except Exception as e:  # noqa: BLE001 — one bad leg must not sink the fan-out
                results.append({"backend": name, "error": str(e)})
                panels.append((name, None))
                continue
            # Exemplars are producer-GENERATED: they carry the readable
            # provenance stamp (and the watermark off tiles) like batch
            # assets; the contact sheet/griddrop/swatch are derived views
            # and stay unstamped. No pipeline seed — sample mode has none.
            if graphics.visible_watermark and kind != "tile":
                apply_watermark(asset)
            asset_path = subject_dir / f"{name}.png"
            asset_path.write_bytes(
                png_bytes(
                    asset,
                    art_provenance(
                        asset=f"sample:{_slug(subject)}/{name}",
                        producer=producer,
                        graphics=graphics,
                    ),
                )
            )
            drop_path = subject_dir / f"{name}_griddrop.png"
            _grid_drop(asset, kind, graphics).save(drop_path)
            results.append(
                {
                    "backend": name,
                    "files": {"asset": str(asset_path), "griddrop": str(drop_path)},
                }
            )
            panels.append((name, asset))

    sheet_path = subject_dir / "contact_sheet.png"
    _contact_sheet(panels, sheet_path)
    return {"subject": subject, "results": results, "contact_sheet": str(sheet_path)}

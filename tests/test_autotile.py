"""G5 autotile core: 16-variant floor slot emission (tileset.py),
mean-preserving edge shading, autotile.json, and mask-based terrain
resolution (layers.py).

The contract under test: mask bits N=1/E=2/S=4/W=8, a bit SET when that
side is EXPOSED (neighbor not collision-"solid"; out-of-grid counts
solid); mask 0 = flat interior emitted FIRST; every variant's region
mean stays byte-exactly the flat base color so region-average consumers
(block render, pygame colors, QA palette_conformance) never notice.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("PIL")

from PIL import Image  # noqa: E402

from canon.adapters import JsonOutputAdapter  # noqa: E402
from examples.platformer_pack.layers import assign_level_terrain  # noqa: E402
from examples.platformer_pack.tiles import DEFAULT_TILES  # noqa: E402
from examples.platformer_pack.tileset import (  # noqa: E402
    AUTOTILE_BIT_SEMANTICS,
    PLACEHOLDER_PALETTE,
    PlaceholderTilesetPhase,
    shade_floor_variant,
)

STAGE = "s1"

#: Default registry ids the grids below stamp (tile_types.json).
EMPTY, FLOOR, PLATFORM, SPIKE, WATER = 0, 1, 2, 10, 20


def _ctx(tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(
        bible=SimpleNamespace(stages={}, tilesets={}, levels={}),
        artifacts={"stage_ids": [STAGE]},
        adapter=JsonOutputAdapter(tmp_path),
        llm=None,
        config=None,
    )


def _run_phase(tmp_path: Path) -> SimpleNamespace:
    ctx = _ctx(tmp_path)
    PlaceholderTilesetPhase().run(ctx)
    return ctx


def _floor_slots(tileset) -> list:
    return [s for s in tileset.slots if s.name == "floor"]


class TestVariantEmission:
    def test_sixteen_floor_variants_mask_order_consecutive(
        self, tmp_path: Path
    ) -> None:
        ctx = _run_phase(tmp_path)
        tileset = ctx.bible.tilesets[STAGE]
        floors = _floor_slots(tileset)
        assert len(floors) == 16
        assert [s.params["autotile_mask"] for s in floors] == list(range(16))
        assert all(s.tile_type == FLOOR for s in floors)
        assert all(s.collision == "solid" for s in floors)
        # Consecutive indices, single-row sheet regions continuing on.
        first = floors[0].index
        assert [s.index for s in floors] == list(range(first, first + 16))
        for s in tileset.slots:
            px = s.px_region[2]
            assert s.px_region == (s.index * px, 0, px, px)
        # Whole-sheet contract: every slot index sequential, 26 total
        # (10 registry tiles, floor expanded to 16, water + its
        # submerged-interior sibling), int8-safe.
        assert [s.index for s in tileset.slots] == list(range(26))

    def test_non_floor_tiles_keep_exactly_one_slot(self, tmp_path: Path) -> None:
        ctx = _run_phase(tmp_path)
        tileset = ctx.bible.tilesets[STAGE]
        for tile in DEFAULT_TILES.tiles:
            if tile.name == "floor":
                continue
            matching = [s for s in tileset.slots if s.tile_type == tile.id]
            if tile.category == "volume":
                # Volume tiles carry the submerged-interior sibling:
                # surface FIRST (the 1:1 default), deep marked.
                assert len(matching) == 2
                assert "water_deep" not in matching[0].params
                assert matching[1].params["water_deep"] is True
                assert matching[1].collision == matching[0].collision
                continue
            assert len(matching) == 1
            assert "autotile_mask" not in matching[0].params

    def test_mask_zero_slot_is_first_and_flat(self, tmp_path: Path) -> None:
        ctx = _run_phase(tmp_path)
        tileset = ctx.bible.tilesets[STAGE]
        first_floor = next(s for s in tileset.slots if s.name == "floor")
        assert first_floor.params["autotile_mask"] == 0
        sheet = Image.open(
            tmp_path / tileset.tilesheet_path
        ).convert("RGBA")
        x, y, w, h = first_floor.px_region
        region = np.asarray(sheet.crop((x, y, x + w, y + h)))
        # Byte-identical to the pre-autotile single floor slot: a flat
        # placeholder-ground square (no style palette in this ctx).
        assert (region == PLACEHOLDER_PALETTE["ground"]).all()

    def test_autotile_json_matches_slots(self, tmp_path: Path) -> None:
        ctx = _run_phase(tmp_path)
        tileset = ctx.bible.tilesets[STAGE]
        data = json.loads(
            (tmp_path / f"tileset/{STAGE}/autotile.json").read_text()
        )
        assert data["tile"] == "floor"
        assert data["tile_type"] == FLOOR
        assert data["bit_semantics"] == AUTOTILE_BIT_SEMANTICS
        assert sorted(data["variants"], key=int) == [str(m) for m in range(16)]
        by_mask = {
            int(s.params["autotile_mask"]): s for s in _floor_slots(tileset)
        }
        for key, entry in data["variants"].items():
            slot = by_mask[int(key)]
            assert entry["index"] == slot.index
            assert tuple(entry["px_region"]) == slot.px_region


class TestMeanPreservingShading:
    BASE = (119, 100, 89, 255)  # mid-range: no clamping in play

    def _flat(self, color=BASE, px: int = 32) -> Image.Image:
        return Image.new("RGBA", (px, px), color)

    def test_mask_zero_untouched(self) -> None:
        img = self._flat()
        assert shade_floor_variant(img, 0) is img

    def test_every_mask_preserves_the_mean_exactly(self) -> None:
        flat = np.asarray(self._flat(), dtype=np.int64)
        for mask in range(16):
            shaded = np.asarray(
                shade_floor_variant(self._flat(), mask), dtype=np.int64
            )
            # Per-channel SUM equality == exact mean equality (no float).
            assert (
                shaded.sum(axis=(0, 1)) == flat.sum(axis=(0, 1))
            ).all(), f"mask {mask} drifted the region mean"
            if mask:
                assert (shaded != flat).any(), f"mask {mask} not shaded"

    def test_region_average_sample_stays_the_base_color(self) -> None:
        # The block render / pygame consumers sample a 1x1 BILINEAR
        # resize — every variant must resolve to the flat base color.
        for mask in range(16):
            sample = (
                shade_floor_variant(self._flat(), mask)
                .resize((1, 1), Image.BILINEAR)
                .getpixel((0, 0))
            )
            assert sample == self.BASE, f"mask {mask} shifted the sample"

    def test_clamp_guard_near_white_still_mean_exact(self) -> None:
        near_white = (250, 252, 251, 255)
        flat = np.asarray(self._flat(near_white), dtype=np.int64)
        for mask in range(16):
            shaded = np.asarray(
                shade_floor_variant(self._flat(near_white), mask),
                dtype=np.int64,
            )
            assert (shaded.sum(axis=(0, 1)) == flat.sum(axis=(0, 1))).all()
            assert shaded.max() <= 255 and shaded.min() >= 0

    def test_saturated_white_stays_white(self) -> None:
        white = (255, 255, 255, 255)
        shaded = np.asarray(shade_floor_variant(self._flat(white), 15))
        assert (shaded == 255).all()


def _write_level(
    ctx: SimpleNamespace, grid: np.ndarray, level_id: str = "l1"
) -> SimpleNamespace:
    rel = f"level/{STAGE}/{level_id}/collision.npz"
    ctx.adapter.write_numpy(rel, collision=grid.astype(np.int8))
    return SimpleNamespace(
        stage_id=STAGE,
        level_id=level_id,
        collision=rel,
        terrain="",
        terrain_hash="",
        step_parents={},
    )


def _terrain(ctx: SimpleNamespace, level: SimpleNamespace) -> np.ndarray:
    with np.load(ctx.adapter.resolve_path(level.terrain)) as data:
        return data["terrain"]


class TestTerrainResolution:
    def _setup(self, tmp_path: Path) -> tuple[SimpleNamespace, dict[int, int]]:
        ctx = _run_phase(tmp_path)
        tileset = ctx.bible.tilesets[STAGE]
        mask_to_index = {
            int(s.params["autotile_mask"]): s.index for s in _floor_slots(tileset)
        }
        return ctx, mask_to_index

    def test_exposure_matrix(self, tmp_path: Path) -> None:
        ctx, floor_at = self._setup(tmp_path)
        tileset = ctx.bible.tilesets[STAGE]
        one_to_one = {
            int(s.tile_type): s.index
            for s in tileset.slots
            if "autotile_mask" not in s.params
            and not s.params.get("water_deep")
        }
        grid = np.array(
            [
                [FLOOR, FLOOR, FLOOR, FLOOR, FLOOR, FLOOR],
                [FLOOR, FLOOR, FLOOR, FLOOR, FLOOR, FLOOR],
                [FLOOR, FLOOR, EMPTY, FLOOR, WATER, FLOOR],
                [FLOOR, FLOOR, FLOOR, FLOOR, FLOOR, FLOOR],
                [FLOOR, FLOOR, FLOOR, FLOOR, FLOOR, FLOOR],
            ],
            dtype=np.int8,
        )
        level = _write_level(ctx, grid)
        assign_level_terrain(ctx, level)
        terrain = _terrain(ctx, level)

        # Level border counts SOLID: the corner cell is fully interior.
        assert terrain[0, 0] == floor_at[0]
        assert terrain[1, 1] == floor_at[0]  # surrounded by floor
        assert terrain[1, 2] == floor_at[4]  # empty below → S exposed
        assert terrain[3, 2] == floor_at[1]  # empty above → N exposed
        assert terrain[2, 1] == floor_at[2]  # empty to the east → E
        # Water is a volume, NOT support: exposed on both sides of it.
        assert terrain[2, 3] == floor_at[2 + 8]  # empty W, water E
        assert terrain[1, 4] == floor_at[4]  # water below → S exposed
        assert terrain[3, 4] == floor_at[1]  # water above → N exposed
        assert terrain[2, 5] == floor_at[8]  # water W; E border = solid
        # Non-floor cells keep the 1:1 map; this water cell sits UNDER
        # floor (no air above), so it resolves to the deep sibling.
        assert terrain[2, 2] == one_to_one[EMPTY]
        deep_water = next(
            s.index for s in tileset.slots if s.params.get("water_deep")
        )
        assert terrain[2, 4] == deep_water

    def test_one_way_and_hazard_count_exposed(self, tmp_path: Path) -> None:
        ctx, floor_at = self._setup(tmp_path)
        tileset = ctx.bible.tilesets[STAGE]
        one_to_one = {
            int(s.tile_type): s.index
            for s in tileset.slots
            if "autotile_mask" not in s.params
            and not s.params.get("water_deep")
        }
        grid = np.array(
            [
                [EMPTY, EMPTY, EMPTY],
                [PLATFORM, FLOOR, SPIKE],
                [FLOOR, FLOOR, FLOOR],
            ],
            dtype=np.int8,
        )
        level = _write_level(ctx, grid)
        assign_level_terrain(ctx, level)
        terrain = _terrain(ctx, level)

        # N empty + E hazard + W one_way exposed; S floor supports.
        assert terrain[1, 1] == floor_at[1 + 2 + 8]
        # One_way above counts exposed; W/S borders count solid.
        assert terrain[2, 0] == floor_at[1]
        # Non-floor tiles resolve 1:1 and never autotile.
        assert terrain[1, 0] == one_to_one[PLATFORM]
        assert terrain[1, 2] == one_to_one[SPIKE]

    def test_step_parents_kept(self, tmp_path: Path) -> None:
        ctx, _ = self._setup(tmp_path)
        grid = np.full((3, 3), FLOOR, dtype=np.int8)
        level = _write_level(ctx, grid)
        assign_level_terrain(ctx, level)
        assert level.step_parents["terrain"] == [
            f"level:{STAGE}/l1/collision",
            f"tileset:{STAGE}",
        ]
        assert level.terrain == f"level/{STAGE}/l1/terrain.npz"
        assert level.terrain_hash.startswith("sha256:")


class TestWaterDeepResolution:
    """Water-striping fix: the air-above probe picks surface vs deep."""

    def test_surface_only_at_the_air_interface(self, tmp_path: Path) -> None:
        ctx = _run_phase(tmp_path)
        tileset = ctx.bible.tilesets[STAGE]
        surface = next(
            s.index
            for s in tileset.slots
            if s.tile_type == WATER and not s.params.get("water_deep")
        )
        deep = next(
            s.index for s in tileset.slots if s.params.get("water_deep")
        )
        grid = np.array(
            [
                [WATER, EMPTY, FLOOR],
                [WATER, WATER, WATER],
                [WATER, WATER, WATER],
            ],
            dtype=np.int8,
        )
        level = _write_level(ctx, grid)
        assign_level_terrain(ctx, level)
        terrain = _terrain(ctx, level)
        # Grid top counts NON-air (borders stay interior, the floors'
        # rule): submerged-to-the-ceiling water draws depth, not a lip.
        assert terrain[0, 0] == deep
        # The real air-water interface keeps the surface lip...
        assert terrain[1, 1] == surface
        # ...but water under water, under solid, and rows below stay deep.
        assert terrain[1, 0] == deep
        assert terrain[1, 2] == deep  # floor above, no air
        assert (terrain[2] == deep).all()

    def test_dry_grid_never_touches_the_deep_slot(self, tmp_path: Path) -> None:
        ctx = _run_phase(tmp_path)
        tileset = ctx.bible.tilesets[STAGE]
        deep = next(
            s.index for s in tileset.slots if s.params.get("water_deep")
        )
        grid = np.array(
            [[EMPTY, EMPTY], [FLOOR, PLATFORM]], dtype=np.int8
        )
        level = _write_level(ctx, grid)
        assign_level_terrain(ctx, level)
        assert (_terrain(ctx, level) != deep).all()


class TestDeriveWaterDeep:
    def test_lip_removed_and_mean_recentered(self) -> None:
        from examples.platformer_pack.tileset import derive_water_deep

        img = Image.new("RGBA", (32, 32), (100, 110, 120, 255))
        for y in range(8):  # the painted surface lip: a bright top band
            for x in range(32):
                img.putpixel((x, y), (240, 245, 250, 255))
        out = derive_water_deep(img)
        top = np.asarray(out)[:8, :, :3]
        assert top.max() < 200, "surface lip survived the derive"
        src_mean = np.asarray(img)[..., :3].mean(axis=(0, 1))
        out_mean = np.asarray(out)[..., :3].mean(axis=(0, 1))
        assert (abs(src_mean - out_mean) <= 1.5).all(), (src_mean, out_mean)

    def test_deterministic(self) -> None:
        from examples.platformer_pack.tileset import derive_water_deep

        img = Image.new("RGBA", (16, 16))
        img.putdata(
            [
                (40 + (x * 7 + y * 13) % 160, 60, 90, 255)
                for y in range(16)
                for x in range(16)
            ]
        )
        a = np.asarray(derive_water_deep(img))
        b = np.asarray(derive_water_deep(img))
        assert (a == b).all()

    def test_flat_square_passes_through_visually(self) -> None:
        from examples.platformer_pack.tileset import derive_water_deep

        flat = Image.new("RGBA", (32, 32), PLACEHOLDER_PALETTE["water"])
        out = derive_water_deep(flat)
        assert (np.asarray(out) == np.asarray(flat)).all()


class TestDeterminism:
    def test_two_runs_identical_sheet_manifest_and_terrain(
        self, tmp_path: Path
    ) -> None:
        grid = np.array(
            [
                [EMPTY, EMPTY, EMPTY],
                [FLOOR, FLOOR, WATER],
                [FLOOR, SPIKE, FLOOR],
            ],
            dtype=np.int8,
        )
        outputs = []
        for run in ("a", "b"):
            root = tmp_path / run
            ctx = _run_phase(root)
            level = _write_level(ctx, grid)
            assign_level_terrain(ctx, level)
            outputs.append(
                (
                    (root / f"tileset/{STAGE}/tilesheet.png").read_bytes(),
                    (root / f"tileset/{STAGE}/manifest.json").read_bytes(),
                    (root / f"tileset/{STAGE}/autotile.json").read_bytes(),
                    _terrain(ctx, level),
                    level.terrain_hash,
                )
            )
        first, second = outputs
        assert first[0] == second[0]
        assert first[1] == second[1]
        assert first[2] == second[2]
        assert (first[3] == second[3]).all()
        assert first[4] == second[4]

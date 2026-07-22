"""Art-template lanes (graphics arc G1): the style-guide-as-schema.

A lane is a full GraphicsSpec JSON — structured aesthetic tokens, the
base_cell ART-density root (physics untouched), derived pixel sizes.
Swapping --graphics IS the restyle."""

from __future__ import annotations

from pathlib import Path

from examples.platformer_pack.graphics import GraphicsSpec, load_graphics

SPECS_DIR = Path("examples/graphics_specs")

LANES = ("hand_drawn_16bit", "prerendered_16bit", "modern_hd")


class TestLaneTemplates:
    def test_all_three_lanes_load_and_cohere(self) -> None:
        for name in LANES:
            spec = load_graphics(SPECS_DIR / f"{name}.json")
            assert spec.lane == name
            assert spec.aesthetic_tokens, name
            # Density coherence: tile_px IS base_cell * cells_per_tile.
            assert spec.tile_px == spec.base_cell * spec.cells_per_tile
            # Lane files derive their style from tokens (no override).
            assert spec.art_style == ""
            assert spec.effective_art_style() == ", ".join(
                spec.aesthetic_tokens
            )

    def test_lanes_are_distinct_prompts(self) -> None:
        styles = {
            load_graphics(SPECS_DIR / f"{n}.json").effective_art_style()
            for n in LANES
        }
        assert len(styles) == 3

    def test_legacy_specs_back_derive_base_cell(self) -> None:
        """Pre-lane specs (tile_px only) stay loadable — base_cell is
        reconciled toward tile_px, never a validation error."""
        snes = load_graphics(SPECS_DIR / "snes_pixel.json")
        assert snes.tile_px == 16
        assert snes.base_cell * snes.cells_per_tile == 16
        hd = load_graphics(SPECS_DIR / "rendered_hd.json")
        assert hd.tile_px == 128
        assert hd.base_cell * hd.cells_per_tile == 128
        # The free-text override still wins for legacy specs.
        assert hd.art_style
        assert hd.effective_art_style() == hd.art_style

    def test_default_spec_unchanged_density(self) -> None:
        spec = GraphicsSpec()
        assert spec.tile_px == 32
        assert (spec.base_cell, spec.cells_per_tile) == (16, 2)
        assert spec.effective_art_style() == spec.art_style  # override set

    def test_art_footprints_scale_in_whole_cells(self) -> None:
        spec = GraphicsSpec()
        assert spec.art_footprint(1.0) == (2, 2)
        assert spec.art_footprint(1.5) == (3, 3)
        assert spec.art_footprint(2.0) == (4, 4)
        assert spec.player_footprint == (4, 4)

    def test_user_only_art_fields_default_off(self) -> None:
        from canon.bible.platformer import EnemyDefinition, PlayerDefinition

        e = EnemyDefinition(enemy_id="x", artifact_id="enemy:x")
        assert e.asymmetric is False and e.palette_override == []
        p = PlayerDefinition(artifact_id="player")
        assert p.asymmetric is False


class TestLaneSwapEndToEnd:
    def test_lane_swap_restyles_the_art_tree(self, tmp_path: Path) -> None:
        """--graphics <lane> IS the restyle: the same seed under two
        lanes differs in the art-derived files (sheet geometry/bytes,
        backdrop band count, manifest graphics block) while the
        GAMEPLAY layers stay byte-identical (collision/entities)."""
        import json
        import random

        from canon import CanonConfig, FakeLLMBackend, LLMClient, run_pipeline
        from canon.bible.models import Bible
        from canon.pipeline.runner import PipelineContext
        from examples.platformer_pack import PlatformerPrompts, compose_pipeline
        from examples.platformer_pack.tileset_art import build_image_producer
        from examples.run_platformer_slice import make_fake_responder

        trees = {}
        for lane in ("hand_drawn_16bit", "modern_hd"):
            out = tmp_path / lane
            ctx = PipelineContext(
                bible=Bible.empty(seed="lane_swap"),
                config=CanonConfig(seed="lane_swap", output_dir=out),
                rng=random.Random("lane_swap"),
                llm=LLMClient(FakeLLMBackend(make_fake_responder())),
                prompts=PlatformerPrompts(),
            )
            run_pipeline(
                compose_pipeline(
                    num_stages=1,
                    graphics=load_graphics(SPECS_DIR / f"{lane}.json"),
                    image_producer=build_image_producer("fake", None),
                ),
                ctx,
            )
            trees[lane] = out

        a, b = trees["hand_drawn_16bit"], trees["modern_hd"]
        stage = next((a / "tileset").iterdir()).name
        sheet_a = (a / "tileset" / stage / "tilesheet.png").read_bytes()
        sheet_b = (b / "tileset" / stage / "tilesheet.png").read_bytes()
        assert sheet_a != sheet_b  # density/filter/posterize restyled
        bands_a = len(list((a / "backdrop" / stage).glob("band_*.png")))
        bands_b = len(list((b / "backdrop" / stage).glob("band_*.png")))
        # Lane's backdrop_bands (2 vs 3); the foreground occluder is an
        # opt-in curated layer the lane templates deliberately don't pin.
        assert (bands_a, bands_b) == (2, 3)
        ga = json.loads((a / "manifest.json").read_text())["graphics"]
        gb = json.loads((b / "manifest.json").read_text())["graphics"]
        assert ga["lane"] == "hand_drawn_16bit" and gb["lane"] == "modern_hd"
        assert ga["tile_px"] == 32 and gb["tile_px"] == 64
        # Gameplay is lane-independent: identical collision bytes.
        col = "level/" + stage + "/l1/collision.npz"
        assert (a / col).read_bytes() == (b / col).read_bytes()

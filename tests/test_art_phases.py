"""Art-track phase tests — late art nodes (tileset repaint, sprites,
backdrops), stage effects, and the skinned review render.

Everything runs on FakeImageBackend per the canned-fake rule. The sprite
fixture is a white canvas with a centered colored blob so background
removal has a real subject to keep.
"""

from __future__ import annotations

import io
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402

from canon.backends.testing import (  # noqa: E402
    FakeImageBackend,
    FakeLLMBackend,
    FakeVLMBackend,
)
from canon.bible.models import Bible  # noqa: E402
from canon.config import CanonConfig  # noqa: E402
from canon.llm.client import LLMClient  # noqa: E402
from canon.packs.platformer import PlatformerPrompts, compose_pipeline  # noqa: E402
from canon.packs.platformer.effects import sanitize_effects  # noqa: E402
from canon.packs.platformer.graphics import DEFAULT_GRAPHICS  # noqa: E402
from canon.packs.platformer.run_slice import make_fake_responder  # noqa: E402
from canon.packs.platformer.tileset_art import (  # noqa: E402
    _CUTOUT_CATEGORIES,
    DiffusionSheetProducer,
    _bottom_align,
    build_sheet_reference,
    conform_to_palette,
    frames_to_strip,
    normalize_frames,
    remove_background,
    segment_frames,
)
from canon.packs.platformer.vlm_qa import (  # noqa: E402
    PLAYER_ANIMATION_STATES,
    STATE_LOOP_MODES,
    enemy_animation_states,
    make_fake_vlm_responder,
)
from canon.pipeline.runner import PipelineContext, run_pipeline  # noqa: E402

SEED = "emberfall_001"
STAGE = "ashen_depths"


def _blob_png(size: int = 64) -> bytes:
    """White canvas, centered crimson blob — a removable background plus
    a keepable subject."""
    img = Image.new("RGB", (size, size), (255, 255, 255))
    for y in range(size // 4, 3 * size // 4):
        for x in range(size // 4, 3 * size // 4):
            img.putpixel((x, y), (180, 40, 40))
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return buffer.getvalue()


def _producer(tmp_path: Path) -> DiffusionSheetProducer:
    path = tmp_path / "blob.png"
    if not path.exists():
        path.write_bytes(_blob_png())
    return DiffusionSheetProducer(FakeImageBackend(placeholder=path))


def _run(output_dir: Path, image_producer=None) -> PipelineContext:
    ctx = PipelineContext(
        bible=Bible.empty(seed=SEED),
        config=CanonConfig(seed=SEED, output_dir=output_dir),
        rng=random.Random(SEED),
        llm=LLMClient(FakeLLMBackend(make_fake_responder())),
        prompts=PlatformerPrompts(),
    )
    run_pipeline(compose_pipeline(image_producer=image_producer), ctx)
    return ctx


class TestRemoveBackground:
    def test_border_cut_subject_kept(self) -> None:
        img = Image.open(io.BytesIO(_blob_png()))
        out = remove_background(img)
        assert out.getpixel((1, 1))[3] == 0  # border transparent
        assert out.getpixel((32, 32))[3] == 255  # blob opaque

    def test_only_hazards_are_cut_out(self) -> None:
        # Only object-like tiles get their backdrop keyed; fill tiles must
        # NOT be cut (that would punch holes in seamless terrain).
        assert "hazard" in _CUTOUT_CATEGORIES
        assert not (_CUTOUT_CATEGORIES & {"solid", "one_way", "volume", "empty"})

    def test_conform_preserves_alpha_and_recolors_only_visible(self) -> None:
        # A hazard drawn as an object on a backdrop (the 'spike yellow box'
        # playtest bug): once the backdrop is cut, conform must leave it
        # transparent AND land the VISIBLE pixels on the role hex — not the
        # discarded backdrop.
        img = Image.new("RGB", (64, 64), (230, 220, 120))  # yellow backdrop
        px = img.load()
        for y in range(20, 60):
            for x in range(28, 36):
                px[x, y] = (120, 30, 25)  # the spike body
        keyed = remove_background(img)
        out = conform_to_palette(keyed, "#d42818", levels=None)
        assert out.mode == "RGBA"
        assert out.getpixel((1, 1))[3] == 0  # backdrop stays transparent
        opaque = [p for p in out.get_flattened_data() if p[3] > 0]
        assert opaque, "the subject must survive the cut"
        n = len(opaque)
        mean = tuple(sum(p[i] for p in opaque) // n for i in range(3))
        # #d42818 == (212, 40, 24); the visible mean must land on it, well
        # inside the QA palette tolerance (48).
        dist = sum((a - b) ** 2 for a, b in zip(mean, (212, 40, 24))) ** 0.5
        assert dist < 48

    def test_conform_opaque_fill_stays_fully_opaque(self) -> None:
        # An RGB fill tile (no backdrop) must come out fully opaque — the
        # alpha support must not change fill-tile behavior.
        fill = Image.new("RGB", (32, 32), (90, 140, 60))
        out = conform_to_palette(fill, "#5a8c3c", levels=None)
        assert out.mode == "RGBA"
        assert all(p[3] == 255 for p in out.get_flattened_data())

    def test_conform_lands_the_qa_rgb_mean_exactly(self) -> None:
        """The recurring paid-run spike failure (three runs straight): the
        HSV shifts land the H/S/V means but the QA code-check scores the
        ARITHMETIC RGB mean — a nonlinear map, so hue-distant generations
        still failed by 85-155 vs tolerance 48. The final RGB recenter must
        land the exact metric vlm_qa uses, for both a hue-distant textured
        tile and a posterized one."""
        import random

        from canon.packs.platformer.vlm_qa import _mean_rgb

        rng = random.Random(7)
        # A textured purple-ish tile aimed at a hot red (the real spike
        # case: mean #644393 vs #c43a1a, distance 155).
        img = Image.new("RGB", (32, 32))
        img.putdata(
            [
                (
                    100 + rng.randrange(60),
                    40 + rng.randrange(60),
                    120 + rng.randrange(80),
                )
                for _ in range(32 * 32)
            ]
        )
        for levels in (None, 5):
            out = conform_to_palette(img, "#c43a1a", levels=levels)
            mean = _mean_rgb(out)
            dist = sum(
                (a - b) ** 2 for a, b in zip(mean, (0xC4, 0x3A, 0x1A))
            ) ** 0.5
            assert dist <= 3, (
                f"levels={levels}: QA-metric distance {dist:.1f} — the "
                "recenter must land the arithmetic RGB mean"
            )

    def test_bottom_align_seats_feet_on_the_frame_bottom(self) -> None:
        # Generated sprites frame the creature with empty space below it, and
        # the consumers bottom-anchor the frame to the floor — so enemies
        # HOVERED (plat_kingdom3). Bottom-align must drop the content so its
        # lowest opaque row is the frame's last row.
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        for y in range(10, 31):  # a blob floating in the upper-middle
            for x in range(24, 40):
                img.putpixel((x, y), (200, 50, 50, 255))
        out = _bottom_align(img)
        alpha = out.getchannel("A")
        assert alpha.getbbox()[3] == out.height  # content reaches the bottom
        # x-centered and no taller than before (content preserved, not scaled).
        assert out.size == img.size
        # A fully-transparent frame (empty fake) is returned untouched.
        empty = Image.new("RGBA", (8, 8), (0, 0, 0, 0))
        assert _bottom_align(empty).getchannel("A").getbbox() is None


class TestArtDescriptors:
    def test_sentry_sprite_reads_as_stationary(self) -> None:
        from canon.packs.platformer.art_phases import _enemy_art_descriptor

        d = _enemy_art_descriptor("sentry", "a plump spring-legged guardian")
        assert "planted" in d and "immobile" in d
        assert "a plump spring-legged guardian" in d
        # A patroller reads as moving, never planted.
        p = _enemy_art_descriptor("patroller", "a round critter")
        assert "walk" in p and "planted" not in p

    def test_player_is_a_weaponless_mascot(self) -> None:
        from canon.packs.platformer.art_phases import PLAYER_DESCRIPTOR

        low = PLAYER_DESCRIPTOR.lower()
        assert "mascot" in low
        assert "not a knight" in low
        assert "weapon" in low
        # 'heroic' is the term that pulled the generator toward armored knights.
        assert "heroic" not in low


class TestSpriteArt:
    def test_sprites_written_and_stamped(self, tmp_path: Path) -> None:
        ctx = _run(tmp_path / "out", _producer(tmp_path))
        for enemy_id, enemy in ctx.bible.enemy_definitions.items():
            assert enemy.sprite_path == f"sprite/enemy/{enemy_id}/base.png"
            assert enemy.sprite_hash.startswith("sha256:")
            sprite_file = tmp_path / "out" / enemy.sprite_path
            assert sprite_file.exists()
            # The enemy JSON on disk carries the sprite reference too.
            spec = json.loads(
                (tmp_path / "out" / f"enemy/{enemy_id}.json").read_text()
            )
            assert spec["sprite_path"] == enemy.sprite_path
        assert (tmp_path / "out" / "sprite/player/base.png").exists()

    def test_empty_generation_falls_back_loudly(self, tmp_path: Path) -> None:
        # Default FakeImageBackend = 1×1 transparent → background removal
        # leaves nothing → warned, sprite_path stays empty, rects survive.
        ctx = _run(tmp_path / "out", DiffusionSheetProducer(FakeImageBackend()))
        warnings = [
            w
            for w in ctx.artifacts.get("slice_warnings", [])
            if w.startswith("sprite art:")
        ]
        # + the item pool + player + the 4 gameplay props (checkpoint
        # flag + dust/splash/sparkle VFX — the exit goal was removed, T3)
        assert len(warnings) == (
            len(ctx.bible.enemy_definitions) + len(ctx.bible.items) + 5
        )
        for enemy in ctx.bible.enemy_definitions.values():
            assert enemy.sprite_path == ""
        # No prop generated → no artifact, and the manifest block is
        # empty — consumers draw their placeholder shapes.
        assert ctx.bible.props == {}
        manifest = json.loads((tmp_path / "out" / "manifest.json").read_text())
        assert manifest["props"] == {STAGE: {}}

    def test_prop_sprites_written_and_stamped(self, tmp_path: Path) -> None:
        ctx = _run(tmp_path / "out", _producer(tmp_path))
        props = ctx.bible.props[STAGE]
        assert props.artifact_id == f"props:{STAGE}"
        # Gameplay props + the one-shot VFX pool (dust/splash/sparkle).
        assert sorted(props.prop_paths) == [
            "checkpoint", "dust", "sparkle", "splash",
        ]
        for name, rel in props.prop_paths.items():
            assert rel == f"sprite/prop/{STAGE}/{name}.png"
            assert (tmp_path / "out" / rel).exists()
            assert props.prop_hashes[rel].startswith("sha256:")
        assert props.provenance_hash
        manifest = json.loads((tmp_path / "out" / "manifest.json").read_text())
        assert manifest["props"][STAGE] == props.prop_paths

    def test_pinned_props_survive_a_sprite_reroll(self, tmp_path: Path) -> None:
        """The per-asset pin guard: props:<stage> pinned → a sprite_art
        re-run with a DIFFERENT producer leaves the prop bytes intact."""
        from canon.packs.platformer.art_phases import SpriteArtPhase

        ctx = _run(tmp_path / "out", _producer(tmp_path))
        rels = list(ctx.bible.props[STAGE].prop_paths.values())
        before = {rel: (tmp_path / "out" / rel).read_bytes() for rel in rels}

        ctx.bible.metadata.pinned.append(f"props:{STAGE}")
        blue = Image.new("RGB", (64, 64), (255, 255, 255))
        for y in range(16, 48):
            for x in range(16, 48):
                blue.putpixel((x, y), (40, 60, 200))
        buffer = io.BytesIO()
        blue.save(buffer, format="PNG")
        blue_path = tmp_path / "blue.png"
        blue_path.write_bytes(buffer.getvalue())
        SpriteArtPhase(
            producer=DiffusionSheetProducer(FakeImageBackend(placeholder=blue_path))
        ).run(ctx)

        for rel, data in before.items():
            assert (tmp_path / "out" / rel).read_bytes() == data

    def test_backend_failure_falls_back_loudly(self, tmp_path: Path) -> None:
        class ExplodingBackend:
            model = "exploding"

            def generate(self, prompt: str, width: int, height: int) -> bytes:
                raise RuntimeError("boom")

        ctx = _run(tmp_path / "out", DiffusionSheetProducer(ExplodingBackend()))
        assert any(
            w.startswith("sprite art: generation failed")
            for w in ctx.artifacts.get("slice_warnings", [])
        )


class TestColorlessSpriteTint:
    def test_gray_sprite_is_tinted_and_warned(self, tmp_path: Path) -> None:
        """A colorless sprite bypassed the dominant-hue check entirely
        (the pale skeletal hound) — now it's warned AND tinted to its
        assigned hue with shading kept."""
        import colorsys

        gray = Image.new("RGB", (64, 64), (255, 255, 255))
        for y in range(16, 48):
            for x in range(16, 48):
                gray.putpixel((x, y), (140 + (x * 3) % 60,) * 3)
        buffer = io.BytesIO()
        gray.save(buffer, format="PNG")
        (tmp_path / "gray.png").write_bytes(buffer.getvalue())

        ctx = _run(
            tmp_path / "out",
            DiffusionSheetProducer(
                FakeImageBackend(placeholder=tmp_path / "gray.png")
            ),
        )
        tinted = [
            w
            for w in ctx.artifacts.get("slice_warnings", [])
            if "came back colorless; tinted" in w
        ]
        # Enemies AND the item pool ride the same tint fallback.
        assert len(tinted) == (
            len(ctx.bible.enemy_definitions) + len(ctx.bible.items)
        )
        # The saved sprite actually carries the assigned hue now.
        enemy_id, enemy = next(iter(ctx.bible.enemy_definitions.items()))
        sprite = Image.open(tmp_path / "out" / enemy.sprite_path).convert("RGBA")
        opaque = [p for p in sprite.get_flattened_data() if p[3] > 0]
        assert opaque
        r, g, b, _a = opaque[len(opaque) // 2]
        _h, s, _v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
        assert s > 0.3, "tint did not add saturation"


class TestBackdropArt:
    def test_bands_written_with_own_artifact(self, tmp_path: Path) -> None:
        """DEFAULT shape: scenery bands only — the foreground occluder is
        an opt-in curated layer (first paid run: the generated band
        blocked the playfield and repeated stage-wide), so nothing named
        band_fg and no depth > 1 without an explicit graphics opt-in."""
        ctx = _run(tmp_path / "out", _producer(tmp_path))
        backdrop = ctx.bible.backdrops[STAGE]
        assert backdrop.artifact_id == f"backdrop:{STAGE}"
        # DEFAULT_GRAPHICS.backdrop_bands scenery bands, far → near.
        assert len(backdrop.band_paths) == 2
        assert all(not rel.endswith("band_fg.png") for rel in backdrop.band_paths)
        assert backdrop.depths == [0.2, 0.5]
        assert "phase:plat:style" in backdrop.parents
        for rel in backdrop.band_paths:
            assert (tmp_path / "out" / rel).exists()
            assert backdrop.band_hashes[rel].startswith("sha256:")
        assert Image.open(tmp_path / "out" / backdrop.band_paths[0]).mode == "RGB"
        manifest = json.loads(
            (tmp_path / "out" / f"backdrop/{STAGE}/manifest.json").read_text()
        )
        assert manifest["band_paths"] == backdrop.band_paths

    def test_foreground_band_opt_in_appends_the_occluder(
        self, tmp_path: Path
    ) -> None:
        """graphics.foreground_band=True (a game that IS directing that
        layer) appends band_fg last with depth > 1 and REAL transparency
        (RGBA occluders consumers draw over the world; scenery stays RGB)."""
        from canon.packs.platformer.art_phases import (
            FOREGROUND_DEPTH,
            BackdropArtPhase,
        )

        ctx = _run(tmp_path / "out")  # placeholder tree: stages + palettes
        graphics = DEFAULT_GRAPHICS.model_copy(update={"foreground_band": True})
        BackdropArtPhase(producer=_producer(tmp_path), graphics=graphics).run(ctx)
        backdrop = ctx.bible.backdrops[STAGE]
        assert len(backdrop.band_paths) == 3
        assert backdrop.band_paths[-1] == f"backdrop/{STAGE}/band_fg.png"
        assert backdrop.depths == [0.2, 0.5, FOREGROUND_DEPTH]
        assert FOREGROUND_DEPTH > 1.0  # the consumer draw-in-front contract
        fg = Image.open(tmp_path / "out" / backdrop.band_paths[-1])
        assert fg.mode == "RGBA"
        assert fg.getchannel("A").getextrema()[0] == 0
        # Readable provenance rides the opt-in write like any band.
        assert fg.text["canon:asset"] == f"band:{STAGE}/fg"

    def test_band_edit_marks_backdrop_only(self, tmp_path: Path) -> None:
        """A hand-tweaked band PNG must mark backdrop:<stage> user-edited
        WITHOUT cascading staleness into the stage's levels — that's the
        whole reason Backdrop is its own artifact."""
        from canon.pipeline.orchestrator import detect_edits

        ctx = _run(tmp_path / "out", _producer(tmp_path))
        band = tmp_path / "out" / ctx.bible.backdrops[STAGE].band_paths[0]
        band.write_bytes(_blob_png(16))
        report = detect_edits(ctx.bible, tmp_path / "out")
        assert report.user_edited == [f"backdrop:{STAGE}"]
        assert not any(nid.startswith("level:") for nid in report.stale)
        # Adopted into the dict-keyed hash field: next pass is clean.
        report = detect_edits(ctx.bible, tmp_path / "out")
        assert report.user_edited == []


class TestTilesetRepaintVariants:
    def test_one_generation_per_name_variants_shaded_mean_preserved(
        self, tmp_path: Path
    ) -> None:
        """16 autotile floor slots must cost ONE floor generation (paid
        runs stay at the unique-name count) — the shared art is pasted
        into every slot region and each masked slot is shaded via
        shade_floor_variant with the region mean preserved EXACTLY
        (palette conformance + every region-average consumer depend on
        it)."""
        from canon.packs.platformer import tileset as tileset_mod

        if not hasattr(tileset_mod, "shade_floor_variant"):
            pytest.skip("tileset.shade_floor_variant not landed yet (G5 autotile)")
        from canon.bible.platformer import Tileset, TileSlot
        from canon.packs.platformer.art_phases import TilesetArtPhase

        ctx = _run(tmp_path / "out")  # placeholder tree: stages + palette
        tile_px = DEFAULT_GRAPHICS.tile_px
        slots: list[TileSlot] = []

        def add(name: str, tile_type: int, collision: str, params: dict) -> None:
            slots.append(
                TileSlot(
                    index=len(slots), tile_type=tile_type, name=name,
                    px_region=(len(slots) * tile_px, 0, tile_px, tile_px),
                    collision=collision, params=params,
                )
            )

        add("empty", 0, "empty", {})
        for mask in range(16):
            add("floor", 1, "solid", {"autotile_mask": mask})
        add("platform", 2, "one_way", {})
        ctx.bible.tilesets[STAGE] = Tileset(
            artifact_id=f"tileset:{STAGE}",
            stage_id=STAGE,
            tilesheet_path=f"tileset/{STAGE}/tilesheet.png",
            slots=slots,
            palette=dict(ctx.bible.tilesets[STAGE].palette),
        )

        calls: list[str] = []
        base = _producer(tmp_path)

        class CountingProducer:
            model = "counting-fake"

            def tile_image(self, tile, role_hex, theme, world_title, graphics):
                calls.append(tile.name)
                return base.tile_image(
                    tile, role_hex, theme, world_title, graphics
                )

        TilesetArtPhase(producer=CountingProducer()).run(ctx)
        assert sorted(calls) == ["empty", "floor", "platform"]  # once per NAME

        sheet = Image.open(tmp_path / "out" / f"tileset/{STAGE}/tilesheet.png")

        def region(i: int) -> Image.Image:
            return sheet.crop(
                (i * tile_px, 0, (i + 1) * tile_px, tile_px)
            ).convert("RGB")

        def channel_sums(img: Image.Image) -> tuple[int, ...]:
            px = list(img.get_flattened_data())
            return tuple(sum(p[c] for p in px) for c in range(3))

        base_sums = channel_sums(region(1))  # the mask-0 floor slot
        shaded_any = False
        for mask in range(16):
            reg = region(1 + mask)
            # Mean preserved EXACTLY: equal per-channel pixel sums.
            assert channel_sums(reg) == base_sums, f"mask {mask} moved the mean"
            if reg.tobytes() != region(1).tobytes():
                shaded_any = True
        assert shaded_any, "no variant differs from the mask-0 art"
        # On a mid-range flat square the shading is guaranteed visible.
        flat = Image.new("RGBA", (tile_px, tile_px), (119, 100, 89, 255))
        shaded = tileset_mod.shade_floor_variant(flat.copy(), 15)
        assert shaded.tobytes() != flat.tobytes()


class TestArtRunsAtTheEnd:
    def test_art_nodes_run_after_level_nodes(self, tmp_path: Path) -> None:
        """The user rule: paid art only after the levels validate. In the
        orchestrated DAG the art nodes' completion order sits strictly
        after every level step node."""
        from canon.packs.platformer.dag import run_orchestrated

        ctx = PipelineContext(
            bible=Bible.empty(seed=SEED),
            config=CanonConfig(seed=SEED, output_dir=tmp_path / "out"),
            rng=random.Random(SEED),
            llm=LLMClient(FakeLLMBackend(make_fake_responder())),
            prompts=PlatformerPrompts(),
        )
        report = run_orchestrated(
            ctx, persist_path=tmp_path / "out" / "bible.json",
            image_producer=_producer(tmp_path),
        )
        assert report.ok
        done = report.done
        last_level = max(
            i for i, nid in enumerate(done) if nid.startswith("level:")
        )
        for art in ("phase:plat:tileset_art", "phase:plat:sprite_art",
                    "phase:plat:backdrop_art", "phase:plat:world_art"):
            assert done.index(art) > last_level, art

    def test_regen_art_node_rerolls_art_only(self, tmp_path: Path) -> None:
        from canon.packs.platformer.dag import run_orchestrated
        from canon.pipeline.orchestrator import mark_stale

        out = tmp_path / "out"
        ctx = PipelineContext(
            bible=Bible.empty(seed=SEED),
            config=CanonConfig(seed=SEED, output_dir=out),
            rng=random.Random(SEED),
            llm=LLMClient(FakeLLMBackend(make_fake_responder())),
            prompts=PlatformerPrompts(),
        )
        run_orchestrated(
            ctx, persist_path=out / "bible.json",
            image_producer=_producer(tmp_path),
        )
        bible = Bible.load(out / "bible.json")
        mark_stale(bible, ["phase:plat:sprite_art"])
        bible.persist(out / "bible.json")

        ctx2 = PipelineContext(
            bible=Bible.load(out / "bible.json"),
            config=CanonConfig(seed=SEED, output_dir=out),
            rng=random.Random(SEED),
            llm=LLMClient(FakeLLMBackend(make_fake_responder())),
            prompts=PlatformerPrompts(),
        )
        report = run_orchestrated(
            ctx2, persist_path=out / "bible.json",
            image_producer=_producer(tmp_path),
        )
        assert "phase:plat:sprite_art" in report.done
        assert not any(nid.startswith("level:") for nid in report.done)


def _corner_pixels(path: Path) -> list[tuple[int, int, int]]:
    """The 4x4 watermark region (2px in from bottom-right), row-major RGB."""
    img = Image.open(path).convert("RGB")
    w, h = img.size
    return [
        img.getpixel((w - 6 + dx, h - 6 + dy))
        for dy in range(4)
        for dx in range(4)
    ]


def _expected_checker() -> list[tuple[int, int, int]]:
    from canon.packs.platformer.tileset_art import _WATERMARK_TONES

    return [
        _WATERMARK_TONES[(dx // 2 + dy // 2) % 2]
        for dy in range(4)
        for dx in range(4)
    ]


class TestReadableProvenance:
    """G6: every producer-generated PNG carries deterministic canon:*
    tEXt chunks — round-tripped through the phases and re-opened."""

    def test_sprite_and_band_text_chunks_round_trip(
        self, tmp_path: Path
    ) -> None:
        ctx = _run(tmp_path / "out", _producer(tmp_path))
        enemy_id, enemy = next(iter(ctx.bible.enemy_definitions.items()))
        sprite = Image.open(tmp_path / "out" / enemy.sprite_path)
        assert sprite.text["canon:generator"] == "canon-ai harness"
        assert sprite.text["canon:asset"] == f"sprite:enemy/{enemy_id}"
        assert sprite.text["canon:gfx"] == DEFAULT_GRAPHICS.digest()
        assert sprite.text["canon:seed"] == SEED
        assert sprite.text["canon:image-seed"] == ""  # fake: no seed knob
        player = Image.open(tmp_path / "out" / "sprite/player/base.png")
        assert player.text["canon:asset"] == "sprite:player"
        band = Image.open(
            tmp_path / "out" / ctx.bible.backdrops[STAGE].band_paths[0]
        )
        assert band.text["canon:asset"] == f"band:{STAGE}/0"
        # (the OPT-IN foreground band's "band:<stage>/fg" chunk is
        # asserted in TestBackdropArt's opt-in test — off by default)
        prop_rel = ctx.bible.props[STAGE].prop_paths["checkpoint"]
        prop = Image.open(tmp_path / "out" / prop_rel)
        assert prop.text["canon:asset"] == f"sprite:prop/{STAGE}/checkpoint"

    def test_same_pipeline_write_twice_is_byte_identical(
        self, tmp_path: Path
    ) -> None:
        # The stamp is a pure function of its inputs — two identical runs
        # write identical stamped bytes (the tree-diff contract).
        a = _run(tmp_path / "a", _producer(tmp_path))
        _run(tmp_path / "b", _producer(tmp_path))
        enemy = next(iter(a.bible.enemy_definitions.values()))
        for rel in (
            enemy.sprite_path,
            a.bible.backdrops[STAGE].band_paths[0],
            "splash/world.png",
        ):
            assert (tmp_path / "a" / rel).read_bytes() == (
                tmp_path / "b" / rel
            ).read_bytes(), rel

    def test_animation_assets_carry_provenance(self, tmp_path: Path) -> None:
        out = tmp_path / "out"
        ctx = _run_animated(out, tmp_path)
        enemy_id = sorted(ctx.bible.enemy_definitions)[0]
        d = out / "sprite" / "enemy" / enemy_id
        walk = Image.open(d / "walk.png")
        assert walk.text["canon:asset"] == f"strip:enemy/{enemy_id}/walk"
        assert walk.text["canon:generator"] == "canon-ai harness"
        atlas = Image.open(d / "atlas.png")
        assert atlas.text["canon:asset"] == f"atlas:enemy/{enemy_id}"
        player_atlas = Image.open(out / "sprite/player/atlas.png")
        assert player_atlas.text["canon:asset"] == "atlas:player"


class TestVisibleWatermark:
    def test_on_marks_sprites_bands_splash_never_tiles(
        self, tmp_path: Path
    ) -> None:
        from canon.packs.platformer.art_phases import (
            BackdropArtPhase,
            SpriteArtPhase,
            TilesetArtPhase,
            WorldArtPhase,
        )

        ctx = _run(tmp_path / "out")  # placeholder tree: stages + palettes
        wm = DEFAULT_GRAPHICS.model_copy(update={"visible_watermark": True})
        producer = _producer(tmp_path)
        SpriteArtPhase(producer=producer, graphics=wm).run(ctx)
        BackdropArtPhase(producer=producer, graphics=wm).run(ctx)
        WorldArtPhase(producer=producer, graphics=wm).run(ctx)
        TilesetArtPhase(producer=producer, graphics=wm).run(ctx)

        expected = _expected_checker()
        out = tmp_path / "out"
        enemy = next(iter(ctx.bible.enemy_definitions.values()))
        assert _corner_pixels(out / enemy.sprite_path) == expected
        assert (
            _corner_pixels(out / ctx.bible.backdrops[STAGE].band_paths[0])
            == expected
        )
        assert _corner_pixels(out / "splash/world.png") == expected
        # NEVER the tilesheet — its region means are palette-checked.
        assert (
            _corner_pixels(out / ctx.bible.tilesets[STAGE].tilesheet_path)
            != expected
        )

    def test_off_leaves_every_corner_unmarked(self, tmp_path: Path) -> None:
        # Default spec (visible_watermark=False): no checker anywhere —
        # the flag off is byte-identical to never having the feature.
        ctx = _run(tmp_path / "out", _producer(tmp_path))
        expected = _expected_checker()
        out = tmp_path / "out"
        enemy = next(iter(ctx.bible.enemy_definitions.values()))
        assert _corner_pixels(out / enemy.sprite_path) != expected
        assert (
            _corner_pixels(out / ctx.bible.backdrops[STAGE].band_paths[0])
            != expected
        )
        assert _corner_pixels(out / "splash/world.png") != expected


class TestWorldArt:
    def test_splash_written_with_world_fields_and_manifest_key(
        self, tmp_path: Path
    ) -> None:
        ctx = _run(tmp_path / "out", _producer(tmp_path))
        world = ctx.bible.world
        assert world.splash_path == "splash/world.png"
        assert world.splash_hash.startswith("sha256:")
        splash = Image.open(tmp_path / "out" / world.splash_path)
        assert splash.mode == "RGB"
        assert splash.text["canon:asset"] == "splash:world"
        assert splash.text["canon:generator"] == "canon-ai harness"
        # world.json re-written with the splash reference; provenance
        # folds the image generators in.
        doc = json.loads((tmp_path / "out" / "world.json").read_text())
        assert doc["splash_path"] == world.splash_path
        assert doc["splash_hash"] == world.splash_hash
        assert world.provenance_hash
        manifest = json.loads((tmp_path / "out" / "manifest.json").read_text())
        assert manifest["splash"] == "splash/world.png"

    def test_no_producer_keeps_empty_splash(self, tmp_path: Path) -> None:
        ctx = _run(tmp_path / "out")
        assert ctx.bible.world.splash_path == ""
        assert ctx.bible.world.splash_hash == ""
        assert not (tmp_path / "out" / "splash").exists()
        manifest = json.loads((tmp_path / "out" / "manifest.json").read_text())
        assert manifest["splash"] == ""

    def test_pinned_splash_keeps_splash_bytes(self, tmp_path: Path) -> None:
        # The card is LEAF art: its pin id is "splash", never "world"
        # (pinning the world id must not be required to protect a card).
        from canon.packs.platformer.art_phases import WorldArtPhase

        ctx = _run(tmp_path / "out", _producer(tmp_path))
        rel = ctx.bible.world.splash_path
        before = (tmp_path / "out" / rel).read_bytes()

        ctx.bible.metadata.pinned.append("splash")
        blue = Image.new("RGB", (64, 64), (40, 60, 200))
        buffer = io.BytesIO()
        blue.save(buffer, format="PNG")
        blue_path = tmp_path / "world_blue.png"
        blue_path.write_bytes(buffer.getvalue())
        WorldArtPhase(
            producer=DiffusionSheetProducer(
                FakeImageBackend(placeholder=blue_path)
            )
        ).run(ctx)
        assert (tmp_path / "out" / rel).read_bytes() == before

    def test_failed_generation_warns_and_keeps_title_card(
        self, tmp_path: Path
    ) -> None:
        class ExplodingBackend:
            model = "exploding"

            def generate(self, prompt: str, width: int, height: int) -> bytes:
                raise RuntimeError("boom")

        ctx = _run(tmp_path / "out", DiffusionSheetProducer(ExplodingBackend()))
        assert any(
            w.startswith("world art: splash generation failed")
            for w in ctx.artifacts.get("slice_warnings", [])
        )
        assert ctx.bible.world.splash_path == ""
        manifest = json.loads((tmp_path / "out" / "manifest.json").read_text())
        assert manifest["splash"] == ""


class TestStageEffects:
    def test_canned_run_carries_sanitized_effects(self, tmp_path: Path) -> None:
        ctx = _run(tmp_path / "out")
        effects = ctx.bible.stages[STAGE].effects
        assert effects and effects[0]["name"] == "particles_falling"
        params = effects[0]["params"]
        assert params["color"] == "#d8cfc4"
        stage_json = json.loads(
            (tmp_path / "out" / f"stage/{STAGE}/stage.json").read_text()
        )
        assert stage_json["effects"] == effects

    def test_vocabulary_prompt_advertises_ranges_and_units(self) -> None:
        """The first real run returned 0-1 normalized params because the
        prompt gave names without ranges — prompts carry constraints."""
        from canon.packs.platformer.effects import describe_vocabulary

        vocab = describe_vocabulary()
        assert "density 1-200" in vocab
        assert "speed 10-400 fall px/s" in vocab
        assert "color '#rrggbb'" in vocab
        # The lighting kinds (Godot-only interpreters) are rollable too.
        assert "canvas_tint(strength 0-1 0-1 blend" in vocab
        assert "player_light(radius 32-400 px, dim 0-1 0-1 darkness" in vocab
        assert "glow(intensity 0-2 bloom energy" in vocab

    def test_sanitize_clamps_and_carries_unknown(self) -> None:
        warnings: list[str] = []
        out = sanitize_effects(
            [
                {"name": "particles_falling",
                 "params": {"density": 9999, "speed": -5, "color": "nope"}},
                {"name": "aurora", "params": {"hue": 1}},  # future kind
                {"no_name": True},
            ],
            warn=warnings.append,
        )
        falling = out[0]["params"]
        assert falling["density"] == 200 and falling["speed"] == 10
        assert falling["color"] == "#e8e8f0"  # bad hex → readable default
        assert len(warnings) == 2  # two clamps
        assert out[1] == {"name": "aurora", "params": {"hue": 1}}  # inert
        assert len(out) == 2

    def test_sanitize_clamps_lighting_kinds(self) -> None:
        warnings: list[str] = []
        out = sanitize_effects(
            [
                {"name": "canvas_tint",
                 "params": {"strength": 5, "color": "#102030"}},
                {"name": "player_light", "params": {"radius": 9999, "dim": -2}},
                {"name": "glow", "params": {"intensity": 99}},
            ],
            warn=warnings.append,
        )
        assert out[0]["params"] == {"strength": 1.0, "color": "#102030"}
        assert out[1]["params"]["radius"] == 400
        assert out[1]["params"]["dim"] == 0
        assert out[1]["params"]["color"] == "#e8e8f0"  # color always appended
        assert out[2]["params"]["intensity"] == 2
        assert len(warnings) == 4  # every out-of-bounds param clamped loudly


class TestSkinnedRender:
    def test_skinned_png_written_beside_block_render(self, tmp_path: Path) -> None:
        ctx = _run(tmp_path / "out", _producer(tmp_path))
        for level_id in ctx.bible.stages[STAGE].level_ids:
            assert (tmp_path / "out" / f"review/{STAGE}/{level_id}.png").exists()
            skinned = tmp_path / "out" / f"review/{STAGE}/{level_id}_skinned.png"
            assert skinned.exists()
            image = Image.open(skinned)
            level = ctx.bible.levels[level_id]
            tile_px = ctx.bible.tilesets[STAGE].slots[0].px_region[2]
            assert image.size == (
                level.grid_width * tile_px, level.grid_height * tile_px,
            )


# ---------------------------------------------------------------------------
# Frame segmentation helpers (B3) — content-aware, robust to N
# ---------------------------------------------------------------------------


def _blob_sheet(n: int, w: int = 800, h: int = 200) -> Image.Image:
    """A white sheet of ``n`` evenly-spaced opaque blobs — a stand-in for a
    generated animation sheet."""
    img = Image.new("RGB", (w, h), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    cell = w // n
    for i in range(n):
        cx = i * cell + cell // 2
        draw.ellipse([cx - 40, h // 2 - 40, cx + 40, h // 2 + 40], fill=(40, 60, 200))
    return img


class TestFrameSegmentation:
    def test_recovers_actual_blob_count(self) -> None:
        # content-aware: whatever N the model drew, not what was asked for
        assert len(segment_frames(_blob_sheet(5))) == 5
        assert len(segment_frames(_blob_sheet(3))) == 3
        assert len(segment_frames(_blob_sheet(2))) == 2

    def test_normalize_makes_uniform_square_frames(self) -> None:
        frames = normalize_frames(segment_frames(_blob_sheet(4)))
        assert len(frames) == 4
        assert all(f.size == frames[0].size for f in frames)
        assert frames[0].width == frames[0].height  # square, like base.png

    def test_blank_sheet_yields_nothing(self) -> None:
        assert segment_frames(Image.new("RGB", (200, 80), (255, 255, 255))) == []
        assert normalize_frames([]) == []
        assert frames_to_strip([]) is None

    def test_thin_streak_is_rejected_as_an_artifact(self) -> None:
        """Real failure (player `fall`): the model left a full-height 3px
        streak beside the real poses. Its bbox is as tall as a frame but it has
        almost no area, so it survived segmentation and inflated the state's
        frame square — shrinking every real frame beside it."""
        sheet = _blob_sheet(3, w=800, h=200)
        draw = ImageDraw.Draw(sheet)
        draw.rectangle([770, 0, 773, 199], fill=(40, 60, 200))  # the streak
        crops = segment_frames(sheet)
        assert len(crops) == 3, [c.size for c in crops]
        assert all(c.width > 10 for c in crops)

    def test_a_two_frame_cycle_is_never_halved(self) -> None:
        # The area filter only runs with >2 crops, so a legitimate 2-frame
        # cycle with uneven poses keeps both frames.
        assert len(segment_frames(_blob_sheet(2))) == 2

    def test_strip_concatenates_frames(self) -> None:
        frames = normalize_frames(segment_frames(_blob_sheet(4)))
        strip = frames_to_strip(frames)
        assert strip.size == (frames[0].width * 4, frames[0].height)

    def test_reference_seeds_frame_one_in_wide_canvas(self) -> None:
        base = Image.new("RGBA", (32, 32), (0, 0, 0, 0))
        base.putpixel((16, 16), (255, 0, 0, 255))
        ref = build_sheet_reference(base, 4, 128)
        assert ref.size == (128 * 4, 128)  # n cells wide, one cell tall


# ---------------------------------------------------------------------------
# SpriteAnimationPhase (B3) — VLM-authored sheet animation
# ---------------------------------------------------------------------------


def _run_animated(output_dir: Path, tmp_path: Path) -> PipelineContext:
    """Full fake pipeline with placeholder-backed sprites (so SpriteArtPhase
    writes real base.png) AND a fake VLM judge (so B3 authors + generates)."""
    ctx = PipelineContext(
        bible=Bible.empty(seed=SEED),
        config=CanonConfig(seed=SEED, output_dir=output_dir),
        rng=random.Random(SEED),
        llm=LLMClient(FakeLLMBackend(make_fake_responder())),
        prompts=PlatformerPrompts(),
    )
    run_pipeline(
        compose_pipeline(
            image_producer=_producer(tmp_path),
            vlm_judge=FakeVLMBackend(make_fake_vlm_responder()),
        ),
        ctx,
    )
    return ctx


class TestSpriteAnimation:
    def test_sprite_drift_math(self) -> None:
        # Ticket 7: opaque-mean-RGB distance separates a RECOLOURED character
        # (palette shift, large) from the same character reposed (≈0).
        from canon.packs.platformer.art_phases import (
            SPRITE_DRIFT_MAX,
            _sprite_drift,
        )

        base = Image.new("RGBA", (16, 16), (200, 40, 40, 255))  # red mascot
        same = base.rotate(90)  # a new pose keeps the palette
        assert _sprite_drift(base, [same]) < 1.0
        orange = Image.new("RGBA", (16, 16), (230, 140, 30, 255))
        assert _sprite_drift(base, [orange]) > SPRITE_DRIFT_MAX

    def test_animation_edits_use_edit_backend_with_base_reference(
        self, tmp_path: Path
    ) -> None:
        # Ticket 7: the animation img2img runs on the (possibly separate)
        # edit_backend, and EVERY sheet edit re-attaches the clean base sprite
        # as an identity reference — "the animator gets the character sheet".
        sprite_backend = FakeImageBackend(placeholder=tmp_path / "blob.png")
        (tmp_path / "blob.png").write_bytes(_blob_png())
        edit_backend = FakeImageBackend()
        producer = DiffusionSheetProducer(sprite_backend, edit_backend)
        ctx = PipelineContext(
            bible=Bible.empty(seed=SEED),
            config=CanonConfig(seed=SEED, output_dir=tmp_path / "out"),
            rng=random.Random(SEED),
            llm=LLMClient(FakeLLMBackend(make_fake_responder())),
            prompts=PlatformerPrompts(),
        )
        run_pipeline(
            compose_pipeline(
                image_producer=producer,
                vlm_judge=FakeVLMBackend(make_fake_vlm_responder()),
            ),
            ctx,
        )
        edits = [c for c in edit_backend.calls if c.get("op") == "edit"]
        assert edits, "no animation edits reached the edit backend"
        assert all(c["references"] == 1 for c in edits)
        assert not any(c.get("op") == "edit" for c in sprite_backend.calls)

    def test_every_enemy_gets_per_state_strips_and_frames_json(
        self, tmp_path: Path
    ) -> None:
        out = tmp_path / "out"
        ctx = _run_animated(out, tmp_path)
        assert ctx.bible.enemy_definitions, "no enemies rolled"
        for enemy_id, enemy in ctx.bible.enemy_definitions.items():
            d = out / "sprite" / "enemy" / enemy_id
            assert (d / "base.png").exists()
            frames = json.loads((d / "frames.json").read_text())
            # archetype-aware set: the canned hopper carries a 5th jump state
            assert set(frames) == set(enemy_animation_states(enemy))
            for state, meta in frames.items():
                assert (d / f"{state}.png").exists()
                assert meta["frames"] >= 2
                assert meta["frame_width"] == meta["frame_height"]
                assert meta["duration_ms"] == DEFAULT_GRAPHICS.anim_frame_ms

    def test_strip_width_matches_frame_count(self, tmp_path: Path) -> None:
        out = tmp_path / "out"
        _run_animated(out, tmp_path)
        d = sorted((out / "sprite" / "enemy").glob("*"))[0]
        meta = json.loads((d / "frames.json").read_text())["walk"]
        assert Image.open(d / "walk.png").width == meta["frame_width"] * meta["frames"]

    def test_animation_manifest_persisted_on_enemy_stats(
        self, tmp_path: Path
    ) -> None:
        ctx = _run_animated(tmp_path / "out", tmp_path)
        enemy = next(iter(ctx.bible.enemy_definitions.values()))
        anim = enemy.stats.get("animation")
        assert anim and "spec" in anim and "states" in anim
        for meta in anim["states"].values():
            assert meta["hash"].startswith("sha256:")  # hashes stay in the Bible

    def test_frames_json_carries_loop_and_durations(self, tmp_path: Path) -> None:
        # G4 playback keys: every state gets its loop mode + a per-frame
        # duration list (uniform v1 — the user's hand-edit lever).
        out = tmp_path / "out"
        _run_animated(out, tmp_path)
        dirs = [out / "sprite" / "player",
                *sorted((out / "sprite" / "enemy").glob("*"))]
        for d in dirs:
            frames = json.loads((d / "frames.json").read_text())
            for state, meta in frames.items():
                assert meta["loop"] == STATE_LOOP_MODES[state]
                assert meta["durations_ms"] == (
                    [meta["duration_ms"]] * meta["frames"]
                )
        player = json.loads((out / "sprite/player/frames.json").read_text())
        assert set(player) == set(PLAYER_ANIMATION_STATES)
        assert player["jump"]["loop"] == "once"
        assert player["land"]["loop"] == "once"
        enemy = json.loads(
            (sorted((out / "sprite" / "enemy").glob("*"))[0] / "frames.json")
            .read_text()
        )
        assert enemy["hurt"]["loop"] == "once"
        assert enemy["death"]["loop"] == "once"

    def test_atlas_written_with_matching_state_counts(self, tmp_path: Path) -> None:
        # G4 shipping manifest: atlas.png + atlas.json beside frames.json,
        # every state at the same frame count, rects inside the atlas.
        out = tmp_path / "out"
        ctx = _run_animated(out, tmp_path)
        for d in [out / "sprite" / "player",
                  *sorted((out / "sprite" / "enemy").glob("*"))]:
            frames = json.loads((d / "frames.json").read_text())
            atlas_meta = json.loads((d / "atlas.json").read_text())
            atlas = Image.open(d / "atlas.png")
            assert atlas_meta["path"].endswith("atlas.png")
            size = frames["walk"]["frame_width"]
            assert atlas_meta["frame_size"] == [size, size]
            assert set(atlas_meta["states"]) == set(frames)
            for state, entry in atlas_meta["states"].items():
                assert len(entry["frames"]) == frames[state]["frames"]
                assert entry["loop"] == frames[state]["loop"]
                assert entry["durations_ms"] == frames[state]["durations_ms"]
                for rect in entry["frames"]:
                    assert rect["x"] >= 0 and rect["y"] >= 0
                    assert rect["x"] + rect["w"] <= atlas.width
                    assert rect["y"] + rect["h"] <= atlas.height
        # the Bible manifest carries the atlas artifact (path + hash)
        enemy = next(iter(ctx.bible.enemy_definitions.values()))
        assert enemy.stats["animation"]["atlas"]["hash"].startswith("sha256:")
        assert ctx.bible.player.animation["atlas"]["path"].endswith("atlas.png")

    def test_hopper_spec_and_manifest_include_jump(self, tmp_path: Path) -> None:
        ctx = _run_animated(tmp_path / "out", tmp_path)
        hopper = next(
            e for e in ctx.bible.enemy_definitions.values()
            if e.archetype == "hopper"
        )
        anim = hopper.stats["animation"]
        assert "jump" in anim["spec"]
        assert "jump" in anim["states"]
        # non-hoppers never author a jump sheet
        for enemy in ctx.bible.enemy_definitions.values():
            if enemy.archetype != "hopper":
                assert "jump" not in enemy.stats["animation"]["states"]

    def test_asymmetric_actor_gets_left_strips_and_atlas_rows(
        self, tmp_path: Path
    ) -> None:
        from canon.packs.platformer.art_phases import SpriteAnimationPhase

        out = tmp_path / "out"
        ctx = _run_animated(out, tmp_path)
        enemy_id = sorted(ctx.bible.enemy_definitions)[0]
        ctx.bible.enemy_definitions[enemy_id].asymmetric = True
        SpriteAnimationPhase(
            producer=_producer(tmp_path),
            judge=FakeVLMBackend(make_fake_vlm_responder()),
        ).run(ctx)

        d = out / "sprite" / "enemy" / enemy_id
        frames = json.loads((d / "frames.json").read_text())
        for state, meta in frames.items():
            assert (d / f"{state}_left.png").exists()
            assert meta["path_left"].endswith(f"{state}_left.png")
            assert meta["frames_left"] == meta["frames"]
            assert "hash_left" not in meta  # hashes stay in the Bible
        atlas_meta = json.loads((d / "atlas.json").read_text())
        for entry in atlas_meta["states"].values():
            assert len(entry["frames_left"]) == len(entry["frames"])
        anim = ctx.bible.enemy_definitions[enemy_id].stats["animation"]
        assert anim["states"]["walk"]["hash_left"].startswith("sha256:")
        # symmetric actors never grow the left keys
        other = json.loads(
            (out / "sprite" / "enemy" /
             sorted(ctx.bible.enemy_definitions)[1] / "frames.json").read_text()
        )
        assert all("path_left" not in m for m in other.values())
        player_atlas = json.loads(
            (out / "sprite/player/atlas.json").read_text()
        )
        assert all(
            "frames_left" not in e for e in player_atlas["states"].values()
        )

    def test_no_judge_keeps_sprites_static(self, tmp_path: Path) -> None:
        # loud fallback: producer present, judge absent → no motion authored
        out = tmp_path / "out"
        _run(out, _producer(tmp_path))  # compose WITHOUT vlm_judge
        d = sorted((out / "sprite" / "enemy").glob("*"))[0]
        assert (d / "base.png").exists()
        assert not (d / "frames.json").exists()
        assert not (d / "walk.png").exists()
        assert not (d / "atlas.json").exists()

    def test_spriteless_enemy_is_skipped(self, tmp_path: Path) -> None:
        # default FakeImageBackend (1×1) → empty sprites → nothing to animate
        out = tmp_path / "out"
        ctx = PipelineContext(
            bible=Bible.empty(seed=SEED),
            config=CanonConfig(seed=SEED, output_dir=out),
            rng=random.Random(SEED),
            llm=LLMClient(FakeLLMBackend(make_fake_responder())),
            prompts=PlatformerPrompts(),
        )
        run_pipeline(
            compose_pipeline(
                image_producer=DiffusionSheetProducer(FakeImageBackend()),
                vlm_judge=FakeVLMBackend(make_fake_vlm_responder()),
            ),
            ctx,
        )
        assert not list((out / "sprite" / "enemy").glob("*/frames.json"))
        for enemy in ctx.bible.enemy_definitions.values():
            assert "animation" not in enemy.stats


class TestAnimationQaEndToEnd:
    """B5 — with a placeholder-backed tree + fake judge, the VLM QA phase
    writes a global animation review report over the animated enemies."""

    def test_animation_qa_report_written_with_checks_and_verdicts(
        self, tmp_path: Path
    ) -> None:
        import json as _json

        out = tmp_path / "out"
        _run_animated(out, tmp_path)  # placeholder sprites + fake vlm judge
        report_path = out / "review" / "animation_qa.json"
        assert report_path.exists(), "animation QA report not written"
        report = _json.loads(report_path.read_text())
        assert report["vlm_model"] == "fake-vlm"
        assert report["actors"], "no actors reviewed"
        # both enemies (enemy:<id>) and the player are reviewed
        assert "player" in report["actors"]
        assert any(k.startswith("enemy:") for k in report["actors"])
        for entry in report["actors"].values():
            # code checks ran (strip + frames + the ticket-6 wander meter +
            # atlas) and passed on the fake frames
            checks = {c["check"] for c in entry["code_checks"]}
            assert {
                "animation_strip", "animation_frames",
                "animation_wander", "animation_atlas",
            } <= checks
            # every check EXCEPT the new sibling-silhouette detector passes on
            # the fake frames...
            assert all(
                c["passed"] for c in entry["code_checks"]
                if c["check"] != "animation_distinct"
            )
            # the fake judge's canned verdict passed all dimensions
            assert all(v["passed"] for v in entry["verdicts"].values())
        # ...and the detector (ticket 8) fires on the fake's canned near-dupe
        # states — the player has several states, so it must flag at least one
        # sibling pair (advisory: it warns, it does not gate the report).
        player_checks = report["actors"]["player"]["code_checks"]
        assert any(
            c["check"] == "animation_distinct" and not c["passed"]
            for c in player_checks
        )


# ---------------------------------------------------------------------------
# Cross-state scale consistency — the jump/fall "half the frame vanishes" bug
# ---------------------------------------------------------------------------


def _pose_sheet(n: int, blob_w: int, blob_h: int, cell: int) -> Image.Image:
    """A sheet of ``n`` identical rectangular 'poses' of a fixed content size,
    one per ``cell``-wide slot. Unlike ``_blob_sheet``'s circles, the pose's
    ASPECT is controllable — which is what the real bug needs: an actor whose
    idle silhouette is wide and whose jump silhouette is tall."""
    img = Image.new("RGB", (cell * n, cell), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    for i in range(n):
        cx = i * cell + cell // 2
        cy = cell // 2
        draw.rectangle(
            [cx - blob_w // 2, cy - blob_h // 2, cx + blob_w // 2, cy + blob_h // 2],
            fill=(40, 60, 200),
        )
    return img


class _PoseEditBackend:
    """Edit backend that draws a DIFFERENT-aspect pose per state, both at the
    same apparent scale, keyed off the state name in the prompt. Mimics the
    real failure: a squat idle and a taller (but not larger) jump tuck."""

    trusted_alpha = True
    model = "pose-stub"

    #: (content width, content height) in generation pixels — the jump pose is
    #: 1.35x the idle pose's height and slightly narrower, exactly as a tuck is.
    POSES = {"idle": (300, 200), "jump": (240, 270)}

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def edit(self, image_bytes, prompt, width, height, references=None) -> bytes:
        state = next((s for s in self.POSES if f" {s} animation" in prompt), "idle")
        bw, bh = self.POSES[state]
        n = max(1, width // height)
        buf = io.BytesIO()
        _pose_sheet(n, bw, bh, height).save(buf, format="PNG")
        self.calls.append({"op": "edit", "state": state})
        return buf.getvalue()


def _content_box(frame: Image.Image) -> tuple[int, int]:
    box = frame.convert("RGBA").getchannel("A").getbbox()
    return (0, 0) if box is None else (box[2] - box[0], box[3] - box[1])


class TestCrossStateScale:
    """The reported bug: 'the character clips and half of the frame vanishes in
    the jump'. Root cause — ``normalize_frames`` sized the frame square from
    ONE state's crops, so every state's largest dimension was stretched to fill
    the cell. An actor whose idle is wide and whose jump is tall therefore
    rendered at two different scales, and `fall`/`jump` play on every jump."""

    def _states(self, tmp_path: Path) -> dict[str, Image.Image]:
        from canon.packs.platformer.art_phases import SpriteAnimationPhase
        from canon.packs.platformer.tileset_art import DiffusionSheetProducer

        out = tmp_path / "out"
        ctx = PipelineContext(
            bible=Bible.empty(seed=SEED),
            config=CanonConfig(seed=SEED, output_dir=out),
            rng=random.Random(SEED),
            llm=LLMClient(FakeLLMBackend(make_fake_responder())),
            prompts=PlatformerPrompts(),
        )
        base_rel = "sprite/enemy/poser/base.png"
        base_abs = out / base_rel
        base_abs.parent.mkdir(parents=True, exist_ok=True)
        base = Image.new("RGBA", (32, 32), (0, 0, 0, 0))
        ImageDraw.Draw(base).rectangle([8, 12, 24, 28], fill=(40, 60, 200, 255))
        base.save(base_abs)

        phase = SpriteAnimationPhase(
            producer=DiffusionSheetProducer(_PoseEditBackend()),
            graphics=DEFAULT_GRAPHICS,
        )
        spec = {
            "idle": {"frames": 2, "motion": "sways"},
            "jump": {"frames": 2, "motion": "tucks"},
        }
        result = phase._animate_actor(ctx, base_rel, spec, "enemy:poser")
        assert set(result.get("states", {})) == {"idle", "jump"}, result
        return {
            st: Image.open(out / f"sprite/enemy/poser/{st}.png")
            for st in ("idle", "jump")
        }

    def test_states_keep_their_relative_proportions(self, tmp_path: Path) -> None:
        """The jump pose is 1.35x the idle pose's height in the SOURCE art, so
        it must be ~1.35x on disk too. Before the fix both states independently
        filled the cell, so this ratio collapsed to whatever each state's own
        aspect happened to be."""
        strips = self._states(tmp_path)
        fw = {st: img.height for st, img in strips.items()}  # square frames
        idle_w, idle_h = _content_box(strips["idle"].crop((0, 0, fw["idle"], fw["idle"])))
        jump_w, jump_h = _content_box(strips["jump"].crop((0, 0, fw["jump"], fw["jump"])))

        source_ratio = 270 / 200  # jump height / idle height, as drawn
        assert idle_h > 0 and jump_h > 0
        assert jump_h / idle_h == pytest.approx(source_ratio, rel=0.12), (
            f"jump/idle height ratio {jump_h / idle_h:.2f} should track the "
            f"source ratio {source_ratio:.2f} — idle {idle_w}x{idle_h}, "
            f"jump {jump_w}x{jump_h}"
        )

    def test_no_state_is_stretched_to_fill_the_cell(self, tmp_path: Path) -> None:
        """Only the actor's LARGEST pose may touch the cell edge. When every
        state maxes out its own cell, the character visibly changes size the
        instant its state changes — the reported defect."""
        strips = self._states(tmp_path)
        maxed = []
        for st, img in strips.items():
            side = img.height
            w, h = _content_box(img.crop((0, 0, side, side)))
            if max(w, h) >= side - 1:
                maxed.append(f"{st} ({w}x{h} in {side}px)")
        assert len(maxed) <= 1, f"more than one state fills its cell: {maxed}"


class TestRenormalizeRepair:
    """`canon asset animate --renormalize` — the free, backend-free reframe for
    art generated before the per-actor fix."""

    def _pack(self, tmp_path: Path) -> tuple:
        from canon.packs.platformer.art_phases import SpriteAnimationPhase
        from canon.packs.platformer.tileset_art import DiffusionSheetProducer

        out = tmp_path / "out"
        ctx = PipelineContext(
            bible=Bible.empty(seed=SEED),
            config=CanonConfig(seed=SEED, output_dir=out),
            rng=random.Random(SEED),
            llm=LLMClient(FakeLLMBackend(make_fake_responder())),
            prompts=PlatformerPrompts(),
        )
        base_rel = "sprite/enemy/poser/base.png"
        (out / base_rel).parent.mkdir(parents=True, exist_ok=True)
        base = Image.new("RGBA", (32, 32), (0, 0, 0, 0))
        ImageDraw.Draw(base).rectangle([8, 12, 24, 28], fill=(40, 60, 200, 255))
        base.save(out / base_rel)
        phase = SpriteAnimationPhase(
            producer=DiffusionSheetProducer(_PoseEditBackend()),
            graphics=DEFAULT_GRAPHICS,
        )
        phase._animate_actor(
            ctx,
            base_rel,
            {"idle": {"frames": 2, "motion": "s"}, "jump": {"frames": 2, "motion": "t"}},
            "enemy:poser",
        )
        return ctx, phase, out, base_rel

    def test_reframes_without_a_backend_and_keeps_playback_metadata(
        self, tmp_path: Path
    ) -> None:
        ctx, phase, out, base_rel = self._pack(tmp_path)
        d = out / "sprite/enemy/poser"
        before = json.loads((d / "frames.json").read_text())

        result = phase._renormalize_actor(ctx, base_rel, "enemy:poser")

        assert set(result["states"]) == {"idle", "jump"}
        after = json.loads((d / "frames.json").read_text())
        for state, meta in after.items():
            # Playback metadata rides through verbatim — a reframe re-seats
            # pixels, it never re-times or re-authors.
            assert meta["frames"] == before[state]["frames"]
            assert meta["loop"] == before[state]["loop"]
            assert meta["durations_ms"] == before[state]["durations_ms"]
        assert (d / "atlas.png").exists() and (d / "atlas.json").exists()

    def test_gives_every_state_headroom(self, tmp_path: Path) -> None:
        """The visible win: no state left flush against its cell edge."""
        ctx, phase, out, base_rel = self._pack(tmp_path)
        d = out / "sprite/enemy/poser"
        # Bake the real pre-fix pathology: every state's content flush against
        # its cell edge with zero headroom. (On the shipped packs this came from
        # per-state sizing at generation scale, where the 4px pad rounded away
        # in the downscale to 32; here we write that end state directly.)
        for state, (cw, ch) in (("idle", (32, 22)), ("jump", (26, 32))):
            n = json.loads((d / "frames.json").read_text())[state]["frames"]
            strip = Image.new("RGBA", (32 * n, 32), (0, 0, 0, 0))
            for i in range(n):
                ImageDraw.Draw(strip).rectangle(
                    [i * 32 + (32 - cw) // 2, 32 - ch,
                     i * 32 + (32 - cw) // 2 + cw - 1, 31],
                    fill=(40, 60, 200, 255),
                )
            strip.save(d / f"{state}.png")
        flush_before = [
            st for st in ("idle", "jump")
            if max(_content_box(Image.open(d / f"{st}.png").crop((0, 0, 32, 32)))) >= 31
        ]
        assert len(flush_before) == 2, "fixture did not reproduce the flush-to-edge state"

        phase._renormalize_actor(ctx, base_rel, "enemy:poser")

        for st in ("idle", "jump"):
            w, h = _content_box(Image.open(d / f"{st}.png").crop((0, 0, 32, 32)))
            assert max(w, h) < 32, f"{st} still fills its cell ({w}x{h})"

    def test_missing_frames_json_is_a_no_op(self, tmp_path: Path) -> None:
        ctx, phase, out, base_rel = self._pack(tmp_path)
        (out / "sprite/enemy/poser/frames.json").unlink()
        assert phase._renormalize_actor(ctx, base_rel, "enemy:poser") == {}

    def test_manifest_without_loop_falls_back_to_state_default(self, tmp_path: Path) -> None:
        """A frames.json that predates the playback keys (or a hand-edit that
        dropped ``loop``) rides through renormalize with ``loop: None``, so
        the writer's per-state fallback must resolve it. Regression: that
        fallback once raised NameError because ``STATE_LOOP_MODES`` was only
        imported inside the generation path."""
        ctx, phase, out, base_rel = self._pack(tmp_path)
        manifest = out / "sprite/enemy/poser/frames.json"
        stripped = {
            state: {k: v for k, v in meta.items() if k != "loop"}
            for state, meta in json.loads(manifest.read_text()).items()
        }
        assert all("loop" not in meta for meta in stripped.values())
        manifest.write_text(json.dumps(stripped))

        result = phase._renormalize_actor(ctx, base_rel, "enemy:poser")

        assert set(result["states"]) == {"idle", "jump"}
        after = json.loads(manifest.read_text())
        for state in ("idle", "jump"):
            assert after[state]["loop"] == STATE_LOOP_MODES.get(state, "loop")
            assert result["states"][state]["loop"] == after[state]["loop"]

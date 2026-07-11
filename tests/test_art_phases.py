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

from PIL import Image, ImageDraw  # noqa: E402

from canon.backends.testing import (  # noqa: E402
    FakeImageBackend,
    FakeLLMBackend,
    FakeVLMBackend,
)
from canon.bible.models import Bible  # noqa: E402
from canon.config import CanonConfig  # noqa: E402
from canon.llm.client import LLMClient  # noqa: E402
from canon.pipeline.runner import PipelineContext, run_pipeline  # noqa: E402
from examples.platformer_pack import PlatformerPrompts, compose_pipeline  # noqa: E402
from examples.platformer_pack.effects import sanitize_effects  # noqa: E402
from examples.platformer_pack.graphics import DEFAULT_GRAPHICS  # noqa: E402
from examples.platformer_pack.tileset_art import (  # noqa: E402
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
from examples.platformer_pack.vlm_qa import make_fake_vlm_responder  # noqa: E402
from examples.run_platformer_slice import make_fake_responder  # noqa: E402

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
        from examples.platformer_pack.art_phases import _enemy_art_descriptor

        d = _enemy_art_descriptor("sentry", "a plump spring-legged guardian")
        assert "planted" in d and "immobile" in d
        assert "a plump spring-legged guardian" in d
        # A patroller reads as moving, never planted.
        p = _enemy_art_descriptor("patroller", "a round critter")
        assert "walk" in p and "planted" not in p

    def test_player_is_a_weaponless_mascot(self) -> None:
        from examples.platformer_pack.art_phases import PLAYER_DESCRIPTOR

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
        # + player + the 2 gameplay props (checkpoint flag, exit goal)
        assert len(warnings) == len(ctx.bible.enemy_definitions) + 3
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
        assert sorted(props.prop_paths) == ["checkpoint", "exit"]
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
        from examples.platformer_pack.art_phases import SpriteArtPhase

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
        assert len(tinted) == len(ctx.bible.enemy_definitions)
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
        ctx = _run(tmp_path / "out", _producer(tmp_path))
        backdrop = ctx.bible.backdrops[STAGE]
        assert backdrop.artifact_id == f"backdrop:{STAGE}"
        assert len(backdrop.band_paths) == 2  # DEFAULT_GRAPHICS.backdrop_bands
        assert len(backdrop.depths) == 2
        assert "phase:plat:style" in backdrop.parents
        for rel in backdrop.band_paths:
            assert (tmp_path / "out" / rel).exists()
            assert backdrop.band_hashes[rel].startswith("sha256:")
        manifest = json.loads(
            (tmp_path / "out" / f"backdrop/{STAGE}/manifest.json").read_text()
        )
        assert manifest["band_paths"] == backdrop.band_paths

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


class TestArtRunsAtTheEnd:
    def test_art_nodes_run_after_level_nodes(self, tmp_path: Path) -> None:
        """The user rule: paid art only after the levels validate. In the
        orchestrated DAG the art nodes' completion order sits strictly
        after every level step node."""
        from examples.platformer_pack.dag import run_orchestrated

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
                    "phase:plat:backdrop_art"):
            assert done.index(art) > last_level, art

    def test_regen_art_node_rerolls_art_only(self, tmp_path: Path) -> None:
        from canon.pipeline.orchestrator import mark_stale
        from examples.platformer_pack.dag import run_orchestrated

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
        from examples.platformer_pack.effects import describe_vocabulary

        vocab = describe_vocabulary()
        assert "density 1-200" in vocab
        assert "speed 10-400 fall px/s" in vocab
        assert "color '#rrggbb'" in vocab

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
    def test_every_enemy_gets_per_state_strips_and_frames_json(
        self, tmp_path: Path
    ) -> None:
        out = tmp_path / "out"
        _run_animated(out, tmp_path)
        enemy_dirs = sorted((out / "sprite" / "enemy").glob("*"))
        assert enemy_dirs, "SpriteArtPhase produced no base sprites"
        for d in enemy_dirs:
            assert (d / "base.png").exists()
            frames = json.loads((d / "frames.json").read_text())
            assert set(frames) == {"idle", "walk", "hurt", "death"}
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

    def test_no_judge_keeps_sprites_static(self, tmp_path: Path) -> None:
        # loud fallback: producer present, judge absent → no motion authored
        out = tmp_path / "out"
        _run(out, _producer(tmp_path))  # compose WITHOUT vlm_judge
        d = sorted((out / "sprite" / "enemy").glob("*"))[0]
        assert (d / "base.png").exists()
        assert not (d / "frames.json").exists()
        assert not (d / "walk.png").exists()

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
            # code checks ran (strip + frames) and passed on the fake frames
            checks = {c["check"] for c in entry["code_checks"]}
            assert {"animation_strip", "animation_frames"} <= checks
            assert all(c["passed"] for c in entry["code_checks"])
            # the fake judge's canned verdict passed all dimensions
            assert all(v["passed"] for v in entry["verdicts"].values())

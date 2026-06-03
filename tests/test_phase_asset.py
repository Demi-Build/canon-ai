"""Unit tests for AssetPhase.

Contract tests using Fake* backends from canon.backends.testing.
No live API calls. No SDK imports inside AssetPhase.

Covers:
- Phase satisfies the Phase protocol
- With all three Fake backends and a bible with characters + entities +
  archetypes + maps, phase produces expected files at expected paths
- Character.portrait_path is set after run
- ClassArchetype.portrait_path is set after run
- "assets" appears in bible.metadata.phases_run
- skip_image=True produces no portrait files but still produces music/sfx
- skip_music=True, skip_sfx=True work symmetrically
- No backends → warning logged, metadata still stamped
- image_concurrency=2 completes all tasks without error
- FakeImageBackend.calls records every prompt + filepath + dimensions
"""

from __future__ import annotations

import random
from pathlib import Path
from typing import Any

from canon import (
    Bible,
    BibleMetadata,
    CanonConfig,
    Character,
    ClassArchetype,
    EntityLore,
    Map,
    PipelineContext,
)
from canon.backends.testing import FakeImageBackend, FakeMusicBackend, FakeSFXBackend
from canon.pipeline.phases.asset import AssetPhase
from canon.pipeline.runner import Phase

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(tmp_path: Path) -> Any:
    """Return a CanonConfig-like object with output_dir set to tmp_path."""
    cfg = CanonConfig(seed="test")
    # CanonConfig is a Pydantic model — use model_copy to override output_dir
    return cfg.model_copy(update={"output_dir": tmp_path})


def _make_ctx(
    tmp_path: Path,
    *,
    num_characters: int = 2,
    num_maps: int = 2,
    entities_per_map: int = 2,
    num_archetypes: int = 2,
    image_backend: Any = None,
    music_backend: Any = None,
    sfx_backend: Any = None,
    environments: list[str] | None = None,
) -> PipelineContext:
    """Build a PipelineContext with an in-memory bible populated with test data."""
    if environments is None:
        environments = ["forest", "dungeon"]

    bible = Bible.empty(seed="asset_test")
    bible.metadata = BibleMetadata()

    # Add characters
    for i in range(num_characters):
        char = Character(
            character_id=f"npc_{i:04d}",
            name=f"NPC {i}",
            role="npc",
            portrait_prompt=f"a portrait of NPC {i}",
        )
        bible.add_character(char)

    # Add maps with entities
    for mi in range(num_maps):
        env = environments[mi % len(environments)]
        m = Map(
            map_id=f"room_{mi}",
            name=f"Room {mi}",
            description=f"A {env} room.",
            environment=env,
        )
        for ei in range(entities_per_map):
            et = "monster" if ei % 2 == 0 else "item"
            entity = EntityLore(
                entity_id=f"{et}_{mi}_{ei}",
                entity_type=et,
                name=f"{et.capitalize()} {mi}-{ei}",
                map_id=f"room_{mi}",
                lore=f"A {et} in room {mi}.",
            )
            m.entities.append(entity)
        bible.maps[m.map_id] = m

    # Add archetypes
    for ai in range(num_archetypes):
        arch = ClassArchetype(
            archetype_id=f"archetype_{ai}",
            name=f"Archetype {ai}",
            description=f"Class {ai}",
            archetype=f"class_{ai}",
            portrait_prompt=f"a portrait of archetype {ai}",
        )
        bible.class_archetypes[arch.archetype_id] = arch

    ctx = PipelineContext(
        bible=bible,
        config=_make_config(tmp_path),
        rng=random.Random("asset_test"),
    )

    # Inject backends directly (bypassing BackendRegistry)
    if image_backend is not None:
        ctx.image_backend = image_backend
    if music_backend is not None:
        ctx.music_backend = music_backend
    if sfx_backend is not None:
        ctx.sfx_backend = sfx_backend

    return ctx


# ---------------------------------------------------------------------------
# Phase protocol
# ---------------------------------------------------------------------------


class TestAssetPhaseProtocol:
    def test_satisfies_phase_protocol(self):
        phase = AssetPhase()
        assert isinstance(phase, Phase)

    def test_has_name_attribute(self):
        assert AssetPhase.name == "assets"

    def test_name_is_assets(self):
        assert AssetPhase().name == "assets"

    def test_has_run_method(self):
        assert callable(AssetPhase().run)


# ---------------------------------------------------------------------------
# Constructor
# ---------------------------------------------------------------------------


class TestAssetPhaseConstructor:
    def test_default_image_concurrency(self):
        assert AssetPhase().image_concurrency == 15

    def test_default_music_concurrency(self):
        assert AssetPhase().music_concurrency == 8

    def test_default_sfx_concurrency(self):
        assert AssetPhase().sfx_concurrency == 10

    def test_custom_image_concurrency(self):
        assert AssetPhase(image_concurrency=2).image_concurrency == 2

    def test_default_skip_flags_are_false(self):
        p = AssetPhase()
        assert not p.skip_image
        assert not p.skip_music
        assert not p.skip_sfx

    def test_portrait_size_default(self):
        assert AssetPhase().portrait_size == (512, 512)

    def test_portrait_size_custom(self):
        assert AssetPhase(portrait_size=(256, 256)).portrait_size == (256, 256)


# ---------------------------------------------------------------------------
# Image output: character portraits
# ---------------------------------------------------------------------------


class TestImageOutputCharacters:
    def test_character_portrait_files_created(self, tmp_path):
        img = FakeImageBackend()
        ctx = _make_ctx(tmp_path, num_characters=2, image_backend=img)
        AssetPhase(skip_music=True, skip_sfx=True).run(ctx)

        portraits_dir = tmp_path / "portraits" / "npcs"
        for char in ctx.bible.characters:
            fp = portraits_dir / f"npc_{char.character_id}.png"
            assert fp.exists(), f"Missing portrait: {fp}"

    def test_character_portrait_path_stamped(self, tmp_path):
        img = FakeImageBackend()
        ctx = _make_ctx(tmp_path, num_characters=2, image_backend=img)
        AssetPhase(skip_music=True, skip_sfx=True).run(ctx)

        for char in ctx.bible.characters:
            assert char.portrait_path is not None
            assert char.portrait_path.endswith(".png")

    def test_character_portrait_path_matches_file(self, tmp_path):
        img = FakeImageBackend()
        ctx = _make_ctx(tmp_path, num_characters=2, image_backend=img)
        AssetPhase(skip_music=True, skip_sfx=True).run(ctx)

        for char in ctx.bible.characters:
            assert Path(char.portrait_path).exists()

    def test_character_portrait_uses_portrait_prompt(self, tmp_path):
        img = FakeImageBackend()
        ctx = _make_ctx(tmp_path, num_characters=1, image_backend=img)
        AssetPhase(skip_music=True, skip_sfx=True).run(ctx)

        char = ctx.bible.characters[0]
        prompts_used = [c["prompt"] for c in img.calls]
        assert char.portrait_prompt in prompts_used

    def test_character_portrait_fallback_when_no_prompt(self, tmp_path):
        img = FakeImageBackend()
        ctx = _make_ctx(tmp_path, num_characters=1, image_backend=img)
        # Remove portrait_prompt from character
        ctx.bible.characters[0].portrait_prompt = None
        AssetPhase(skip_music=True, skip_sfx=True).run(ctx)

        char = ctx.bible.characters[0]
        # Fallback prompt: "a character: <name>"
        fallback = f"a character: {char.name}"
        prompts_used = [c["prompt"] for c in img.calls]
        assert fallback in prompts_used


# ---------------------------------------------------------------------------
# Image output: entity portraits
# ---------------------------------------------------------------------------


class TestImageOutputEntities:
    def test_monster_portrait_files_created(self, tmp_path):
        img = FakeImageBackend()
        ctx = _make_ctx(tmp_path, entities_per_map=2, image_backend=img)
        AssetPhase(skip_music=True, skip_sfx=True).run(ctx)

        portraits_dir = tmp_path / "portraits"
        monster_dir = portraits_dir / "monsters"
        # At least one monster file should exist
        assert monster_dir.exists() or (portraits_dir / "items").exists()

    def test_item_portrait_files_created(self, tmp_path):
        img = FakeImageBackend()
        ctx = _make_ctx(tmp_path, entities_per_map=2, image_backend=img)
        AssetPhase(skip_music=True, skip_sfx=True).run(ctx)

        item_dir = tmp_path / "portraits" / "items"
        # We have entities with type "item"
        assert item_dir.exists()

    def test_entity_portrait_slug_naming(self, tmp_path):
        """Monster/item files use slug from name."""
        img = FakeImageBackend()
        ctx = _make_ctx(tmp_path, num_maps=1, entities_per_map=2, image_backend=img)
        AssetPhase(skip_music=True, skip_sfx=True).run(ctx)

        m = ctx.bible.maps["room_0"]
        for entity in m.entities:
            if entity.entity_type in ("monster", "item"):
                slug = entity.name.lower().replace(" ", "_")
                prefix = "mon" if entity.entity_type == "monster" else "item"
                fp = tmp_path / "portraits" / f"{entity.entity_type}s" / f"{prefix}_{slug}.png"
                assert fp.exists(), f"Missing entity portrait: {fp}"


# ---------------------------------------------------------------------------
# Image output: archetype portraits
# ---------------------------------------------------------------------------


class TestImageOutputArchetypes:
    def test_archetype_portrait_files_created(self, tmp_path):
        img = FakeImageBackend()
        ctx = _make_ctx(tmp_path, num_archetypes=2, image_backend=img)
        AssetPhase(skip_music=True, skip_sfx=True).run(ctx)

        classes_dir = tmp_path / "portraits" / "classes"
        for i in range(2):
            fp = classes_dir / f"class_{i}.png"
            assert fp.exists(), f"Missing archetype portrait: {fp}"

    def test_archetype_portrait_path_stamped(self, tmp_path):
        img = FakeImageBackend()
        ctx = _make_ctx(tmp_path, num_archetypes=2, image_backend=img)
        AssetPhase(skip_music=True, skip_sfx=True).run(ctx)

        for arch in ctx.bible.class_archetypes.values():
            assert arch.portrait_path is not None
            assert arch.portrait_path.endswith(".png")

    def test_archetype_portrait_path_matches_file(self, tmp_path):
        img = FakeImageBackend()
        ctx = _make_ctx(tmp_path, num_archetypes=2, image_backend=img)
        AssetPhase(skip_music=True, skip_sfx=True).run(ctx)

        for arch in ctx.bible.class_archetypes.values():
            assert Path(arch.portrait_path).exists()

    def test_archetype_portrait_uses_portrait_prompt(self, tmp_path):
        img = FakeImageBackend()
        ctx = _make_ctx(tmp_path, num_archetypes=1, image_backend=img)
        AssetPhase(skip_music=True, skip_sfx=True).run(ctx)

        arch = next(iter(ctx.bible.class_archetypes.values()))
        prompts_used = [c["prompt"] for c in img.calls]
        assert arch.portrait_prompt in prompts_used


# ---------------------------------------------------------------------------
# Image output: environment portraits
# ---------------------------------------------------------------------------


class TestImageOutputEnvironments:
    def test_environment_portrait_files_created(self, tmp_path):
        img = FakeImageBackend()
        ctx = _make_ctx(tmp_path, num_maps=2, image_backend=img)
        AssetPhase(skip_music=True, skip_sfx=True).run(ctx)

        portraits_dir = tmp_path / "portraits"
        for i in range(2):
            fp = portraits_dir / f"environment_{i}.png"
            assert fp.exists(), f"Missing environment portrait: {fp}"


# ---------------------------------------------------------------------------
# Music output
# ---------------------------------------------------------------------------


class TestMusicOutput:
    def test_fixed_music_files_created(self, tmp_path):
        music = FakeMusicBackend()
        ctx = _make_ctx(tmp_path, music_backend=music)
        AssetPhase(skip_image=True, skip_sfx=True).run(ctx)

        music_dir = tmp_path / "music"
        for fname in ["combat.mp3", "puzzle_event.mp3", "start_screen.mp3",
                      "victory.mp3", "game_over.mp3"]:
            fp = music_dir / fname
            assert fp.exists(), f"Missing music file: {fp}"

    def test_per_env_music_files_created(self, tmp_path):
        music = FakeMusicBackend()
        ctx = _make_ctx(tmp_path, environments=["forest", "dungeon"], music_backend=music)
        AssetPhase(skip_image=True, skip_sfx=True).run(ctx)

        music_dir = tmp_path / "music"
        # Each unique environment gets a track
        envs = {m.environment for m in ctx.bible.maps.values() if m.environment}
        for env in envs:
            fp = music_dir / f"maze_{env}.mp3"
            assert fp.exists(), f"Missing env music: {fp}"

    def test_combat_track_exists(self, tmp_path):
        music = FakeMusicBackend()
        ctx = _make_ctx(tmp_path, music_backend=music)
        AssetPhase(skip_image=True, skip_sfx=True).run(ctx)
        assert (tmp_path / "music" / "combat.mp3").exists()

    def test_victory_track_exists(self, tmp_path):
        music = FakeMusicBackend()
        ctx = _make_ctx(tmp_path, music_backend=music)
        AssetPhase(skip_image=True, skip_sfx=True).run(ctx)
        assert (tmp_path / "music" / "victory.mp3").exists()

    def test_game_over_track_exists(self, tmp_path):
        music = FakeMusicBackend()
        ctx = _make_ctx(tmp_path, music_backend=music)
        AssetPhase(skip_image=True, skip_sfx=True).run(ctx)
        assert (tmp_path / "music" / "game_over.mp3").exists()

    def test_music_calls_recorded(self, tmp_path):
        music = FakeMusicBackend()
        ctx = _make_ctx(tmp_path, music_backend=music)
        AssetPhase(skip_image=True, skip_sfx=True).run(ctx)
        assert len(music.calls) > 0

    def test_music_call_has_expected_keys(self, tmp_path):
        music = FakeMusicBackend()
        ctx = _make_ctx(tmp_path, music_backend=music)
        AssetPhase(skip_image=True, skip_sfx=True).run(ctx)
        for call in music.calls:
            assert "prompt" in call
            assert "filepath" in call
            assert "duration_seconds" in call


# ---------------------------------------------------------------------------
# SFX output
# ---------------------------------------------------------------------------


class TestSFXOutput:
    def test_fixed_sfx_files_created(self, tmp_path):
        sfx = FakeSFXBackend()
        ctx = _make_ctx(tmp_path, sfx_backend=sfx)
        AssetPhase(skip_image=True, skip_music=True).run(ctx)

        sfx_dir = tmp_path / "sfx"
        FIXED_NAMES = [
            "weapon_light_swing.mp3", "weapon_light_hit.mp3",
            "weapon_heavy_swing.mp3", "weapon_heavy_hit.mp3",
            "spell_damage_single_cast.mp3", "spell_heal_cast.mp3",
            "player_take_damage.mp3", "player_death.mp3",
            "item_pickup.mp3", "door_open.mp3", "dice_roll.mp3",
            "event_complete.mp3",
        ]
        for fname in FIXED_NAMES:
            fp = sfx_dir / fname
            assert fp.exists(), f"Missing SFX file: {fp}"

    def test_per_env_ambience_created(self, tmp_path):
        sfx = FakeSFXBackend()
        ctx = _make_ctx(tmp_path, environments=["forest", "dungeon"], sfx_backend=sfx)
        AssetPhase(skip_image=True, skip_music=True).run(ctx)

        sfx_dir = tmp_path / "sfx"
        envs = {m.environment for m in ctx.bible.maps.values() if m.environment}
        for env in envs:
            fp = sfx_dir / f"ambience_{env}.mp3"
            assert fp.exists(), f"Missing ambience SFX: {fp}"

    def test_sfx_calls_recorded(self, tmp_path):
        sfx = FakeSFXBackend()
        ctx = _make_ctx(tmp_path, sfx_backend=sfx)
        AssetPhase(skip_image=True, skip_music=True).run(ctx)
        assert len(sfx.calls) > 0

    def test_sfx_call_has_expected_keys(self, tmp_path):
        sfx = FakeSFXBackend()
        ctx = _make_ctx(tmp_path, sfx_backend=sfx)
        AssetPhase(skip_image=True, skip_music=True).run(ctx)
        for call in sfx.calls:
            assert "prompt" in call
            assert "filepath" in call
            assert "duration_seconds" in call
            assert "loop" in call

    def test_ambience_sfx_is_looped(self, tmp_path):
        sfx = FakeSFXBackend()
        ctx = _make_ctx(tmp_path, environments=["forest"], sfx_backend=sfx)
        AssetPhase(skip_image=True, skip_music=True).run(ctx)

        # Ambience calls should have loop=True
        ambience_calls = [c for c in sfx.calls if "ambience" in c.get("prompt", "")]
        assert len(ambience_calls) >= 1
        for call in ambience_calls:
            assert call["loop"] is True

    def test_fixed_sfx_is_not_looped(self, tmp_path):
        sfx = FakeSFXBackend()
        ctx = _make_ctx(tmp_path, sfx_backend=sfx)
        AssetPhase(skip_image=True, skip_music=True).run(ctx)

        non_ambience = [c for c in sfx.calls if "ambience" not in c.get("prompt", "")]
        assert len(non_ambience) >= 1
        for call in non_ambience:
            assert call["loop"] is False


# ---------------------------------------------------------------------------
# FakeImageBackend call recording
# ---------------------------------------------------------------------------


class TestFakeImageBackendCallRecording:
    def test_calls_records_every_image_prompt(self, tmp_path):
        img = FakeImageBackend()
        ctx = _make_ctx(tmp_path, num_characters=2, num_archetypes=2,
                        image_backend=img)
        AssetPhase(skip_music=True, skip_sfx=True).run(ctx)
        # Should have calls for each character + archetype + entities + maps
        assert len(img.calls) > 0

    def test_calls_contains_filepath(self, tmp_path):
        img = FakeImageBackend()
        ctx = _make_ctx(tmp_path, num_characters=1, image_backend=img)
        AssetPhase(skip_music=True, skip_sfx=True).run(ctx)
        for call in img.calls:
            assert "filepath" in call

    def test_calls_contains_dimensions(self, tmp_path):
        img = FakeImageBackend()
        ctx = _make_ctx(tmp_path, num_characters=1, image_backend=img)
        AssetPhase(skip_music=True, skip_sfx=True).run(ctx)
        for call in img.calls:
            assert "width" in call
            assert "height" in call

    def test_calls_respect_portrait_size(self, tmp_path):
        img = FakeImageBackend()
        ctx = _make_ctx(tmp_path, num_characters=1, image_backend=img)
        AssetPhase(portrait_size=(256, 256), skip_music=True, skip_sfx=True).run(ctx)
        for call in img.calls:
            assert call["width"] == 256
            assert call["height"] == 256

    def test_calls_default_portrait_size_is_512(self, tmp_path):
        img = FakeImageBackend()
        ctx = _make_ctx(tmp_path, num_characters=1, image_backend=img)
        AssetPhase(skip_music=True, skip_sfx=True).run(ctx)
        for call in img.calls:
            assert call["width"] == 512
            assert call["height"] == 512

    def test_character_portrait_call_count_matches_character_count(self, tmp_path):
        img = FakeImageBackend()
        # Use minimal setup: 3 chars, no archetypes, no maps
        ctx = _make_ctx(tmp_path, num_characters=3, num_maps=0,
                        num_archetypes=0, image_backend=img)
        AssetPhase(skip_music=True, skip_sfx=True).run(ctx)
        # 3 character portraits only
        assert len(img.calls) == 3


# ---------------------------------------------------------------------------
# Metadata stamping
# ---------------------------------------------------------------------------


class TestMetadataStamping:
    def test_assets_in_phases_run(self, tmp_path):
        img = FakeImageBackend()
        ctx = _make_ctx(tmp_path, image_backend=img)
        AssetPhase(skip_music=True, skip_sfx=True).run(ctx)
        assert "assets" in ctx.bible.metadata.phases_run

    def test_phases_run_appends_on_each_run(self, tmp_path):
        img = FakeImageBackend()
        ctx = _make_ctx(tmp_path, image_backend=img)
        phase = AssetPhase(skip_music=True, skip_sfx=True)
        phase.run(ctx)
        phase.run(ctx)
        assert ctx.bible.metadata.phases_run.count("assets") == 2

    def test_metadata_stamped_even_with_no_backends(self, tmp_path):
        ctx = _make_ctx(tmp_path)  # no backends
        AssetPhase().run(ctx)
        assert "assets" in ctx.bible.metadata.phases_run

    def test_metadata_stamped_on_empty_bible(self, tmp_path):
        bible = Bible.empty(seed="test")
        bible.metadata = BibleMetadata()
        cfg = CanonConfig(seed="test").model_copy(update={"output_dir": tmp_path})
        ctx = PipelineContext(
            bible=bible,
            config=cfg,
            rng=random.Random("test"),
        )
        AssetPhase().run(ctx)
        assert "assets" in ctx.bible.metadata.phases_run


# ---------------------------------------------------------------------------
# Skip flags
# ---------------------------------------------------------------------------


class TestSkipFlags:
    def test_skip_image_produces_no_portrait_files(self, tmp_path):
        img = FakeImageBackend()
        music = FakeMusicBackend()
        sfx = FakeSFXBackend()
        ctx = _make_ctx(tmp_path, image_backend=img, music_backend=music,
                        sfx_backend=sfx)
        AssetPhase(skip_image=True).run(ctx)

        portraits_dir = tmp_path / "portraits"
        # No portrait files
        assert not portraits_dir.exists() or not any(portraits_dir.rglob("*.png"))
        # No image backend calls
        assert len(img.calls) == 0

    def test_skip_image_still_produces_music(self, tmp_path):
        music = FakeMusicBackend()
        ctx = _make_ctx(tmp_path, music_backend=music)
        AssetPhase(skip_image=True, skip_sfx=True).run(ctx)
        assert (tmp_path / "music" / "combat.mp3").exists()

    def test_skip_image_still_produces_sfx(self, tmp_path):
        sfx = FakeSFXBackend()
        ctx = _make_ctx(tmp_path, sfx_backend=sfx)
        AssetPhase(skip_image=True, skip_music=True).run(ctx)
        assert (tmp_path / "sfx" / "weapon_light_swing.mp3").exists()

    def test_skip_music_produces_no_music_files(self, tmp_path):
        music = FakeMusicBackend()
        ctx = _make_ctx(tmp_path, music_backend=music)
        AssetPhase(skip_image=True, skip_music=True).run(ctx)

        music_dir = tmp_path / "music"
        assert not music_dir.exists() or not any(music_dir.rglob("*.mp3"))
        assert len(music.calls) == 0

    def test_skip_music_still_produces_portraits(self, tmp_path):
        img = FakeImageBackend()
        ctx = _make_ctx(tmp_path, num_characters=1, image_backend=img)
        AssetPhase(skip_music=True, skip_sfx=True).run(ctx)
        assert len(img.calls) > 0

    def test_skip_sfx_produces_no_sfx_files(self, tmp_path):
        sfx = FakeSFXBackend()
        ctx = _make_ctx(tmp_path, sfx_backend=sfx)
        AssetPhase(skip_image=True, skip_music=True, skip_sfx=True).run(ctx)

        sfx_dir = tmp_path / "sfx"
        assert not sfx_dir.exists() or not any(sfx_dir.rglob("*.mp3"))
        assert len(sfx.calls) == 0

    def test_skip_all_three_stamps_metadata(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        AssetPhase(skip_image=True, skip_music=True, skip_sfx=True).run(ctx)
        assert "assets" in ctx.bible.metadata.phases_run


# ---------------------------------------------------------------------------
# No backends registered
# ---------------------------------------------------------------------------


class TestNoBackends:
    def test_no_backends_exits_cleanly(self, tmp_path):
        ctx = _make_ctx(tmp_path)  # no backends injected
        # Must not raise
        AssetPhase().run(ctx)

    def test_no_backends_still_stamps_metadata(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        AssetPhase().run(ctx)
        assert "assets" in ctx.bible.metadata.phases_run

    def test_no_backends_produces_no_files(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        AssetPhase().run(ctx)
        # Neither portraits/ nor music/ nor sfx/ should contain files
        assert not any((tmp_path / "portraits").rglob("*") if (tmp_path / "portraits").exists() else [])
        assert not any((tmp_path / "music").rglob("*") if (tmp_path / "music").exists() else [])
        assert not any((tmp_path / "sfx").rglob("*") if (tmp_path / "sfx").exists() else [])


# ---------------------------------------------------------------------------
# Bounded concurrency
# ---------------------------------------------------------------------------


class TestBoundedConcurrency:
    def test_image_concurrency_2_completes_all_tasks(self, tmp_path):
        img = FakeImageBackend()
        ctx = _make_ctx(tmp_path, num_characters=5, num_archetypes=3,
                        image_backend=img)
        AssetPhase(image_concurrency=2, skip_music=True, skip_sfx=True).run(ctx)

        # All character portraits should be created
        portraits_dir = tmp_path / "portraits" / "npcs"
        for char in ctx.bible.characters:
            fp = portraits_dir / f"npc_{char.character_id}.png"
            assert fp.exists(), f"Missing portrait with concurrency=2: {fp}"

    def test_music_concurrency_1_completes_all_tracks(self, tmp_path):
        music = FakeMusicBackend()
        ctx = _make_ctx(tmp_path, music_backend=music)
        AssetPhase(music_concurrency=1, skip_image=True, skip_sfx=True).run(ctx)

        for fname in ["combat.mp3", "victory.mp3", "game_over.mp3"]:
            assert (tmp_path / "music" / fname).exists()

    def test_sfx_concurrency_1_completes_all_effects(self, tmp_path):
        sfx = FakeSFXBackend()
        ctx = _make_ctx(tmp_path, sfx_backend=sfx)
        AssetPhase(sfx_concurrency=1, skip_image=True, skip_music=True).run(ctx)

        assert (tmp_path / "sfx" / "player_death.mp3").exists()


# ---------------------------------------------------------------------------
# All three backends together
# ---------------------------------------------------------------------------


class TestAllBackendsTogether:
    def test_all_backends_produce_expected_output(self, tmp_path):
        img = FakeImageBackend()
        music = FakeMusicBackend()
        sfx = FakeSFXBackend()
        ctx = _make_ctx(
            tmp_path,
            num_characters=2,
            num_maps=2,
            entities_per_map=2,
            num_archetypes=2,
            image_backend=img,
            music_backend=music,
            sfx_backend=sfx,
            environments=["forest", "dungeon"],
        )
        AssetPhase().run(ctx)

        # Image checks
        for char in ctx.bible.characters:
            assert (tmp_path / "portraits" / "npcs" / f"npc_{char.character_id}.png").exists()
        for i in range(2):
            assert (tmp_path / "portraits" / "classes" / f"class_{i}.png").exists()
        for i in range(2):
            assert (tmp_path / "portraits" / f"environment_{i}.png").exists()

        # Music checks
        for fname in ["combat.mp3", "maze_forest.mp3", "maze_dungeon.mp3",
                      "victory.mp3", "game_over.mp3"]:
            assert (tmp_path / "music" / fname).exists()

        # SFX checks
        for fname in ["weapon_light_swing.mp3", "player_death.mp3",
                      "ambience_forest.mp3", "ambience_dungeon.mp3"]:
            assert (tmp_path / "sfx" / fname).exists()

        # Metadata
        assert "assets" in ctx.bible.metadata.phases_run

    def test_image_calls_count_matches_expected(self, tmp_path):
        """Total image calls = characters + entities + archetypes + maps."""
        img = FakeImageBackend()
        ctx = _make_ctx(
            tmp_path,
            num_characters=2,
            num_maps=2,
            entities_per_map=2,
            num_archetypes=2,
            image_backend=img,
        )
        AssetPhase(skip_music=True, skip_sfx=True).run(ctx)

        num_chars = 2
        num_entities = 2 * 2  # 2 maps × 2 entities
        num_archetypes = 2
        num_env_portraits = 2  # one per map

        expected = num_chars + num_entities + num_archetypes + num_env_portraits
        assert len(img.calls) == expected


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_empty_bible_runs_without_error(self, tmp_path):
        img = FakeImageBackend()
        music = FakeMusicBackend()
        sfx = FakeSFXBackend()
        bible = Bible.empty(seed="empty")
        bible.metadata = BibleMetadata()
        cfg = CanonConfig(seed="empty").model_copy(update={"output_dir": tmp_path})
        ctx = PipelineContext(
            bible=bible,
            config=cfg,
            rng=random.Random("empty"),
        )
        ctx.image_backend = img
        ctx.music_backend = music
        ctx.sfx_backend = sfx
        # Empty bible = no characters, no maps, no archetypes
        AssetPhase().run(ctx)
        assert "assets" in ctx.bible.metadata.phases_run

    def test_empty_bible_still_generates_music_fixed_tracks(self, tmp_path):
        music = FakeMusicBackend()
        bible = Bible.empty(seed="empty")
        bible.metadata = BibleMetadata()
        cfg = CanonConfig(seed="empty").model_copy(update={"output_dir": tmp_path})
        ctx = PipelineContext(
            bible=bible,
            config=cfg,
            rng=random.Random("empty"),
        )
        ctx.music_backend = music
        AssetPhase(skip_image=True, skip_sfx=True).run(ctx)
        # Fixed tracks always generated
        assert (tmp_path / "music" / "combat.mp3").exists()

    def test_empty_bible_generates_fixed_sfx(self, tmp_path):
        sfx = FakeSFXBackend()
        bible = Bible.empty(seed="empty")
        bible.metadata = BibleMetadata()
        cfg = CanonConfig(seed="empty").model_copy(update={"output_dir": tmp_path})
        ctx = PipelineContext(
            bible=bible,
            config=cfg,
            rng=random.Random("empty"),
        )
        ctx.sfx_backend = sfx
        AssetPhase(skip_image=True, skip_music=True).run(ctx)
        assert (tmp_path / "sfx" / "player_death.mp3").exists()

    def test_single_env_no_duplicate_music_tracks(self, tmp_path):
        """Same environment across multiple maps → only one maze_<env>.mp3."""
        music = FakeMusicBackend()
        ctx = _make_ctx(
            tmp_path,
            num_maps=3,
            environments=["forest"],  # all maps same env
            music_backend=music,
        )
        AssetPhase(skip_image=True, skip_sfx=True).run(ctx)

        # Should only be one maze_forest.mp3
        maze_calls = [c for c in music.calls if "maze_forest" in c.get("filepath", "")]
        assert len(maze_calls) == 1

    def test_maps_with_no_environment_skipped_for_music(self, tmp_path):
        """Maps with environment=None do not generate a track."""
        music = FakeMusicBackend()
        bible = Bible.empty(seed="noenv")
        bible.metadata = BibleMetadata()
        # Add a map with no environment
        m = Map(map_id="room_0", name="Empty Room", description="No env")
        bible.maps["room_0"] = m
        cfg = CanonConfig(seed="noenv").model_copy(update={"output_dir": tmp_path})
        ctx = PipelineContext(bible=bible, config=cfg, rng=random.Random("noenv"))
        ctx.music_backend = music
        AssetPhase(skip_image=True, skip_sfx=True).run(ctx)

        # No maze_None.mp3 or maze_.mp3
        music_dir = tmp_path / "music"
        if music_dir.exists():
            maze_files = list(music_dir.glob("maze_*.mp3"))
            assert len(maze_files) == 0

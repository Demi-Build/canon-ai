"""Contract tests for ManifestPhase.

Tests verify the file shapes produced by ManifestPhase — the observable
boundary that mazeworld and cradle consume.

Uses tmp_path for filesystem isolation. No re-implementation of the phase logic.
"""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

from canon import (
    Bible,
    CanonConfig,
    GenerationStats,
    Map,
    PipelineContext,
    StoryArc,
)
from canon.bible.models import (
    ClassArchetype,
    EntityLore,
    Faction,
)
from canon.pipeline.phases.manifest import ManifestPhase
from canon.pipeline.runner import Phase

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _make_minimal_ctx(tmp_path: Path, *, num_maps: int = 1) -> PipelineContext:
    """Minimal context: bible with N maps, no entities, no characters."""
    bible = Bible.empty(seed="manifest_test")
    bible.story = StoryArc(
        title="Test World",
        synopsis="synopsis here",
        seed="manifest_test",
        factions=[Faction(
            faction_id="f1",
            name="Test Faction",
            description="desc",
            history="hist",
            leader="Leader",
        )],
    )
    for i in range(num_maps):
        bible.maps[f"room_{i}"] = Map(
            map_id=f"room_{i}",
            name=f"Room {i}",
            description="desc",
            environment="ruins",
            level=i + 1,
            story_beat="A beat.",
        )
    config = CanonConfig(seed="manifest_test", output_dir=str(tmp_path))
    return PipelineContext(
        bible=bible,
        config=config,
        rng=random.Random(42),
        stats=GenerationStats(),
    )


def _add_entity(bible: Bible, map_id: str, entity_type: str, name: str = "X") -> None:
    """Add a minimal EntityLore to a map."""
    entity = EntityLore(
        entity_id=f"{entity_type}_{len(bible.maps[map_id].entities)}",
        entity_type=entity_type,
        name=name,
        lore="lore text",
        map_id=map_id,
    )
    bible.maps[map_id].entities.append(entity)


# ---------------------------------------------------------------------------
# Phase protocol
# ---------------------------------------------------------------------------


class TestPhaseProtocol:
    def test_satisfies_phase_protocol(self):
        assert isinstance(ManifestPhase(), Phase)

    def test_phase_name_is_manifest(self):
        assert ManifestPhase.name == "manifest"


# ---------------------------------------------------------------------------
# manifest.json — required keys
# ---------------------------------------------------------------------------


class TestManifestJson:
    def test_manifest_json_is_written(self, tmp_path: Path):
        ctx = _make_minimal_ctx(tmp_path)
        ManifestPhase().run(ctx)
        assert (tmp_path / "manifest.json").exists()

    def test_manifest_json_has_required_keys(self, tmp_path: Path):
        ctx = _make_minimal_ctx(tmp_path)
        ManifestPhase().run(ctx)
        data = _load(tmp_path / "manifest.json")
        required_keys = [
            "seed",
            "num_rooms",
            "environments",
            "environment_names",
            "npc_count",
            "quest_count",
            "event_count",
            "class_count",
            "rooms",
            "validation_report",
            "generation_stats",
            "music",
            "sfx",
        ]
        for key in required_keys:
            assert key in data, f"manifest.json missing key: {key!r}"

    def test_num_rooms_matches_bible(self, tmp_path: Path):
        ctx = _make_minimal_ctx(tmp_path, num_maps=3)
        ManifestPhase().run(ctx)
        data = _load(tmp_path / "manifest.json")
        assert data["num_rooms"] == 3

    def test_environments_list_length(self, tmp_path: Path):
        ctx = _make_minimal_ctx(tmp_path, num_maps=2)
        ManifestPhase().run(ctx)
        data = _load(tmp_path / "manifest.json")
        assert len(data["environments"]) == 2

    def test_npc_count_from_entities(self, tmp_path: Path):
        ctx = _make_minimal_ctx(tmp_path)
        _add_entity(ctx.bible, "room_0", "npc", "Goblin A")
        _add_entity(ctx.bible, "room_0", "npc", "Goblin B")
        ManifestPhase().run(ctx)
        data = _load(tmp_path / "manifest.json")
        assert data["npc_count"] == 2

    def test_quest_count_from_entities(self, tmp_path: Path):
        ctx = _make_minimal_ctx(tmp_path)
        _add_entity(ctx.bible, "room_0", "quest")
        ManifestPhase().run(ctx)
        data = _load(tmp_path / "manifest.json")
        assert data["quest_count"] == 1

    def test_event_count_from_entities(self, tmp_path: Path):
        ctx = _make_minimal_ctx(tmp_path)
        _add_entity(ctx.bible, "room_0", "event")
        ManifestPhase().run(ctx)
        data = _load(tmp_path / "manifest.json")
        assert data["event_count"] == 1

    def test_class_count_from_bible(self, tmp_path: Path):
        ctx = _make_minimal_ctx(tmp_path)
        ctx.bible.class_archetypes["warrior"] = ClassArchetype(
            archetype_id="warrior",
            name="Warrior",
        )
        ManifestPhase().run(ctx)
        data = _load(tmp_path / "manifest.json")
        assert data["class_count"] == 1

    def test_rooms_index_is_list(self, tmp_path: Path):
        ctx = _make_minimal_ctx(tmp_path, num_maps=2)
        ManifestPhase().run(ctx)
        data = _load(tmp_path / "manifest.json")
        assert isinstance(data["rooms"], list)
        assert len(data["rooms"]) == 2

    def test_rooms_index_has_required_fields(self, tmp_path: Path):
        ctx = _make_minimal_ctx(tmp_path)
        ManifestPhase().run(ctx)
        data = _load(tmp_path / "manifest.json")
        room = data["rooms"][0]
        assert "room_id" in room
        assert "environment" in room
        assert "environment_name" in room
        assert "npc_count" in room
        assert "event_count" in room
        assert "quest_count" in room

    def test_story_title_in_manifest(self, tmp_path: Path):
        ctx = _make_minimal_ctx(tmp_path)
        ManifestPhase().run(ctx)
        data = _load(tmp_path / "manifest.json")
        assert data["story_title"] == "Test World"

    def test_faction_name_in_manifest(self, tmp_path: Path):
        ctx = _make_minimal_ctx(tmp_path)
        ManifestPhase().run(ctx)
        data = _load(tmp_path / "manifest.json")
        assert data["faction_name"] == "Test Faction"

    def test_seed_in_manifest(self, tmp_path: Path):
        ctx = _make_minimal_ctx(tmp_path)
        ManifestPhase().run(ctx)
        data = _load(tmp_path / "manifest.json")
        assert data["story_seed"] == "manifest_test"

    def test_generation_stats_in_manifest(self, tmp_path: Path):
        ctx = _make_minimal_ctx(tmp_path)
        ManifestPhase().run(ctx)
        data = _load(tmp_path / "manifest.json")
        assert isinstance(data["generation_stats"], dict)
        assert "llm_calls" in data["generation_stats"]

    def test_music_paths_from_artifacts(self, tmp_path: Path):
        ctx = _make_minimal_ctx(tmp_path)
        ctx.artifacts["music_paths"] = {"room_0": "music/room_0.mp3"}
        ManifestPhase().run(ctx)
        data = _load(tmp_path / "manifest.json")
        assert data["music"]["room_0"] == "music/room_0.mp3"

    def test_sfx_paths_from_artifacts(self, tmp_path: Path):
        ctx = _make_minimal_ctx(tmp_path)
        ctx.artifacts["sfx_paths"] = {"ambient": "sfx/ambient.mp3"}
        ManifestPhase().run(ctx)
        data = _load(tmp_path / "manifest.json")
        assert data["sfx"]["ambient"] == "sfx/ambient.mp3"


# ---------------------------------------------------------------------------
# world_bible.json — required shape
# ---------------------------------------------------------------------------


class TestWorldBibleJson:
    def test_world_bible_json_is_written(self, tmp_path: Path):
        ctx = _make_minimal_ctx(tmp_path)
        ManifestPhase().run(ctx)
        assert (tmp_path / "world_bible.json").exists()

    def test_world_bible_has_story_and_rooms_keys(self, tmp_path: Path):
        ctx = _make_minimal_ctx(tmp_path)
        ManifestPhase().run(ctx)
        data = _load(tmp_path / "world_bible.json")
        assert "story" in data
        assert "rooms" in data

    def test_world_bible_story_is_dict(self, tmp_path: Path):
        ctx = _make_minimal_ctx(tmp_path)
        ManifestPhase().run(ctx)
        data = _load(tmp_path / "world_bible.json")
        assert isinstance(data["story"], dict)

    def test_world_bible_rooms_is_dict(self, tmp_path: Path):
        ctx = _make_minimal_ctx(tmp_path)
        ManifestPhase().run(ctx)
        data = _load(tmp_path / "world_bible.json")
        assert isinstance(data["rooms"], dict)

    def test_world_bible_room_has_required_fields(self, tmp_path: Path):
        ctx = _make_minimal_ctx(tmp_path)
        ManifestPhase().run(ctx)
        data = _load(tmp_path / "world_bible.json")
        room = data["rooms"]["room_0"]
        required = ["environment", "environment_name", "level", "story_beat"]
        for key in required:
            assert key in room, f"world_bible room missing: {key!r}"

    def test_world_bible_room_has_entity_arrays(self, tmp_path: Path):
        ctx = _make_minimal_ctx(tmp_path)
        ManifestPhase().run(ctx)
        data = _load(tmp_path / "world_bible.json")
        room = data["rooms"]["room_0"]
        for array_key in ["npcs", "items", "monsters", "events", "quests"]:
            assert array_key in room, f"world_bible room missing array: {array_key!r}"
            assert isinstance(room[array_key], list)

    def test_world_bible_entity_stub_shape(self, tmp_path: Path):
        ctx = _make_minimal_ctx(tmp_path)
        _add_entity(ctx.bible, "room_0", "npc", "Stormguard")
        ManifestPhase().run(ctx)
        data = _load(tmp_path / "world_bible.json")
        stubs = data["rooms"]["room_0"]["npcs"]
        assert len(stubs) == 1
        stub = stubs[0]
        assert "entity_type" in stub
        assert "entity_id" in stub
        assert "name" in stub
        assert stub["name"] == "Stormguard"

    def test_world_bible_entities_bucketed_by_type(self, tmp_path: Path):
        ctx = _make_minimal_ctx(tmp_path)
        _add_entity(ctx.bible, "room_0", "npc")
        _add_entity(ctx.bible, "room_0", "item")
        _add_entity(ctx.bible, "room_0", "monster")
        ManifestPhase().run(ctx)
        data = _load(tmp_path / "world_bible.json")
        room = data["rooms"]["room_0"]
        assert len(room["npcs"]) == 1
        assert len(room["items"]) == 1
        assert len(room["monsters"]) == 1
        assert len(room["events"]) == 0
        assert len(room["quests"]) == 0


# ---------------------------------------------------------------------------
# generation_stats.json — standalone stats file
# ---------------------------------------------------------------------------


class TestGenerationStatsJson:
    def test_generation_stats_json_is_written(self, tmp_path: Path):
        ctx = _make_minimal_ctx(tmp_path)
        ManifestPhase().run(ctx)
        assert (tmp_path / "generation_stats.json").exists()

    def test_generation_stats_json_matches_stats_to_dict(self, tmp_path: Path):
        ctx = _make_minimal_ctx(tmp_path)
        ctx.stats.record_call("story", input_tokens=100, output_tokens=50, cost=0.01)
        ManifestPhase().run(ctx)
        data = _load(tmp_path / "generation_stats.json")
        expected = ctx.stats.to_dict()
        # Check a few key values
        assert data["llm_calls"] == expected["llm_calls"]
        assert data["llm_calls"] == 1


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------


class TestManifestMetadata:
    def test_manifest_in_phases_run(self, tmp_path: Path):
        ctx = _make_minimal_ctx(tmp_path)
        ManifestPhase().run(ctx)
        assert "manifest" in ctx.bible.metadata.phases_run

    def test_manifest_appended_not_reset(self, tmp_path: Path):
        ctx = _make_minimal_ctx(tmp_path)
        ctx.bible.metadata.phases_run.append("story")
        ManifestPhase().run(ctx)
        assert "story" in ctx.bible.metadata.phases_run
        assert "manifest" in ctx.bible.metadata.phases_run


# ---------------------------------------------------------------------------
# Empty bible edge cases
# ---------------------------------------------------------------------------


class TestEmptyBible:
    def test_empty_bible_produces_valid_manifest(self, tmp_path: Path):
        """Empty bible (no maps, no characters) produces valid manifest with 0s."""
        bible = Bible.empty(seed="empty")
        config = CanonConfig(seed="empty", output_dir=str(tmp_path))
        ctx = PipelineContext(
            bible=bible,
            config=config,
            rng=random.Random(42),
            stats=GenerationStats(),
        )
        ManifestPhase().run(ctx)
        data = _load(tmp_path / "manifest.json")
        assert data["num_rooms"] == 0
        assert data["npc_count"] == 0
        assert data["quest_count"] == 0
        assert data["event_count"] == 0
        assert data["class_count"] == 0

    def test_empty_bible_world_bible_has_empty_rooms(self, tmp_path: Path):
        bible = Bible.empty(seed="empty")
        config = CanonConfig(seed="empty", output_dir=str(tmp_path))
        ctx = PipelineContext(
            bible=bible,
            config=config,
            rng=random.Random(42),
            stats=GenerationStats(),
        )
        ManifestPhase().run(ctx)
        data = _load(tmp_path / "world_bible.json")
        assert data["rooms"] == {}


# ---------------------------------------------------------------------------
# Output path override via output_paths
# ---------------------------------------------------------------------------


class TestOutputPathOverride:
    def test_custom_manifest_path(self, tmp_path: Path):
        ctx = _make_minimal_ctx(tmp_path)
        ctx.config.output_paths["manifest"] = "custom/my_manifest.json"
        ManifestPhase().run(ctx)
        assert (tmp_path / "custom" / "my_manifest.json").exists()

    def test_custom_world_bible_path(self, tmp_path: Path):
        ctx = _make_minimal_ctx(tmp_path)
        ctx.config.output_paths["world_bible"] = "custom/world.json"
        ManifestPhase().run(ctx)
        assert (tmp_path / "custom" / "world.json").exists()


# ---------------------------------------------------------------------------
# Fix 1 — _resolve_seed hook
# ---------------------------------------------------------------------------


class TestResolveSeedHook:
    """Verify that _resolve_seed is the extension point for seed coercion
    and that subclasses can override it without touching _write_manifest."""

    def test_default_resolve_seed_returns_config_seed(self, tmp_path: Path):
        ctx = _make_minimal_ctx(tmp_path)
        ctx.config.seed = "my_seed"
        phase = ManifestPhase()
        assert phase._resolve_seed(ctx) == "my_seed"

    def test_manifest_uses_resolve_seed_value(self, tmp_path: Path):
        """The manifest.json 'seed' field comes from _resolve_seed."""
        ctx = _make_minimal_ctx(tmp_path)
        ctx.config.seed = "original_seed"

        class SeedOverridePhase(ManifestPhase):
            def _resolve_seed(self, ctx):  # type: ignore[override]
                return 99999

        SeedOverridePhase().run(ctx)
        data = _load(tmp_path / "manifest.json")
        assert data["seed"] == 99999

    def test_subclass_can_coerce_seed_to_int(self, tmp_path: Path):
        """Subclass that coerces string seed to int works end-to-end."""
        import hashlib

        ctx = _make_minimal_ctx(tmp_path)
        ctx.config.seed = "shadowspire_001"

        class IntSeedPhase(ManifestPhase):
            def _resolve_seed(self, ctx):  # type: ignore[override]
                raw = getattr(ctx.config, "seed", "")
                if isinstance(raw, int):
                    return raw
                if isinstance(raw, str) and raw.isdigit():
                    return int(raw)
                return int(hashlib.md5(raw.encode()).hexdigest()[:8], 16)

        IntSeedPhase().run(ctx)
        data = _load(tmp_path / "manifest.json")
        assert isinstance(data["seed"], int), f"Expected int seed, got {type(data['seed'])}"


# ---------------------------------------------------------------------------
# Fix 6 — _scan_audio_dir
# ---------------------------------------------------------------------------


class TestScanAudioDir:
    """Verify that _scan_audio_dir scans a directory and returns stem → path dict."""

    def test_returns_empty_dict_when_dir_absent(self, tmp_path: Path):
        phase = ManifestPhase()
        result = phase._scan_audio_dir(tmp_path, "nonexistent_dir")
        assert result == {}

    def test_returns_empty_dict_when_dir_empty(self, tmp_path: Path):
        (tmp_path / "music").mkdir()
        phase = ManifestPhase()
        result = phase._scan_audio_dir(tmp_path, "music")
        assert result == {}

    def test_scans_mp3_files(self, tmp_path: Path):
        music_dir = tmp_path / "music"
        music_dir.mkdir()
        (music_dir / "combat.mp3").write_bytes(b"")
        (music_dir / "ambient.mp3").write_bytes(b"")
        phase = ManifestPhase()
        result = phase._scan_audio_dir(tmp_path, "music")
        assert "combat" in result
        assert "ambient" in result
        assert result["combat"].endswith("combat.mp3")

    def test_scans_wav_and_ogg(self, tmp_path: Path):
        sfx_dir = tmp_path / "sfx"
        sfx_dir.mkdir()
        (sfx_dir / "footstep.wav").write_bytes(b"")
        (sfx_dir / "door.ogg").write_bytes(b"")
        phase = ManifestPhase()
        result = phase._scan_audio_dir(tmp_path, "sfx")
        assert "footstep" in result
        assert "door" in result

    def test_ignores_non_audio_files(self, tmp_path: Path):
        music_dir = tmp_path / "music"
        music_dir.mkdir()
        (music_dir / "combat.mp3").write_bytes(b"")
        (music_dir / "README.txt").write_bytes(b"ignore me")
        phase = ManifestPhase()
        result = phase._scan_audio_dir(tmp_path, "music")
        assert "combat" in result
        assert "README" not in result

    def test_manifest_scans_music_dir_when_artifact_absent(self, tmp_path: Path):
        """When ctx.artifacts has no music_paths, manifest.music is populated
        by scanning the output music/ directory."""
        ctx = _make_minimal_ctx(tmp_path)
        music_dir = tmp_path / "music"
        music_dir.mkdir()
        (music_dir / "battle.mp3").write_bytes(b"")
        ManifestPhase().run(ctx)
        data = _load(tmp_path / "manifest.json")
        assert "battle" in data["music"]

    def test_artifact_music_paths_takes_priority_over_dir_scan(self, tmp_path: Path):
        """If ctx.artifacts already has music_paths, the dir scan is skipped."""
        ctx = _make_minimal_ctx(tmp_path)
        ctx.artifacts["music_paths"] = {"theme": "somewhere/theme.mp3"}
        music_dir = tmp_path / "music"
        music_dir.mkdir()
        (music_dir / "battle.mp3").write_bytes(b"")
        ManifestPhase().run(ctx)
        data = _load(tmp_path / "manifest.json")
        assert data["music"] == {"theme": "somewhere/theme.mp3"}
        assert "battle" not in data["music"]

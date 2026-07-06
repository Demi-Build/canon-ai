"""Tests for canon.bible.platformer — model shapes, Bible wiring, and
MazeWorld back-compat (a pre-Phase-1 Bible deserializes unchanged)."""

from __future__ import annotations

import json
from pathlib import Path

from canon.bible.artifacts import ArtifactStatus, PhaseStatus
from canon.bible.models import Bible
from canon.bible.platformer import (
    BossDefinition,
    EnemyDefinition,
    Level,
    Placement,
    RigManifest,
    RigPart,
    SparseMaskEntry,
    Stage,
    Tileset,
    TileSlot,
    TileType,
    World,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _sample_world() -> World:
    return World(
        artifact_id="world",
        title="Emberfall",
        stage_ids=["s1", "s2"],
        edges=[("s1", "s2")],
        unlock_rules={"s2": {"requires": "boss:ember_king"}},
    )


def _sample_level() -> Level:
    return Level(
        artifact_id="",  # levels decompose into step artifacts; manifest carries none
        level_id="l1",
        stage_id="s1",
        grid_width=200,
        grid_height=60,
        collision="level/s1/l1/collision.npz",
        collision_hash="sha256:aa",
        hazards=[SparseMaskEntry(x=4, y=9, type="spike")],
        triggers=[SparseMaskEntry(x=0, y=0, type="checkpoint", params={"id": 1})],
        entities=[
            Placement(ref="enemy:goblin_grunt", pos=(12, 40)),
            Placement(
                ref="enemy:goblin_grunt",
                pos=(80, 40),
                overrides={"elite": True},
            ),
        ],
        parents=["level:s1/l1/collision", "level:s1/l1/hazards"],
    )


def _sample_enemy() -> EnemyDefinition:
    return EnemyDefinition(
        artifact_id="enemy:goblin_grunt",
        enemy_id="goblin_grunt",
        name="Goblin Grunt",
        archetype="walker",
        stats={"hp": 12, "speed": 2},
        behavior={"patrol_range": 6},
        rig=RigManifest(
            rig_type="biped",
            parts=[RigPart(name="body", image_path="portrait/enemy/goblin_body.png")],
            anchors={"hand_r": (14, 22)},
        ),
        portrait_path="portrait/enemy/goblin_grunt.png",
    )


class TestModelShapes:
    def test_definitions_round_trip(self) -> None:
        for model in (
            _sample_world(),
            Stage(
                artifact_id="stage:s1",
                stage_id="s1",
                theme="ember caves",
                enemy_refs=["enemy:goblin_grunt"],
                boss_ref="boss:ember_king",
                level_ids=["l1"],
                tileset_ref="tileset:s1",
            ),
            _sample_level(),
            _sample_enemy(),
            BossDefinition(
                artifact_id="boss:ember_king",
                enemy_id="ember_king",
                phases=[{"hp_pct": 50, "behavior": "enrage"}],
                arena={"width": 30, "height": 20},
            ),
            Tileset(
                artifact_id="tileset:s1",
                stage_id="s1",
                tilesheet_path="tileset/s1/tilesheet.png",
                slots=[TileSlot(index=0, tile_type=TileType.FLOOR)],
            ),
        ):
            dumped = model.model_dump(mode="json")
            json.dumps(dumped)  # JSON-serializable
            assert type(model).model_validate(dumped) == model

    def test_boss_is_an_enemy_definition(self) -> None:
        assert isinstance(BossDefinition(enemy_id="x"), EnemyDefinition)

    def test_placement_references_not_copies(self) -> None:
        p = Placement(ref="enemy:goblin_grunt", pos=(1, 2))
        dumped = p.model_dump()
        assert dumped["ref"] == "enemy:goblin_grunt"
        assert "stats" not in dumped  # a placement never embeds definition data

    def test_artifact_meta_inherited(self) -> None:
        enemy = _sample_enemy()
        assert enemy.status is ArtifactStatus.PENDING
        assert enemy.parents == []

    def test_tile_type_values(self) -> None:
        assert TileType.EMPTY == 0
        assert TileType.FLOOR == 1
        assert TileType.PLATFORM == 2
        assert TileType.SPIKE == 10

    def test_level_has_no_inline_grid_field(self) -> None:
        """§6.2 guard: dense grids are referenced, never embedded."""
        for field_name, field in Level.model_fields.items():
            assert "list[list" not in str(field.annotation), (
                f"Level.{field_name} looks like an inline grid; dense masks "
                "must be .npz references (PRD §6.2)."
            )


class TestBibleWiring:
    def test_platformer_bible_round_trips(self) -> None:
        bible = Bible.empty(seed="plat")
        bible.world = _sample_world()
        bible.stages["s1"] = Stage(stage_id="s1")
        bible.levels["l1"] = _sample_level()
        bible.enemy_definitions["goblin_grunt"] = _sample_enemy()
        bible.boss_definitions["ember_king"] = BossDefinition(enemy_id="ember_king")
        bible.tilesets["s1"] = Tileset(stage_id="s1")
        bible.metadata.phase_status["world"] = PhaseStatus.DONE

        again = Bible.model_validate(json.loads(bible.model_dump_json()))
        assert again.world == bible.world
        assert again.levels["l1"].entities[1].overrides == {"elite": True}
        assert again.metadata.phase_status["world"] is PhaseStatus.DONE
        # MazeWorld fields untouched and empty
        assert again.maps == {}

    def test_pre_phase1_bible_still_loads(self) -> None:
        """Back-compat: a Bible serialized before the platformer fields
        existed deserializes with empty platformer defaults."""
        data = json.loads((FIXTURES / "sample_bible.json").read_text())
        assert "world" not in data or data.get("world") is None
        bible = Bible.model_validate(data)
        assert bible.world is None
        assert bible.stages == {}
        assert bible.enemy_definitions == {}
        assert bible.metadata.phase_status == {}

    def test_empty_bible_serializes_without_platformer_noise(self) -> None:
        """MazeWorld Bibles gain the new keys with empty values — additive,
        and existing consumers that iterate known keys are unaffected."""
        dumped = Bible.empty(seed="x").model_dump(mode="json")
        assert dumped["world"] is None
        assert dumped["stages"] == {}
        assert dumped["levels"] == {}

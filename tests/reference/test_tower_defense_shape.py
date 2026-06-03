"""Reference pipeline test: tower-defense (non-fantasy, character-less,
dialogue-less). Proves canon is genuinely game-agnostic — composition skips
dialogue and characters entirely; the resulting bible is empty in those slots
with no errors.
"""
import random

import pytest

from canon import (
    Bible,
    BibleMetadata,
    CanonConfig,
    ClassPhase,
    DefaultPromptSet,
    EntityPhase,
    FakeLLMBackend,
    GenerationStats,
    LLMClient,
    Map,
    PipelineContext,
    StoryPhase,
    ValidationPhase,
    run_pipeline,
)
from tests.reference.tower_defense_data import (
    ARCHER_ARCHETYPE,
    ENEMY_WAVE_SPEC,
    MAGE_ARCHETYPE,
    TOWER_SPEC,
    TRAP_ARCHETYPE,
    build_canned_responses,
)


@pytest.fixture
def tower_defense_outputs(tmp_path):
    """Run a full tower-defense-shape pipeline and return the resulting bible.

    Composition uses only StoryPhase, ClassPhase, EntityPhase(tower),
    EntityPhase(enemy_wave), ValidationPhase. No CharacterPhase, no
    DialoguePhase, no NarrativePhase.
    """
    seed = "defend_the_hold_007"
    num_maps = 3
    towers_per_stage = 3
    waves_per_stage = 4

    bible = Bible.empty(seed=seed)
    bible.metadata = BibleMetadata()
    for i in range(num_maps):
        bible.maps[f"stage_{i}"] = Map(
            map_id=f"stage_{i}",
            name=f"Stage {i}",
            description=f"Defensive stage {i}.",
            environment=["plains", "forest_pass", "keep_walls"][i],
            level=i + 1,
            story_beat=f"beat_{i}",
        )

    config = CanonConfig(seed=seed, num_maps=num_maps, output_dir=tmp_path)
    stats = GenerationStats()
    responses = build_canned_responses(
        num_maps=num_maps,
        towers_per_stage=towers_per_stage,
        waves_per_stage=waves_per_stage,
    )
    backend = FakeLLMBackend(responses)
    llm = LLMClient(backend, stats=stats)
    prompts = DefaultPromptSet()

    ctx = PipelineContext(
        bible=bible,
        config=config,
        rng=random.Random(seed),
        llm=llm,
        prompts=prompts,
        stats=stats,
        schemas={"tower": TOWER_SPEC, "enemy_wave": ENEMY_WAVE_SPEC},
        archetypes={
            "archer_tower": ARCHER_ARCHETYPE,
            "mage_tower": MAGE_ARCHETYPE,
            "trap_tower": TRAP_ARCHETYPE,
        },
    )

    bible_out = run_pipeline(
        phases=[
            StoryPhase(),
            ClassPhase(archetype_count=3, instantiate_for_roles=()),
            EntityPhase(entity_type="tower", per_map=True, count=towers_per_stage),
            EntityPhase(entity_type="enemy_wave", per_map=True, count=waves_per_stage),
            ValidationPhase(),
        ],
        ctx=ctx,
    )
    return bible_out, ctx, backend


class TestTowerDefenseShapeAcceptance:
    def test_pipeline_runs_to_completion(self, tower_defense_outputs):
        bible, ctx, _ = tower_defense_outputs
        assert "story" in bible.metadata.phases_run
        assert "classes" in bible.metadata.phases_run
        assert "entity:tower" in bible.metadata.phases_run
        assert "entity:enemy_wave" in bible.metadata.phases_run
        # ValidationPhase stores its result in ctx.artifacts, not phases_run —
        # it is the only phase that does not stamp phases_run directly.
        assert "validation_report" in ctx.artifacts

    def test_no_characters_phase_run(self, tower_defense_outputs):
        bible, _, _ = tower_defense_outputs
        # CharacterPhase wasn't composed
        assert "characters" not in bible.metadata.phases_run

    def test_no_dialogue_phase_run(self, tower_defense_outputs):
        bible, _, _ = tower_defense_outputs
        assert "dialogue" not in bible.metadata.phases_run

    def test_no_narrative_phase_run(self, tower_defense_outputs):
        bible, _, _ = tower_defense_outputs
        assert "narrative" not in bible.metadata.phases_run

    def test_zero_characters(self, tower_defense_outputs):
        bible, _, _ = tower_defense_outputs
        assert bible.characters == []

    def test_zero_dialogues(self, tower_defense_outputs):
        bible, _, _ = tower_defense_outputs
        assert bible.dialogues == {}

    def test_story_arc_populated(self, tower_defense_outputs):
        bible, _, _ = tower_defense_outputs
        assert bible.story.title == "Defend the Hold"
        assert len(bible.story.factions) == 1
        assert len(bible.story.beats) == 3
        assert len(bible.story.escalation_arc) == 4

    def test_archetypes_generated(self, tower_defense_outputs):
        bible, _, _ = tower_defense_outputs
        assert len(bible.class_archetypes) == 3
        assert "archer_tower" in bible.class_archetypes
        assert "mage_tower" in bible.class_archetypes
        assert "trap_tower" in bible.class_archetypes

    def test_towers_per_stage(self, tower_defense_outputs):
        bible, _, _ = tower_defense_outputs
        for stage_id in ["stage_0", "stage_1", "stage_2"]:
            entities = bible.maps[stage_id].entities
            towers = [e for e in entities if e.entity_type == "tower"]
            assert len(towers) == 3

    def test_waves_per_stage(self, tower_defense_outputs):
        bible, _, _ = tower_defense_outputs
        for stage_id in ["stage_0", "stage_1", "stage_2"]:
            entities = bible.maps[stage_id].entities
            waves = [e for e in entities if e.entity_type == "enemy_wave"]
            assert len(waves) == 4

    def test_skeletons_persisted_for_towers(self, tower_defense_outputs):
        bible, _, _ = tower_defense_outputs
        for map_obj in bible.maps.values():
            for entity in map_obj.entities:
                if entity.entity_type == "tower":
                    assert "tower_type" in entity.skeleton
                    assert "damage" in entity.skeleton
                    assert "range" in entity.skeleton

    def test_bible_persists_and_loads(self, tower_defense_outputs, tmp_path):
        bible, _, _ = tower_defense_outputs
        bible_path = tmp_path / "tower_defense.json"
        bible.persist(bible_path)
        loaded = Bible.load(bible_path)
        assert loaded.seed == bible.seed
        assert len(loaded.maps) == 3
        assert loaded.characters == []
        assert loaded.dialogues == {}

    def test_validation_report_is_empty_no_op(self, tower_defense_outputs):
        bible, ctx, _ = tower_defense_outputs
        # No checkers/validators registered, so validation is a no-op
        report = ctx.artifacts.get("validation_report")
        assert report is not None
        # Empty report means it ran cleanly
        assert report.passed  # nothing to fail

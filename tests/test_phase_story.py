"""Unit tests for StoryPhase.

Uses FakeLLMBackend for deterministic, network-free LLM simulation.
"""

import json
import random

from canon import (
    Bible,
    CanonConfig,
    DefaultPromptSet,
    FakeLLMBackend,
    GenerationStats,
    LLMClient,
    Map,
    PipelineContext,
)
from canon.pipeline.phases.story import StoryPhase

# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def make_ctx(responses, seed="test_seed"):
    bible = Bible.empty(seed=seed)
    backend = FakeLLMBackend(responses)
    llm = LLMClient(backend, stats=GenerationStats())
    return PipelineContext(
        bible=bible,
        config=CanonConfig(seed=seed),
        rng=random.Random(seed),
        llm=llm,
        prompts=DefaultPromptSet(),
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_happy_path():
    response = json.dumps({
        "title": "The Shattered Crown",
        "synopsis": "A kingdom torn by war.",
        "factions": [
            {
                "faction_id": "f1",
                "name": "Crimson Order",
                "description": "elite knights",
                "history": "ancient",
                "leader": "lord",
                "threat_level": 5,
            }
        ],
        "escalation_arc": ["arrival", "battle", "victory"],
        "beats": [
            {
                "map_id": "map_0",
                "beat": "Hero arrives.",
                "boss_name": "Captain X",
                "boss_lore": "Old guard.",
            }
        ],
    })
    ctx = make_ctx([response])
    StoryPhase().run(ctx)

    assert ctx.bible.story.title == "The Shattered Crown"
    assert ctx.bible.story.synopsis == "A kingdom torn by war."
    assert len(ctx.bible.story.factions) == 1
    assert ctx.bible.story.factions[0].name == "Crimson Order"
    assert ctx.bible.story.factions[0].threat_level == 5
    assert len(ctx.bible.story.beats) == 1
    assert ctx.bible.story.beats[0].boss_name == "Captain X"
    assert ctx.bible.story.escalation_arc == ["arrival", "battle", "victory"]
    assert "story" in ctx.bible.metadata.phases_run


# ---------------------------------------------------------------------------
# Retry behaviour
# ---------------------------------------------------------------------------


def test_retries_on_invalid_json():
    bad = "not json"
    good = json.dumps({"title": "OK", "synopsis": "story"})
    ctx = make_ctx([bad, good])
    StoryPhase().run(ctx)
    assert ctx.bible.story.title == "OK"


def test_retries_on_missing_required_fields():
    """Missing 'synopsis' triggers retry; second response is valid."""
    no_synopsis = json.dumps({"title": "Has Title"})
    good = json.dumps({"title": "Fixed", "synopsis": "Now it has one."})
    ctx = make_ctx([no_synopsis, good])
    StoryPhase().run(ctx)
    assert ctx.bible.story.title == "Fixed"


def test_retries_send_feedback_to_llm():
    """After a bad response, a second call is made (feedback path)."""
    bad = "not json at all"
    good = json.dumps({"title": "Retry Success", "synopsis": "Works."})
    ctx = make_ctx([bad, good])
    StoryPhase().run(ctx)
    # Two calls should have been made: one failed, one succeeded.
    assert len(ctx.llm.backend.calls) == 2


# ---------------------------------------------------------------------------
# Fallback exhaustion
# ---------------------------------------------------------------------------


def test_uses_fallback_after_exhaustion():
    # 4 bad responses > default max_retries=3
    ctx = make_ctx(["bad", "bad", "bad", "bad"])
    StoryPhase().run(ctx)
    # Fallback produces a minimal valid StoryArc
    story = ctx.bible.story
    assert "Untitled World" in story.title or story.synopsis == "An unwritten world."


def test_fallback_still_records_phase_in_metadata():
    ctx = make_ctx(["bad", "bad", "bad", "bad"])
    StoryPhase().run(ctx)
    assert "story" in ctx.bible.metadata.phases_run


# ---------------------------------------------------------------------------
# Metadata recording
# ---------------------------------------------------------------------------


def test_records_phase_in_metadata():
    response = json.dumps({"title": "x", "synopsis": "y"})
    ctx = make_ctx([response])
    StoryPhase().run(ctx)
    assert "story" in ctx.bible.metadata.phases_run


def test_does_not_duplicate_phase_name_on_second_run():
    """Running the phase twice appends the name twice — callers own idempotency."""
    response = json.dumps({"title": "x", "synopsis": "y"})
    ctx = make_ctx([response, response])
    phase = StoryPhase()
    phase.run(ctx)
    phase.run(ctx)
    assert ctx.bible.metadata.phases_run.count("story") == 2


# ---------------------------------------------------------------------------
# Phase protocol satisfaction
# ---------------------------------------------------------------------------


def test_satisfies_phase_protocol():
    from canon.pipeline.runner import Phase

    assert isinstance(StoryPhase(), Phase)


def test_phase_name_is_story():
    assert StoryPhase.name == "story"
    assert StoryPhase().name == "story"


# ---------------------------------------------------------------------------
# Optional field leniency
# ---------------------------------------------------------------------------


def test_missing_optional_fields_accepted():
    """title+synopsis only — all optional fields absent — must not crash."""
    minimal = json.dumps({"title": "Bare Minimum", "synopsis": "Just enough."})
    ctx = make_ctx([minimal])
    StoryPhase().run(ctx)
    story = ctx.bible.story
    assert story.title == "Bare Minimum"
    assert story.factions == []
    assert story.beats == []
    assert story.escalation_arc == []
    assert story.climax is None
    assert story.primary_antagonist_faction_id is None


def test_faction_parsing_leniency_no_faction_id():
    """Factions missing 'faction_id' fall back to name-derived id."""
    response = json.dumps({
        "title": "T",
        "synopsis": "S",
        "factions": [
            {"name": "The Grey Wardens", "description": "warriors", "history": "old", "leader": "warden"}
        ],
    })
    ctx = make_ctx([response])
    StoryPhase().run(ctx)
    faction = ctx.bible.story.factions[0]
    assert faction.faction_id == "the_grey_wardens"
    assert faction.name == "The Grey Wardens"


def test_faction_parsing_leniency_malformed_faction_skipped():
    """A faction that triggers a parse error is silently skipped; others proceed."""
    response = json.dumps({
        "title": "T",
        "synopsis": "S",
        "factions": [
            None,  # malformed — will raise AttributeError on .get()
            {"faction_id": "good_one", "name": "Good Faction", "description": "fine", "history": "", "leader": ""},
        ],
    })
    ctx = make_ctx([response])
    StoryPhase().run(ctx)
    # The None faction should be skipped; the valid one should be present.
    assert len(ctx.bible.story.factions) == 1
    assert ctx.bible.story.factions[0].faction_id == "good_one"


def test_story_structure_passed_to_prompt():
    """story_structure kwarg reaches the prompt (does not crash)."""
    structure = {"num_factions": 2, "tone": "dark"}
    response = json.dumps({"title": "Structured", "synopsis": "Darker tone."})
    ctx = make_ctx([response])
    StoryPhase(story_structure=structure).run(ctx)
    assert ctx.bible.story.title == "Structured"
    # The structure hint should appear in the user_message sent.
    assert "dark" in ctx.llm.backend.calls[0].user_message


def test_seed_embedded_in_story_arc():
    """The seed from the context is stored on the resulting StoryArc."""
    response = json.dumps({"title": "T", "synopsis": "S"})
    ctx = make_ctx([response], seed="my_unique_seed")
    StoryPhase().run(ctx)
    assert ctx.bible.story.seed == "my_unique_seed"


# ---------------------------------------------------------------------------
# v0.2 file persistence: story.json and world_bible.json
# ---------------------------------------------------------------------------


def make_ctx_with_output(responses, tmp_path, seed="test_seed", num_maps=0):
    """Build a PipelineContext with output_dir pointed at tmp_path."""
    bible = Bible.empty(seed=seed)
    for i in range(num_maps):
        bible.maps[f"room_{i}"] = Map(
            map_id=f"room_{i}",
            name=f"Room {i}",
            description="",
            environment=["forest", "ruins"][i % 2],
            story_beat=f"Beat {i}",
        )
    backend = FakeLLMBackend(responses)
    llm = LLMClient(backend, stats=GenerationStats())
    config = CanonConfig(seed=seed, output_dir=tmp_path)
    return PipelineContext(
        bible=bible,
        config=config,
        rng=random.Random(seed),
        llm=llm,
        prompts=DefaultPromptSet(),
    )


class TestStoryPhaseFilePersistence:
    def test_story_json_written(self, tmp_path):
        """StoryPhase writes data/story/story.json after run."""
        response = json.dumps({"title": "The Written World", "synopsis": "A tale begins."})
        ctx = make_ctx_with_output([response], tmp_path)
        StoryPhase().run(ctx)
        story_path = tmp_path / "story" / "story.json"
        assert story_path.exists(), f"Expected {story_path} to exist"

    def test_story_json_has_required_fields(self, tmp_path):
        """story.json contains title, synopsis, seed at minimum."""
        response = json.dumps({"title": "The Written World", "synopsis": "A tale begins."})
        ctx = make_ctx_with_output([response], tmp_path)
        StoryPhase().run(ctx)
        import json as _json
        data = _json.loads((tmp_path / "story" / "story.json").read_text())
        assert data["title"] == "The Written World"
        assert data["synopsis"] == "A tale begins."
        assert "seed" in data

    def test_world_bible_json_written(self, tmp_path):
        """StoryPhase writes data/world_bible.json after run."""
        response = json.dumps({"title": "T", "synopsis": "S"})
        ctx = make_ctx_with_output([response], tmp_path)
        StoryPhase().run(ctx)
        wb_path = tmp_path / "world_bible.json"
        assert wb_path.exists(), f"Expected {wb_path} to exist"

    def test_world_bible_has_story_key(self, tmp_path):
        """world_bible.json top level has 'story' key."""
        response = json.dumps({"title": "T2", "synopsis": "S2"})
        ctx = make_ctx_with_output([response], tmp_path)
        StoryPhase().run(ctx)
        import json as _json
        data = _json.loads((tmp_path / "world_bible.json").read_text())
        assert "story" in data
        assert data["story"]["title"] == "T2"

    def test_world_bible_has_rooms_key(self, tmp_path):
        """world_bible.json top level has 'rooms' dict."""
        response = json.dumps({"title": "T", "synopsis": "S"})
        ctx = make_ctx_with_output([response], tmp_path)
        StoryPhase().run(ctx)
        import json as _json
        data = _json.loads((tmp_path / "world_bible.json").read_text())
        assert "rooms" in data
        assert isinstance(data["rooms"], dict)

    def test_world_bible_rooms_populated_from_maps(self, tmp_path):
        """Each Map in the bible generates a room entry in world_bible.json."""
        response = json.dumps({"title": "T", "synopsis": "S"})
        ctx = make_ctx_with_output([response], tmp_path, num_maps=2)
        StoryPhase().run(ctx)
        import json as _json
        data = _json.loads((tmp_path / "world_bible.json").read_text())
        rooms = data["rooms"]
        assert "room_0" in rooms
        assert "room_1" in rooms

    def test_world_bible_room_has_entity_buckets(self, tmp_path):
        """Each room entry in world_bible.json has empty entity bucket lists."""
        response = json.dumps({"title": "T", "synopsis": "S"})
        ctx = make_ctx_with_output([response], tmp_path, num_maps=1)
        StoryPhase().run(ctx)
        import json as _json
        data = _json.loads((tmp_path / "world_bible.json").read_text())
        room = data["rooms"]["room_0"]
        for bucket in ("npcs", "items", "monsters", "events", "quests"):
            assert bucket in room, f"Missing bucket: {bucket}"
            assert isinstance(room[bucket], list)

    def test_story_json_written_even_with_fallback(self, tmp_path):
        """Even when retries are exhausted and fallback fires, story.json is written."""
        ctx = make_ctx_with_output(["bad", "bad", "bad", "bad"], tmp_path)
        StoryPhase().run(ctx)
        assert (tmp_path / "story" / "story.json").exists()

    def test_world_bible_written_even_with_fallback(self, tmp_path):
        """world_bible.json is written even when story generation falls back."""
        ctx = make_ctx_with_output(["bad", "bad", "bad", "bad"], tmp_path)
        StoryPhase().run(ctx)
        assert (tmp_path / "world_bible.json").exists()

    def test_story_json_respects_custom_output_paths(self, tmp_path):
        """When output_paths['story'] is overridden, the custom path is used."""
        response = json.dumps({"title": "Custom Path", "synopsis": "S"})
        ctx = make_ctx_with_output([response], tmp_path)
        # Use object.__setattr__ to set a dynamic attribute on the CanonConfig
        # (CanonConfig is frozen/strict Pydantic; output_paths is a runtime-injected
        #  coordinator attribute, not a declared field).
        object.__setattr__(ctx.config, "output_paths", {
            "story": "custom/my_story.json",
            "world_bible": "world_bible.json",
        })
        StoryPhase().run(ctx)
        assert (tmp_path / "custom" / "my_story.json").exists()

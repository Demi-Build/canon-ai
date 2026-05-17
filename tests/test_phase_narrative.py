"""Unit tests for NarrativePhase.

Uses FakeLLMBackend for deterministic, network-free LLM simulation.
Narrative responses are JSON-wrapped prose (matching DefaultPromptSet format).

Response budget for a 2-map run:
  1 (synopsis) + 2 (map intros) + 1 (victory) + 1 (defeat) = 5 responses.

v0.2 additions: after run(), data/narrative.json is written with mazeworld shape.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

from canon import (
    Bible,
    CanonConfig,
    DefaultPromptSet,
    FakeLLMBackend,
    GenerationStats,
    LLMClient,
    Map,
    PipelineContext,
    StoryArc,
)
from canon.pipeline.phases.narrative import NarrativePhase

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _synopsis_json(text: str) -> str:
    return json.dumps({"synopsis": text})


def _intro_json(text: str) -> str:
    return json.dumps({"intro_text": text})


def _victory_json(text: str) -> str:
    return json.dumps({"victory_text": text})


def _defeat_json(text: str) -> str:
    return json.dumps({"defeat_text": text})


def make_ctx(responses, *, with_maps: int = 2, seed: str = "t",
             output_dir: str | None = None) -> PipelineContext:
    """Build a PipelineContext wired to a FakeLLMBackend.

    ``responses`` is a list of strings returned in order.
    For *with_maps* == 2 you need exactly 5 responses:
        synopsis, map_0 intro, map_1 intro, victory, defeat.
    """
    bible = Bible.empty(seed=seed)
    bible.story = StoryArc(title="Test World", synopsis="initial synopsis", seed=seed)
    for i in range(with_maps):
        bible.maps[f"map_{i}"] = Map(
            map_id=f"map_{i}",
            name=f"Map {i}",
            description="initial description",
            environment="forest",
            story_beat="",
        )
    backend = FakeLLMBackend(responses)
    llm = LLMClient(backend, stats=GenerationStats())
    config = CanonConfig(seed=seed)
    if output_dir is not None:
        config = CanonConfig(seed=seed, output_dir=output_dir)
    return PipelineContext(
        bible=bible,
        config=config,
        rng=random.Random(seed),
        llm=llm,
        prompts=DefaultPromptSet(),
    )


def _five_responses(synopsis="S.", intro0="I0.", intro1="I1.", victory="V.", defeat="D."):
    return [
        _synopsis_json(synopsis),
        _intro_json(intro0),
        _intro_json(intro1),
        _victory_json(victory),
        _defeat_json(defeat),
    ]


# ---------------------------------------------------------------------------
# Phase protocol
# ---------------------------------------------------------------------------


def test_phase_protocol_satisfaction():
    from canon.pipeline.runner import Phase

    assert isinstance(NarrativePhase(), Phase)


def test_phase_name_is_narrative():
    assert NarrativePhase.name == "narrative"
    assert NarrativePhase().name == "narrative"


# ---------------------------------------------------------------------------
# Happy path — synopsis
# ---------------------------------------------------------------------------


def test_synopsis_is_updated():
    """LLM-generated synopsis overwrites the initial structural synopsis."""
    responses = [
        _synopsis_json("A polished world-spanning adventure."),
        _intro_json("You stand at the edge of an ancient forest."),
        _intro_json("The city hums with restless energy."),
        _victory_json("You have saved the realm!"),
        _defeat_json("Darkness consumes you."),
    ]
    ctx = make_ctx(responses)
    NarrativePhase().run(ctx)
    assert ctx.bible.story.synopsis == "A polished world-spanning adventure."


def test_synopsis_stored_in_artifacts():
    responses = [
        _synopsis_json("A grand tale."),
        _intro_json("Intro 0."),
        _intro_json("Intro 1."),
        _victory_json("You win."),
        _defeat_json("You lose."),
    ]
    ctx = make_ctx(responses)
    NarrativePhase().run(ctx)
    assert ctx.artifacts["narrative"]["synopsis"] == "A grand tale."


# ---------------------------------------------------------------------------
# Happy path — per-map intros
# ---------------------------------------------------------------------------


def test_map_descriptions_are_updated():
    """Each map's description is replaced with the generated intro text."""
    responses = [
        _synopsis_json("Synopsis."),
        _intro_json("Dark entrance to the dungeon."),
        _intro_json("Glittering treasure vault."),
        _victory_json("Victory!"),
        _defeat_json("Defeat."),
    ]
    ctx = make_ctx(responses)
    NarrativePhase().run(ctx)
    assert ctx.bible.maps["map_0"].description == "Dark entrance to the dungeon."
    assert ctx.bible.maps["map_1"].description == "Glittering treasure vault."


def test_map_intros_stored_in_artifacts():
    responses = [
        _synopsis_json("Synopsis."),
        _intro_json("Intro A."),
        _intro_json("Intro B."),
        _victory_json("V."),
        _defeat_json("D."),
    ]
    ctx = make_ctx(responses)
    NarrativePhase().run(ctx)
    intros = ctx.artifacts["narrative"]["map_intros"]
    assert intros["map_0"] == "Intro A."
    assert intros["map_1"] == "Intro B."


def test_correct_number_of_map_intros():
    responses = [
        _synopsis_json("S."),
        _intro_json("I0."),
        _intro_json("I1."),
        _victory_json("V."),
        _defeat_json("D."),
    ]
    ctx = make_ctx(responses)
    NarrativePhase().run(ctx)
    assert len(ctx.artifacts["narrative"]["map_intros"]) == 2


# ---------------------------------------------------------------------------
# Happy path — victory / defeat
# ---------------------------------------------------------------------------


def test_victory_text_stored_in_artifacts():
    responses = [
        _synopsis_json("S."),
        _intro_json("I0."),
        _intro_json("I1."),
        _victory_json("You have triumphed over all odds!"),
        _defeat_json("D."),
    ]
    ctx = make_ctx(responses)
    NarrativePhase().run(ctx)
    assert ctx.artifacts["narrative"]["victory_text"] == "You have triumphed over all odds!"


def test_defeat_text_stored_in_artifacts():
    responses = [
        _synopsis_json("S."),
        _intro_json("I0."),
        _intro_json("I1."),
        _victory_json("V."),
        _defeat_json("The shadows claim you forever."),
    ]
    ctx = make_ctx(responses)
    NarrativePhase().run(ctx)
    assert ctx.artifacts["narrative"]["defeat_text"] == "The shadows claim you forever."


def test_victory_defeat_not_on_bible_story():
    """Victory/defeat live only in ctx.artifacts for v0.1 — not on Bible.story."""
    responses = [
        _synopsis_json("S."),
        _intro_json("I0."),
        _intro_json("I1."),
        _victory_json("V."),
        _defeat_json("D."),
    ]
    ctx = make_ctx(responses)
    NarrativePhase().run(ctx)
    # Confirm these are NOT accidentally assigned as attributes on StoryArc
    assert not hasattr(ctx.bible.story, "victory_text")
    assert not hasattr(ctx.bible.story, "defeat_text")


# ---------------------------------------------------------------------------
# Metadata recording
# ---------------------------------------------------------------------------


def test_narrative_in_phases_run():
    responses = [
        _synopsis_json("S."),
        _intro_json("I0."),
        _intro_json("I1."),
        _victory_json("V."),
        _defeat_json("D."),
    ]
    ctx = make_ctx(responses)
    NarrativePhase().run(ctx)
    assert "narrative" in ctx.bible.metadata.phases_run


def test_phases_run_appended_not_replaced():
    """If metadata already has phases listed, 'narrative' is appended."""
    ctx = make_ctx(
        [
            _synopsis_json("S."),
            _intro_json("I0."),
            _intro_json("I1."),
            _victory_json("V."),
            _defeat_json("D."),
        ]
    )
    ctx.bible.metadata.phases_run.append("story")
    NarrativePhase().run(ctx)
    assert ctx.bible.metadata.phases_run == ["story", "narrative"]


# ---------------------------------------------------------------------------
# Fallback behaviour on empty / blank responses
# ---------------------------------------------------------------------------


def test_empty_synopsis_response_uses_fallback():
    """An empty string from the LLM triggers fallback to the original synopsis.

    Retry responses are consumed immediately after the first failure, before
    map intros — so the ordering must be: empty, empty, empty, then map intros.
    """
    responses = [
        "",  # synopsis attempt 1 — fails validation
        "",  # synopsis attempt 2 — fails validation
        "",  # synopsis attempt 3 — fails validation → fallback fires
        _intro_json("I0."),
        _intro_json("I1."),
        _victory_json("V."),
        _defeat_json("D."),
    ]
    ctx = make_ctx(responses)
    NarrativePhase().run(ctx)
    # The fallback is the original 'initial synopsis'
    assert ctx.bible.story.synopsis == "initial synopsis"


def test_empty_victory_response_uses_fallback():
    """All-empty victory responses fall back to the hardcoded default."""
    responses = [
        _synopsis_json("S."),
        _intro_json("I0."),
        _intro_json("I1."),
        "",  # victory attempt 1
        "",  # victory attempt 2
        "",  # victory attempt 3 — exhausted; fallback fires
        _defeat_json("D."),
    ]
    ctx = make_ctx(responses)
    NarrativePhase().run(ctx)
    assert ctx.artifacts["narrative"]["victory_text"] == "You have triumphed."


def test_empty_defeat_response_uses_fallback():
    """All-empty defeat responses fall back to the hardcoded default."""
    responses = [
        _synopsis_json("S."),
        _intro_json("I0."),
        _intro_json("I1."),
        _victory_json("V."),
        "",  # defeat attempt 1
        "",  # defeat attempt 2
        "",  # defeat attempt 3
    ]
    ctx = make_ctx(responses)
    NarrativePhase().run(ctx)
    assert ctx.artifacts["narrative"]["defeat_text"] == "You have fallen."


# ---------------------------------------------------------------------------
# Retry / feedback path
# ---------------------------------------------------------------------------


def test_retry_on_blank_synopsis_then_succeeds():
    """A blank response triggers a retry; the second call succeeds."""
    responses = [
        "   ",  # blank — fails validation
        _synopsis_json("Retried and polished synopsis."),
        _intro_json("I0."),
        _intro_json("I1."),
        _victory_json("V."),
        _defeat_json("D."),
    ]
    ctx = make_ctx(responses)
    NarrativePhase().run(ctx)
    assert ctx.bible.story.synopsis == "Retried and polished synopsis."
    # Two calls were made for synopsis (one bad + one good)
    assert len(ctx.llm.backend.calls) == 6


def test_retry_sends_second_call():
    """After a blank map intro, a second LLM call is made."""
    responses = [
        _synopsis_json("S."),
        "   ",  # map_0 intro — blank triggers retry
        _intro_json("Second try intro."),
        _intro_json("I1."),
        _victory_json("V."),
        _defeat_json("D."),
    ]
    ctx = make_ctx(responses)
    NarrativePhase().run(ctx)
    assert ctx.bible.maps["map_0"].description == "Second try intro."
    # 6 calls total: synopsis + 2 map_0 attempts + map_1 + victory + defeat
    assert len(ctx.llm.backend.calls) == 6


# ---------------------------------------------------------------------------
# Zero-map edge case
# ---------------------------------------------------------------------------


def test_zero_maps_still_produces_synopsis_victory_defeat():
    """Phase works with no maps: synopsis/victory/defeat still generated."""
    responses = [
        _synopsis_json("An empty world."),
        _victory_json("Victory in the void."),
        _defeat_json("Fallen in the void."),
    ]
    ctx = make_ctx(responses, with_maps=0)
    NarrativePhase().run(ctx)
    assert ctx.bible.story.synopsis == "An empty world."
    assert ctx.artifacts["narrative"]["victory_text"] == "Victory in the void."
    assert ctx.artifacts["narrative"]["defeat_text"] == "Fallen in the void."
    assert ctx.artifacts["narrative"]["map_intros"] == {}


def test_zero_maps_narrative_in_phases_run():
    responses = [
        _synopsis_json("S."),
        _victory_json("V."),
        _defeat_json("D."),
    ]
    ctx = make_ctx(responses, with_maps=0)
    NarrativePhase().run(ctx)
    assert "narrative" in ctx.bible.metadata.phases_run


# ---------------------------------------------------------------------------
# Raw prose fallback (non-JSON PromptSet)
# ---------------------------------------------------------------------------


def test_raw_prose_response_used_directly():
    """If the LLM returns raw prose (not JSON), it's used as-is."""
    raw_synopsis = "A land torn by ancient feuds and forgotten gods."
    responses = [
        raw_synopsis,  # synopsis — raw prose, no JSON wrapper
        "The gates stand tall before you.",  # map_0 intro
        "A quiet courtyard awaits.",  # map_1 intro
        "You are victorious!",  # victory
        "You are defeated.",  # defeat
    ]
    ctx = make_ctx(responses)
    NarrativePhase().run(ctx)
    assert ctx.bible.story.synopsis == raw_synopsis
    assert ctx.bible.maps["map_0"].description == "The gates stand tall before you."
    assert ctx.artifacts["narrative"]["victory_text"] == "You are victorious!"
    assert ctx.artifacts["narrative"]["defeat_text"] == "You are defeated."


# ---------------------------------------------------------------------------
# artifacts["narrative"] dict structure
# ---------------------------------------------------------------------------


def test_artifacts_narrative_has_all_keys():
    responses = [
        _synopsis_json("S."),
        _intro_json("I0."),
        _intro_json("I1."),
        _victory_json("V."),
        _defeat_json("D."),
    ]
    ctx = make_ctx(responses)
    NarrativePhase().run(ctx)
    narr = ctx.artifacts["narrative"]
    assert "synopsis" in narr
    assert "map_intros" in narr
    assert "victory_text" in narr
    assert "defeat_text" in narr


# ---------------------------------------------------------------------------
# v0.2: narrative.json file output
# ---------------------------------------------------------------------------


def test_narrative_json_file_written(tmp_path: Path):
    """After run(), data/narrative.json exists at output_dir/narrative.json."""
    responses = _five_responses(
        synopsis="The world is in peril.",
        intro0="You enter the ruins.",
        intro1="The palace awaits.",
        victory="You saved the day!",
        defeat="Darkness wins.",
    )
    ctx = make_ctx(responses, output_dir=str(tmp_path))
    NarrativePhase().run(ctx)
    out = tmp_path / "narrative.json"
    assert out.exists(), "narrative.json should be written by NarrativePhase"


def test_narrative_json_has_required_keys(tmp_path: Path):
    """narrative.json contains synopsis, game_over, victory keys."""
    responses = _five_responses()
    ctx = make_ctx(responses, output_dir=str(tmp_path))
    NarrativePhase().run(ctx)
    data = json.loads((tmp_path / "narrative.json").read_text())
    assert "synopsis" in data, "narrative.json must have 'synopsis' key"
    assert "game_over" in data, "narrative.json must have 'game_over' key"
    assert "victory" in data, "narrative.json must have 'victory' key"


def test_narrative_json_has_room_intro_keys(tmp_path: Path):
    """narrative.json has room_intro_<map_id> keys for each map."""
    responses = _five_responses(intro0="Ruin entrance.", intro1="Palace gate.")
    ctx = make_ctx(responses, output_dir=str(tmp_path))
    NarrativePhase().run(ctx)
    data = json.loads((tmp_path / "narrative.json").read_text())
    assert "room_intro_map_0" in data
    assert "room_intro_map_1" in data


def test_narrative_json_synopsis_matches_artifacts(tmp_path: Path):
    """narrative.json synopsis value matches ctx.artifacts['narrative']['synopsis']."""
    synopsis_text = "A world forged in shadow."
    responses = _five_responses(synopsis=synopsis_text)
    ctx = make_ctx(responses, output_dir=str(tmp_path))
    NarrativePhase().run(ctx)
    data = json.loads((tmp_path / "narrative.json").read_text())
    assert data["synopsis"] == synopsis_text
    assert ctx.artifacts["narrative"]["synopsis"] == synopsis_text


def test_narrative_json_game_over_is_defeat_text(tmp_path: Path):
    """narrative.json game_over maps to defeat_text from artifacts."""
    defeat = "You have fallen in darkness."
    responses = _five_responses(defeat=defeat)
    ctx = make_ctx(responses, output_dir=str(tmp_path))
    NarrativePhase().run(ctx)
    data = json.loads((tmp_path / "narrative.json").read_text())
    assert data["game_over"] == defeat
    assert ctx.artifacts["narrative"]["defeat_text"] == defeat


def test_narrative_json_victory_matches_artifacts(tmp_path: Path):
    """narrative.json victory maps to victory_text from artifacts."""
    victory = "The realm is saved!"
    responses = _five_responses(victory=victory)
    ctx = make_ctx(responses, output_dir=str(tmp_path))
    NarrativePhase().run(ctx)
    data = json.loads((tmp_path / "narrative.json").read_text())
    assert data["victory"] == victory


def test_narrative_json_room_intro_content(tmp_path: Path):
    """room_intro_<map_id> values match map_intros from artifacts."""
    responses = _five_responses(intro0="Deep cave entrance.", intro1="Ancient temple.")
    ctx = make_ctx(responses, output_dir=str(tmp_path))
    NarrativePhase().run(ctx)
    data = json.loads((tmp_path / "narrative.json").read_text())
    assert data["room_intro_map_0"] == "Deep cave entrance."
    assert data["room_intro_map_1"] == "Ancient temple."


def test_narrative_json_zero_maps(tmp_path: Path):
    """With no maps, narrative.json still has synopsis/game_over/victory."""
    responses = [
        _synopsis_json("Empty world."),
        _victory_json("Victory!"),
        _defeat_json("Defeat."),
    ]
    ctx = make_ctx(responses, with_maps=0, output_dir=str(tmp_path))
    NarrativePhase().run(ctx)
    data = json.loads((tmp_path / "narrative.json").read_text())
    assert "synopsis" in data
    assert "game_over" in data
    assert "victory" in data
    # No room_intro keys
    room_keys = [k for k in data if k.startswith("room_intro_")]
    assert len(room_keys) == 0


def test_narrative_json_creates_parent_dirs(tmp_path: Path):
    """narrative.json is created even if output_paths subdirectory doesn't exist."""
    responses = _five_responses()
    ctx = make_ctx(responses, output_dir=str(tmp_path / "deep" / "output"))
    NarrativePhase().run(ctx)
    out = tmp_path / "deep" / "output" / "narrative.json"
    assert out.exists()

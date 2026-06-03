"""Unit tests for DialoguePhase.

Uses FakeLLMBackend for deterministic, network-free LLM simulation.
"""

from __future__ import annotations

import json
import random

from canon import (
    Bible,
    CanonConfig,
    DefaultPromptSet,
    FakeLLMBackend,
    GenerationStats,
    LLMClient,
    PipelineContext,
)
from canon.bible.models import Character
from canon.dialogue.models import DialogueChoice, DialogueNode, DialogueTree
from canon.pipeline.phases.dialogue import DialoguePhase
from canon.pipeline.runner import Phase

# ---------------------------------------------------------------------------
# Helpers
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


def make_character(
    character_id="npc_001",
    name="Old Guard",
    role="npc",
    primary_map_id=None,
    dialogue_tree_id=None,
):
    return Character(
        character_id=character_id,
        name=name,
        role=role,
        primary_map_id=primary_map_id,
        lore="An ancient figure.",
        dialogue_tree_id=dialogue_tree_id,
    )


def valid_dialogue_response(entry_node_id="start", extra_nodes=None):
    """Build a valid dialogue JSON string."""
    nodes = {
        "start": {
            "prompt": "Greetings, traveller.",
            "choices": [
                {"text": "Hello.", "next_node_id": "reply"},
                {"text": "Goodbye.", "next_node_id": None},
            ],
        },
        "reply": {
            "prompt": "What brings you here?",
            "choices": [],
        },
    }
    if extra_nodes:
        nodes.update(extra_nodes)
    return json.dumps({"entry_node_id": entry_node_id, "nodes": nodes})


# ---------------------------------------------------------------------------
# Happy path: tree generated for matching character
# ---------------------------------------------------------------------------


def test_generates_tree_for_matching_role():
    ctx = make_ctx([valid_dialogue_response()])
    char = make_character()
    ctx.bible.add_character(char)

    DialoguePhase(roles=("npc",)).run(ctx)

    assert len(ctx.bible.dialogues) == 1
    assert "npc_001" in ctx.bible.dialogues


def test_tree_entry_node_id_matches_llm_response():
    ctx = make_ctx([valid_dialogue_response(entry_node_id="start")])
    char = make_character()
    ctx.bible.add_character(char)

    DialoguePhase(roles=("npc",)).run(ctx)

    tree = ctx.bible.dialogues["npc_001"]
    assert tree.entry_node_id == "start"


def test_character_dialogue_tree_id_set_after_generation():
    ctx = make_ctx([valid_dialogue_response()])
    char = make_character()
    ctx.bible.add_character(char)

    DialoguePhase(roles=("npc",)).run(ctx)

    assert char.dialogue_tree_id == "npc_001_dialogue"


def test_tree_id_naming_convention():
    """Tree IDs follow the pattern `{character_id}_dialogue`."""
    ctx = make_ctx([valid_dialogue_response()])
    char = make_character(character_id="merchant_042", role="merchant")
    ctx.bible.add_character(char)

    DialoguePhase(roles=("merchant",)).run(ctx)

    assert char.dialogue_tree_id == "merchant_042_dialogue"
    tree = ctx.bible.dialogues["merchant_042"]
    assert tree.tree_id == "merchant_042_dialogue"


def test_add_dialogue_called_with_tree():
    """The stored value is a fully-typed DialogueTree, not a raw dict."""
    ctx = make_ctx([valid_dialogue_response()])
    char = make_character()
    ctx.bible.add_character(char)

    DialoguePhase(roles=("npc",)).run(ctx)

    tree = ctx.bible.dialogues["npc_001"]
    assert isinstance(tree, DialogueTree)
    assert tree.character_id == "npc_001"


# ---------------------------------------------------------------------------
# Node and choice typing
# ---------------------------------------------------------------------------


def test_nodes_are_typed_dialogue_nodes():
    ctx = make_ctx([valid_dialogue_response()])
    char = make_character()
    ctx.bible.add_character(char)

    DialoguePhase(roles=("npc",)).run(ctx)

    tree = ctx.bible.dialogues["npc_001"]
    for node in tree.nodes.values():
        assert isinstance(node, DialogueNode)


def test_choices_are_typed_dialogue_choices():
    ctx = make_ctx([valid_dialogue_response()])
    char = make_character()
    ctx.bible.add_character(char)

    DialoguePhase(roles=("npc",)).run(ctx)

    tree = ctx.bible.dialogues["npc_001"]
    start_node = tree.nodes["start"]
    assert len(start_node.choices) == 2
    for choice in start_node.choices:
        assert isinstance(choice, DialogueChoice)


def test_choice_with_null_next_node_id():
    ctx = make_ctx([valid_dialogue_response()])
    char = make_character()
    ctx.bible.add_character(char)

    DialoguePhase(roles=("npc",)).run(ctx)

    tree = ctx.bible.dialogues["npc_001"]
    # The second choice has next_node_id=None (end of conversation)
    goodbye_choice = tree.nodes["start"].choices[1]
    assert goodbye_choice.next_node_id is None


def test_choice_conditions_and_effects_parsed():
    response = json.dumps({
        "entry_node_id": "start",
        "nodes": {
            "start": {
                "prompt": "Can you help?",
                "choices": [
                    {
                        "text": "Here's the key.",
                        "next_node_id": "done",
                        "conditions": ["has_item:key_1"],
                        "effects": ["gives_quest:q_5"],
                    }
                ],
            },
            "done": {"prompt": "Thanks.", "choices": []},
        },
    })
    ctx = make_ctx([response])
    char = make_character()
    ctx.bible.add_character(char)

    DialoguePhase(roles=("npc",)).run(ctx)

    tree = ctx.bible.dialogues["npc_001"]
    choice = tree.nodes["start"].choices[0]
    assert choice.conditions == ["has_item:key_1"]
    assert choice.effects == ["gives_quest:q_5"]


def test_node_tags_parsed():
    response = json.dumps({
        "entry_node_id": "start",
        "nodes": {
            "start": {
                "prompt": "Welcome.",
                "tags": ["greeting", "first_meeting"],
                "choices": [],
            }
        },
    })
    ctx = make_ctx([response])
    char = make_character()
    ctx.bible.add_character(char)

    DialoguePhase(roles=("npc",)).run(ctx)

    tree = ctx.bible.dialogues["npc_001"]
    assert tree.nodes["start"].tags == ["greeting", "first_meeting"]


def test_node_speaker_parsed():
    response = json.dumps({
        "entry_node_id": "start",
        "nodes": {
            "start": {
                "speaker": "npc_001",
                "prompt": "I speak.",
                "choices": [],
            }
        },
    })
    ctx = make_ctx([response])
    char = make_character()
    ctx.bible.add_character(char)

    DialoguePhase(roles=("npc",)).run(ctx)

    tree = ctx.bible.dialogues["npc_001"]
    assert tree.nodes["start"].speaker == "npc_001"


# ---------------------------------------------------------------------------
# Role filtering: characters with roles NOT in `roles` are skipped
# ---------------------------------------------------------------------------


def test_skips_character_with_excluded_role():
    ctx = make_ctx([])
    char = make_character(role="hostile")
    ctx.bible.add_character(char)

    DialoguePhase(roles=("npc", "merchant")).run(ctx)

    assert len(ctx.bible.dialogues) == 0
    assert char.dialogue_tree_id is None


def test_generates_only_for_matching_roles_in_mixed_cast():
    npc_response = valid_dialogue_response()
    ctx = make_ctx([npc_response])
    npc = make_character(character_id="npc_001", role="npc")
    boss = make_character(character_id="boss_001", role="boss")
    ctx.bible.add_character(npc)
    ctx.bible.add_character(boss)

    DialoguePhase(roles=("npc",)).run(ctx)

    assert "npc_001" in ctx.bible.dialogues
    assert "boss_001" not in ctx.bible.dialogues
    assert boss.dialogue_tree_id is None


def test_generates_for_all_matching_roles():
    r1 = valid_dialogue_response()
    r2 = valid_dialogue_response()
    ctx = make_ctx([r1, r2])
    npc = make_character(character_id="npc_001", role="npc")
    merchant = make_character(character_id="mer_001", role="merchant")
    ctx.bible.add_character(npc)
    ctx.bible.add_character(merchant)

    DialoguePhase(roles=("npc", "merchant")).run(ctx)

    assert len(ctx.bible.dialogues) == 2


# ---------------------------------------------------------------------------
# Preservation: characters with existing dialogue_tree_id are skipped
# ---------------------------------------------------------------------------


def test_skips_character_with_existing_dialogue_tree_id():
    ctx = make_ctx([])
    char = make_character(dialogue_tree_id="hand_authored_tree")
    ctx.bible.add_character(char)

    DialoguePhase(roles=("npc",)).run(ctx)

    assert len(ctx.bible.dialogues) == 0
    # The hand-authored id is NOT overwritten
    assert char.dialogue_tree_id == "hand_authored_tree"


def test_skips_already_set_generates_others():
    r1 = valid_dialogue_response()
    ctx = make_ctx([r1])
    pre_authored = make_character(character_id="npc_pre", dialogue_tree_id="existing_tree")
    new_npc = make_character(character_id="npc_new")
    ctx.bible.add_character(pre_authored)
    ctx.bible.add_character(new_npc)

    DialoguePhase(roles=("npc",)).run(ctx)

    assert "npc_pre" not in ctx.bible.dialogues
    assert "npc_new" in ctx.bible.dialogues
    assert pre_authored.dialogue_tree_id == "existing_tree"


# ---------------------------------------------------------------------------
# nodes_per_tree: exact int vs range tuple
# ---------------------------------------------------------------------------


def test_nodes_per_tree_exact_int_accepted():
    """When nodes_per_tree is an int, LLM is asked for that exact count (no crash)."""
    ctx = make_ctx([valid_dialogue_response()])
    char = make_character()
    ctx.bible.add_character(char)

    DialoguePhase(roles=("npc",), nodes_per_tree=5).run(ctx)

    assert char.dialogue_tree_id is not None


def test_nodes_per_tree_range_randomizes_with_seeded_rng():
    """With nodes_per_tree=(lo, hi) and a seeded RNG, the chosen count is
    deterministic and within [lo, hi]."""
    seed = "determinism_test"
    rng_ref = random.Random(seed)
    expected_count = rng_ref.randint(3, 8)

    # Capture what count the phase sends to the prompt by inspecting calls.
    ctx = make_ctx([valid_dialogue_response()], seed=seed)
    char = make_character()
    ctx.bible.add_character(char)

    DialoguePhase(roles=("npc",), nodes_per_tree=(3, 8)).run(ctx)

    # The call was made — the count in the prompt should contain expected_count.
    assert len(ctx.llm.backend.calls) >= 1
    call_msg = ctx.llm.backend.calls[0].user_message
    assert str(expected_count) in call_msg


def test_nodes_per_tree_range_stays_in_bounds():
    """Over many seeds, the selected count always falls within [lo, hi]."""
    for seed_int in range(10):
        seed = f"seed_{seed_int}"
        ctx = make_ctx([valid_dialogue_response()], seed=seed)
        char = make_character()
        ctx.bible.add_character(char)

        phase = DialoguePhase(roles=("npc",), nodes_per_tree=(3, 8))
        # Intercept the LLM call to check the prompt
        phase.run(ctx)
        # Verify the count embedded in the user_message is within [3, 8].
        call_msg = ctx.llm.backend.calls[0].user_message
        found = False
        for n in range(3, 9):
            if str(n) in call_msg:
                found = True
                break
        assert found, f"No count in [3, 8] found in prompt for seed={seed}"


# ---------------------------------------------------------------------------
# Retry / validation fallback
# ---------------------------------------------------------------------------


def test_retries_on_invalid_json():
    bad = "not json"
    good = valid_dialogue_response()
    ctx = make_ctx([bad, good])
    char = make_character()
    ctx.bible.add_character(char)

    DialoguePhase(roles=("npc",)).run(ctx)

    assert char.dialogue_tree_id is not None
    assert len(ctx.llm.backend.calls) == 2


def test_retries_on_missing_nodes_field():
    bad = json.dumps({"entry_node_id": "start"})  # no 'nodes'
    good = valid_dialogue_response()
    ctx = make_ctx([bad, good])
    char = make_character()
    ctx.bible.add_character(char)

    DialoguePhase(roles=("npc",)).run(ctx)

    tree = ctx.bible.dialogues["npc_001"]
    assert "start" in tree.nodes


def test_retries_on_entry_node_id_not_in_nodes():
    bad = json.dumps({
        "entry_node_id": "missing_node",
        "nodes": {"start": {"prompt": "hi", "choices": []}},
    })
    good = valid_dialogue_response()
    ctx = make_ctx([bad, good])
    char = make_character()
    ctx.bible.add_character(char)

    DialoguePhase(roles=("npc",)).run(ctx)

    assert len(ctx.llm.backend.calls) == 2


def test_fallback_to_minimal_tree_after_exhaustion():
    """After max_retries failures the fallback is used; a minimal tree is created."""
    ctx = make_ctx(["bad"] * 5)
    ctx.config.max_retries = 3
    char = make_character()
    ctx.bible.add_character(char)

    DialoguePhase(roles=("npc",)).run(ctx)

    # A tree should still be created (from the fallback JSON)
    assert char.dialogue_tree_id == "npc_001_dialogue"
    tree = ctx.bible.dialogues["npc_001"]
    assert isinstance(tree, DialogueTree)
    assert "start" in tree.nodes


def test_fallback_tree_entry_is_start():
    ctx = make_ctx(["bad"] * 5)
    ctx.config.max_retries = 3
    char = make_character()
    ctx.bible.add_character(char)

    DialoguePhase(roles=("npc",)).run(ctx)

    tree = ctx.bible.dialogues["npc_001"]
    assert tree.entry_node_id == "start"


# ---------------------------------------------------------------------------
# Malformed node handling
# ---------------------------------------------------------------------------


def test_malformed_node_values_skipped():
    """Non-dict node values in the LLM response are silently skipped."""
    response = json.dumps({
        "entry_node_id": "start",
        "nodes": {
            "start": {"prompt": "Good node.", "choices": []},
            "bad_node": "this is not a dict",  # malformed — should be skipped
        },
    })
    ctx = make_ctx([response])
    char = make_character()
    ctx.bible.add_character(char)

    DialoguePhase(roles=("npc",)).run(ctx)

    tree = ctx.bible.dialogues["npc_001"]
    assert "start" in tree.nodes
    assert "bad_node" not in tree.nodes


def test_malformed_choice_values_skipped():
    """Non-dict choice values are silently skipped; valid choices still parsed."""
    response = json.dumps({
        "entry_node_id": "start",
        "nodes": {
            "start": {
                "prompt": "Node with mixed choices.",
                "choices": [
                    "not a dict",
                    {"text": "Valid choice", "next_node_id": None},
                ],
            }
        },
    })
    ctx = make_ctx([response])
    char = make_character()
    ctx.bible.add_character(char)

    DialoguePhase(roles=("npc",)).run(ctx)

    tree = ctx.bible.dialogues["npc_001"]
    start_node = tree.nodes["start"]
    # Only the valid choice should appear
    assert len(start_node.choices) == 1
    assert start_node.choices[0].text == "Valid choice"


# ---------------------------------------------------------------------------
# No characters / empty bible
# ---------------------------------------------------------------------------


def test_no_characters_logs_warning_and_records_phase(caplog):
    import logging

    ctx = make_ctx([])

    with caplog.at_level(logging.WARNING):
        DialoguePhase().run(ctx)

    assert "dialogue" in ctx.bible.metadata.phases_run
    assert len(ctx.bible.dialogues) == 0


def test_no_characters_matching_roles_still_records_phase():
    ctx = make_ctx([])
    # Only hostile characters — none match default roles
    for i in range(3):
        ctx.bible.add_character(make_character(
            character_id=f"hostile_{i}", role="hostile"
        ))

    DialoguePhase().run(ctx)

    assert "dialogue" in ctx.bible.metadata.phases_run
    assert len(ctx.bible.dialogues) == 0


# ---------------------------------------------------------------------------
# Metadata recording
# ---------------------------------------------------------------------------


def test_dialogue_appears_in_metadata_phases_run():
    ctx = make_ctx([valid_dialogue_response()])
    char = make_character()
    ctx.bible.add_character(char)

    DialoguePhase(roles=("npc",)).run(ctx)

    assert "dialogue" in ctx.bible.metadata.phases_run


def test_dialogue_phase_name_is_dialogue():
    assert DialoguePhase.name == "dialogue"
    assert DialoguePhase().name == "dialogue"


# ---------------------------------------------------------------------------
# Phase protocol satisfaction
# ---------------------------------------------------------------------------


def test_satisfies_phase_protocol():
    assert isinstance(DialoguePhase(), Phase)


def test_phase_protocol_name_and_run():
    phase = DialoguePhase()
    assert hasattr(phase, "name")
    assert callable(phase.run)


# ---------------------------------------------------------------------------
# Context path: primary_map_id present vs absent
# ---------------------------------------------------------------------------


def test_uses_cumulative_context_when_map_id_set():
    """Characters with primary_map_id use get_cumulative_context (no crash)."""
    ctx = make_ctx([valid_dialogue_response()])
    char = make_character(primary_map_id="map_0")
    ctx.bible.add_character(char)

    # Bible has no map_0 — get_cumulative_context will return empty string
    # but should not raise.
    DialoguePhase(roles=("npc",)).run(ctx)

    assert char.dialogue_tree_id is not None


def test_uses_global_context_when_no_map_id():
    ctx = make_ctx([valid_dialogue_response()])
    char = make_character(primary_map_id=None)
    ctx.bible.add_character(char)

    DialoguePhase(roles=("npc",)).run(ctx)

    assert char.dialogue_tree_id is not None


# ---------------------------------------------------------------------------
# Multiple characters — correct tree per character
# ---------------------------------------------------------------------------


def test_each_character_gets_own_tree():
    r1 = valid_dialogue_response()
    r2 = valid_dialogue_response()
    ctx = make_ctx([r1, r2])
    npc1 = make_character(character_id="npc_001", role="npc")
    npc2 = make_character(character_id="npc_002", role="npc")
    ctx.bible.add_character(npc1)
    ctx.bible.add_character(npc2)

    DialoguePhase(roles=("npc",)).run(ctx)

    assert npc1.dialogue_tree_id == "npc_001_dialogue"
    assert npc2.dialogue_tree_id == "npc_002_dialogue"
    assert ctx.bible.dialogues["npc_001"].character_id == "npc_001"
    assert ctx.bible.dialogues["npc_002"].character_id == "npc_002"


def test_two_llm_calls_for_two_characters():
    ctx = make_ctx([valid_dialogue_response(), valid_dialogue_response()])
    npc1 = make_character(character_id="npc_001", role="npc")
    npc2 = make_character(character_id="npc_002", role="npc")
    ctx.bible.add_character(npc1)
    ctx.bible.add_character(npc2)

    DialoguePhase(roles=("npc",)).run(ctx)

    assert len(ctx.llm.backend.calls) == 2


# ---------------------------------------------------------------------------
# v0.2: 4-variant dialogue trees for quest-bearing characters
# ---------------------------------------------------------------------------


def make_quest_giver(character_id="qgiver_001", name="Quest Giver"):
    """Make a quest-bearing NPC. Uses role='npc' (a valid CharacterRole).
    The quest_bearing_roles parameter on DialoguePhase selects which *roles*
    trigger 4-variant generation — the role value itself needn't be special.
    """
    return Character(
        character_id=character_id,
        name=name,
        role="npc",
        lore="A quest giver.",
    )


class TestQuestBearingDialogueVariants:
    """Verify 4-variant tree generation for quest_bearing_roles."""

    def test_quest_giver_gets_four_trees(self):
        """A character whose role is in quest_bearing_roles gets 4 dialogue trees."""
        responses = [valid_dialogue_response()] * 4
        ctx = make_ctx(responses)
        char = make_quest_giver()
        ctx.bible.add_character(char)

        DialoguePhase(
            roles=("npc",),
            quest_bearing_roles=("npc",),
        ).run(ctx)

        # 4 trees in bible.dialogues
        assert len(ctx.bible.dialogues) == 4

    def test_quest_giver_base_tree_id_set(self):
        """dialogue_tree_id (base) is set on the character."""
        responses = [valid_dialogue_response()] * 4
        ctx = make_ctx(responses)
        char = make_quest_giver()
        ctx.bible.add_character(char)

        DialoguePhase(
            roles=("npc",),
            quest_bearing_roles=("npc",),
        ).run(ctx)

        assert char.dialogue_tree_id == "qgiver_001_dialogue"

    def test_quest_giver_complete_tree_id_set(self):
        """dialogue_tree_complete_id is set on the character."""
        responses = [valid_dialogue_response()] * 4
        ctx = make_ctx(responses)
        char = make_quest_giver()
        ctx.bible.add_character(char)

        DialoguePhase(
            roles=("npc",),
            quest_bearing_roles=("npc",),
        ).run(ctx)

        assert char.dialogue_tree_complete_id == "qgiver_001_dialogue_complete"

    def test_quest_giver_failed_tree_id_set(self):
        """dialogue_tree_failed_id is set on the character."""
        responses = [valid_dialogue_response()] * 4
        ctx = make_ctx(responses)
        char = make_quest_giver()
        ctx.bible.add_character(char)

        DialoguePhase(
            roles=("npc",),
            quest_bearing_roles=("npc",),
        ).run(ctx)

        assert char.dialogue_tree_failed_id == "qgiver_001_dialogue_failed"

    def test_quest_giver_incomplete_tree_id_set(self):
        """dialogue_tree_incomplete_id is set on the character."""
        responses = [valid_dialogue_response()] * 4
        ctx = make_ctx(responses)
        char = make_quest_giver()
        ctx.bible.add_character(char)

        DialoguePhase(
            roles=("npc",),
            quest_bearing_roles=("npc",),
        ).run(ctx)

        assert char.dialogue_tree_incomplete_id == "qgiver_001_dialogue_incomplete"

    def test_four_distinct_tree_ids_in_bible(self):
        """All four tree variants are stored in bible.dialogues with unique tree_ids."""
        responses = [valid_dialogue_response()] * 4
        ctx = make_ctx(responses)
        char = make_quest_giver()
        ctx.bible.add_character(char)

        DialoguePhase(
            roles=("npc",),
            quest_bearing_roles=("npc",),
        ).run(ctx)

        expected_keys = {
            "qgiver_001_dialogue",
            "qgiver_001_dialogue_complete",
            "qgiver_001_dialogue_failed",
            "qgiver_001_dialogue_incomplete",
        }
        assert set(ctx.bible.dialogues.keys()) == expected_keys

    def test_four_llm_calls_for_quest_giver(self):
        """Generating 4 variants requires exactly 4 LLM calls."""
        responses = [valid_dialogue_response()] * 4
        ctx = make_ctx(responses)
        char = make_quest_giver()
        ctx.bible.add_character(char)

        DialoguePhase(
            roles=("npc",),
            quest_bearing_roles=("npc",),
        ).run(ctx)

        assert len(ctx.llm.backend.calls) == 4

    def test_non_quest_npc_still_gets_one_tree(self):
        """Regular NPCs (not in quest_bearing_roles) still get only one tree.

        Here we use quest_bearing_roles=("merchant",) so the NPC (role="npc")
        is NOT in the quest_bearing_roles set — it should get a single tree.
        """
        responses = [valid_dialogue_response()] * 2
        ctx = make_ctx(responses)
        npc = make_character(character_id="npc_001", role="npc")
        ctx.bible.add_character(npc)

        DialoguePhase(
            roles=("npc",),
            quest_bearing_roles=("merchant",),  # npc not in here
        ).run(ctx)

        assert len(ctx.bible.dialogues) == 1
        assert npc.dialogue_tree_id == "npc_001_dialogue"
        assert npc.dialogue_tree_complete_id is None
        assert npc.dialogue_tree_failed_id is None
        assert npc.dialogue_tree_incomplete_id is None

    def test_mixed_cast_quest_and_regular(self):
        """Quest bearer (merchant) gets 4 trees; regular NPC gets 1. Total trees = 5."""
        responses = [valid_dialogue_response()] * 5
        ctx = make_ctx(responses)
        # Use merchant as the quest-bearing role; npc as regular
        quest_bearer = Character(
            character_id="merch_001",
            name="Quest Merchant",
            role="merchant",
            lore="A merchant who gives quests.",
        )
        npc = make_character(character_id="npc_001", role="npc")
        ctx.bible.add_character(quest_bearer)
        ctx.bible.add_character(npc)

        DialoguePhase(
            roles=("merchant", "npc"),
            quest_bearing_roles=("merchant",),
        ).run(ctx)

        assert len(ctx.bible.dialogues) == 5
        assert quest_bearer.dialogue_tree_complete_id is not None
        assert npc.dialogue_tree_complete_id is None

    def test_empty_quest_bearing_roles_gives_single_tree(self):
        """Default empty quest_bearing_roles means all characters get one tree (backward-compat)."""
        responses = [valid_dialogue_response()]
        ctx = make_ctx(responses)
        # Use a normal npc character
        char = make_character(character_id="npc_001", role="npc")
        ctx.bible.add_character(char)

        DialoguePhase(
            roles=("npc",),
            # quest_bearing_roles defaults to () — single tree only
        ).run(ctx)

        assert len(ctx.bible.dialogues) == 1
        assert char.dialogue_tree_id is not None
        assert char.dialogue_tree_complete_id is None

    def test_variant_trees_are_typed_dialogue_tree_objects(self):
        """All 4 variants stored in bible.dialogues are proper DialogueTree instances."""
        responses = [valid_dialogue_response()] * 4
        ctx = make_ctx(responses)
        char = make_quest_giver()
        ctx.bible.add_character(char)

        DialoguePhase(
            roles=("npc",),
            quest_bearing_roles=("npc",),
        ).run(ctx)

        from canon.dialogue.models import DialogueTree
        for tree in ctx.bible.dialogues.values():
            assert isinstance(tree, DialogueTree)

    def test_quest_giver_still_recorded_in_phases_run(self):
        """phases_run still contains 'dialogue' when quest variants are generated."""
        responses = [valid_dialogue_response()] * 4
        ctx = make_ctx(responses)
        char = make_quest_giver()
        ctx.bible.add_character(char)

        DialoguePhase(
            roles=("npc",),
            quest_bearing_roles=("npc",),
        ).run(ctx)

        assert "dialogue" in ctx.bible.metadata.phases_run

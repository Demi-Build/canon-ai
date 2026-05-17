"""Unit tests for canon.dialogue.models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from canon.dialogue import (
    DialogueChoice,
    DialogueNode,
    DialogueTree,
    is_entry,
    is_terminal,
)

# ---------------------------------------------------------------------------
# DialogueChoice
# ---------------------------------------------------------------------------


def test_choice_with_next_node_id() -> None:
    choice = DialogueChoice(text="Tell me more", next_node_id="explain")
    assert choice.text == "Tell me more"
    assert choice.next_node_id == "explain"
    assert choice.conditions == []
    assert choice.effects == []


def test_choice_without_next_node_id_ends_conversation() -> None:
    choice = DialogueChoice(text="Goodbye")
    assert choice.next_node_id is None


def test_choice_explicit_none_next_node_id() -> None:
    choice = DialogueChoice(text="Farewell", next_node_id=None)
    assert choice.next_node_id is None


def test_choice_conditions_and_effects() -> None:
    choice = DialogueChoice(
        text="Take the key",
        next_node_id="end",
        conditions=["has_quest:q_5"],
        effects=["gives_item:key_1", "advances_quest:q_5"],
    )
    assert choice.conditions == ["has_quest:q_5"]
    assert choice.effects == ["gives_item:key_1", "advances_quest:q_5"]


# ---------------------------------------------------------------------------
# DialogueNode
# ---------------------------------------------------------------------------


def test_node_default_speaker_is_none() -> None:
    node = DialogueNode(node_id="start", prompt="Hello, traveler.")
    assert node.speaker is None
    assert node.choices == []
    assert node.tags == []


def test_node_with_explicit_speaker() -> None:
    node = DialogueNode(
        node_id="cutscene_a",
        speaker="npc_villain_42",
        prompt="At last, we meet.",
    )
    assert node.speaker == "npc_villain_42"


def test_node_no_is_entry_or_is_terminal_fields() -> None:
    """Per verification doc: persisted shape must not carry these flags."""
    node = DialogueNode(node_id="start", prompt="Hi.")
    dumped = node.model_dump()
    assert "is_entry" not in dumped
    assert "is_terminal" not in dumped


def test_node_empty_choices_allowed_terminal_node() -> None:
    node = DialogueNode(node_id="end", prompt="Goodbye.", choices=[])
    assert node.choices == []


def test_node_with_choices_and_tags() -> None:
    node = DialogueNode(
        node_id="greeting",
        prompt="Welcome to the shop.",
        choices=[
            DialogueChoice(text="Browse", next_node_id="shop"),
            DialogueChoice(text="Leave"),
        ],
        tags=["greeting", "merchant"],
    )
    assert len(node.choices) == 2
    assert node.tags == ["greeting", "merchant"]


# ---------------------------------------------------------------------------
# DialogueTree
# ---------------------------------------------------------------------------


def _sample_tree(**overrides: object) -> DialogueTree:
    nodes = {
        "start": DialogueNode(
            node_id="start",
            prompt="Hail and well met!",
            choices=[
                DialogueChoice(text="Tell me about the village", next_node_id="village"),
                DialogueChoice(text="Goodbye"),
            ],
        ),
        "village": DialogueNode(
            node_id="village",
            prompt="The village is quiet, mostly.",
            choices=[DialogueChoice(text="I see.")],
        ),
    }
    kwargs: dict[str, object] = {
        "tree_id": "tree_npc_1_default",
        "character_id": "npc_1",
        "nodes": nodes,
    }
    kwargs.update(overrides)
    return DialogueTree(**kwargs)  # type: ignore[arg-type]


def test_tree_default_entry_node_id_is_start() -> None:
    tree = _sample_tree()
    assert tree.entry_node_id == "start"


def test_tree_explicit_entry_node_id() -> None:
    tree = _sample_tree(entry_node_id="village")
    assert tree.entry_node_id == "village"


def test_tree_default_variant_is_none() -> None:
    tree = _sample_tree()
    assert tree.variant is None


def test_tree_default_generation_trail_is_none() -> None:
    tree = _sample_tree()
    assert tree.generation_trail is None


def test_tree_round_trip_json() -> None:
    tree = _sample_tree()
    payload = tree.model_dump_json()
    restored = DialogueTree.model_validate_json(payload)
    assert restored == tree
    assert restored.nodes["start"].choices[0].next_node_id == "village"
    assert restored.nodes["start"].choices[1].next_node_id is None


def test_tree_round_trip_preserves_variant() -> None:
    tree = _sample_tree(variant="quest_complete")
    payload = tree.model_dump_json()
    restored = DialogueTree.model_validate_json(payload)
    assert restored.variant == "quest_complete"


def test_quest_variant_tree_is_valid_separate_entity() -> None:
    """Quest-conditional trees are *separate* DialogueTree entries that share
    character_id and set the variant field — not nested inside a base tree."""
    base = _sample_tree(tree_id="tree_npc_1_default")
    complete = _sample_tree(
        tree_id="tree_npc_1_complete",
        variant="quest_complete",
    )
    failed = _sample_tree(
        tree_id="tree_npc_1_failed",
        variant="quest_failed",
    )

    # All three share character_id but are independently valid trees
    assert base.character_id == complete.character_id == failed.character_id
    assert base.variant is None
    assert complete.variant == "quest_complete"
    assert failed.variant == "quest_failed"

    # No nesting: DialogueTree has no `variants` field
    assert "variants" not in base.model_dump()


def test_tree_required_fields() -> None:
    with pytest.raises(ValidationError):
        DialogueTree()  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def test_is_entry_helper() -> None:
    tree = _sample_tree()
    assert is_entry(tree, "start") is True
    assert is_entry(tree, "village") is False
    assert is_entry(tree, "nonexistent") is False


def test_is_entry_with_custom_entry_node_id() -> None:
    tree = _sample_tree(entry_node_id="village")
    assert is_entry(tree, "village") is True
    assert is_entry(tree, "start") is False


def test_is_terminal_helper() -> None:
    leaf = DialogueNode(node_id="end", prompt="Bye.", choices=[])
    branching = DialogueNode(
        node_id="mid",
        prompt="Pick one.",
        choices=[DialogueChoice(text="A"), DialogueChoice(text="B")],
    )
    assert is_terminal(leaf) is True
    assert is_terminal(branching) is False


def test_is_terminal_choice_with_only_end_conversation_is_still_non_terminal() -> None:
    """A node with at least one choice is not terminal — even if every choice
    ends the conversation. Terminality is about whether the node itself
    offers no further branches."""
    node = DialogueNode(
        node_id="farewell",
        prompt="Until next time.",
        choices=[DialogueChoice(text="Goodbye"), DialogueChoice(text="Farewell")],
    )
    assert is_terminal(node) is False


# ---------------------------------------------------------------------------
# DialogueNode.prompt field — rename from text (v0.2)
# ---------------------------------------------------------------------------


def test_node_prompt_field_name_in_serialized_json() -> None:
    """After the text→prompt rename, the serialised key must be 'prompt'."""
    node = DialogueNode(node_id="start", prompt="Hello, traveler.")
    dumped = node.model_dump()
    assert "prompt" in dumped
    assert "text" not in dumped
    assert dumped["prompt"] == "Hello, traveler."


def test_node_round_trip_preserves_prompt_field() -> None:
    node = DialogueNode(node_id="start", prompt="Hello, world.")
    payload = node.model_dump_json()
    restored = DialogueNode.model_validate_json(payload)
    assert restored.prompt == "Hello, world."
    assert "text" not in payload

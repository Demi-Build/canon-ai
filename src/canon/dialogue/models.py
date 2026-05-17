"""Dialogue data models for canon.

Game-agnostic dialogue tree shape that generalizes MazeWorld's existing
NPC dialogue format and extends to multi-speaker cutscenes, VN-style
branching, vendor exchanges, etc.

Persisted shape is kept lean: the entry node is identified by
``DialogueTree.entry_node_id``, and "terminal" is derived from a node having
no choices. See ``is_entry`` / ``is_terminal`` helpers below.

Quest-conditional dialogue (e.g. before/after a quest is complete) is modeled
as *separate* DialogueTree entries that share a ``character_id`` and set the
``variant`` field — they are not nested inside a base tree.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from canon.bible.models import GenerationTrail


class DialogueChoice(BaseModel):
    """A player-selectable branch from a dialogue node.

    ``next_node_id=None`` ends the conversation. ``conditions`` and ``effects``
    are free-form string tokens (e.g. ``"has_item:key_1"``,
    ``"gives_quest:q_5"``) interpreted by the host game; canon does not
    enforce a grammar.
    """

    text: str
    next_node_id: str | None = None
    conditions: list[str] = Field(default_factory=list)
    effects: list[str] = Field(default_factory=list)


class DialogueNode(BaseModel):
    """A single beat of dialogue.

    ``speaker=None`` means "use the tree's ``character_id``" — the common
    case for single-NPC dialogue. Set ``speaker`` explicitly for cutscenes or
    group dialogues where multiple characters speak within one tree.

    No ``is_entry``/``is_terminal`` flags: entry is identified by
    ``DialogueTree.entry_node_id``; terminal is derived from an empty
    ``choices`` list. Use the module-level helpers if convenience access is
    needed.
    """

    node_id: str
    speaker: str | None = None
    prompt: str
    choices: list[DialogueChoice] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class DialogueTree(BaseModel):
    """A full conversation graph for one character (or scene).

    ``entry_node_id`` defaults to ``"start"`` to match MazeWorld's hardcoded
    convention. ``variant`` distinguishes quest-conditional trees that share
    a ``character_id`` (e.g. ``"quest_complete"``, ``"quest_failed"``) — each
    variant is a separate, independently-editable DialogueTree entry.
    """

    tree_id: str
    character_id: str
    entry_node_id: str = "start"
    nodes: dict[str, DialogueNode]
    variant: str | None = None
    generation_trail: GenerationTrail | None = None


def is_entry(tree: DialogueTree, node_id: str) -> bool:
    """True if ``node_id`` is the entry node of ``tree``."""
    return node_id == tree.entry_node_id


def is_terminal(node: DialogueNode) -> bool:
    """True if ``node`` has no outgoing choices (a leaf of the tree)."""
    return not node.choices


# Resolve the GenerationTrail forward reference now that bible.models is in
# the package and importable.
from canon.bible.models import GenerationTrail  # noqa: E402, F401

DialogueTree.model_rebuild()

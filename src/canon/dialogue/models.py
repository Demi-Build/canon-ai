"""Dialogue data models for canon.

Game-agnostic dialogue tree shape that generalizes MazeWorld's existing
NPC dialogue format and extends to multi-speaker cutscenes, VN-style
branching, vendor exchanges, etc.

Persisted shape is kept lean: the entry node is identified by
``DialogueTree.entry_node_id``, and "terminal" is derived from a node having
no choices. See ``is_entry`` / ``is_terminal`` helpers below.

Row P0-9 (W1 P5 dialogue) EXTENDS ``DialogueTree`` with the selector model
(Phase 0 §7.1, P0 paper P.1.1 / P.3.3): a character's dialogue is an ORDERED
LIST of trees, each carrying an author ``label``, the selector ``axis`` it
sorts under, a ``selector`` (``None`` = the fallback) and a ``rank``
(first match wins, top to bottom). Quest-conditional dialogue is therefore
still *separate* trees sharing a ``character_id`` — the legacy ``variant``
field stays for the pre-selector callers (``canon.pipeline.phases.dialogue``
writes it) and is simply the label of what is now a ``quest:`` selector.

``stored_tree`` / ``stored_node`` are the on-disk projection of the P.1.1
``dialogue_trees`` list — exactly ``{tree_id, character_id, label, axis,
selector, rank, entry_node_id, nodes}`` in that key order, so a save is a
stable diff. The projection is hand-written rather than ``model_dump``ed
because ``selector: null`` and ``speaker: null`` are MEANINGFUL (fallback
tree / "use the tree's character") and an ``exclude_none`` dump would drop
them, while a plain dump would add the two seed-only fields.

Per-choice ``conditions`` / ``effects`` are PRESERVED here even though the
engine copy (``packs.dungeon.dialogue._to_mazeworld_tree``) drops them —
doctrine 10, "data may outrun the engine": the authoring store is never
trimmed to what the runtime can evaluate. See ``canon.dialogue.storage``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from canon.bible.models import GenerationTrail


class DialogueChoice(BaseModel):
    """A player-selectable branch from a dialogue node.

    ``next_node_id=None`` ends the conversation. ``conditions`` and ``effects``
    are string tokens in the P.2 grammar (e.g. ``"has_item:2000"``,
    ``"gives_quest:4000"``); the model itself enforces no grammar — legality
    and vocabulary are the pack registry's ``DialogueSpec``, parsed by
    ``canon.dialogue.grammar`` and evaluated by ``canon.dialogue.evaluator``
    (row P0-9). A token the host engine cannot evaluate is still legal data.
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


class Selector(BaseModel):
    """A tree's selection predicate (P0 paper P.3.3, `PLAN.md:43-45`).

    ``rows`` are condition tokens in the same grammar as a choice's, at
    ``selector`` scope: **ALL rows must match** for the tree to be selected,
    and the FIRST tree (by ``rank``) whose selector matches wins. A tree with
    ``selector=None`` is the fallback — the ``otherwise → default`` row.
    """

    rows: list[str] = Field(default_factory=list)


class DialogueTree(BaseModel):
    """A full conversation graph for one character (or scene).

    ``entry_node_id`` defaults to ``"start"`` to match MazeWorld's hardcoded
    convention.

    Selector model (row P0-9): ``label`` is the author's name for the tree
    ("night vigil"), ``axis`` the registered selector axis it groups under
    (``DialogueSpec.selector_axes`` — data, never an enum), ``selector`` the
    ordered predicate (``None`` = fallback) and ``rank`` its precedence.
    ``variant`` predates the selector model and is kept for the generation
    phases that still set it; it carries no selection meaning.
    """

    tree_id: str
    character_id: str
    entry_node_id: str = "start"
    nodes: dict[str, DialogueNode]
    label: str = ""
    axis: str | None = None
    selector: Selector | None = None
    rank: int = 0
    variant: str | None = None
    generation_trail: GenerationTrail | None = None


def is_entry(tree: DialogueTree, node_id: str) -> bool:
    """True if ``node_id`` is the entry node of ``tree``."""
    return node_id == tree.entry_node_id


def is_terminal(node: DialogueNode) -> bool:
    """True if ``node`` has no outgoing choices (a leaf of the tree)."""
    return not node.choices


# ---------------------------------------------------------------------------
# The P.1.1 `dialogue_trees` on-disk projection (row P0-9)
# ---------------------------------------------------------------------------


def stored_choice(choice: DialogueChoice | dict) -> dict[str, Any]:
    """One choice as it sits in ``dialogue_trees`` — ``conditions`` and
    ``effects`` ALWAYS present (empty lists), because the engine copy drops
    them and this store is the only place they survive."""
    if isinstance(choice, DialogueChoice):
        choice = choice.model_dump()
    return {
        "text": choice.get("text", ""),
        "next_node_id": choice.get("next_node_id"),
        "conditions": list(choice.get("conditions") or []),
        "effects": list(choice.get("effects") or []),
    }


def stored_node(node: DialogueNode | dict) -> dict[str, Any]:
    """One node as it sits in ``dialogue_trees``."""
    if isinstance(node, DialogueNode):
        node = node.model_dump()
    return {
        "node_id": node.get("node_id", ""),
        "speaker": node.get("speaker"),
        "prompt": node.get("prompt", ""),
        "choices": [stored_choice(c) for c in node.get("choices") or []],
        "tags": list(node.get("tags") or []),
    }


def stored_tree(tree: DialogueTree | dict) -> dict[str, Any]:
    """One entry of the NPC's ``dialogue_trees`` list (P.1.1): ``{tree_id,
    character_id, label, axis, selector, rank, entry_node_id, nodes}``."""
    if isinstance(tree, DialogueTree):
        tree = tree.model_dump()
    selector = tree.get("selector")
    if isinstance(selector, Selector):
        selector = selector.model_dump()
    return {
        "tree_id": tree.get("tree_id", ""),
        "character_id": str(tree.get("character_id", "")),
        "label": tree.get("label") or "",
        "axis": tree.get("axis"),
        "selector": None if selector is None else {"rows": list(selector.get("rows") or [])},
        "rank": int(tree.get("rank") or 0),
        "entry_node_id": tree.get("entry_node_id") or "start",
        # The map key names the node when the payload's own ``node_id`` is
        # missing — a hand-written buffer must not come back with empty ids.
        "nodes": {
            node_id: _keyed_node(node_id, node)
            for node_id, node in (tree.get("nodes") or {}).items()
        },
    }


def _keyed_node(node_id: str, node: DialogueNode | dict) -> dict[str, Any]:
    stored = stored_node(node)
    stored["node_id"] = stored.get("node_id") or node_id
    return stored


# Resolve the GenerationTrail forward reference now that bible.models is in
# the package and importable.
from canon.bible.models import GenerationTrail  # noqa: E402, F401

DialogueTree.model_rebuild()

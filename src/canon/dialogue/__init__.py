"""Dialogue for canon — models, the one grammar, the one evaluator, the
``dialogue_trees`` storage + legacy shim, the ``EditOp`` apply, and the
``dialogue`` / ``scene`` verbs (Phase 0 §7; row P0-9).

The models predate the row; everything else lands with P0-9's selector model.
Import the verbs lazily from ``canon.dialogue.verbs`` — this package's
top-level names stay the data shapes so ``canon --help`` never pays for the
pack registry.
"""

from canon.dialogue.models import (
    DialogueChoice,
    DialogueNode,
    DialogueTree,
    Selector,
    is_entry,
    is_terminal,
    stored_choice,
    stored_node,
    stored_tree,
)

__all__ = [
    "DialogueChoice",
    "DialogueNode",
    "DialogueTree",
    "Selector",
    "is_entry",
    "is_terminal",
    "stored_choice",
    "stored_node",
    "stored_tree",
]

"""``dialogue_trees`` storage + the legacy four-field compat shim (Phase 0
§7.1, P0 paper P.1.1 and P.9 S9; row P0-9).

The model, approved: an NPC's dialogue is an ORDERED LIST of trees, each with
a ``selector`` (first match wins) or none (the fallback). It lives on the row
under ``DialogueSpec.storage["field"]`` — ``dialogue_trees`` for the dungeon
template — and the FOUR legacy keys named by ``storage["legacy_fields"]`` are
**written back on every save while the engine still reads them** (S9, frozen
on-disk contract). No migration verb, no rename: a pack that has never been
edited carries only the legacy keys and reads through ``import_legacy``
forever (master §2's read-both shim).

The mechanical mapping, both directions (§7.1):

===============================  ==================================  ======
legacy key                       selector                            rank
===============================  ==================================  ======
``dialogue_tree_incomplete``     ``quest:<quest_id>:active``           0
``dialogue_tree_complete``       ``quest:<quest_id>:completed``        1
``dialogue_tree_failed``         ``quest:<quest_id>:failed``           2
``dialogue_tree``                — (the fallback)                    999
===============================  ==================================  ======

Which quest state maps to which slot is DERIVED from the two data lists (the
legacy field names and ``DialogueSpec.operands.quest.states``), never a
hardcoded pair: ``completed`` → the ``…_complete`` slot, ``failed`` → the
``…_failed`` slot, every other state → the residual "incomplete" slot, which
is exactly what the engine shows while a quest is neither completed nor
failed (``quest_manager.py:302-325``). ``quest:<id>:active`` on a selector
row is P.2.4's own worked example of an operand the engine narrows away —
amber, never blocked.

Write-back walks the trees in rank order and gives each the FIRST slot its
selector claims; a tree whose selector names an axis the four fields cannot
express (``time:``, ``flag:``, …) gets NO slot and is reported as an
engine-lag WARNING — the data outruns the engine (doctrine 10), the save
still happens, and the engine simply never plays that tree. ``dialogue_tree``
falls back to the incomplete-slot tree when no fallback tree exists, which is
the generation pipeline's own rule (``dialogue.py:152`` ``shown = incomplete
or base``) and is what keeps a round trip byte-compatible with a generated
pack.

Where BOTH slots are claimed — the shape a generated quest-giver imports as,
since ``dialogue_tree`` is the pipeline's own duplicate of
``dialogue_tree_incomplete`` and maps on as a separate fallback tree — the
two are free to diverge, and the first edit to either one makes them. That is
reported as an engine-lag WARNING and nothing else: the engine has no
fallback slot, it plays ``dialogue_tree`` until the quest resolves
(``quest_manager.py:302-325``) and reads the residual slot for the combat
taunt alone (``game_controller.py:683``), so an edit to the residual tree is
not what the player sees before then. Doctrine 10 again — the authoring model
is the superset, the save still happens, and nothing here silently copies one
authored tree over another.

``engine_tree`` is the same projection ``packs.dungeon.dialogue.
_to_mazeworld_tree`` writes — ``{"nodes": {id: {prompt, choices:[{text,
next_node_id}]}}}``, entry aliased to ``"start"``. It DROPS per-choice
``conditions`` / ``effects``; ``dialogue_trees`` keeps them. That asymmetry is
deliberate and load-bearing: doctrine 10 says authoring is never trimmed to
what the runtime can evaluate, so the authoring store is the superset and the
engine copy is the projection. Nothing reconciles them by deleting data.

Deliberately absent, by row ownership: the room/grid writer (P0-8, and the
carry-over fixer owns ``adapters/dungeon_write.py``); cradle's adapters
(waves 2–3).
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from canon.db_ops import _entity, _locate, _read_collection, _resolve
from canon.dialogue.grammar import format_token, spec_of
from canon.dialogue.models import stored_tree
from canon.packs import PackSpec, ResolvedPack
from canon.packs.spec import DialogueSpec

__all__ = [
    "ResolvedNpc",
    "engine_tree",
    "import_legacy",
    "legacy_projection",
    "legacy_slot_for_state",
    "npc_trees",
    "resolve_npc",
    "state_for_slot",
    "write_back",
]


class ResolvedNpc:
    """One NPC located for a dialogue verb: the pack, the resolved spec, the
    ``npc`` ``EntityKind``, the loaded collection document (the CAS unit) and
    the row inside it. Mirrors what ``db_ops.update_db_row`` computes before
    it calls the write core — the same resolution, not a second one."""

    __slots__ = ("document", "entity", "npc_id", "pack", "rel_path", "resolved", "row", "spec")

    def __init__(self, pack: Path, resolved: ResolvedPack, npc_id: str) -> None:
        self.pack = pack
        self.resolved = resolved
        self.spec: PackSpec = resolved.spec
        self.npc_id = str(npc_id)
        dialogue = self.spec.dialogue
        kind = (dialogue.storage or {}).get("on", "npc") if dialogue else "npc"
        self.entity = _entity(self.spec, kind)
        self.rel_path = str(self.entity.layout.get("path"))
        self.document = _read_collection(pack, self.entity)
        self.row, _accessor = _locate(self.entity, self.document, self.npc_id)

    @property
    def dialogue(self) -> DialogueSpec:
        return spec_of(self.spec.dialogue)

    @property
    def field(self) -> str:
        return str((self.dialogue.storage or {}).get("field") or "dialogue_trees")

    @property
    def legacy_fields(self) -> list[str]:
        return list((self.dialogue.storage or {}).get("legacy_fields") or [])

    def engine_blocks(self) -> dict[str, Any] | None:
        """The primary engine's ``evaluable_namespaces`` (P.2.4), falling back
        to ``DialogueSpec.engine_evaluable_seed`` for a pre-registry pack —
        **never** to "all supported"."""
        return primary_engine_blocks(self.spec)


def primary_engine_blocks(spec: PackSpec) -> dict[str, Any] | None:
    """P.2.4's absent-resolution, in one place: the primary engine's block, or
    the ``DialogueSpec`` seed for that engine id, or ``None`` (= nothing
    known, so every gate is amber). Never "all supported"."""
    engine = spec.primary_engine()
    if engine is None:
        return None
    blocks = engine.get("evaluable_namespaces")
    if blocks is None and spec.dialogue is not None:
        blocks = spec.dialogue.engine_evaluable_seed.get(str(engine.get("id", "")))
    return blocks if isinstance(blocks, dict) else None


def resolve_npc(pack_dir: str | Path, npc_id: str) -> ResolvedNpc:
    """Resolve pack + NPC row, fail-closed. Raises ``FileNotFoundError`` for
    an unknown row and ``ValueError`` when the pack declares no dialogue
    capability (doctrine 4: named, not silent)."""
    pack, resolved = _resolve(pack_dir)
    if "dialogue" not in resolved.spec.capabilities or resolved.spec.dialogue is None:
        raise ValueError(
            f"{pack} declares no 'dialogue' capability — enable it with "
            "`canon registry set` before authoring dialogue (Phase 0 §5.1a)"
        )
    return ResolvedNpc(pack, resolved, npc_id)


# ---------------------------------------------------------------------------
# The mechanical legacy mapping (§7.1)
# ---------------------------------------------------------------------------


def _slot_suffix(base: str, name: str) -> str:
    return name[len(base) + 1 :] if name.startswith(base + "_") else name


def legacy_slot_for_state(state: str, legacy_fields: list[str]) -> str:
    """Which legacy key a ``quest:<id>:<state>`` selector maps onto. Derived
    from the field names themselves: the first variant slot whose suffix
    prefixes the state (``complete`` → ``completed``, ``failed`` → ``failed``),
    else the residual slot the engine shows while the quest is unresolved."""
    if not legacy_fields:
        return ""
    base, variants = legacy_fields[0], legacy_fields[1:]
    for name in variants:
        if state.startswith(_slot_suffix(base, name)):
            return name
    return variants[0] if variants else base


def state_for_slot(slot: str, spec: DialogueSpec) -> str | None:
    """The inverse: the quest state a legacy slot stands for, taken as the
    LAST pack state that maps to it (``not_started``/``active`` both map to
    the incomplete slot; ``active`` is the one the engine is actually in once
    the quest is given — P.2.4's own ``quest:4000:active`` example)."""
    legacy = list((spec.storage or {}).get("legacy_fields") or [])
    states = list((spec.operands.get("quest") or {}).get("states") or [])
    matches = [s for s in states if legacy_slot_for_state(s, legacy) == slot]
    return matches[-1] if matches else None


def import_legacy(row: dict, npc_id: str, spec: DialogueSpec) -> list[dict[str, Any]]:
    """The four legacy keys as ``dialogue_trees`` entries (§7.1's "existing
    four-variant NPCs map on as ``quest:`` selectors"; `PLAN.md:75`).

    Order and rank follow the table in the module docstring; the fallback is
    last (rank 999). An NPC with no ``quest_id`` gets its variant trees with
    ``selector: null`` — honest (the engine swaps them on a quest it knows
    from ``npc.quest_id``, and without one there is nothing to gate on) — and
    ``validate`` warns that only the first fallback can ever be selected.
    """
    legacy = list((spec.storage or {}).get("legacy_fields") or [])
    if not legacy:
        return []
    base, variants = legacy[0], legacy[1:]
    quest_id = row.get("quest_id")
    trees: list[dict[str, Any]] = []
    for rank, name in enumerate(variants):
        payload = row.get(name)
        if not isinstance(payload, dict) or not payload.get("nodes"):
            continue
        state = state_for_slot(name, spec)
        selector = (
            {"rows": [format_token("quest", quest_id, state)]}
            if quest_id is not None and state
            else None
        )
        trees.append(
            stored_tree({
                "tree_id": f"{npc_id}:{_slot_suffix(base, name)}",
                "character_id": npc_id,
                "label": _slot_suffix(base, name),
                "axis": "quest",
                "selector": selector,
                "rank": rank,
                "entry_node_id": _entry_of(payload),
                "nodes": _nodes_from_engine(payload),
            })
        )
    payload = row.get(base)
    if isinstance(payload, dict) and payload.get("nodes"):
        trees.append(
            stored_tree({
                "tree_id": f"{npc_id}:default",
                "character_id": npc_id,
                "label": "default",
                "axis": None,
                "selector": None,
                "rank": 999,
                "entry_node_id": _entry_of(payload),
                "nodes": _nodes_from_engine(payload),
            })
        )
    return trees


def _entry_of(payload: dict) -> str:
    nodes = payload.get("nodes") or {}
    return "start" if "start" in nodes else (next(iter(nodes), "start"))


def _nodes_from_engine(payload: dict) -> dict[str, Any]:
    """The engine's ``{prompt, choices:[{text, next_node_id}]}`` nodes as
    author nodes. Dangling ``next_node_id`` values are PRESERVED (`PLAN.md:75`
    — the orphan case the editor must show, not silently drop)."""
    out: dict[str, Any] = {}
    for node_id, node in (payload.get("nodes") or {}).items():
        node = node if isinstance(node, dict) else {}
        out[node_id] = {
            "node_id": node_id,
            "speaker": node.get("speaker"),
            "prompt": node.get("prompt", ""),
            "choices": [
                {
                    "text": choice.get("text", ""),
                    "next_node_id": choice.get("next_node_id"),
                    "conditions": list(choice.get("conditions") or []),
                    "effects": list(choice.get("effects") or []),
                }
                for choice in (node.get("choices") or [])
                if isinstance(choice, dict)
            ],
            "tags": list(node.get("tags") or []),
        }
    return out


def npc_trees(row: dict, npc_id: str, spec: DialogueSpec) -> tuple[list[dict[str, Any]], str]:
    """``(trees, source)`` for one NPC row — the new storage when it carries
    any, else the legacy import. ``source`` is ``dialogue_trees`` | ``legacy``
    | ``none`` so every surface can say where the data came from."""
    field = str((spec.storage or {}).get("field") or "dialogue_trees")
    stored = row.get(field)
    if isinstance(stored, list) and stored:
        return [stored_tree(t) for t in stored if isinstance(t, dict)], field
    imported = import_legacy(row, npc_id, spec)
    return imported, ("legacy" if imported else "none")


# ---------------------------------------------------------------------------
# Write-back (the compat shim)
# ---------------------------------------------------------------------------


def engine_tree(tree: dict) -> dict[str, Any]:
    """The engine's copy of one tree — byte-for-byte the shape
    ``MazeworldDialoguePhase._to_mazeworld_tree`` emits: ``{"nodes": {id:
    {prompt, choices:[{text, next_node_id}]}}}`` with the entry aliased to
    ``"start"``. Per-choice ``conditions`` / ``effects`` are DROPPED here and
    only here — they survive in ``dialogue_trees`` (doctrine 10)."""
    nodes: dict[str, Any] = {}
    for node_id, node in (tree.get("nodes") or {}).items():
        nodes[node_id] = {
            "prompt": node.get("prompt", ""),
            "choices": [
                {"text": c.get("text", ""), "next_node_id": c.get("next_node_id")}
                for c in (node.get("choices") or [])
            ],
        }
    entry = tree.get("entry_node_id") or "start"
    if entry != "start" and entry in nodes and "start" not in nodes:
        nodes["start"] = nodes[entry]
    return {"nodes": nodes}


def _claimed_slot(tree: dict, legacy_fields: list[str]) -> tuple[str | None, str | None]:
    """``(slot, reason)`` — which legacy key this tree's selector maps onto,
    or ``None`` plus the engine-lag reason it maps onto none."""
    selector = tree.get("selector")
    if selector is None:
        return (legacy_fields[0] if legacy_fields else None), None
    rows = list((selector or {}).get("rows") or [])
    if not rows:
        return (legacy_fields[0] if legacy_fields else None), None
    for row in rows:
        parts = str(row).split(":")
        if parts[0] == "quest" and len(parts) >= 3:
            return legacy_slot_for_state(parts[2], legacy_fields), None
    axes = ", ".join(sorted({str(r).split(":")[0] for r in rows}))
    return None, (
        f"tree {tree.get('tree_id')!r} is selected by {axes} — the four legacy "
        "dialogue_tree* keys only carry quest state, so the engine never plays "
        "this tree (engine lag: the data is kept, the engine copy is not written)"
    )


def legacy_projection(
    trees: list[dict], legacy_fields: list[str]
) -> tuple[dict[str, Any], dict[str, str], list[str]]:
    """``(slot → engine tree, tree_id → slot, warnings)``.

    First match wins: trees are walked in RANK order and each takes the first
    unclaimed slot its selector maps to. A slot already claimed by a
    higher-ranked tree, or a selector the legacy shape cannot express, is a
    warning — never a refusal.
    """
    ordered = sorted(trees, key=lambda t: (int(t.get("rank") or 0), str(t.get("tree_id"))))
    slots: dict[str, Any] = {}
    claims: dict[str, str] = {}
    warnings: list[str] = []
    for tree in ordered:
        slot, reason = _claimed_slot(tree, legacy_fields)
        if reason:
            warnings.append(reason)
            continue
        if slot is None:
            continue
        if slot in slots:
            warnings.append(
                f"tree {tree.get('tree_id')!r} maps onto {slot!r}, already taken by "
                f"{claims_of(claims, slot)!r} (first match wins) — the engine will not play it"
            )
            continue
        slots[slot] = engine_tree(tree)
        claims[str(tree.get("tree_id"))] = slot
    base = legacy_fields[0] if legacy_fields else None
    if base and base not in slots:
        # The pipeline's own rule (`dialogue.py:152`): the shown tree is the
        # incomplete variant when there is no separate fallback.
        residual = residual_of(legacy_fields)
        source = slots.get(residual) if residual else None
        if source is None and ordered:
            source = engine_tree(ordered[0])
        if source is not None:
            slots[base] = copy.deepcopy(source)
    elif base and residual_of(legacy_fields) in slots:
        # Both slots are claimed, by two DIFFERENT trees — the shape a legacy
        # quest-giver imports as (`dialogue_tree` is the pipeline's duplicate
        # of `dialogue_tree_incomplete`, so it maps on as a separate fallback
        # tree). The engine has no fallback slot: it plays `dialogue_tree`
        # until the quest resolves and only then swaps in the complete/failed
        # copy (`quest_manager.py:302-325`), reading the residual slot for the
        # combat taunt alone. So once the two diverge, an edit to the
        # residual tree is not what the player sees before the quest resolves.
        # Engine lag, named at the point of the save — never a refusal, and
        # nothing here reconciles the two by copying over an authored tree.
        residual = residual_of(legacy_fields)
        if slots[base] != slots[residual]:
            warnings.append(
                f"{claims_of(claims, residual)!r} claims {residual!r} but the engine plays "
                f"{base!r} ({claims_of(claims, base)!r}) until the quest resolves — the two "
                "now carry different trees, so this edit is not what the player sees before "
                "then (engine lag: both trees are kept)"
            )
    return slots, claims, warnings


def residual_of(legacy_fields: list[str]) -> str:
    """The "incomplete" slot — the second legacy field, which the pipeline's
    own rule (`dungeon/dialogue.py:152` ``shown = incomplete or base``) copies
    into the base slot at generation time."""
    return legacy_fields[1] if len(legacy_fields) > 1 else ""


def claims_of(claims: dict[str, str], slot: str) -> str:
    for tree_id, taken in claims.items():
        if taken == slot:
            return tree_id
    return ""


def write_back(row: dict, trees: list[dict], spec: DialogueSpec) -> list[str]:
    """Set ``dialogue_trees`` and the legacy four on *row* in place; return
    the engine-lag warnings. A legacy key with no claimant is REMOVED, so a
    non-quest NPC keeps exactly the one ``dialogue_tree`` the pipeline gives
    it and never grows three empty siblings."""
    field = str((spec.storage or {}).get("field") or "dialogue_trees")
    legacy = list((spec.storage or {}).get("legacy_fields") or [])
    ordered = sorted(trees, key=lambda t: (int(t.get("rank") or 0), str(t.get("tree_id"))))
    row[field] = [stored_tree(t) for t in ordered]
    slots, _claims, warnings = legacy_projection(ordered, legacy)
    for name in legacy:
        if name in slots:
            row[name] = slots[name]
        else:
            row.pop(name, None)
    return warnings

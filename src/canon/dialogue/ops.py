"""The ``EditOp`` union as the ``dialogue update`` / ``scene update`` wire
format, and its pure ``apply`` (row P0-9).

The union is `design_handoff_dialogue/PLAN.md:118-134` verbatim — one op per
gesture, the op list is what ``⌘S`` ships — implemented here so cradle's
``ops.ts`` and canon agree on one shape and canon is the only writer
(doctrine 1: cradle never writes pack files). Every op names its target by
id, never by index into a list the client guessed at, except the
choice-within-node index the design itself uses.

Tree ops operate on an ``AuthorDoc`` — ``{"character_id", "trees": [...]}``
in the P.1.1 ``dialogue_trees`` shape. Scene ops operate on ONE scene row
(the P.1.5 ``type: "scene"`` event). Both are pure: ``apply_ops`` deep-copies,
applies in order, and returns ``(doc, details)`` where each detail is the
per-op journal entry ``{i, k, target, changed}`` — the design's
"canon journals each op separately with its own per-field diff" (README §7)
inside ONE batch, one CAS snapshot, one file write.

Two behaviours the design pins that are easy to get wrong:

- ``node.remove`` RETARGETS every inbound choice to ``null`` ("becomes end of
  conversation", README §8's consequence preview). The other repairs the
  sheet offers are additional ops the surface appends; this is the default
  the preview draws.
- ``tree.rank`` is a SEMANTIC edit, not a view preference (`PLAN.md:257`):
  ``order`` re-ranks 0..n-1 and re-orders the list, and any tree absent from
  ``order`` keeps its relative position after the named ones.

Scene line numbers are renumbered to a contiguous 1..N after every structural
line op, and every choice option's ``to`` is remapped with them — branch
targets are line numbers (README, screen 08), so a bare insert would silently
re-point half the script.

Deliberately absent, by row ownership: undo/redo and the keyed edit buffer
(cradle's ``useDialogueEditor``, wave 2 — canon takes the flushed list);
``node.rename`` (forbidden in v1, `PLAN.md:254`).
"""

from __future__ import annotations

import copy
from typing import Any

from canon.dialogue.models import stored_choice, stored_node, stored_tree

__all__ = ["TREE_OPS", "SCENE_OPS", "apply_ops", "apply_scene_ops"]

#: `PLAN.md:118-129` — the tree half of the union.
TREE_OPS: tuple[str, ...] = (
    "node.add", "node.remove", "node.prompt", "node.speaker", "node.tags",
    "choice.add", "choice.remove", "choice.text", "choice.target",
    "choice.conditions", "choice.effects",
    "tree.entry", "tree.add", "tree.remove", "tree.duplicate",
    "tree.selector", "tree.rank",
)

#: `PLAN.md:130-133` — the scene half.
SCENE_OPS: tuple[str, ...] = (
    "scene.line.add", "scene.line.remove", "scene.line.text", "scene.line.speaker",
    "scene.line.conditions",
    "scene.actor.add", "scene.actor.remove", "scene.actor.required",
    "scene.settings", "scene.trigger", "scene.once", "scene.on_finish",
)


class OpError(ValueError):
    """A refused op, naming its index and kind (fail-closed, doctrine 1:
    nothing in the batch lands if any op is illegal)."""

    def __init__(self, index: int, kind: str, message: str) -> None:
        super().__init__(f"op[{index}] {kind}: {message}")
        self.payload = {"op_index": index, "op": kind}


def _tree(doc: dict, index: int, kind: str, tree_id: Any) -> dict:
    for tree in doc["trees"]:
        if str(tree.get("tree_id")) == str(tree_id):
            return tree
    known = [t.get("tree_id") for t in doc["trees"]]
    raise OpError(index, kind, f"no tree {tree_id!r} on this character (have {known})")


def _node(tree: dict, index: int, kind: str, node_id: Any) -> dict:
    nodes = tree.setdefault("nodes", {})
    if str(node_id) not in nodes:
        raise OpError(
            index, kind, f"tree {tree.get('tree_id')!r} has no node {node_id!r} (have {sorted(nodes)})"
        )
    return nodes[str(node_id)]


def _choice(node: dict, index: int, kind: str, at: Any) -> dict:
    choices = node.setdefault("choices", [])
    try:
        position = int(at)
    except (TypeError, ValueError):
        raise OpError(index, kind, f"choice index {at!r} is not an integer") from None
    if not (0 <= position < len(choices)):
        raise OpError(
            index, kind,
            f"node {node.get('node_id')!r} has {len(choices)} choice(s); index {position} is out of range",
        )
    return choices[position]


def _set(detail: dict, field: str, old: Any, new: Any) -> dict:
    detail.setdefault("changed", {})[field] = {"from": old, "to": new}
    return detail


def apply_ops(doc: dict[str, Any], ops: Any) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Apply the tree half of the union to an ``AuthorDoc``. Pure: *doc* is
    deep-copied. Returns ``(doc, details)``; raises ``OpError`` on the first
    illegal op so a batch is all-or-nothing before any byte is written."""
    if not isinstance(ops, list) or not ops:
        raise ValueError("--ops needs a non-empty JSON array of EditOps")
    out = copy.deepcopy(doc)
    out.setdefault("trees", [])
    details: list[dict[str, Any]] = []
    for index, raw in enumerate(ops):
        if not isinstance(raw, dict):
            raise ValueError(f"op[{index}] must be an object, got {type(raw).__name__}")
        kind = str(raw.get("k") or "")
        if kind not in TREE_OPS:
            raise OpError(
                index, kind or "?",
                f"unknown op — the dialogue op kinds are {list(TREE_OPS)}"
                + (f"; {kind!r} is a scene op — use `canon scene update`" if kind in SCENE_OPS else ""),
            )
        details.append(_apply_tree_op(out, index, kind, raw))
    return out, details


def _apply_tree_op(doc: dict, index: int, kind: str, op: dict) -> dict[str, Any]:  # noqa: C901, PLR0912, PLR0915
    detail: dict[str, Any] = {"i": index, "k": kind}
    if kind == "tree.add":
        tree_id = str(op.get("tree") or "")
        if not tree_id:
            raise OpError(index, kind, "needs a 'tree' id")
        if any(str(t.get("tree_id")) == tree_id for t in doc["trees"]):
            raise OpError(index, kind, f"tree {tree_id!r} already exists")
        rank = max([int(t.get("rank") or 0) for t in doc["trees"]] or [-1]) + 1
        tree = stored_tree({
            "tree_id": tree_id,
            "character_id": doc.get("character_id", ""),
            "label": op.get("label") or tree_id,
            "axis": op.get("axis"),
            "selector": op.get("selector"),
            "rank": op.get("rank", rank),
            "entry_node_id": op.get("entry_node_id") or "start",
            "nodes": op.get("nodes") or {},
        })
        doc["trees"].append(tree)
        detail["target"] = f"tree:{tree_id}"
        return _set(detail, "tree", None, tree_id)
    if kind == "tree.duplicate":
        source = _tree(doc, index, kind, op.get("from"))
        tree_id = str(op.get("tree") or "")
        if not tree_id:
            raise OpError(index, kind, "needs a 'tree' id for the copy")
        if any(str(t.get("tree_id")) == tree_id for t in doc["trees"]):
            raise OpError(index, kind, f"tree {tree_id!r} already exists")
        rank = max([int(t.get("rank") or 0) for t in doc["trees"]] or [-1]) + 1
        copied = stored_tree({
            **copy.deepcopy(source),
            "tree_id": tree_id,
            "label": op.get("label") or f"{source.get('label') or source.get('tree_id')} copy",
            "axis": op.get("axis", source.get("axis")),
            # A copy is UNGATED until the author gives it a selector: two
            # trees with the same selector would make the copy unreachable,
            # which is exactly the uncoverable-row warning we would then
            # have to raise against an edit the user never made.
            "selector": None,
            "rank": rank,
        })
        doc["trees"].append(copied)
        detail["target"] = f"tree:{tree_id}"
        return _set(detail, "tree", None, f"{source.get('tree_id')} → {tree_id}")
    if kind == "tree.rank":
        order = op.get("order")
        if not isinstance(order, list) or not order:
            raise OpError(index, kind, "needs 'order': the tree ids in their new precedence order")
        known = {str(t.get("tree_id")): t for t in doc["trees"]}
        unknown = [t for t in order if str(t) not in known]
        if unknown:
            raise OpError(index, kind, f"order names unknown tree(s) {unknown}")
        before = [str(t.get("tree_id")) for t in doc["trees"]]
        ranked = [known[str(t)] for t in order]
        ranked += [t for t in doc["trees"] if str(t.get("tree_id")) not in {str(o) for o in order}]
        for position, tree in enumerate(ranked):
            tree["rank"] = position
        doc["trees"] = ranked
        detail["target"] = "selector"
        return _set(detail, "rank_order", before, [str(t.get("tree_id")) for t in ranked])

    tree = _tree(doc, index, kind, op.get("tree"))
    tree_id = str(tree.get("tree_id"))
    detail["target"] = f"tree:{tree_id}"

    if kind == "tree.remove":
        doc["trees"] = [t for t in doc["trees"] if str(t.get("tree_id")) != tree_id]
        return _set(detail, "tree", tree_id, None)
    if kind == "tree.selector":
        old = tree.get("selector")
        selector = op.get("selector")
        if selector is not None:
            if not isinstance(selector, dict) or not isinstance(selector.get("rows"), list):
                raise OpError(index, kind, "selector must be null (fallback) or {\"rows\": [tokens]}")
            selector = {"rows": [str(r) for r in selector["rows"]]}
        tree["selector"] = selector
        if "axis" in op:
            tree["axis"] = op.get("axis")
        return _set(detail, "selector", old, selector)
    if kind == "tree.entry":
        old = tree.get("entry_node_id")
        node_id = str(op.get("node_id") or "")
        if node_id not in (tree.get("nodes") or {}):
            raise OpError(index, kind, f"tree {tree_id!r} has no node {node_id!r} to make the entry")
        tree["entry_node_id"] = node_id
        return _set(detail, "entry_node_id", old, node_id)

    nodes = tree.setdefault("nodes", {})
    node_id = str(op.get("node_id") or "")
    detail["target"] = f"tree:{tree_id}/node:{node_id}"

    if kind == "node.add":
        if node_id in nodes:
            raise OpError(index, kind, f"node {node_id!r} already exists in {tree_id!r}")
        payload = op.get("node") or {}
        nodes[node_id] = stored_node({**payload, "node_id": node_id})
        return _set(detail, "node", None, node_id)
    if kind == "node.remove":
        if node_id not in nodes:
            raise OpError(index, kind, f"tree {tree_id!r} has no node {node_id!r}")
        removed = nodes.pop(node_id)
        retargeted: list[str] = []
        for other_id, other in nodes.items():
            for position, choice in enumerate(other.get("choices") or []):
                if choice.get("next_node_id") == node_id:
                    choice["next_node_id"] = None
                    retargeted.append(f"{other_id}[{position}]")
        detail["retargeted_to_end"] = retargeted
        if tree.get("entry_node_id") == node_id:
            detail["entry_now_missing"] = True
        _set(detail, "node", node_id, None)
        detail["removed_choices"] = len(removed.get("choices") or [])
        return detail

    node = _node(tree, index, kind, node_id)

    if kind in ("node.prompt", "node.speaker"):
        field = kind.split(".")[1]
        old = node.get(field)
        value = op.get("value")
        if field == "prompt" and value is None:
            raise OpError(index, kind, "a node prompt cannot be null (use an empty string)")
        node[field] = value if value is None else str(value)
        return _set(detail, field, old, node[field])
    if kind == "node.tags":
        tags = op.get("tags")
        if not isinstance(tags, list):
            raise OpError(index, kind, "needs 'tags': a list of strings")
        old = list(node.get("tags") or [])
        node["tags"] = [str(t) for t in tags]
        return _set(detail, "tags", old, node["tags"])
    if kind == "choice.add":
        choices = node.setdefault("choices", [])
        at = op.get("index", len(choices))
        try:
            position = int(at)
        except (TypeError, ValueError):
            raise OpError(index, kind, f"choice index {at!r} is not an integer") from None
        if not (0 <= position <= len(choices)):
            raise OpError(index, kind, f"index {position} is outside 0..{len(choices)}")
        choice = stored_choice(op.get("choice") or {"text": ""})
        choices.insert(position, choice)
        detail["target"] = f"tree:{tree_id}/node:{node_id}/choice:{position}"
        return _set(detail, "choice", None, choice)
    if kind == "choice.remove":
        choice = _choice(node, index, kind, op.get("index"))
        node["choices"].remove(choice)
        detail["target"] = f"tree:{tree_id}/node:{node_id}/choice:{op.get('index')}"
        return _set(detail, "choice", choice, None)

    choice = _choice(node, index, kind, op.get("index"))
    detail["target"] = f"tree:{tree_id}/node:{node_id}/choice:{int(op.get('index'))}"
    if kind == "choice.text":
        old = choice.get("text")
        choice["text"] = "" if op.get("value") is None else str(op.get("value"))
        return _set(detail, "text", old, choice["text"])
    if kind == "choice.target":
        old = choice.get("next_node_id")
        value = op.get("value")
        choice["next_node_id"] = None if value is None else str(value)
        return _set(detail, "next_node_id", old, choice["next_node_id"])
    field = kind.split(".")[1]  # conditions | effects
    tokens = op.get("tokens")
    if not isinstance(tokens, list):
        raise OpError(index, kind, f"needs 'tokens': a list of {field} tokens")
    old = list(choice.get(field) or [])
    choice[field] = [str(t) for t in tokens]
    return _set(detail, field, old, choice[field])


# ---------------------------------------------------------------------------
# Scene ops (P.1.5 sub-shape)
# ---------------------------------------------------------------------------


def _renumber(scene: dict) -> None:
    """Lines get contiguous numbers 1..N in list order and every choice
    option's ``to`` follows them. A ``to`` whose target line is gone points
    at the line that now occupies its place, or ``None`` past the end."""
    lines = scene.get("lines") or []
    remap: dict[int, int | None] = {}
    for position, line in enumerate(lines, start=1):
        old = line.get("n")
        if isinstance(old, int):
            remap[old] = position
        line["n"] = position
    for line in lines:
        if line.get("k") != "choice":
            continue
        for option in line.get("options") or []:
            target = option.get("to")
            if isinstance(target, int) and target in remap:
                option["to"] = remap[target]
            elif isinstance(target, int):
                option["to"] = target if 1 <= target <= len(lines) else None


def apply_scene_ops(scene: dict[str, Any], ops: Any) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Apply the scene half of the union to ONE scene row. Pure, same
    contract as ``apply_ops``."""
    if not isinstance(ops, list) or not ops:
        raise ValueError("--ops needs a non-empty JSON array of EditOps")
    out = copy.deepcopy(scene)
    out.setdefault("lines", [])
    out.setdefault("actors", [])
    details: list[dict[str, Any]] = []
    for index, raw in enumerate(ops):
        if not isinstance(raw, dict):
            raise ValueError(f"op[{index}] must be an object, got {type(raw).__name__}")
        kind = str(raw.get("k") or "")
        if kind not in SCENE_OPS:
            raise OpError(
                index, kind or "?",
                f"unknown op — the scene op kinds are {list(SCENE_OPS)}"
                + (f"; {kind!r} is a tree op — use `canon dialogue update`" if kind in TREE_OPS else ""),
            )
        details.append(_apply_scene_op(out, index, kind, raw))
    return out, details


def _line(scene: dict, index: int, kind: str, n: Any) -> dict:
    try:
        number = int(n)
    except (TypeError, ValueError):
        raise OpError(index, kind, f"line number {n!r} is not an integer") from None
    for line in scene.get("lines") or []:
        if int(line.get("n") or 0) == number:
            return line
    have = [line.get("n") for line in scene.get("lines") or []]
    raise OpError(index, kind, f"scene has no line {number} (have {have})")


def _apply_scene_op(scene: dict, index: int, kind: str, op: dict) -> dict[str, Any]:  # noqa: C901, PLR0912
    detail: dict[str, Any] = {"i": index, "k": kind, "target": f"scene:{scene.get('id')}"}
    if kind in ("scene.settings", "scene.trigger", "scene.once", "scene.on_finish"):
        field = kind.split(".")[1]
        old = scene.get(field)
        value = op.get("value")
        if field in ("settings", "on_finish"):
            if not isinstance(value, list):
                raise OpError(index, kind, f"{field} must be a list of tokens")
            value = [str(v) for v in value]
        elif field == "once":
            value = bool(value)
        else:
            value = str(value)
        scene[field] = value
        return _set(detail, field, old, value)
    if kind.startswith("scene.actor."):
        character_id = str(op.get("character_id") or "")
        if not character_id:
            raise OpError(index, kind, "needs a 'character_id'")
        actors = scene.setdefault("actors", [])
        detail["target"] = f"scene:{scene.get('id')}/actor:{character_id}"
        existing = next((a for a in actors if str(a.get("character_id")) == character_id), None)
        if kind == "scene.actor.add":
            if existing is not None:
                raise OpError(index, kind, f"{character_id!r} is already an actor")
            actor = {"character_id": character_id, "required": bool(op.get("required", True))}
            actors.append(actor)
            return _set(detail, "actor", None, actor)
        if existing is None:
            raise OpError(index, kind, f"{character_id!r} is not an actor of this scene")
        if kind == "scene.actor.remove":
            actors.remove(existing)
            detail["required"] = bool(existing.get("required"))
            return _set(detail, "actor", existing, None)
        old = bool(existing.get("required"))
        existing["required"] = bool(op.get("required", True))
        return _set(detail, "required", old, existing["required"])

    lines = scene.setdefault("lines", [])
    if kind == "scene.line.add":
        payload = op.get("value")
        if not isinstance(payload, dict):
            raise OpError(index, kind, "needs 'value': the line object ({k: 'line'|'choice', ...})")
        try:
            at = int(op.get("n", len(lines) + 1))
        except (TypeError, ValueError):
            raise OpError(index, kind, f"line number {op.get('n')!r} is not an integer") from None
        at = max(1, min(at, len(lines) + 1))
        line = _normalize_line(payload, at)
        lines.insert(at - 1, line)
        _renumber(scene)
        detail["target"] = f"scene:{scene.get('id')}/line:{at}"
        return _set(detail, "line", None, line)
    line = _line(scene, index, kind, op.get("n"))
    number = int(line.get("n") or 0)
    detail["target"] = f"scene:{scene.get('id')}/line:{number}"
    if kind == "scene.line.remove":
        lines.remove(line)
        _renumber(scene)
        return _set(detail, "line", line, None)
    if line.get("k") != "line":
        raise OpError(index, kind, f"line {number} is a choice block — it has no {kind.split('.')[-1]}")
    if kind == "scene.line.text":
        old = line.get("text")
        line["text"] = "" if op.get("value") is None else str(op.get("value"))
        return _set(detail, "text", old, line["text"])
    if kind == "scene.line.speaker":
        old = line.get("speaker")
        value = op.get("value")
        line["speaker"] = None if value is None else str(value)
        return _set(detail, "speaker", old, line["speaker"])
    value = op.get("value")
    if not isinstance(value, list):
        raise OpError(index, kind, "needs 'value': a list of condition tokens")
    old = list(line.get("conditions") or [])
    line["conditions"] = [str(v) for v in value]
    return _set(detail, "conditions", old, line["conditions"])


def _normalize_line(payload: dict, number: int) -> dict[str, Any]:
    """One scene line in the P.1.5 sub-shape — ``{k:"line", n, speaker, text,
    conditions}`` or ``{k:"choice", n, options:[{text, to, conditions}]}``."""
    kind = str(payload.get("k") or "line")
    if kind == "choice":
        return {
            "k": "choice",
            "n": number,
            "options": [
                {
                    "text": str(option.get("text", "")),
                    "to": option.get("to"),
                    "conditions": [str(c) for c in (option.get("conditions") or [])],
                }
                for option in (payload.get("options") or [])
                if isinstance(option, dict)
            ],
        }
    return {
        "k": "line",
        "n": number,
        "speaker": None if payload.get("speaker") is None else str(payload.get("speaker")),
        "text": str(payload.get("text", "")),
        "conditions": [str(c) for c in (payload.get("conditions") or [])],
    }

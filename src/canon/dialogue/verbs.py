"""The ``canon dialogue`` / ``canon scene`` verbs (Phase 0 §7.2; row P0-9).

Six dialogue verbs — ``show · update · validate · test · select · improve`` —
and the three scene verbs ``update · validate · test``. Every one is
JSON-emitting and every write takes ``--actor``.

What each verb extends, rather than re-implements:

- ``update`` / ``scene update`` mount on the P0-6 **write core**
  (``canon.write_core.write_document``): same protected wall, same fail-closed
  validate, same ``user_edited`` rule, same journal, same CAS pair, ONE
  snapshot of the file per save. The op list is applied first, purely
  (``canon.dialogue.ops``), and lands as ONE batch — "canon journals each op
  separately with its own per-field diff" rides in ``detail.ops`` while
  ``detail.changed`` carries the per-key file diff the journal already
  understands. A quest-scope save is one ``dialogue update`` per touched NPC
  (cradle batches them; canon never sees the group).
- ``show`` / ``validate`` / ``select`` read through
  ``canon.dialogue.storage`` — the ``dialogue_trees`` list when the row has
  one, the legacy four otherwise (the read-both shim; no migration verb).
- every gate verdict comes from ``canon.dialogue.evaluator`` — the ONE
  evaluator, so the tester, the rail and the gate ribbon cannot disagree.
- namespace legality and operand vocabulary come from the registry's
  ``DialogueSpec`` via ``canon.dialogue.grammar``; the operand TABLES come
  from the pack's own rows through ``canon.packs.rows.load_rows``, falling
  back to the layout's ``row_source`` mirror on a tree whose index does not
  exist yet (the same read-both shim ``db update`` resolves a row through).
  No verb here builds a token by concatenation or hardcodes an id.

Doctrine 10 is the rule this row is built around: **data may outrun the
engine.** ``validate`` returns ``{errors, warnings}`` and unreachable nodes,
dangling targets, uncoverable selector rows and every engine-lag finding are
WARNINGS — they are surfaced loudly and they never block a save. The only
blocking errors are the ones README's state table names: a missing entry
node, an unparseable token, an unresolved entity id, a duplicate tree id, and
a scene-only namespace used in a tree.

Deliberately absent, by row ownership: the cradle surfaces and the docked
tester (waves 2–3); the live in-game scene (Phase 2 W2.2); engine evaluation
of the new namespaces (its own arc — this row only reports what the engine's
``evaluable_namespaces`` block claims).
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from canon.db_ops import (
    _collection_in,
    _entity,
    _locate,
    _read_collection,
    _resolve,
    _row_files,
    _wall,
)
from canon.dialogue.evaluator import apply_effects, evaluate_conditions, normalize_state
from canon.dialogue.grammar import (
    TokenError,
    engine_evaluable,
    parse_effect,
    parse_token,
    spec_of,
)
from canon.dialogue.models import stored_tree
from canon.dialogue.ops import apply_ops, apply_scene_ops
from canon.dialogue.scenes import blank_scene, is_scene, normalize_scene, walk_scene
from canon.dialogue.storage import (
    ResolvedNpc,
    legacy_projection,
    npc_trees,
    primary_engine_blocks,
    resolve_npc,
    write_back,
)
from canon.packs import PackSpec
from canon.packs.rows import load_per_file_rows, load_rows, read_json
from canon.packs.spec import DialogueSpec, EntityKind
from canon.write_core import write_document

__all__ = [
    "ResolvedScene",
    "describe_token",
    "dialogue_select",
    "dialogue_show",
    "dialogue_test",
    "dialogue_update",
    "dialogue_validate",
    "load_scene",
    "load_tree",
    "operand_tables",
    "scene_test",
    "scene_update",
    "scene_validate",
    "validate_scene",
    "validate_trees",
]


# ---------------------------------------------------------------------------
# Operand tables — the pack's own rows, per namespace descriptor
# ---------------------------------------------------------------------------


def _mirror_rows(pack: Path, entity: EntityKind) -> dict[str, dict] | None:
    """The whole collection as a ``row_source`` MIRROR carries it, or ``None``
    when no such mirror is on disk.

    The read-both shim ``db_ops._resolve_row_files`` already resolves ONE row
    through (P.1.7's mirror table, declared as DATA on the kind's layout),
    widened here to the whole table: the legacy dungeon trees (both demos, the
    reference fixture) predate ``rooms/rooms.json``, and ``load_rows``
    correctly reads an absent index as an empty kind — which the operand
    tables would otherwise take as proof that no room exists, refusing every
    legal ``room:`` token. Same ``_row_files`` list, same ``_collection_in``
    container; nothing is synthesized and no index is created (master §2).
    """
    for target in _row_files(entity, ""):
        if target.primary or not target.row_source:
            continue
        try:
            collection = _collection_in(read_json(pack / target.rel), target)
        except (OSError, ValueError):
            continue
        if isinstance(collection, dict):
            return {str(key): row for key, row in collection.items() if isinstance(row, dict)}
        if isinstance(collection, list):
            out: dict[str, dict] = {}
            for index, row in enumerate(collection):
                if not isinstance(row, dict):
                    continue
                value = row.get(target.id_field)
                out[str(value) if value is not None else str(index)] = row
            return out
    return None


def _rows_of(pack: Path, spec: PackSpec, kind: str) -> dict[str, dict] | None:
    """*kind*'s rows, or ``None`` when the pack carries NO readable source for
    them (no index, no ``row_source`` mirror, an unknown kind, a file that
    will not parse). ``None`` and ``{}`` are different answers and callers
    must keep them apart: ``{}`` is "read, and the kind is genuinely empty",
    ``None`` is "nothing to read" — and a reader gap may never harden into a
    refusal (doctrine 10)."""
    entity = spec.entities.get(kind)
    if entity is None:
        return None
    layout = entity.layout or {}
    try:
        if entity.loader is not None:
            rows = entity.loader(pack)
        elif layout.get("mode") == "collection":
            rows = load_rows(pack, entity)
        else:
            return load_per_file_rows(pack, entity)
        if rows:
            return rows
        # An empty answer from an ABSENT index is not an empty kind: the
        # layout's `row_source` mirror may carry the rows (legacy trees).
        path = str(layout.get("path") or "")
        if path and read_json(pack / path) is None:
            return _mirror_rows(pack, entity)
        return rows
    except (ValueError, OSError):
        return None


def operand_tables(pack: Path, spec: PackSpec, dialogue: DialogueSpec) -> dict[str, set[str]]:
    """``namespace → the legal operand ids`` for every entity-backed
    namespace, read from the pack's OWN row files through the descriptor's
    ``entity`` / ``field`` / ``filter`` (P.3.3). A namespace with no entity
    descriptor (``time``, ``player``, ``flag``, ``segment``) is absent here —
    its vocabulary is the descriptor's own value list, which the grammar
    already enforced at parse time.

    A namespace whose rows have NO readable source is also absent (``_rows_of``
    answering ``None``), which ``_unresolved`` reads as "unknown vocabulary,
    say nothing" rather than "no such row" — a reader gap warns at worst, it
    never refuses a legal token."""
    tables: dict[str, set[str]] = {}
    for namespace, descriptor in (dialogue.operands or {}).items():
        kind = descriptor.get("entity")
        if not kind:
            continue
        field = str(descriptor.get("field") or "id")
        rows = _rows_of(pack, spec, str(kind))
        if rows is None:
            continue
        matcher = descriptor.get("filter") or {}
        ids: set[str] = set()
        for key, row in rows.items():
            if not isinstance(row, dict):
                ids.add(str(key))
                continue
            if any(str(row.get(k)) != str(v) for k, v in matcher.items()):
                continue
            # A mirror row keeps its id in the KEY (`world_bible.json.rooms`
            # rows carry no `id`), so a null/absent field falls back to it.
            value = row.get(field)
            ids.add(str(value) if value is not None else str(key))
        tables[namespace] = ids
    return tables


def _unresolved(parsed_slots: dict[str, str], namespace: str, tables: dict[str, set[str]]) -> str | None:
    entity_id = parsed_slots.get("entity_id")
    known = tables.get(namespace)
    if entity_id is None or known is None:
        return None
    if entity_id in known:
        return None
    return f"{namespace}:{entity_id} does not resolve to a row in this pack"


# ---------------------------------------------------------------------------
# Token inspection (what the rail and the gate ribbon render)
# ---------------------------------------------------------------------------


def describe_token(
    token: Any,
    *,
    scope: str,
    dialogue: DialogueSpec,
    blocks: dict[str, Any] | None,
    tables: dict[str, set[str]] | None = None,
    kind: str = "condition",
) -> dict[str, Any]:
    """One token as the UI needs it: parsed or NOT, its engine-evaluability
    at this scope, and the reason for either. Never raises — a bad token
    describes itself (doctrine 4)."""
    try:
        parsed = parse_effect(token, spec=dialogue) if kind == "effect" else parse_token(
            token, scope=scope, spec=dialogue
        )
    except TokenError as exc:
        return {
            "token": str(token), "namespace": str(token).split(":")[0], "kind": kind,
            "legal": False, "reason": str(exc), "engine_evaluable": False, "engine_reason": None,
        }
    evaluable, engine_reason = engine_evaluable(parsed, "effects" if kind == "effect" else scope, blocks)
    out: dict[str, Any] = {
        **parsed.as_json(),
        "legal": True,
        "reason": None,
        "engine_evaluable": evaluable,
        "engine_reason": engine_reason,
    }
    if tables is not None:
        out["unresolved"] = _unresolved(parsed.slots, parsed.namespace, tables)
    return out


# ---------------------------------------------------------------------------
# dialogue show
# ---------------------------------------------------------------------------


def _scenes_for(pack: Path, spec: PackSpec, dialogue: DialogueSpec, npc_id: str) -> list[dict[str, Any]]:
    """The scenes this NPC acts in — the rail's scene rows. Scenes are
    referenced, never embedded (§7.1): one store, three readers."""
    out: list[dict[str, Any]] = []
    for row in (_rows_of(pack, spec, "event") or {}).values():
        if not is_scene(row, dialogue):
            continue
        actors = [str(a.get("character_id")) for a in (row.get("actors") or []) if isinstance(a, dict)]
        if npc_id not in actors:
            continue
        out.append({
            "id": row.get("id"),
            "title": row.get("title") or row.get("name"),
            "actors": actors,
            "required": [
                str(a.get("character_id"))
                for a in (row.get("actors") or [])
                if isinstance(a, dict) and a.get("required")
            ],
            "lines": len(row.get("lines") or []),
            "trigger": row.get("trigger"),
        })
    return sorted(out, key=lambda s: str(s["id"]))


def dialogue_show(pack_dir: str | Path, npc_id: str) -> dict[str, Any]:
    """The NPC's trees, their selectors, ranks and gates, plus each token's
    engine-evaluability against the PRIMARY engine's block — the data the
    navigator rail and the gate ribbon render (README Q4, Q3)."""
    res = resolve_npc(pack_dir, npc_id)
    dialogue = res.dialogue
    trees, source = npc_trees(res.row, res.npc_id, dialogue)
    blocks = res.engine_blocks()
    tables = operand_tables(res.pack, res.spec, dialogue)
    slots, claims, lag = legacy_projection(trees, res.legacy_fields)
    out_trees: list[dict[str, Any]] = []
    for tree in trees:
        selector = tree.get("selector")
        rows = [
            describe_token(row, scope="selector", dialogue=dialogue, blocks=blocks, tables=tables)
            for row in ((selector or {}).get("rows") or [])
        ]
        gates: list[dict[str, Any]] = []
        for node_id, node in (tree.get("nodes") or {}).items():
            for index, choice in enumerate(node.get("choices") or []):
                if not (choice.get("conditions") or choice.get("effects")):
                    continue
                gates.append({
                    "node_id": node_id,
                    "choice": index,
                    "text": choice.get("text"),
                    "conditions": [
                        describe_token(t, scope="tree", dialogue=dialogue, blocks=blocks, tables=tables)
                        for t in choice.get("conditions") or []
                    ],
                    "effects": [
                        describe_token(
                            t, scope="tree", dialogue=dialogue, blocks=blocks, tables=tables, kind="effect"
                        )
                        for t in choice.get("effects") or []
                    ],
                })
        out_trees.append({
            "tree_id": tree.get("tree_id"),
            "label": tree.get("label"),
            "axis": tree.get("axis"),
            "rank": tree.get("rank"),
            "selector": None if selector is None else {"rows": rows},
            "fallback": selector is None,
            "entry_node_id": tree.get("entry_node_id"),
            "nodes": len(tree.get("nodes") or {}),
            "choices": sum(len(n.get("choices") or []) for n in (tree.get("nodes") or {}).values()),
            "terminal_nodes": sorted(
                node_id for node_id, node in (tree.get("nodes") or {}).items()
                if not (node.get("choices") or [])
            ),
            "gates": gates,
            "legacy_slot": claims.get(str(tree.get("tree_id"))),
        })
    engine = res.spec.primary_engine() or {}
    return {
        "npc": res.npc_id,
        "name": res.row.get("name"),
        "quest_id": res.row.get("quest_id"),
        "source": source,
        "storage_field": res.field,
        "legacy_fields": res.legacy_fields,
        "legacy_written": sorted(slots),
        "engine": {"id": engine.get("id"), "evaluable_namespaces": blocks},
        "selector_axes": list(dialogue.selector_axes),
        "trees": out_trees,
        "scenes": _scenes_for(res.pack, res.spec, dialogue, res.npc_id),
        "warnings": lag,
    }


# ---------------------------------------------------------------------------
# dialogue validate
# ---------------------------------------------------------------------------


def _reachable(tree: dict) -> set[str]:
    nodes = tree.get("nodes") or {}
    entry = str(tree.get("entry_node_id") or "start")
    if entry not in nodes:
        return set()
    seen, stack = {entry}, [entry]
    while stack:
        node = nodes.get(stack.pop()) or {}
        for choice in node.get("choices") or []:
            target = choice.get("next_node_id")
            if target and target in nodes and target not in seen:
                seen.add(target)
                stack.append(target)
    return seen


def validate_trees(
    trees: list[dict],
    *,
    dialogue: DialogueSpec,
    blocks: dict[str, Any] | None,
    tables: dict[str, set[str]],
    legacy_fields: list[str],
) -> dict[str, list[str]]:
    """``{errors[], warnings[]}`` over one character's trees.

    Errors (the ONLY blocking cases, README's state table): a tree with nodes
    but no entry node, an unparseable / illegal token, an unresolved entity
    id, a duplicate tree id. Everything else — unreachable nodes, dangling
    targets, uncoverable selector rows, engine lag — is a WARNING (doctrine
    10: authoring is never blocked by what the runtime can evaluate).
    """
    errors: list[str] = []
    warnings: list[str] = []
    seen_ids: set[str] = set()
    seen_selectors: dict[str, str] = {}
    fallback_at: tuple[int, str] | None = None
    for tree in sorted(trees, key=lambda t: (int(t.get("rank") or 0), str(t.get("tree_id")))):
        tree_id = str(tree.get("tree_id"))
        if tree_id in seen_ids:
            errors.append(f"duplicate tree_id {tree_id!r} — tree ids are the selector's addresses")
        seen_ids.add(tree_id)
        nodes = tree.get("nodes") or {}
        entry = str(tree.get("entry_node_id") or "start")
        if nodes and entry not in nodes:
            errors.append(f"tree {tree_id!r}: entry node {entry!r} is not in the tree")
        if not nodes:
            warnings.append(f"tree {tree_id!r} is empty — the rail offers '+ author this tree'")
        selector = tree.get("selector")
        rank = int(tree.get("rank") or 0)
        if selector is None:
            if fallback_at is not None:
                warnings.append(
                    f"tree {tree_id!r} is ranked after the fallback {fallback_at[1]!r} — "
                    "first match wins, so it can never be selected"
                )
            else:
                fallback_at = (rank, tree_id)
        else:
            key = "|".join(sorted(str(r) for r in (selector.get("rows") or [])))
            if key in seen_selectors:
                warnings.append(
                    f"tree {tree_id!r} has the same selector as {seen_selectors[key]!r} — "
                    "first match wins, so this row can never be reached"
                )
            else:
                seen_selectors[key] = tree_id
            for row in selector.get("rows") or []:
                described = describe_token(
                    row, scope="selector", dialogue=dialogue, blocks=blocks, tables=tables
                )
                if not described["legal"]:
                    errors.append(f"tree {tree_id!r} selector: {described['reason']}")
                    continue
                if described.get("unresolved"):
                    errors.append(f"tree {tree_id!r} selector: {described['unresolved']}")
                if not described["engine_evaluable"]:
                    warnings.append(f"tree {tree_id!r} selector: {described['engine_reason']}")
        reachable = _reachable(tree)
        for node_id in sorted(set(nodes) - reachable):
            warnings.append(
                f"tree {tree_id!r}: node {node_id!r} is unreachable from {entry!r} — "
                "it keeps its gates and still ships"
            )
        for node_id, node in nodes.items():
            for index, choice in enumerate(node.get("choices") or []):
                target = choice.get("next_node_id")
                if target and target not in nodes:
                    warnings.append(
                        f"tree {tree_id!r}: {node_id}[{index}] points at {target!r}, which is not in "
                        "the tree — it ends the conversation in game"
                    )
                for token in choice.get("conditions") or []:
                    described = describe_token(
                        token, scope="tree", dialogue=dialogue, blocks=blocks, tables=tables
                    )
                    if not described["legal"]:
                        errors.append(f"tree {tree_id!r} {node_id}[{index}]: {described['reason']}")
                        continue
                    if described.get("unresolved"):
                        errors.append(f"tree {tree_id!r} {node_id}[{index}]: {described['unresolved']}")
                    if not described["engine_evaluable"]:
                        warnings.append(
                            f"tree {tree_id!r} {node_id}[{index}]: {described['engine_reason']}"
                        )
                for token in choice.get("effects") or []:
                    described = describe_token(
                        token, scope="tree", dialogue=dialogue, blocks=blocks, tables=tables,
                        kind="effect",
                    )
                    if not described["legal"]:
                        errors.append(f"tree {tree_id!r} {node_id}[{index}]: {described['reason']}")
                        continue
                    if described.get("unresolved"):
                        errors.append(f"tree {tree_id!r} {node_id}[{index}]: {described['unresolved']}")
                    if not described["engine_evaluable"]:
                        warnings.append(
                            f"tree {tree_id!r} {node_id}[{index}]: {described['engine_reason']}"
                        )
    if fallback_at is None and trees:
        warnings.append(
            "no fallback tree (one with no selector) — a state matching no selector row plays nothing; "
            "the engine still reads dialogue_tree"
        )
    _slots, _claims, lag = legacy_projection(trees, legacy_fields)
    warnings.extend(lag)
    return {"errors": errors, "warnings": warnings}


def dialogue_validate(pack_dir: str | Path, npc_id: str) -> dict[str, Any]:
    """``{errors[], warnings[]}`` for one NPC — the validator panel and the
    save sheet read this. Warnings never block."""
    res = resolve_npc(pack_dir, npc_id)
    dialogue = res.dialogue
    trees, source = npc_trees(res.row, res.npc_id, dialogue)
    report = validate_trees(
        trees,
        dialogue=dialogue,
        blocks=res.engine_blocks(),
        tables=operand_tables(res.pack, res.spec, dialogue),
        legacy_fields=res.legacy_fields,
    )
    return {"npc": res.npc_id, "source": source, "trees": len(trees), **report}


# ---------------------------------------------------------------------------
# dialogue update
# ---------------------------------------------------------------------------


def _summary(value: Any) -> Any:
    """The journal's per-key value summary. A whole dialogue tree in a
    ``from``/``to`` pair would make the journal unreadable and enormous; the
    fine-grained per-field diff rides in ``detail.ops`` (one entry per
    EditOp, the design's ask), and the file's exact prior bytes are in the
    CAS under ``before_hash``, so nothing is lost."""
    if isinstance(value, list):
        return {
            "trees": len(value),
            "tree_ids": [str(t.get("tree_id")) for t in value if isinstance(t, dict)],
            "nodes": sum(len(t.get("nodes") or {}) for t in value if isinstance(t, dict)),
        }
    if isinstance(value, dict):
        return {"nodes": len(value.get("nodes") or {})}
    return value


def dialogue_update(
    pack_dir: str | Path,
    npc_id: str,
    ops: Any,
    *,
    actor: str = "user",
    session: str | None = None,
) -> dict[str, Any]:
    """Apply an ``EditOp`` list to one NPC's dialogue as ONE batch.

    Fail-closed: the ops are applied purely first, then validated; a single
    error means nothing is written. On success the row's ``dialogue_trees``
    and the legacy four are rewritten together (the compat shim), the file is
    snapshotted into the CAS once, and one journal event carries the per-key
    diff plus ``detail.ops`` — one entry per op with its own field diff.

    The FIRST save on a legacy NPC materializes ``dialogue_trees`` from the
    four legacy keys, so it is a real diff even when the ops changed nothing
    else — the read-both shim upgrading on write, never a migration verb and
    never a background rewrite (doctrine 6). Every save after that obeys the
    write core's "no diff → no write, no journal" rule unchanged.
    """
    res = resolve_npc(pack_dir, npc_id)
    dialogue = res.dialogue
    trees, source = npc_trees(res.row, res.npc_id, dialogue)
    doc, details = apply_ops({"character_id": res.npc_id, "trees": trees}, ops)
    blocks = res.engine_blocks()
    tables = operand_tables(res.pack, res.spec, dialogue)
    report = validate_trees(
        doc["trees"], dialogue=dialogue, blocks=blocks, tables=tables,
        legacy_fields=res.legacy_fields,
    )
    if report["errors"]:
        raise ValueError(
            "dialogue update refused (fail-closed): " + "; ".join(report["errors"])
        )
    keys = [res.field, *res.legacy_fields]
    before = {key: copy.deepcopy(res.row.get(key)) for key in keys}
    warnings: list[str] = list(report["warnings"])
    entity = res.entity

    def apply(document: Any, _changes: dict) -> dict[str, dict]:
        row, _accessor = _locate(entity, document, res.npc_id)
        # `validate_trees` above already ran the same `legacy_projection`, so
        # every engine-lag line it found is here twice otherwise — one warning
        # said twice reads as two problems.
        warnings.extend(w for w in write_back(row, doc["trees"], dialogue) if w not in warnings)
        # The db core's stamp rule, unchanged: a row that HAS a status gets
        # `user_edited`; a dungeon row without one is not given one, so the
        # engine's file shape is untouched (`db_ops.update_db_row`).
        if "status" in row:
            row["status"] = "user_edited"
        diff: dict[str, dict] = {}
        for key in keys:
            old, new = before[key], row.get(key)
            if old != new:
                diff[key] = {"from": _summary(old), "to": _summary(new)}
        return diff

    result = write_document(
        res.pack,
        artifact_id=f"npc:{res.npc_id}",
        rel_path=res.rel_path,
        document=res.document,
        changes={res.field: doc["trees"]},
        wall=_wall(entity),
        routed=None,  # this IS the verb `npc.routed` sends dialogue fields to
        apply=apply,
        user_edited=False,
        actor=actor,
        session=session,
        detail={"kind": "dialogue_update", "type": "npc", "ops": details},
        warnings=warnings,
    )
    row, _accessor = _locate(entity, result["document"], res.npc_id)
    return {
        "npc": res.npc_id,
        "source": source,
        "ops": details,
        "trees": row.get(res.field) or [],
        "legacy_written": [key for key in res.legacy_fields if key in row],
        "changed": result["changed"],
        "no_change": bool(result.get("no_change")),
        "warnings": result["warnings"],
        "before_hash": result.get("before_hash"),
        "after_hash": result.get("after_hash"),
    }


# ---------------------------------------------------------------------------
# dialogue test  (the UNSAVED buffer — a tree payload, never a pack lookup)
# ---------------------------------------------------------------------------


_PackContext = tuple[DialogueSpec, dict[str, Any] | None, Path | None, PackSpec | None]


def _pack_context(pack_dir: str | Path | None) -> _PackContext:
    if pack_dir is None:
        return spec_of(None), None, None, None
    pack, resolved = _resolve(pack_dir)
    spec = resolved.spec
    return spec_of(spec.dialogue), primary_engine_blocks(spec), pack, spec


def dialogue_test(
    tree: dict,
    state: Any,
    *,
    pack_dir: str | Path | None = None,
    node_id: str | None = None,
    choose: int | None = None,
) -> dict[str, Any]:
    """Walk ONE tree payload — the unsaved buffer, not a pack lookup
    (`PLAN.md:256`) — against a simulated state.

    Returns the node, per-choice ``pass`` / ``fail`` / ``unevaluable`` with
    the FAILING CONDITION NAMED, the effect ledger, and the post-effect state.
    ``--choose`` applies that choice's effects and reports the next node, so a
    caller walks the tree one round-trip at a time.
    """
    dialogue, blocks, pack, spec = _pack_context(pack_dir)
    tables = operand_tables(pack, spec, dialogue) if pack is not None and spec is not None else {}
    tree = stored_tree(tree)
    nodes = tree.get("nodes") or {}
    current = str(node_id or tree.get("entry_node_id") or "start")
    if current not in nodes:
        raise ValueError(
            f"tree {tree.get('tree_id')!r} has no node {current!r} (nodes: {sorted(nodes)})"
        )
    sim = normalize_state(state)
    node = nodes[current]
    choices: list[dict[str, Any]] = []
    for index, choice in enumerate(node.get("choices") or []):
        gates = evaluate_conditions(
            choice.get("conditions"), sim, scope="tree", spec=dialogue, engine_blocks=blocks
        )
        effects = [
            describe_token(
                token, scope="tree", dialogue=dialogue, blocks=blocks, tables=tables, kind="effect"
            )
            for token in choice.get("effects") or []
        ]
        choices.append({
            "index": index,
            "text": choice.get("text"),
            "next_node_id": choice.get("next_node_id"),
            "dangling": bool(choice.get("next_node_id")) and choice.get("next_node_id") not in nodes,
            "effects": effects,
            **gates,
        })
    tally = {"pass": 0, "fail": 0, "unevaluable": 0, "error": 0}
    for entry in choices:
        for condition in entry["conditions"]:
            verdict = str(condition["verdict"])
            tally[verdict] = tally.get(verdict, 0) + 1
    out: dict[str, Any] = {
        "tree_id": tree.get("tree_id"),
        "entry_node_id": tree.get("entry_node_id"),
        "node": {
            "node_id": current,
            "speaker": node.get("speaker") or tree.get("character_id"),
            "prompt": node.get("prompt"),
            "terminal": not (node.get("choices") or []),
        },
        "choices": choices,
        "gates": tally,
        "state": sim,
        "post_effect_state": sim,
        "fired": [],
        "chose": None,
        "next_node_id": None,
    }
    if choose is None:
        return out
    if not (0 <= int(choose) < len(choices)):
        raise ValueError(f"--choose {choose} is outside 0..{max(len(choices) - 1, 0)}")
    picked = choices[int(choose)]
    if not picked["pass"]:
        out["chose"] = int(choose)
        out["refused"] = (
            f"choice {choose} is blocked: {picked['failing_reason']} "
            f"(blocked by 1 of {len(picked['conditions'])} conditions)"
        )
        return out
    source = (node.get("choices") or [])[int(choose)]
    post, fired = apply_effects(source.get("effects"), sim, spec=dialogue, engine_blocks=blocks)
    out["chose"] = int(choose)
    out["fired"] = fired
    out["post_effect_state"] = post
    out["next_node_id"] = picked["next_node_id"] if not picked["dangling"] else None
    return out


# ---------------------------------------------------------------------------
# dialogue select
# ---------------------------------------------------------------------------


def _selector_verdict(
    tree: dict, sim: dict, dialogue: DialogueSpec, blocks: dict[str, Any] | None
) -> dict[str, Any]:
    selector = tree.get("selector")
    if selector is None:
        return {
            "pass": True, "conditions": [], "failing_condition": None,
            "failing_reason": None, "unevaluable": [],
        }
    return evaluate_conditions(
        selector.get("rows"), sim, scope="selector", spec=dialogue, engine_blocks=blocks
    )


def dialogue_select(pack_dir: str | Path, npc_id: str, state: Any) -> dict[str, Any]:
    """Which tree the state selects — AND why each other tree did not.

    First match wins in rank order; ALL of a selector's rows must pass. The
    ``engine`` block is the selector-level engine-lag case (`PLAN.md:258`):
    the engine SKIPS a row it cannot evaluate and falls through to the next
    one, so it may play a different tree than the tester does. When it does,
    ``engine.diverges`` is true and the reason is named — loud, never
    blocking.
    """
    res = resolve_npc(pack_dir, npc_id)
    dialogue = res.dialogue
    blocks = res.engine_blocks()
    trees, source = npc_trees(res.row, res.npc_id, dialogue)
    ordered = sorted(trees, key=lambda t: (int(t.get("rank") or 0), str(t.get("tree_id"))))
    _slots, claims, lag = legacy_projection(ordered, res.legacy_fields)
    sim = normalize_state(state)
    selected: str | None = None
    engine_pick: str | None = None
    rows: list[dict[str, Any]] = []
    for tree in ordered:
        tree_id = str(tree.get("tree_id"))
        verdict = _selector_verdict(tree, sim, dialogue, blocks)
        engine_blind = [c for c in verdict["conditions"] if not c["engine_evaluable"]]
        if selected is None and verdict["pass"]:
            selected, status, why = tree_id, "selected", None
        elif not verdict["pass"]:
            status = "blocked"
            why = f"blocked by {verdict['failing_reason']}"
        else:
            status = "shadowed"
            why = f"a higher-ranked tree ({selected}) matched first"
        if engine_pick is None and not engine_blind and verdict["pass"]:
            engine_pick = tree_id
        rows.append({
            "tree_id": tree_id,
            "label": tree.get("label"),
            "axis": tree.get("axis"),
            "rank": tree.get("rank"),
            "fallback": tree.get("selector") is None,
            "selector": tree.get("selector"),
            "status": status,
            "would_play": status == "selected",
            "rows": verdict["conditions"],
            "why_not": why,
            "engine_blind_rows": [c["token"] for c in engine_blind],
            "legacy_slot": claims.get(tree_id),
        })
    diverges = engine_pick != selected
    return {
        "npc": res.npc_id,
        "source": source,
        "selected": selected,
        "selected_label": next((r["label"] for r in rows if r["tree_id"] == selected), None),
        "trees": rows,
        "engine": {
            "id": (res.spec.primary_engine() or {}).get("id"),
            "selected": engine_pick,
            "legacy_slot": claims.get(str(engine_pick)) if engine_pick else None,
            "diverges": diverges,
            "reason": (
                f"the engine cannot evaluate every selector row above {selected!r}, so it falls "
                f"through to {engine_pick!r} while the tester picks {selected!r} — "
                "author freely, the runtime lags (doctrine 10)"
                if diverges else None
            ),
        },
        "state": sim,
        "warnings": lag,
    }


# ---------------------------------------------------------------------------
# scene verbs
# ---------------------------------------------------------------------------


class ResolvedScene:
    """One scene row located in ``events/events.json`` (S7) — the event
    collection is the CAS unit, exactly as it is for ``db update``."""

    __slots__ = ("created", "document", "entity", "pack", "rel_path", "row", "scene_id", "spec")

    def __init__(self, pack_dir: str | Path, scene_id: Any, *, create: bool = False, title: str = "") -> None:
        pack, resolved = _resolve(pack_dir)
        spec = resolved.spec
        if "dialogue" not in spec.capabilities or spec.dialogue is None:
            raise ValueError(f"{pack} declares no 'dialogue' capability — scenes need it (§5.1a)")
        self.pack, self.spec = pack, spec
        self.entity = _entity(spec, "event")
        self.rel_path = str(self.entity.layout.get("path"))
        self.document = _read_collection(pack, self.entity)
        self.created = False
        dialogue = spec_of(spec.dialogue)
        if scene_id is None:
            if not create:
                raise ValueError("--scene is required unless --create allocates a new id")
            scene_id = self._allocate()
        self.scene_id = str(scene_id)
        try:
            self.row, _accessor = _locate(self.entity, self.document, self.scene_id)
        except FileNotFoundError:
            if not create:
                raise
            self.row = blank_scene(self._coerce(self.scene_id), dialogue, title=title)
            self.document.append(self.row) if isinstance(self.document, list) else self.document.update(
                {self.scene_id: self.row}
            )
            self.created = True
        if not self.created and not is_scene(self.row, dialogue):
            raise ValueError(
                f"event {self.scene_id} has type {self.row.get('type')!r}, not "
                f"{(dialogue.scene or {}).get('event_type', 'scene')!r} — `db update` owns that row"
            )

    def _coerce(self, scene_id: str) -> Any:
        try:
            return int(scene_id)
        except (TypeError, ValueError):
            return scene_id

    def _allocate(self) -> Any:
        base = int(((self.entity.id_alloc or {}).get("base")) or 0)
        rows = load_rows(self.pack, self.entity)
        used = [int(k) for k in rows if str(k).lstrip("-").isdigit()]
        return max([*used, base - 1]) + 1


def _scene_context(res: ResolvedScene) -> tuple[DialogueSpec, dict[str, Any] | None, dict[str, set[str]]]:
    dialogue = spec_of(res.spec.dialogue)
    blocks = primary_engine_blocks(res.spec)
    return dialogue, blocks, operand_tables(res.pack, res.spec, dialogue)


def validate_scene(
    scene: dict,
    *,
    dialogue: DialogueSpec,
    blocks: dict[str, Any] | None,
    tables: dict[str, set[str]],
    positions: set[str] | None = None,
) -> dict[str, list[str]]:
    """``{errors[], warnings[]}`` for one scene. The S7 invariant is checked
    here as an ERROR: a scene must never carry an ``event_positions`` entry,
    because the engine would then trigger it as a ``CombatEvent``.

    P.2.1's "restricted to the scene's ``actors[]``" is checked here too, as a
    WARNING (doctrine 10 — the token is legal, it just can never pass): the
    operand descriptor's ``restrict_to`` (``grammar.namespace_shape``'s slot)
    is read against this scene's own roster, so it stops being a slot nothing
    consumes. The scene's actor set is scene-local, so it is checked here
    rather than by widening ``operand_tables``, which is pack-wide."""
    errors: list[str] = []
    warnings: list[str] = []
    triggers = list((dialogue.scene or {}).get("triggers") or [])
    if triggers and scene.get("trigger") not in triggers:
        errors.append(f"trigger {scene.get('trigger')!r} is not one of {triggers}")
    actors = [str(a.get("character_id")) for a in scene.get("actors") or []]
    known_npcs = tables.get("actor")
    for actor in actors:
        if known_npcs is not None and actor not in known_npcs:
            errors.append(f"actor {actor} does not resolve to an npc row in this pack")
    if not actors:
        warnings.append("the scene has no actors — nothing can speak in it")

    def _off_roster(described: dict[str, Any], where: str) -> None:
        """P.2.1's ``restrict_to: "scene.actors"``, consumed at last: an
        ``actor:`` operand naming someone this scene does not cast can never
        be satisfied — ``walk_scene`` only ever sees presence for the actors
        the caller lists. A WARNING, never an error (doctrine 10): the token
        is legal and the row is real, it just cannot pass here. Wording
        matches the speaker warning below."""
        descriptor = (dialogue.operands or {}).get(str(described.get("namespace"))) or {}
        if descriptor.get("restrict_to") != "scene.actors":
            return
        entity_id = (described.get("slots") or {}).get("entity_id")
        if entity_id is None or str(entity_id) in actors:
            return
        warnings.append(
            f"{where}: {entity_id} is not an actor of this scene — the gate can never pass "
            f"(scene {scene.get('id')} casts {actors or 'nobody'})"
        )
    if positions and str(scene.get("id")) in positions:
        errors.append(
            f"scene {scene.get('id')} has an event_positions entry — a scene must never get one "
            "(P.9 S7), or the engine triggers it as a combat event"
        )
    for token in scene.get("settings") or []:
        described = describe_token(token, scope="scene", dialogue=dialogue, blocks=blocks, tables=tables)
        if not described["legal"]:
            errors.append(f"scene setting: {described['reason']}")
        elif described.get("unresolved"):
            errors.append(f"scene setting: {described['unresolved']}")
        elif not described["engine_evaluable"]:
            warnings.append(f"scene setting: {described['engine_reason']}")
        if described["legal"]:
            _off_roster(described, "scene setting")
    for token in scene.get("on_finish") or []:
        described = describe_token(
            token, scope="scene", dialogue=dialogue, blocks=blocks, tables=tables, kind="effect"
        )
        if not described["legal"]:
            errors.append(f"scene on_finish: {described['reason']}")
        elif described.get("unresolved"):
            errors.append(f"scene on_finish: {described['unresolved']}")
        elif not described["engine_evaluable"]:
            warnings.append(f"scene on_finish: {described['engine_reason']}")
    numbers = [int(line.get("n") or 0) for line in scene.get("lines") or []]
    for line in scene.get("lines") or []:
        where = f"line {line.get('n')}"
        speaker = line.get("speaker")
        if line.get("k") == "line" and speaker is not None and str(speaker) not in actors:
            warnings.append(f"{where}: {speaker} is not an actor of this scene — the line never plays")
        for token in line.get("conditions") or []:
            described = describe_token(
                token, scope="scene", dialogue=dialogue, blocks=blocks, tables=tables
            )
            if not described["legal"]:
                errors.append(f"{where}: {described['reason']}")
            elif described.get("unresolved"):
                errors.append(f"{where}: {described['unresolved']}")
            elif not described["engine_evaluable"]:
                warnings.append(f"{where}: {described['engine_reason']}")
            if described["legal"]:
                _off_roster(described, where)
        for option in line.get("options") or []:
            if option.get("to") is not None and int(option["to"]) not in numbers:
                warnings.append(f"{where}: an option points at line {option['to']}, which does not exist")
            for token in option.get("conditions") or []:
                described = describe_token(
                    token, scope="scene", dialogue=dialogue, blocks=blocks, tables=tables
                )
                if not described["legal"]:
                    errors.append(f"{where} option: {described['reason']}")
                elif described.get("unresolved"):
                    errors.append(f"{where} option: {described['unresolved']}")
                elif not described["engine_evaluable"]:
                    warnings.append(f"{where} option: {described['engine_reason']}")
                if described["legal"]:
                    _off_roster(described, f"{where} option")
    return {"errors": errors, "warnings": warnings}


def _event_positions(pack: Path, spec: PackSpec) -> set[str]:
    """Every event id that HAS a grid placement — the set a scene must stay
    out of (S7). Read through the room ``GridKind``'s own placements block,
    never a literal key name.

    The grid FILES are enumerated by ``tools_read.grid_ids`` — the same glob
    ``pack_info`` counts rooms with — not by joining through the room rows.
    Joining was silently empty on every tree that ships today: the legacy
    trees have no ``rooms/rooms.json``, and even with rows loaded the bible
    copies carry ``maze_ref: ""`` and no ``id``, so the path came out as
    ``rooms/None/maze.json`` and this whole guard never fired.
    """
    from canon.agent.tools_read import grid_ids

    out: set[str] = set()
    grid = spec.grids.get("room")
    if grid is None:
        return out
    blocks = [
        (key, block) for key, block in grid.placements.items() if block.get("kind") == "event"
    ]
    if not blocks:
        return out
    for ids in grid_ids(pack, grid.path_template):
        path = pack / grid.path_template.format(**ids)
        if not path.is_file():
            continue
        try:
            maze = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for key, block in blocks:
            raw = maze.get(key)
            entries = raw.values() if isinstance(raw, dict) else (raw or [])
            for entry in entries:
                if isinstance(entry, dict) and entry.get(block.get("id")) is not None:
                    out.add(str(entry[block["id"]]))
    return out


def scene_update(
    pack_dir: str | Path,
    scene_id: Any,
    ops: Any,
    *,
    actor: str = "user",
    session: str | None = None,
    create: bool = False,
    title: str = "",
) -> dict[str, Any]:
    """Apply scene ``EditOp``s to one ``type: "scene"`` event row.

    Scene writes go through the EVENT kind's row path (S7) — the same
    collection file, the same write core, the same journal — and never touch
    ``event_positions``, so the engine is never handed a scene to trigger.
    ``--create`` is the row-creation door: ``db update`` refuses every
    scene-only field (they are ``routed`` to this verb), so this verb has to
    be able to make the row it owns.
    """
    res = ResolvedScene(pack_dir, scene_id, create=create, title=title)
    dialogue, blocks, tables = _scene_context(res)
    scene = normalize_scene(res.row, dialogue)
    updated, details = apply_scene_ops(scene, ops) if ops else (scene, [])
    report = validate_scene(
        updated, dialogue=dialogue, blocks=blocks, tables=tables,
        positions=_event_positions(res.pack, res.spec),
    )
    if report["errors"]:
        raise ValueError("scene update refused (fail-closed): " + "; ".join(report["errors"]))
    # A CREATED scene compares against nothing: every key is new, so the diff
    # is never empty and the write core never mistakes a fresh row for a no-op.
    before = (
        {}
        if res.created
        else {key: copy.deepcopy(res.row.get(key)) for key in ("type", *_SCENE_KEYS)}
    )
    warnings = list(report["warnings"])
    entity, sid = res.entity, res.scene_id
    created = res.created

    def apply(document: Any, _changes: dict) -> dict[str, dict]:
        if created:
            row = _find_or_append(document, entity, sid, updated)
        else:
            row, _accessor = _locate(entity, document, sid)
        for key in ("type", *_SCENE_KEYS):
            row[key] = copy.deepcopy(updated[key])
        if "status" in row:
            row["status"] = "user_edited"
        diff: dict[str, dict] = {}
        for key in ("type", *_SCENE_KEYS):
            if before.get(key) != row[key]:
                diff[key] = {"from": before.get(key), "to": row[key]}
        return diff

    result = write_document(
        res.pack,
        artifact_id=f"event:{sid}",
        rel_path=res.rel_path,
        document=res.document,
        changes={"lines": updated["lines"]},
        wall=_wall(entity),
        routed=None,  # this IS the verb `event.routed` sends scene fields to
        apply=apply,
        user_edited=False,
        actor=actor,
        session=session,
        detail={"kind": "scene_update", "type": "event", "scene": sid, "ops": details},
        op="create" if created else "edit",
        warnings=warnings,
    )
    row, _accessor = _locate(entity, result["document"], sid)
    return {
        "scene": sid,
        "created": created,
        "ops": details,
        "row": row,
        "changed": result["changed"],
        "no_change": bool(result.get("no_change")),
        "warnings": result["warnings"],
        "before_hash": result.get("before_hash"),
        "after_hash": result.get("after_hash"),
    }


#: The keys a scene save writes onto the event row. ``name`` / ``description``
#: are not scene fields but ARE required by the engine's ``Event`` model
#: (``canon.dialogue.scenes.blank_scene`` explains why they must be present);
#: they ride here so a created row keeps them and an edited one never loses
#: them.
_SCENE_KEYS = (
    "name", "description", "title", "actors", "settings", "trigger", "once", "on_finish", "lines",
)


def _find_or_append(document: Any, entity: Any, scene_id: str, seed: dict) -> dict:
    try:
        row, _accessor = _locate(entity, document, scene_id)
        return row
    except FileNotFoundError:
        row = {"id": seed.get("id"), "type": seed.get("type")}
        if isinstance(document, list):
            document.append(row)
        else:
            document[scene_id] = row
        return row


def scene_validate(pack_dir: str | Path, scene_id: Any) -> dict[str, Any]:
    """``{errors[], warnings[]}`` for one scene row."""
    res = ResolvedScene(pack_dir, scene_id)
    dialogue, blocks, tables = _scene_context(res)
    scene = normalize_scene(res.row, dialogue)
    report = validate_scene(
        scene, dialogue=dialogue, blocks=blocks, tables=tables,
        positions=_event_positions(res.pack, res.spec),
    )
    return {"scene": res.scene_id, "lines": len(scene.get("lines") or []), **report}


def scene_test(scene: dict, state: Any, *, pack_dir: str | Path | None = None) -> dict[str, Any]:
    """Play a scene payload (the unsaved buffer) against a simulated state
    that additionally carries ACTOR PRESENCE — the one test control scenes
    need that trees do not (README, screen 08)."""
    dialogue, blocks, _pack, _spec = _pack_context(pack_dir)
    return walk_scene(normalize_scene(scene, dialogue), state, spec=dialogue, engine_blocks=blocks)


def load_scene(pack_dir: str | Path, scene_id: Any) -> dict[str, Any]:
    """The stored scene row, normalized — what ``scene test`` walks when the
    caller has no unsaved buffer."""
    res = ResolvedScene(pack_dir, scene_id)
    return normalize_scene(res.row, spec_of(res.spec.dialogue))


def load_tree(pack_dir: str | Path, npc_id: str, tree_id: str | None = None) -> dict[str, Any]:
    """One stored tree — what ``dialogue test`` walks when the caller has no
    unsaved buffer.

    With no *tree_id* this is the FIRST TREE IN RANK ORDER, the same
    precedence ``dialogue_select`` walks — not "the selected one": selection
    needs a state payload, and this verb takes none, so for a legacy
    four-variant NPC the default is ``<npc>:incomplete`` (rank 0) while
    ``dialogue select`` on a ``not_started`` quest answers ``<npc>:default``
    (rank 999, the fallback). Pass ``--tree-id`` — or use ``dialogue
    select`` — to test what a given state would play.
    """
    res: ResolvedNpc = resolve_npc(pack_dir, npc_id)
    trees, _source = npc_trees(res.row, res.npc_id, res.dialogue)
    if not trees:
        raise FileNotFoundError(f"npc {npc_id} has no dialogue trees")
    if tree_id is None:
        return sorted(trees, key=lambda t: (int(t.get("rank") or 0), str(t.get("tree_id"))))[0]
    for tree in trees:
        if str(tree.get("tree_id")) == str(tree_id):
            return tree
    raise FileNotFoundError(
        f"npc {npc_id} has no tree {tree_id!r} (have {[t.get('tree_id') for t in trees]})"
    )

"""The doctrine-1 write core — ONE pipeline every canon mutation verb mounts on
(master §3.0-A / C3; P0 paper P.7.2; row P0-6).

    resolve → protected-field wall → apply → fail-closed validate → warnings
    (surface, never block) → ``user_edited`` stamp → journal per-field diff →
    CAS snapshot of the FILE

``write_document`` is that pipeline as one function. Everything a verb
differs in is a PARAMETER — the wall, the container set, the routed map,
the apply/validate/warn steps, the writer — never a constant inside it
(P.7.2: "the reusable part is the matcher and the pipeline, not the set").
Consumers today: ``db update`` (``canon.db_ops``) and ``world update``
(``canon.world_ops``) run the whole pipeline (``write_document``); the
builder-less ``db new``, ``db define`` / ``db evolve``, ``registry set`` /
capability enablement and the registry synthesis (``canon.registry_ops``)
mount on its tail (``commit_document``). THREE verbs deliberately keep
their own snapshot/write/``record`` chain and are NOT on this core:
``db schema --set`` (it writes a schema document, not a row, and journals
``schema:<type>``), the builder-backed ``db new`` and ``db complete`` (the
seed's builder supplies its own adapter and a provenance stamp hook that
must run BETWEEN the write and the journal, so the row lands byte-identical
to pipeline generation) — grep ``provenance.record`` for exactly those.
Phase 2's ``tune set`` mounts here with its own wall + key table (W2.1).

What this module extends: ``update_db_row``'s per-field discipline
(``packs/platformer/ops.py``) — the protected-leaf matcher, the whole-
container refusal, the "no diff → no write, no journal" rule, the
``before_hash``/``after_hash`` CAS pair and the ``detail.changed`` diff shape
are lifted from it verbatim and parameterized; ``canon.provenance`` does the
journaling and the object store (adopt-on-write: the first mutation on a
legacy tree creates ``.canon/`` because ``record``/``snapshot_bytes``
``mkdir`` it — Phase 0 §8.1, nothing here re-implements that).

The address grammar shared by every consumer (P.1 list-container
addressing + P.7.1 world addressing): dotted segments; ``<c>[<i>]`` a
0-based list index that must exist; ``<c>[+]`` append; ``<list>[<key>=<value>]``
the one list item whose ``<key>`` equals ``<value>`` (never an index for
world fields — the world verb refuses numeric indices before it gets here);
``= null`` deletes a nested key or list item (a top-level delete is refused).

Deliberately absent, by row ownership: the ``tune set`` wall and key table
(W2.1); the A6 additive journal fields (P1-A6 — events written here carry
the pre-A6 shape and stay valid); the Godot/pygame launch seams (W2.0).
"""

from __future__ import annotations

import copy
import re
from collections.abc import Callable, Collection, Iterable
from pathlib import Path
from typing import Any

from canon import provenance
from canon.adapters import GodotOutputAdapter, JsonOutputAdapter

__all__ = [
    "NotYetError",
    "apply_changes",
    "check_wall",
    "commit_document",
    "get_path",
    "leaf_of",
    "pack_adapter",
    "parse_address",
    "set_path",
    "write_document",
]


class NotYetError(ValueError):
    """A structured "not yet" (doctrine 4: disabled-with-a-reason, never
    hidden). ``payload`` rides into the CLI's JSON error so a caller can
    dispatch on ``not_yet`` + ``row`` instead of parsing prose."""

    def __init__(self, message: str, **payload: Any) -> None:
        super().__init__(message)
        self.payload = {"not_yet": True, **payload}


def pack_adapter(pack: str | Path) -> JsonOutputAdapter:
    """The pack's output adapter: Godot-engine packs keep their ``.grid.json``
    siblings in sync (the ``platformer_write._pack_adapter`` rule, now the
    one definition every writer shares)."""
    pack = Path(pack)
    if (pack / "project.godot").is_file():
        return GodotOutputAdapter(pack)
    return JsonOutputAdapter(pack)


# ---------------------------------------------------------------------------
# Address grammar
# ---------------------------------------------------------------------------

_SEGMENT = re.compile(r"^([^\[\]]*)((?:\[[^\[\]]*\])*)$")
_BRACKET = re.compile(r"\[([^\[\]]*)\]")


def parse_address(path: str) -> list[tuple[str, list[str]]]:
    """``"a.b[2].c[+]"`` → ``[("a", []), ("b", ["2"]), ("c", ["+"])]``. A
    malformed segment (stray bracket) is a ``ValueError`` naming the path."""
    if not isinstance(path, str) or not path:
        raise ValueError(f"empty field path {path!r}")
    out: list[tuple[str, list[str]]] = []
    for raw in path.split("."):
        match = _SEGMENT.match(raw)
        if match is None:
            raise ValueError(f"malformed field path {path!r} (segment {raw!r})")
        name, brackets = match.group(1), _BRACKET.findall(match.group(2))
        if not name and not brackets:
            raise ValueError(f"malformed field path {path!r} (empty segment)")
        out.append((name, brackets))
    return out


def leaf_of(path: str) -> str:
    """The LAST named segment, brackets stripped — what the protected wall
    matches on (``stats.animation`` → ``animation``; ``abilities[0].name`` →
    ``name``; ``monster_ids[2]`` → ``monster_ids``)."""
    segments = parse_address(path)
    for name, _ in reversed(segments):
        if name:
            return name
    return segments[-1][0]


def _step_into(node: Any, accessor: str, path: str, *, create: bool) -> Any:
    """One list accessor (``2`` | ``key=value``) applied to *node*; ``+`` is
    only legal as the FINAL accessor and is handled by the setter."""
    if not isinstance(node, list):
        raise ValueError(f"{path!r}: [{accessor}] addresses a list, found {type(node).__name__}")
    if accessor == "+":
        raise ValueError(f"{path!r}: [+] (append) is only valid as the last accessor")
    if "=" in accessor:
        key, _, value = accessor.partition("=")
        for item in node:
            if isinstance(item, dict) and str(item.get(key)) == value:
                return item
        raise ValueError(f"{path!r}: no list item with {key}={value!r}")
    try:
        index = int(accessor)
    except ValueError:
        raise ValueError(f"{path!r}: list accessor [{accessor}] must be an index, [+] or key=value") from None
    if not (0 <= index < len(node)):
        raise ValueError(f"{path!r}: index {index} out of range (0..{len(node) - 1})")
    return node[index]


def _walk(doc: Any, path: str, *, create: bool) -> tuple[Any, str, list[str]]:
    """Descend to the parent of the final segment. Returns ``(parent, name,
    brackets)`` for the last segment; with *create* absent dict containers on
    the way are made (a list accessor never creates)."""
    segments = parse_address(path)
    node = doc
    for name, brackets in segments[:-1]:
        if name:
            if not isinstance(node, dict):
                raise ValueError(f"{path!r}: {name!r} addresses an object, found {type(node).__name__}")
            if name not in node:
                if not create:
                    raise ValueError(f"{path!r}: {name!r} is absent")
                node[name] = {} if not brackets else []
            node = node[name]
        for accessor in brackets:
            node = _step_into(node, accessor, path, create=create)
    name, brackets = segments[-1]
    return node, name, brackets


def get_path(doc: Any, path: str) -> tuple[bool, Any]:
    """``(present, value)`` at *path*; ``(False, None)`` when any segment is
    absent (never raises for absence — only for a type mismatch)."""
    try:
        parent, name, brackets = _walk(doc, path, create=False)
        node = parent
        if name:
            if not isinstance(node, dict) or name not in node:
                return False, None
            node = node[name]
        for accessor in brackets:
            if accessor == "+":
                return False, None
            node = _step_into(node, accessor, path, create=False)
        return True, node
    except ValueError as exc:
        if "absent" in str(exc) or "no list item" in str(exc) or "out of range" in str(exc):
            return False, None
        raise


def set_path(doc: Any, path: str, value: Any) -> tuple[Any, Any]:
    """Write *value* at *path* (``None`` deletes a nested key / list item;
    ``[+]`` appends). Returns ``(old, new)`` for the diff — ``old`` is
    ``None`` when the slot did not exist. A top-level delete is refused."""
    parent, name, brackets = _walk(doc, path, create=True)
    if not brackets:
        if not isinstance(parent, dict):
            raise ValueError(f"{path!r}: {name!r} addresses an object, found {type(parent).__name__}")
        old = parent.get(name)
        if value is None:
            if parent is doc:
                raise ValueError(f"cannot delete top-level field {path!r}")
            parent.pop(name, None)
        else:
            parent[name] = value
        return old, value
    node = parent
    if name:
        if not isinstance(node, dict):
            raise ValueError(f"{path!r}: {name!r} addresses an object, found {type(node).__name__}")
        if name not in node:
            node[name] = []
        node = node[name]
    for accessor in brackets[:-1]:
        node = _step_into(node, accessor, path, create=True)
    last = brackets[-1]
    if not isinstance(node, list):
        raise ValueError(f"{path!r}: [{last}] addresses a list, found {type(node).__name__}")
    if last == "+":
        if value is None:
            raise ValueError(f"{path!r}: [+] appends a value, got null")
        node.append(value)
        return None, value
    if "=" in last:
        key, _, match_value = last.partition("=")
        for index, item in enumerate(node):
            if isinstance(item, dict) and str(item.get(key)) == match_value:
                old = item
                if value is None:
                    node.pop(index)
                else:
                    node[index] = value
                return old, value
        raise ValueError(f"{path!r}: no list item with {key}={match_value!r}")
    try:
        index = int(last)
    except ValueError:
        raise ValueError(f"{path!r}: list accessor [{last}] must be an index, [+] or key=value") from None
    if not (0 <= index < len(node)):
        raise ValueError(f"{path!r}: index {index} out of range (0..{len(node) - 1})")
    old = node[index]
    if value is None:
        node.pop(index)
    else:
        node[index] = value
    return old, value


def apply_changes(doc: Any, changes: dict[str, Any]) -> dict[str, dict]:
    """The generic apply step: every change lands at its address; the
    per-field diff is keyed by the caller's names (``{name: {from, to}}``),
    unchanged values are dropped so a no-op never journals."""
    diff: dict[str, dict] = {}
    for name, value in changes.items():
        old, new = set_path(doc, name, value)
        if old != new:
            diff[name] = {"from": old, "to": new}
    return diff


# ---------------------------------------------------------------------------
# The wall
# ---------------------------------------------------------------------------


def check_wall(
    name: str,
    *,
    wall: Collection[str],
    containers: Collection[str] = (),
    routed: dict[str, str] | None = None,
    reason: str = "identity / provenance / asset plumbing",
    container_hint: str | Callable[[str], str] | None = None,
    refuse: Callable[[str], str | None] | None = None,
) -> None:
    """The reusable matcher (P.7.2): a protected LEAF (last named segment, so
    a dotted path cannot sneak past — ``stats.animation``) is refused with
    *reason*; a whole-container write is refused with the knob-wise hint; a
    routed field is refused naming its owning verb; *refuse* is the caller's
    extra rule (``registry set``'s top-level identity keys).

    *container_hint* is a ``{name}`` format string OR a callable the caller
    uses to branch on the loaded document — a LIST container's P.1 grammar is
    ``<c>[<i>].<key>``, not ``<c>.<key>``, so one refusal must name the
    grammar that actually works (``db_ops.update_db_row`` passes the
    callable)."""
    leaf = leaf_of(name)
    if leaf in wall:
        raise ValueError(f"{name!r} is protected ({reason})")
    if routed and (leaf in routed or name in routed):
        verb = routed.get(name, routed.get(leaf))
        raise ValueError(f"{name!r} is owned by {verb} — use that surface")
    if name in containers:
        if callable(container_hint):
            raise ValueError(container_hint(name))
        raise ValueError(
            container_hint.format(name=name)
            if container_hint
            else f"{name!r} is a container — edit knobs individually ('{name}.<key>' or their flat names)"
        )
    if refuse is not None:
        why = refuse(name)
        if why:
            raise ValueError(why)


# ---------------------------------------------------------------------------
# The pipeline
# ---------------------------------------------------------------------------


def write_document(
    pack: str | Path,
    *,
    artifact_id: str,
    rel_path: str,
    document: Any,
    changes: dict[str, Any],
    wall: Collection[str] = (),
    containers: Collection[str] = (),
    routed: dict[str, str] | None = None,
    wall_reason: str = "identity / provenance / asset plumbing",
    container_hint: str | Callable[[str], str] | None = None,
    refuse: Callable[[str], str | None] | None = None,
    apply: Callable[[Any, dict[str, Any]], dict[str, dict]] | None = None,
    warn: Callable[[Any, dict[str, dict]], Iterable[str]] | None = None,
    validate: Callable[[Any, dict[str, dict]], Any] | None = None,
    user_edited: bool | None = None,
    writer: Callable[[str, Any], str] | None = None,
    actor: str = "user",
    session: str | None = None,
    detail: dict[str, Any] | None = None,
    op: str = "edit",
    source: str = "user",
    gen: dict | None = None,
    gen_kind: str | None = None,
    accuracy: str | None = None,
    cost_error: str | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    """Run the doctrine-1 pipeline over ONE file.

    *document* is the loaded current content of ``<pack>/<rel_path>`` (the
    CAS unit — for a collection layout that is the whole collection, and the
    journal diff stays per-field); *changes* are the caller's addressed
    values. Steps, in order:

    1. wall — ``check_wall`` on every change name (fail before touching data);
    2. apply — *apply* (caller's routing, e.g. ``db update``'s flat-name
       nesting) or the generic ``apply_changes``; both return the diff;
    3. no diff → ``{"no_change": True}``: nothing written, nothing journaled;
    4. warn — *warn(updated, diff)* surfaces, never blocks (off-table values);
    5. stamp — ``status: user_edited`` when *user_edited* is True, or when
       it is ``None`` and the document already carries a ``status`` key
       ("where the row model has it": a dungeon row without one is not given
       one — the engine's file shape is untouched);
    6. validate — *validate(updated, diff)* is fail-closed; it may return the
       normalized document to write (the platformer's model dump + hand-added
       keys) or ``None`` to write *updated* as-is, and anything it appends to
       the caller's *warnings* list joins the result (the pre-existing
       dangling ``refs`` warning);
    7. CAS + journal — snapshot before, write through *writer* (default: the
       pack adapter's ``write_json_singleton``), snapshot after, record one
       event with ``detail = {**detail, "changed": diff}``.

    Returns ``{document, changed, warnings, before_hash, after_hash, event}``.
    """
    pack = Path(pack)
    if not isinstance(changes, dict) or not changes:
        raise ValueError("--set needs a non-empty JSON object of field: value")
    for name in changes:
        check_wall(
            name, wall=wall, containers=containers, routed=routed, reason=wall_reason,
            container_hint=container_hint, refuse=refuse,
        )
    updated = copy.deepcopy(document)
    diff = (apply or apply_changes)(updated, changes)
    # Copied AFTER apply: a caller's apply step may append its own warnings
    # (an unknown top-level key on a schema-less row) to the list it passed.
    # The caller's list is re-read once more after *validate* (below), which
    # is where ``update_db_row`` appends its pre-existing-dangling-ref
    # warnings — a snapshot alone silently dropped them.
    caller_warnings = warnings if warnings is not None else []
    collected: list[str] = list(caller_warnings)
    seen = len(caller_warnings)
    if not diff:
        return {
            "document": document, "changed": {}, "no_change": True, "warnings": collected,
            "before_hash": None, "after_hash": None, "event": None,
        }
    if warn is not None:
        collected.extend(warn(updated, diff))
    if user_edited or (user_edited is None and isinstance(updated, dict) and "status" in updated):
        updated["status"] = "user_edited"
    data = updated
    if validate is not None:
        normalized = validate(updated, diff)
        if normalized is not None:
            data = normalized
    collected.extend(caller_warnings[seen:])
    committed = commit_document(
        pack, artifact_id=artifact_id, rel_path=rel_path, data=data, actor=actor, session=session,
        detail={**(detail or {}), "changed": diff}, op=op, source=source, gen=gen,
        gen_kind=gen_kind, accuracy=accuracy, cost_error=cost_error, writer=writer,
    )
    return {"document": data, "changed": diff, "warnings": collected, **committed}


def commit_document(
    pack: str | Path,
    *,
    artifact_id: str,
    rel_path: str,
    data: Any,
    actor: str = "user",
    session: str | None = None,
    detail: dict[str, Any] | None = None,
    op: str = "create",
    source: str = "user",
    gen: dict | None = None,
    gen_kind: str | None = None,
    accuracy: str | None = None,
    cost_error: str | None = None,
    writer: Callable[[str, Any], str] | None = None,
) -> dict[str, Any]:
    """The pipeline's tail on its own — CAS snapshot before, write, CAS
    snapshot after, one journal event — for verbs that materialize a WHOLE
    document rather than address changes into one (``db new`` on a
    collection, ``db define``'s new files, the registry synthesis). Returns
    ``{before_hash, after_hash, event}``.

    Row P1-A6: ``gen_kind`` / ``accuracy`` / ``cost_error`` ride beside ``gen``
    the way ``platformer_write.apply_level_edit`` already threads them.
    ``provenance.record`` REFUSES a costed event with no accuracy flag (P.8.2:
    an unlabelled cost is the silent-$0 failure), and this function writes the
    file BEFORE it journals — so a costed caller without the flag would land the
    bytes and lose the event. All three default to ``None``, which is what every
    free write already passes."""
    pack = Path(pack)
    path = pack / rel_path
    before = provenance.snapshot_file(pack, path)
    write = writer or pack_adapter(pack).write_json_singleton
    write(rel_path, data)
    after = provenance.snapshot_file(pack, path)
    event = provenance.record(
        pack,
        artifact_id=artifact_id,
        op=op,
        source=source,
        actor=actor,
        session=session,
        detail=detail,
        before_hash=before,
        after_hash=after,
        gen=gen,
        gen_kind=gen_kind,
        accuracy=accuracy,
        cost_error=cost_error,
    )
    return {"before_hash": before, "after_hash": after, "event": event}

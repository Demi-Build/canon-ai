"""The ``canon db`` verbs in core — ``types / schema / new / complete /
update / define / evolve`` over ANY pack's ``EntityKind`` registry (Phase 0
§5.1a, §6; P0 paper P.1 conventions, P.3.1, P.7.5; row P0-6).

Extracted from ``canon.packs.platformer.ops``: the bodies of ``db_types``,
``read_db_schema``, ``update_db_schema``, ``new_db_row``, ``complete_db_row``
and ``update_db_row`` moved here verbatim and are parameterized by the
kind's registry entry (``id_field``, ``layout``, ``nesting``, ``containers``,
``protected`` + ``CORE_PROTECTED``, ``routed``, the field lists, ``schema``,
``model``, ``builder``) plus the pack context ``resolve_pack`` answers. The
platformer module keeps every public name as a thin wrapper (its tables
are the seed those entries are built from), so its outputs stay
byte-identical and the agent's write tools keep importing them.

The one genuinely new mechanism (§5.1): the collection layout. A row of a
``collection`` kind lives inside ``npcs/npcs.json`` (``array`` |
``keyed_object`` | ``array_positional``); the verbs read the file, locate
the row by ``id_field`` (or the object key), rewrite the FILE, snapshot the
FILE into the CAS, and journal the per-field diff on ``<kind>:<id>``.
List containers (``shop_inventory``, ``abilities``, …) take the P.1
addressing ``<c>[<i>].<key>`` / ``<c>[+]`` / ``<c>[<i>] = null`` through the
core's address grammar.

Refusal copy (P.1): a protected field — "identity / provenance / asset
plumbing"; a routed field — "owned by <verb> — use that surface"; a
container — knob-wise. ``db schema`` output gains ``user_fields, hidden,
decorative, protected, routed`` beside ``type/source/path/schema`` (and
``db types`` beside its four) — ``RowEditor`` reads them at P0-8.

Generation: a kind whose seed binds a ``builder`` (the platformer's
anchored enemy/item bodies) generates exactly as before — same prompts, rng
streams, provenance stamping. A kind without one (every dungeon kind, every
``db define``d kind) gets a skeleton-only ``db new`` (anchored roll +
``renames`` + the user's fields, id per ``id_alloc``) and a structured
not-yet on ``db complete``: the dungeon's LLM prompts are per-POOL
generation bodies (``compose_mazeworld_specs``), not per-row completions,
and no P0 row wires the ``canon.ops`` trio through the registry (Phase 0
§6 "exists, unwired") — ``COMPLETE_NOT_YET_ROW`` names that gap.

Deliberately absent, by row ownership: the cradle surfaces (P0-8); the
dungeon room writer (P0-8 — ``room`` rows route their grid fields to grid
verbs); dialogue/scene verbs (P0-9); type renames (v1.1).
"""

from __future__ import annotations

import contextlib
import copy
import json
import random
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from canon import db_models, provenance
from canon.packs import ResolvedPack, resolve_pack
from canon.packs.rows import load_per_file_rows, load_rows, read_json
from canon.packs.spec import CORE_PROTECTED, EntityKind, PackSpec
from canon.pipeline.rng import derive_rng
from canon.registry_ops import ensure_registry, write_registry
from canon.skeleton.core import roll_skeleton
from canon.skeleton.loader import load_skeleton_spec
from canon.write_core import (
    NotYetError,
    check_wall,
    commit_document,
    pack_adapter,
    parse_address,
    set_path,
    write_document,
)

__all__ = [
    "COMPLETE_NOT_YET_ROW",
    "BuiltRow",
    "build_llm",
    "complete_db_row",
    "db_define",
    "db_evolve",
    "db_types",
    "new_db_row",
    "read_db_schema",
    "update_db_row",
    "update_db_schema",
]

#: Who brings per-row LLM completion for builder-less kinds (see docstring).
COMPLETE_NOT_YET_ROW = "Phase 0 §6 `canon generate / regenerate / reroll` registry wiring (unassigned in the master)"

_LAYOUT_FORMATS = ("array", "keyed_object", "array_positional")


@dataclass
class BuiltRow:
    """What an ``EntityKind.builder`` returns: the row (a Pydantic model or
    a dict), the warnings the build raised, the journal ``gen`` block when
    the LLM authored it, the adapter to write through (the platformer's
    Godot-aware ``ctx.adapter``) and the after-write provenance stamp
    (``stamp(row, content_hash)``)."""

    row: Any
    warnings: list[str] = field(default_factory=list)
    gen: dict | None = None
    adapter: Any = None
    stamp: Callable[[Any, str], None] | None = None
    #: Row P1-A6 (master §3.0-B): the ``genKind`` + ``accuracy`` the journal
    #: event carries when the LLM authored the row. ``text`` for LLM-authored
    #: DATA (P.9 J4); both are open strings a builder supplies, never a type
    #: this core encodes. Absent ⇒ the row cost nothing to make.
    gen_kind: str | None = None
    accuracy: str | None = None
    #: The op-result cost block (``{usd, llm_usd, …}``) the paid tools and the
    #: spend ledger report — the same shape every other paid verb returns.
    cost: dict | None = None


# ---------------------------------------------------------------------------
# Resolution helpers
# ---------------------------------------------------------------------------


def _resolve(pack_dir: str | Path) -> tuple[Path, ResolvedPack]:
    pack = Path(pack_dir)
    if not (pack / "manifest.json").is_file() and not (pack / ".canon" / "registry.json").is_file():
        raise FileNotFoundError(f"not a pack (no manifest.json): {pack}")
    return pack, resolve_pack(pack)


def _entity(spec: PackSpec, kind: str) -> EntityKind:
    if kind not in spec.entities:
        raise ValueError(f"unknown db type {kind!r} (one of {list(spec.entities)})")
    return spec.entities[kind]


def _wall(entity: EntityKind) -> frozenset[str]:
    return CORE_PROTECTED | frozenset(entity.protected)


def _reason(entity: EntityKind) -> str:
    asset = (entity.asset or {}).get("field")
    if asset:
        return f"identity / provenance / asset plumbing — {asset} changes via `canon asset replace`"
    return "identity / provenance / asset plumbing"


def _is_per_file(entity: EntityKind) -> bool:
    return (entity.layout or {}).get("mode") == "per_file"


def _per_file_rel(entity: EntityKind, entity_id: Any) -> str:
    return f"{entity.layout.get('dir')}/{entity_id}.json"


def _rows(pack: Path, entity: EntityKind) -> dict[str, Any]:
    """Existing rows keyed by id — the seed's loader (the platformer's
    model-validating ``_load_defs``) or the generic layout read."""
    if entity.loader is not None:
        return entity.loader(pack)
    if _is_per_file(entity):
        return load_per_file_rows(pack, entity)
    return load_rows(pack, entity)


def _read_collection(pack: Path, entity: EntityKind) -> Any:
    fmt = entity.layout.get("format")
    if fmt not in _LAYOUT_FORMATS:
        raise ValueError(f"unknown layout format {fmt!r} for kind {entity.kind!r}")
    data = read_json(pack / str(entity.layout.get("path", "")))
    if data is None:
        return {} if fmt == "keyed_object" else []
    db_models.check_collection_shape(entity, data)
    return data


@dataclass(frozen=True)
class _RowFile:
    """One file a row of a collection kind lives in — the layout's own index,
    or one of the ``layout.mirrors`` entries (P0-8 carry-over).

    The mirror list is registry DATA on the kind's layout (the dungeon room's
    ``rooms/rooms.json`` index + its ``world_bible`` / ``manifest`` /
    ``maze.json`` mirrors, P0 paper P.1.7), shaped like ``world update``'s own
    field table (P.7.3): one journal event per file, the mirrors carrying
    ``mirror_of``. A kind that declares none resolves to exactly one
    ``_RowFile`` — today's single-file path, unchanged.

    ``artifact_id`` names the CAS UNIT, which is the FILE, not the row: the
    index's own id is ``<kind>:<id>`` because that file holds only rows of
    that kind, and a mirror keeps the mirror label its layout declares
    (``world_bible``, ``manifest``). That holds even when a ``row_source``
    mirror stands in as the primary — the bytes snapshotted and the bytes a
    restore would write back are the WHOLE mirror file, which is what
    ``platformer_write._restore_document`` resolves ``<kind>:<id>`` to (the
    kind's layout path, ``rooms/rooms.json``). Consequence, by design: on a
    legacy tree with no index, a room-row edit is journalled under
    ``world_bible`` alongside ``world update``'s story edits, so the row's own
    lineage is reachable through ``world_bible`` and through the
    ``room:<id>/grid`` mirror — never through ``room:<id>``, which on that
    tree names a file that does not exist. ``detail`` carries the row id
    (``update_db_row``) so every event stays attributable to its row whatever
    file stood in.
    """

    rel: str
    container: str          # key holding the collection; "" = the file's root
    format: str             # keyed_object | array | array_positional | document
    id_field: str
    artifact_id: str
    fields: tuple[str, ...] | None = None   # None = whatever the mirror carries
    row_source: bool = False                # may stand in as the row (legacy trees)
    primary: bool = False


def _row_files(entity: EntityKind, entity_id: str) -> list[_RowFile]:
    """The layout's index first, then its declared mirrors — ids substituted
    into the file path and artifact id (``rooms/{id}/maze.json``)."""
    layout = entity.layout or {}
    out = [
        _RowFile(
            rel=str(layout.get("path", "")),
            container="",
            format=str(layout.get("format", "")),
            id_field=entity.id_field,
            artifact_id=f"{entity.kind}:{entity_id}",
            row_source=True,
            primary=True,
        )
    ]
    for mirror in layout.get("mirrors") or []:
        if not isinstance(mirror, dict) or not mirror.get("file"):
            raise ValueError(f"{entity.kind}: layout.mirrors entries need a 'file'")
        fields = mirror.get("fields")
        out.append(
            _RowFile(
                rel=str(mirror["file"]).format(id=entity_id),
                container=str(mirror.get("path", "")),
                format=str(mirror.get("format", "keyed_object")),
                id_field=str(mirror.get("id_field", entity.id_field)),
                artifact_id=str(mirror.get("artifact") or Path(str(mirror["file"])).stem).format(
                    id=entity_id
                ),
                fields=tuple(fields) if fields else None,
                row_source=bool(mirror.get("row_source")),
            )
        )
    return out


def _collection_in(document: Any, target: _RowFile) -> Any:
    """The collection inside a row file — the document itself when the file's
    root IS the collection, else the named container key."""
    if not target.container:
        return document
    if isinstance(document, dict):
        return document.get(target.container)
    return None


def _row_in(document: Any, target: _RowFile, entity_id: str) -> dict | None:
    """*entity_id*'s row inside one row file, or ``None`` when this file does
    not carry it (an absent index, a mirror written before the row existed)."""
    if target.format == "document":
        return document if isinstance(document, dict) else None
    data = _collection_in(document, target)
    if isinstance(data, dict):
        row = data.get(str(entity_id))
        return row if isinstance(row, dict) else None
    if isinstance(data, list):
        for row in data:
            if isinstance(row, dict) and str(row.get(target.id_field)) == str(entity_id):
                return row
    return None


def _set_row_in(document: Any, target: _RowFile, entity_id: str, data: dict) -> Any:
    """Put a rewritten row back where ``_row_in`` found it, and answer the
    document to write (the model path's normalized dump)."""
    if target.format == "document":
        return data
    collection = _collection_in(document, target)
    if isinstance(collection, dict):
        collection[str(entity_id)] = data
    elif isinstance(collection, list):
        for index, row in enumerate(collection):
            if isinstance(row, dict) and str(row.get(target.id_field)) == str(entity_id):
                collection[index] = data
                break
    return document


def _check_collection(entity: EntityKind, target: _RowFile, document: Any) -> None:
    """The layout's shape + id-uniqueness checks against the COLLECTION the
    row lives in — the file's root for the index, the named container for a
    mirror standing in as the row file (the whole ``world_bible.json`` is not
    a collection of rows, so the check has to be scoped)."""
    if target.format not in _LAYOUT_FORMATS or target.format != (entity.layout or {}).get("format"):
        return
    collection = _collection_in(document, target)
    db_models.check_collection_shape(entity, collection)
    db_models.check_ids_unique(entity, collection)


def _read_row_file(pack: Path, entity: EntityKind, target: _RowFile) -> Any:
    """One row file's whole document (the CAS unit). The kind's own index is
    read through ``_read_collection`` so its shape check still runs."""
    if target.primary:
        return _read_collection(pack, entity)
    return read_json(pack / target.rel)


def _resolve_row_files(
    pack: Path, entity: EntityKind, entity_id: str
) -> tuple[_RowFile, Any, list[tuple[_RowFile, Any]]]:
    """``(primary, its document, [(mirror, its document)])`` for one row.

    The row is resolved from whichever file HAS it, index first, then a
    ``row_source`` mirror — the read-both shim the legacy dungeon trees need,
    which predate ``rooms/rooms.json`` (master §2: never a migrate verb, and
    nothing is synthesized here). Mirrors that exist and already carry the row
    ride along; a mirror whose file is absent is skipped, and no file is
    created. ``FileNotFoundError`` when no file carries the row.
    """
    targets = _row_files(entity, entity_id)
    documents: dict[str, Any] = {}
    for target in targets:
        try:
            documents[target.rel] = _read_row_file(pack, entity, target)
        except (OSError, json.JSONDecodeError, ValueError):
            if target.primary:
                raise
            documents[target.rel] = None
    primary = next(
        (
            t
            for t in targets
            if t.row_source and _row_in(documents.get(t.rel), t, entity_id) is not None
        ),
        None,
    )
    if primary is None:
        raise FileNotFoundError(f"{entity.kind} {entity_id!r} not found")
    mirrors = [
        (t, documents[t.rel])
        for t in targets
        if t is not primary
        and documents.get(t.rel) is not None
        and _row_in(documents[t.rel], t, entity_id) is not None
    ]
    return primary, documents[primary.rel], mirrors


def _write_mirrors(
    pack: Path,
    entity: EntityKind,
    entity_id: str,
    mirrors: list[tuple[_RowFile, Any]],
    changed: dict[str, dict],
    *,
    actor: str,
    session: str | None,
) -> list[dict]:
    """Write each mirror that carries a changed field, one journal event per
    file with ``mirror_of`` (P.7.3). A mirror gets a field when its own
    ``fields`` list names it, or — with no list — when the mirror row ALREADY
    carries that key: a mirror is kept consistent, never grown a key the file
    does not have (the manifest's room entry is a summary, not a row copy).
    """
    out: list[dict] = []
    for target, document in mirrors:
        row = _row_in(document, target, entity_id)
        assert row is not None
        values = {
            name: diff["to"]
            for name, diff in changed.items()
            if "." not in name
            and "[" not in name
            and (name in target.fields if target.fields is not None else name in row)
        }
        if not values:
            continue

        def apply(doc: Any, addressed: dict, _t=target) -> dict[str, dict]:
            mirror_row = _row_in(doc, _t, entity_id)
            diff: dict[str, dict] = {}
            for name, value in addressed.items():
                old = mirror_row.get(name)  # type: ignore[union-attr]
                if old != value:
                    mirror_row[name] = value  # type: ignore[index]
                    diff[name] = {"from": old, "to": value}
            return diff

        result = write_document(
            pack,
            artifact_id=target.artifact_id,
            rel_path=target.rel,
            document=document,
            changes=values,
            apply=apply,
            user_edited=False,
            actor=actor,
            session=session,
            detail={
                "kind": "db_update",
                "type": entity.kind,
                "mirror_of": f"{entity.kind}:{entity_id}",
            },
        )
        if result.get("no_change"):
            continue
        out.append({
            "file": target.rel,
            "artifact_id": target.artifact_id,
            "changed": result["changed"],
            "mirror_of": f"{entity.kind}:{entity_id}",
            "before_hash": result["before_hash"],
            "after_hash": result["after_hash"],
        })
    return out


def _locate(entity: EntityKind, data: Any, entity_id: str) -> tuple[dict, Any]:
    """``(row, accessor)`` for *entity_id* in a loaded collection — the
    object key or the array index. Absent → ``FileNotFoundError`` (the
    per-file verbs' "not found" error class)."""
    if isinstance(data, dict):
        if str(entity_id) in data:
            return data[str(entity_id)], str(entity_id)
    else:
        for index, row in enumerate(data):
            if isinstance(row, dict) and str(row.get(entity.id_field)) == str(entity_id):
                return row, index
    raise FileNotFoundError(f"{entity.kind} {entity_id!r} not found")


def _insert(entity: EntityKind, data: Any, row: dict) -> Any:
    key = str(row.get(entity.id_field))
    if isinstance(data, dict):
        data[key] = row
    else:
        data.append(row)
    return data


def _complete_not_yet(entity: EntityKind) -> NotYetError:
    return NotYetError(
        f"db complete is not yet available for {entity.kind!r}: the kind binds no per-row completion body "
        f"(its prompts are generation-side pool bodies) — {COMPLETE_NOT_YET_ROW} brings it; "
        "`db new` (skeleton roll + your fields) and `db update` work today",
        row=COMPLETE_NOT_YET_ROW,
        type=entity.kind,
    )


def build_llm(kind: str | None, model: str | None = None, stats: Any = None):
    """The op LLM client (fake | anthropic | none) — the platformer module's
    generic builder, imported on demand so ``--help`` never pays for it."""
    from canon.packs.platformer.ops import build_llm as _build

    return _build(kind, model, stats)


# ---------------------------------------------------------------------------
# db types / db schema
# ---------------------------------------------------------------------------


def _registry_lists(entity: EntityKind) -> dict[str, Any]:
    return {
        "user_fields": list(entity.user_fields),
        "hidden": list(entity.hidden),
        "decorative": list(entity.decorative),
        "protected": sorted(_wall(entity)),
        "routed": dict(entity.routed),
    }


def db_types(pack_dir: str | Path) -> dict:
    """The entity-type registry + field specs (drives editor form UIs) —
    ``ops.db_types`` verbatim over every registered kind, plus the P.1
    lists and the layout."""
    pack, resolved = _resolve(pack_dir)
    spec = resolved.spec
    out: dict[str, Any] = {}
    for kind, entity in spec.entities.items():
        skeleton, _path, source = db_models.schema_for(pack, spec, entity)
        out[kind] = {
            "dir": entity.layout.get("dir") if _is_per_file(entity) else None,
            "id_field": entity.id_field,
            "skeleton_fields": db_models.skeleton_field_entries(skeleton),
            "llm_fields": list(entity.llm_fields),
            "code_fields": list(entity.code_fields),
            "schema_source": source,
            "label": entity.label,
            "layout": dict(entity.layout),
            **_registry_lists(entity),
        }
    return out


def read_db_schema(pack_dir: str | Path, entity_type: str) -> dict:
    """The EFFECTIVE roll-table schema for one type + where it came from
    (``source``: ``pack`` | ``default`` | ``None`` when neither side ships
    one — the schema then reads as an empty table)."""
    pack, resolved = _resolve(pack_dir)
    entity = _entity(resolved.spec, entity_type)
    _skeleton, path, source = db_models.schema_for(pack, resolved.spec, entity)
    schema = read_json(path) if path is not None else db_models.empty_schema(entity_type)
    return {
        "type": entity_type,
        "source": source,
        "path": str(path) if path is not None else None,
        "schema": schema,
        **_registry_lists(entity),
    }


def _check_lookup_coverage(spec) -> None:
    """Every lookup table must cover its dependency's choice values — the
    classic table-editing mistake (add an archetype, forget its speed row)."""
    for name, entry in spec.fields.items():
        if entry.lookup is None or not entry.depends_on:
            continue
        parent = spec.fields.get(entry.depends_on)
        if parent is None or parent.choices is None:
            continue
        missing = [v for v, _ in parent.choices if v not in entry.lookup]
        if missing:
            raise ValueError(
                f"lookup {name!r} has no row for {entry.depends_on} "
                f"value(s) {missing} — add rows or remove those choices"
            )


def _validate_schema_document(merged: dict) -> None:
    """Fail-closed validation: loader (shape + dependency order), coverage,
    then a smoke roll (skipped when a field keys off outer context — no
    context to thread here). An empty table is legal (a define'd kind)."""
    if not merged.get("fields"):
        return
    spec = load_skeleton_spec(merged)
    _check_lookup_coverage(spec)
    if not any(f.depends_on_context for f in spec.fields.values()):
        roll_skeleton(spec, random.Random(0))


def update_db_schema(
    pack_dir: str | Path,
    entity_type: str,
    changes: dict,
    *,
    actor: str = "user",
    session: str | None = None,
) -> dict:
    """Edit the roll tables bounding generation for one entity type.

    ``changes`` = ``{"fields": {<name>: <field entry> | null}}`` — each named
    field entry is replaced wholesale (null deletes the field); everything
    else carries over. The merged document must load as a SkeletonSpec, pass
    lookup-coverage, and survive a smoke roll BEFORE anything is written.
    Edits land as a PACK-LOCAL override (``schemas/<type>.json``; the
    template default is never touched) and journal ``op:"edit"`` on
    ``schema:<type>`` — the column-evolution verb (§3.0-A), unchanged.
    """
    field_changes = (changes or {}).get("fields")
    if not isinstance(field_changes, dict) or not field_changes:
        raise ValueError('--set needs {"fields": {<name>: <entry>|null, ...}}')
    pack, resolved = _resolve(pack_dir)
    entity = _entity(resolved.spec, entity_type)
    current = read_db_schema(pack, entity_type)
    merged = json.loads(json.dumps(current["schema"]))  # deep copy
    fields = merged.setdefault("fields", {})
    diff: dict[str, dict] = {}
    for name, entry in field_changes.items():
        old = fields.get(name)
        if entry is None:
            fields.pop(name, None)
        else:
            fields[name] = entry
        if old != entry:
            diff[name] = {"from": old, "to": entry}
    if not diff:
        return {**current, "changed": {}, "no_change": True}

    _validate_schema_document(merged)

    rel = entity.schema or f"schemas/{entity_type}.json"
    before = provenance.snapshot_file(pack, Path(current["path"])) if current["path"] else None
    pack_adapter(pack).write_json_singleton(rel, merged)
    after = provenance.snapshot_file(pack, pack / rel)
    provenance.record(
        pack,
        artifact_id=f"schema:{entity_type}",
        op="edit",
        source="user",
        actor=actor,
        session=session,
        detail={
            "kind": "db_schema", "type": entity_type,
            "changed": sorted(diff), "was": current["source"],
        },
        before_hash=before,
        after_hash=after,
    )
    return {
        "type": entity_type, "source": "pack", "path": str(pack / rel),
        "schema": merged, "changed": diff, **_registry_lists(entity),
    }


# ---------------------------------------------------------------------------
# db new / complete
# ---------------------------------------------------------------------------


def _as_json(row: Any) -> Any:
    return row.model_dump(mode="json") if isinstance(row, BaseModel) else row


def _id_of(entity: EntityKind, row: Any) -> Any:
    return getattr(row, entity.id_field) if isinstance(row, BaseModel) else row.get(entity.id_field)


def _new_built_row(
    pack: Path,
    entity: EntityKind,
    fields: dict,
    *,
    complete: bool,
    llm: Any,
    system_override: str | None,
    actor: str,
    session: str | None,
) -> dict:
    """``ops.new_db_row``'s body: the seed's anchored builder writes the
    row exactly as pipeline generation would."""
    index = len(_rows(pack, entity))
    built = entity.builder(  # type: ignore[misc]
        pack, index=index, fields=fields, complete=complete, llm=llm, system_override=system_override,
    )
    row = built.row
    entity_id = _id_of(entity, row)
    adapter = built.adapter or pack_adapter(pack)
    if _is_per_file(entity):
        rel = _per_file_rel(entity, entity_id)
        content_hash = adapter.write_json_singleton(rel, _as_json(row))
        before = None
    else:
        rel = str(entity.layout.get("path"))
        data = _read_collection(pack, entity)
        before = provenance.snapshot_file(pack, pack / rel)
        content_hash = adapter.write_json_singleton(rel, _insert(entity, data, _as_json(row)))
    if built.stamp is not None:
        built.stamp(row, content_hash)
    after = provenance.snapshot_file(pack, pack / rel)
    provenance.record(
        pack,
        artifact_id=f"{entity.kind}:{entity_id}",
        op="generate" if complete else "create",
        source="llm" if complete else "user",
        actor=actor,
        session=session,
        detail={
            "kind": "db_new",
            "type": entity.kind,
            "locked": sorted((fields or {}).keys()),
        },
        before_hash=before,
        after_hash=after,
        gen=built.gen if complete else None,
        gen_kind=built.gen_kind if complete else None,
        accuracy=built.accuracy if complete else None,
    )
    return {
        "type": entity.kind,
        "id": entity_id,
        "row": _as_json(row),
        "completed": complete,
        "warnings": list(built.warnings),
        "changed": after is not None,  # a brand-new row always wrote bytes
        "changed_artifacts": [f"{entity.kind}:{entity_id}"] if after is not None else [],
    }


def _allocate_id(entity: EntityKind, existing: dict[str, Any], fields: dict) -> Any:
    """P.3.1: ``max(existing ids ≥ base, base − 1) + 1`` for an int-allocated
    kind (a caller-supplied id is refused — allocation is the rule); a kind
    with ``id_alloc: null`` requires the id in *fields* and it must be new."""
    if entity.id_alloc:
        if entity.id_field in fields:
            raise ValueError(
                f"{entity.id_field!r} is allocated for {entity.kind!r} (id_alloc base "
                f"{entity.id_alloc.get('base')}) — omit it"
            )
        base = int(entity.id_alloc.get("base", 0))
        ids = []
        for key in existing:
            try:
                value = int(key)
            except (TypeError, ValueError):
                continue
            if value >= base:
                ids.append(value)
        return max(ids, default=base - 1) + 1
    new_id = fields.get(entity.id_field)
    if new_id in (None, ""):
        raise ValueError(
            f"{entity.kind!r} has no id allocation (id_alloc null) — pass --fields "
            f"'{{\"{entity.id_field}\": \"<slug>\"}}'"
        )
    if str(new_id) in existing:
        raise ValueError(f"{entity.kind} {new_id!r} already exists")
    return new_id


def _new_collection_row(
    pack: Path,
    spec: PackSpec,
    entity: EntityKind,
    fields: dict,
    *,
    actor: str,
    session: str | None,
) -> dict:
    """Skeleton-only ``db new`` for a builder-less kind: the anchored roll
    (user fields are locked constraints, skeleton OR on-disk names), the
    ``renames`` map applied (dotted targets nest), then the user's fields
    verbatim; validated through the kind's (dynamic) model + the P.3.1
    fail-closed checks; written into the collection (or its own file for a
    ``per_file`` kind); journaled ``op: create`` on ``<kind>:<id>``."""
    wall = _wall(entity)
    for name in fields:
        if name == entity.id_field:
            continue
        check_wall(name, wall=wall, routed=entity.routed, reason=_reason(entity))
    existing = _rows(pack, entity)
    new_id = _allocate_id(entity, existing, fields)
    skeleton, _p, _s = db_models.schema_for(pack, spec, entity)
    row: dict[str, Any] = {entity.id_field: new_id}
    warnings: list[str] = []
    if skeleton is not None:
        inverse = {disk: skel for skel, disk in entity.renames.items()}
        anchors = {
            inverse.get(name, name): value
            for name, value in fields.items()
            if inverse.get(name, name) in skeleton.fields and value is not None
        }
        manifest = read_json(pack / "manifest.json") or {}
        rng = derive_rng(str(manifest.get("seed", "")), f"db:{entity.kind}", len(existing))
        try:
            rolled = roll_skeleton(skeleton, rng, context=dict(fields), locked=anchors)
        except KeyError as exc:
            raise ValueError(str(exc)) from None
        for skel_name, value in rolled.items():
            set_path(row, entity.renames.get(skel_name, skel_name), value)
    for name, value in fields.items():
        if name == entity.id_field:
            continue
        set_path(row, name, value)
    model = entity.model or db_models.dynamic_model(entity, skeleton)
    try:
        model.model_validate(row)
    except ValidationError as exc:
        raise ValueError(f"{entity.kind} row fails validation: {exc}") from None
    warnings.extend(db_models.check_refs(pack, spec, entity, row, list(fields)))

    if _is_per_file(entity):
        rel = _per_file_rel(entity, new_id)
        data: Any = row
    else:
        rel = str(entity.layout.get("path"))
        data = _insert(entity, _read_collection(pack, entity), row)
        db_models.check_ids_unique(entity, data)
    committed = commit_document(
        pack,
        artifact_id=f"{entity.kind}:{new_id}",
        rel_path=rel,
        data=data,
        actor=actor,
        session=session,
        detail={"kind": "db_new", "type": entity.kind, "locked": sorted(fields)},
        op="create",
        source="user",
    )
    return {
        "type": entity.kind,
        "id": new_id,
        "row": row,
        "completed": False,
        "warnings": warnings,
        "changed": committed["after_hash"] is not None,
        "changed_artifacts": [f"{entity.kind}:{new_id}"],
    }


def new_db_row(
    pack_dir: str | Path,
    entity_type: str,
    fields: dict | None = None,
    *,
    complete: bool = False,
    llm: Any = None,
    system_override: str | None = None,
    actor: str = "user",
    session: str | None = None,
) -> dict:
    """Create one anchored row: user fields are locked constraints, the
    skeleton rolls the rest, and (with ``complete``) the LLM authors its
    fields exactly as pipeline generation would — through the kind's seed
    ``builder``; a builder-less kind rolls its skeleton only (``complete``
    is a structured not-yet)."""
    pack, resolved = _resolve(pack_dir)
    entity = _entity(resolved.spec, entity_type)
    fields = dict(fields or {})
    if entity.builder is not None:
        return _new_built_row(
            pack, entity, fields, complete=complete, llm=llm, system_override=system_override,
            actor=actor, session=session,
        )
    if complete:
        raise _complete_not_yet(entity)
    return _new_collection_row(pack, resolved.spec, entity, fields, actor=actor, session=session)


def complete_db_row(
    pack_dir: str | Path,
    entity_type: str,
    entity_id: str,
    locked: list[str] | None = None,
    *,
    reroll: bool = False,
    llm: Any = None,
    system_override: str | None = None,
    actor: str = "user",
    session: str | None = None,
) -> dict:
    """LLM-complete an EXISTING row. ``locked`` fields are preserved as
    constraints; with ``reroll`` the unlocked mechanical fields re-roll too
    (``ops.complete_db_row`` verbatim, the builder and tables injected)."""
    pack, resolved = _resolve(pack_dir)
    entity = _entity(resolved.spec, entity_type)
    if entity.builder is None:
        raise _complete_not_yet(entity)
    if _is_per_file(entity):
        rel = _per_file_rel(entity, entity_id)
        path = pack / rel
        if not path.is_file():
            raise FileNotFoundError(f"{entity_type} {entity_id!r} not found")
        current = read_json(path)
        collection = None
    else:
        rel = str(entity.layout.get("path"))
        path = pack / rel
        collection = _read_collection(pack, entity)
        current, _accessor = _locate(entity, collection, entity_id)
        del _accessor
    locked = list(locked or [])
    before = provenance.snapshot_file(pack, path)

    # Rebuild the row through the anchored builder: locked fields (plus the
    # stable id) carry over; everything else re-rolls / re-authors.
    fields: dict[str, Any] = {entity.id_field: entity_id}
    flat = dict(current)
    for container in entity.containers:
        if isinstance(current.get(container), dict):
            flat.update(current[container])
    for name in locked:
        if name in flat:
            fields[name] = flat[name]
    if not reroll:
        # Keep every existing mechanical value as an anchor; only the LLM
        # fields (and empties) change.
        skeleton, _p, _s = db_models.schema_for(pack, resolved.spec, entity)
        for name in (skeleton.fields if skeleton is not None else {}):
            if name in flat and flat.get(name) is not None and name not in fields:
                fields[name] = flat[name]
        for name in entity.code_fields:
            if flat.get(name) and name not in fields:
                fields[name] = flat[name]

    # Stable index: derive from position among existing ids so re-completion
    # doesn't shift the rng streams of other rows.
    existing = list(_rows(pack, entity))
    index = existing.index(entity_id) if entity_id in existing else len(existing)
    built = entity.builder(
        pack, index=index, fields=fields, complete=True, llm=llm, system_override=system_override,
    )
    row = built.row

    # The id must not drift on completion.
    if str(_id_of(entity, row)) != str(entity_id):
        data = _as_json(row)
        data[entity.id_field] = entity_id
        data["artifact_id"] = f"{entity_type}:{entity_id}"
        row = entity.model.model_validate(data) if entity.model is not None else data

    adapter = built.adapter or pack_adapter(pack)
    if collection is None:
        content_hash = adapter.write_json_singleton(rel, _as_json(row))
    else:
        _row, accessor = _locate(entity, collection, entity_id)
        collection[accessor] = _as_json(row)
        content_hash = adapter.write_json_singleton(rel, collection)
    if built.stamp is not None:
        built.stamp(row, content_hash)
    after = provenance.snapshot_file(pack, path)
    event = provenance.record(
        pack,
        artifact_id=f"{entity_type}:{entity_id}",
        op="regenerate",
        source="llm",
        actor=actor,
        session=session,
        detail={
            "kind": "db_complete",
            "type": entity_type,
            "locked": sorted(locked),
            "reroll": reroll,
        },
        before_hash=before,
        after_hash=after,
        gen=built.gen,
        gen_kind=built.gen_kind,
        accuracy=built.accuracy,
    )
    changed = after is not None and after != before
    return {
        "type": entity_type,
        "id": entity_id,
        "row": _as_json(row),
        "cost": dict(built.cost or {}),
        # Row P1-A6 / P.8.7: the ts of the costed journal event, so a derived
        # spend row can point back at it and never be summed twice.
        "journal_ref": event.get("ts") if event.get("costCents") is not None else None,
        "warnings": list(built.warnings),
        "changed": changed,
        "changed_artifacts": [f"{entity_type}:{entity_id}"] if changed else [],
    }


# ---------------------------------------------------------------------------
# db update — DIRECT human edits (no rerolls, no LLM)
# ---------------------------------------------------------------------------


def _apply_row_changes(
    row: dict,
    changes: dict,
    *,
    entity: EntityKind,
    model: type[BaseModel],
    warnings: list[str],
) -> dict[str, dict]:
    """``update_db_row``'s routing loop, verbatim: flat names route into
    their nested homes (``nesting``), model fields land top-level, dotted
    paths reach hand-added knobs in a dict container, ``None`` deletes a
    nested key; plus the P.1 list-container addressing for bracketed names.
    A dict row (no Pydantic model) accepts an unknown top-level key with a
    warning — ``extra = allow``, doctrine 10 — where a modeled row refuses."""
    nesting = entity.nesting
    containers = tuple(entity.containers)
    modeled = entity.model is not None
    known_lists = set(entity.llm_fields) | set(entity.code_fields) | set(entity.user_fields)
    known_lists |= set(entity.hidden) | set(entity.decorative)
    diff: dict[str, dict] = {}
    for name, value in changes.items():
        if "[" in name:
            container = parse_address(name)[0][0]
            if container not in containers:
                raise ValueError(
                    f"list path {name!r} must start with a declared container (one of {list(containers)})"
                )
            if not isinstance(row.get(container), list) and container in row:
                raise ValueError(f"{container!r} is not a list container — use '{container}.<key>'")
            old, new = set_path(row, name, value)
            if old != new:
                diff[name] = {"from": old, "to": new}
            continue
        if "." in name:
            container, _, key = name.partition(".")
            if container not in containers or not key or "." in key:
                raise ValueError(
                    f"dotted path {name!r} must be <container>.<key> with "
                    f"container one of {list(containers)}"
                )
            if isinstance(row.get(container), list):
                raise ValueError(f"{container!r} is a list container — address items as '{container}[<i>].<key>'")
        elif name in nesting:
            container, key = nesting[name], name
        elif name in model.model_fields:
            container, key = "", name
        elif not modeled and (name in row or name in known_lists):
            container, key = "", name
        elif not modeled:
            container, key = "", name
            warnings.append(f"{name!r} is not in the schema or the registry field lists — kept as a hand edit")
        else:
            known = sorted(
                (set(nesting) | set(model.model_fields)) - _wall(entity)
            )
            raise ValueError(
                f"unknown field {name!r} for {entity.kind} — one of {known}, "
                "or a dotted path like 'stats.custom'"
            )
        if not container:
            if value is None:
                raise ValueError(f"cannot delete top-level field {name!r}")
            old = row.get(key)
            row[key] = value
        else:
            bucket = row.setdefault(container, {})
            old = bucket.get(key)
            if value is None:
                bucket.pop(key, None)
            else:
                bucket[key] = value
        if old != value:
            diff[name] = {"from": old, "to": value}
    return diff


def update_db_row(
    pack_dir: str | Path,
    entity_type: str,
    entity_id: str,
    changes: dict,
    *,
    actor: str = "user",
    session: str | None = None,
) -> dict:
    """Apply direct human edits to an existing row — values land verbatim.

    ``changes`` maps flat field names to new values: known knobs route into
    their nested dicts, model fields land top-level, dotted paths
    ("stats.custom") reach hand-added knobs, bracketed paths address list
    containers (P.1); ``None`` deletes a nested key. The result is validated
    through the entity model (fail-closed — the Pydantic one, or the P.3.1
    dynamic model), rewritten + rehashed, stamped ``user_edited`` where the
    row carries a status, and journaled ``op:"edit"`` with the per-field diff
    — the (generated → human-corrected) training pair. Mounted on
    ``write_core.write_document``.

    A collection kind whose layout declares ``mirrors`` (the P0-8 carry-over:
    the dungeon room, P.1.7) resolves the row from whichever file HAS it — the
    index, else a ``row_source`` mirror, so the legacy trees that predate
    ``rooms/rooms.json`` are editable with no migration and nothing
    synthesized — and writes every mirror that exists in the same batch, one
    journal event per file carrying ``mirror_of`` (P.7.3).
    """
    if not isinstance(changes, dict) or not changes:
        raise ValueError("--set needs a non-empty JSON object of field: value")
    pack, resolved = _resolve(pack_dir)
    spec = resolved.spec
    entity = _entity(spec, entity_type)
    per_file = _is_per_file(entity)
    primary: _RowFile | None = None
    mirrors: list[tuple[_RowFile, Any]] = []
    if per_file:
        rel = _per_file_rel(entity, entity_id)
        path = pack / rel
        if not path.is_file():
            raise FileNotFoundError(f"{entity_type} {entity_id!r} not found")
        document: Any = read_json(path)
    else:
        primary, document, mirrors = _resolve_row_files(pack, entity, entity_id)
        rel = primary.rel
    skeleton, _p, _s = db_models.schema_for(pack, spec, entity)
    model = entity.model or db_models.dynamic_model(entity, skeleton)
    warnings: list[str] = []

    def row_of(doc: Any) -> dict:
        if per_file:
            return doc
        assert primary is not None
        row = _row_in(doc, primary, entity_id)
        if row is None:
            raise FileNotFoundError(f"{entity.kind} {entity_id!r} not found")
        return row

    def apply(doc: Any, addressed: dict) -> dict[str, dict]:
        row = row_of(doc)
        diff = _apply_row_changes(row, addressed, entity=entity, model=model, warnings=warnings)
        if diff and not per_file and "status" in row:
            row["status"] = "user_edited"
        return diff

    def container_hint(name: str) -> str:
        """P.1: a LIST container's grammar is ``<c>[<i>].<key>``, a dict
        container's is ``<c>.<key>`` — one refusal names the grammar that
        works, instead of sending the caller into a second refusal. The
        row decides; when the row does not carry the container at all,
        both forms are named rather than guessing."""
        value = row_of(document).get(name)
        if isinstance(value, list):
            return (
                f"{name!r} is a list container — address items as '{name}[<i>].<key>', "
                f"append with '{name}[+]', delete an item with '{name}[<i>]' = null"
            )
        if isinstance(value, dict):
            return f"{name!r} is a container — edit knobs individually ('{name}.<key>' or their flat names)"
        return (
            f"{name!r} is a container — edit knobs individually: '{name}.<key>' for an object, "
            f"'{name}[<i>].<key>' / '{name}[+]' for a list"
        )

    def warn(doc: Any, diff: dict[str, dict]) -> list[str]:
        return db_models.off_table_warnings(
            skeleton,
            db_models.flatten_row(row_of(doc), entity.containers),
            list(diff),
            renames=entity.renames,
        )

    def validate(doc: Any, diff: dict[str, dict]) -> Any:
        row = row_of(doc)
        if entity.model is not None:
            entity_obj = model.model_validate(row)  # fail-closed shape check
            data = entity_obj.model_dump(mode="json")
            for key, value in row.items():  # keep hand-added top-level keys
                if key not in data:
                    data[key] = value
            if per_file:
                return data
            assert primary is not None
            return _set_row_in(doc, primary, entity_id, data)
        try:
            model.model_validate(row)
        except ValidationError as exc:
            raise ValueError(f"{entity.kind} {entity_id!r} fails validation: {exc}") from None
        if not per_file:
            assert primary is not None
            _check_collection(entity, primary, doc)
        warnings.extend(db_models.check_refs(pack, spec, entity, row, list(diff)))
        return None

    # The row and its mirrors ride ONE batchId so a reader walks the pair as
    # one act (P.7.3); a row with no mirror binds nothing — a batch of one is
    # noise (the rule ``apply_room_edit`` already follows).
    batch = f"db-update:{entity_type}:{entity_id}" if mirrors else None
    with provenance.bind_batch(batch) if mirrors else contextlib.nullcontext():
        result = write_document(
            pack,
            artifact_id=primary.artifact_id if primary is not None else f"{entity_type}:{entity_id}",
            rel_path=rel,
            document=document,
            changes=changes,
            wall=_wall(entity),
            containers=tuple(entity.containers),
            routed=entity.routed,
            wall_reason=_reason(entity),
            container_hint=container_hint,
            apply=apply,
            warn=warn,
            validate=validate,
            user_edited=None if per_file else False,
            actor=actor,
            session=session,
            # The pre-extraction detail shape is frozen (`{kind, type}` plus
            # the core's `changed`) and stays byte-identical for every kind
            # whose artifact id already names the row. `id` is added ONLY when
            # a `row_source` mirror stood in and the event therefore publishes
            # under the MIRROR's name (`world_bible`): without it, two edits to
            # two different rooms carry no distinguishing field anywhere once
            # the batch is unbound (a row no mirror happens to carry).
            detail=(
                {"kind": "db_update", "type": entity_type}
                if primary is None or primary.artifact_id == f"{entity_type}:{entity_id}"
                else {"kind": "db_update", "type": entity_type, "id": entity_id}
            ),
            warnings=warnings,
        )
        files = (
            []
            if result.get("no_change")
            else _write_mirrors(
                pack, entity, entity_id, mirrors, result["changed"],
                actor=actor, session=session,
            )
        )
    row = row_of(result["document"])
    if result.get("no_change"):
        return {
            "type": entity_type, "id": entity_id, "row": row,
            "changed": {}, "no_change": True, "warnings": result["warnings"],
            "file": rel, "mirrors": [],
        }
    return {
        "type": entity_type, "id": entity_id, "row": row,
        "changed": result["changed"], "warnings": result["warnings"],
        "file": rel, "mirrors": files,
    }


# ---------------------------------------------------------------------------
# db define / db evolve (P.7.5)
# ---------------------------------------------------------------------------

_DEFINE_REQUIRED = ("label", "layout", "id_field")


def _validate_layout(kind: str, layout: Any) -> dict:
    if not isinstance(layout, dict) or layout.get("mode") not in ("per_file", "collection"):
        raise ValueError(
            f'{kind}: layout must be {{"mode": "per_file", "dir": …}} or '
            '{"mode": "collection", "path": …, "format": …}'
        )
    if layout["mode"] == "per_file":
        if not layout.get("dir"):
            raise ValueError(f"{kind}: a per_file layout needs a dir")
    else:
        if not layout.get("path") or not str(layout["path"]).endswith(".json"):
            raise ValueError(f"{kind}: a collection layout needs a .json path")
        if layout.get("format") not in _LAYOUT_FORMATS:
            raise ValueError(f"{kind}: collection format must be one of {list(_LAYOUT_FORMATS)}")
    # Pack containment: the collection file / per-file dir is addressed
    # RELATIVE to the pack root everywhere downstream (``pack / rel_path`` in
    # ``commit_document``, ``JsonOutputAdapter.resolve_path``), and pathlib
    # resolves ``pack / "/abs"`` to ``/abs`` — so an absolute path (or a
    # ``~`` / ``..`` escape) would put the kind's rows outside the pack and
    # break the ``<pack>/.canon/`` durable-truth invariant. The payload comes
    # straight off `--set`, so this is the wall for it.
    for key in ("dir", "path"):
        value = layout.get(key)
        if value in (None, ""):
            continue
        text = str(value)
        if Path(text).is_absolute() or text.startswith("~") or ".." in Path(text).parts:
            raise ValueError(
                f"{kind}: layout paths stay inside the pack — {key}={text!r} must be relative to the pack root "
                "(no leading '/', no '~', no '..')"
            )
    return dict(layout)


def db_define(
    pack_dir: str | Path,
    kind: str,
    payload: dict,
    *,
    actor: str = "user",
    session: str | None = None,
) -> dict:
    """``canon db define <pack> --type <kind> --set '<json>'`` — append a
    net-new ``EntityKind`` to the pack registry (P.7.5). The payload is a
    partial stamped entry (minimum ``label``, ``layout``, ``id_field``; an
    inline ``schema`` object becomes ``schemas/<kind>.json``). Writes the
    schema file, the empty collection file (or dir) in the declared
    format, and the registry entry; refuses an existing kind; journals one
    ``db_define`` on ``registry`` plus a ``create`` per new file. From then
    on every generic verb and every cradle surface serves the kind with
    zero code changes (success criterion 6)."""
    if not isinstance(payload, dict) or not payload:
        raise ValueError("--set needs a JSON object (at least label, layout, id_field)")
    if not isinstance(kind, str) or not kind.replace("_", "").isalnum() or not kind[0].isalpha():
        raise ValueError(f"kind {kind!r} must be an identifier (letters, digits, underscores)")
    # Doctrine 1's order is resolve → wall → validate → write: the whole
    # payload is checked against the READ-ONLY resolution first, so a refused
    # `db define` never synthesizes `.canon/registry.json` (which would flip
    # the pack from tier-2/3 to tier-1 resolution on a typo). ``ensure_registry``
    # — itself a write + a journal event — runs only once the verb is going
    # to write.
    pack, resolved = _resolve(pack_dir)
    if kind in resolved.spec.entities:
        raise ValueError(f"kind {kind!r} already exists — `db evolve` changes an existing kind")
    missing = [k for k in _DEFINE_REQUIRED if not payload.get(k)]
    if missing:
        raise ValueError(f"db define needs {list(_DEFINE_REQUIRED)}; missing {missing}")
    entry = copy.deepcopy(payload)
    schema_inline = entry.pop("schema", None)
    if isinstance(schema_inline, str):
        raise ValueError(
            'schema must be an inline object ({"fields": {...}}) — the file is written as schemas/<kind>.json'
        )
    if schema_inline is not None and not isinstance(schema_inline, dict):
        raise ValueError("schema must be an object")
    entry["layout"] = _validate_layout(kind, entry["layout"])
    entry["schema"] = f"schemas/{kind}.json"
    # The id is protected on every SEEDED kind (the platformer's
    # ``_protected_for``, the dungeon seed's explicit ``protected``) but
    # CORE_PROTECTED names no id field — so a defined kind is given the same
    # default here, or `db update` would let a row's id drift out of sync
    # with its filename (per_file) / its collection key.
    protected = list(entry.get("protected") or [])
    if entry["id_field"] not in protected:
        protected.append(entry["id_field"])
    entry["protected"] = protected
    try:
        EntityKind(kind=kind, **entry)
    except TypeError as exc:
        raise ValueError(f"db define payload: {exc}") from None
    schema_doc = db_models.empty_schema(kind)
    if schema_inline:
        schema_doc.update({k: v for k, v in schema_inline.items() if k in ("schema_version", "fields")})
    _validate_schema_document(schema_doc)

    doc, resolved, synthesis = ensure_registry(pack, actor=actor, session=session)
    events: list[dict] = []
    if synthesis is not None:
        events.append(synthesis)
    files: list[str] = []
    schema_rel = entry["schema"]
    if not (pack / schema_rel).is_file():
        committed = commit_document(
            pack, artifact_id=f"schema:{kind}", rel_path=schema_rel, data=schema_doc,
            actor=actor, session=session, detail={"kind": "db_define", "type": kind}, op="create",
        )
        events.append(committed["event"])
        files.append(schema_rel)
    layout = entry["layout"]
    if layout["mode"] == "collection":
        rel = str(layout["path"])
        if not (pack / rel).is_file():
            empty: Any = {} if layout["format"] == "keyed_object" else []
            committed = commit_document(
                pack, artifact_id=f"collection:{kind}", rel_path=rel, data=empty,
                actor=actor, session=session, detail={"kind": "db_define", "type": kind}, op="create",
            )
            events.append(committed["event"])
            files.append(rel)
    else:
        (pack / str(layout["dir"])).mkdir(parents=True, exist_ok=True)

    stamped = EntityKind(kind=kind, **entry).stamped()

    def apply(target: dict, _changes: dict) -> dict[str, dict]:
        target.setdefault("entities", {})[kind] = copy.deepcopy(stamped)
        return {f"entities.{kind}": {"from": None, "to": copy.deepcopy(stamped)}}

    result = write_registry(
        pack, doc, {f"entities.{kind}": stamped}, kind="db_define", actor=actor, session=session,
        apply=apply, detail_extra={"type": kind},
    )
    events.append(result["event"])
    return {
        "type": kind,
        "entry": stamped,
        "files": files,
        "changed": result["changed"],
        "events": len(events),
        "warnings": result["warnings"],
    }


def _rename_in_row(row: dict, old: str, new: str) -> bool:
    """Rename a top-level or one-level dotted key in *row*; True when it
    changed anything."""
    if "." in old:
        container, _, key = old.partition(".")
        bucket = row.get(container)
        new_key = new.partition(".")[2] if "." in new else new
        if isinstance(bucket, dict) and key in bucket:
            bucket[new_key] = bucket.pop(key)
            return True
        return False
    if old in row:
        value = row.pop(old)
        row[new] = value
        return True
    return False


def _rename_in_list(values: list[str], old: str, new: str) -> list[str]:
    out = []
    for name in values:
        if name == old:
            out.append(new)
        elif name.startswith(old + ".") or name.startswith(old + "["):
            out.append(new + name[len(old):])
        else:
            out.append(name)
    return out


def db_evolve(
    pack_dir: str | Path,
    kind: str,
    *,
    rename_field: str | None = None,
    rename_type: str | None = None,
    actor: str = "user",
    session: str | None = None,
) -> dict:
    """``canon db evolve <pack> --type <t> --rename-field old:new`` — a
    mechanical, journaled field rename across the type's rows + its
    registry entry (P.7.5; code applies it, no LLM): every row is rewritten
    (one ``edit`` event per rewritten file), the skeleton keeps its roll
    name while ``renames`` gains/updates the on-disk name, and every registry
    list naming the field (``llm_fields / code_fields / user_fields / hidden /
    decorative / protected / routed / refs / nesting``) follows; one
    ``db_evolve`` event on ``registry``. Warns loudly that the engine must
    follow. ``--rename-type`` is v1.1 — a structured not-yet."""
    if rename_type:
        raise NotYetError(
            "type renames are v1.1 (Phase 0 §6: `db evolve` does field renames only) — "
            "define the new kind with `db define` and move rows by hand until then",
            row="v1.1", type=kind,
        )
    if not rename_field or ":" not in rename_field:
        raise ValueError("--rename-field takes old:new")
    old, _, new = rename_field.partition(":")
    old, new = old.strip(), new.strip()
    if not old or not new or old == new:
        raise ValueError("--rename-field takes two different, non-empty names old:new")
    # resolve → wall → validate → write (doctrine 1): every refusal below is
    # answered off the READ-ONLY resolution, so a refused `db evolve` never
    # synthesizes `.canon/registry.json`.
    pack, resolved = _resolve(pack_dir)
    entity = _entity(resolved.spec, kind)
    wall = _wall(entity)
    if old.rsplit(".", 1)[-1] in wall or old == entity.id_field:
        raise ValueError(f"{old!r} is protected (identity / provenance / asset plumbing) — it cannot be renamed")
    if old in entity.routed:
        raise ValueError(f"{old!r} is owned by {entity.routed[old]} — use that surface")
    if new.rsplit(".", 1)[-1] in wall:
        raise ValueError(f"{new!r} is a protected name")
    # A kind whose seed binds a Pydantic model cannot be evolved by data
    # alone: `renames` moves the name on disk, but the model still DECLARES
    # the old one, so the next `db update` re-materializes it with its
    # default (``model_dump`` in ``update_db_row``'s validate) and the row
    # ends up carrying both names with the engine reading the fabricated
    # value. Doctrine 4 — refused with the reason, not silently corrupting.
    if "." not in old and entity.model is not None and old in entity.model.model_fields:
        raise ValueError(
            f"{old!r} is a declared field of {entity.model.__name__} — renaming it on {kind!r} needs a code "
            "change (the model would re-add the old name with its default on the next write); `renames` can "
            "only move a field the model does not declare"
        )

    doc, resolved, synthesis = ensure_registry(pack, actor=actor, session=session)
    entities = doc.get("entities") or {}
    if kind not in entities:
        raise ValueError(f"unknown db type {kind!r} (one of {list(entities)})")

    warnings = [
        f"engine must follow: {kind}.{old} is now {kind}.{new} on disk — the runtime reads the old name until "
        "its loader is updated (doctrine 10: data may outrun the engine)"
    ]
    rewritten: list[dict] = []
    if _is_per_file(entity):
        for row_id, row in load_per_file_rows(pack, entity).items():
            updated = copy.deepcopy(row)
            if not _rename_in_row(updated, old, new):
                continue
            rel = _per_file_rel(entity, row_id)
            committed = commit_document(
                pack, artifact_id=f"{kind}:{row_id}", rel_path=rel, data=updated, actor=actor, session=session,
                detail={"kind": "db_evolve", "type": kind, "changed": {old: {"from": old, "to": new}}}, op="edit",
            )
            rewritten.append({"file": rel, "after_hash": committed["after_hash"]})
    else:
        data = _read_collection(pack, entity)
        rows = list(data.values()) if isinstance(data, dict) else data
        touched = 0
        for row in rows:
            if isinstance(row, dict) and _rename_in_row(row, old, new):
                touched += 1
        if touched:
            rel = str(entity.layout.get("path"))
            committed = commit_document(
                pack, artifact_id=f"collection:{kind}", rel_path=rel, data=data, actor=actor, session=session,
                detail={"kind": "db_evolve", "type": kind, "rows": touched, "changed": {old: {"from": old, "to": new}}},
                op="edit",
            )
            rewritten.append({"file": rel, "rows": touched, "after_hash": committed["after_hash"]})

    entry = copy.deepcopy(entities[kind])
    renames = dict(entry.get("renames") or {})
    # The skeleton keeps its roll name: an existing map entry re-points to
    # the new disk name; a first rename maps the old disk name (== the roll
    # name until now) to the new one.
    skeleton_name = next((skel for skel, disk in renames.items() if disk == old), old)
    renames[skeleton_name] = new
    entry["renames"] = renames
    for key in ("llm_fields", "code_fields", "user_fields", "hidden", "decorative", "protected"):
        if entry.get(key):
            entry[key] = _rename_in_list(list(entry[key]), old, new)
    if entry.get("routed"):
        entry["routed"] = {(_rename_in_list([k], old, new)[0]): v for k, v in entry["routed"].items()}
    if entry.get("refs"):
        entry["refs"] = {(_rename_in_list([k], old, new)[0]): v for k, v in entry["refs"].items()}
    if entry.get("nesting"):
        entry["nesting"] = {
            (new if k == old else k): (new if v == old else v) for k, v in entry["nesting"].items()
        }
    if entry.get("containers"):
        entry["containers"] = _rename_in_list(list(entry["containers"]), old, new)

    def apply(target: dict, _changes: dict) -> dict[str, dict]:
        target.setdefault("entities", {})[kind] = entry
        return {f"entities.{kind}.fields": {"from": old, "to": new}}

    result = write_registry(
        pack, doc, {f"entities.{kind}.fields": new}, kind="db_evolve", actor=actor, session=session,
        apply=apply, detail_extra={"type": kind}, warnings=warnings,
    )
    return {
        "type": kind,
        "renamed": {"from": old, "to": new},
        "rewritten": rewritten,
        "entry": entry,
        "changed": result["changed"],
        "warnings": result["warnings"],
    }

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

Restore comes in TWO explicitly separated scopes, because the CAS unit of a
collection kind is the FILE while the thing a user points at is a ROW.
``restore_db_row`` lifts ONE row out of the stored version and drops it into
the CURRENT file, so every sibling row keeps the edits made since — the same
scoping the room-step restore does one level up, applied to a row slot
(``_row_in`` / ``_set_row_in``). It is what every restore surface reaches:
``platformer_write._restore_document`` — the one entry point behind ``canon
asset restore``, the agent's restore tool and the editor's Restore button —
sends ``<kind>:<id>`` of a collection kind here. ``restore_db_collection``
is the whole-file action, kept and labelled as such so a caller choosing it
knows every row in the file goes back, and answerable as a PLAN first
(``dry_run``) so the rows it would remove can be named before it writes.
Both run through the write core, so a restore is validated, journaled and
versioned like any other write: it writes a NEW version, it never rewinds
history and it never deletes a row.

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
    "generate_asset",
    "new_db_row",
    "read_db_schema",
    "restore_db_collection",
    "restore_db_row",
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
    mirror stands in as the primary — the bytes snapshotted are the WHOLE
    file (the kind's layout path, ``rooms/rooms.json``), so a stored version
    of ``<kind>:<id>`` carries every sibling row with it. What a RESTORE
    writes back is only the row's own slot out of those bytes
    (``restore_db_row``, where ``platformer_write._restore_document`` sends
    ``<kind>:<id>``); taking the whole file back is the separate, labelled
    ``restore_db_collection``. Consequence, by design: on a
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


def _collection_ids(entity: EntityKind, data: Any) -> list[str]:
    """Row ids in a collection's DATA, in file order — the in-memory twin of
    ``load_rows``' keying (a keyed object by its keys, an array by each row's
    ``id_field``), for comparing a stored version against the file on disk.

    Deliberately tolerant where ``load_rows`` is not: an entry with no id, or
    one that is not a row at all, has no id to report and is skipped. That is
    what lets a MALFORMED current file still be compared against the version
    that repairs it.
    """
    if isinstance(data, dict):
        return [str(key) for key in data]
    if not isinstance(data, list):
        return []
    return [
        str(row[entity.id_field])
        for row in data
        if isinstance(row, dict) and row.get(entity.id_field) is not None
    ]


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
    op: str = "edit",
    detail_kind: str = "db_update",
) -> list[dict]:
    """Write each mirror that carries a changed field, one journal event per
    file with ``mirror_of`` (P.7.3). A mirror gets a field when its own
    ``fields`` list names it, or — with no list — when the mirror row ALREADY
    carries that key: a mirror is kept consistent, never grown a key the file
    does not have (the manifest's room entry is a summary, not a row copy).

    *op* / *detail_kind* name the ACT the mirror write belongs to, so a row
    RESTORE's mirrors journal as a restore instead of borrowing the edit
    verb's label. Both default to what ``db update`` has always passed.
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
            op=op,
            detail={
                "kind": detail_kind,
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


def _row_pipeline(
    pack: Path,
    spec: PackSpec,
    entity: EntityKind,
    entity_id: str,
    *,
    per_file: bool,
    primary: _RowFile | None,
    skeleton: Any,
    model: type[BaseModel],
    warnings: list[str],
    ref_scope: Callable[[dict[str, dict]], list[str]],
) -> tuple[Callable[[Any], dict], Callable[[Any, dict], list[str]], Callable[[Any, dict], Any]]:
    """The ``(row_of, warn, validate)`` trio a ROW-level write mounts on
    ``write_document`` — lifted out of ``update_db_row`` verbatim so the row
    RESTORE runs the identical fail-closed validation instead of growing a
    second write path beside it.

    ``row_of`` finds the row in the document (the file itself for a
    ``per_file`` kind, the row slot inside the collection otherwise);
    ``warn`` surfaces off-table values; ``validate`` is fail-closed — it
    returns the model's normalized dump put back in the row's slot, or
    ``None`` to write the mutated document as-is.

    *ref_scope* is the ONE difference between the two callers. An EDIT that
    introduces a dangling reference is refused (``check_refs`` raises for a
    path it was handed as changed); a RESTORE only ever warns — the author
    picked those bytes, and nothing here repairs or refuses what they asked
    for, the same rule the room-step restore follows for a placement standing
    in a restored wall.
    """

    def row_of(doc: Any) -> dict:
        if per_file:
            return doc
        assert primary is not None
        row = _row_in(doc, primary, entity_id)
        if row is None:
            raise FileNotFoundError(f"{entity.kind} {entity_id!r} not found")
        return row

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
        warnings.extend(db_models.check_refs(pack, spec, entity, row, ref_scope(diff)))
        return None

    return row_of, warn, validate


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
    row_of, warn, validate = _row_pipeline(
        pack, spec, entity, entity_id,
        per_file=per_file, primary=primary, skeleton=skeleton, model=model,
        warnings=warnings, ref_scope=lambda diff: list(diff),
    )

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
# restore — ONE row slot, or the whole collection file
# ---------------------------------------------------------------------------


def _restore_lineage(
    pack: Path,
    entity: EntityKind,
    artifact_id: str,
    to_hash: str,
    *,
    per_file: bool,
) -> None:
    """Refuse a version that is not part of this artifact's own history.

    A collection kind's CAS unit is the FILE, so every event that ever wrote
    that file is part of the lineage: the rows' own ids (``npc:1000``, and the
    ``world_bible`` name a ``row_source`` mirror publishes under), plus the
    whole-file ``collection:<kind>`` events ``db define`` / ``db evolve``
    write. A ``per_file`` kind gets the strict test instead — one file, one
    row, one artifact id — or one row's bytes would land on another row.
    """
    kind = entity.kind

    def owns(aid: str) -> bool:
        if per_file:
            return aid == artifact_id
        return aid == artifact_id or aid.startswith(f"{kind}:") or aid == f"collection:{kind}"

    if not any(
        owns(str(event.get("artifact_id", "")))
        and to_hash in (event.get("before_hash"), event.get("after_hash"))
        for event in provenance.all_events(pack)
    ):
        raise ValueError(
            f"{to_hash} is not part of {artifact_id}'s history — restore only rewinds an "
            "artifact's own lineage"
        )


def _version_document(pack: Path, to_hash: str) -> Any:
    """The stored version's bytes, parsed. A hash whose bytes are not JSON is
    the wrong hash (a sprite, an atlas) — refused before anything is read out
    of it."""
    data = provenance.read_object(pack, to_hash)
    try:
        return json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError(f"version {to_hash} is not JSON — wrong hash?") from None


def _is_whole_document(target: _RowFile | None, *, per_file: bool) -> bool:
    """True when the row IS the file — a ``per_file`` kind, or a ``document``
    mirror standing in as the row (the file holds one row, not a collection)."""
    return per_file or (target is not None and target.format == "document")


def _stored_row(
    entity: EntityKind,
    target: _RowFile | None,
    entity_id: str,
    version: Any,
    to_hash: str,
    rel: str,
    *,
    per_file: bool,
) -> dict:
    """The ONE row this restore is about, lifted out of the stored version.

    The two refusals the owner ruled on live here. A version that does not
    carry the row (it predates the row's creation, or postdates its removal)
    is refused rather than writing an absent row back as a deletion; a version
    of some other file is refused as the wrong hash.
    """
    if _is_whole_document(target, per_file=per_file):
        if not isinstance(version, dict):
            raise ValueError(f"version {to_hash} is not a {entity.kind} row — wrong hash?")
        stored_id = version.get(entity.id_field)
        if stored_id is not None and str(stored_id) != str(entity_id):
            raise ValueError(
                f"version {to_hash} belongs to {entity.kind} {stored_id!r}, not {entity_id!r}"
            )
        return version
    assert target is not None
    # The lineage test is per-ARTIFACT-FAMILY, and a family can span more than
    # one file (a room's rows and its grid files are both `room:…`), so the
    # bytes are checked against the layout's own shape before a row is looked
    # for in them: without this a grid version would reach the "no such row"
    # refusal below and blame the row for what is really the wrong hash.
    collection = _collection_in(version, target)
    shaped = isinstance(collection, (list, dict))
    if shaped and target.format == (entity.layout or {}).get("format"):
        try:
            db_models.check_collection_shape(entity, collection)
        except ValueError:
            shaped = False
    if not shaped:
        raise ValueError(f"version {to_hash} is not {rel} — wrong hash?")
    row = _row_in(version, target, entity_id)
    if row is None:
        raise ValueError(
            f"{entity.kind} {entity_id!r} is not in version {to_hash} of {rel} — that version "
            "was written before the row was created (or after it was removed). A restore never "
            "deletes a row: pick a version that carries it, or restore the whole collection to "
            "take every row in the file back that far."
        )
    return row


def restore_db_row(
    pack_dir: str | Path,
    entity_type: str,
    entity_id: str,
    to_hash: str,
    *,
    actor: str = "user",
    session: str | None = None,
) -> dict:
    """Make a stored version of ONE row current again (``op:"restore"``).

    Scoped to the row's own slot. The stored version of a collection kind is
    the WHOLE file — every sibling row as it stood then — so writing those
    bytes back would silently revert every edit made to every other row in
    ``npcs.json`` since (doctrine 10: an edit may not disappear unannounced).
    Instead the target row is lifted OUT of the stored version and dropped
    into the CURRENT file (``_row_in`` / ``_set_row_in``): siblings are not
    read, not rewritten, not touched. This is the room-step restore's fix one
    level down — there a step's own keys, here a row's own slot.

    The stored state of the row is what becomes current: a key the version
    does not carry is removed, the row's own ``status`` comes back with it
    (nothing is stamped over the bytes the author picked), and the journal
    diff is per top-level row key — a container comes back whole, because the
    slot being restored is the row, not one knob inside it. The write goes
    through the same pipeline as a hand edit — the entity model validates
    fail-closed, mirrors that carry a restored field follow in the same batch,
    and the result is a NEW version, journaled, with nothing deleted
    (doctrine 6). A restore whose row is already current is a ``no_change``:
    nothing written, nothing journaled.

    Refused when the chosen version does not carry the row at all — it
    predates the row's creation or postdates its removal. Nothing is written
    and the row is never deleted; ``restore_db_collection`` is the labelled
    way to take the whole file back that far. Where the rewind brings back a
    field another surface owns — ``routed`` to another verb, or behind the
    wall (the asset pointer, its hash, the identity/provenance plumbing) — it
    warns naming that surface: a restore is the author's choice of bytes, so
    it is never refused and never repaired behind their back, but no
    protection class goes by unannounced.
    """
    pack, resolved = _resolve(pack_dir)
    spec = resolved.spec
    entity = _entity(spec, entity_type)
    per_file = _is_per_file(entity)
    primary: _RowFile | None = None
    mirrors: list[tuple[_RowFile, Any]] = []
    if per_file:
        rel = _per_file_rel(entity, entity_id)
        if not (pack / rel).is_file():
            raise FileNotFoundError(f"{entity_type} {entity_id!r} not found")
        document: Any = read_json(pack / rel)
        artifact_id = f"{entity_type}:{entity_id}"
    else:
        primary, document, mirrors = _resolve_row_files(pack, entity, entity_id)
        rel, artifact_id = primary.rel, primary.artifact_id

    _restore_lineage(pack, entity, artifact_id, to_hash, per_file=per_file)
    version = _version_document(pack, to_hash)
    stored = copy.deepcopy(
        _stored_row(entity, primary, entity_id, version, to_hash, rel, per_file=per_file)
    )
    whole_document = _is_whole_document(primary, per_file=per_file)

    skeleton, _p, _s = db_models.schema_for(pack, spec, entity)
    model = entity.model or db_models.dynamic_model(entity, skeleton)
    warnings: list[str] = []
    row_of, warn, validate = _row_pipeline(
        pack, spec, entity, entity_id,
        per_file=per_file, primary=primary, skeleton=skeleton, model=model,
        # A rewind may land on a reference whose target was created later —
        # warn, never refuse: the author chose these bytes and nothing here
        # repairs or blocks what they asked for.
        warnings=warnings, ref_scope=lambda _diff: [],
    )

    def apply(doc: Any, _changes: dict) -> dict[str, dict]:
        current = row_of(doc)
        diff: dict[str, dict] = {}
        for name in sorted(set(current) | set(stored)):
            old = current.get(name)
            if name not in stored:
                diff[name] = {"from": old, "to": None}  # the version had no such key
            elif old != stored[name]:
                diff[name] = {"from": old, "to": stored[name]}
        if not diff:
            return {}
        # A version is the whole row, so a rewind can bring back a field some
        # OTHER surface owns — a field ROUTED to another verb (an npc's grid
        # position, a dialogue tree) or one behind the WALL (the asset pointer
        # and its hash, the identity/provenance plumbing). `db update` refuses
        # both; a restore cannot, because the author picked these bytes — so it
        # says which surface may now disagree instead of refusing or quietly
        # repairing (doctrine 10). The stronger protection class must not be
        # the quieter one: every disturbed field is named, whichever list it
        # is on.
        walled = _wall(entity)
        asset = entity.asset or {}
        plumbing = {asset.get("field"), asset.get("hash_field")} - {None}
        for name, change in diff.items():
            verb = entity.routed.get(name)
            if verb:
                warnings.append(
                    f"{name!r} is owned by {verb}: the restore brought its stored value back, so "
                    f"the {verb} surface may now disagree with this row — nothing was repaired for you"
                )
            elif name in plumbing:
                warnings.append(
                    f"{name!r} is asset plumbing (`canon asset replace` owns it): the restore "
                    f"moved it back to {change['to']!r} — nothing checked that the file it names "
                    "is on disk, and nothing was repaired for you"
                )
            elif name in walled:
                warnings.append(
                    f"{name!r} is protected (identity / provenance / asset plumbing): the restore "
                    "brought its stored value back — nothing was repaired for you"
                )
        row = copy.deepcopy(stored)
        if whole_document:
            doc.clear()
            doc.update(row)
        else:
            assert primary is not None
            _set_row_in(doc, primary, entity_id, row)
        return diff

    label = f"restores {entity_type} {entity_id} in {rel} (1 row; siblings untouched)"
    batch = f"db-restore:{entity_type}:{entity_id}" if mirrors else None
    with provenance.bind_batch(batch) if mirrors else contextlib.nullcontext():
        result = write_document(
            pack,
            artifact_id=artifact_id,
            rel_path=rel,
            document=document,
            # The row is ONE slot, so the pipeline is handed one change and the
            # apply step above answers the per-FIELD diff the journal carries.
            changes={"row": stored},
            apply=apply,
            warn=warn,
            validate=validate,
            user_edited=False,
            actor=actor,
            session=session,
            detail={
                "kind": "row_restore",
                "type": entity_type,
                "id": entity_id,
                "scope": "row",
                "to": to_hash,
                "file": rel,
                "rows": 1,
                "label": label,
            },
            op="restore",
            source="user",
            warnings=warnings,
        )
        files = (
            []
            if result.get("no_change")
            else _write_mirrors(
                pack, entity, entity_id, mirrors,
                # A key the version did not carry is removed from the ROW; a
                # mirror is kept consistent in the keys it has and is never
                # handed a null to write.
                {name: d for name, d in result["changed"].items() if name in stored},
                actor=actor, session=session, op="restore", detail_kind="row_restore",
            )
        )
    return {
        # `kind` / `label` are the shape every restore surface already answers
        # with (the journal detail, the History card, the editor's response
        # type) — the scoped restore keeps them so callers need no new branch.
        "kind": "row_restore",
        "type": entity_type,
        "id": entity_id,
        "scope": "row",
        "label": label,
        "row": row_of(result["document"]),
        "restored_to": to_hash,
        "artifact_id": artifact_id,
        "file": rel,
        "mirrors": files,
        "changed": result["changed"],
        "no_change": bool(result.get("no_change")),
        "warnings": result["warnings"],
        "before_hash": result["before_hash"],
        "after_hash": result["after_hash"],
    }


def restore_db_collection(
    pack_dir: str | Path,
    entity_type: str,
    to_hash: str,
    *,
    entity_id: str | None = None,
    dry_run: bool = False,
    actor: str = "user",
    session: str | None = None,
) -> dict:
    """Make a stored version of the WHOLE collection file current again.

    The separate, explicitly labelled whole-file action: every row in
    ``<layout.path>`` goes back to the way it stood in that version, including
    rows the caller never looked at. ``restore_db_row`` is the per-row scope;
    this one is for "take the file back", and its journal label says so
    ("restores every ``<kind>`` row in ``<file>`` (N rows)") so a caller
    choosing it knows what it reverts.

    Fail-closed on what it WRITES: the version must be part of the
    collection's own lineage and must re-parse in the kind's layout format
    with unique ids. Open on what it REPLACES: the current file is read as
    bytes, never shape-checked, because repairing a collection a bad merge or
    a hand edit left malformed is exactly what this action is for.

    A row created since that version is not in it, so this takes the file back
    past that row's creation and REMOVES it. Nothing goes silently, and
    nothing is deleted without asking first (doctrine 6): *dry_run* answers
    the whole plan — ``label``, ``rows``, ``removed``, ``warnings`` — and
    writes NOTHING, so a caller can name the rows about to go before it
    offers the button, not in the journal afterwards. The refusals are the
    same in a dry run as in the write, so what a caller is shown is what it
    would get. ``restore_db_row`` is the way to take one row back without
    touching the rest of the file. History itself is never rewound: this
    writes a NEW version and journals ``op:"restore"``; a version that is
    already current is a ``no_change``. *entity_id* only attributes the event
    to the row the caller came from, so the restore stays visible in that
    row's history; without it the event is published under the collection
    itself.
    """
    pack, resolved = _resolve(pack_dir)
    entity = _entity(resolved.spec, entity_type)
    if _is_per_file(entity):
        raise ValueError(
            f"{entity_type} rows each live in their own file — there is no collection file to "
            f"restore; restore the row instead"
        )
    rel = str(entity.layout.get("path"))
    artifact_id = f"{entity_type}:{entity_id}" if entity_id is not None else f"collection:{entity_type}"
    _restore_lineage(pack, entity, artifact_id, to_hash, per_file=False)
    version = _version_document(pack, to_hash)
    try:
        db_models.check_collection_shape(entity, version)
    except ValueError:
        raise ValueError(f"version {to_hash} is not the {entity_type} collection — wrong hash?") from None
    db_models.check_ids_unique(entity, version)

    rows = len(version)
    warnings: list[str] = []
    mirrored = [str(m.get("file")) for m in (entity.layout.get("mirrors") or []) if isinstance(m, dict)]
    if mirrored:
        warnings.append(
            f"{entity_type} rows are also copied into {', '.join(mirrored)} — a whole-collection "
            f"restore writes {rel} only, so those copies keep their current values until each row "
            "is edited or restored"
        )
    # The CURRENT file is read as BYTES, not through `_read_collection`'s shape
    # check: a whole-file restore is precisely the way back from a collection a
    # bad merge or a hand edit left malformed, so the state being REPLACED may
    # not gate it. What is about to be WRITTEN stays fail-closed — the version
    # is shape- and id-checked above.
    try:
        current: Any = read_json(pack / rel)
        if current is None:  # absent file: the kind's empty collection
            current = {} if entity.layout.get("format") == "keyed_object" else []
    except json.JSONDecodeError:
        # Not even JSON — a merge left its markers in the file. There is
        # nothing to compare it against and no ids to name; this action is the
        # way out of that state, so it proceeds and overwrites.
        current = object()
    no_change = current == version
    # Doctrine 6: nothing is deleted without asking. A row created since the
    # chosen version is not IN it, so taking the file back that far removes it
    # — name that at the point of CHOICE, which means the plan is finished
    # before anything is written and `dry_run` can hand it back whole. A
    # caller that asks first shows the ids; a caller that does not still gets
    # them in the warnings, the label and `detail.removes`.
    kept = set(_collection_ids(entity, version))
    removed = [] if no_change else [rid for rid in _collection_ids(entity, current) if rid not in kept]
    label = f"restores every {entity_type} row in {rel} ({rows} rows)"
    if removed:
        listed = ", ".join(removed[:10]) + (f", +{len(removed) - 10} more" if len(removed) > 10 else "")
        warnings.append(
            f"{len(removed)} {entity_type} row(s) are not in that version and this restore REMOVES "
            f"them: {listed} — they were created after it. Restore the row instead to take one row "
            "back and leave the rest of the file alone"
        )
        label += f", removing {len(removed)} added since ({listed})"
    plan = {
        "kind": "row_restore",
        "type": entity_type,
        "scope": "collection",
        "label": label,
        "file": rel,
        "rows": rows,
        "restored_to": to_hash,
        "artifact_id": artifact_id,
        "removed": removed,
        "warnings": warnings,
    }
    if dry_run or no_change:
        return {
            **plan,
            # A dry run wrote nothing; a no-change had nothing to write.
            **({"dry_run": True} if dry_run else {}),
            "no_change": no_change,
            "before_hash": None,
            "after_hash": None,
        }
    committed = commit_document(
        pack,
        artifact_id=artifact_id,
        rel_path=rel,
        data=version,
        actor=actor,
        session=session,
        detail={
            "kind": "row_restore",
            "type": entity_type,
            "scope": "collection",
            "to": to_hash,
            "file": rel,
            "rows": rows,
            "label": label,
            **({"removes": removed} if removed else {}),
        },
        op="restore",
        source="user",
    )
    return {
        **plan,
        "no_change": False,
        "before_hash": committed["before_hash"],
        "after_hash": committed["after_hash"],
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


# ---------------------------------------------------------------------------
# `canon asset generate` for every non-platformer pack — `--target missing`
# ---------------------------------------------------------------------------

#: The target that means "every asset this pack should have and does not".
MISSING_TARGET = "missing"

#: ``family → the flag that wires its backend`` — for the refusal that names
#: the missing capability rather than a document.
_BACKEND_FLAGS: dict[str, str] = {
    "image": "--image-backend", "music": "--music-backend", "sfx": "--sfx-backend",
}


def _asset_stats_path(pack: Path) -> str:
    from canon.config import _default_output_paths

    return _default_output_paths().get("generation_stats", "generation_stats.json")


def _asset_cost_error(kind: str, backend: str | None) -> str | None:
    """The LOUD "no price row" reason for a PAID backend canon cannot price
    (never a silent $0) — ``None`` for fake/none (an honest $0) or a priced
    backend. The platformer's ``_cost_error``, for the dungeon's lanes."""
    from canon import pricing

    if not pricing.is_paid(kind, backend):
        return None
    resolved = pricing.default_model(kind, backend)
    warnings: list[str] = []
    if resolved and pricing.price_for(kind, resolved, warnings) is not None:
        return None
    return f"{backend}: no price row for {resolved or backend!r} in canon.pricing"


def generate_asset(
    pack_dir: str | Path,
    target: str,
    *,
    image_backend: str | None = None,
    image_model: str | None = None,
    image_edit_model: str | None = None,
    image_edit_backend: str | None = None,
    music_backend: str | None = None,
    sfx_backend: str | None = None,
    prompt_override: str | None = None,
    actor: str = "user",
    session: str | None = None,
) -> dict[str, Any]:
    """(Re)generate the assets of a non-platformer pack — ONE ``<kind>:<id>``
    (``npc:1000`` / ``class:warrior`` / ``room:room_0`` / ``portrait:player``
    / ``music:combat`` / ``sfx:door_open``) or ``--target missing``: every
    asset whose file is absent from disk. ``missing`` only ever writes into
    an empty slot — a target ``generation_stats.failures`` still lists whose
    file has since appeared is not regenerated; its stale record is dropped
    from the list instead. The same signature as the platformer's
    ``ops.generate_asset``, so ``_pack_ops`` routes ``canon asset generate``
    here for a dungeon pack unchanged.

    A single-target reroll overwrites a file that exists, so its prior bytes
    are snapshotted into the object store first (``provenance.snapshot_file``
    — the platformer's reroll does the same) and the op's journal event
    carries ``before_hash`` / ``after_hash``: every write is a version.

    How it repairs without re-spending: the pack's asset PLAN is rebuilt from
    disk by the template's planner (``canon.adapters.ASSET_PLANNERS``),
    filtered to what is missing, and run through the pipeline's own
    ``AssetPhase.run_jobs`` — the executor that retries a retryable failure,
    counts every call and records every final failure. What lands is written
    back through ``backfill_portraits`` (rows + manifest, one journal event
    per file under *actor*), the manifest's audio index is refreshed, and
    ``generation_stats.json`` is updated in place: counters accumulate, the
    ``failures`` list drops what now exists and gains what still does not.
    The op itself journals one costed event on *target*. A family without a
    backend flag is skipped and reported, never guessed.

    ``prompt_override`` applies to a single-target reroll only (a repair of
    many assets has no one prompt to replace); ``image_model`` is set on an
    image backend that exposes ``model``. The edit-backend knobs are accepted
    for signature parity and unused — nothing here is img2img.
    """
    import random

    from canon.adapters import ASSET_BACKEND_BUILDERS, ASSET_PLANNERS, grid_verb
    from canon.bible.models import Bible
    from canon.config import CanonConfig
    from canon.packs import PACKS
    from canon.packs.dungeon.portraits import backfill_portraits
    from canon.pipeline.phases.asset import AssetPhase
    from canon.pipeline.phases.manifest import ManifestPhase
    from canon.pipeline.runner import PipelineContext
    from canon.pipeline.stats import GenerationStats
    from canon.pipeline.steplog import StepLog

    pack = Path(pack_dir)
    resolved = resolve_pack(pack)
    planner = grid_verb(ASSET_PLANNERS, resolved.pack_type)
    if planner is None:
        raise ValueError(
            f"asset generate has no planner for a {resolved.pack_type!r} pack — "
            "its own asset verbs serve it"
        )
    builder = grid_verb(ASSET_BACKEND_BUILDERS, resolved.pack_type)

    # 1. The plan, filtered to the target.
    jobs = planner(pack, resolved.spec, fallback=PACKS.get(resolved.pack_type))
    stats_rel = _asset_stats_path(pack)
    existing_stats = read_json(pack / stats_rel)
    if not isinstance(existing_stats, dict):
        existing_stats = {}
    if target == MISSING_TARGET:
        selected = [j for j in jobs if not (pack / j.rel).is_file()]
    else:
        selected = [j for j in jobs if j.target == target]
        if not selected:
            known = sorted({j.target.split(":", 1)[0] for j in jobs})
            raise FileNotFoundError(
                f"asset target {target!r} not found in {pack} — "
                f"targets are <kind>:<id> for {', '.join(known)}, or {MISSING_TARGET!r}"
            )
        if prompt_override:
            for job in selected:
                job.prompt = prompt_override

    # 2. Backends — only the families the caller wired.
    names = {"image": image_backend or "", "music": music_backend or "", "sfx": sfx_backend or ""}
    if builder is None:  # pragma: no cover — every planner entry has a builder
        raise ValueError(f"asset generate has no backend builder for {resolved.pack_type!r}")
    try:
        image, music, sfx = builder(names["image"], names["music"], names["sfx"])
    except SystemExit as e:  # the runner's own refusal wording, as a ValueError
        raise ValueError(str(e)) from None
    if image is not None and image_model and hasattr(image, "model"):
        image.model = image_model
    backends = {"image": image, "music": music, "sfx": sfx}
    families = {j.family for j in selected}
    skipped: dict[str, str] = {}
    for family in sorted(families):
        if backends.get(family) is None:
            count = sum(1 for j in selected if j.family == family)
            skipped[family] = (
                f"no {_BACKEND_FLAGS[family]} given — its {count} asset(s) were left as they are"
            )
    runnable = [j for j in selected if backends.get(j.family) is not None]
    if selected and not runnable:
        wanted = ", ".join(_BACKEND_FLAGS[f] for f in sorted(families))
        raise ValueError(f"{target!r} needs a backend for {sorted(families)}: pass {wanted}")

    # 3. Run them through the pipeline's executor, on a context of this pack.
    manifest = read_json(pack / "manifest.json")
    seed = str((manifest or {}).get("seed") if isinstance(manifest, dict) else "") or "repair"
    stats = GenerationStats(
        image_backend=names["image"], music_backend=names["music"], sfx_backend=names["sfx"],
    )
    ctx = PipelineContext(
        bible=Bible.empty(seed=seed),
        config=CanonConfig(seed=seed, output_dir=pack),
        rng=random.Random(seed),
        stats=stats,
        adapter=pack_adapter(pack),
        steplog=StepLog(pack),
    )
    for family, backend in backends.items():
        if backend is not None:
            setattr(ctx, f"{family}_backend", backend)
    phase = AssetPhase(
        skip_image=image is None, skip_music=music is None, skip_sfx=sfx is None,
    )
    # A file about to be overwritten is versioned first (rule: nothing is
    # lost without a snapshot). Under ``missing`` every slot is empty by
    # construction, so this only ever fires for a single-target reroll.
    before_hashes = {
        j.target: h for j in runnable if (h := provenance.snapshot_file(pack, pack / j.rel))
    }
    new_failures = phase.run_jobs(ctx, runnable) if runnable else []
    failed_targets = {f["target"] for f in new_failures}
    # Landed means the EXECUTOR landed it: a reroll whose backend failed
    # leaves the old file in place, and that file is not a success.
    landed = [
        j for j in runnable if j.target not in failed_targets and (pack / j.rel).is_file()
    ]
    after_hashes = {
        j.target: h for j in landed if (h := provenance.snapshot_file(pack, pack / j.rel))
    }

    # 4. Write back what landed — rows + manifest portraits (journaled per
    # file under the actor), then the manifest's audio index.
    changed_files: list[str] = []
    if any(j.family == "image" for j in landed):
        repaired = backfill_portraits(pack, actor=actor, session=session)
        changed_files.extend(repaired.get("files") or [])
    if any(j.family in ("music", "sfx") for j in landed):
        changed_files.extend(_refresh_audio_index(pack, ManifestPhase(), actor=actor, session=session))

    # 5. generation_stats.json: counters accumulate; the failure list is
    # what is STILL missing — repaired targets drop out, new failures land.
    merged = dict(existing_stats)
    for field_name in (
        "image_attempts", "image_successes", "music_attempted", "music_succeeded",
        "sfx_attempted", "sfx_succeeded",
    ):
        merged[field_name] = int(merged.get(field_name) or 0) + int(getattr(stats, field_name, 0))
    merged["images_attempted"] = merged["image_attempts"]
    merged["images_succeeded"] = merged["image_successes"]
    for cost_field in ("image_cost_usd", "audio_cost_usd"):
        merged[cost_field] = round(
            float(merged.get(cost_field) or 0.0) + float(getattr(stats, cost_field, 0.0)), 6
        )
    merged["total_cost_usd"] = round(
        float(merged.get("llm_cost_usd") or 0.0) + float(merged.get("vlm_cost_usd") or 0.0)
        + merged["image_cost_usd"] + merged["audio_cost_usd"], 6,
    )
    landed_targets = {j.target for j in landed}
    on_disk = {j.target for j in jobs if (pack / j.rel).is_file()}
    kept = [
        f for f in (existing_stats.get("failures") or [])
        if isinstance(f, dict) and f.get("target") not in landed_targets
        and f.get("target") not in failed_targets
        # A listed target whose file is on disk was repaired some other way
        # (a reroll, user art); the record is stale and drops out.
        and f.get("target") not in on_disk
    ]
    merged["failures"] = kept + list(new_failures)
    if selected:
        commit_document(
            pack, artifact_id="generation_stats", rel_path=stats_rel, data=merged,
            actor=actor, session=session, op="edit", source="repair",
            detail={"kind": "asset_generate", "target": target},
        )
        changed_files.append(stats_rel)

    # 6. One costed journal event for the op itself.
    image_usd = float(stats.image_cost_usd)
    audio_usd = float(stats.audio_cost_usd)
    used = [b for b in backends.values() if b is not None]
    accuracy = provenance.combine_accuracy(*[provenance.backend_accuracy(b) for b in used])
    ran_families = sorted({j.family for j in runnable})
    gen_kind = (
        "image" if ran_families == ["image"]
        else "audio" if ran_families and "image" not in ran_families
        else "asset"
    )
    primary_family = "image" if "image" in ran_families else (ran_families[0] if ran_families else "image")
    primary_backend = names.get(primary_family) or None
    cost_block = {
        "usd": round(image_usd + audio_usd, 6), "llm_usd": 0.0,
        "image_usd": round(image_usd, 6), "audio_usd": round(audio_usd, 6),
        "input_tokens": 0, "output_tokens": 0, "calls": 0, "backend": "",
    }
    gen = provenance.gen_cost(
        cost_block, accuracy=accuracy, backend=primary_backend,
        model=str(getattr(backends.get(primary_family), "model", "") or "") or None,
        component_accuracy={
            f: provenance.backend_accuracy(b) for f, b in backends.items() if b is not None
        },
    )
    cost_error = next(
        (err for f in ran_families if (err := _asset_cost_error("music" if f == "music" else f, names[f]))),
        None,
    )
    # The hashes are per file; the op event names one target, so they ride
    # along only for a single-target reroll (one file in play). ``missing``
    # fills empty slots: no before, and its per-file commits (rows,
    # manifest) carry their own hashes.
    only = runnable[0].target if target != MISSING_TARGET and len(runnable) == 1 else None
    event = provenance.record(
        pack, artifact_id=target,
        op="regenerate" if before_hashes else "generate",
        source="llm", actor=actor, session=session,
        detail={
            "kind": "asset_generate", "planned": len(selected), "landed": len(landed),
            "failed": len(new_failures), "skipped": skipped,
        },
        before_hash=before_hashes.get(only) if only else None,
        after_hash=after_hashes.get(only) if only else None,
        gen=gen, gen_kind=gen_kind, accuracy=accuracy, cost_error=cost_error,
    )
    warnings = [f"{f['target']}: {f['message']} — {f['hint']}" for f in new_failures]
    warnings.extend(f"{family}: {reason}" for family, reason in skipped.items())
    return {
        "target": target,
        "generated": bool(landed),
        "planned": [j.target for j in selected],
        "landed": [j.target for j in landed],
        "failures": list(new_failures),
        "skipped": skipped,
        "gen": gen,
        "journal_ref": event.get("ts") if event.get("costCents") is not None else None,
        "cost": {
            **cost_block,
            "image_usd": round(image_usd, 6), "audio_usd": round(audio_usd, 6),
        },
        "warnings": warnings,
        "changed": bool(landed),
        "changed_artifacts": [j.target for j in landed],
        "files": sorted(set(changed_files)),
    }


def _refresh_audio_index(pack: Path, manifest_phase: Any, *, actor: str, session: str | None) -> list[str]:
    """Re-scan ``music/`` and ``sfx/`` into ``manifest.json``'s ``music`` /
    ``sfx`` maps the way ``ManifestPhase`` builds them, committed under the
    actor when anything changed. Returns the files written."""
    rel = "manifest.json"
    manifest = read_json(pack / rel)
    if not isinstance(manifest, dict):
        return []
    music = manifest_phase._scan_audio_dir(pack, "music")
    sfx = manifest_phase._scan_audio_dir(pack, "sfx")
    if manifest.get("music") == music and manifest.get("sfx") == sfx:
        return []
    manifest["music"], manifest["sfx"] = music, sfx
    commit_document(
        pack, artifact_id="manifest", rel_path=rel, data=manifest, actor=actor,
        session=session, op="edit", source="repair",
        detail={"kind": "asset_generate", "recorded": {"music": len(music), "sfx": len(sfx)}},
    )
    return [rel]

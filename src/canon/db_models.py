"""Schema-derived row models + the fail-closed checks every ``db`` verb runs
(P0 paper P.3.1 "dynamic model rule"; row P0-6).

An ``EntityKind`` with a Pydantic ``model`` (the platformer's enemy/item)
validates through it — the strictness upgrade. A kind WITHOUT one (every
dungeon kind, every ``db define``d kind) validates through a model built
here from its skeleton schema:

- fields = the skeleton's rolled fields under their ON-DISK names
  (``renames`` applied), typed by mode — ``choices`` → the value type of the
  first choice, ``range`` → ``int``, ``lookup`` → the value type (``int``
  when ``lookup_ranges``); every field optional (a row may predate a schema
  edit); ``extra = allow`` — non-rolled fields are unchecked in v1;
- REFINEMENT, documented here because P.1 forced it: a rolled field the
  registry also lists in ``code_fields`` (event ``difficulty``: rolled as a
  label, coerced to an int by the parser; npc ``type``: rolled as
  ``behavior_type``, written as the engine class name) is typed ``Any`` —
  the code derivation, not the roll, owns its on-disk type. A dotted rename
  target (``item_stats.stat_modifier``) is nested and also unchecked.
- off-table values (a choice not in the table, a range/band miss) WARN,
  never block — ``update_db_row``'s precedent, lifted verbatim into
  ``off_table_warnings``.

Fail-closed (P.3.1) for a collection layout = the file re-parses in its
``layout.format`` (``check_collection_shape``), ``id_field`` values stay
unique (``check_ids_unique``), and every ``refs`` path the write TOUCHED
resolves against the pack (``check_refs`` — a dangling ref a legacy row
already carried warns; a dangling ref this write introduces refuses, so an
unrelated edit on a legacy row never blocks: doctrine 10).

What this extends: ``canon.skeleton`` (the same ``SkeletonSpec`` that
bounds generation now bounds editing — §5.1a) and ``ops.db_types``'s field
serialization (``skeleton_field_entries``, verbatim). Deliberately absent:
container sub-schemas (P.1 "validated against the sub-schema" is a v1.1
skeleton extension — a ``[+]`` append checks shape, not table membership).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, create_model

from canon.packs.spec import EntityKind, PackSpec
from canon.skeleton.core import SkeletonSpec
from canon.skeleton.loader import load_skeleton_spec
from canon.write_core import get_path, parse_address

__all__ = [
    "check_collection_shape",
    "check_ids_unique",
    "check_refs",
    "dynamic_model",
    "empty_schema",
    "flatten_row",
    "off_table_warnings",
    "schema_for",
    "skeleton_field_entries",
]


def empty_schema(kind: str) -> dict[str, Any]:
    """The schema document a kind with no roll table reads as (and the one
    ``db define`` writes when the payload carries none)."""
    return {"schema_version": "1", "entity_type": kind, "fields": {}}


def schema_for(
    pack: str | Path, spec: PackSpec, entity: EntityKind
) -> tuple[SkeletonSpec | None, Path | None, str | None]:
    """``(skeleton spec, path, source)`` for *entity* in *pack*: the pack-local
    ``schemas/<kind>.json`` override wins (``source: "pack"``), else the
    template's (``"default"`` — ``db types``' word for it), else ``None``
    (no roll table on either side; the spec is ``None`` and the dynamic
    model has no typed fields). A file with an empty ``fields`` object is a
    present-but-empty table (``db define`` without an inline schema)."""
    rel = entity.schema or f"schemas/{entity.kind}.json"
    local = Path(pack) / rel
    if local.is_file():
        path, source = local, "pack"
    elif spec.template_dir is not None and (spec.template_dir / rel).is_file():
        path, source = spec.template_dir / rel, "default"
    else:
        return None, None, None
    import json

    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict) and not raw.get("fields"):
        return None, path, source
    return load_skeleton_spec(path), path, source


def skeleton_field_entries(spec: SkeletonSpec | None) -> list[dict[str, Any]]:
    """``db types``' per-field serialization (verbatim from
    ``ops.db_types``): name + mode + the table each mode carries."""
    fields: list[dict[str, Any]] = []
    if spec is None:
        return fields
    for name, field in spec.fields.items():
        entry: dict[str, Any] = {"name": name}
        if field.choices is not None:
            entry["mode"] = "choices"
            entry["choices"] = [v for v, _ in field.choices]
        elif field.range is not None:
            entry["mode"] = "range"
            entry["range"] = list(field.range)
        else:
            entry["mode"] = "lookup"
            entry["depends_on"] = (
                field.depends_on or field.depends_on_context
            )
        fields.append(entry)
    return fields


_SCALARS = (bool, int, float, str)


def _value_type(value: Any) -> Any:
    for scalar in _SCALARS:
        if isinstance(value, scalar):
            return scalar
    return Any


def _field_type(field: Any) -> Any:
    if field.choices is not None:
        return _value_type(field.choices[0][0])
    if field.range is not None:
        return int
    if field.lookup is not None:
        if field.lookup_ranges:
            return int
        first = next(iter(field.lookup.values()), None)
        return _value_type(first)
    return Any


def dynamic_model(entity: EntityKind, spec: SkeletonSpec | None) -> type[BaseModel]:
    """The P.3.1 dynamic model for *entity* (see the module docstring for
    the typing rule). ``model_fields`` names the typed on-disk fields;
    everything else rides through ``extra = allow``."""
    fields: dict[str, Any] = {}
    if spec is not None:
        for name, field in spec.fields.items():
            disk = entity.renames.get(name, name)
            if "." in disk or "[" in disk:
                continue
            typ = Any if disk in entity.code_fields else _field_type(field)
            fields[disk] = (typ | None, None)
    name = "".join(part.title() for part in entity.kind.split("_")) + "Row"
    return create_model(name, __config__=ConfigDict(extra="allow"), **fields)


def flatten_row(row: dict, containers: list[str] | tuple[str, ...]) -> dict[str, Any]:
    """Top-level scalars + every dict container's knobs, one flat namespace
    — what the off-table check and the anchored completion read
    (``update_db_row`` / ``complete_db_row`` precedent)."""
    flat: dict[str, Any] = {k: v for k, v in row.items() if not isinstance(v, dict)}
    for bucket in containers:
        value = row.get(bucket)
        if isinstance(value, dict):
            flat.update(value)
    return flat


def off_table_warnings(
    spec: SkeletonSpec | None,
    flat_after: dict[str, Any],
    changed: list[str],
    renames: dict[str, str] | None = None,
) -> list[str]:
    """Off-table values are allowed (hand edits are authoritative) but
    surfaced — generation would never roll them. Checked AFTER the whole edit
    lands so lookups resolve against the row's final state (editing size and
    hp together judges hp under the new size). Verbatim from
    ``update_db_row``; the leaf of a dotted name is the disk name.

    *renames* is the kind's ``EntityKind.renames`` (skeleton roll name →
    on-disk name). ``db evolve`` moves the DISK name and leaves the skeleton
    on its roll name, so the leaf is translated back before the table lookup
    — without it every renamed field (every dungeon seed rename, every
    evolved field) silently loses its roll-table warnings."""
    warnings: list[str] = []
    if spec is None:
        return warnings
    to_disk = {skel: disk.rsplit(".", 1)[-1] for skel, disk in (renames or {}).items()}
    to_roll = {disk: skel for skel, disk in to_disk.items()}
    for changed_name in changed:
        leaf = changed_name.rsplit(".", 1)[-1]
        field = spec.fields.get(to_roll.get(leaf, leaf))
        value = flat_after.get(leaf)
        if field is None or value is None:
            continue
        try:
            if field.choices is not None:
                allowed = [v for v, _ in field.choices]
                if value not in allowed:
                    warnings.append(
                        f"{leaf}={value!r} is outside the roll table "
                        f"({allowed}) — kept as a hand edit"
                    )
            elif field.range is not None:
                if not (field.range[0] <= value <= field.range[1]):
                    warnings.append(
                        f"{leaf}={value!r} is outside the rolled range "
                        f"{list(field.range)} — kept as a hand edit"
                    )
            elif field.lookup is not None and field.depends_on:
                dep_val = flat_after.get(to_disk.get(field.depends_on, field.depends_on))
                entry = field.lookup.get(dep_val)
                if entry is None:
                    warnings.append(
                        f"{leaf}={value!r}: no roll-table row for "
                        f"{field.depends_on}={dep_val!r} — kept as a hand edit"
                    )
                elif (
                    field.lookup_ranges
                    and isinstance(entry, (list, tuple)) and len(entry) == 2
                ):
                    if not (entry[0] <= value <= entry[1]):
                        warnings.append(
                            f"{leaf}={value!r} is outside the rolled band "
                            f"{list(entry)} for {field.depends_on}={dep_val!r} "
                            "— kept as a hand edit"
                        )
                elif not field.lookup_ranges and entry != value:
                    warnings.append(
                        f"{leaf}={value!r} differs from the table's {entry!r} "
                        f"for {field.depends_on}={dep_val!r} — kept as a hand edit"
                    )
        except TypeError:
            warnings.append(
                f"{leaf}={value!r} has a different type than the roll "
                "table — kept as a hand edit"
            )
    return warnings


# ---------------------------------------------------------------------------
# Fail-closed collection checks (P.3.1)
# ---------------------------------------------------------------------------


def check_collection_shape(entity: EntityKind, data: Any) -> None:
    """The collection re-parses in its ``layout.format``."""
    fmt = (entity.layout or {}).get("format")
    if fmt == "keyed_object":
        if not isinstance(data, dict) or not all(isinstance(v, dict) for v in data.values()):
            raise ValueError(f"{entity.layout.get('path')}: a keyed_object collection must be an object of row objects")
    elif fmt in ("array", "array_positional"):
        if not isinstance(data, list) or not all(isinstance(v, dict) for v in data):
            raise ValueError(f"{entity.layout.get('path')}: an {fmt} collection must be an array of row objects")
    else:
        raise ValueError(f"unknown layout format {fmt!r} for kind {entity.kind!r}")


def check_ids_unique(entity: EntityKind, data: Any) -> None:
    """``id_field`` values stay unique (keyed objects: key == row id when
    the row carries one)."""
    seen: dict[str, int] = {}
    rows = list(data.values()) if isinstance(data, dict) else list(data)
    keys = list(data.keys()) if isinstance(data, dict) else [None] * len(rows)
    for key, row in zip(keys, rows, strict=True):
        if not isinstance(row, dict):
            continue
        value = row.get(entity.id_field, key)
        if value is None:
            continue
        if key is not None and entity.id_field in row and str(row[entity.id_field]) != str(key):
            raise ValueError(
                f"{entity.kind} row keyed {key!r} carries {entity.id_field}={row[entity.id_field]!r} "
                "— the key and the id must agree"
            )
        seen[str(value)] = seen.get(str(value), 0) + 1
    duplicates = sorted(k for k, n in seen.items() if n > 1)
    if duplicates:
        raise ValueError(f"{entity.kind} {entity.id_field} values must be unique — duplicated: {duplicates}")


def _ref_values(row: dict, path: str) -> list[Any]:
    """Every value at a ``refs`` path — ``quest_id``, ``reward.item_id``,
    ``shop_inventory[].item_id`` (each item's key), ``monster_ids[]`` (each
    element)."""
    if "[]" not in path:
        present, value = get_path(row, path)
        return [value] if present else []
    head, _, tail = path.partition("[]")
    present, container = get_path(row, head) if head else (True, row)
    if not present or not isinstance(container, list):
        return []
    if not tail:
        return list(container)
    tail = tail.lstrip(".")
    out: list[Any] = []
    for item in container:
        if isinstance(item, dict):
            present, value = get_path(item, tail)
            if present:
                out.append(value)
    return out


def _touches(path: str, changed: list[str]) -> bool:
    """Did any changed address land inside the ref path's family?"""
    family = path.replace("[]", "")
    for name in changed:
        plain = ".".join(seg for seg, _ in parse_address(name))
        if plain == family or plain.startswith(family + ".") or family.startswith(plain + "."):
            return True
    return False


def check_refs(
    pack: str | Path,
    spec: PackSpec,
    entity: EntityKind,
    row: dict,
    changed: list[str],
) -> list[str]:
    """Every ``refs`` path resolves against the pack. Returns the WARNINGS
    for dangling refs the write did not touch (legacy data); RAISES for a
    dangling ref the write introduced (a path in *changed*). ``None`` /
    absent values never count."""
    from canon.packs.rows import load_per_file_rows, load_rows

    warnings: list[str] = []
    cache: dict[str, set[str]] = {}
    for path, target in entity.refs.items():
        kind, _, _id_field = target.partition(".")
        other = spec.entities.get(kind)
        if other is None:
            warnings.append(f"refs {path!r} → {target!r}: no kind {kind!r} in this pack — unchecked")
            continue
        if kind not in cache:
            try:
                if (other.layout or {}).get("mode") == "collection":
                    cache[kind] = set(load_rows(pack, other))
                else:
                    cache[kind] = set(load_per_file_rows(pack, other))
            except ValueError as exc:
                warnings.append(f"refs {path!r} → {target!r}: {exc} — unchecked")
                cache[kind] = set()
                continue
        known = cache[kind]
        dangling = [v for v in _ref_values(row, path) if v is not None and str(v) not in known]
        if not dangling:
            continue
        message = f"{path}={dangling[0]!r} does not resolve to a {kind} row ({target})"
        if _touches(path, changed):
            raise ValueError(message)
        warnings.append(message + " — pre-existing, left as is")
    return warnings

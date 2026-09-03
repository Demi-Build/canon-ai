"""``canon world update`` — world/bible-level fields on the reusable
protected-wall write core (master §3.0-A; P0 paper P.7.1–P.7.3; P.9 R13;
row P0-6).

The field table is DATA: ``PackSpec.world_fields`` (stamped, so a project's
registry may grow it) — dotted key → ``{file, path, mirrors[]}``. The core
resolves the key there, writes ``file.path``, then each mirror in the same
batch, one journal event per file: the primary carries ``detail.kind:
world_update`` on artifact ``world``; a mirror carries ``mirror_of: "world"``
on its own artifact (``manifest`` / ``story`` / ``narrative``, P.7.3).

Address grammar (P.7.1): segments are dotted; ``<list>[<key>=<value>]``
selects the one list item whose ``<key>`` equals ``<value>`` (``story.beats``
keyed by ``room_id``); a numeric index is NEVER accepted for world fields;
a table key with a ``<room_id>``-style placeholder is a template expanded
against the pack's rooms at resolve time (an unknown room refuses).
Container vs leaf (P.7.2): a dict-valued field is refused whole and written
knob-wise (``unlock_rules.type``); a list of scalars is a leaf replaced
wholesale, journaled as one ``{from, to}``.

The wall is a PARAMETER of the core (P.7.2): ``update_world`` passes
``WORLD_WALL`` (the union below) and the pack's table; Phase 2's ``tune
set`` passes its own wall admitting ``movement/rules/combat`` — those keys
are not in this table by decision. ``set_world_title`` is R13: ``world new
--name`` routes through here, closing the un-journaled ``_set_world_name``
bypass (deleted from ``cli/main.py``).

Deliberately absent, by row ownership: ``manifest.movement/combat/rules``
(W2.1 ``tune set``); stage-level fields (P.9 R11); room-level fields (``db
update --type room``); the world-map edit (``world map-edit``, its own verb).
"""

from __future__ import annotations

import re
from collections import OrderedDict
from collections.abc import Collection
from pathlib import Path
from typing import Any

from canon.packs import resolve_pack
from canon.packs.rows import read_json
from canon.write_core import check_wall, get_path, parse_address, set_path, write_document

__all__ = ["ARTIFACT_IDS", "WORLD_WALL", "resolve_world_field", "set_world_title", "title_field", "update_world"]

#: P.7.2 — the union across pack types, matched on the last dotted segment.
WORLD_WALL: frozenset[str] = frozenset({
    # identity
    "artifact_id", "seed", "story_seed", "world_id", "game", "pack_type", "faction_id",
    "primary_antagonist_faction_id", "room_id", "stage_ids", "edges",
    # provenance
    "provenance_hash", "parents", "status", "review_status", "generated_at",
    "validation_report", "generation_stats", "warnings", "canon_version",
    # generation-owned / derived
    "stages", "levels", "world_map", "enemies", "items", "rooms", "num_rooms", "environments",
    "environment_names", "portraits_generated", "player_classes", "entity_index", "story_npcs",
    "story_items", "story_monsters", "maze_width", "maze_height",
    # engine-owned
    "game_mode", "movement", "rules", "combat", "tiles", "graphics", "variants", "palettes",
    # asset plumbing
    "splash", "splash_path", "splash_hash", "music", "sfx", "audio", "props",
    # other-verb-owned
    "map_nodes", "map_edges", "map_locked",
})

#: written file → artifact id (P.7.3). Unlisted files use their stem.
ARTIFACT_IDS: dict[str, str] = {
    "world.json": "world",
    "world_bible.json": "world",
    "manifest.json": "manifest",
    "story/story.json": "story",
    "narrative.json": "narrative",
}

_PLACEHOLDER = re.compile(r"<(\w+)>")


def _wall_hit(name: str, wall: Collection[str]) -> bool:
    """P.7.2's ``*_count`` glob beside the exact leaf set."""
    leaf = name.rsplit(".", 1)[-1].split("[", 1)[0]
    return leaf in wall or leaf.endswith("_count")


def _room_ids(pack: Path, spec: Any) -> set[str]:
    """Every room id the pack knows: the room kind's collection index, the
    grid's per-room files (legacy trees), the bible's ``rooms`` map."""
    ids: set[str] = set()
    room = spec.entities.get("room")
    if room is not None and (room.layout or {}).get("mode") == "collection":
        data = read_json(pack / str(room.layout.get("path", "")))
        if isinstance(data, dict):
            ids.update(str(k) for k in data)
        elif isinstance(data, list):
            ids.update(str(r.get(room.id_field)) for r in data if isinstance(r, dict))
    for grid in spec.grids.values():
        template = grid.path_template
        if "{" in template:
            pattern = re.sub(r"\{[^}]+\}", "*", template)
            head = template.split("{", 1)[0]
            for path in pack.glob(pattern):
                rel = str(path.relative_to(pack))[len(head):]
                ids.add(rel.split("/", 1)[0])
    bible = read_json(pack / "world_bible.json")
    if isinstance(bible, dict) and isinstance(bible.get("rooms"), dict):
        ids.update(str(k) for k in bible["rooms"])
    return ids


def _refuse_numeric(name: str) -> None:
    for seg, brackets in parse_address(name):
        if seg.isdigit() or any(b.isdigit() for b in brackets):
            raise ValueError(
                f"{name!r}: a numeric index is never accepted for world fields — address list items by key "
                "(<list>[<key>=<value>])"
            )


def resolve_world_field(
    table: dict[str, dict], name: str, rooms: set[str]
) -> tuple[str, dict[str, Any], str]:
    """``(table key, entry with placeholders substituted, sub-path)`` for the
    user's dotted *name*: an exact key, the longest key prefix (``unlock_rules``
    for ``unlock_rules.type``), or a placeholder template
    (``story.beats.<room_id>.summary``) whose captured values must be known
    rooms. Unknown → ``ValueError`` listing the table."""
    _refuse_numeric(name)
    if name in table:
        return name, dict(table[name]), ""
    prefix = max((k for k in table if "<" not in k and name.startswith(k + ".")), key=len, default=None)
    if prefix is not None:
        return prefix, dict(table[prefix]), name[len(prefix) + 1:]
    for key, entry in table.items():
        if "<" not in key:
            continue
        pattern = re.escape(key)
        for placeholder in _PLACEHOLDER.findall(key):
            pattern = pattern.replace(re.escape(f"<{placeholder}>"), f"(?P<{placeholder}>[^.\\[\\]]+)")
        match = re.match(f"^{pattern}(?:\\.(?P<sub>.+))?$", name)
        if match is None:
            continue
        values = {k: v for k, v in match.groupdict().items() if k != "sub"}
        for placeholder, value in values.items():
            if placeholder == "room_id" and value not in rooms:
                raise ValueError(f"{name!r}: unknown room {value!r} (known: {sorted(rooms)})")

        def substitute(text: str) -> str:
            for placeholder, value in values.items():
                text = text.replace(f"<{placeholder}>", value)
            return text

        resolved = {
            "file": entry["file"],
            "path": substitute(entry["path"]),
            "mirrors": [{"file": m["file"], "path": substitute(m["path"])} for m in entry.get("mirrors", [])],
        }
        return key, resolved, match.group("sub") or ""
    raise ValueError(f"unknown world field {name!r} — one of {sorted(table)}")


def _validator_for(pack_type: str, file: str):
    """The primary file's fail-closed check: the platformer's ``World`` model
    for ``world.json``; a structural check elsewhere (the dungeon bible has
    no Pydantic model — its story dict is the engine's file)."""
    if file == "world.json":
        from canon.bible.platformer import World

        def validate(doc: Any, _diff: dict) -> None:
            World.model_validate(doc)

        return validate

    def structural(doc: Any, _diff: dict) -> None:
        if not isinstance(doc, dict):
            raise ValueError(f"{file}: expected a JSON object")

    return structural


def update_world(
    pack_dir: str | Path,
    changes: dict[str, Any],
    *,
    actor: str = "user",
    session: str | None = None,
    wall: Collection[str] = WORLD_WALL,
    field_table: dict[str, dict] | None = None,
    user_edited: bool | None = None,
) -> dict[str, Any]:
    """Apply world-level edits per the pack's field table (see the module
    docstring). Returns ``{changed, files[], warnings, no_change}`` — one
    ``files`` entry per written file with its artifact id and hashes.

    *user_edited* is the core's stamp parameter: ``None`` (the default, a
    human correcting generated content) stamps ``status: user_edited`` where
    the document carries a status; ``False`` writes the field and journals it
    without the stamp — what ``set_world_title`` passes, because a generator
    naming the world it just made is not a human correction."""
    if not isinstance(changes, dict) or not changes:
        raise ValueError("--set needs a non-empty JSON object of field: value")
    pack = Path(pack_dir)
    resolved = resolve_pack(pack)
    spec = resolved.spec
    table = field_table if field_table is not None else spec.world_fields
    if not table:
        raise ValueError(f"pack type {resolved.pack_type!r} declares no world fields")
    rooms = _room_ids(pack, spec)

    # resolve → wall, per change, before any file is touched
    per_file: OrderedDict[str, dict[str, tuple[str, Any]]] = OrderedDict()
    mirror_of: dict[str, str] = {}
    documents: dict[str, Any] = {}
    warnings: list[str] = []
    for name, value in changes.items():
        if _wall_hit(name, wall):
            raise ValueError(f"{name!r} is protected (identity / provenance / generation-owned / engine-owned — P.7.2)")
        check_wall(name, wall=wall)
        _key, entry, sub = resolve_world_field(table, name, rooms)
        primary_file = entry["file"]
        if primary_file not in documents:
            documents[primary_file] = read_json(pack / primary_file)
            if documents[primary_file] is None:
                raise FileNotFoundError(f"{primary_file} not found in {pack}")
        path = entry["path"] + (f".{sub}" if sub else "")
        present, current = get_path(documents[primary_file], entry["path"])
        if not sub and present and isinstance(current, dict):
            raise ValueError(f"{name!r} is a container — write its knobs individually ('{name}.<key>')")
        if sub and present and not isinstance(current, dict):
            raise ValueError(f"{name!r}: {_key!r} is a leaf, not a container")
        per_file.setdefault(primary_file, {})[name] = (path, value)
        for mirror in entry.get("mirrors", []):
            mirror_file = mirror["file"]
            if mirror_file not in documents:
                documents[mirror_file] = read_json(pack / mirror_file)
            if documents[mirror_file] is None:
                warnings.append(f"mirror {mirror_file} is absent — {name!r} written to {primary_file} only")
                continue
            mirror_path = mirror["path"] + (f".{sub}" if sub else "")
            per_file.setdefault(mirror_file, {})[name] = (mirror_path, value)
            mirror_of.setdefault(mirror_file, "world")

    files: list[dict[str, Any]] = []
    changed: dict[str, dict] = {}
    for file, addressed in per_file.items():
        def apply(doc: Any, items: dict[str, Any], _addressed=addressed) -> dict[str, dict]:
            diff: dict[str, dict] = {}
            for name, (path, value) in _addressed.items():
                old, new = set_path(doc, path, value)
                if old != new:
                    diff[name] = {"from": old, "to": new}
            return diff

        detail: dict[str, Any] = {"kind": "world_update"}
        if file in mirror_of:
            detail["mirror_of"] = mirror_of[file]
        artifact_id = ARTIFACT_IDS.get(file, Path(file).stem)
        result = write_document(
            pack,
            artifact_id=artifact_id,
            rel_path=file,
            document=documents[file],
            changes={name: value for name, (_p, value) in addressed.items()},
            apply=apply,
            validate=_validator_for(resolved.pack_type, file) if file not in mirror_of else None,
            user_edited=user_edited,
            actor=actor,
            session=session,
            detail=detail,
        )
        if result.get("no_change"):
            continue
        files.append({
            "file": file, "artifact_id": artifact_id, "changed": result["changed"],
            "before_hash": result["before_hash"], "after_hash": result["after_hash"],
            **({"mirror_of": mirror_of[file]} if file in mirror_of else {}),
        })
        if file not in mirror_of:
            changed.update(result["changed"])
    return {
        "pack_type": resolved.pack_type,
        "changed": changed,
        "files": files,
        "warnings": warnings,
        "no_change": not files,
    }


def title_field(spec: Any) -> str | None:
    """Which ``world_fields`` key holds the world's display title, per
    template — DATA, never a branch on the pack type (row P0-10, when
    ``world new`` began dispatching through the registry and a dungeon
    create had to be nameable too).

    The convention the field table already follows: the entry named exactly
    ``title``, else the single one whose last dotted segment is ``title``
    (the platformer's ``title``, the dungeon's ``story.title``). ``None``
    when a template declares no title field — the caller says so rather than
    guessing (doctrine 4)."""
    table = getattr(spec, "world_fields", None) or {}
    if "title" in table:
        return "title"
    candidates = [key for key in table if key.rsplit(".", 1)[-1] == "title"]
    return candidates[0] if len(candidates) == 1 else None


def set_world_title(
    pack_dir: str | Path, name: str, *, actor: str = "user", session: str | None = None
) -> dict[str, Any]:
    """R13: ``world new --name`` — the title through the journaled core.

    ``user_edited=False``: naming the world at create is the generator
    finishing its job, so ``world.json`` stays ``status: pending``. Stamping
    it here would be an emitted-tree delta R14 never sanctioned AND would
    label every freshly generated world as human-corrected, inverting the
    (generated → human-corrected) signal the journal exists to collect. An
    explicit ``world update`` keeps the stamp.

    Row P0-10: the field is resolved per template (``title_field``) so the
    same call names a platformer (``title`` → ``world.json``) and a dungeon
    (``story.title`` → ``world_bible.json`` + its two mirrors)."""
    spec = resolve_pack(pack_dir).spec
    field = title_field(spec)
    if field is None:
        raise ValueError(
            f"pack type {spec.pack_type!r} declares no world title field in world_fields"
        )
    return update_world(pack_dir, {field: name}, actor=actor, session=session, user_edited=False)

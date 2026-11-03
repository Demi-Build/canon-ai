"""The ask-tier write tools (Phase 1 A4; master §3.1 stage 4; Phase 1 §4.B).

``register_write_tools(registry, pack_dir, *, actor_for)`` registers the
§4.B ask-tier rows into row A2's ``ToolRegistry`` — every tool tier
``"ask"`` (a permission chip first; project-scoped Always-allow through the
engine), each a THIN in-process wrapper over the canon verb that already
exists (D3: imports, not subprocesses; doctrine 2: nothing here writes a
byte itself — the verbs carry doctrine 1's discipline: resolve → protected
wall → fail-closed validate → warnings → ``user_edited`` stamp → journal →
CAS snapshot). Inputs are validated against the JSON schema the model
sees (row A3's ``validate_input``); results are compact JSON strings —
the verb's own document plus ``journal``: the events the call appended
(artifact id, op, actor, session, before/after hashes), which is what
"undo this" and the ``restore`` tool key on.

What each tool extends:

- ``apply_level_edit`` → ``canon.adapters.platformer_write.apply_level_edit``
  (sparse placement/field edits; ``canon level apply-edit``).
- ``import_level_grids`` → ``import_level_grids`` (the painted collision
  grid; terrain/background/hazards re-derive; ``canon level import-grids``).
- ``create_level`` → ``create_level``; ``publish_level`` → ``publish_level``.
- ``edit_world_map`` → ``apply_world_map_edit`` (``canon world map``).
- ``update_row`` → ``canon.packs.platformer.ops.update_db_row``;
  ``update_schema`` → ``ops.update_db_schema`` (row P0-6 is extracting
  their bodies to the write core; these names stay as its wrappers).
- ``pin`` / ``unpin`` → the ``canon pin`` / ``canon unpin`` semantics on
  the pack's ``bible.json`` (``pinned_ids`` / ``pinnable_ids`` from the
  orchestrator; atomic reject of unpinnable ids; a pin clears a stale
  mark). The CLI's bodies are not importable functions, so the in-process
  form lives here — one pair, mirrored line for line; pins journal nothing
  on either surface (bible metadata, not an artifact version).
- ``restore`` → ``restore_level_step`` for ``level:<stage>/<level>/<step>``,
  ``restore_asset`` for ``enemy:`` / ``item:`` / ``player`` / ``tilesheet:``
  / ``backdrop:`` (``canon level restore`` / ``canon asset restore``).

Grid tools dispatch by the pack's GridKind through row A3's OWN resolver
(``tools_read.grid_verb_or_not_yet`` over a module-local writer table —
one resolution rule, so P0-8's ``room`` writers widen one function): the
platformer ``level`` kind has writers; a dungeon ``room`` answers the
structured "not yet" naming row P0-8, exactly as the CLI does.

Attribution: every verb is called with ``actor=<agent actor>`` and
``session=<conversation>`` read from ``actor_for()`` at call time — the
service binds ``canon.agent.actors.bind_call`` around ``registry.execute``
and passes ``current_call`` here, so the actor string is built by
``agent_actor`` alone (I6) and the registry (A2) stays unchanged. The
``journal`` a result carries is collected by ``journal_window``, which
holds the pack's attribution lock across the verb: the pack journal is a
global append log, and a plain index slice would hand this call another
concurrent call's artifacts and hashes (the very hashes ``restore``
keys on).

Row P1-A7.5 widens exactly one thing here: ``restore`` gained the
``code:<engine-copy path>`` family (``canon.engine_ops.restore_code_file``),
so an ``edit_project_code`` change undoes through the restore path every
other write already uses rather than through a second verb.

Deliberately absent, by row ownership: the paid tools and the estimate
(A6), ``edit_project_code`` / ``engine_status`` / ``engine_sync``
(``tools_code``, row A7.5), ``sandbox_level`` / ``play_*`` (A4.5 / W2.0),
the chip UI (A5), specialist threading beyond the foreman (A4.5). Tiers are
data; nothing here prices anything.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from canon.agent.actors import CallContext, current_call
from canon.agent.registry import Tool, ToolRegistry
from canon.agent.tools_read import compact, grid_verb_or_not_yet, validate_input
from canon.llm.chat import ToolSpec

#: The tier every tool here registers under — a chip first.
WRITE_TIER = "ask"

#: The tools in registration order (= the order every request offers them, after the reads).
WRITE_TOOL_NAMES: tuple[str, ...] = (
    "apply_level_edit",
    "import_level_grids",
    "create_level",
    "publish_level",
    "edit_world_map",
    "update_row",
    "update_schema",
    "pin",
    "unpin",
    "restore",
)

#: GridKind id → the level writers (``canon level apply-edit`` & co.); rooms at P0-8.
GRID_EDITORS: dict[str, str] = {"level": "canon.adapters.platformer_write:apply_level_edit"}
GRID_IMPORTERS: dict[str, str] = {"level": "canon.adapters.platformer_write:import_level_grids"}
GRID_CREATORS: dict[str, str] = {"level": "canon.adapters.platformer_write:create_level"}
GRID_PUBLISHERS: dict[str, str] = {"level": "canon.adapters.platformer_write:publish_level"}
GRID_RESTORERS: dict[str, str] = {"level": "canon.adapters.platformer_write:restore_level_step"}

#: Restore targets that route to ``restore_asset`` (its own target grammar).
_ASSET_RESTORE_KINDS = ("enemy", "item", "player", "tilesheet", "backdrop")

#: Row A7.5's restore family: ``code:<engine-copy path>`` →
#: ``canon.engine_ops.restore_code_file`` (the same journal + CAS path; it
#: also clears the ``modified`` stamp when the bytes are canon's again).
CODE_RESTORE_KIND = "code"

#: Level steps ``restore`` can rewind (``platformer_write._RESTORABLE``, restated for the schema text).
RESTORABLE_STEPS = ("entities", "items", "triggers", "hazards", "foreground")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _grid_verb_or_not_yet(pack: Path, table: dict[str, str], what: str) -> Callable[..., Any]:
    """Row A3's ``grid_verb_or_not_yet`` without the kind — ONE resolution
    rule for both tool modules, so row P0-8's ``room`` writers widen one
    function (``tools_read``'s), never two copies."""
    _, verb = grid_verb_or_not_yet(pack, table, what)
    return verb


def _compact_event(event: dict) -> dict:
    detail = event.get("detail") or {}
    out: dict[str, Any] = {
        "artifact_id": event.get("artifact_id"),
        "op": event.get("op"),
        "actor": event.get("actor"),
        "kind": detail.get("kind") if isinstance(detail, dict) else None,
    }
    # Row P1-A6 (additive): the lane + money fields the transcript's write
    # card, the last-change chip and History render (P.8.7's cradle read
    # side). ``gen`` stays out — the compact view is a handle, not an audit
    # record; the journal itself is the audit record.
    for key in ("ts", "session", "before_hash", "after_hash",
                "identity", "costCents", "accuracy", "genKind", "batchId"):
        if event.get(key) is not None:
            out[key] = event[key]
    return out


def compact_events(events: list[dict]) -> list[dict]:
    """The transcript-facing view of a call's journal events (row A4's shape,
    widened by row A6's lane fields). Public so the paid tools render the same
    handles the write tools do."""
    return [_compact_event(e) for e in events]


#: One lock per pack (by resolved path) around the journal-attribution window.
_JOURNAL_LOCKS: dict[str, threading.RLock] = {}
_JOURNAL_LOCKS_GUARD = threading.Lock()


def journal_lock(pack: Path) -> threading.RLock:
    """The pack's attribution lock (created on first use). Re-entrant: a
    verb that runs another write verb inside itself takes it once."""
    key = str(Path(pack).resolve())
    with _JOURNAL_LOCKS_GUARD:
        lock = _JOURNAL_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _JOURNAL_LOCKS[key] = lock
        return lock


@contextmanager
def journal_window(pack: Path) -> Iterator[list[dict]]:
    """Run a write and collect EXACTLY the journal events it appended.

    The journal is a pack-global append log, so a bare index slice around
    a call ("everything appended while I ran") claims whatever a
    concurrent call wrote — two conversations on one pack, or two parallel
    delegations of one specialist, which the run manager schedules by
    design. Holding the pack's ``journal_lock`` across the verb makes the
    slice exact: the only writes inside the window are this call's. The
    lock wraps the VERB only — never the permission round-trip (the chip
    is decided before the tool body runs), so an undecided chip never
    blocks another pack write.

    The events also land on the bound ``CallContext.journal`` (A4's
    attribution seam), which is where the run manager reads a run's
    ``artifacts_touched`` from instead of re-slicing the log.
    """
    from canon.provenance import all_events

    events: list[dict] = []
    with journal_lock(pack):
        before = len(all_events(pack))
        try:
            yield events
        finally:
            events.extend(all_events(pack)[before:])
            try:
                current_call().journal.extend(events)
            except LookupError:
                pass  # a verb run outside a turn (the CLI) has no sink


def with_journal(pack: Path, run: Callable[[], dict]) -> dict:
    """Run a verb and attach the journal events it appended as
    ``journal`` — artifact ids, ops, actor/session and before/after hashes,
    the undo handles the transcript's write card needs. Attribution is
    ``journal_window``'s: this call's events, never a concurrent call's."""
    with journal_window(pack) as events:
        result = dict(run())
    return {**result, "journal": [_compact_event(e) for e in events]}


def _bible(pack: Path):
    from canon.bible.models import Bible

    path = pack / "bible.json"
    if not path.is_file():
        raise FileNotFoundError(
            "no bible.json in this pack — pins protect bible-tracked art artifacts, and a pack generated "
            "without --orchestrate carries no bible (nothing here is pinnable)"
        )
    return path, Bible.load(path)


# ---------------------------------------------------------------------------
# The tool bodies — each ``(pack, input, call) -> JSON-able``
# ---------------------------------------------------------------------------


def apply_level_edit(pack: Path, tool_input: dict, call: CallContext) -> dict:
    verb = _grid_verb_or_not_yet(pack, GRID_EDITORS, "apply_level_edit")
    level_id, edit = tool_input["level_id"], tool_input["sparse_edits"]
    return with_journal(pack, lambda: verb(pack, level_id, edit, actor=call.actor, session=call.conversation))


def import_level_grids(pack: Path, tool_input: dict, call: CallContext) -> dict:
    verb = _grid_verb_or_not_yet(pack, GRID_IMPORTERS, "import_level_grids")
    level_id, rows = tool_input["level_id"], tool_input["layers"]["collision"]
    return with_journal(pack, lambda: verb(pack, level_id, rows, actor=call.actor, session=call.conversation))


def create_level(pack: Path, tool_input: dict, call: CallContext) -> dict:
    verb = _grid_verb_or_not_yet(pack, GRID_CREATORS, "create_level")
    params = tool_input["params"]
    return with_journal(
        pack,
        lambda: verb(
            pack,
            params["stage_id"],
            params.get("width", 60),
            params.get("height", 16),
            params.get("level_id"),
            actor=call.actor,
            session=call.conversation,
        ),
    )


def publish_level(pack: Path, tool_input: dict, call: CallContext) -> dict:
    verb = _grid_verb_or_not_yet(pack, GRID_PUBLISHERS, "publish_level")
    return with_journal(
        pack,
        lambda: verb(
            pack,
            tool_input["level_id"],
            tool_input.get("position"),
            bool(tool_input.get("remove", False)),
            actor=call.actor,
            session=call.conversation,
        ),
    )


def edit_world_map(pack: Path, tool_input: dict, call: CallContext) -> dict:
    from canon.adapters.platformer_write import apply_world_map_edit

    edits = tool_input["edits"]
    return with_journal(pack, lambda: apply_world_map_edit(pack, edits, actor=call.actor, session=call.conversation))


def update_row(pack: Path, tool_input: dict, call: CallContext) -> dict:
    from canon.packs.platformer.ops import update_db_row

    kind, row_id, fields = tool_input["type"], tool_input["id"], tool_input["fields"]
    if not fields:
        raise ValueError("update_row: fields must be a non-empty object of field: value")
    return with_journal(
        pack, lambda: update_db_row(pack, kind, row_id, fields, actor=call.actor, session=call.conversation)
    )


def update_schema(pack: Path, tool_input: dict, call: CallContext) -> dict:
    from canon.packs.platformer.ops import update_db_schema

    kind, changes = tool_input["type"], tool_input["changes"]
    return with_journal(
        pack, lambda: update_db_schema(pack, kind, changes, actor=call.actor, session=call.conversation)
    )


def pin(pack: Path, tool_input: dict, _call: CallContext) -> dict:
    """``canon pin`` in process: atomic reject of unpinnable ids, then pin
    and clear any stale mark (a stale mark would reschedule the owning
    phase and defeat the pin)."""
    from canon.bible.artifacts import ArtifactStatus
    from canon.pipeline.orchestrator import pinnable_ids, pinned_ids

    ids = list(tool_input["ids"])
    path, bible = _bible(pack)
    pinned, pinnable = pinned_ids(bible), pinnable_ids(bible)
    unknown = sorted(set(ids) - pinnable)
    if unknown:
        raise ValueError(
            f"not pinnable: {unknown} — pinnable ids are the hash-tracked art artifacts: {sorted(pinnable)}"
        )
    added = sorted(set(ids) - pinned)
    already = sorted(set(ids) & pinned)
    stale_cleared: list[str] = []
    status = bible.metadata.node_status
    for artifact_id in added:
        if status.get(artifact_id) is ArtifactStatus.STALE:
            status[artifact_id] = ArtifactStatus.DONE
            stale_cleared.append(artifact_id)
    bible.metadata.pinned = sorted(pinned | set(added))
    bible.persist(path)
    return {"result": "pinned", "pinned": added, "already_pinned": already, "stale_cleared": stale_cleared}


def unpin(pack: Path, tool_input: dict, _call: CallContext) -> dict:
    """``canon unpin`` in process: release pins (idempotent); status untouched."""
    from canon.pipeline.orchestrator import pinned_ids

    ids = list(tool_input["ids"])
    path, bible = _bible(pack)
    pinned = pinned_ids(bible)
    removed = sorted(pinned & set(ids))
    not_pinned = sorted(set(ids) - pinned)
    bible.metadata.pinned = sorted(pinned - set(removed))
    bible.persist(path)
    return {"result": "unpinned", "unpinned": removed, "not_pinned": not_pinned}


def restore(pack: Path, tool_input: dict, call: CallContext) -> dict:
    target, to_hash = tool_input["target"], tool_input["version_hash"]
    kind, _, rest = target.partition(":")
    if kind == "level":
        stage_id, _, tail = rest.partition("/")
        level_id, _, step = tail.partition("/")
        if not (stage_id and level_id and step):
            raise ValueError(
                f"level targets are level:<stage>/<level>/<step> (steps: {', '.join(RESTORABLE_STEPS)}); got {target!r}"
            )
        verb = _grid_verb_or_not_yet(pack, GRID_RESTORERS, "restore")
        return with_journal(
            pack, lambda: verb(pack, level_id, step, to_hash, actor=call.actor, session=call.conversation)
        )
    if kind in _ASSET_RESTORE_KINDS:
        from canon.adapters.platformer_write import restore_asset

        return with_journal(
            pack, lambda: restore_asset(pack, target, to_hash, actor=call.actor, session=call.conversation)
        )
    if kind == CODE_RESTORE_KIND:
        # Row A7.5's one-click restore: the SAME restore path, widened by one
        # target family rather than given a second verb. It reverts the engine
        # copy's file and clears its `modified` stamp when the bytes are
        # canon's again (so `engine sync` manages it once more).
        from canon.engine_ops import restore_code_file

        return with_journal(
            pack, lambda: restore_code_file(pack, target, to_hash, actor=call.actor, session=call.conversation)
        )
    raise ValueError(
        f"cannot restore {target!r}: targets are level:<stage>/<level>/<step>, enemy:<id>, item:<id>, player, "
        f"tilesheet:<stage>, backdrop:<stage>/<index>, {CODE_RESTORE_KIND}:<engine-copy path> — schema:<kind>, "
        "stage:<id> and world have no restore verb yet"
    )


# ---------------------------------------------------------------------------
# Chip copy: "‹Specialist› wants to ‹verb› ‹target›"
# ---------------------------------------------------------------------------


def _short_hash(value: str) -> str:
    return value if len(value) <= 19 else value[:19] + "…"


#: name → the "‹verb› ‹target›" the permission chip shows for an input.
TARGETS: dict[str, Callable[[dict], str]] = {
    "apply_level_edit": lambda i: f"edit level {i['level_id']} ({', '.join(sorted(i['sparse_edits']))})",
    "import_level_grids": lambda i: f"import grids into {i['level_id']}",
    "create_level": lambda i: f"create a level in stage {i['params']['stage_id']}",
    "publish_level": lambda i: f"{'unpublish' if i.get('remove') else 'publish'} {i['level_id']}",
    "edit_world_map": lambda i: "edit the world map",
    "update_row": lambda i: f"update {i['type']} {i['id']} ({', '.join(i['fields'])})",
    "update_schema": lambda i: f"change the {i['type']} schema",
    "pin": lambda i: f"pin {', '.join(i['ids'])}",
    "unpin": lambda i: f"unpin {', '.join(i['ids'])}",
    "restore": lambda i: f"restore {i['target']} to {_short_hash(i['version_hash'])}",
}


# ---------------------------------------------------------------------------
# Specs + registration
# ---------------------------------------------------------------------------

_LEVEL_ID = {"type": "string", "description": "Level id as describe_pack lists it, e.g. 'l1'."}
_XY = {"type": "integer", "minimum": 0}
_POINT = {"type": "array", "items": _XY, "minItems": 2, "maxItems": 2, "description": "[x, y] in cells."}
_MASK_ENTRY = {
    "type": "object",
    "properties": {"x": _XY, "y": _XY, "type": {"type": "string"}, "params": {"type": "object"}},
    "required": ["x", "y", "type"],
    "additionalProperties": False,
}
_SPARSE_EDITS = {
    "type": "object",
    "description": (
        "Any subset of the sparse layers; a layer given REPLACES that layer wholesale (send the full list). "
        "entities/items are placements, triggers/hazards/foreground are masks, spawn/exit are points, "
        "music_path/music_sections repoint the level's music."
    ),
    "properties": {
        "entities": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "enemy_id": {"type": "string"},
                    "x": _XY,
                    "y": _XY,
                    "variant": {"type": "string"},
                },
                "required": ["enemy_id", "x", "y"],
                "additionalProperties": False,
            },
        },
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"item_id": {"type": "string"}, "x": _XY, "y": _XY, "source": {"type": "string"}},
                "required": ["item_id", "x", "y"],
                "additionalProperties": False,
            },
        },
        "triggers": {"type": "array", "items": _MASK_ENTRY},
        "hazards": {"type": "array", "items": _MASK_ENTRY},
        "foreground": {"type": "array", "items": _MASK_ENTRY},
        "spawn": _POINT,
        "exit": _POINT,
        "music_path": {"type": "string"},
        "music_hash": {"type": "string"},
        "music_sections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "start": _XY,
                    "end": _XY,
                    "music_path": {"type": "string"},
                    "music_hash": {"type": "string"},
                    "name": {"type": "string"},
                },
                "required": ["start", "end"],
                "additionalProperties": False,
            },
        },
    },
    "additionalProperties": False,
}

#: name → (description for the model, input schema, body, touches)
_TOOLS: dict[str, tuple[str, dict, Callable[[Path, dict, CallContext], Any], str]] = {
    "apply_level_edit": (
        "Sparse edit of ONE level: enemy/item placements, trigger/hazard/foreground masks, spawn/exit, music. "
        "Each layer you pass replaces that layer in full — read it first (export_level) and send it back edited. "
        "Journals 'edit' per changed step with before/after hashes (the 'journal' in the result is what restore "
        "takes). validate_level afterwards; nothing rebuilds. Grid cells are import_level_grids' job.",
        {
            "type": "object",
            "properties": {"level_id": _LEVEL_ID, "sparse_edits": _SPARSE_EDITS},
            "required": ["level_id", "sparse_edits"],
            "additionalProperties": False,
        },
        apply_level_edit,
        "writes level/<stage>/<id>/{entities,items,triggers,hazards,foreground}.json + level.json; journals edit",
    ),
    "import_level_grids": (
        "Replace a level's collision grid with a painted one (rows of tile-type ints, row-major grids[y][x], at "
        "least 4x4; a different size RESIZES the level and clamps placements). Terrain, background and hazards "
        "re-derive from it — pass only 'collision'. Journals 'edit' on the collision step; validate_level after.",
        {
            "type": "object",
            "properties": {
                "level_id": _LEVEL_ID,
                "layers": {
                    "type": "object",
                    "properties": {
                        "collision": {
                            "type": "array",
                            "items": {"type": "array", "items": {"type": "integer"}, "minItems": 4},
                            "minItems": 4,
                        }
                    },
                    "required": ["collision"],
                    "additionalProperties": False,
                },
            },
            "required": ["level_id", "layers"],
            "additionalProperties": False,
        },
        import_level_grids,
        "writes level/<stage>/<id>/{collision,terrain,background}.npz (+.grid.json), hazards.json, level.json",
    ),
    "create_level": (
        "Scaffold a hand-built DRAFT level in a stage: flat floor, spawn left, exit right. It exists on disk "
        "(and in the editor) but is NOT in the playable progression until publish_level. Returns the new "
        "level_id (auto-assigned unless given).",
        {
            "type": "object",
            "properties": {
                "params": {
                    "type": "object",
                    "properties": {
                        "stage_id": {"type": "string", "description": "A stage id from describe_pack."},
                        "width": {"type": "integer", "minimum": 8},
                        "height": {"type": "integer", "minimum": 8},
                        "level_id": {"type": "string", "description": "Optional explicit id (must be new)."},
                    },
                    "required": ["stage_id"],
                    "additionalProperties": False,
                }
            },
            "required": ["params"],
            "additionalProperties": False,
        },
        create_level,
        "writes a new level/<stage>/<id>/ dir; journals create per step",
    ),
    "publish_level": (
        "Insert a level into (remove: true — take it out of) the playable progression. 'position' is 1-based "
        "within its stage (publishing at 2 makes it X-2 and renumbers the rest; omitted = last). Rewrites "
        "stage.json and the manifest's level order + world map; journals 'edit' on stage:<id>.",
        {
            "type": "object",
            "properties": {
                "level_id": _LEVEL_ID,
                "position": {"type": "integer", "minimum": 1},
                "remove": {"type": "boolean"},
            },
            "required": ["level_id"],
            "additionalProperties": False,
        },
        publish_level,
        "writes stage/<stage>/stage.json and manifest.json; journals edit",
    ),
    "edit_world_map": (
        "Hand-author the world map as overrides on world.json: 'nodes' {level_id: {pos: [x, y] in 0..1} | null "
        "(null hands the node back to the generator)}, 'edges' [{a, b, kind: path|lock|secret|…, condition?, "
        "stop?}] (replaces the edge list), 'locked' bool. No-op edits journal nothing.",
        {
            "type": "object",
            "properties": {
                "edits": {
                    "type": "object",
                    "properties": {
                        "nodes": {"type": "object"},
                        "edges": {"type": "array", "items": {"type": "object"}},
                        "locked": {"type": "boolean"},
                    },
                    "additionalProperties": False,
                }
            },
            "required": ["edits"],
            "additionalProperties": False,
        },
        edit_world_map,
        "writes world.json map_nodes/map_edges/map_locked; journals edit on world",
    ),
    "update_row": (
        "Direct edit of ONE database row (type from db_types, e.g. 'enemy'; id e.g. 'cinder_beetle'): 'fields' "
        "maps flat field names to new values — stat/behavior knobs route into their nested dicts, model fields "
        "land top-level, dotted paths ('stats.custom') reach hand-added knobs, null deletes a nested knob. "
        "Protected fields (identity, provenance, sprite plumbing) are refused by the verb. Values land "
        "verbatim (off-table values are kept and WARNED, never rerolled); the row is validated fail-closed, "
        "stamped user_edited and journaled 'edit' with the per-field diff.",
        {
            "type": "object",
            "properties": {
                "type": {"type": "string", "description": "A kind from db_types."},
                "id": {"type": "string", "description": "The row id."},
                "fields": {"type": "object", "description": "field: value changes (non-empty)."},
            },
            "required": ["type", "id", "fields"],
            "additionalProperties": False,
        },
        update_row,
        "writes <type>/<id>.json via canon db update; journals edit",
    ),
    "update_schema": (
        "Edit the roll tables bounding generation for one kind: 'changes' = {fields: {<name>: <field entry> | "
        "null}} — each named field is replaced WHOLESALE (null deletes it). Validated fail-closed (loader, "
        "lookup coverage, smoke roll) BEFORE anything is written; lands as the pack-local override "
        "schemas/<type>.json (the template default is never touched); journals 'edit' on schema:<type>.",
        {
            "type": "object",
            "properties": {
                "type": {"type": "string", "description": "A kind from db_types."},
                "changes": {
                    "type": "object",
                    "properties": {"fields": {"type": "object"}},
                    "required": ["fields"],
                    "additionalProperties": False,
                },
            },
            "required": ["type", "changes"],
            "additionalProperties": False,
        },
        update_schema,
        "writes schemas/<type>.json (pack-local override); journals edit on schema:<type>",
    ),
    "pin": (
        "Protect art artifacts from regeneration (canon pin): ids like tileset:<stage>, enemy:<id>, "
        "backdrop:<stage>, player. Pinned content is skipped by regen cascades AND art phases. Level steps "
        "are never pinnable (hand edits protect them). Atomic: one unpinnable id rejects the whole call.",
        {
            "type": "object",
            "properties": {"ids": {"type": "array", "items": {"type": "string"}, "minItems": 1}},
            "required": ["ids"],
            "additionalProperties": False,
        },
        pin,
        "writes bible.json metadata.pinned (no journal row — pins are metadata)",
    ),
    "unpin": (
        "Release pinned artifacts (canon unpin; idempotent). Status is untouched — nothing re-rolls until "
        "explicitly targeted.",
        {
            "type": "object",
            "properties": {"ids": {"type": "array", "items": {"type": "string"}, "minItems": 1}},
            "required": ["ids"],
            "additionalProperties": False,
        },
        unpin,
        "writes bible.json metadata.pinned (no journal row — pins are metadata)",
    ),
    "restore": (
        "Make a stored version current again — the undo. 'target' is the artifact id: "
        f"level:<stage>/<level>/<step> (steps: {', '.join(RESTORABLE_STEPS)}), enemy:<id>, item:<id> (row JSON or "
        "sprite PNG by the bytes), player, tilesheet:<stage>, backdrop:<stage>/<index>, "
        f"{CODE_RESTORE_KIND}:<engine-copy path> (an edit_project_code change; the modified stamp clears when the "
        "file is canon's again). 'version_hash' is a "
        "before_hash/after_hash from get_versions or a write's 'journal'. Nothing is deleted: restore writes a "
        "NEW version and journals 'restore'.",
        {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "The full artifact id."},
                "version_hash": {"type": "string", "description": "sha256:<hex> from get_versions."},
            },
            "required": ["target", "version_hash"],
            "additionalProperties": False,
        },
        restore,
        "writes the target's files from .canon/objects; journals restore",
    ),
}


def _bind(
    name: str,
    schema: dict,
    body: Callable[[Path, dict, CallContext], Any],
    pack: Path,
    actor_for: Callable[[], CallContext],
) -> Callable[[dict], str]:
    def run(tool_input: dict) -> str:
        validate_input(name, schema, tool_input)
        return compact(body(pack, tool_input, actor_for()))

    run.__name__ = name
    return run


def write_tool_specs() -> list[ToolSpec]:
    """The specs alone (what the eval corpus and the panel's tool list show)."""
    return [ToolSpec(name=name, description=desc, input_schema=schema) for name, (desc, schema, _, _) in _TOOLS.items()]


def register_write_tools(
    registry: ToolRegistry,
    pack_dir: str | Path,
    *,
    actor_for: Callable[[], CallContext],
) -> list[str]:
    """Register every write tool for ``pack_dir`` into ``registry`` (tier
    ``"ask"``, ``WRITE_TOOL_NAMES`` order) and return the names.

    ``actor_for`` is called at EVERY tool run for the ``CallContext`` —
    ``(actor, conversation)`` — the verb is attributed to
    (``canon.agent.actors.current_call`` in the service; a test passes a
    lambda). The chip describers register on the registry's engine so the
    ``permission_request`` event can say "‹verb› ‹target›". Nothing is read
    at registration.
    """
    pack = Path(pack_dir)
    names: list[str] = []
    engine = registry.permissions
    for name in WRITE_TOOL_NAMES:
        description, schema, body, touches = _TOOLS[name]
        spec = ToolSpec(name=name, description=description, input_schema=schema)
        registry.register(
            Tool(spec=spec, tier=WRITE_TIER, run=_bind(name, schema, body, pack, actor_for), touches=touches)
        )
        if hasattr(engine, "describe"):
            engine.describe(name, TARGETS[name])
        names.append(name)
    return names


__all__ = [
    "CODE_RESTORE_KIND",
    "GRID_CREATORS",
    "GRID_EDITORS",
    "GRID_IMPORTERS",
    "GRID_PUBLISHERS",
    "GRID_RESTORERS",
    "RESTORABLE_STEPS",
    "TARGETS",
    "WRITE_TIER",
    "WRITE_TOOL_NAMES",
    "compact_events",
    "register_write_tools",
    "journal_lock",
    "journal_window",
    "with_journal",
    "write_tool_specs",
]

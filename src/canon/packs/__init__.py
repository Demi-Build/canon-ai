"""``canon.packs`` — the pack registry home (Phase 0 §5.1; P0 paper P.3–P.4).

Row P0-4 (W3.2 packaging surgery) relocated the two built-in packs here so
they ship inside the wheel as package code + package data (schemas, cost
model, rule overrides, graphics lanes, the Godot template):

- ``canon.packs.platformer`` — the platformer vertical-slice pack. Its slice
  runner lives at ``canon.packs.platformer.run_slice`` (``python -m`` it;
  ``canon world new`` spawns exactly that).
- ``canon.packs.dungeon`` — the dungeon pack. The module is ``dungeon`` to
  match the registry id it declares (renamed from ``mazeworld`` 2026-09-01;
  only the package path moved). The MazeWorld-named classes and tests
  (``MazeworldManifestPhase``, ``tests/test_mazeworld_*.py``) describe the
  engine's data shapes and rename with the W2.0 pull-in.

Row P0-3 (W1 P1) adds the registry seam on top:

- ``PACKS`` — the built-in ``PackSpec`` seeds keyed by ``pack_type``. Ids are
  data: a third pack registers by adding an entry (the ``canon.packs``
  entry-point group for out-of-tree packs is P0-6 / later, not now).
- ``resolve_pack(pack_dir)`` — P.4.1's four-tier resolution (registry →
  manifest stamp → shape → error) with the read-both shim: no migration, no
  file written, a legacy pack resolves by shape forever.
- ``pack_info(pack_dir)`` — the P.4.6 document ``canon pack info`` emits;
  cradle's ``world_kind`` is its ``pack_type`` verbatim.

Row P0-6 (W1 P3 write) makes tier 1 real: ``effective_spec(seed, registry)``
overlays the pack's ``.canon/registry.json`` onto the code seed — every
stamped ``entities`` / ``grids`` / ``dialogue`` / ``capabilities`` /
``world_fields`` … entry comes from the FILE, the seed contributes only its
seed-only callables (models, loaders, builders, prompts, compose) for the
kinds it knows, and a ``db define``d kind gets the generic collection /
per-file loader from ``canon.packs.rows``. ``resolve_pack`` answers with
that effective spec whenever tier 1 answers, so ``pack info`` and every
verb see project-defined kinds with zero code changes (§5.1a, success
criterion 6). Nothing here writes: synthesis is ``canon.registry_ops``.

Row P0-10 (W2 create flow) adds the template half:

- ``pack_templates()`` — the P.4.4 wizard metadata for every installed seed
  (``canon pack templates``). Cradle's ``NewProjectModal`` renders its two
  cards, its count fields, its ranges and its phase labels from THIS, so the
  hardcoded ``TEMPLATES`` array and the hardcoded 22-entry ``plat:*`` label
  map are both gone (master §3.0-E, S5).
- ``PackSpec.runner`` (in ``spec.py``) — how ``world new --template <id>``
  spawns each template's create runner. ``world new`` dispatches through
  ``PACKS`` on that data instead of hardcoding the platformer runner.

Registry STAMPING at create is ``canon.registry_ops.ensure_registry`` (built
at P0-6, first called at create by P0-10). After P0-4 nothing in ``src/canon`` needs the source
checkout: every data file is located relative to its module, so an installed
wheel is self-sufficient — including the pygame play harness
(``canon.packs.platformer.play``, moved in 2026-09-01 so a bundled cradle can
▶ Play a level; cradle's ``canon_repo_root()`` now only locates the
interpreter and ``.env`` until P0-11's bundled runtime takes over).
"""

from __future__ import annotations

import copy
import dataclasses
import json
import re
from dataclasses import dataclass
from dataclasses import fields as _dc_fields
from functools import partial
from pathlib import Path
from typing import Any

from canon.packs.dungeon.spec import PACK_SPEC as _DUNGEON
from canon.packs.platformer.spec import PACK_SPEC as _PLATFORMER
from canon.packs.rows import load_per_file_rows, load_rows
from canon.packs.spec import (
    CORE_PROTECTED,
    TUNING_RESERVED,
    DialogueSpec,
    EngineEntry,
    EntityKind,
    GridKind,
    Layout,
    PackSpec,
    WizardMeta,
)

__all__ = [
    "CORE_PROTECTED",
    "PACKS",
    "REGISTRY_SCHEMA",
    "TUNING_RESERVED",
    "DialogueSpec",
    "EngineEntry",
    "EntityKind",
    "GridKind",
    "Layout",
    "PackSpec",
    "PackTypeError",
    "ResolvedPack",
    "WizardMeta",
    "effective_spec",
    "pack_info",
    "pack_templates",
    "resolve_pack",
]

#: ``.canon/registry.json.schema`` (P.4.2) — mirrors ``canon-engine/v1``.
REGISTRY_SCHEMA = "canon-registry/v1"

#: The built-in seeds, keyed by their registry id. Insertion order is the
#: ``pack templates`` card order (P0-10).
PACKS: dict[str, PackSpec] = {
    _PLATFORMER.pack_type: _PLATFORMER,
    _DUNGEON.pack_type: _DUNGEON,
}


class PackTypeError(ValueError):
    """P.4.1 tier 4: the directory is not a pack any registered seed
    recognises (or names a ``pack_type`` no seed is registered under)."""


@dataclass
class ResolvedPack:
    """What ``resolve_pack`` answers: the seed, which tier answered
    (``registry`` | ``manifest`` | ``shape``), the registry document when
    tier 1 answered (rides along for P0-10's stamped reads), and the id."""

    spec: PackSpec
    source: str
    registry: dict | None
    pack_type: str


def _read_json(path: Path) -> Any | None:
    """A JSON document, or ``None`` when the file is absent or unreadable —
    an absent / unparseable sidecar never fails a pack; the next tier answers.
    (A sidecar that PARSES but is malformed is the caller's call — see
    ``resolve_pack`` on a v1 registry without a ``pack_type``.)"""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _seed_for(pack_type: str, pack: Path, tier: str) -> PackSpec:
    spec = PACKS.get(pack_type)
    if spec is None:
        raise PackTypeError(
            f"unknown pack type {pack_type!r} ({tier}) in {pack}: "
            f"registered seeds are {sorted(PACKS)}"
        )
    return spec


_ENTITY_STAMPED = tuple(
    f.name for f in _dc_fields(EntityKind) if f.name not in ("kind", *EntityKind.SEED_ONLY)
)
_GRID_STAMPED = tuple(f.name for f in _dc_fields(GridKind) if f.name != "kind")
_DIALOGUE_STAMPED = tuple(f.name for f in _dc_fields(DialogueSpec) if f.name not in DialogueSpec.SEED_ONLY)
#: Registry keys copied onto the effective spec verbatim when present.
_SPEC_OVERLAY = (
    "label", "description", "vocab", "capabilities", "counts", "wizard", "engines", "world_fields", "phase_labels",
)


def _entity_from_registry(kind: str, entry: Any, seed_entry: EntityKind | None) -> EntityKind:
    if not isinstance(entry, dict):
        raise PackTypeError(f"registry entities.{kind} must be an object")
    unknown = sorted(set(entry) - set(_ENTITY_STAMPED))
    if unknown:
        raise PackTypeError(f"registry entities.{kind} carries unknown field(s) {unknown}")
    try:
        entity = EntityKind(kind=kind, **{k: v for k, v in entry.items()})
    except TypeError as exc:
        raise PackTypeError(f"registry entities.{kind}: {exc}") from None
    if seed_entry is not None:
        for name in EntityKind.SEED_ONLY:
            setattr(entity, name, getattr(seed_entry, name))
    # A kind the seed does not know (db define'd) reads through the generic
    # layout loader; a seed kind keeps its own loader even when its layout
    # was edited, because that loader validates into the seed's model.
    if entity.loader is None:
        mode = (entity.layout or {}).get("mode")
        entity.loader = partial(load_rows if mode == "collection" else load_per_file_rows, entity=entity)
    return entity


def effective_spec(seed: PackSpec, registry: dict[str, Any]) -> PackSpec:
    """The seed with the registry document overlaid (P.4.1 tier 1): stamped
    data from the file, seed-only code from the seed. A registry that omits
    a block (the minimal pre-P0-10 stamp) keeps the seed's. Malformed
    entries are a ``PackTypeError`` — a present registry is the source of
    truth, and guessing past it would hide the corruption."""
    changes: dict[str, Any] = {}
    if isinstance(registry.get("entities"), dict):
        changes["entities"] = {
            kind: _entity_from_registry(kind, entry, seed.entities.get(kind))
            for kind, entry in registry["entities"].items()
        }
    if isinstance(registry.get("grids"), dict):
        grids: dict[str, GridKind] = {}
        for kind, entry in registry["grids"].items():
            if not isinstance(entry, dict) or set(entry) - set(_GRID_STAMPED):
                raise PackTypeError(f"registry grids.{kind} is malformed")
            grids[kind] = GridKind(kind=kind, **entry)
        changes["grids"] = grids
    for key in _SPEC_OVERLAY:
        if key in registry:
            changes[key] = registry[key]
    capabilities = changes.get("capabilities", seed.capabilities)
    if isinstance(registry.get("dialogue"), dict):
        data = {k: v for k, v in registry["dialogue"].items() if k in _DIALOGUE_STAMPED}
        tree_model = seed.dialogue.tree_model if seed.dialogue is not None else None
        if tree_model is None:
            from canon.dialogue.models import DialogueTree

            tree_model = DialogueTree
        changes["dialogue"] = DialogueSpec(**data, tree_model=tree_model)
    elif "capabilities" in registry and "dialogue" not in capabilities:
        changes["dialogue"] = None
    return dataclasses.replace(seed, **changes)


def resolve_pack(pack_dir: str | Path) -> ResolvedPack:
    """P.4.1's resolution order, all four tiers (the read-both shim):

    1. ``<pack>/.canon/registry.json`` with ``schema == "canon-registry/v1"``
       — its ``pack_type`` picks the seed and the document rides along;
    2. ``manifest.json.pack_type`` — the mirror every manifest writer stamps
       (the pre-registry tier: every pack P0-3…P0-9 touch has a stamp but no
       registry until P0-10 writes the first one);
    3. shape detection — ``level/`` ⇒ ``platformer``; ``rooms/`` +
       ``world_bible.json`` ⇒ ``dungeon`` (both demo worlds resolve here;
       cradle's heuristic today is ``manifest.json`` + ``level/``);
    4. ``PackTypeError`` naming the directory.

    Tier 1 answers with the EFFECTIVE spec (``effective_spec``): the file's
    stamped entries over the seed's code — a ``db define``d kind is a real
    ``EntityKind`` here (row P0-6).

    Fail-closed on a malformed registry (doctrine 1): a sidecar that parses
    and declares ``canon-registry/v1`` but carries no usable ``pack_type``
    (or one no seed is registered under) is a hard ``PackTypeError`` — the
    later tiers do NOT answer for it, because a present registry is the
    source of truth and silently guessing past it would hide the corruption.
    Only an absent or unparseable file falls through.

    No migration: nothing is written, no file is synthesized.
    """
    pack = Path(pack_dir)
    registry = _read_json(pack / ".canon" / "registry.json")
    if isinstance(registry, dict) and registry.get("schema") == REGISTRY_SCHEMA:
        pack_type = registry.get("pack_type")
        if not isinstance(pack_type, str) or not pack_type:
            raise PackTypeError(f"registry without a pack_type in {pack}")
        seed = _seed_for(pack_type, pack, "registry")
        return ResolvedPack(effective_spec(seed, registry), "registry", registry, pack_type)
    manifest = _read_json(pack / "manifest.json")
    if isinstance(manifest, dict):
        pack_type = manifest.get("pack_type")
        if isinstance(pack_type, str) and pack_type:
            return ResolvedPack(_seed_for(pack_type, pack, "manifest"), "manifest", None, pack_type)
    if (pack / "level").is_dir():
        return ResolvedPack(_seed_for("platformer", pack, "shape"), "shape", None, "platformer")
    if (pack / "rooms").is_dir() and (pack / "world_bible.json").is_file():
        return ResolvedPack(_seed_for("dungeon", pack, "shape"), "shape", None, "dungeon")
    raise PackTypeError(
        f"unknown pack type: {pack} has no .canon/registry.json, no manifest.json "
        "pack_type stamp, and no recognised shape (level/ or rooms/ + world_bible.json)"
    )


# ---------------------------------------------------------------------------
# `canon pack info` (P.4.6)
# ---------------------------------------------------------------------------


def _row_count(pack: Path, entity: EntityKind, grids: dict[str, GridKind] | None = None) -> int:
    """Rows on disk per the kind's layout: ``per_file`` = JSON files in the
    dir; ``collection`` = array length / object key count; 0 when absent —
    except that a collection kind which also owns a ``GridKind`` (rooms)
    falls back to counting the grid's per-item files
    (``rooms/room_*/maze.json``) when the collection index is missing: the
    legacy trees (both bundled demos, the reference fixture) predate
    ``rooms/rooms.json`` (decided 2026-09-01, P0 paper P.9)."""
    layout = entity.layout or {}
    mode = layout.get("mode")
    if mode == "per_file":
        directory = pack / str(layout.get("dir", ""))
        return len(list(directory.glob("*.json"))) if directory.is_dir() else 0
    if mode == "collection":
        data = _read_json(pack / str(layout.get("path", "")))
        if isinstance(data, list):
            return len(data)
        if isinstance(data, dict):
            return len(data)
        for grid in (grids or {}).values():
            template = grid.path_template
            if grid.kind == entity.kind and "{" in template:
                pattern = re.sub(r"\{[^}]+\}", "*", template)
                return len(list(pack.glob(pattern)))
    return 0


def _schema_source(pack: Path, spec: PackSpec, entity: EntityKind) -> str | None:
    """``pack`` when the pack carries its own ``schemas/<kind>.json`` (the
    ``_schema_path`` override precedent), ``template`` when the seed ships
    one, ``None`` when the kind has no roll table on either side."""
    if not entity.schema:
        return None
    if (pack / entity.schema).is_file():
        return "pack"
    if spec.template_dir is not None and (spec.template_dir / entity.schema).is_file():
        return "template"
    return None


def pack_info(pack_dir: str | Path) -> dict[str, Any]:
    """The one JSON document cradle, P0-5, P0-8 and P0-9 read (P.4.6), built
    from ``resolve_pack``. Never a hardcoded union: every kind the seed
    declares renders, counts come from the files, ``placeable`` from
    ``grids.*.placements``, the ``engine_evaluable_*`` blocks from the primary
    seed engine (falling back to ``DialogueSpec.engine_evaluable_seed`` —
    never to "all supported", P.2.4). A pre-registry pack answers with
    ``template.version: null``.

    Row P1-A7.5 adds ``engine_copy`` — the CODE-EVOLVED flag
    (``canon.engine_ops.code_evolved``): which files of this project's own
    engine copy were hand- or agent-edited, who edited them, and the interim
    rule that follows from it. §7.1 requires the agent to disclose this
    before it runs that engine, and the panel surfaces it beside the engine
    chip."""
    pack = Path(pack_dir)
    resolved = resolve_pack(pack)
    spec = resolved.spec
    placeable = {
        placement.get("kind")
        for grid in spec.grids.values()
        for placement in grid.placements.values()
    }
    entities = {
        kind: {
            "label": entity.label,
            "id_field": entity.id_field,
            "layout": dict(entity.layout),
            "count": _row_count(pack, entity, spec.grids),
            "placeable": kind in placeable,
            "schema_source": _schema_source(pack, spec, entity),
        }
        for kind, entity in spec.entities.items()
    }
    grids = {
        kind: {
            "placements": {
                key: {"kind": placement.get("kind"), "wire": placement.get("wire")}
                for key, placement in grid.placements.items()
            },
            "points": list(grid.points),
            "dims": dict(grid.dims),
        }
        for kind, grid in spec.grids.items()
    }
    out: dict[str, Any] = {
        "pack_type": resolved.pack_type,
        "label": spec.label,
        "description": spec.description,
        "capabilities": list(spec.capabilities),
        "vocab": list(spec.vocab),
        "entities": entities,
        "grids": grids,
    }
    if "dialogue" in spec.capabilities and spec.dialogue is not None:
        out["dialogue"] = spec.dialogue.stamped()
        out["dialogue"].pop("engine_evaluable_seed", None)
    engine = spec.primary_engine()
    if engine is not None:
        namespaces = engine.get("evaluable_namespaces")
        if namespaces is None and spec.dialogue is not None:
            namespaces = spec.dialogue.engine_evaluable_seed.get(engine.get("id", ""))
        if namespaces is not None:
            out["engine_evaluable_namespaces"] = namespaces
        if engine.get("evaluable_bindings") is not None:
            out["engine_evaluable_bindings"] = engine["evaluable_bindings"]
    out["engines"] = [
        {"id": entry.get("id"), "primary": bool(entry.get("primary"))}
        for entry in spec.engines
    ]
    # Row P1-A7.5 / Phase 1 §7.1: whether this project's ENGINE COPY carries
    # hand- or agent-edited files. The probe must surface it BEFORE anything
    # agent-triggered runs that engine, and it carries master §3.0-I's interim
    # rule (pygame surfaces show template physics for a code-evolved pack)
    # with it. Never fatal: an engine module that will not import names its
    # problem inside the block rather than breaking the probe.
    try:
        from canon.engine_ops import code_evolved

        out["engine_copy"] = code_evolved(pack)
    except Exception as exc:  # noqa: BLE001 — a probe names its failure
        out["engine_copy"] = {"present": False, "code_evolved": False, "problem": f"{type(exc).__name__}: {exc}"}
    template = (resolved.registry or {}).get("template") or {}
    out["template"] = {
        "id": template.get("id", resolved.pack_type),
        "version": template.get("version"),
    }
    out["source"] = resolved.source
    return out


# ---------------------------------------------------------------------------
# `canon pack templates` (P.4.4) — row P0-10
# ---------------------------------------------------------------------------


def _distribution(spec: PackSpec) -> list[str]:
    """W2.4: the wizard's distribution axis is DERIVED from the engines block
    (``engines[*].exports``), never authored — engine choice and distribution
    are coupled, so one datum feeds both. Sorted for a stable card."""
    targets: set[str] = set()
    for engine in spec.engines:
        for target in engine.get("exports") or []:
            if isinstance(target, str) and target:
                targets.add(target)
    return sorted(targets)


def template_meta(spec: PackSpec) -> dict[str, Any]:
    """One P.4.4 entry for *spec*: the seed's ``wizard`` block with the four
    fields that must not be able to drift filled from the spec itself —
    ``id``/``label``/``description``/``vocab`` mirror the template, ``defaults``
    falls back to ``PackSpec.counts``, ``phase_labels`` IS ``PackSpec.phase_labels``
    (the same map the registry stamps, §3.0-E), and ``distribution`` is derived
    from the engines block. Everything else (``ranges``, ``advanced``,
    ``engine``, ``dimension``, ``beta``) is template-authored data.

    ``ranges`` may be ``None`` — a template that has not authored bands yet is
    honest about it rather than inventing one; the wizard then renders an
    unbounded stepper (doctrine 4: say what you don't know).

    Two DERIVED keys the wizard cannot guess, both read off the spec so a third
    template gets them for free:

    - ``generators`` — the generator lanes this template's runner actually
      accepts (``PackSpec.runner['backends']``). The dungeon has no ``vlm``
      lane, so offering an Animation backend for it key-gated and
      spend-confirmed a run canon prices at $0 and then ignores. The wizard
      disables what a template has no lane for, WITH the reason (doctrine 4).
    - ``count_scope`` — count field → ``"total"`` or ``"per_<grid kind>"``
      (``per_room`` on the dungeon), from each entity kind's ``per_map`` flag
      and the template's own grid vocabulary. The dungeon's NPC/monster/item
      counts are PER ROOM (``DatabasePhase`` multiplies by the map count), so a
      label reading plain "NPCs" is off by a factor of rooms. The kind that IS
      the map (it names a grid) is excluded — rooms are not per room."""
    wizard = dict(spec.wizard or {})
    lanes = list((spec.runner or {}).get("backends") or {})
    grids = spec.grids or {}
    map_kind = next(iter(grids), "")
    count_scope: dict[str, str] = {}
    for kind_name, kind in (spec.entities or {}).items():
        field = getattr(kind, "count_key", None)
        if not field or kind_name in grids:
            continue
        per_map = bool(getattr(kind, "per_map", False)) and bool(map_kind)
        count_scope[field] = f"per_{map_kind}" if per_map else "total"
    meta: dict[str, Any] = {
        "id": spec.pack_type,
        "label": wizard.get("label") or spec.label,
        "description": wizard.get("description") or spec.description,
        "vocab": list(wizard.get("vocab") or spec.vocab),
        "defaults": dict(wizard.get("defaults") or spec.counts),
        "ranges": copy.deepcopy(wizard.get("ranges")),
        "advanced": list(wizard.get("advanced") or []),
        "engine": list(wizard.get("engine") or [e.get("id") for e in spec.engines if e.get("id")]),
        "dimension": wizard.get("dimension") or "2D",
        "distribution": _distribution(spec),
        "beta": bool(wizard.get("beta", False)),
        "phase_labels": dict(spec.phase_labels),
        "generators": lanes,
        "count_scope": count_scope,
    }
    return meta


def pack_templates() -> list[dict[str, Any]]:
    """Every installed template's P.4.4 wizard metadata, in ``PACKS`` order
    (which is the card order). Template-side: read BEFORE a pack exists, so it
    takes no pack directory and touches no disk."""
    return [template_meta(spec) for spec in PACKS.values()]

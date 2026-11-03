"""The portrait write-back — the half of the AssetPhase contract that was
missing, in both of the places it is needed.

``parsers.py`` writes ``"profile_image": None,  # AssetPhase fills later`` on
every row it builds, and the registry entry for each kind names the field that
holds its art (``EntityKind.asset["field"]``). But the collections are written
by ``DatabasePhase`` BEFORE ``AssetPhase`` runs, so nothing ever put the
produced path back into them: a generated pack ended up with the PNGs on disk
and every ``profile_image`` still ``null``. This module closes that loop:

- ``PortraitWritebackPhase`` — the compose-list phase. It reads the paths
  ``AssetPhase`` recorded in memory (never a filename of its own) and flushes
  them into the row files. Nothing is generated here and nothing is spent.
- ``backfill_portraits`` — the same stamping over a pack ALREADY on disk, for
  the packs generated before the loop was closed. It adopts the portraits that
  are already there, asking ``canon.pipeline.phases.asset`` for the path each
  row would own and stamping only the ones that really exist. It never calls a
  backend, so it costs nothing and cannot overwrite art with a fresh roll.

What this extends rather than duplicates: the naming convention lives ONCE, in
``asset.py``; which field holds a kind's art comes from the registry
(``EntityKind.asset``), never a literal here; the in-pipeline flush writes
through ``ctx.adapter`` like every other phase, and the on-disk repair writes
through ``canon.write_core.commit_document``, so it snapshots to the CAS and
journals one event per file under the caller's ``--actor`` like every other
mutation verb.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from canon.pipeline.phases.asset import (
    FIXED_PORTRAITS,
    environment_portrait_rel,
    row_portrait_rel,
)

logger = logging.getLogger(__name__)

#: The ``EntityKind.asset["kinds"]`` member that means "this field holds an
#: image". Registry data, matched — never a list of kind names.
IMAGE_ASSET = "image"

#: Default portrait sub-directory. ``CanonConfig.output_paths`` can move it
#: during a run; a pack on disk carries no such setting, so the repair reads
#: the directory every generated tree has.
DEFAULT_PORTRAITS_DIR = "portraits"


# ---------------------------------------------------------------------------
# Registry reads — which kinds carry image art, and where their rows live
# ---------------------------------------------------------------------------


def image_asset_kinds(spec: Any, fallback: Any = None) -> list[tuple[Any, str]]:
    """``(EntityKind, field)`` for every kind of *spec* whose registry entry
    declares an IMAGE asset and stores its rows in a collection file.

    A kind that declares audio (music / sfx) or no asset at all is skipped, as
    is one whose rows are not a single collection — this walks files, and a
    per-file kind has no collection to rewrite.

    *fallback* is a read-both shim, not a migration: a pack whose stamped
    registry predates a kind's asset declaration carries no ``asset`` block for
    it at all, and a stamped entry replaces the seed's wholesale. When the
    effective entry declares nothing, the seed's declaration answers — the
    LAYOUT still comes from the pack's own registry, and nothing is rewritten.
    A registry that names a different field keeps it.
    """
    out: list[tuple[Any, str]] = []
    spare = (getattr(fallback, "entities", None) or {}) if fallback is not None else {}
    for kind, entity in (getattr(spec, "entities", None) or {}).items():
        asset = getattr(entity, "asset", None) or getattr(spare.get(kind), "asset", None) or {}
        field = asset.get("field")
        if not field or IMAGE_ASSET not in (asset.get("kinds") or []):
            continue
        if (getattr(entity, "layout", None) or {}).get("mode") != "collection":
            continue
        out.append((entity, field))
    return out


def _iter_rows(doc: Any, entity: Any) -> list[tuple[int, str, dict]]:
    """``(position, key, row)`` over a collection document, generic over
    ``layout.format`` — the same three shapes ``canon.packs.rows.load_rows``
    reads, kept as the live objects so a caller can mutate them in place.

    ``keyed_object`` files carry the id in the KEY; the array formats carry it
    in ``id_field`` (a row missing it keys by its position, so a hand-broken
    row never blocks the walk).
    """
    id_field = getattr(entity, "id_field", "id") or "id"
    rows: list[tuple[int, str, dict]] = []
    if isinstance(doc, dict):
        for i, (key, row) in enumerate(doc.items()):
            if isinstance(row, dict):
                rows.append((i, str(key), row))
    elif isinstance(doc, list):
        for i, row in enumerate(doc):
            if isinstance(row, dict):
                raw = row.get(id_field)
                rows.append((i, str(raw) if raw is not None else str(i), row))
    return rows


# ---------------------------------------------------------------------------
# The shared stamping step
# ---------------------------------------------------------------------------


def stamp_collection(doc: Any, entity: Any, field: str, resolve: Any) -> dict[str, str]:
    """Set *field* on every row of *doc* for which *resolve* answers a path.

    ``resolve(position, key, row) -> str | None``. A row whose slot already
    holds a value is left alone — a portrait a person chose is not something a
    repair may replace. Mutates *doc* in place; returns ``{key: path}`` for the
    rows it actually changed.
    """
    changed: dict[str, str] = {}
    for position, key, row in _iter_rows(doc, entity):
        if row.get(field):
            continue
        path = resolve(position, key, row)
        if not path:
            continue
        row[field] = path
        changed[key] = path
    return changed


# ---------------------------------------------------------------------------
# In-pipeline: the compose-list phase
# ---------------------------------------------------------------------------


class PortraitWritebackPhase:
    """Flush the portrait paths ``AssetPhase`` recorded into the row files.

    Runs AFTER both the asset generation and every phase that rewrites a
    collection from memory (placement rewrites ``events/events.json``), so the
    stamp is the last word on those files. A no-op when no portraits were
    produced: nothing is written, so a run with the image backend off leaves a
    byte-identical tree.
    """

    name: str = "portraits"

    def run(self, ctx: Any) -> None:
        from canon.packs.dungeon.spec import PACK_SPEC

        recorded = self._recorded(ctx)
        output_dir = Path(getattr(ctx.config, "output_dir", "."))
        written: list[str] = []
        for entity, field in image_asset_kinds(PACK_SPEC):
            rel = str((getattr(entity, "layout", None) or {}).get("path", ""))
            if not rel:
                continue
            doc = _read_json(output_dir / rel)
            if doc is None:
                continue
            paths = recorded.get(entity.kind, {})
            changed = stamp_collection(
                doc, entity, field,
                lambda _pos, key, _row, _paths=paths: _paths.get(key),
            )
            if changed:
                ctx.adapter.write_json_singleton(rel, doc)
                written.append(rel)
        if written:
            logger.info("Portrait paths recorded in %s.", ", ".join(written))

        from canon.bible.models import BibleMetadata

        if not isinstance(getattr(ctx.bible, "metadata", None), BibleMetadata):
            ctx.bible.metadata = BibleMetadata()
        ctx.bible.metadata.phases_run.append(self.name)

    @staticmethod
    def _recorded(ctx: Any) -> dict[str, dict[str, str]]:
        """``{kind: {row key: path}}`` — every portrait ``AssetPhase`` stamped
        in memory. Entity stubs carry theirs in ``EntityLore.extra`` (the core
        model declares no portrait field); archetypes have a real
        ``portrait_path``. The keys are the ones ``_iter_rows`` produces: a
        stringified row id, and the archetype id for a class."""
        by_kind: dict[str, dict[str, str]] = {}
        for m in (getattr(ctx.bible, "maps", None) or {}).values():
            for stub in m.entities or []:
                path = (getattr(stub, "extra", None) or {}).get("portrait_path")
                if path:
                    by_kind.setdefault(stub.entity_type, {})[str(stub.entity_id)] = path
        for arch_id, arch in (getattr(ctx.bible, "class_archetypes", None) or {}).items():
            path = getattr(arch, "portrait_path", None)
            if path:
                by_kind.setdefault("class", {})[str(getattr(arch, "archetype", "") or arch_id)] = path
        return by_kind


# ---------------------------------------------------------------------------
# On disk: the repair
# ---------------------------------------------------------------------------


def backfill_portraits(
    pack_dir: str | Path,
    *,
    actor: str = "user",
    session: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Record the portraits ALREADY on disk in the rows and manifest of an
    existing pack. Generates nothing, deletes nothing, spends nothing.

    For every row of every kind whose registry entry declares an image asset,
    the path that row would own is asked of ``canon.pipeline.phases.asset``; if
    that file exists and the row's slot is empty, the path is stamped. The
    manifest's fixed portraits and per-room environment portraits are filled
    the same way. Each changed file is committed through the write core, so it
    is snapshotted to the CAS and journaled under *actor*.

    ``dry_run`` reports what would change and writes nothing.
    """
    from canon.packs import PACKS, resolve_pack

    pack = Path(pack_dir)
    resolved = resolve_pack(pack)
    spec = resolved.spec
    changed: dict[str, dict[str, str]] = {}
    files: list[str] = []
    missing: list[str] = []

    def commit(rel: str, doc: Any, detail: dict) -> None:
        files.append(rel)
        if dry_run:
            return
        from canon.write_core import commit_document

        commit_document(
            pack,
            artifact_id=rel,
            rel_path=rel,
            data=doc,
            actor=actor,
            session=session,
            op="edit",
            source="repair",
            detail=detail,
        )

    for entity, field in image_asset_kinds(spec, PACKS.get(resolved.pack_type)):
        rel = str((getattr(entity, "layout", None) or {}).get("path", ""))
        if not rel:
            continue
        doc = _read_json(pack / rel)
        if doc is None:
            continue

        def resolve(position: int, key: str, row: dict, _kind: str = entity.kind) -> str | None:
            candidate = row_portrait_rel(
                _kind, index=position, row_id=key, name=row.get("name"),
                portraits_dir=DEFAULT_PORTRAITS_DIR,
            )
            if (pack / candidate).is_file():
                return candidate
            missing.append(candidate)
            return None

        stamped = stamp_collection(doc, entity, field, resolve)
        if stamped:
            changed[entity.kind] = stamped
            commit(rel, doc, {"kind": entity.kind, "field": field, "recorded": stamped})

    manifest_changed = _backfill_manifest(pack, commit)
    if manifest_changed:
        changed["manifest"] = manifest_changed

    return {
        "pack_dir": str(pack),
        "recorded": {kind: len(rows) for kind, rows in changed.items()},
        "files": files,
        "no_file_on_disk": sorted(set(missing)),
        "dry_run": dry_run,
    }


def _backfill_manifest(pack: Path, commit: Any) -> dict[str, str]:
    """Fill ``manifest.json``'s portrait fields from the files on disk: the
    fixed portraits by name, each room's environment portrait by position, and
    ``portraits_generated`` from whether any portrait exists at all."""
    rel = "manifest.json"
    manifest = _read_json(pack / rel)
    if not isinstance(manifest, dict):
        return {}
    recorded: dict[str, str] = {}
    for key, stem, _prompt in FIXED_PORTRAITS:
        if manifest.get(key):
            continue
        candidate = f"{DEFAULT_PORTRAITS_DIR}/{stem}.png"
        if (pack / candidate).is_file():
            manifest[key] = candidate
            recorded[key] = candidate
    rooms = manifest.get("rooms")
    if isinstance(rooms, list):
        for i, room in enumerate(rooms):
            if not isinstance(room, dict) or room.get("environment_portrait"):
                continue
            candidate = environment_portrait_rel(i, portraits_dir=DEFAULT_PORTRAITS_DIR)
            if (pack / candidate).is_file():
                room["environment_portrait"] = candidate
                recorded[f"rooms[{i}].environment_portrait"] = candidate
    has_art = any((pack / DEFAULT_PORTRAITS_DIR).rglob("*.png"))
    if has_art and not manifest.get("portraits_generated"):
        manifest["portraits_generated"] = True
        recorded["portraits_generated"] = "true"
    if not recorded:
        return {}
    commit(rel, manifest, {"recorded": recorded})
    return recorded


def _read_json(path: Path) -> Any | None:
    """The parsed file, or ``None`` when it is absent or unreadable — a pack
    missing one collection is still worth repairing for the others."""
    from canon.packs.rows import read_json

    try:
        return read_json(path)
    except (OSError, ValueError):
        logger.warning("Portrait write-back: %s is not readable JSON; left alone.", path)
        return None

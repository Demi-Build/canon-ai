"""GodotExportPhase — drops the Godot project template into the output tree.

The generated data tree IS the Godot project: ``project.godot`` lands at
the output root (``res://`` cannot escape the project root, so this puts
every generated artifact in reach), and the game scene/script live under
``godot/``. Static template files are pack data
(``src/canon/packs/platformer/godot_template/``) copied through the adapter
like any other artifact.

Composed only when the runner is invoked with ``--engine godot``.

Because the runtime is COPIED at generation time, a pack keeps whatever
engine code existed the day it was made — and this phase only runs during a
full pipeline run. Every engine fix shipped afterwards is invisible to packs
that already exist. That is not hypothetical: the AtlasTexture-margin render
fix reached the template and no pack, so shipped packs kept mis-drawing
trimmed frames in normal gameplay.

So the phase also STAMPS what it wrote (``godot/.engine.json``), and the
functions below let `canon engine status` / `canon engine sync` compare a
pack against the current template and refresh it in place. The stamp is
content-hash based rather than a version string: there is no version
vocabulary to keep honest, and it is the same addressing the rest of canon
uses.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from canon.bible.models import BibleMetadata

TEMPLATE_DIR = Path(__file__).parent / "godot_template"

#: Where the stamp lives inside a pack. Under ``godot/`` so it travels with
#: the thing it describes, dot-prefixed so it reads as machinery.
STAMP_REL = "godot/.engine.json"
STAMP_SCHEMA = "canon-engine/v1"

#: Row P1-A7.5 — the ATTRIBUTION half of the stamp, additive (the schema
#: string is unchanged: a reader that predates this key ignores it, and no
#: pack needs migrating). ``files`` still records what CANON wrote, which is
#: what makes ``engine_status`` call a differing file ``modified``; this map
#: records WHO changed it and to what, so the answer to "why did sync refuse
#: this file?" is a name and a timestamp rather than an inference.
#: ``{rel: {hash, was, actor, session, at, op}}``.
MODIFIED_KEY = "modified"

logger = logging.getLogger(__name__)


def _sha(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def template_files() -> dict[str, bytes]:
    """The current engine runtime, ``rel path -> bytes``."""
    out: dict[str, bytes] = {}
    for source in sorted(TEMPLATE_DIR.rglob("*")):
        if source.is_file():
            out[source.relative_to(TEMPLATE_DIR).as_posix()] = source.read_bytes()
    return out


def template_manifest(files: dict[str, bytes] | None = None) -> dict:
    """The stamp payload for a set of runtime files.

    ``template_hash`` folds the per-file hashes so one comparison answers
    "is this pack current?", while ``files`` answers "which parts differ?".
    """
    files = template_files() if files is None else files
    per_file = {rel: _sha(data) for rel, data in sorted(files.items())}
    rollup = hashlib.sha256(
        "\n".join(f"{rel}:{h}" for rel, h in per_file.items()).encode()
    ).hexdigest()
    return {
        "schema": STAMP_SCHEMA,
        "template_hash": "sha256:" + rollup,
        "files": per_file,
    }


def read_stamp(pack_dir: str | Path) -> dict | None:
    """The stamp a pack was written with, or None for packs that predate it."""
    path = Path(pack_dir) / STAMP_REL
    if not path.is_file():
        return None
    try:
        stamp = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return stamp if isinstance(stamp, dict) else None


def _write_stamp(pack_dir: str | Path, stamp: dict) -> dict:
    """Persist ``stamp`` at ``STAMP_REL`` (creating ``godot/``)."""
    path = Path(pack_dir) / STAMP_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(stamp, indent=2))
    return stamp


def modified_entries(pack_dir: str | Path) -> dict[str, dict]:
    """The stamp's attribution map (``{rel: {...}}``), or ``{}``. Row A7.5."""
    entries = (read_stamp(pack_dir) or {}).get(MODIFIED_KEY)
    return {k: v for k, v in entries.items() if isinstance(v, dict)} if isinstance(entries, dict) else {}


def ensure_stamp(pack_dir: str | Path) -> dict:
    """The pack's stamp, creating one from WHAT IS ON DISK if it has none.

    A pack that predates stamping has no record of what canon wrote, so
    ``engine_status`` calls every differing file ``unstamped`` and
    ``engine_sync`` overwrites it — which would silently undo an agent's
    code edit. Row A7.5 stamps before it writes: the baseline recorded is
    the pack's own current bytes (the honest "this is what was here"), so
    afterwards its untouched runtime files read ``stale`` (syncable, as they
    were) and only the edited one reads ``modified``.
    """
    pack = Path(pack_dir)
    stamp = read_stamp(pack)
    if stamp is not None:
        return stamp
    present = {rel: (pack / rel).read_bytes() for rel in template_files() if (pack / rel).is_file()}
    return _write_stamp(pack, template_manifest(present))


def stamp_modified(
    pack_dir: str | Path,
    rel: str,
    *,
    after_hash: str,
    actor: str = "user",
    session: str | None = None,
    op: str = "edit",
    at: str | None = None,
) -> dict:
    """Record ``rel`` as ``modified`` by ``actor`` (row A7.5) and return the stamp.

    Attribution survives the edit: ``engine_status`` names who changed the
    file and ``engine_sync`` refuses to overwrite it (it already refuses any
    file differing from its stamp — this says by whom, in the file itself,
    where a later reader hits it)."""
    from datetime import UTC, datetime

    pack = Path(pack_dir)
    stamp = dict(ensure_stamp(pack))
    entries = dict(stamp.get(MODIFIED_KEY) or {})
    entries[rel] = {
        "hash": after_hash,
        "was": (stamp.get("files") or {}).get(rel),
        "actor": actor,
        "session": session,
        "at": at or datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "op": op,
    }
    stamp[MODIFIED_KEY] = dict(sorted(entries.items()))
    return _write_stamp(pack, stamp)


def clear_modified(pack_dir: str | Path, rel: str) -> dict | None:
    """Drop ``rel``'s attribution (row A7.5's restore leg) — the file is
    canon's again. ``None`` when the pack has no stamp; the stamp is left
    untouched when nothing was attributed."""
    pack = Path(pack_dir)
    stamp = read_stamp(pack)
    if stamp is None:
        return None
    entries = dict(stamp.get(MODIFIED_KEY) or {})
    if rel not in entries:
        return stamp
    entries.pop(rel)
    stamp = dict(stamp)
    if entries:
        stamp[MODIFIED_KEY] = entries
    else:
        stamp.pop(MODIFIED_KEY, None)
    return _write_stamp(pack, stamp)


def engine_status(pack_dir: str | Path) -> dict:
    """Compare a pack's engine runtime against the current template.

    Pure read. Classifies every runtime file so the caller can explain itself:

    ``missing``     the pack never got this file
    ``stale``       matches its stamp, but the template has moved on — safe to sync
    ``modified``    differs from its OWN stamp: hand-edited, so sync must not
                    silently overwrite it
    ``unstamped``   the pack predates stamping, so "hand-edited" is unknowable
    ``current``     byte-identical to the template

    Row P1-A7.5 adds the attribution the stamp now carries: a ``modified``
    file whose change was journaled (a ``game_coder`` edit, or a hand edit
    made through ``canon engine edit``) gains ``by`` — actor, session and
    timestamp — and the document answers ``code_evolved`` directly, which is
    what the probe (``pack_info``) and the agent's disclosure read. An
    attributed file the template does not carry is listed too, flagged
    ``in_template: False``, so ``code_evolved`` cannot answer False while the
    stamp records an edit.
    """
    pack = Path(pack_dir)
    current = template_files()
    manifest = template_manifest(current)
    stamp = read_stamp(pack)
    stamped: dict[str, str] = (stamp or {}).get("files") or {}
    attribution = modified_entries(pack)

    files: list[dict] = []
    for rel, data in current.items():
        want = _sha(data)
        target = pack / rel
        have = _sha(target.read_bytes()) if target.is_file() else None
        if have is None:
            state = "missing"
        elif have == want:
            state = "current"
        elif stamp is None:
            # No record of what canon wrote, so we cannot tell a hand edit from
            # an old build. Treat it as stale (syncable) but say it's unstamped.
            state = "unstamped"
        elif stamped.get(rel) == have:
            state = "stale"
        else:
            state = "modified"
        entry: dict = {"path": rel, "state": state}
        by = attribution.get(rel)
        if by is not None and state == "modified":
            entry["by"] = by
        files.append(entry)

    # Row A7.5: an attributed edit to an engine-copy file the CURRENT template
    # does not carry — a hand-added `godot/hud.gd`, or a file a later template
    # dropped while shipped packs still carry it. The loop above only walks the
    # template, so without this the stamp would record the edit while
    # `modified`, `attribution` and `code_evolved` all answered empty, and
    # §7.1's disclosure would never fire for it. `in_template: False` says why
    # `engine sync` has nothing to write for it.
    for rel in sorted(set(attribution) - set(current)):
        files.append({"path": rel, "state": "modified", "in_template": False, "by": attribution[rel]})

    by_state = [f["path"] for f in files if f["state"] != "current"]
    modified = [f["path"] for f in files if f["state"] == "modified"]
    unstamped = [f["path"] for f in files if f["state"] == "unstamped"]
    has_engine = (pack / "project.godot").is_file()
    return {
        "pack": str(pack),
        "has_engine": has_engine,
        "stamped": stamp is not None,
        "template_hash": manifest["template_hash"],
        "pack_hash": (stamp or {}).get("template_hash"),
        "current": not by_state,
        "files": files,
        "behind": by_state,
        "modified": modified,
        "unstamped": unstamped,
        # Row A7.5 / master §3.0-I: this copy carries agent- or hand-edited
        # files, so it is no longer the template's — say it once, here, where
        # every reader of the status already looks.
        "code_evolved": bool(has_engine and (modified or unstamped)),
        "attribution": {rel: by for rel, by in attribution.items() if rel in modified},
    }


def engine_sync(
    pack_dir: str | Path,
    *,
    dry_run: bool = False,
    force: bool = False,
    actor: str = "user",
    session: str | None = None,
) -> dict:
    """Refresh a pack's engine runtime from the current template.

    Fail-closed on hand edits: a file that differs from its OWN stamp is
    REFUSED by name rather than overwritten, because the stamp is the only
    evidence of what canon put there. ``force`` overwrites anyway. Mirrors the
    refusal shape of ``db update``'s protected fields and ``restore``'s
    history-scope guard — say what was skipped and why, don't guess.
    """
    from canon import provenance

    pack = Path(pack_dir)
    if not (pack / "project.godot").is_file():
        raise FileNotFoundError(
            f"no project.godot in {pack} — this pack was generated without the "
            f"godot engine, so there is no runtime to sync"
        )

    status = engine_status(pack)
    current = template_files()
    manifest = template_manifest(current)

    written: list[str] = []
    refused: list[str] = []
    for entry in status["files"]:
        rel, state = entry["path"], entry["state"]
        if state == "current":
            continue
        if rel not in current:
            # An engine-copy file the template does not carry (row A7.5's
            # attributed edits). Sync has no bytes to write for it, so it is
            # refused by name and never overwritten — not even with --force.
            refused.append(rel)
            continue
        if state == "modified" and not force:
            refused.append(rel)
            continue
        written.append(rel)

    if dry_run:
        return {
            "engine": "dry_run",
            "would_write": written,
            "refused": refused,
            "current": status["current"],
        }

    if not written:
        # Nothing to do — and a no-op must not pollute the journal (the same
        # hygiene rule apply_level_edit and world map-edit follow).
        return {
            "engine": "no_change",
            "written": [],
            "refused": refused,
            "current": status["current"],
        }

    before = (status.get("pack_hash") or "").strip() or None
    for rel in written:
        target = pack / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(current[rel])

    # Attribution SURVIVES a sync (row A7.5): a refused file is still that
    # actor's work, so its entry is carried into the new stamp. A file this
    # sync actually wrote is canon's again — ``--force`` over a modified file
    # is the only way that happens — so its entry is dropped with the bytes.
    kept = {rel: by for rel, by in modified_entries(pack).items() if rel not in written}
    stamp = dict(manifest)
    if kept:
        stamp[MODIFIED_KEY] = dict(sorted(kept.items()))
    _write_stamp(pack, stamp)

    provenance.record(
        pack,
        artifact_id="engine:godot",
        op="import",
        source="code",
        actor=actor,
        session=session,
        detail={
            "kind": "engine_sync",
            "written": written,
            "refused": refused,
            "forced": bool(force and refused == []),
        },
        before_hash=before,
        after_hash=manifest["template_hash"],
    )
    return {
        "engine": "updated",
        "written": written,
        "refused": refused,
        "template_hash": manifest["template_hash"],
    }


class GodotExportPhase:
    name = "plat:godot_export"

    def run(self, ctx: Any) -> None:
        files = template_files()
        for rel, data in files.items():
            ctx.adapter.write_binary(rel, data)

        # Stamp what we wrote, so a later `engine status` can tell a stale
        # runtime from a hand-edited one.
        ctx.adapter.write_binary(
            STAMP_REL, json.dumps(template_manifest(files), indent=2).encode()
        )

        logger.info(
            "GodotExportPhase wrote %d project files — play with: "
            "godot --path %s", len(files),
            ctx.adapter.resolve_path(".").resolve(),
        )
        if not isinstance(getattr(ctx.bible, "metadata", None), BibleMetadata):
            ctx.bible.metadata = BibleMetadata()
        ctx.bible.metadata.phases_run.append(self.name)

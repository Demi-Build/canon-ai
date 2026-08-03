"""GodotExportPhase — drops the Godot project template into the output tree.

The generated data tree IS the Godot project: ``project.godot`` lands at
the output root (``res://`` cannot escape the project root, so this puts
every generated artifact in reach), and the game scene/script live under
``godot/``. Static template files are pack data
(``examples/platformer_pack/godot_template/``) copied through the adapter
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


def engine_status(pack_dir: str | Path) -> dict:
    """Compare a pack's engine runtime against the current template.

    Pure read. Classifies every runtime file so the caller can explain itself:

    ``missing``     the pack never got this file
    ``stale``       matches its stamp, but the template has moved on — safe to sync
    ``modified``    differs from its OWN stamp: hand-edited, so sync must not
                    silently overwrite it
    ``unstamped``   the pack predates stamping, so "hand-edited" is unknowable
    ``current``     byte-identical to the template
    """
    pack = Path(pack_dir)
    current = template_files()
    manifest = template_manifest(current)
    stamp = read_stamp(pack)
    stamped: dict[str, str] = (stamp or {}).get("files") or {}

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
        files.append({"path": rel, "state": state})

    by_state = [f["path"] for f in files if f["state"] != "current"]
    return {
        "pack": str(pack),
        "has_engine": (pack / "project.godot").is_file(),
        "stamped": stamp is not None,
        "template_hash": manifest["template_hash"],
        "pack_hash": (stamp or {}).get("template_hash"),
        "current": not by_state,
        "files": files,
        "behind": by_state,
        "modified": [f["path"] for f in files if f["state"] == "modified"],
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

    stamp_path = pack / STAMP_REL
    stamp_path.parent.mkdir(parents=True, exist_ok=True)
    stamp_path.write_text(json.dumps(manifest, indent=2))

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

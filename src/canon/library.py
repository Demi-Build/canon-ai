"""The GLOBAL asset library (Library Piece C) — cross-project sharing.

A per-user store ABOVE the per-pack object stores:

    ~/.canon/library/            (override root via $CANON_LIBRARY)
      objects/<sha256-hex>       CAS, same format as pack stores
      index.jsonl                append-only: one entry per published asset

Doctrine (design doc §5/§5a, user-locked):
- Publishing snapshots an asset's bytes (composite assets travel WHOLE:
  an enemy def bundles row + sprite + animation files) and journals a
  ``op:"keep"`` event in the SOURCE pack — curation is a strong positive
  training signal.
- Importing COPIES bytes into the destination pack (packs stay
  self-contained and ship alone), always mints a fresh id (never
  overwrites), journals ``op:"import"`` with
  ``detail.kind:"library_import"`` + full source provenance, and stamps a
  durable ``library_ref`` into the artifact so a future
  "check for updates / re-import everywhere" op can find divergent copies.
- The index records the source project, so viewers can toggle
  global vs project scope and sort by project.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from canon import provenance

#: v1 shareable kinds (locked): whole levels/stages join in V2, style
#: bundles are the first v2 kind ahead of them.
KINDS = ("enemy_def", "item_def", "player_skin", "tile", "backdrop", "audio")


def library_root() -> Path:
    return Path(os.environ.get("CANON_LIBRARY", "~/.canon/library")).expanduser()


def _objects_dir() -> Path:
    d = library_root() / "objects"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _index_path() -> Path:
    library_root().mkdir(parents=True, exist_ok=True)
    return library_root() / "index.jsonl"


def _store(data: bytes) -> str:
    digest = hashlib.sha256(data).hexdigest()
    target = _objects_dir() / digest
    if not target.exists():
        target.write_bytes(data)
    return f"sha256:{digest}"


def read_object(content_hash: str) -> bytes:
    digest = content_hash.split(":", 1)[-1]
    return (_objects_dir() / digest).read_bytes()


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _entries() -> list[dict]:
    p = _index_path()
    out: list[dict] = []
    if not p.is_file():
        return out
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _append(entry: dict) -> None:
    with _index_path().open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, separators=(",", ":")) + "\n")


def _library_id(source_artifact: str, objects: dict[str, str]) -> str:
    """Deterministic id from content + source — republishing identical
    bytes converges on the same entry (dedup by identity)."""
    basis = source_artifact + "|" + "|".join(
        f"{k}={v}" for k, v in sorted(objects.items())
    )
    return "lib-" + hashlib.sha256(basis.encode()).hexdigest()[:12]


def _sprite_bundle(pack: Path, sprite_path: str) -> dict[str, str]:
    """CAS-store an actor's whole art bundle: base sprite + everything
    beside it (animation strips, atlas, frames manifest)."""
    objects: dict[str, str] = {}
    if not sprite_path:
        return objects
    base = pack / sprite_path
    if base.is_file():
        objects["sprite"] = _store(base.read_bytes())
    sprite_dir = base.parent
    if sprite_dir.is_dir():
        for f in sorted(sprite_dir.iterdir()):
            if (
                f.is_file() and f.name != base.name
                and not f.name.startswith(".")   # .DS_Store and friends
                and not f.name.endswith(".tmp")  # crashed atomic writes
            ):
                objects[f"file:{f.name}"] = _store(f.read_bytes())
    return objects


def publish(
    pack_dir: str | Path,
    target: str,
    *,
    name: str | None = None,
    tags: tuple[str, ...] | list[str] = (),
    actor: str = "user",
    session: str | None = None,
) -> dict:
    """Publish one asset from a pack into the global library.

    Targets: ``enemy:<id>`` | ``item:<id>`` | ``player`` |
    ``tile:<stage>/<name>`` | ``backdrop:<stage>/<index>`` | ``audio:<stage>``
    """
    pack = Path(pack_dir).resolve()
    manifest = _read_json(pack / "manifest.json")
    world = manifest.get("world")
    world_name = str(
        (world.get("title") if isinstance(world, dict) else world)
        or manifest.get("game", "")
        or pack.name
    )
    kind_prefix, _, rest = target.partition(":")
    objects: dict[str, str] = {}
    meta: dict[str, Any] = {}
    preview = ""
    default_name = rest or kind_prefix

    if kind_prefix in ("enemy", "item") and rest:
        row_path = pack / kind_prefix / f"{rest}.json"
        if not row_path.is_file():
            raise FileNotFoundError(f"{kind_prefix} {rest!r} not found")
        row = _read_json(row_path)
        objects["row"] = _store(row_path.read_bytes())
        objects.update(_sprite_bundle(pack, str(row.get("sprite_path", ""))))
        preview = objects.get("sprite", "")
        default_name = str(row.get("name") or rest)
        kind = f"{kind_prefix}_def"

    elif kind_prefix == "player":
        objects.update(_sprite_bundle(pack, "sprite/player/base.png"))
        if not objects:
            raise FileNotFoundError("no player sprite in this pack")
        preview = objects.get("sprite", "")
        default_name = f"{world_name} player"
        kind = "player_skin"

    elif kind_prefix == "tile" and "/" in rest:
        from PIL import Image

        stage_id, _, tile_name = rest.partition("/")
        tileset = _read_json(pack / "tileset" / stage_id / "manifest.json")
        slots = [s for s in tileset.get("slots", []) if s.get("name") == tile_name]
        if not slots:
            raise ValueError(f"tile {tile_name!r} not in the {stage_id} tileset")
        import io

        sheet = Image.open(pack / tileset["tilesheet_path"]).convert("RGBA")
        x, y, w, h = slots[0]["px_region"]
        buf = io.BytesIO()
        sheet.crop((x, y, x + w, y + h)).save(buf, "PNG")
        objects["art"] = _store(buf.getvalue())
        preview = objects["art"]
        meta["slot"] = {
            "name": tile_name,
            "tile_type": slots[0].get("tile_type"),
            "collision": slots[0].get("collision", ""),
            "params": {
                k: v for k, v in (slots[0].get("params") or {}).items()
                if k not in ("autotile_mask", "water_deep")
            },
        }
        default_name = tile_name
        kind = "tile"

    elif kind_prefix == "backdrop" and "/" in rest:
        stage_id, _, idx_s = rest.partition("/")
        backdrop = _read_json(pack / "backdrop" / stage_id / "manifest.json")
        bands = backdrop.get("band_paths", [])
        idx = int(idx_s)
        if not (0 <= idx < len(bands)):
            raise ValueError(f"band index {idx} out of range")
        objects["art"] = _store((pack / bands[idx]).read_bytes())
        preview = objects["art"]
        depths = backdrop.get("depths", [])
        meta["depth"] = float(depths[idx]) if idx < len(depths) else 0.5
        default_name = f"{stage_id} band {idx}"
        kind = "backdrop"

    elif kind_prefix == "audio" and rest:
        audio = _read_json(pack / "audio" / rest / "manifest.json")
        music = str(audio.get("music_path", ""))
        if not music or not (pack / music).is_file():
            raise FileNotFoundError(f"stage {rest!r} has no music track")
        objects["music"] = _store((pack / music).read_bytes())
        meta["filename"] = Path(music).name
        default_name = f"{rest} theme"
        kind = "audio"

    else:
        raise ValueError(
            f"unknown publish target {target!r} — enemy:<id> | item:<id> | "
            "player | tile:<stage>/<name> | backdrop:<stage>/<i> | audio:<stage>"
        )

    library_id = _library_id(f"{world_name}:{target}", objects)
    existing = next(
        (e for e in _entries() if e.get("library_id") == library_id), None
    )
    if existing is not None:
        # Same bytes → same entry, but a re-publish may still rename/retag:
        # append an updated index line (latest wins on read).
        merged_tags = sorted(set(existing.get("tags") or []) | set(tags))
        renamed = bool(name) and name != existing.get("name")
        if renamed or merged_tags != sorted(existing.get("tags") or []):
            updated = {
                **existing,
                "name": name or existing.get("name"),
                "tags": merged_tags,
                "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
            _append(updated)
            return {**updated, "deduped": True}
        return {**existing, "deduped": True}

    entry = {
        "schema": 1,
        "library_id": library_id,
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "kind": kind,
        "name": name or default_name,
        "tags": list(tags),
        "source": {
            "pack": str(pack),
            "world": world_name,
            # Journal artifact families: a tile's chain lives on the
            # TILESET artifact — "tile:<stage>" is nobody's id.
            "artifact_id": target if kind_prefix == "player" else (
                f"tileset:{rest.partition('/')[0]}"
                if kind_prefix == "tile"
                else f"{kind_prefix}:{rest.partition('/')[0]}"
                if kind_prefix in ("backdrop", "audio")
                else target
            ),
            "target": target,
        },
        "objects": objects,
        "meta": meta,
        "preview": preview,
        "actor": actor,
    }
    _append(entry)
    # Curation is a positive signal — journal in the SOURCE pack.
    provenance.record(
        pack,
        artifact_id=entry["source"]["artifact_id"],
        op="keep",
        source="user",
        actor=actor,
        session=session,
        detail={"kind": "library_publish", "library_id": library_id},
        after_hash=preview or next(iter(objects.values()), None),
    )
    return entry


def list_entries(
    kind: str | None = None,
    tag: str | None = None,
    query: str | None = None,
    project: str | None = None,
) -> list[dict]:
    """The index, newest first, deduped by library_id (latest wins).
    ``project`` matches the source pack path or world name — the
    global-vs-project view toggle."""
    seen: dict[str, dict] = {}
    for e in _entries():
        lid = e.get("library_id")
        if lid:
            seen[lid] = e
    out = sorted(seen.values(), key=lambda e: str(e.get("ts", "")), reverse=True)
    if kind:
        out = [e for e in out if e.get("kind") == kind]
    if tag:
        out = [e for e in out if tag in (e.get("tags") or [])]
    if query:
        q = query.lower()
        out = [e for e in out if q in str(e.get("name", "")).lower()]
    if project:
        # Accept a pack PATH (normalized comparison — cradle passes its open
        # worldPath) or a substring of the path/world title.
        p_real = os.path.realpath(os.path.expanduser(project)).rstrip("/").lower()
        p_sub = project.lower()

        def _match(e: dict) -> bool:
            src = e.get("source") or {}
            pack_path = str(src.get("pack", ""))
            return (
                os.path.realpath(pack_path).rstrip("/").lower() == p_real
                or p_sub in pack_path.lower()
                or p_sub in str(src.get("world", "")).lower()
            )

        out = [e for e in out if _match(e)]
    return out


def _rewrite_paths(value: Any, old: str, new: str) -> Any:
    """Recursively rewrite path prefixes inside an animation block."""
    if isinstance(value, str):
        return value.replace(old, new)
    if isinstance(value, dict):
        return {k: _rewrite_paths(v, old, new) for k, v in value.items()}
    if isinstance(value, list):
        return [_rewrite_paths(v, old, new) for v in value]
    return value


def import_entry(
    pack_dir: str | Path,
    library_id: str,
    *,
    new_id: str | None = None,
    into: str | None = None,
    actor: str = "user",
    session: str | None = None,
) -> dict:
    """Import a library entry into a pack. Fresh ids always (never
    overwrite); ``into`` targets tile/backdrop kinds
    (``tile:<stage>/<name>`` / ``backdrop:<stage>/<i>``)."""
    from canon.adapters.platformer_write import _pack_adapter, replace_asset

    entry = next(
        (e for e in list_entries() if e.get("library_id") == library_id), None
    )
    if entry is None:
        raise FileNotFoundError(f"library entry {library_id!r} not found")
    pack = Path(pack_dir)
    kind = entry["kind"]
    objects: dict[str, str] = entry.get("objects") or {}
    lib_detail = {
        "kind": "library_import",
        "library_id": library_id,
        "source_pack": (entry.get("source") or {}).get("pack", ""),
        "source_artifact": (entry.get("source") or {}).get("artifact_id", ""),
    }

    if kind in ("enemy_def", "item_def"):
        family = "enemy" if kind == "enemy_def" else "item"
        row = json.loads(read_object(objects["row"]).decode("utf-8"))
        id_field = f"{family}_id"
        base = new_id or str(row.get(id_field) or entry["name"]).strip() or "imported"
        existing = {p.stem for p in (pack / family).glob("*.json")}
        nid, counter = base, 2
        while nid in existing:
            nid = f"{base}_{counter}"
            counter += 1
        adapter = _pack_adapter(pack)
        old_dir = str(Path(str(row.get("sprite_path") or f"sprite/{family}/{base}/base.png")).parent)
        new_dir = f"sprite/{family}/{nid}"
        # Bytes first: base sprite + the whole animation bundle.
        if "sprite" in objects:
            sprite_hash = adapter.write_binary(
                f"{new_dir}/base.png", read_object(objects["sprite"])
            )
            row["sprite_path"] = f"{new_dir}/base.png"
            row["sprite_hash"] = sprite_hash
        for key, obj_hash in objects.items():
            if key.startswith("file:"):
                fname = key[5:]
                data = read_object(obj_hash)
                # Playback manifests (frames.json / atlas.json) embed
                # pack-relative paths into the SOURCE id's dir, and both
                # play surfaces read THOSE, not stats.animation — a verbatim
                # copy animates the wrong actor (or silently goes static).
                if fname.endswith(".json") and old_dir != new_dir:
                    try:
                        content = json.loads(data.decode("utf-8"))
                        data = json.dumps(
                            _rewrite_paths(content, old_dir, new_dir), indent=2
                        ).encode("utf-8")
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        pass
                adapter.write_binary(f"{new_dir}/{fname}", data)
        row[id_field] = nid
        row["artifact_id"] = f"{family}:{nid}"
        row["status"] = "user_edited"  # imported art survives regen cascades
        stats = row.setdefault("stats", {})
        if isinstance(stats.get("animation"), dict):
            stats["animation"] = _rewrite_paths(stats["animation"], old_dir, new_dir)
        stats["library_ref"] = {
            "library_id": library_id,
            "source_pack": lib_detail["source_pack"],
            "source_artifact": lib_detail["source_artifact"],
            "imported_row_hash": objects.get("row", ""),
        }
        rel = f"{family}/{nid}.json"
        adapter.write_json_singleton(rel, row)
        after = provenance.snapshot_file(pack, pack / rel)
        provenance.record(
            pack,
            artifact_id=f"{family}:{nid}",
            op="import",
            source="import",
            actor=actor,
            session=session,
            detail={**lib_detail, "id": nid},
            after_hash=after,
        )
        return {"kind": kind, "id": nid, "row": row, "library_id": library_id}

    if kind in ("tile", "backdrop"):
        if not into:
            raise ValueError(
                f"importing a {kind} needs --into "
                f"({'tile:<stage>/<name>' if kind == 'tile' else 'backdrop:<stage>/<index>'})"
            )
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp.write(read_object(objects["art"]))
            tmp_path = tmp.name
        try:
            # ONE journal event: replace_asset installs + records, with the
            # library provenance folded into its detail.
            result = replace_asset(
                pack, into, tmp_path, actor=actor, session=session,
                extra_detail={**lib_detail, "into": into},
            )
        finally:
            os.unlink(tmp_path)
        return {"kind": kind, "into": into, "library_id": library_id, **result}

    if kind == "player_skin":
        adapter = _pack_adapter(pack)
        # This kind REPLACES art in place (the player is a singleton) — the
        # old bytes must be recoverable, like every other overwriting verb.
        before = provenance.snapshot_file(pack, pack / "sprite/player/base.png")
        if "sprite" in objects:
            adapter.write_binary(
                "sprite/player/base.png", read_object(objects["sprite"])
            )
        for key, obj_hash in objects.items():
            if key.startswith("file:"):
                adapter.write_binary(
                    f"sprite/player/{key[5:]}", read_object(obj_hash)
                )
        after = provenance.snapshot_file(pack, pack / "sprite/player/base.png")
        provenance.record(
            pack,
            artifact_id="player",
            op="import",
            source="import",
            actor=actor,
            session=session,
            detail=lib_detail,
            before_hash=before,
            after_hash=after,
        )
        return {"kind": kind, "library_id": library_id}

    if kind == "audio":
        if not into or not into.startswith("audio:"):
            raise ValueError("importing audio needs --into audio:<stage>")
        stage_id = into.partition(":")[2]
        manifest_rel = f"audio/{stage_id}/manifest.json"
        audio_path = pack / manifest_rel
        if not audio_path.is_file():
            raise FileNotFoundError(f"stage {stage_id!r} has no audio manifest")
        adapter = _pack_adapter(pack)
        audio = _read_json(audio_path)
        # Land on the track path every consumer ALREADY points at (play
        # surfaces read the top-level manifest's audio block, not this
        # per-stage manifest) — falling back to the generation convention.
        filename = str((entry.get("meta") or {}).get("filename", "theme.mp3"))
        ext = Path(filename).suffix or ".mp3"
        rel = str(audio.get("music_path") or f"music/{stage_id}/theme{ext}")
        before = provenance.snapshot_file(pack, pack / rel)
        data = read_object(objects["music"])
        music_hash = adapter.write_binary(rel, data)
        provenance.snapshot_bytes(pack, data)  # after_hash must resolve via object cat
        audio["music_path"] = rel
        audio["music_hash"] = music_hash
        audio["status"] = "user_edited"
        adapter.write_json_singleton(manifest_rel, audio)
        top = _read_json(pack / "manifest.json")
        block = (top.get("audio") or {}).get(stage_id)
        if isinstance(block, dict):
            block["music"] = rel
            adapter.write_json_singleton("manifest.json", top)
        provenance.record(
            pack,
            artifact_id=f"audio:{stage_id}",
            op="import",
            source="import",
            actor=actor,
            session=session,
            detail={**lib_detail, "into": into},
            before_hash=before,
            after_hash=music_hash,
        )
        return {"kind": kind, "into": into, "library_id": library_id}

    raise ValueError(f"unsupported library kind {kind!r}")

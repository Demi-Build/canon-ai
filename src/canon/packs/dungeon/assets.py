"""Every asset a dungeon pack ON DISK owns, as ``AssetJob`` records — the
planner behind ``canon asset generate --target missing`` (and a single
``<kind>:<id>`` reroll) for this template.

The pipeline plans its assets from the in-memory bible
(``AssetPhase.plan_image_jobs`` / ``plan_music_jobs`` / ``plan_sfx_jobs``).
A pack on disk has no bible to hand, so this module rebuilds the SAME plan
from what the pipeline emitted: the rows of every kind whose registry entry
declares an image asset (``EntityKind.asset`` — never a list of kind names),
the manifest's rooms (environment portraits) and fixed portraits, and the
music / SFX catalogs ``AssetPhase`` itself ships. Every filename comes from
``canon.pipeline.phases.asset``'s naming functions, every prompt from the same
rule the pipeline used, so a repaired asset is the one the run would have
made. Nothing here calls a backend or writes a file: the executor is
``AssetPhase.run_jobs`` and the write-back is ``portraits.backfill_portraits``.

Registered under ``canon.adapters.ASSET_PLANNERS["dungeon"]``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from canon.packs.dungeon.portraits import DEFAULT_PORTRAITS_DIR, image_asset_kinds
from canon.packs.rows import load_rows, read_json
from canon.pipeline.phases.asset import (
    FIXED_PORTRAITS,
    AssetJob,
    AssetPhase,
    environment_portrait_rel,
    fixed_portrait_rel,
    music_jobs,
    row_portrait_rel,
    sfx_jobs,
)

__all__ = ["plan_assets", "row_portrait_prompt"]

#: The manifest's ``rooms[]`` entries carry the room id under this key.
_ROOM_ID = "room_id"


def row_portrait_prompt(kind: str, row: dict[str, Any], *, index: int = 0) -> str:
    """The prompt the pipeline sent for this row's portrait.

    ``DatabasePhase`` builds each entity stub's ``name`` as the row's or
    ``"<kind>_<index>"`` and its ``lore`` as ``lore`` → ``desc`` →
    ``backstory`` (first present); ``AssetPhase`` prompts with that stub's
    ``lore`` or ``"a <kind>: <name>"``. A class archetype prompts with its
    ``portrait_prompt`` or ``"a <name>"``. Same chain, same fallbacks.
    """
    name = row.get("name") or f"{kind}_{index}"
    if kind == "class":
        return str(row.get("portrait_prompt") or f"a {name}")
    lore = row.get("lore") or row.get("desc") or row.get("backstory") or ""
    return str(lore or f"a {kind}: {name}")


def plan_assets(
    pack: str | Path,
    spec: Any,
    *,
    fallback: Any = None,
    portrait_size: tuple[int, int] | None = None,
) -> list[AssetJob]:
    """Every asset job the pack at *pack* owns, in the pipeline's own order:
    row portraits (per registry kind), environment portraits (per manifest
    room), the fixed portraits, then the music and SFX catalogs with one
    per-environment track / ambience each.

    *spec* is the pack's effective registry (``resolve_pack(...).spec``);
    *fallback* the seed for the read-both shim ``image_asset_kinds`` applies.
    *portrait_size* defaults to ``AssetPhase``'s.
    """
    pack = Path(pack)
    width, height = portrait_size or AssetPhase(
        skip_image=True, skip_music=True, skip_sfx=True
    ).portrait_size
    size = {"width": width, "height": height}
    jobs: list[AssetJob] = []

    for entity, _field in image_asset_kinds(spec, fallback):
        try:
            rows = load_rows(pack, entity)
        except ValueError:
            continue
        for position, (key, row) in enumerate(rows.items()):
            if not isinstance(row, dict):
                continue
            rel = row_portrait_rel(
                entity.kind, index=position, row_id=key, name=row.get("name"),
                portraits_dir=DEFAULT_PORTRAITS_DIR,
            )
            jobs.append(AssetJob(
                "image", f"{entity.kind}:{key}", rel,
                row_portrait_prompt(entity.kind, row, index=position), dict(size),
            ))

    manifest = read_json(pack / "manifest.json")
    rooms = manifest.get("rooms") if isinstance(manifest, dict) else None
    environments: list[str] = []
    for index, room in enumerate(rooms if isinstance(rooms, list) else []):
        if not isinstance(room, dict):
            continue
        env = str(room.get("environment") or "")
        if env and env not in environments:
            environments.append(env)
        room_id = str(room.get(_ROOM_ID) or f"room_{index}")
        jobs.append(AssetJob(
            "image", f"room:{room_id}",
            environment_portrait_rel(index, portraits_dir=DEFAULT_PORTRAITS_DIR),
            f"a {env}: {room.get('environment_name') or room_id}", dict(size),
        ))

    for _key, stem, prompt in FIXED_PORTRAITS:
        jobs.append(AssetJob(
            "image", f"portrait:{stem}",
            fixed_portrait_rel(stem, portraits_dir=DEFAULT_PORTRAITS_DIR), prompt, dict(size),
        ))

    jobs.extend(music_jobs(environments))
    jobs.extend(sfx_jobs(environments))
    return jobs

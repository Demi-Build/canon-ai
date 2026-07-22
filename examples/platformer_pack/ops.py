"""Single-entity generation ops on an existing OUTPUT PACK (cradle's seam).

The pipeline phases generate whole pools inside a live run; these ops let an
editor create/complete ONE database row or (re)generate ONE asset against a
pack that already exists on disk. The trick that keeps this duplication-free:

- context is RECONSTRUCTED from the output tree (manifest.json + world.json +
  stage/tileset/enemy/item JSONs) into a real ``PipelineContext`` — the only
  pipeline-run-only input is the stage ``roster_brief`` scratch, which degrades
  to ``""``;
- asset generation calls the REAL phases (SpriteArtPhase / SpriteAnimationPhase
  / BackdropArtPhase / AudioPhase) on a bible filtered to the target, with
  everything else pin-suppressed — same prompts, same fallbacks, same
  provenance stamping as a pipeline run;
- data generation lifts the enemy/item loop bodies with ANCHORING: any field
  the user set is locked (``roll_skeleton(..., locked=...)``) and dependent
  lookups resolve from the anchors, so skeleton-driven generation bends around
  human choices instead of overwriting them.

Every op journals to ``.canon/journal.jsonl`` with a ``gen`` block (models
used) — the provenance trail for training data.
"""

from __future__ import annotations

import json
import random
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from canon import provenance
from canon.adapters.platformer_write import _pack_adapter
from canon.bible.artifacts import make_artifact_id
from canon.bible.models import Bible
from canon.bible.platformer import (
    EnemyDefinition,
    ItemDefinition,
    PlayerDefinition,
    Stage,
    Tileset,
    World,
)
from canon.llm.client import LLMClient
from canon.pipeline.rng import derive_rng
from canon.pipeline.runner import PipelineContext
from canon.skeleton.core import roll_skeleton
from canon.skeleton.loader import load_skeleton_spec

from examples.platformer_pack import phases as P
from examples.platformer_pack.graphics import DEFAULT_GRAPHICS, GraphicsSpec
from examples.platformer_pack.prompts import PlatformerPrompts
from examples.platformer_pack.tiles import DEFAULT_TILES

SCHEMAS_DIR = Path(__file__).parent / "schemas"

#: The generic DB registry — what cradle's "+ new row" is driven by. Each
#: entry: where rows live, the skeleton schema, which fields the LLM authors,
#: and which are rolled in code (archetype-dependent rolls the declarative
#: schema can't express yet). MazeWorld types join this table when their specs
#: land as JSON (PRD §4 / v1.5 loader).
DB_TYPES: dict[str, dict] = {
    "enemy": {
        "dir": "enemy",
        "schema": SCHEMAS_DIR / "enemy.json",
        "id_field": "enemy_id",
        "llm_fields": ["name", "flavor"],
        "code_fields": [
            "habitats", "swim_style", "hop_height", "hop_period_s",
            "placeholder_color",
        ],
        "phase_label": "plat:enemies",
    },
    "item": {
        "dir": "item",
        "schema": SCHEMAS_DIR / "item.json",
        "id_field": "item_id",
        "llm_fields": ["name", "flavor"],
        "code_fields": ["placeholder_color"],
        "phase_label": "plat:items",
    },
}


# ---------------------------------------------------------------------------
# Pack context reconstruction
# ---------------------------------------------------------------------------


def _read(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def load_pack(pack_dir: str | Path) -> SimpleNamespace:
    """Everything the ops need from the output tree, parsed once."""
    pack = Path(pack_dir)
    manifest_path = pack / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"not a platformer pack (no manifest.json): {pack}")
    manifest = _read(manifest_path)
    world = None
    if (pack / "world.json").is_file():
        world = World.model_validate(_read(pack / "world.json"))
    stages: dict[str, Stage] = {}
    tilesets: dict[str, Tileset] = {}
    stage_root = pack / "stage"
    if stage_root.is_dir():
        for sd in sorted(stage_root.iterdir()):
            sj = sd / "stage.json"
            if sj.is_file():
                stage = Stage.model_validate(_read(sj))
                stages[stage.stage_id] = stage
    ts_root = pack / "tileset"
    if ts_root.is_dir():
        for td in sorted(ts_root.iterdir()):
            tj = td / "manifest.json"
            if tj.is_file():
                ts = Tileset.model_validate(_read(tj))
                tilesets[ts.stage_id] = ts
    palettes = dict(manifest.get("palettes") or {})
    if not palettes:
        palettes = {sid: dict(ts.palette) for sid, ts in tilesets.items()}
    graphics = (
        GraphicsSpec.model_validate(manifest["graphics"])
        if manifest.get("graphics")
        else DEFAULT_GRAPHICS
    )
    return SimpleNamespace(
        pack=pack,
        manifest=manifest,
        seed=str(manifest.get("seed", "")),
        world=world,
        stages=stages,
        tilesets=tilesets,
        palettes=palettes,
        graphics=graphics,
    )


def _load_defs(pack: Path, kind: str) -> dict[str, Any]:
    model = EnemyDefinition if kind == "enemy" else ItemDefinition
    out: dict[str, Any] = {}
    d = pack / DB_TYPES[kind]["dir"]
    if d.is_dir():
        for f in sorted(d.glob("*.json")):
            try:
                entity = model.model_validate(_read(f))
                out[getattr(entity, DB_TYPES[kind]["id_field"])] = entity
            except Exception:  # noqa: BLE001 — a hand-broken row shouldn't block ops
                continue
    return out


def make_ctx(
    info: SimpleNamespace,
    *,
    llm: LLMClient | None = None,
    bible: Bible | None = None,
) -> PipelineContext:
    """A real PipelineContext over the reconstructed pack state."""
    if bible is None:
        bible = Bible.empty(info.seed)
        bible.world = info.world
        bible.stages.update(info.stages)
        bible.tilesets.update(info.tilesets)
    config = SimpleNamespace(
        seed=info.seed, output_dir=str(info.pack), max_retries=3
    )
    return PipelineContext(
        bible=bible,
        config=config,
        rng=random.Random(0),
        llm=llm,
        prompts=PlatformerPrompts(),
        artifacts={"palettes": info.palettes, "warnings": []},
        adapter=_pack_adapter(info.pack),
    )


def _warnings(ctx: PipelineContext) -> list[str]:
    return list(ctx.artifacts.get("slice_warnings", []) or [])


# ---------------------------------------------------------------------------
# Backend builders (explicit, fail-fast — same doctrine as the runner)
# ---------------------------------------------------------------------------


def build_llm(kind: str | None, model: str | None = None) -> LLMClient | None:
    if not kind or kind == "none":
        return None
    if kind == "fake":
        from canon.backends.testing import FakeLLMBackend
        from examples.run_platformer_slice import make_fake_responder

        return LLMClient(backend=FakeLLMBackend(make_fake_responder()))
    if kind == "anthropic":
        import os

        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise ValueError(
                "--llm-backend anthropic needs ANTHROPIC_API_KEY in the "
                "environment (pass --env-file or export it first)."
            )
        from canon.backends.anthropic import AnthropicBackend

        from examples.platformer_pack.models import load_models

        backend = AnthropicBackend(model=model) if model else AnthropicBackend()
        try:
            resolver = load_models().resolve
        except Exception:  # noqa: BLE001 — table optional
            resolver = None
        return LLMClient(backend=backend, model_resolver=resolver)
    raise ValueError(f"unknown llm backend {kind!r} (fake|anthropic).")


def _llm_model(llm: LLMClient | None, label: str) -> str:
    if llm is None:
        return ""
    resolver = getattr(llm, "model_resolver", None)
    backend = getattr(llm, "backend", None)
    if resolver and getattr(backend, "supports_request_model", False):
        model = resolver(label)
        if model:
            return str(model)
    return str(getattr(backend, "model", "fake"))


# ---------------------------------------------------------------------------
# db types — the registry serialized for form UIs
# ---------------------------------------------------------------------------


def db_types(pack_dir: str | Path) -> dict:
    out: dict[str, Any] = {}
    for kind, meta in DB_TYPES.items():
        spec = load_skeleton_spec(meta["schema"])
        fields = []
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
        out[kind] = {
            "dir": meta["dir"],
            "id_field": meta["id_field"],
            "skeleton_fields": fields,
            "llm_fields": meta["llm_fields"],
            "code_fields": meta["code_fields"],
        }
    return out


# ---------------------------------------------------------------------------
# db new / complete — anchored row generation
# ---------------------------------------------------------------------------


def _anchors_for_spec(spec, fields: dict) -> dict:
    return {
        k: v for k, v in (fields or {}).items() if k in spec.fields and v is not None
    }


def _enemy_row(
    info: SimpleNamespace,
    ctx: PipelineContext,
    index: int,
    fields: dict,
    complete: bool,
) -> EnemyDefinition:
    """The EnemyGeneratorPhase loop body, anchored by user fields."""
    fields = fields or {}
    spec = load_skeleton_spec(DB_TYPES["enemy"]["schema"])
    anchors = _anchors_for_spec(spec, fields)
    seed = info.seed
    skeleton = roll_skeleton(
        spec, derive_rng(seed, "plat:enemies", index), locked=anchors
    )
    rarity = str(skeleton.get("rarity", "common"))
    stages = list(info.stages.values())
    biomes = [s.biome for s in stages if s.biome]

    habitats = fields.get("habitats") or P.roll_habitats(
        rarity, biomes, derive_rng(seed, "plat:enemies:habitat", index)
    )
    swim_style = str(fields.get("swim_style") or "")
    if not swim_style and skeleton["archetype"] == "swimmer":
        styles, weights = zip(*P.SWIM_STYLES)
        swim_style = derive_rng(seed, "plat:enemies:swim", index).choices(
            styles, weights=weights
        )[0]
    hop_height = int(fields.get("hop_height") or 0)
    hop_period_s = float(fields.get("hop_period_s") or 0.0)
    if not hop_height and skeleton["archetype"] == "hopper":
        hop_rng = derive_rng(seed, "plat:enemies:hop", index)
        hop_height = int(hop_rng.randint(2, 3))
        hop_period_s = round(0.8 + 0.8 * float(hop_rng.random()), 2)

    if habitats == ["*"]:
        theme = info.world.title if info.world else ""
    else:
        home = next((s for s in stages if s.biome in habitats), None)
        theme = home.theme if home else ""

    used_names = [e.name for e in _load_defs(info.pack, "enemy").values()]
    anchored_name = str(fields.get("name") or "").strip()
    flavor = str(fields.get("flavor") or "")
    if complete and ctx.llm is not None:
        habitat_desc = (
            "roams EVERY biome of the world"
            if habitats == ["*"]
            else f"native to the {', '.join(habitats)} biome(s) only"
        )
        data = P.llm_json(
            ctx,
            f"plat:enemies:{index}",
            lambda fb: ctx.prompts.enemy_generation(
                skeleton, theme, "", index,
                used_names=list(used_names), feedback=fb,
                rarity=rarity, habitat_desc=habitat_desc,
            ),
            required_keys=("name",),
            fallback={"name": anchored_name or f"Enemy {index}", "flavor": flavor},
            validate_obj=lambda obj: (
                [
                    f"Name {obj.get('name')!r} is already taken; invent a "
                    "clearly different one."
                ]
                if not anchored_name
                and str(obj.get("name", "")).strip().lower()
                in {n.lower() for n in used_names}
                else []
            ),
        )
        name = anchored_name or str(data["name"])
        flavor = flavor or str(data.get("flavor", ""))
    else:
        name = anchored_name or f"New Enemy {index}"

    enemy_id = str(fields.get("enemy_id") or P.slugify(name))
    existing_ids = set(_load_defs(info.pack, "enemy"))
    base, counter = enemy_id, 2
    while enemy_id in existing_ids:
        enemy_id = f"{base}_{counter}"
        counter += 1

    patrol_range = int(skeleton["patrol_range"])
    aggro_mult = float(skeleton.get("aggro_mult", 0) or 0)
    leash_mult = float(skeleton.get("leash_mult", 0) or 0)
    behavior = {
        "patrol_range": patrol_range,
        "aggro_range": round(aggro_mult * patrol_range),
        "leash_range": 0 if leash_mult < 0 else round(leash_mult * patrol_range),
    }
    if swim_style:
        behavior["swim_style"] = swim_style
    if hop_height:
        behavior["hop_height"] = hop_height
        behavior["hop_period_s"] = hop_period_s

    reserved: list[tuple[float, float]] = []
    for palette in info.palettes.values():
        reserved.extend(P.reserved_hue_bands(palette, DEFAULT_TILES))
    reserved_bands = tuple(dict.fromkeys(reserved)) or P.DEFAULT_RESERVED_HUES
    bg_lums = P.background_luminances(info.palettes, DEFAULT_TILES)
    color = str(
        fields.get("placeholder_color")
        or P.placeholder_color(index, reserved_bands, bg_lums)
    )

    return EnemyDefinition(
        artifact_id=make_artifact_id("enemy", enemy_id),
        enemy_id=enemy_id,
        name=name,
        archetype=str(skeleton["archetype"]),
        size=float(skeleton.get("size", 1.0)),
        rarity=rarity,
        habitats=habitats,
        stats={
            "hp": skeleton["hp"],
            "damage": skeleton["damage"],
            "speed": skeleton["speed"],
            "flavor": flavor,
            "placeholder_color": color,
        },
        behavior=behavior,
        parents=[
            make_artifact_id("world"),
            *(
                s.artifact_id
                for s in stages
                if habitats != ["*"] and s.biome in habitats
            ),
        ],
    )


def _item_row(
    info: SimpleNamespace,
    ctx: PipelineContext,
    index: int,
    fields: dict,
    complete: bool,
) -> ItemDefinition:
    """The ItemGeneratorPhase loop body, anchored by user fields."""
    fields = fields or {}
    spec = load_skeleton_spec(DB_TYPES["item"]["schema"])
    anchors = _anchors_for_spec(spec, fields)
    skeleton = roll_skeleton(
        spec, derive_rng(info.seed, "plat:items", index), locked=anchors
    )
    kind = str(skeleton["kind"])
    params = {
        key: skeleton[key]
        for key in ("duration_s", "heal_amount", "coin_value", "boost_mult")
        if skeleton.get(key)
    }
    world_title = info.world.title if info.world else ""
    used_names = [i.name for i in _load_defs(info.pack, "item").values()]
    anchored_name = str(fields.get("name") or "").strip()
    flavor = str(fields.get("flavor") or "")
    if complete and ctx.llm is not None:
        data = P.llm_json(
            ctx,
            f"plat:items:{index}",
            lambda fb: ctx.prompts.item_generation(
                skeleton, world_title, index,
                used_names=list(used_names), feedback=fb,
            ),
            required_keys=("name",),
            fallback={"name": anchored_name or f"Item {index}", "flavor": flavor},
            validate_obj=None,
        )
        name = anchored_name or str(data["name"])
        flavor = flavor or str(data.get("flavor", ""))
    else:
        name = anchored_name or f"New Item {index}"

    item_id = str(fields.get("item_id") or P.slugify(name))
    existing_ids = set(_load_defs(info.pack, "item"))
    base, counter = item_id, 2
    while item_id in existing_ids:
        item_id = f"{base}_{counter}"
        counter += 1

    bg_lums = P.background_luminances(info.palettes, DEFAULT_TILES)
    color = str(
        fields.get("placeholder_color")
        or P.placeholder_color(index + 40, P.DEFAULT_RESERVED_HUES, bg_lums)
    )
    return ItemDefinition(
        artifact_id=make_artifact_id("item", item_id),
        item_id=item_id,
        name=name,
        kind=kind,
        rarity=str(skeleton.get("rarity", "common")),
        params=params,
        stats={"flavor": flavor, "placeholder_color": color},
        parents=[make_artifact_id("world")],
    )


def new_db_row(
    pack_dir: str | Path,
    entity_type: str,
    fields: dict | None = None,
    *,
    complete: bool = False,
    llm: LLMClient | None = None,
    actor: str = "user",
    session: str | None = None,
) -> dict:
    """Create one anchored row: user fields are locked constraints, the
    skeleton rolls the rest, and (with ``complete``) the LLM authors its
    fields exactly as pipeline generation would."""
    if entity_type not in DB_TYPES:
        raise ValueError(f"unknown db type {entity_type!r} (one of {list(DB_TYPES)})")
    info = load_pack(pack_dir)
    ctx = make_ctx(info, llm=llm)
    index = len(_load_defs(info.pack, entity_type))
    builder = _enemy_row if entity_type == "enemy" else _item_row
    entity = builder(info, ctx, index, fields or {}, complete)
    entity_id = getattr(entity, DB_TYPES[entity_type]["id_field"])
    rel = f"{DB_TYPES[entity_type]['dir']}/{entity_id}.json"
    content_hash = ctx.adapter.write_json_singleton(
        rel, entity.model_dump(mode="json")
    )
    P.stamp_provenance(
        ctx, entity, content_hash,
        label=f"{DB_TYPES[entity_type]['phase_label']}:{index}",
    )
    after = provenance.snapshot_file(info.pack, info.pack / rel)
    label = f"{DB_TYPES[entity_type]['phase_label']}:{index}"
    provenance.record(
        info.pack,
        artifact_id=f"{entity_type}:{entity_id}",
        op="generate" if complete else "create",
        source="llm" if complete else "user",
        actor=actor,
        session=session,
        detail={
            "kind": "db_new",
            "type": entity_type,
            "locked": sorted((fields or {}).keys()),
        },
        after_hash=after,
        gen={"llm_model": _llm_model(llm, label)} if complete else None,
    )
    return {
        "type": entity_type,
        "id": entity_id,
        "row": entity.model_dump(mode="json"),
        "completed": complete,
        "warnings": _warnings(ctx),
    }


def complete_db_row(
    pack_dir: str | Path,
    entity_type: str,
    entity_id: str,
    locked: list[str] | None = None,
    *,
    reroll: bool = False,
    llm: LLMClient | None = None,
    actor: str = "user",
    session: str | None = None,
) -> dict:
    """LLM-complete an EXISTING row. ``locked`` fields are preserved as
    constraints; with ``reroll`` the unlocked mechanical fields re-roll too."""
    if entity_type not in DB_TYPES:
        raise ValueError(f"unknown db type {entity_type!r} (one of {list(DB_TYPES)})")
    info = load_pack(pack_dir)
    rel = f"{DB_TYPES[entity_type]['dir']}/{entity_id}.json"
    path = info.pack / rel
    if not path.is_file():
        raise FileNotFoundError(f"{entity_type} {entity_id!r} not found")
    current = _read(path)
    locked = list(locked or [])
    before = provenance.snapshot_file(info.pack, path)

    # Rebuild the row through the anchored builder: locked fields (plus the
    # stable id) carry over; everything else re-rolls / re-authors.
    fields: dict[str, Any] = {DB_TYPES[entity_type]["id_field"]: entity_id}
    flat = dict(current)
    flat.update(current.get("stats") or {})
    flat.update(current.get("behavior") or {})
    for name in locked:
        if name in flat:
            fields[name] = flat[name]
    if not reroll:
        # Keep every existing mechanical value as an anchor; only the LLM
        # fields (and empties) change.
        spec = load_skeleton_spec(DB_TYPES[entity_type]["schema"])
        for name in spec.fields:
            if name in flat and flat.get(name) is not None and name not in fields:
                fields[name] = flat[name]
        for name in DB_TYPES[entity_type]["code_fields"]:
            if flat.get(name) and name not in fields:
                fields[name] = flat[name]

    ctx = make_ctx(info, llm=llm)
    # Stable index: derive from position among existing ids so re-completion
    # doesn't shift the rng streams of other rows.
    existing = list(_load_defs(info.pack, entity_type))
    index = existing.index(entity_id) if entity_id in existing else len(existing)
    builder = _enemy_row if entity_type == "enemy" else _item_row
    entity = builder(info, ctx, index, fields, complete=True)

    # The id must not drift on completion.
    if getattr(entity, DB_TYPES[entity_type]["id_field"]) != entity_id:
        data = entity.model_dump(mode="json")
        data[DB_TYPES[entity_type]["id_field"]] = entity_id
        data["artifact_id"] = f"{entity_type}:{entity_id}"
        model = EnemyDefinition if entity_type == "enemy" else ItemDefinition
        entity = model.model_validate(data)

    content_hash = ctx.adapter.write_json_singleton(
        rel, entity.model_dump(mode="json")
    )
    label = f"{DB_TYPES[entity_type]['phase_label']}:{index}"
    P.stamp_provenance(ctx, entity, content_hash, label=label)
    after = provenance.snapshot_file(info.pack, path)
    provenance.record(
        info.pack,
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
        gen={"llm_model": _llm_model(llm, label)},
    )
    return {
        "type": entity_type,
        "id": entity_id,
        "row": entity.model_dump(mode="json"),
        "warnings": _warnings(ctx),
    }


# ---------------------------------------------------------------------------
# asset generate / animate — the real phases on a filtered, pin-suppressed bible
# ---------------------------------------------------------------------------


def _parse_target(target: str) -> tuple[str, str]:
    kind, _, rest = target.partition(":")
    if kind in ("enemy", "item", "backdrop", "audio") and not rest:
        raise ValueError(f"target {target!r} needs an id (e.g. {kind}:<id>)")
    if kind not in ("enemy", "item", "player", "backdrop", "audio"):
        raise ValueError(
            f"unknown target {target!r} — enemy:<id> | item:<id> | player | "
            "backdrop:<stage> | audio:<stage>"
        )
    return kind, rest


def _sprite_bible(info: SimpleNamespace, kind: str, rest: str) -> Bible:
    """Bible filtered to the sprite target; everything else pin-suppressed."""
    bible = Bible.empty(info.seed)
    bible.world = info.world
    bible.stages.update(info.stages)
    bible.tilesets.update(info.tilesets)
    pins = [make_artifact_id("props", sid) for sid in info.stages]
    if kind == "enemy":
        defs = _load_defs(info.pack, "enemy")
        if rest not in defs:
            raise FileNotFoundError(f"enemy {rest!r} not found")
        bible.enemy_definitions[rest] = defs[rest]
        pins.append("player")
    elif kind == "item":
        defs = _load_defs(info.pack, "item")
        if rest not in defs:
            raise FileNotFoundError(f"item {rest!r} not found")
        bible.items[rest] = defs[rest]
        pins.append("player")
    elif kind == "player":
        base = info.pack / "sprite/player/base.png"
        if base.is_file():
            bible.player = PlayerDefinition(
                artifact_id="player", sprite_path="sprite/player/base.png"
            )
        # SpriteArtPhase creates the PlayerDefinition when absent.
    bible.metadata.pinned = pins
    return bible


def _asset_paths(info: SimpleNamespace, kind: str, rest: str) -> list[str]:
    if kind == "enemy":
        return [f"sprite/enemy/{rest}/base.png"]
    if kind == "item":
        return [f"sprite/item/{rest}/base.png"]
    if kind == "player":
        return ["sprite/player/base.png"]
    if kind == "backdrop":
        return [f"backdrop/{rest}/manifest.json"]
    return [f"audio/{rest}/manifest.json"]


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
    actor: str = "user",
    session: str | None = None,
) -> dict:
    """(Re)generate ONE asset by running the real art/audio phase against a
    bible filtered to the target."""
    info = load_pack(pack_dir)
    kind, rest = _parse_target(target)
    primary = _asset_paths(info, kind, rest)[0]
    before = provenance.snapshot_file(info.pack, info.pack / primary)
    gen: dict[str, Any] = {}

    if kind in ("enemy", "item", "player"):
        from examples.platformer_pack.art_phases import SpriteArtPhase
        from examples.platformer_pack.tileset_art import build_image_producer

        if not image_backend or image_backend == "none":
            raise ValueError("sprite targets need --image-backend (fake|fal|…)")
        producer = build_image_producer(
            image_backend, image_model, image_edit_model,
            seed=info.seed, edit_kind=image_edit_backend,
        )
        bible = _sprite_bible(info, kind, rest)
        ctx = make_ctx(info, bible=bible)
        SpriteArtPhase(producer=producer, graphics=info.graphics).run(ctx)
        gen["image_model"] = str(producer.model)
    elif kind == "backdrop":
        from examples.platformer_pack.art_phases import BackdropArtPhase
        from examples.platformer_pack.tileset_art import build_image_producer

        if rest not in info.stages:
            raise FileNotFoundError(f"stage {rest!r} not found")
        if not image_backend or image_backend == "none":
            raise ValueError("backdrop targets need --image-backend (fake|fal|…)")
        producer = build_image_producer(
            image_backend, image_model, image_edit_model,
            seed=info.seed, edit_kind=image_edit_backend,
        )
        bible = Bible.empty(info.seed)
        bible.world = info.world
        bible.stages[rest] = info.stages[rest]
        bible.tilesets.update(
            {rest: info.tilesets[rest]} if rest in info.tilesets else {}
        )
        ctx = make_ctx(info, bible=bible)
        BackdropArtPhase(producer=producer, graphics=info.graphics).run(ctx)
        gen["image_model"] = str(producer.model)
    else:  # audio:<stage>
        from examples.platformer_pack.audio_phases import (
            AudioPhase,
            build_music_producer,
            build_sfx_producer,
        )

        if rest not in info.stages:
            raise FileNotFoundError(f"stage {rest!r} not found")
        music = build_music_producer(music_backend)
        sfx = build_sfx_producer(sfx_backend)
        if music is None and sfx is None:
            raise ValueError(
                "audio targets need --music-backend and/or --sfx-backend"
            )
        bible = Bible.empty(info.seed)
        bible.world = info.world
        bible.stages[rest] = info.stages[rest]
        ctx = make_ctx(info, bible=bible)
        AudioPhase(music_producer=music, sfx_producer=sfx).run(ctx)
        if music is not None:
            gen["music_model"] = str(getattr(music, "model", type(music).__name__))
        if sfx is not None:
            gen["sfx_model"] = str(getattr(sfx, "model", type(sfx).__name__))

    after = provenance.snapshot_file(info.pack, info.pack / primary)
    provenance.record(
        info.pack,
        artifact_id=target,
        op="regenerate" if before else "generate",
        source="llm",
        actor=actor,
        session=session,
        detail={"kind": "asset_generate"},
        before_hash=before,
        after_hash=after,
        gen=gen,
    )
    return {
        "target": target,
        "generated": after is not None and after != before,
        "gen": gen,
        "warnings": _warnings(ctx),
    }


def animate_asset(
    pack_dir: str | Path,
    target: str,
    *,
    image_backend: str | None = None,
    image_model: str | None = None,
    image_edit_model: str | None = None,
    image_edit_backend: str | None = None,
    vlm_backend: str | None = None,
    vlm_model: str | None = None,
    reuse_spec: bool = False,
    actor: str = "user",
    session: str | None = None,
) -> dict:
    """The multi-image path: VLM-authored motion spec + one img2img sheet per
    state, sliced into strips + frames.json + a packed atlas — for ONE actor."""
    from examples.platformer_pack.art_phases import SpriteAnimationPhase
    from examples.platformer_pack.tileset_art import build_image_producer
    from examples.platformer_pack.vlm_qa import (
        ANIM_FRAMES_MAX,
        PLAYER_ANIM_FRAMES_MAX,
        PLAYER_ANIMATION_STATES,
        build_vlm_judge,
        enemy_animation_states,
        enemy_animation_subject,
    )

    info = load_pack(pack_dir)
    kind, rest = _parse_target(target)
    if kind not in ("enemy", "player"):
        raise ValueError("animate targets: enemy:<id> | player")
    if not image_backend or image_backend == "none":
        raise ValueError("animate needs --image-backend with img2img support")
    producer = build_image_producer(
        image_backend, image_model, image_edit_model,
        seed=info.seed, edit_kind=image_edit_backend,
    )
    judge = build_vlm_judge(vlm_backend or "none", vlm_model)
    if judge is None and not reuse_spec:
        raise ValueError(
            "animate needs --vlm-backend (or --reuse-spec with a stored spec)"
        )

    bible = _sprite_bible(info, kind, rest)
    ctx = make_ctx(info, bible=bible)
    phase = SpriteAnimationPhase(
        producer=producer, judge=judge, graphics=info.graphics
    )

    if kind == "enemy":
        enemy = bible.enemy_definitions[rest]
        actor_id = f"enemy:{rest}"
        sprite_path = enemy.sprite_path
        stored = (enemy.stats.get("animation") or {}).get("spec")
        subject = enemy_animation_subject(enemy)
        states = enemy_animation_states(enemy)
        frames_max = ANIM_FRAMES_MAX
        asymmetric = bool(getattr(enemy, "asymmetric", False))
    else:
        player = bible.player
        if player is None or not player.sprite_path:
            raise FileNotFoundError("no player sprite (sprite/player/base.png)")
        actor_id = "player"
        sprite_path = player.sprite_path
        stored = (getattr(player, "animation", None) or {}).get("spec")
        from examples.platformer_pack.art_phases import PLAYER_DESCRIPTOR

        subject = (
            f"Character: the PLAYER hero — {PLAYER_DESCRIPTOR}. A small "
            f"bouncy platformer mascot, side view facing right."
        )
        states = PLAYER_ANIMATION_STATES
        frames_max = PLAYER_ANIM_FRAMES_MAX
        asymmetric = bool(getattr(player, "asymmetric", False))

    if not sprite_path:
        raise FileNotFoundError(f"{target} has no base sprite — generate one first")

    before = provenance.snapshot_file(
        info.pack, info.pack / Path(sprite_path).parent / "frames.json"
    )
    if reuse_spec and stored:
        result = phase._animate_actor(ctx, sprite_path, stored, actor_id, asymmetric)
        manifest = {"spec": stored, **result} if result.get("states") else {}
    else:
        manifest = phase._animate_one(
            ctx, actor_id, sprite_path, subject, states, frames_max,
            asymmetric=asymmetric,
        )

    if manifest and kind == "enemy":
        enemy.stats["animation"] = manifest
        ctx.adapter.write_json_singleton(
            f"enemy/{rest}.json", enemy.model_dump(mode="json")
        )
    after = provenance.snapshot_file(
        info.pack, info.pack / Path(sprite_path).parent / "frames.json"
    )
    gen = {
        "image_model": str(producer.model),
        "vlm_model": str(getattr(judge, "model", "")) if judge else "",
        "reused_spec": bool(reuse_spec and stored),
    }
    provenance.record(
        info.pack,
        artifact_id=target,
        op="regenerate" if before else "generate",
        source="llm",
        actor=actor,
        session=session,
        detail={
            "kind": "asset_animate",
            "states": sorted((manifest.get("states") or {}).keys()),
        },
        before_hash=before,
        after_hash=after,
        gen=gen,
    )
    return {
        "target": target,
        "animated": bool(manifest),
        "states": sorted((manifest.get("states") or {}).keys()),
        "gen": gen,
        "warnings": _warnings(ctx),
    }

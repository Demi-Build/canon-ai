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


def _schema_path(pack: str | Path, kind: str) -> Path:
    """The effective roll-table schema for a pack: a pack-local
    ``schemas/<kind>.json`` override (user-edited distributions travel with
    the game) wins over the repo default."""
    local = Path(pack) / "schemas" / f"{kind}.json"
    return local if local.is_file() else DB_TYPES[kind]["schema"]


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
        schema_path = _schema_path(pack_dir, kind)
        spec = load_skeleton_spec(schema_path)
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
            "schema_source": (
                "default" if schema_path == meta["schema"] else "pack"
            ),
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
    spec = load_skeleton_spec(_schema_path(info.pack, "enemy"))
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
        # Stash the (deterministic) prompt for the journal's gen block — the
        # lineage tree shows "its prompt if generated".
        ctx.artifacts["gen_prompt"] = ctx.prompts.enemy_generation(
            skeleton, theme, "", index,
            used_names=list(used_names), feedback=None,
            rarity=rarity, habitat_desc=habitat_desc,
        ).user_message
        fallback = {"name": anchored_name or f"Enemy {index}", "flavor": flavor}
        data = P.llm_json(
            ctx,
            f"plat:enemies:{index}",
            lambda fb: ctx.prompts.enemy_generation(
                skeleton, theme, "", index,
                used_names=list(used_names), feedback=fb,
                rarity=rarity, habitat_desc=habitat_desc,
            ),
            required_keys=("name",),
            fallback=fallback,
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
        ctx.artifacts["gen_fallback"] = data is fallback
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
    spec = load_skeleton_spec(_schema_path(info.pack, "item"))
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
        ctx.artifacts["gen_prompt"] = ctx.prompts.item_generation(
            skeleton, world_title, index,
            used_names=list(used_names), feedback=None,
        ).user_message
        fallback = {"name": anchored_name or f"Item {index}", "flavor": flavor}
        data = P.llm_json(
            ctx,
            f"plat:items:{index}",
            lambda fb: ctx.prompts.item_generation(
                skeleton, world_title, index,
                used_names=list(used_names), feedback=fb,
            ),
            required_keys=("name",),
            fallback=fallback,
            validate_obj=None,
        )
        ctx.artifacts["gen_fallback"] = data is fallback
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
        gen={
            "llm_model": _llm_model(llm, label),
            "prompt": str(ctx.artifacts.pop("gen_prompt", "") or ""),
            # Truthful training data: the prompt WAS sent, but the text came
            # from the fallback when retries exhausted.
            **(
                {"fallback": True}
                if ctx.artifacts.pop("gen_fallback", False)
                else {}
            ),
        }
        if complete
        else None,
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
        spec = load_skeleton_spec(_schema_path(info.pack, entity_type))
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
        gen={
            "llm_model": _llm_model(llm, label),
            "prompt": str(ctx.artifacts.pop("gen_prompt", "") or ""),
            **(
                {"fallback": True}
                if ctx.artifacts.pop("gen_fallback", False)
                else {}
            ),
        },
    )
    return {
        "type": entity_type,
        "id": entity_id,
        "row": entity.model_dump(mode="json"),
        "warnings": _warnings(ctx),
    }


# ---------------------------------------------------------------------------
# db update — DIRECT human edits (no rerolls, no LLM): row fields, tile-type
# gameplay knobs, and the roll-table schemas themselves
# ---------------------------------------------------------------------------

#: Flat-name routing for row edits: which nested dict each known knob lives
#: in. Anything else is either a top-level model field or needs an explicit
#: dotted path ("stats.custom_knob") to reach hand-added keys.
_UPDATE_NESTING: dict[str, dict[str, str]] = {
    "enemy": {
        **dict.fromkeys(
            ("hp", "damage", "speed", "flavor", "placeholder_color"), "stats"
        ),
        **dict.fromkeys(
            (
                "patrol_range", "aggro_range", "leash_range", "swim_style",
                "hop_height", "hop_period_s",
            ),
            "behavior",
        ),
    },
    "item": {
        **dict.fromkeys(
            ("duration_s", "heal_amount", "coin_value", "boost_mult"), "params"
        ),
        **dict.fromkeys(("flavor", "placeholder_color"), "stats"),
    },
}

#: Dotted-path containers each row shape allows.
_DICT_CONTAINERS: dict[str, tuple[str, ...]] = {
    "enemy": ("stats", "behavior"),
    "item": ("params", "stats"),
}

#: Identity / provenance / art plumbing — never editable through db update
#: (sprites change via ``canon asset replace``; animation via ``asset
#: animate``). Matched against the LAST path segment so dotted paths can't
#: sneak past (``stats.animation``).
_PROTECTED_FIELDS = frozenset({
    "artifact_id", "enemy_id", "item_id", "provenance_hash", "parents",
    "status", "review_status", "sprite_path", "sprite_hash", "animation",
})

_COLLISION_CATEGORIES = ("empty", "solid", "one_way", "hazard", "volume")

#: Slot params owned by generation/code, not user-tunable gameplay knobs:
#: autotile variant selection and the code-derived deep-water sibling flag.
_STRUCTURAL_PARAMS = frozenset({"autotile_mask", "water_deep"})


def update_db_row(
    pack_dir: str | Path,
    entity_type: str,
    entity_id: str,
    changes: dict,
    *,
    actor: str = "user",
    session: str | None = None,
) -> dict:
    """Apply direct human edits to an existing row — values land verbatim.

    ``changes`` maps flat field names to new values: known stat/behavior/
    params knobs route into their nested dicts, model fields land top-level,
    and dotted paths ("stats.custom") reach hand-added knobs; ``None``
    deletes a nested key. The result is validated through the entity model
    (fail-closed), rewritten + rehashed, stamped ``user_edited``, and
    journaled ``op:"edit"`` with the per-field diff — the (generated →
    human-corrected) training pair.
    """
    if entity_type not in DB_TYPES:
        raise ValueError(f"unknown db type {entity_type!r} (one of {list(DB_TYPES)})")
    if not isinstance(changes, dict) or not changes:
        raise ValueError("--set needs a non-empty JSON object of field: value")
    pack = Path(pack_dir)
    rel = f"{DB_TYPES[entity_type]['dir']}/{entity_id}.json"
    path = pack / rel
    if not path.is_file():
        raise FileNotFoundError(f"{entity_type} {entity_id!r} not found")
    model = EnemyDefinition if entity_type == "enemy" else ItemDefinition
    current = _read(path)
    updated = json.loads(json.dumps(current))  # deep copy
    nesting = _UPDATE_NESTING[entity_type]
    containers = _DICT_CONTAINERS[entity_type]
    spec = load_skeleton_spec(_schema_path(pack, entity_type))
    diff: dict[str, dict] = {}
    warnings: list[str] = []

    for name, value in changes.items():
        leaf = name.rsplit(".", 1)[-1]
        if leaf in _PROTECTED_FIELDS:
            raise ValueError(
                f"{name!r} is protected (identity/provenance/art plumbing — "
                "sprites change via `canon asset replace`)"
            )
        if name in containers:
            # Whole-container replaces would smuggle protected keys past the
            # wall (stats.animation, sprite hashes) and wipe siblings.
            raise ValueError(
                f"{name!r} is a container — edit knobs individually "
                f"('{name}.<key>' or their flat names)"
            )
        if "." in name:
            container, _, key = name.partition(".")
            if container not in containers or not key or "." in key:
                raise ValueError(
                    f"dotted path {name!r} must be <container>.<key> with "
                    f"container one of {list(containers)}"
                )
        elif name in nesting:
            container, key = nesting[name], name
        elif name in model.model_fields:
            container, key = "", name
        else:
            known = sorted(
                (set(nesting) | set(model.model_fields)) - _PROTECTED_FIELDS
            )
            raise ValueError(
                f"unknown field {name!r} for {entity_type} — one of {known}, "
                "or a dotted path like 'stats.custom'"
            )
        if not container:
            if value is None:
                raise ValueError(f"cannot delete top-level field {name!r}")
            old = updated.get(key)
            updated[key] = value
        else:
            bucket = updated.setdefault(container, {})
            old = bucket.get(key)
            if value is None:
                bucket.pop(key, None)
            else:
                bucket[key] = value
        if old != value:
            diff[name] = {"from": old, "to": value}

    # Off-table values are allowed (hand edits are authoritative) but
    # surfaced — generation would never roll them. Checked AFTER the whole
    # edit lands so lookups resolve against the row's final state (editing
    # size and hp together judges hp under the new size).
    flat_after: dict[str, Any] = {
        k: v for k, v in updated.items() if not isinstance(v, dict)
    }
    for bucket in containers:
        flat_after.update(updated.get(bucket) or {})
    for changed_name in diff:
        leaf = changed_name.rsplit(".", 1)[-1]
        field = spec.fields.get(leaf)
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
                dep_val = flat_after.get(field.depends_on)
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

    if not diff:
        return {
            "type": entity_type, "id": entity_id, "row": current,
            "changed": {}, "no_change": True, "warnings": warnings,
        }

    before = provenance.snapshot_file(pack, path)
    updated["status"] = "user_edited"
    entity = model.model_validate(updated)  # fail-closed shape check
    data = entity.model_dump(mode="json")
    for key, value in updated.items():  # keep hand-added top-level keys
        if key not in data:
            data[key] = value
    _pack_adapter(pack).write_json_singleton(rel, data)
    after = provenance.snapshot_file(pack, path)
    provenance.record(
        pack,
        artifact_id=f"{entity_type}:{entity_id}",
        op="edit",
        source="user",
        actor=actor,
        session=session,
        detail={"kind": "db_update", "type": entity_type, "changed": diff},
        before_hash=before,
        after_hash=after,
    )
    return {
        "type": entity_type, "id": entity_id, "row": data,
        "changed": diff, "warnings": warnings,
    }


def update_tile_slots(
    pack_dir: str | Path,
    target: str,
    changes: dict,
    *,
    actor: str = "user",
    session: str | None = None,
) -> dict:
    """Edit gameplay knobs on a tile TYPE: ``target`` = "<stage>/<tile_name>".

    ``changes`` accepts ``collision`` (category string) and ``params`` (dict
    merged into every slot of the name; ``None`` deletes a knob). Structural
    fields (px_region, autotile_mask, water_deep, …) belong to generation and
    are refused. All same-name slots update together — the same doctrine as
    tile reskins.
    """
    if not isinstance(changes, dict) or not changes:
        raise ValueError("--set needs a non-empty JSON object")
    pack = Path(pack_dir)
    stage_id, _, tile_name = str(target).partition("/")
    if not stage_id or not tile_name:
        raise ValueError(f"tile id {target!r} must be <stage>/<tile_name>")
    ts_rel = f"tileset/{stage_id}/manifest.json"
    ts_path = pack / ts_rel
    if not ts_path.is_file():
        raise FileNotFoundError(f"tileset for stage {stage_id!r} not found")
    unknown = set(changes) - {"collision", "params"}
    if unknown:
        raise ValueError(
            f"tile edits accept only 'collision' and 'params' (got "
            f"{sorted(unknown)}) — art changes go through `canon asset "
            "replace`, structure belongs to generation"
        )
    tileset = _read(ts_path)
    slots = [s for s in tileset.get("slots", []) if s.get("name") == tile_name]
    if not slots:
        names = sorted({s.get("name", "") for s in tileset.get("slots", [])})
        raise ValueError(
            f"tile {tile_name!r} not in the {stage_id} tileset (has {names})"
        )

    # Diffs consider EVERY same-name slot: a hand-edited manifest can leave
    # siblings divergent, and this op is exactly the convergence tool — a
    # slots[0]-only sample would report no_change and never write.
    diff: dict[str, dict] = {}
    collision = changes.get("collision")
    if collision is not None:
        if collision not in _COLLISION_CATEGORIES:
            raise ValueError(
                f"collision {collision!r} is not a category "
                f"{list(_COLLISION_CATEGORIES)}"
            )
        olds = sorted({str(s.get("collision", "")) for s in slots})
        if olds != [collision]:
            diff["collision"] = {
                "from": olds[0] if len(olds) == 1 else olds, "to": collision,
            }
        for slot in slots:
            slot["collision"] = collision
    params = changes.get("params")
    if params is not None:
        if not isinstance(params, dict):
            raise ValueError("'params' must be an object of knob: value")
        blocked = _STRUCTURAL_PARAMS & set(params)
        if blocked:
            raise ValueError(
                f"params {sorted(blocked)} are structural (generation-owned)"
            )
        for key, value in params.items():
            if value is None:
                needs_write = any(
                    key in (s.get("params") or {}) for s in slots
                )
            else:
                needs_write = any(
                    (s.get("params") or {}).get(key) != value for s in slots
                )
            if needs_write:
                diff[f"params.{key}"] = {
                    "from": (slots[0].get("params") or {}).get(key),
                    "to": value,
                }
        for slot in slots:
            bucket = slot.setdefault("params", {})
            for key, value in params.items():
                if value is None:
                    bucket.pop(key, None)
                else:
                    bucket[key] = value

    if not diff:
        return {
            "stage": stage_id, "tile": tile_name, "slots": len(slots),
            "changed": {}, "no_change": True,
        }

    before = provenance.snapshot_file(pack, ts_path)
    tileset["status"] = "user_edited"
    Tileset.model_validate(tileset)  # fail-closed shape check
    _pack_adapter(pack).write_json_singleton(ts_rel, tileset)
    after = provenance.snapshot_file(pack, ts_path)
    provenance.record(
        pack,
        artifact_id=f"tileset:{stage_id}",
        op="edit",
        source="user",
        actor=actor,
        session=session,
        detail={
            "kind": "tile_params", "tile": tile_name,
            "slots": len(slots), "changed": diff,
        },
        before_hash=before,
        after_hash=after,
    )
    return {
        "stage": stage_id, "tile": tile_name, "slots": len(slots),
        "changed": diff,
    }


def read_db_schema(pack_dir: str | Path, entity_type: str) -> dict:
    """The EFFECTIVE roll-table schema for one type + where it came from."""
    if entity_type not in DB_TYPES:
        raise ValueError(f"unknown db type {entity_type!r} (one of {list(DB_TYPES)})")
    path = _schema_path(pack_dir, entity_type)
    return {
        "type": entity_type,
        "source": "default" if path == DB_TYPES[entity_type]["schema"] else "pack",
        "path": str(path),
        "schema": _read(path),
    }


def _check_lookup_coverage(spec) -> None:
    """Every lookup table must cover its dependency's choice values — the
    classic table-editing mistake (add an archetype, forget its speed row)."""
    for name, field in spec.fields.items():
        if field.lookup is None or not field.depends_on:
            continue
        parent = spec.fields.get(field.depends_on)
        if parent is None or parent.choices is None:
            continue
        missing = [v for v, _ in parent.choices if v not in field.lookup]
        if missing:
            raise ValueError(
                f"lookup {name!r} has no row for {field.depends_on} "
                f"value(s) {missing} — add rows or remove those choices"
            )


def update_db_schema(
    pack_dir: str | Path,
    entity_type: str,
    changes: dict,
    *,
    actor: str = "user",
    session: str | None = None,
) -> dict:
    """Edit the roll tables bounding generation for one entity type.

    ``changes`` = ``{"fields": {<name>: <field entry> | null}}`` — each named
    field entry is replaced wholesale (null deletes the field); everything
    else carries over. The merged document must load as a SkeletonSpec, pass
    lookup-coverage, and survive a smoke roll BEFORE anything is written.
    Edits land as a PACK-LOCAL override (``schemas/<type>.json``; the repo
    default is never touched) and journal ``op:"edit"`` on ``schema:<type>``.
    """
    if entity_type not in DB_TYPES:
        raise ValueError(f"unknown db type {entity_type!r} (one of {list(DB_TYPES)})")
    field_changes = (changes or {}).get("fields")
    if not isinstance(field_changes, dict) or not field_changes:
        raise ValueError('--set needs {"fields": {<name>: <entry>|null, ...}}')
    pack = Path(pack_dir)
    current = read_db_schema(pack, entity_type)
    merged = json.loads(json.dumps(current["schema"]))  # deep copy
    fields = merged.setdefault("fields", {})
    diff: dict[str, dict] = {}
    for name, entry in field_changes.items():
        old = fields.get(name)
        if entry is None:
            fields.pop(name, None)
        else:
            fields[name] = entry
        if old != entry:
            diff[name] = {"from": old, "to": entry}
    if not diff:
        return {**current, "changed": {}, "no_change": True}

    # Fail-closed validation: loader (shape + dependency order), coverage,
    # then a smoke roll (skipped when a field keys off outer context — no
    # context to thread here).
    spec = load_skeleton_spec(merged)
    _check_lookup_coverage(spec)
    if not any(f.depends_on_context for f in spec.fields.values()):
        roll_skeleton(spec, random.Random(0))

    rel = f"schemas/{entity_type}.json"
    before = provenance.snapshot_file(pack, Path(current["path"]))
    _pack_adapter(pack).write_json_singleton(rel, merged)
    after = provenance.snapshot_file(pack, pack / rel)
    provenance.record(
        pack,
        artifact_id=f"schema:{entity_type}",
        op="edit",
        source="user",
        actor=actor,
        session=session,
        detail={
            "kind": "db_schema", "type": entity_type,
            "changed": sorted(diff), "was": current["source"],
        },
        before_hash=before,
        after_hash=after,
    )
    return {
        "type": entity_type, "source": "pack", "path": str(pack / rel),
        "schema": merged, "changed": diff,
    }


# ---------------------------------------------------------------------------
# level validate — generation's REAL checks on a level as it sits on disk
# (pure read: no repairs, no writes, no journal)
# ---------------------------------------------------------------------------


def validate_level(
    pack_dir: str | Path, level_id: str, _seen: frozenset = frozenset()
) -> dict:
    """Run the real validation suite over one level exactly as it sits on
    disk, hand edits included.

    Mirrors the play surfaces' load semantics: movement/rules merge the
    level's persisted overrides over the manifest ("a level validates under
    its own physics"), the tile registry comes from the manifest, box item
    placements overlay their container tile before terrain checks
    (collision.npz never carries boxes), water anchors count (persisted
    grids are post-flood — ``swim_anchors``), and spilled contained water is
    re-interpreted as free-water features via
    :func:`repair_containment_grid` (the generation-time exemption is never
    persisted) so flooded shorelines don't false-positive. Secret rooms
    validate recursively (cycle-guarded) and ride in ``rooms``.
    """
    import numpy as np

    from canon.adapters.platformer_write import _find_level_dir
    from canon.bible.platformer import SparseMaskEntry
    from examples.platformer_pack.combat import DEFAULT_COMBAT, CombatSpec
    from examples.platformer_pack.movement import PlayerMovementSpec
    from examples.platformer_pack.rules import GameRules
    from examples.platformer_pack.tiles import DEFAULT_TILES, TileRegistry
    from examples.platformer_pack.validate import (
        check_item_placements,
        check_level,
        check_placements,
        repair_containment_grid,
    )
    from examples.platformer_pack.variants import DEFAULT_VARIANTS, VariantSet

    # A hand-edited cyclic room link must report, not recurse forever.
    if level_id in _seen:
        return {
            "level_id": level_id, "stage_id": "", "display_name": "",
            "ok": False,
            "checks": [{
                "name": "rooms",
                "problems": [
                    f"cyclic room link: {level_id!r} appears in its own ancestry"
                ],
            }],
            "movement": {}, "rooms": [],
        }

    pack = Path(pack_dir)
    manifest = _read(pack / "manifest.json")
    level_dir, stage_id = _find_level_dir(pack, level_id)
    level = _read(level_dir / "level.json")
    grid = np.load(level_dir / "collision.npz")["collision"].copy()

    movement = PlayerMovementSpec.model_validate({
        **(manifest.get("movement") or {}),
        **(level.get("movement_overrides") or {}),
    })
    rules = GameRules.model_validate({
        **(manifest.get("rules") or {}),
        **(level.get("rules_overrides") or {}),
    })
    combat = (
        CombatSpec.model_validate(manifest["combat"])
        if manifest.get("combat") else DEFAULT_COMBAT
    )
    variants = (
        VariantSet.model_validate({"variants": manifest["variants"]})
        if manifest.get("variants") else DEFAULT_VARIANTS
    )
    # The manifest carries the game's ACTUAL registry (compose persists it);
    # the repo default only serves packs from before that landed.
    tiles = (
        TileRegistry.model_validate({"tiles": manifest["tiles"]})
        if manifest.get("tiles") else DEFAULT_TILES
    )

    spawn = tuple(level.get("spawn") or (0, 0))
    exit_ = tuple(level.get("exit") or (0, 0))
    triggers = [
        SparseMaskEntry.model_validate(t) for t in (level.get("triggers") or [])
    ]

    def _flat(name: str) -> list:
        p = level_dir / name
        return _read(p) if p.is_file() else []

    entities = _flat("entities.json")
    items = _flat("items.json")

    # Box placements overlay their container tile for terrain/reachability —
    # every play surface does the same at load.
    ts_path = pack / "tileset" / stage_id / "manifest.json"
    slots = (_read(ts_path).get("slots") if ts_path.is_file() else None) or []
    box_tile = next(
        (
            int(s["tile_type"]) for s in slots
            if (s.get("params") or {}).get("container")
        ),
        None,
    )
    boxed = grid.copy()
    grid_h, grid_w = boxed.shape
    for rec in items:
        if str(rec.get("source", "")) != "box" or box_tile is None:
            continue
        try:
            bx, by = int(rec.get("x")), int(rec.get("y"))
        except (TypeError, ValueError):
            continue  # malformed record — check_item_placements reports it
        if 0 <= by < grid_h and 0 <= bx < grid_w:
            boxed[by, bx] = box_tile

    # Hand-edited slot physics diverging from the registry is worth saying
    # out loud: play follows the tileset slots, this validation the registry.
    tile_notes: list[str] = []
    by_id = {t.id: t.category for t in tiles.tiles}
    seen_divergence: set[tuple] = set()
    for s in slots:
        category = by_id.get(int(s.get("tile_type", -1)))
        slot_collision = s.get("collision") or ""
        key = (s.get("name"), slot_collision, category)
        if (
            category is not None and slot_collision
            and slot_collision != category and key not in seen_divergence
        ):
            seen_divergence.add(key)
            tile_notes.append(
                f"tile {s.get('name')!r} collision {slot_collision!r} diverges "
                f"from registry category {category!r} — play follows the "
                "tileset; this validation follows the registry"
            )

    free_volume, containment_notes = repair_containment_grid(grid.copy(), tiles)

    terrain_problems = check_level(
        boxed, spawn, exit_, movement,
        rules=rules, tiles=tiles, triggers=triggers,
        free_volume=free_volume or None,
        swim_anchors=True,  # persisted grids are post-flood
    )

    # Enemy placements validate against the plain grid (generation places
    # enemies before the items step), items against the same + spawn reach.
    enemies = {
        eid: {
            "archetype": d.archetype,
            "size": d.size,
            "rarity": d.rarity,
            "swim_style": str((d.behavior or {}).get("swim_style", "") or ""),
        }
        for eid, d in _load_defs(pack, "enemy").items()
    }
    _, enemy_problems, enemy_repairs = check_placements(
        grid, entities, spawn, enemies,
        rules=rules, tiles=tiles, variants=variants, combat=combat,
    )
    item_defs = {
        iid: d.model_dump(mode="json")
        for iid, d in _load_defs(pack, "item").items()
    }
    _, item_problems, item_repairs = check_item_placements(
        grid, items, spawn, item_defs, movement, rules, tiles,
    )

    checks = [
        {
            "name": "terrain", "problems": terrain_problems,
            "notes": containment_notes + tile_notes,
        },
        {
            "name": "enemies", "problems": enemy_problems,
            "repairs": enemy_repairs, "count": len(entities),
        },
        {
            "name": "items", "problems": item_problems,
            "repairs": item_repairs, "count": len(items),
        },
    ]
    rooms = [
        validate_level(pack, rid, _seen=_seen | {level_id})
        for rid in (level.get("secret_rooms") or [])
    ]
    # Two-tier verdict: ``ok`` = playable as-is (spawn→exit works); repairs
    # are placement defects generation would relocate/drop — the level still
    # plays, so they ride separately in ``repair_count`` for amber surfacing.
    ok = not any(c["problems"] for c in checks) and all(r["ok"] for r in rooms)
    return {
        "level_id": level_id,
        "stage_id": stage_id,
        "display_name": str(level.get("display_name") or ""),
        "ok": ok,
        "checks": checks,
        "repair_count": len(enemy_repairs) + len(item_repairs)
        + sum(r.get("repair_count", 0) for r in rooms),
        "movement": movement.model_dump(mode="json"),
        "rooms": rooms,
    }


# ---------------------------------------------------------------------------
# level generation — composable per-level ops (terrain / enemies / items)
# ---------------------------------------------------------------------------
#
# The pipeline's per-level generators (stamp_level_collision → place_level_
# entities → place_level_items → decorate_level) run against a PipelineContext.
# These ops reconstruct that ctx from the on-disk pack and drive ONE level's
# steps INDEPENDENTLY, so a caller (the cradle create flow) can mix hand-work
# and generation in any order: generate a whole level, or just its terrain,
# or place enemies/items onto an EXISTING (even hand-painted) level. Every op
# leaves the level a DRAFT — it never rewrites manifest.json (publish stays a
# separate step). Fake backend = $0 (canned DSL); anthropic = paid.


def _pack_specs(info: SimpleNamespace) -> SimpleNamespace:
    """Base gameplay specs reconstructed from the manifest — the generation
    inputs (movement/rules/combat/variants/tiles) + graphics. Mirrors
    validate_level's manifest→spec construction; a NEW level carries no
    per-level overrides yet, so there is nothing to merge."""
    from examples.platformer_pack.combat import DEFAULT_COMBAT, CombatSpec
    from examples.platformer_pack.movement import PlayerMovementSpec
    from examples.platformer_pack.rules import GameRules
    from examples.platformer_pack.tiles import DEFAULT_TILES, TileRegistry
    from examples.platformer_pack.variants import DEFAULT_VARIANTS, VariantSet

    m = info.manifest
    return SimpleNamespace(
        movement=PlayerMovementSpec.model_validate(m.get("movement") or {}),
        rules=GameRules.model_validate(m.get("rules") or {}),
        combat=CombatSpec.model_validate(m["combat"]) if m.get("combat") else DEFAULT_COMBAT,
        variants=VariantSet.model_validate({"variants": m["variants"]}) if m.get("variants") else DEFAULT_VARIANTS,
        tiles=TileRegistry.model_validate({"tiles": m["tiles"]}) if m.get("tiles") else DEFAULT_TILES,
        graphics=info.graphics,
    )


def _level_gen_ctx(
    pack_dir: str | Path, backend: str, model: str | None
) -> tuple[SimpleNamespace, PipelineContext]:
    """A PipelineContext over the on-disk pack, populated for LEVEL generation.
    load_pack/make_ctx don't set the enemy roster or item pool (the placement
    generators read them from the bible) — do it here, plus the artifact slots
    the generators consult (level_briefs, level_overrides)."""
    info = load_pack(pack_dir)
    ctx = make_ctx(info, llm=build_llm(backend, model))
    ctx.bible.enemy_definitions.update(_load_defs(Path(pack_dir), "enemy"))
    ctx.bible.items.update(_load_defs(Path(pack_dir), "item"))
    ctx.artifacts.setdefault("level_briefs", {})
    ctx.artifacts.setdefault("level_overrides", {})
    return info, ctx


def _load_level_into_ctx(ctx: PipelineContext, pack_dir: str | Path, level_id: str):
    """Load an EXISTING on-disk level into the bible + register it in its stage
    so the placement/decorator generators find it (they read the level from
    ctx.bible.levels and its grid from disk). Enables placing enemies/items
    onto a level someone else generated OR hand-painted."""
    from canon.adapters.platformer_write import _find_level_dir
    from canon.bible.platformer import Level

    level_dir, stage_id = _find_level_dir(Path(pack_dir), level_id)
    level = Level.model_validate(_read(level_dir / "level.json"))
    ctx.bible.levels[level_id] = level
    stage = ctx.bible.stages[stage_id]
    if level_id not in stage.level_ids:
        stage.level_ids.append(level_id)
    return level, stage_id


def _effective_seed(info: SimpleNamespace, level_id: str, seed: str | None) -> str:
    """The generation seed: an explicit pin (reproducible), else a SALTED
    per-level seed so repeated generates vary — recorded for reproducibility."""
    import secrets

    return str(seed) if seed else f"{info.seed}:{level_id}:{secrets.token_hex(4)}"


def _write_empty_placements(pack_dir: str | Path, level_id: str) -> None:
    """Ensure a terrain-only level has empty entities/items files on disk
    (the placement generators write them; a terrain-only pass skips them, and
    validate/play/read all expect the files to exist)."""
    from canon.adapters.platformer_write import _find_level_dir

    level_dir, _ = _find_level_dir(Path(pack_dir), level_id)
    for name in ("entities.json", "items.json"):
        p = level_dir / name
        if not p.exists():
            p.write_text("[]")


def _level_gen_result(
    pack_dir: str | Path, level_id: str, stage_id: str,
    seed: str, layout_fallback: bool, ctx: PipelineContext,
) -> dict:
    """Uniform op return: the inline validation verdict (same validator the
    play surfaces use) + provenance-relevant fields."""
    v = validate_level(pack_dir, level_id)
    return {
        "level_id": level_id,
        "stage_id": stage_id,
        "ok": v["ok"],
        "repair_count": v.get("repair_count", 0),
        "layout_fallback": bool(layout_fallback),
        "seed": seed,
        "warnings": _warnings(ctx),
    }


def generate_terrain(
    pack_dir: str | Path,
    *,
    stage_id: str,
    brief: str,
    backend: str = "fake",
    model: str | None = None,
    difficulty: int | None = None,
    width: int | None = None,
    height: int | None = None,
    axis: str | None = None,
    seed: str | None = None,
    actor: str = "user",
    session: str | None = None,
) -> dict:
    """Generate ONE new DRAFT level's TERRAIN (collision + spawn/exit/hazards/
    triggers), no placements. Full-control pins (difficulty/width/height/axis)
    ride the level-override artifact; secret rooms are opted out (on-demand
    single-level intent). Journals op=generate; never touches manifest.json."""
    from canon.adapters.platformer_write import _next_level_id, baseline_level
    from examples.platformer_pack.level import (
        stamp_level_collision,
        write_level_hazards,
        write_level_manifest,
        write_level_triggers,
    )

    info, ctx = _level_gen_ctx(pack_dir, backend, model)
    if stage_id not in ctx.bible.stages:
        raise ValueError(
            f"no stage {stage_id!r} in pack — stages: {list(ctx.bible.stages)}"
        )
    specs = _pack_specs(info)
    lid = _next_level_id(Path(pack_dir))
    eff_seed = _effective_seed(info, lid, seed)
    ctx.config.seed = eff_seed
    stage = ctx.bible.stages[stage_id]
    stage.level_ids.append(lid)  # in-memory only — draft stays out of manifest
    idx = stage.level_ids.index(lid)
    ctx.artifacts["level_briefs"][lid] = brief
    override: dict = {"no_secret_rooms": True}
    if difficulty is not None:
        override["difficulty"] = int(difficulty)
    if width is not None:
        override["grid_width"] = int(width)
    if height is not None:
        override["grid_height"] = int(height)
    if axis:
        override["axis"] = str(axis)
    ctx.artifacts["level_overrides"][lid] = override

    level = stamp_level_collision(
        ctx, lid, idx,
        movement=specs.movement, rules=specs.rules, tiles=specs.tiles,
        graphics=specs.graphics,
        default_width=int(width) if width else 56,
        default_height=int(height) if height else 16,
        phase_name="plat:layout",
    )
    write_level_hazards(ctx, level)
    write_level_triggers(ctx, level)
    write_level_manifest(ctx, level)
    _write_empty_placements(pack_dir, lid)
    baseline_level(Path(pack_dir), lid, actor=actor, session=session)
    return _level_gen_result(
        pack_dir, lid, stage_id, eff_seed, level.layout_fallback, ctx
    )


def place_enemies(
    pack_dir: str | Path,
    *,
    level_id: str,
    backend: str = "fake",
    model: str | None = None,
    max_enemies: int = 4,
    seed: str | None = None,
    actor: str = "user",
    session: str | None = None,
) -> dict:
    """Place enemies onto an EXISTING level (generated or hand-painted),
    (re)writing entities.json against its on-disk grid. Grid-driven +
    self-validating, so it adapts to whatever terrain is there."""
    from canon.adapters.platformer_write import baseline_level
    from examples.platformer_pack.level import place_level_entities

    info, ctx = _level_gen_ctx(pack_dir, backend, model)
    level, stage_id = _load_level_into_ctx(ctx, pack_dir, level_id)
    specs = _pack_specs(info)
    ctx.config.seed = _effective_seed(info, level_id, seed)
    place_level_entities(
        ctx, level, max_enemies=int(max_enemies), rules=specs.rules,
        tiles=specs.tiles, variants=specs.variants, combat=specs.combat,
    )
    baseline_level(Path(pack_dir), level_id, actor=actor, session=session)
    return _level_gen_result(
        pack_dir, level_id, stage_id, ctx.config.seed, level.layout_fallback, ctx
    )


def place_items(
    pack_dir: str | Path,
    *,
    level_id: str,
    backend: str = "fake",
    model: str | None = None,
    max_items: int = 12,
    seed: str | None = None,
    actor: str = "user",
    session: str | None = None,
) -> dict:
    """Place items onto an EXISTING level (generated or hand-painted),
    (re)writing items.json against its on-disk grid + enemy roster."""
    from canon.adapters.platformer_write import baseline_level
    from examples.platformer_pack.level import place_level_items

    info, ctx = _level_gen_ctx(pack_dir, backend, model)
    level, stage_id = _load_level_into_ctx(ctx, pack_dir, level_id)
    specs = _pack_specs(info)
    ctx.config.seed = _effective_seed(info, level_id, seed)
    place_level_items(
        ctx, level, max_items=int(max_items), rules=specs.rules,
        tiles=specs.tiles, movement=specs.movement,
    )
    baseline_level(Path(pack_dir), level_id, actor=actor, session=session)
    return _level_gen_result(
        pack_dir, level_id, stage_id, ctx.config.seed, level.layout_fallback, ctx
    )


def generate_level(
    pack_dir: str | Path,
    *,
    stage_id: str,
    brief: str,
    backend: str = "fake",
    model: str | None = None,
    difficulty: int | None = None,
    width: int | None = None,
    height: int | None = None,
    axis: str | None = None,
    max_enemies: int = 4,
    max_items: int = 12,
    seed: str | None = None,
    actor: str = "user",
    session: str | None = None,
) -> dict:
    """Convenience: a WHOLE draft level — terrain, then enemies, then items —
    by chaining the composable ops. Shares one seed so a pinned seed fully
    reproduces the level."""
    terrain = generate_terrain(
        pack_dir, stage_id=stage_id, brief=brief, backend=backend, model=model,
        difficulty=difficulty, width=width, height=height, axis=axis,
        seed=seed, actor=actor, session=session,
    )
    lid = terrain["level_id"]
    # reuse the terrain's effective seed so the whole level is one reproducible unit
    place_enemies(
        pack_dir, level_id=lid, backend=backend, model=model,
        max_enemies=max_enemies, seed=terrain["seed"], actor=actor, session=session,
    )
    return place_items(
        pack_dir, level_id=lid, backend=backend, model=model,
        max_items=max_items, seed=terrain["seed"], actor=actor, session=session,
    )


def regenerate_terrain(
    pack_dir: str | Path,
    *,
    level_id: str,
    brief: str,
    backend: str = "fake",
    model: str | None = None,
    difficulty: int | None = None,
    width: int | None = None,
    height: int | None = None,
    axis: str | None = None,
    seed: str | None = None,
    actor: str = "user",
    session: str | None = None,
) -> dict:
    """Regenerate an EXISTING level's TERRAIN in place from a brief — turns a
    flat/blank draft into a designed level, or redesigns an existing one. Same
    level id (published or draft), overwriting collision/hazards/triggers. Keeps
    the level's current size unless width/height are given. The old placements
    belonged to the OLD terrain, so they're CLEARED — re-run place_enemies/
    place_items to repopulate. Journals op=generate on the changed steps; never
    touches manifest.json (a published level stays published, just redesigned)."""
    from canon.adapters.platformer_write import _find_level_dir, baseline_level
    from canon.bible.platformer import Level
    from examples.platformer_pack.level import (
        stamp_level_collision,
        write_level_hazards,
        write_level_manifest,
        write_level_triggers,
    )

    info, ctx = _level_gen_ctx(pack_dir, backend, model)
    level_dir, stage_id = _find_level_dir(Path(pack_dir), level_id)
    prior = Level.model_validate(_read(level_dir / "level.json"))
    specs = _pack_specs(info)
    eff_seed = _effective_seed(info, level_id, seed)
    ctx.config.seed = eff_seed
    stage = ctx.bible.stages[stage_id]
    if level_id not in stage.level_ids:  # a draft isn't registered in its stage
        stage.level_ids.append(level_id)
    idx = stage.level_ids.index(level_id)
    ctx.artifacts["level_briefs"][level_id] = brief or prior.brief
    # Keep the current dims by default (a "regenerate", not a "resize").
    override: dict = {
        "no_secret_rooms": True,
        "grid_width": int(width) if width else int(prior.grid_width),
        "grid_height": int(height) if height else int(prior.grid_height),
    }
    if difficulty is not None:
        override["difficulty"] = int(difficulty)
    if axis:
        override["axis"] = str(axis)
    ctx.artifacts["level_overrides"][level_id] = override

    level = stamp_level_collision(
        ctx, level_id, idx,
        movement=specs.movement, rules=specs.rules, tiles=specs.tiles,
        graphics=specs.graphics,
        default_width=int(prior.grid_width), default_height=int(prior.grid_height),
        phase_name="plat:layout",
    )
    write_level_hazards(ctx, level)
    write_level_triggers(ctx, level)
    write_level_manifest(ctx, level)
    # Placements were pinned to the OLD terrain — clear them (user re-runs 🎲).
    (level_dir / "entities.json").write_text("[]")
    (level_dir / "items.json").write_text("[]")
    baseline_level(Path(pack_dir), level_id, actor=actor, session=session)
    return _level_gen_result(
        pack_dir, level_id, stage_id, eff_seed, level.layout_fallback, ctx
    )


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

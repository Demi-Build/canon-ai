"""The platformer template's registry seed — ``PACK_SPEC`` for pack_type
``"platformer"`` (P0 paper P.3 / P.4, row P0-3).

The two entity kinds are BUILT FROM ``ops.DB_TYPES`` at import — the table
cradle's "+ new row" already drives — so the registry entry and the op table
cannot drift: ``dir`` → ``layout``, ``id_field``, ``llm_fields``,
``code_fields`` and ``phase_label`` are read, never re-typed; ``nesting`` /
``containers`` / ``protected`` come from the ``db update`` routing tables
beside it (``_UPDATE_NESTING``, ``_DICT_CONTAINERS``, ``_PROTECTED_FIELDS``);
``builder`` (row P0-6) binds the anchored enemy/item generation bodies so
``canon.db_ops`` generates through this seed without importing it.

Row P0-10 fills the three slots P0-3 left open here: ``phase_labels`` (the
§3.0-E phase-id → label map — the 22 ids cradle's ``CreateProgress`` used to
hardcode, now template DATA that `pack templates` and the stamped registry
both carry), the wizard's ``ranges`` (P.9 R8: authored from the design
package's steppers), and ``runner`` (how ``world new --template platformer``
spawns ``canon.packs.platformer.run_slice`` — the registry dispatch that
replaced the hardcoded runner in ``cli/main.py``).

What this seed deliberately does NOT carry, by row ownership: the harness
``pygame`` engine entry (not attached in Phase 0 — P.9 R6; recorded in P.4.3
for W2.0), and the ``dialogue`` capability (a platformer-descended project
enables it via §5.1a at W2.2).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from canon.bible.platformer import EnemyDefinition, ItemDefinition
from canon.packs.platformer.compose import compose_pipeline
from canon.packs.platformer.estimate import ESTIMATOR as _ESTIMATOR
from canon.packs.platformer.ops import (
    _DICT_CONTAINERS,
    _PROTECTED_FIELDS,
    _UPDATE_NESTING,
    DB_TYPES,
    _load_defs,
    row_builder,
)
from canon.packs.platformer.prompts import PlatformerPrompts
from canon.packs.spec import CORE_PROTECTED, EntityKind, GridKind, PackSpec

PACK_TYPE = "platformer"

_TEMPLATE_DIR = Path(__file__).parent

#: Per-kind display + generation metadata that ``DB_TYPES`` does not carry.
#: ``count_key`` = the ``world new`` flag / wizard count name.
_KIND_META: dict[str, dict] = {
    "enemy": {"label": "Enemies", "count_key": "enemies", "model": EnemyDefinition},
    "item": {"label": "Items", "count_key": "items", "model": ItemDefinition},
}

_ALL_ID_FIELDS = frozenset(meta["id_field"] for meta in DB_TYPES.values())


def _protected_for(kind: str) -> list[str]:
    """This kind's additions to the CORE wall: its own id field plus the
    shared asset plumbing (sprite_path / sprite_hash / animation) — the other
    kinds' id fields are not this kind's business."""
    return sorted(
        name
        for name in _PROTECTED_FIELDS - CORE_PROTECTED
        if name == DB_TYPES[kind]["id_field"] or name not in _ALL_ID_FIELDS
    )


def _per_file_loader(kind: str) -> Callable[[str | Path], dict[str, Any]]:
    """The P.3.1 ``loader`` for a per-file kind (row P0-5): ``ops._load_defs``
    — the ``enemy/<id>.json`` read every db op already performs, validated
    into the kind's model and keyed by its ``id_field`` — bound to *kind*.
    Wraps, never rewrites; seed-only, never stamped."""

    def loader(pack: str | Path) -> dict[str, Any]:
        return _load_defs(Path(pack), kind)

    loader.__name__ = f"load_{kind}_rows"
    return loader


def _entity_from_db_type(kind: str) -> EntityKind:
    meta = DB_TYPES[kind]
    extra = _KIND_META[kind]
    return EntityKind(
        kind=kind,
        label=extra["label"],
        layout={"mode": "per_file", "dir": meta["dir"]},
        id_field=meta["id_field"],
        id_alloc=None,  # slug ids
        schema=f"schemas/{kind}.json",
        llm_fields=list(meta["llm_fields"]),
        code_fields=list(meta["code_fields"]),
        nesting=dict(_UPDATE_NESTING.get(kind, {})),
        containers=list(_DICT_CONTAINERS.get(kind, ())),
        protected=_protected_for(kind),
        phase_label=meta["phase_label"],
        per_map=False,
        count_key=extra["count_key"],
        asset={
            "field": "sprite_path",
            "hash_field": "sprite_hash",
            "kinds": ["image"],
            "targets": [f"{kind}:<{meta['id_field']}>"],
        },
        model=extra["model"],
        loader=_per_file_loader(kind),
        # P0-6: the anchored generation body ``db new`` / ``db complete``
        # call through ``canon.db_ops`` (seed-only, never stamped).
        builder=row_builder(kind),
    )


ENTITIES: dict[str, EntityKind] = {kind: _entity_from_db_type(kind) for kind in DB_TYPES}

#: P.3.2 — the level GridKind: a directory of step files under
#: ``level/{stage_id}/{level_id}/``; per-engine siblings (``*.grid.json``)
#: live on the engine entry's ``artifacts``, never here.
LEVEL_GRID = GridKind(
    kind="level",
    ref_field="level_id",
    path_template="level/{stage_id}/{level_id}/",
    file=None,
    steps={
        "collision": "collision.npz",
        "terrain": "terrain.npz",
        "background": "background.npz",
        "hazards": "hazards.json",
        "triggers": "triggers.json",
        "foreground": "foreground.json",
        "entities": "entities.json",
        "items": "items.json",
        "level": "level.json",
    },
    dense=["collision", "terrain", "background"],
    sparse=["hazards", "triggers", "foreground"],
    placements={
        "entities": {
            "kind": "enemy", "wire": "entities", "shape": "list", "id": "enemy_id",
            "grid_stamp": None, "journal_kind": "enemy_move",
        },
        "items": {
            "kind": "item", "wire": "items", "shape": "list", "id": "item_id",
            "grid_stamp": None, "journal_kind": "item_move",
        },
    },
    points=["spawn", "exit"],
    dims={"width_field": "grid_width", "height_field": "grid_height", "default": [48, 16]},
    cell_vocab="tile_types.json",
    derived=["terrain", "background", "hazards"],
    restorable=["entities", "items", "triggers", "hazards", "foreground"],
    artifact_id="level:{stage_id}/{level_id}/{step}",
)

#: P.4.3 worked entry 1. ``template.version`` is derived from
#: ``godot/.engine.json.template_hash`` at read (P.9 R7), so the seed carries
#: no hash. ``primary: true`` is the ▶ Play default; the pygame harness stays
#: on cradle's current ``play_level`` path until W2.0 (P.9 R6).
GODOT_ENGINE: dict = {
    "id": "godot",
    "template": {"ref": "platformer_pack/godot_template", "version": None},
    "launch": {"cmd": "{godot}", "args": ["--path", "{pack}"], "env": {"PLAT_LEVEL": "{level}"}},
    "live_channel": {"kind": "hooks-v0", "protocol": None},
    "artifacts": [
        "project.godot",
        "godot/main.tscn",
        "godot/main.gd",
        "godot/.engine.json",
        "level/*/*/*.grid.json",
    ],
    "exports": ["computer", "web", "mobile"],
    "primary": True,
}

#: ``world new`` defaults (``cli/main.py`` ``world_new`` options) — the
#: wizard's count names, mirrored in ``wizard.defaults``.
DEFAULT_COUNTS: dict[str, int] = {"stages": 1, "levels": 2, "enemies": 4, "items": 4}

#: P.9 R8 — the wizard's numeric bands, authored at P0-10 from the design
#: package's steppers (`design_handoff_editor_worldmap_start` README, "New
#: project → Step 2": Stages 1–8, Levels per stage 1–12, Enemies 0–24,
#: Items 0–24). Data, not validation: the runner accepts any count; these
#: bound the wizard's steppers.
DEFAULT_RANGES: dict[str, list[int]] = {
    "stages": [1, 8],
    "levels": [1, 12],
    "enemies": [0, 24],
    "items": [0, 24],
}

#: §3.0-E — the phase-id → label map. THE single source for every surface that
#: names a pipeline phase (cradle's CreateProgress, the JobTray and the agent's
#: run cards all render this map; nothing hardcodes ``plat:*`` any more). Keys
#: are the ids the StepLog emits with the ``phase:`` prefix stripped; an id
#: absent here still renders (the reader de-prefixes and humanizes it), so a
#: new phase is never invisible.
#:
#: Two SHAPES of key, because the orchestrator (the create default since master
#: §8 Q6) emits per-ARTIFACT nodes, not only phases:
#:
#: - a whole id (``plat:world``) — the sequential phases and the DAG singletons;
#: - a node FAMILY (``review``) or a family LEAF (``level:terrain``), which name
#:   the per-artifact ids ``level:<stage>/<level>/<layer>`` and
#:   ``review:<stage>/<level>``. A reader tries the whole id, then
#:   ``<family>:<leaf>``, then ``<family>``, and keeps the id's own context — so
#:   the 36 level nodes of a default create read as "Terrain · ashen_depths/l1"
#:   from ten entries instead of 36 raw ids. A new layer is one line HERE and
#:   nothing in cradle.
#:
#: ``plat:layout``/``terrain``/``background``/``placement``/``item_placement``/
#: ``decorator``/``render``/``level_steps`` name the SEQUENTIAL pipeline
#: (``--no-orchestrate``), which is still a supported run.
PHASE_LABELS: dict[str, str] = {
    "plat:world": "World premise",
    "plat:stage": "Stages",
    "plat:style": "Art direction",
    "plat:enemies": "Enemy roster",
    "plat:items": "Item pool",
    "plat:tileset": "Tile slots",
    "plat:layout": "Level layouts",
    "plat:terrain": "Terrain",
    "plat:background": "Backgrounds",
    "plat:placement": "Placing enemies",
    "plat:item_placement": "Placing items",
    "plat:decorator": "Decoration",
    "plat:tileset_art": "Tileset art",
    "plat:sprite_art": "Sprite art",
    "plat:sprite_animation": "Animation",
    "plat:backdrop_art": "Backdrops",
    "plat:world_art": "Title art",
    "plat:audio": "Music & SFX",
    "plat:render": "Review renders",
    "plat:vlm_qa": "Quality pass",
    "plat:manifest": "Manifest",
    "plat:level_steps": "Level steps",
    # The orchestrator's per-artifact families (see the note above).
    "level": "Level",
    "level:collision": "Collision",
    "level:terrain": "Terrain",
    "level:background": "Background",
    "level:foreground": "Foreground",
    "level:hazards": "Hazards",
    "level:triggers": "Triggers",
    "level:entities": "Placing enemies",
    "level:items": "Placing items",
    "level:level": "Level assembly",
    "review": "Review",
    "review:legend": "Legend review",
}

#: Row P0-10 — how ``world new --template platformer`` spawns this template's
#: runner (``PackSpec.runner``). ``--engine json`` is the create default the
#: hardcoded dispatch always passed; ``--orchestrate`` is declared because the
#: platformer HAS a DAG scheduler, and master §8 Q6 makes it the create default
#: (its only fixture delta is an additive ``bible.json``).
RUNNER: dict[str, Any] = {
    "module": "canon.packs.platformer.run_slice",
    "output": "--output-dir",
    "seed": "--seed",
    "model": "--model",
    "counts": {
        "stages": "--num-stages",
        "levels": "--num-levels",
        "enemies": "--num-enemies",
        "items": "--num-items",
    },
    "backends": {
        "llm": "--backend",
        "image": "--image-backend",
        "music": "--music-backend",
        "sfx": "--sfx-backend",
        "vlm": "--vlm-backend",
    },
    "extra": ["--engine", "json"],
    "orchestrate": "--orchestrate",
}

PACK_SPEC = PackSpec(
    pack_type=PACK_TYPE,
    label="Platformer",
    description="Side-scrolling stages of levels, wired into a world map.",
    vocab=["stages", "levels", "paths"],
    entities=ENTITIES,
    grids={"level": LEVEL_GRID},
    dialogue=None,
    capabilities=["grid"],
    counts=dict(DEFAULT_COUNTS),
    # P.4.4 — template-side wizard metadata; the cards render from this.
    # ``distribution`` is DERIVED from engines[*].exports by `pack templates`,
    # never authored (W2.4), so it stays empty here.
    wizard={
        "id": PACK_TYPE,
        "label": "Platformer",
        "description": "Side-scrolling stages of levels, wired into a world map.",
        "vocab": ["stages", "levels", "paths"],
        "defaults": dict(DEFAULT_COUNTS),
        "ranges": dict(DEFAULT_RANGES),
        "advanced": [],
        "engine": ["godot"],
        "dimension": "2D",
        "distribution": [],
        "beta": False,
        "phase_labels": dict(PHASE_LABELS),
    },
    engines=[GODOT_ENGINE],
    tuning_vocabulary="rule_overrides.json",
    # P.7.1 platformer rows — the `world update` field table (P0-6 builds the
    # verb on this data; the mirror closes the `_set_world_name` journal gap).
    world_fields={
        "title": {
            "file": "world.json",
            "path": "title",
            "mirrors": [{"file": "manifest.json", "path": "world"}],
        },
        "unlock_rules": {
            "file": "world.json",
            "path": "unlock_rules",
            "mirrors": [{"file": "manifest.json", "path": "unlock"}],
        },
    },
    phase_labels=dict(PHASE_LABELS),
    runner=dict(RUNNER),
    # P.9 R3 — template data the runner takes as flags today; manifest copy
    # is the owned instance in Phase 0 (no pack-local copies).
    data_files={
        "game_rules": "game_rules.json",
        "combat": "combat.json",
        "tile_types": "tile_types.json",
        "variants": "variants.json",
        "graphics": "graphics.json",
        "models": "models.json",
        "sections": "sections.json",
        "water_levels": "water_levels.json",
        "secret_rooms": "secret_rooms.json",
        "rule_overrides": "rule_overrides.json",
    },
    compose=compose_pipeline,
    estimator=_ESTIMATOR,
    prompts=PlatformerPrompts,
    validators=None,
    template_dir=_TEMPLATE_DIR,
)

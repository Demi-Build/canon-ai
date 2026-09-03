"""The pack registry's seed shapes — ``EntityKind`` / ``GridKind`` /
``DialogueSpec`` / ``PackSpec`` (P0 paper P.3.1–P.3.4, row P0-3).

Convention (P.3): **seed-only** fields live in Python (callables, Pydantic
classes, template paths) and are never serialized; **stamped** fields are the
JSON subset ``world new --template`` writes into ``<pack>/.canon/registry.json``
(row P0-10) and every verb reads thereafter (P.4.1). ``PackSpec.stamped()`` is
that subset; this row only exposes it.

Every "kind"/"id" here is a plain ``str`` — an open vocabulary, never a
``Literal`` union (master doctrine 8, the M0-readiness rule). ``Layout``,
``WizardMeta`` and ``EngineEntry`` are open dict shapes for the same reason:
a third template adds a mode / an engine id as data, not as a type change.

Row P0-6 adds two seed-side pieces on top: ``EntityKind.builder`` — the
seed-only per-row generation body ``db new`` / ``db complete`` call (the
platformer binds its anchored enemy/item builders; a kind without one rolls
its skeleton only and answers ``db complete`` with a structured not-yet) —
and ``default_dialogue()``, the core's implementing seed for the ``dialogue``
capability (P.7.4: enabling it on a platformer-descended pack copies this
block into the registry; the dungeon seed is built FROM it, so the condition
grammar lives once — §3.0-G).

Deliberately absent, by row ownership: registry stamping and ``pack
templates`` (P0-10); the reserved ``tuning`` block's key vocabulary (W2.1 —
``TUNING_RESERVED`` is the P.4.5 placeholder P0-10 stamps for every
template).
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

#: ``{"mode": "per_file", "dir": "enemy"}`` |
#: ``{"mode": "collection", "path": "npcs/npcs.json", "format": "array" | "keyed_object" | "array_positional"}``
#: — the "one genuinely new core mechanism" (Phase 0 §5.1): a two-member open
#: shape, not an enum.
Layout = dict[str, Any]

#: P.4.4 — ``pack templates`` wizard metadata (id/label/description/vocab/
#: defaults/ranges/advanced/engine/dimension/distribution/beta/phase_labels).
WizardMeta = dict[str, Any]

#: P.4.3 — one §5.1b engines-block entry (id/template/launch/live_channel/
#: artifacts/exports/primary, plus the additive capability-gated
#: ``evaluable_namespaces`` / ``evaluable_bindings`` blocks).
EngineEntry = dict[str, Any]

#: The CORE protected wall every ``EntityKind.protected`` list ADDS to
#: (P.3.1: artifact_id, provenance_hash, parents, status, review_status).
CORE_PROTECTED: frozenset[str] = frozenset({
    "artifact_id", "provenance_hash", "parents", "status", "review_status",
})

#: P.4.5 — what every template stamps in Phase 0 (spec-only slot; W2.1
#: populates ``keys`` and flips ``status``).
TUNING_RESERVED: dict[str, Any] = {
    "schema": "canon-tuning/v0",
    "status": "reserved",
    "keys": {},
}


def _stamped_fields(obj: Any, excluded: tuple[str, ...]) -> dict[str, Any]:
    """Every dataclass field of *obj* except *excluded* (the seed-only ones,
    plus ``kind`` — the registry map key carries it, P.4.2), deep-copied so a
    caller mutating the stamped dict never reaches back into the seed."""
    return {
        f.name: copy.deepcopy(getattr(obj, f.name))
        for f in fields(obj)
        if f.name not in excluded
    }


@dataclass(kw_only=True)
class EntityKind:
    """One row type of a pack — where its rows live, how they are authored,
    and what ``db update`` may touch (P.3.1). ``layout`` and ``id_field`` are
    REQUIRED, as the paper declares them (``kw_only`` lets them follow the
    defaulted ``label``): a kind without a home on disk cannot be counted,
    loaded or edited, so constructing one is a ``TypeError``, not a later
    surprise for a P0-6 ``db define``."""

    # identity / storage (stamped)
    kind: str
    label: str = ""
    layout: Layout
    id_field: str
    id_alloc: dict | None = None
    schema: str | None = None
    # authoring split (stamped)
    llm_fields: list[str] = field(default_factory=list)
    code_fields: list[str] = field(default_factory=list)
    user_fields: list[str] = field(default_factory=list)
    hidden: list[str] = field(default_factory=list)
    decorative: list[str] = field(default_factory=list)
    # write discipline (stamped)
    nesting: dict[str, str] = field(default_factory=dict)
    containers: list[str] = field(default_factory=list)
    protected: list[str] = field(default_factory=list)
    routed: dict[str, str] = field(default_factory=dict)
    renames: dict[str, str] = field(default_factory=dict)
    refs: dict[str, str] = field(default_factory=dict)
    # generation (stamped where data)
    phase_label: str = ""
    per_map: bool = False
    count_key: str | None = None
    dedup: list[str] = field(default_factory=list)
    asset: dict | None = None
    vocab: dict[str, list] = field(default_factory=dict)
    # seed-only (code)
    model: type | None = None
    loader: Callable | None = None
    parser: Callable | None = None
    prompt_method: str | None = None
    prompt_kwargs: dict = field(default_factory=dict)
    #: P0-6 additive (seed-only): the per-row anchored generation body —
    #: ``builder(pack_dir, *, index, fields, complete, llm, system_override)``
    #: → ``canon.db_ops.BuiltRow``. ``None`` = skeleton-only ``db new``,
    #: structured not-yet on ``db complete``.
    builder: Callable | None = None

    SEED_ONLY = ("model", "loader", "parser", "prompt_method", "prompt_kwargs", "builder")

    def stamped(self) -> dict[str, Any]:
        """The P.1.x canonical entry: every stamped field EXCEPT ``kind`` —
        the registry keys ``entities`` by it (P.4.2), so the entry carries
        no copy and P0-10's ``template.version`` hashes one shape, not two."""
        return _stamped_fields(self, ("kind", *self.SEED_ONLY))


@dataclass
class GridKind:
    """A spatial artifact family — the dungeon room's ``maze.json`` or the
    platformer level's directory of step files (P.3.2). All stamped."""

    kind: str
    ref_field: str = ""
    path_template: str = ""
    file: str | None = None
    steps: dict[str, str] = field(default_factory=dict)
    dense: list[str] = field(default_factory=list)
    sparse: list[str] = field(default_factory=list)
    placements: dict[str, dict] = field(default_factory=dict)
    points: list[str] = field(default_factory=list)
    dims: dict = field(default_factory=dict)
    cell_vocab: str = ""
    derived: list[str] = field(default_factory=list)
    restorable: list[str] = field(default_factory=list)
    artifact_id: str = ""

    def stamped(self) -> dict[str, Any]:
        """All fields except ``kind`` — the registry keys ``grids`` by it
        (P.4.2), the same rule as ``EntityKind.stamped``."""
        return _stamped_fields(self, ("kind",))


@dataclass
class DialogueSpec:
    """The condition grammar + selector model as pack data (P.3.3, §3.0-G).
    Absent from the registry when ``"dialogue"`` is not a capability."""

    storage: dict = field(default_factory=dict)
    condition_namespaces: list[str] = field(default_factory=list)
    scene_only_namespaces: list[str] = field(default_factory=list)
    effects: list[str] = field(default_factory=list)
    scopes: list[str] = field(default_factory=list)
    operands: dict[str, dict] = field(default_factory=dict)
    selector_axes: list[str] = field(default_factory=list)
    scene: dict = field(default_factory=dict)
    engine_evaluable_seed: dict[str, dict] = field(default_factory=dict)
    tree_model: type | None = None  # seed-only

    SEED_ONLY = ("tree_model",)

    def stamped(self) -> dict[str, Any]:
        return _stamped_fields(self, self.SEED_ONLY)


#: P.3.3 — the selector model + condition grammar as data (§3.0-G: one
#: vocabulary, pack-registry data). ``default_dialogue()`` hands out a fresh
#: ``DialogueSpec`` built from it: the dungeon seed extends it with its
#: engine-evaluability seed + tree model; ``canon registry set`` copies it
#: into a pack that enables ``dialogue`` (P.7.4, capability-enablement).
DEFAULT_DIALOGUE_DATA: dict[str, Any] = {
    "storage": {
        "on": "npc",
        "field": "dialogue_trees",
        "legacy_fields": [
            "dialogue_tree", "dialogue_tree_incomplete", "dialogue_tree_complete",
            "dialogue_tree_failed",
        ],
    },
    "condition_namespaces": [
        "has_item", "quest", "time", "player", "flag", "segment", "room", "scene", "event",
    ],
    "scene_only_namespaces": ["actor"],
    "effects": ["gives_item", "takes_item", "gives_quest", "advance_quest", "set_flag"],
    "scopes": ["tree", "selector", "scene", "effects", "music"],
    "operands": {
        "has_item": {"entity": "item", "field": "id"},
        "quest": {
            "entity": "quest", "field": "id",
            "states": ["not_started", "active", "completed", "failed"],
        },
        "time": {"windows": ["dawn", "day", "dusk", "night"]},
        "player": {
            "fields": [
                "level", "health", "max_health", "stamina", "money", "archetype",
                "STR", "DEX", "CON", "INT", "WIS", "CHA", "LUCK",
            ],
            "ops": ["<", "<=", "==", ">=", ">"],
        },
        "flag": {"keys": "from set_flag effects", "values": ["true", "false"]},
        "segment": {"values": []},
        "room": {"entity": "room", "field": "id"},
        "scene": {
            "entity": "event", "field": "id", "filter": {"type": "scene"},
            "states": ["seen", "unseen"],
        },
        "event": {"entity": "event", "field": "id", "states": ["solved", "unsolved"]},
        "actor": {
            "entity": "npc", "field": "id", "restrict_to": "scene.actors",
            "states": ["present", "absent"],
        },
    },
    "selector_axes": ["quest", "segment", "time", "flag", "room", "scene", "player", "custom"],
    "scene": {
        "event_type": "scene",
        "triggers": ["enter_room", "talk_any_actor", "quest_advance"],
        "once": True,
        "on_finish": "effects",
    },
    "engine_evaluable_seed": {},
}


def default_dialogue(**overrides: Any) -> DialogueSpec:
    """A fresh ``DialogueSpec`` from ``DEFAULT_DIALOGUE_DATA`` (deep-copied so
    no two seeds share a list), with *overrides* applied — the dungeon passes
    its ``engine_evaluable_seed`` and ``tree_model``."""
    data = copy.deepcopy(DEFAULT_DIALOGUE_DATA)
    data.update(overrides)
    return DialogueSpec(**data)


@dataclass
class PackSpec:
    """The seed a template contributes to the registry (P.3.4). ``pack_type``
    is the registry id — data; the source of truth once stamped is
    ``.canon/registry.json``, mirrored into ``manifest.json.pack_type`` on
    every manifest write (P.4.1)."""

    pack_type: str
    label: str
    description: str = ""
    vocab: list[str] = field(default_factory=list)
    entities: dict[str, EntityKind] = field(default_factory=dict)
    grids: dict[str, GridKind] = field(default_factory=dict)
    dialogue: DialogueSpec | None = None
    capabilities: list[str] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    wizard: WizardMeta = field(default_factory=dict)
    engines: list[EngineEntry] = field(default_factory=list)
    tuning_vocabulary: str | None = None
    world_fields: dict[str, dict] = field(default_factory=dict)
    phase_labels: dict[str, str] = field(default_factory=dict)
    data_files: dict[str, str] = field(default_factory=dict)
    #: SEED-ONLY, additive at row P0-10: how ``canon world new --template <id>``
    #: spawns this template's create runner — the registry dispatch that
    #: replaced the hardcoded platformer runner in ``cli/main.py``. An open
    #: dict (never a union, never a branch on the template id):
    #:
    #: - ``module``  — ``python -m <module>`` (in the wheel after P0-4);
    #: - ``output``  — the flag taking the output directory;
    #: - ``seed`` / ``model`` — the flags taking those, or absent;
    #: - ``counts``  — ``world new`` count flag → this runner's flag
    #:   (the wizard's count names, P.4.4 ``defaults`` keys);
    #: - ``backends``— generator kind (``llm``/``image``/``music``/``sfx``/
    #:   ``vlm``) → this runner's flag; a kind the runner has no flag for is
    #:   reported as unsupported (doctrine 4), never dropped in silence;
    #: - ``extra``   — fixed argv this template always passes;
    #: - ``orchestrate`` — the flag for the DAG scheduler, present only on a
    #:   template that HAS one (master §8 Q6 makes it the create default).
    runner: dict[str, Any] = field(default_factory=dict)
    # seed-only callables / classes
    compose: Callable | None = None
    estimator: Any = None  # (count_fn, cost_model path) — born at P0-7
    prompts: type | None = None
    validators: Callable | None = None
    archetypes: dict = field(default_factory=dict)
    schemas: dict = field(default_factory=dict)
    #: Seed-only, additive to P.3.4: the package directory the seed's
    #: ``schemas/<kind>.json`` files and ``data_files`` resolve against — the
    #: template half of ``_schema_path``'s pack-local-override precedent
    #: (``ops.py:94-99``), which ``pack info``'s ``schema_source`` needs.
    template_dir: Path | None = None

    def primary_engine(self) -> EngineEntry | None:
        """The ``primary: true`` seed engine (else the first) — the entry
        whose evaluability blocks ``pack info`` surfaces (P.2.4, P.4.6)."""
        for entry in self.engines:
            if entry.get("primary"):
                return entry
        return self.engines[0] if self.engines else None

    def stamped(self) -> dict[str, Any]:
        """The JSON-serializable registry subset (P.3.4): ``pack_type, label,
        description, vocab, capabilities, counts, entities, grids, dialogue,
        engines, tuning, world_fields, phase_labels, wizard`` — in the P.4.2
        key order. Seed-only = every callable/class plus ``data_files``,
        ``tuning_vocabulary``, ``runner`` and ``template_dir``. The
        ``template`` block (id / version hash / created_at) is added at stamp
        time (``canon.registry_ops.synthesize_registry``)."""
        out: dict[str, Any] = {
            "pack_type": self.pack_type,
            "label": self.label,
            "description": self.description,
            "vocab": list(self.vocab),
            "capabilities": list(self.capabilities),
            "counts": dict(self.counts),
            "entities": {kind: ek.stamped() for kind, ek in self.entities.items()},
            "grids": {kind: gk.stamped() for kind, gk in self.grids.items()},
        }
        if self.dialogue is not None and "dialogue" in self.capabilities:
            out["dialogue"] = self.dialogue.stamped()
        out["engines"] = copy.deepcopy(self.engines)
        out["tuning"] = copy.deepcopy(TUNING_RESERVED)
        out["world_fields"] = copy.deepcopy(self.world_fields)
        out["phase_labels"] = dict(self.phase_labels)
        out["wizard"] = copy.deepcopy(self.wizard)
        return out

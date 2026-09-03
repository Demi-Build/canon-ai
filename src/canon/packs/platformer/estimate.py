"""Cost forecasting for the platformer pack — the pack's COUNT FUNCTION plus
thin ``estimate_run`` / ``estimate_cradle`` wrappers over the shared engine
(``canon.estimator``, row P0-7; PRD §9.2's estimator hook).

Since P0-7 this module only answers "which nodes fire how many times":
LLM calls per task label across a would-run node list (level layouts
priced per width-driven section, the world/stage/enemy/item/style one-offs
by roster size), images per stage/enemy/item/player/world splash, music per
stage, the SFX catalog, and the VLM families (staleness-carried level
judgments, per-actor animation QA + authoring). Every count-per-unit knob
is DATA in ``cost_model.json``; every DOLLAR comes from ``canon.pricing``
by the selected backend's model (the three ``*_usd_per_*`` keys the data
file used to carry dissolved into that module — §3.0-C).

The engine (``canon.estimator``) owns the token tables, the pricing, the
paid/backend mask, the retry multiplier, the summation and the breakdown
shape; ``ESTIMATOR`` is the ``PackSpec.estimator`` pair this pack registers.
A fresh bible (no stages yet) prices the ``fresh_plan`` shape instead — the
two-pass bootstrap means its per-level nodes don't exist to count.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from canon.backends.anthropic import DEFAULT_MODEL
from canon.estimator import Estimator, actuals_by_task, estimate
from canon.packs.platformer.models import DEFAULT_MODELS_PATH

DEFAULT_COST_MODEL_PATH = Path(__file__).parent / "cost_model.json"

#: Kept name — the run hook's per-task calibration (now the engine's).
_actuals_by_task = actuals_by_task

#: task label per node-id family (level steps not listed cost no LLM).
_LEVEL_STEP_TASKS = {
    "collision": "plat:layout",
    "entities": "plat:placement",
    "items": "plat:item_placement",
    "foreground": "plat:decorator",
}

#: op -> level-step nodes the op runs (mirrors ops.generate_level's chain and
#: the single-step place/regenerate ops). Steps map to tasks via
#: _LEVEL_STEP_TASKS; anything else costs no LLM.
_OP_STEPS = {
    "generate": ("collision", "entities", "items", "foreground"),
    "layout": ("collision",),  # regenerate_terrain: terrain only, clears placements
    "enemies": ("entities",),
    "items": ("items",),
}


def load_cost_model(path: str | Path = DEFAULT_COST_MODEL_PATH) -> dict:
    return json.loads(Path(path).read_text())


def _vlm_model() -> str:
    """The judge's model is NOT table-driven — it comes from --vlm-model /
    CANON_PLAT_VLM_MODEL, defaulting to the backend's DEFAULT_MODEL."""
    return os.environ.get("CANON_PLAT_VLM_MODEL") or DEFAULT_MODEL


def _sections_for_level(bible: Any, level_id: str, avg: float) -> float:
    """Width-driven section count (mirrors plan_level's _TARGET_ADVANCE=20
    partition, capped at 5) when the level entity exists; the data-file
    average otherwise. A SECRET-ROOM id without an entity prices at the
    room average (1-2 sections), not the full-level one."""
    from canon.packs.platformer.level import parent_of_room_id

    level = getattr(bible, "levels", {}).get(level_id)
    if level is None:
        if parent_of_room_id(level_id) is not None:
            return 1.3  # rooms are 1-2 sections (plan_room)
        return avg
    extent = (
        int(getattr(level, "grid_height", 0))
        if getattr(level, "layout_axis", "horizontal") == "vertical"
        else int(getattr(level, "grid_width", 0))
    )
    if extent <= 0:
        return avg
    return float(min(5, max(1, round(extent / 20))))


def _task_calls(nodes: list, bible: Any, cost_model: dict) -> dict[str, float]:
    """LLM calls per task label across the would-run node list."""
    avg_sections = float(cost_model.get("sections_per_level_avg", 4.0))
    num_stages = len(getattr(bible, "stages", {}) or {})
    num_enemies = len(getattr(bible, "enemy_definitions", {}) or {})
    calls: dict[str, float] = {}

    def add(task: str, n: float) -> None:
        calls[task] = calls.get(task, 0.0) + n

    for node in nodes:
        nid = node.node_id
        if nid.startswith("level:"):
            step = nid.rsplit("/", 1)[-1]
            task = _LEVEL_STEP_TASKS.get(step)
            if task is None:
                continue
            if task == "plat:layout":
                level_id = nid.split("/")[-2]
                add(task, _sections_for_level(bible, level_id, avg_sections))
            else:
                add(task, 1)
        elif nid == "phase:plat:world":
            add("plat:world", 1)
        elif nid == "phase:plat:stage":
            add("plat:stage", max(num_stages, 1))
        elif nid == "phase:plat:enemies":
            add("plat:enemies", max(num_enemies, 1))
        elif nid == "phase:plat:items":
            add(
                "plat:items",
                max(len(getattr(bible, "items", {}) or {}), 1),
            )
        elif nid == "phase:plat:style":
            add("plat:style", max(num_stages, 1))
    return calls


def _asset_counts(nodes: list, bible: Any, cost_model: dict) -> dict:
    """Images / music / sfx / VLM families the would-run nodes generate —
    counts only; the engine prices them.

    VLM: level judgments are staleness-carried (QA v2): best = only levels
    whose layout re-runs (their renders change), worst = every level (a
    graphics/model change re-judges all). Animation QA (per actor, v1
    always-cadence) and the authoring pass (when sprite_animation runs) are
    additional per-actor families."""
    a = cost_model.get("assets", {})
    node_ids = {n.node_id for n in nodes}
    num_stages = max(len(getattr(bible, "stages", {}) or {}), 1)
    num_enemies = len(getattr(bible, "enemy_definitions", {}) or {})
    num_levels = len(getattr(bible, "levels", {}) or {})

    images = 0
    if {"phase:plat:tileset_art", "phase:plat:backdrop_art"} & node_ids:
        images += num_stages * int(a.get("images_per_stage", 10))
    if {"phase:plat:sprite_art", "phase:plat:sprite_animation"} & node_ids:
        images += num_enemies * int(a.get("images_per_enemy", 5))
        images += len(getattr(bible, "items", {}) or {}) * int(
            a.get("images_per_item", 1)
        )
        images += int(a.get("images_player", 4))
    if "phase:plat:world_art" in node_ids:
        images += int(a.get("images_world", 1))  # one splash per world
    music = (
        num_stages * int(a.get("music_per_stage", 1))
        if "phase:plat:audio" in node_ids
        else 0
    )
    sfx = int(a.get("sfx_events", 4)) if "phase:plat:audio" in node_ids else 0

    vlm: dict[str, dict] = {}
    if "plat:vlm_qa" in node_ids and num_levels:
        changed_levels = len({
            n.node_id.split("/")[-2]
            for n in nodes
            if n.node_id.startswith("level:")
        })
        actors = num_enemies + 1  # + the player
        vlm = {
            "level_judgments": {
                "best": min(changed_levels, num_levels), "worst": num_levels,
                "tokens": "vlm_per_level",
            },
            "animation_qa": {"count": actors, "tokens": "vlm_per_actor"},
            "animation_authoring": {
                "count": actors if "phase:plat:sprite_animation" in node_ids else 0,
                "tokens": "vlm_per_actor",
            },
        }
    return {"images": images, "music": music, "sfx": sfx, "vlm": vlm}


class _FreshNode:
    """Synthetic node stand-in for pricing a run-from-scratch forecast."""

    def __init__(self, node_id: str) -> None:
        self.node_id = node_id


_PLAN_ALIASES = {"stages": "num_stages", "levels": "num_levels", "enemies": "num_enemies", "items": "num_items"}


def _fresh_nodes(plan: dict) -> tuple[list, Any]:
    """The node families a full fresh run executes, at fresh_plan scale.
    Returns synthetic nodes + a bible stand-in carrying the counts.
    SECRET ROOMS (multi-room arc) add expected mini-levels:
    ``secret_rooms_avg`` rooms per level, each priced like a small level
    (its layout at the 1-2 section room average). Accepts the wizard's
    ``stages`` / ``levels`` / … spellings as well as ``num_*``."""
    plan = {**plan, **{_PLAN_ALIASES[k]: v for k, v in plan.items() if k in _PLAN_ALIASES}}
    num_levels = int(plan.get("num_levels", 9))
    num_rooms = int(round(num_levels * float(plan.get("secret_rooms_avg", 0.0))))

    class _Bible:
        stages = {f"s{i}": None for i in range(int(plan.get("num_stages", 3)))}
        enemy_definitions = {
            f"e{i}": None for i in range(int(plan.get("num_enemies", 7)))
        }
        items = {f"i{i}": None for i in range(int(plan.get("num_items", 5)))}
        levels = {f"l{i + 1}": None for i in range(num_levels)}

    nodes = [
        _FreshNode("phase:plat:world"),
        _FreshNode("phase:plat:stage"),
        _FreshNode("phase:plat:enemies"),
        _FreshNode("phase:plat:items"),
        _FreshNode("phase:plat:style"),
        _FreshNode("phase:plat:tileset_art"),
        _FreshNode("phase:plat:backdrop_art"),
        _FreshNode("phase:plat:sprite_art"),
        _FreshNode("phase:plat:sprite_animation"),
        _FreshNode("phase:plat:world_art"),
        _FreshNode("phase:plat:audio"),
        _FreshNode("plat:vlm_qa"),
    ]
    level_ids = list(_Bible.levels)
    # Expected rooms ride round-robin on the parents (l1r1, l2r1, ...) —
    # the id shape is what _sections_for_level prices the room rate by.
    level_ids += [
        f"l{(i % num_levels) + 1}r{(i // num_levels) + 1}"
        for i in range(num_rooms)
    ]
    for lid in level_ids:
        # A fresh run generates all four per-level steps — the pipeline DAG
        # unconditionally creates an `items` node per level/room (place_items
        # issues an LLM call for any non-empty item pool), so `plat:item_placement`
        # must be priced here too. (Omitting it silently under-counted every
        # world/fresh forecast by ~one cheap-tier call per level.)
        for step in ("collision", "entities", "items", "foreground"):
            nodes.append(_FreshNode(f"level:<stage>/{lid}/{step}"))
    return nodes, _Bible()


def _op_bible(pack_dir: str | Path, level_id: str) -> Any:
    """A minimal bible stand-in carrying just the target level, so
    _sections_for_level can price plat:layout by the level's real width."""
    from types import SimpleNamespace

    from canon.adapters.platformer_write import _find_level_dir
    from canon.bible.platformer import Level

    level_dir, _stage = _find_level_dir(Path(pack_dir), level_id)
    level = Level.model_validate(json.loads((level_dir / "level.json").read_text()))
    return SimpleNamespace(
        levels={level_id: level}, stages={}, enemy_definitions={}, items={}
    )


def _synthetic_op_bible(level_id: str, width: int, axis: str) -> Any:
    """Stand-in for pricing a NEW level's op (no level on disk yet) — carries
    just the requested width/axis so _sections_for_level can count layout
    sections from the form's dimensions."""
    from types import SimpleNamespace

    lvl = SimpleNamespace(
        grid_width=int(width), grid_height=int(width),
        layout_axis=axis or "horizontal",
    )
    return SimpleNamespace(
        levels={level_id: lvl}, stages={}, enemy_definitions={}, items={}
    )


def _animate_edits(pack_dir: str | Path, target: str) -> int:
    """ONE actor's animation run, PRICED BY STATES, NOT FRAMES:
    `_sheet_frames` issues exactly one ImageEditBackend.edit() per state
    per facing (art_phases.py `_animate_actor` calls it once per state,
    and once more per state only for an `asymmetric` actor). The frame
    count only widens the reference sheet inside that single call — so
    multiplying by frames would over-charge ~4x. See
    test_estimate_animate_prices_by_states_not_frames."""
    from canon.packs.platformer.ops import (
        _animate_actor_spec,
        _parse_target,
        _sprite_bible,
        load_pack,
    )

    info = load_pack(pack_dir)
    t_kind, rest = _parse_target(target)
    if t_kind not in ("enemy", "player"):
        raise ValueError("animate targets: enemy:<id> | player")
    spec_in = _animate_actor_spec(_sprite_bible(info, t_kind, rest), t_kind, rest)
    facings = 2 if spec_in.asymmetric else 1
    return len(spec_in.states) * facings


# ---------------------------------------------------------------------------
# The count function — ``PackSpec.estimator``'s pack half
# ---------------------------------------------------------------------------


def count_platformer(params: dict, bible: Any = None) -> dict:
    """Which nodes fire how many times, per ``params["scope"]``:

    - ``world`` / ``run`` — the would-run ``params["nodes"]`` against
      ``bible`` (the run hook's tree mode), or the ``fresh_plan`` shape
      merged with ``params["counts"]`` when no nodes are given;
    - the per-level ops (``generate`` / ``layout`` / ``enemies`` /
      ``items``) — the LLM steps one level op runs, ``bible`` carrying the
      target level so layouts price by real width; no assets;
    - ``animate`` — one actor's img2img edits (by states) + one VLM
      authoring call unless ``reuse_spec``;
    - ``music`` — one track.

    ``params["cost_model"]`` is injected by the engine (the knobs live there).
    """
    cost_model = params["cost_model"]
    scope = params.get("scope", "world")
    empty_vlm: dict[str, dict] = {}

    if scope in ("world", "run"):
        nodes = params.get("nodes")
        if nodes is None:
            plan = {**cost_model.get("fresh_plan", {}), **(params.get("counts") or {})}
            nodes, bible = _fresh_nodes(plan)
        return {"llm": _task_calls(nodes, bible, cost_model), **_asset_counts(nodes, bible, cost_model)}

    if scope in _OP_STEPS:
        lid = params.get("level_id") or "__preview__"
        nodes = [_FreshNode(f"level:x/{lid}/{step}") for step in _OP_STEPS[scope]]
        return {"llm": _task_calls(nodes, bible, cost_model), "images": 0, "music": 0, "sfx": 0, "vlm": empty_vlm}

    if scope == "animate":
        edits = _animate_edits(params["pack_dir"], params["target"])
        # The VLM authors the motion spec once per run — unless --reuse-spec
        # replays the stored one, which skips the vision call entirely.
        vlm = {} if params.get("reuse_spec") else {
            "animation_authoring": {"count": 1, "tokens": "vlm_per_actor"},
        }
        return {"llm": {}, "images": edits, "music": 0, "sfx": 0, "vlm": vlm}

    if scope == "music":
        return {"llm": {}, "images": 0, "music": 1, "sfx": 0, "vlm": empty_vlm}

    raise ValueError(
        f"unknown estimate scope {scope!r} (world|music|animate|{'|'.join(_OP_STEPS)})"
    )


ESTIMATOR = Estimator(
    count_fn=count_platformer,
    cost_model_path=DEFAULT_COST_MODEL_PATH,
    models_path=DEFAULT_MODELS_PATH,
    vlm_model_fn=_vlm_model,
    cost_model_env="CANON_PLAT_COST_MODEL",
    models_env="CANON_PLAT_MODELS",
)

#: The category whose backend/model the top-level §3.0-E keys report per scope.
_PRIMARY_KIND = {"animate": "image", "music": "music"}


# ---------------------------------------------------------------------------
# Cradle-facing estimate — the editor needs a BACKEND-aware, COUNT-aware price
# for a specific op it is about to fire. estimate_run (the `canon estimate`
# hook) is neither: it always prices at real-API rates and prices the fixed
# fresh_plan shape. Both ride the same engine; estimate_cradle masks the
# categories whose backend is unpaid, so a fake/none run reads $0 while still
# showing the counts (good "what an upgrade would cost" UX).
# ---------------------------------------------------------------------------


def estimate_cradle(
    scope: str,
    *,
    counts: dict | None = None,
    backends: dict[str, str] | None = None,
    pack_dir: str | Path | None = None,
    level_id: str | None = None,
    width: int | None = None,
    axis: str | None = None,
    target: str | None = None,
    reuse_spec: bool = False,
) -> dict:
    """Price ONE cradle op, backend- and count-aware.

    ``scope="world"`` prices a fresh full run at the requested ``counts``
    (stages/levels/enemies/items) — the New Project surface. The per-op scopes
    (``generate`` / ``layout`` / ``enemies`` / ``items``) price the LLM steps a
    single level op runs against an existing ``pack_dir``/``level_id``.
    ``animate`` prices one actor's animation run (``target``). In every
    case the returned USD reflects the chosen ``backends`` ($0 for fake/none).
    Same output schema as estimate_run plus ``scope`` + echoed ``backends``
    (+ the additive §3.0-E keys, row P0-7).
    """
    backends = dict(backends or {})
    params: dict[str, Any] = {"scope": scope}
    bible: Any = None
    actuals_dir: Path | None = None

    if scope == "world":
        params["counts"] = dict(counts or {})
    elif scope in _OP_STEPS:
        lid = level_id or "__preview__"
        if width is not None:
            # Pricing a NEW level's op — no level on disk; use the form's width.
            bible = _synthetic_op_bible(lid, width, axis or "")
        elif pack_dir and level_id:
            bible = _op_bible(pack_dir, level_id)
        else:
            raise ValueError(
                f"scope {scope!r} needs (pack_dir + level_id) or an explicit width"
            )
        params["level_id"] = lid
        actuals_dir = Path(pack_dir) if pack_dir else None
    elif scope == "animate":
        if not (pack_dir and target):
            raise ValueError("scope 'animate' needs pack_dir + target")
        params.update({"pack_dir": pack_dir, "target": target, "reuse_spec": reuse_spec})
    elif scope != "music":
        raise ValueError(
            f"unknown estimate scope {scope!r} "
            f"(world|music|animate|{'|'.join(_OP_STEPS)})"
        )

    result = estimate(
        ESTIMATOR, params, bible, backends=backends,
        primary_kind=_PRIMARY_KIND.get(scope, "llm"), actuals_dir=actuals_dir,
        template="platformer",
    )
    return {"scope": scope, "backends": backends, **result}


def estimate_run(ctx: Any, nodes: list, bible: Any) -> dict:
    """`canon estimate --estimator` hook: price the would-run *nodes* at
    real-API rates (no backend mask — the run hook has no selection)."""
    fresh = not (getattr(bible, "stages", {}) or {})
    params: dict[str, Any] = {"scope": "run"}
    if not fresh:
        params["nodes"] = nodes
    output_dir = Path(getattr(ctx.config, "output_dir", "."))
    result = estimate(
        ESTIMATOR, params, bible, backends=None,
        actuals_dir=None if fresh else output_dir, template="platformer",
    )
    calibration = "actuals" if (not fresh and actuals_by_task(output_dir)) else "defaults"
    return {"mode": "fresh" if fresh else "tree", "calibration": calibration, **result}

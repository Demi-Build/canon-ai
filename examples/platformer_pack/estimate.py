"""Cost forecasting for the platformer pack — `canon estimate`'s
estimator hook (PRD §9.2).

``estimate_run(ctx, nodes, bible)`` prices the WOULD-RUN node list the
CLI hands it: LLM calls per node family (statically known), tokens per
task from ``cost_model.json`` — overridden by a real tree's
``generation_stats.json`` actuals when present — priced through the
per-agent model table (models.json) and the backend PRICING table.
Diffusion/audio/VLM ride flat per-asset prices from the same data file.

Everything numeric is DATA in ``cost_model.json``; this module only
counts nodes and multiplies. A fresh bible (no stages yet) prices the
``fresh_plan`` shape instead — the two-pass bootstrap means its
per-level nodes don't exist to count.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from canon.backends.anthropic import DEFAULT_MODEL, PRICING
from examples.platformer_pack.models import ModelTable, load_models

DEFAULT_COST_MODEL_PATH = Path(__file__).parent / "cost_model.json"

#: task label per node-id family (level steps not listed cost no LLM).
_LEVEL_STEP_TASKS = {
    "collision": "plat:layout",
    "entities": "plat:placement",
    "items": "plat:item_placement",
    "foreground": "plat:decorator",
}


def load_cost_model(path: str | Path = DEFAULT_COST_MODEL_PATH) -> dict:
    return json.loads(Path(path).read_text())


def _sections_for_level(bible: Any, level_id: str, avg: float) -> float:
    """Width-driven section count (mirrors plan_level's _TARGET_ADVANCE=20
    partition, capped at 5) when the level entity exists; the data-file
    average otherwise. A SECRET-ROOM id without an entity prices at the
    room average (1-2 sections), not the full-level one."""
    from examples.platformer_pack.level import parent_of_room_id

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


def _pricing_for(model: str, what: str, warnings: list[str]) -> dict:
    """PRICING lookup that is LOUD about unpriced models — the backend
    made the identical condition a warning because a silent $0 makes
    cost reports lie; the forecast must not lie either."""
    pricing = PRICING.get(model)
    if pricing is None:
        warnings.append(
            f"no PRICING entry for model {model!r} ({what}) — forecast "
            f"prices it at $0; add it to canon.backends.anthropic.PRICING"
        )
        return {"input": 0.0, "output": 0.0}
    return pricing


def _actuals_by_task(output_dir: Path) -> dict[str, dict]:
    """Per-task-prefix token averages from a real tree's
    generation_stats.json (Chunk-1 wiring) — measured beats guessed."""
    stats_path = Path(output_dir) / "generation_stats.json"
    if not stats_path.exists():
        return {}
    try:
        by_phase = json.loads(stats_path.read_text()).get("by_phase") or {}
    except (OSError, json.JSONDecodeError):
        return {}
    sums: dict[str, dict] = {}
    for label, entry in by_phase.items():
        calls = int(entry.get("calls", 0))
        if calls <= 0 or not int(entry.get("input_tokens", 0)):
            continue  # zero-token entries (fake runs) can't calibrate
        task = ":".join(label.split(":")[:2])
        agg = sums.setdefault(task, {"calls": 0, "in": 0, "out": 0})
        agg["calls"] += calls
        agg["in"] += int(entry.get("input_tokens", 0))
        agg["out"] += int(entry.get("output_tokens", 0))
    return {
        task: {
            "input_tokens": agg["in"] / agg["calls"],
            "output_tokens": agg["out"] / agg["calls"],
        }
        for task, agg in sums.items()
        if agg["calls"]
    }


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


def _price_llm(
    calls_by_task: dict[str, float],
    cost_model: dict,
    table: ModelTable,
    actuals: dict[str, dict],
    warnings: list[str],
) -> dict:
    task_costs = cost_model.get("tasks", {})
    default_task = cost_model.get(
        "default_task", {"input_tokens": 1200, "output_tokens": 600}
    )
    worst_mult = 1 + int(cost_model.get("worst_retries", 3))
    by_task: dict[str, dict] = {}
    total_calls = 0.0
    best_usd = 0.0
    for task, calls in sorted(calls_by_task.items()):
        tokens = actuals.get(task) or task_costs.get(task) or default_task
        model = table.resolve(task) or ""
        pricing = _pricing_for(model, f"task {task}", warnings)
        usd = calls * (
            tokens["input_tokens"] * pricing["input"]
            + tokens["output_tokens"] * pricing["output"]
        )
        by_task[task] = {
            "calls": round(calls, 1),
            "model": model,
            "input_tokens_per_call": round(tokens["input_tokens"]),
            "output_tokens_per_call": round(tokens["output_tokens"]),
            "usd": round(usd, 4),
        }
        total_calls += calls
        best_usd += usd
    return {
        "by_task": by_task,
        "calls": round(total_calls, 1),
        "usd": {
            "best": round(best_usd, 4),
            "worst": round(best_usd * worst_mult, 4),
        },
    }


def _price_assets(
    nodes: list, bible: Any, cost_model: dict, warnings: list[str]
) -> dict:
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

    # VLM pricing: the judge's model is NOT table-driven — it comes from
    # --vlm-model / CANON_PLAT_VLM_MODEL, defaulting to the backend's
    # DEFAULT_MODEL. Level judgments are staleness-carried (QA v2): best
    # = only levels whose layout re-runs (their renders change), worst =
    # every level (a graphics/model change re-judges all). Animation QA
    # (per actor, v1 always-cadence) and the authoring pass (when
    # sprite_animation runs) are additional VLM families.
    vlm_best_usd = vlm_worst_usd = 0.0
    vlm_detail: dict[str, Any] = {}
    if "plat:vlm_qa" in node_ids and num_levels:
        vlm_model = (
            os.environ.get("CANON_PLAT_VLM_MODEL") or DEFAULT_MODEL
        )
        pricing = _pricing_for(vlm_model, "VLM judge", warnings)
        tokens = cost_model.get(
            "vlm_per_level", {"input_tokens": 2500, "output_tokens": 400}
        )
        per_call = (
            tokens["input_tokens"] * pricing["input"]
            + tokens["output_tokens"] * pricing["output"]
        )
        changed_levels = len({
            n.node_id.split("/")[-2]
            for n in nodes
            if n.node_id.startswith("level:")
        })
        judged_best = min(changed_levels, num_levels)
        actor_tokens = cost_model.get("vlm_per_actor") or tokens
        per_actor = (
            actor_tokens["input_tokens"] * pricing["input"]
            + actor_tokens["output_tokens"] * pricing["output"]
        )
        actors = num_enemies + 1  # + the player
        anim_qa = actors  # v1 cadence: every animated actor, every run
        authoring = (
            actors if "phase:plat:sprite_animation" in node_ids else 0
        )
        vlm_best_usd = (
            judged_best * per_call + (anim_qa + authoring) * per_actor
        )
        vlm_worst_usd = (
            num_levels * per_call + (anim_qa + authoring) * per_actor
        )
        vlm_detail = {
            "model": vlm_model,
            "level_judgments": {"best": judged_best, "worst": num_levels},
            "animation_qa": anim_qa,
            "animation_authoring": authoring,
            "usd": {
                "best": round(vlm_best_usd, 4),
                "worst": round(vlm_worst_usd, 4),
            },
        }

    image_usd = images * float(a.get("image_usd_per_call", 0.04))
    music_usd = music * float(a.get("music_usd_per_track", 0.10))
    sfx_usd = sfx * float(a.get("sfx_usd_per_event", 0.05))
    flat = image_usd + music_usd + sfx_usd
    return {
        "images": {"count": images, "usd": round(image_usd, 4)},
        "music": {"count": music, "usd": round(music_usd, 4)},
        "sfx": {"count": sfx, "usd": round(sfx_usd, 4)},
        "vlm": vlm_detail,
        "usd": {
            "best": round(flat + vlm_best_usd, 4),
            "worst": round(flat + vlm_worst_usd, 4),
        },
    }


class _FreshNode:
    """Synthetic node stand-in for pricing a run-from-scratch forecast."""

    def __init__(self, node_id: str) -> None:
        self.node_id = node_id


def _fresh_nodes(plan: dict) -> tuple[list, Any]:
    """The node families a full fresh run executes, at fresh_plan scale.
    Returns synthetic nodes + a bible stand-in carrying the counts.
    SECRET ROOMS (multi-room arc) add expected mini-levels:
    ``secret_rooms_avg`` rooms per level, each priced like a small level
    (its layout at the 1-2 section room average)."""
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


# ---------------------------------------------------------------------------
# Cradle-facing estimate — the editor needs a BACKEND-aware, COUNT-aware price
# for a specific op it is about to fire. estimate_run (the `canon estimate`
# hook) is neither: it always prices at real-API rates and prices the fixed
# fresh_plan shape. estimate_cradle reuses the SAME pricing primitives
# (_task_calls / _price_llm / _price_assets / _fresh_nodes) and then masks the
# categories whose backend is unpaid, so a fake/none run reads $0 while still
# showing the counts (good "what an upgrade would cost" UX).
# ---------------------------------------------------------------------------

#: op -> level-step nodes the op runs (mirrors ops.generate_level's chain and
#: the single-step place/regenerate ops). Steps map to tasks via
#: _LEVEL_STEP_TASKS; anything else costs no LLM.
_OP_STEPS = {
    "generate": ("collision", "entities", "items", "foreground"),
    "layout": ("collision",),  # regenerate_terrain: terrain only, clears placements
    "enemies": ("entities",),
    "items": ("items",),
}


def _paid(kind: str, backend: str | None) -> bool:
    """Whether a category's chosen backend actually bills. fake/none/"" = $0
    everywhere; local image gen is on-box ($0). Mirrors the NewProjectModal /
    per-op selectors in cradle."""
    v = (backend or "").strip().lower()
    if kind == "llm" or kind == "vlm":
        return v == "anthropic"
    if kind == "image":
        return v in {"fal", "retro", "pixellab"}
    if kind == "music":
        return v == "lyria"
    if kind == "sfx":
        return v == "elevenlabs"
    return False


def _zero_llm(llm: dict) -> None:
    for entry in llm.get("by_task", {}).values():
        entry["usd"] = 0.0
    llm["usd"] = {"best": 0.0, "worst": 0.0}


def _apply_backend_mask(
    llm: dict, assets: dict, backends: dict[str, str]
) -> None:
    """Zero the USD of any category whose backend does not bill; recompute the
    assets + roll-up totals. Counts stay untouched (so the UI can still show
    '18 images · $0, fake')."""
    if not _paid("llm", backends.get("llm")):
        _zero_llm(llm)
    if not _paid("image", backends.get("image")):
        assets["images"]["usd"] = 0.0
    if not _paid("music", backends.get("music")):
        assets["music"]["usd"] = 0.0
    if not _paid("sfx", backends.get("sfx")):
        assets["sfx"]["usd"] = 0.0
    vlm = assets.get("vlm") or {}
    if vlm and not _paid("vlm", backends.get("vlm")):
        vlm["usd"] = {"best": 0.0, "worst": 0.0}
    flat = (
        assets["images"]["usd"] + assets["music"]["usd"] + assets["sfx"]["usd"]
    )
    vlm_usd = vlm.get("usd", {"best": 0.0, "worst": 0.0})
    assets["usd"] = {
        "best": round(flat + vlm_usd["best"], 4),
        "worst": round(flat + vlm_usd["worst"], 4),
    }


def _empty_assets() -> dict:
    return {
        "images": {"count": 0, "usd": 0.0},
        "music": {"count": 0, "usd": 0.0},
        "sfx": {"count": 0, "usd": 0.0},
        "vlm": {},
        "usd": {"best": 0.0, "worst": 0.0},
    }


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
    Same output schema as estimate_run plus ``scope`` + echoed ``backends``.
    """
    cost_model = load_cost_model(
        os.environ.get("CANON_PLAT_COST_MODEL") or DEFAULT_COST_MODEL_PATH
    )
    models_path = os.environ.get("CANON_PLAT_MODELS")
    table = load_models(models_path) if models_path else load_models()
    backends = dict(backends or {})
    warnings: list[str] = []

    if scope == "world":
        plan = {**cost_model.get("fresh_plan", {}), **(counts or {})}
        nodes, bible = _fresh_nodes(plan)
        llm = _price_llm(
            _task_calls(nodes, bible, cost_model), cost_model, table, {}, warnings
        )
        assets = _price_assets(nodes, bible, cost_model, warnings)
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
        nodes = [
            _FreshNode(f"level:x/{lid}/{step}") for step in _OP_STEPS[scope]
        ]
        actuals = _actuals_by_task(Path(pack_dir)) if pack_dir else {}
        llm = _price_llm(
            _task_calls(nodes, bible, cost_model), cost_model, table, actuals,
            warnings,
        )
        assets = _empty_assets()
    elif scope == "animate":
        # ONE actor's animation run. PRICED BY STATES, NOT FRAMES:
        # `_sheet_frames` issues exactly one ImageEditBackend.edit() per state
        # per facing (art_phases.py `_animate_actor` calls it once per state,
        # and once more per state only for an `asymmetric` actor). The frame
        # count only widens the reference sheet inside that single call — so
        # multiplying by frames would over-charge ~4x. See
        # test_estimate_animate_prices_by_states_not_frames.
        if not (pack_dir and target):
            raise ValueError("scope 'animate' needs pack_dir + target")
        from examples.platformer_pack.ops import (
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
        edits = len(spec_in.states) * facings

        a = cost_model.get("assets", {})
        llm = {"by_task": {}, "calls": 0.0, "usd": {"best": 0.0, "worst": 0.0}}
        assets = _empty_assets()
        assets["images"] = {
            "count": edits,
            "usd": round(edits * float(a.get("image_usd_per_call", 0.04)), 4),
        }
        # The VLM authors the motion spec once per run — unless --reuse-spec
        # replays the stored one, which skips the vision call entirely.
        if not reuse_spec:
            vlm_model = os.environ.get("CANON_PLAT_VLM_MODEL") or DEFAULT_MODEL
            pricing = _pricing_for(vlm_model, "VLM judge", warnings)
            tok = cost_model.get("vlm_per_actor") or cost_model.get(
                "vlm_per_level", {"input_tokens": 1200, "output_tokens": 300}
            )
            per_actor = (
                tok["input_tokens"] * pricing["input"]
                + tok["output_tokens"] * pricing["output"]
            )
            assets["vlm"] = {
                "model": vlm_model,
                "animation_authoring": 1,
                "usd": {
                    "best": round(per_actor, 4), "worst": round(per_actor, 4)
                },
            }
    elif scope == "music":
        # A single-track (re)generation — flat per-track price, backend-masked
        # (fake = $0). No LLM. The mask recomputes assets/total.
        a = cost_model.get("assets", {})
        llm = {"by_task": {}, "calls": 0.0, "usd": {"best": 0.0, "worst": 0.0}}
        assets = _empty_assets()
        assets["music"] = {
            "count": 1, "usd": round(float(a.get("music_usd_per_track", 0.10)), 4)
        }
    else:
        raise ValueError(
            f"unknown estimate scope {scope!r} "
            f"(world|music|animate|{'|'.join(_OP_STEPS)})"
        )

    _apply_backend_mask(llm, assets, backends)
    return {
        "scope": scope,
        "backends": backends,
        "llm": llm,
        "assets": assets,
        "total_usd": {
            "best": round(llm["usd"]["best"] + assets["usd"]["best"], 4),
            "worst": round(llm["usd"]["worst"] + assets["usd"]["worst"], 4),
        },
        "warnings": warnings,
    }


def estimate_run(ctx: Any, nodes: list, bible: Any) -> dict:
    """`canon estimate --estimator` hook: price the would-run *nodes*."""
    cost_model = load_cost_model(
        os.environ.get("CANON_PLAT_COST_MODEL") or DEFAULT_COST_MODEL_PATH
    )
    models_path = os.environ.get("CANON_PLAT_MODELS")
    table = load_models(models_path) if models_path else load_models()

    fresh = not (getattr(bible, "stages", {}) or {})
    if fresh:
        nodes, bible = _fresh_nodes(cost_model.get("fresh_plan", {}))

    output_dir = Path(getattr(ctx.config, "output_dir", "."))
    actuals = _actuals_by_task(output_dir) if not fresh else {}

    warnings: list[str] = []
    llm = _price_llm(
        _task_calls(nodes, bible, cost_model), cost_model, table, actuals,
        warnings,
    )
    assets = _price_assets(nodes, bible, cost_model, warnings)
    return {
        "mode": "fresh" if fresh else "tree",
        "calibration": "actuals" if actuals else "defaults",
        "llm": llm,
        "assets": assets,
        "total_usd": {
            "best": round(llm["usd"]["best"] + assets["usd"]["best"], 4),
            "worst": round(llm["usd"]["worst"] + assets["usd"]["worst"], 4),
        },
        "warnings": warnings,
    }

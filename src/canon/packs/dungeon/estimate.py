"""Cost forecasting for the dungeon pack — its COUNT FUNCTION + calibrated
``cost_model.json`` over the shared engine (``canon.estimator``, row P0-7;
P0 paper W2.1.2 "a pack supplies only a count function and its calibrated
cost model — that pair is ``PackSpec.estimator``").

The counts follow ``compose.py``'s phase graph, read from the code where the
code carries them (the class loadouts and spell pools are ``specs.py`` data;
the environment cycle is ``compose._ENVIRONMENTS``) and from
``cost_model.json`` where the pipeline keeps them in method bodies (the
fixed music/SFX catalogs, portraits per stub, dialogue variants per quest
giver):

- ``story`` 1 · ``classes`` one flavor call per generated archetype ·
  ``classes:loadout`` one batched name/description call per non-empty
  spell/ability group of those archetypes · ``spell_pool`` one per pool;
- ``db:<kind>`` rooms × the per-room count, for item / monster / npc /
  event / quest (``DatabasePhase.per_map``);
- ``dialogue`` per room: the quest giver (the room's first NPC, when the
  room has a quest) gets the 4-variant tree set, every other NPC one tree;
- ``narrative`` synopsis + one intro per room + victory + defeat;
- portraits for every per-room entity stub + every class + one per room
  environment; music = the fixed catalog + one track per unique
  environment; SFX = the fixed catalog + one ambience per environment.

Token counts per task are calibrated (see ``cost_model.json._calibration``)
and every dollar comes from ``canon.pricing`` by the selected backend's
model — the dungeon has no per-agent model table, so every task prices at
one model (the anthropic backend's ``DEFAULT_MODEL`` unless ``--model``).

Deliberately absent, by row ownership: the wizard that renders these
numbers (P0-10 reads ``world estimate --template dungeon``), the ledger
accuracy rows (A6), per-op scopes (the dungeon has no per-level ops yet —
W2.0's pull-in).
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from canon.backends.anthropic import DEFAULT_MODEL
from canon.estimator import Estimator, estimate
from canon.packs.dungeon.compose import _ENVIRONMENTS
from canon.packs.dungeon.specs import MAZEWORLD_CLASS_LOADOUTS, MAZEWORLD_POOL_SPECS

DEFAULT_COST_MODEL_PATH = Path(__file__).parent / "cost_model.json"

#: The per-room ``DatabasePhase`` kinds, in ``compose_mazeworld_specs`` order.
_PER_ROOM_KINDS = ("item", "monster", "npc", "event", "quest")


def _group_calls(group: Any) -> int:
    """One batched ``generate_named_entries`` call per non-empty loadout group."""
    return 1 if group is not None and (group.starting or group.pool) else 0


def count_dungeon(params: dict, bible: Any = None) -> dict:
    """Which phases fire how many times for a fresh dungeon run at
    ``params["counts"]`` (merged over the cost model's ``fresh_plan``; keys
    ``rooms / npc / monster / item / event / quest / class`` — the P.4.4
    wizard defaults). ``bible`` is unused: the dungeon pipeline is linear
    (no DAG), so there is no partial-tree mode to count.

    ``params["cost_model"]`` is injected by the engine."""
    cost_model = params["cost_model"]
    plan = {**cost_model.get("fresh_plan", {}), **(params.get("counts") or {})}
    rooms = max(int(plan.get("rooms", 3)), 0)
    per_room = {kind: max(int(plan.get(kind, 0)), 0) for kind in _PER_ROOM_KINDS}
    # ClassPhase generates one class per loadout spec; compose slices the list.
    loadouts = MAZEWORLD_CLASS_LOADOUTS[: max(int(plan.get("class", 0)), 0)]
    n_class = len(loadouts)
    a = cost_model.get("assets", {})
    envs = min(rooms, len(_ENVIRONMENTS))

    llm: dict[str, float] = {"story": 1}
    if n_class:
        llm["classes"] = n_class
        loadout_calls = sum(_group_calls(s.spells) + _group_calls(s.abilities) for s in loadouts)
        if loadout_calls:
            llm["classes:loadout"] = loadout_calls
    llm["spell_pool"] = len(MAZEWORLD_POOL_SPECS)
    for kind in _PER_ROOM_KINDS:
        if rooms * per_room[kind]:
            llm[f"db:{kind}"] = rooms * per_room[kind]
    npc, quest = per_room["npc"], per_room["quest"]
    givers = min(int(a.get("quest_givers_per_room", 1)), npc) if quest else 0
    dialogue = rooms * (givers * int(a.get("dialogue_variants_per_quest_giver", 4)) + (npc - givers))
    if dialogue:
        llm["dialogue"] = dialogue
    llm["narrative"] = 3 + rooms  # synopsis + per-room intro + victory + defeat

    stubs = rooms * sum(per_room.values())
    images = (
        stubs * int(a.get("portraits_per_entity", 1))
        + n_class * int(a.get("portraits_per_class", 1))
        + rooms * int(a.get("portraits_per_room", 1))
    )
    music = int(a.get("music_fixed_tracks", 5)) + envs * int(a.get("music_per_environment", 1))
    sfx = int(a.get("sfx_fixed_effects", 12)) + envs * int(a.get("sfx_per_environment", 1))
    return {"llm": llm, "images": images, "music": music, "sfx": sfx, "vlm": {}}


ESTIMATOR = Estimator(
    count_fn=count_dungeon,
    cost_model_path=DEFAULT_COST_MODEL_PATH,
    models_path=None,
    default_model=DEFAULT_MODEL,
    cost_model_env="CANON_DUNGEON_COST_MODEL",
)


def estimate_cradle(
    scope: str = "world",
    *,
    counts: dict | None = None,
    backends: dict[str, str] | None = None,
    model: str | None = None,
) -> dict:
    """Price a fresh dungeon run at ``counts`` + ``backends`` — the same
    output schema as the platformer's ``estimate_cradle`` (cradle's
    ``CostEstimate`` + the additive §3.0-E keys) with ``template:
    "dungeon"``. ``model`` prices every task at that LLM id instead of the
    anthropic default. Only the ``world`` scope exists for the dungeon."""
    if scope != "world":
        raise ValueError(f"unknown dungeon estimate scope {scope!r} (world)")
    backends = dict(backends or {})
    est = replace(ESTIMATOR, default_model=model) if model else ESTIMATOR
    result = estimate(
        est, {"scope": scope, "counts": dict(counts or {})}, None,
        backends=backends, template="dungeon",
    )
    return {"scope": scope, "backends": backends, **result}


def estimate_run(ctx: Any, nodes: list, bible: Any) -> dict:
    """`canon estimate --estimator` hook: the dungeon pipeline is linear, so
    a run prices the full fresh shape at ``ctx.config``'s ``num_maps`` +
    ``counts`` at real-API rates (no backend selection)."""
    config = getattr(ctx, "config", None)
    counts = dict(getattr(config, "counts", None) or {})
    num_maps = getattr(config, "num_maps", None)
    if num_maps is not None:
        counts.setdefault("rooms", int(num_maps))
    est = ESTIMATOR
    model = getattr(config, "model", None)
    if model:
        est = replace(ESTIMATOR, default_model=model)
    result = estimate(est, {"scope": "world", "counts": counts}, bible, backends=None, template="dungeon")
    return {"mode": "fresh", "calibration": "defaults", **result}

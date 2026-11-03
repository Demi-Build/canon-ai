"""``canon.estimator`` — the cost-forecast ENGINE every pack prices through
(the core/pack split).

Extracted from ``canon.packs.platformer.estimate`` (the ~70% that was never
platformer-specific): the cost-model JSON schema + loader, the price lookup
(now :mod:`canon.pricing` — the only price source, §3.0-C), the paid /
backend-mask logic, the retry multipliers, the summation and the
per-generator breakdown shape cradle's ``CostEstimate`` reads (keys
unchanged), plus the additive estimate keys ``low / high / backend / model /
unitCount`` (and ``accuracy``) on the top-level result and on every asset
block.

A pack contributes ONE pair — ``PackSpec.estimator = Estimator(count_fn,
cost_model_path, …)`` — where ``count_fn(params, bible | None) -> counts``
answers "which nodes fire how many times" for a scope::

    {
      "llm":    {"<task>": calls, ...},          # LLM calls per cost-model task
      "images": n, "music": n, "sfx": n,        # flat per-unit assets
      "image_px": px,                            # OPTIONAL: the one generation
                                                 # size those images are asked
                                                 # at (a mixed scope omits it)
      "vlm":    {"<family>": {"best": b, "worst": w, "tokens": "<cost-model key>"}
                 | {"count": n, "tokens": "<cost-model key>"}, ...},
    }

Everything numeric the pack owns is DATA in its ``cost_model.json``
(tokens per task, retry multiplier, counts-per-unit knobs, the fresh plan);
every DOLLAR comes from :mod:`canon.pricing` by the selected backend's
model — a cost model carries no price. The engine only counts and
multiplies.

MEASURED BEATS GUESSED, on both sides. When the caller names an
``actuals_dir``, the pack's own ``generation_stats.json`` overrides the
shipped tables: :func:`actuals_by_task` for per-task tokens and
:func:`actuals_by_unit` for per-unit asset dollars (a real run's
``image_cost_usd / image_successes`` beats a published $0.008–$0.185 span).
Runs that spent nothing — every fake / none backend — calibrate nothing, and
which source a forecast used is on the estimate as ``calibration``
(``"actuals"`` | ``"defaults"``), never left silent.

Deliberately absent, by row ownership: the ledger's per-row accuracy flag
and dashboard read side (A6), the wizard that renders these numbers (P0-10),
the Meshy backend (W2.2 — its rows already sit in ``canon.pricing``).
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from canon import model_table, pricing

#: The additive estimate keys the engine appends to the top-level result and
#: to every asset block (the original keys are untouched — cradle's
#: ``CostEstimate`` contract holds; ``vlm.model`` predates this row and is NOT
#: in the vlm block's additive set).
ADDITIVE_KEYS: tuple[str, ...] = ("low", "high", "backend", "model", "unitCount", "accuracy")
VLM_ADDITIVE_KEYS: tuple[str, ...] = ("low", "high", "backend", "unitCount", "accuracy")
TOP_ADDITIVE_KEYS: tuple[str, ...] = ADDITIVE_KEYS + ("template", "unitLabel", "calibration")

#: ``calibration`` is additive on the cradle/op shapes but PRE-DATES the split
#: on the RUN hook's ``{mode, calibration, …}`` — :func:`strip_additive` keeps
#: it whenever the result carries ``mode``.
_RUN_SHAPE_MARKER = "mode"

#: What ``canon estimate`` (the ``--estimator`` hook, no backend selection)
#: prices at: "real-API rates" = the default paid backend per kind.
DEFAULT_PAID_BACKENDS: dict[str, str] = {
    "llm": "anthropic", "vlm": "anthropic", "image": "fal", "music": "lyria", "sfx": "elevenlabs",
}

_DEFAULT_TASK = {"input_tokens": 1200, "output_tokens": 600}
_DEFAULT_VLM_TOKENS = {"input_tokens": 2500, "output_tokens": 400}


@dataclass(frozen=True)
class Estimator:
    """The pair a pack registers on ``PackSpec.estimator``.

    ``count_fn(params, bible)`` receives the loaded cost model as
    ``params["cost_model"]`` (the counts-per-unit knobs live there).
    ``models_path`` names a per-agent model table (the platformer's
    ``models.json``); ``None`` prices every task at ``default_model``.
    ``vlm_model_fn`` names the VLM judge model (the platformer reads an env
    var); ``None`` prices VLM at the vlm backend's default row. The two
    ``*_env`` names let a run override the data files (the platformer's
    ``CANON_PLAT_COST_MODEL`` / ``CANON_PLAT_MODELS``).
    """

    count_fn: Callable[[dict, Any], dict]
    cost_model_path: Path
    models_path: Path | None = None
    default_model: str | None = None
    vlm_model_fn: Callable[[], str] | None = None
    cost_model_env: str | None = None
    models_env: str | None = None

    def cost_model(self) -> dict:
        path = (os.environ.get(self.cost_model_env) if self.cost_model_env else None) or self.cost_model_path
        return load_cost_model(path)

    def fresh_plan(self) -> dict:
        """The cost model's ``fresh_plan`` — the default counts an estimate
        prices when the caller names none (``world estimate``'s defaults)."""
        return dict(self.cost_model().get("fresh_plan", {}))

    def resolver(self) -> Callable[[str], str | None]:
        """``task -> model id``: the per-agent table when the pack ships one,
        else the single ``default_model``."""
        path = (os.environ.get(self.models_env) if self.models_env else None) or self.models_path
        if path is not None:
            # The models.json FORMAT is core (``canon.model_table``, PRD §9.1
            # "per-agent model assignment as pack data"): the engine resolves
            # any pack's table without importing a pack.
            return model_table.load_models(path).resolve
        default = self.default_model
        return lambda _task: default


def load_cost_model(path: str | Path) -> dict:
    return json.loads(Path(path).read_text())


# ---------------------------------------------------------------------------
# Price lookups — every dollar goes through canon.pricing
# ---------------------------------------------------------------------------


def _llm_pricing_for(model: str, what: str, warnings: list[str]) -> dict[str, float]:
    """Per-token ``{"input", "output"}`` for ``model`` — LOUD when unpriced:
    the warning names the model and the table, and the call prices at $0
    flagged ``estimated`` (§3.0-B: never a silent $0)."""
    row = pricing.price_for("llm", model, warnings)
    if row is None:
        warnings[-1] = f"{warnings[-1]} ({what})"
        return pricing.per_token(pricing.zero_row("llm"))
    return pricing.per_token(row)


def _unit_row(
    kind: str, backend: str | None, warnings: list[str], model_override: str | None = None
) -> tuple[str | None, dict[str, Any]]:
    """``(model, row)`` a flat-per-unit category prices by: the CALL's own
    model when it named one, else the backend's default model's row when the
    backend bills, else the $0 row (an unpaid backend — fake / none / local —
    is a real $0, not an unpriced one).

    The override never resurrects an unpaid backend: a call that names a model
    on ``fake`` still costs nothing."""
    model = pricing.default_model(kind, backend)
    if model is None:
        return None, pricing.zero_row(kind)
    model = model_override or model
    row = pricing.price_for(kind, model, warnings)
    return model, (row if row is not None else pricing.zero_row(kind))


def _priced_override(kind: str, named: str, warnings: list[str]) -> str | None:
    """``named`` when :mod:`canon.pricing` knows its rate, else ``None``.

    A named model beats the routing table's pick — the caller passes one only
    where the run really will use it — but only while its price is real. An id
    canon cannot price keeps the table's model AND the table's price behind the
    loud warning ``price_for`` appends: naming an unknown model must not turn a
    paid card into a confident $0."""
    return named if pricing.price_for(kind, named, warnings) is not None else None


def generation_stats(output_dir: str | Path) -> dict:
    """A tree's ``generation_stats.json``, or ``{}`` (absent / unreadable).

    The ONE reader both calibration sources go through — and, promoted to
    the public surface, the one reader a create's MEASURED money goes
    through too (``world new``'s ``actual_usd``, the agent's create spend
    row). Every template writes this file standalone at the pack root (the
    path ``config.py`` declares canonical); only the dungeon also embeds a
    copy in its manifest, so a reader that looks for the embedded block
    misses every platformer run. An empty dict means "unmeasured", never
    "$0": callers must not turn an absent file into a zero."""
    stats_path = Path(output_dir) / "generation_stats.json"
    if not stats_path.exists():
        return {}
    try:
        loaded = json.loads(stats_path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


#: The pre-promotion name, kept so the module's own callers and any test
#: that reached for it keep working.
_generation_stats = generation_stats


def actuals_by_task(output_dir: str | Path) -> dict[str, dict]:
    """Per-task-prefix token averages from a real tree's
    ``generation_stats.json`` — measured beats guessed. Zero-token entries
    (fake runs) never calibrate."""
    by_phase = _generation_stats(output_dir).get("by_phase") or {}
    sums: dict[str, dict] = {}
    for label, entry in by_phase.items():
        calls = int(entry.get("calls", 0))
        if calls <= 0 or not int(entry.get("input_tokens", 0)):
            continue
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


#: flat asset kind -> (units-produced field, $-spent field, the sibling whose
#: units share that $ bucket). ``generation_stats.json`` keeps ONE
#: ``audio_cost_usd`` for music + SFX, so an audio kind only calibrates when
#: the recorded run produced none of its sibling — a mixed audio run leaves
#: both on the published rates rather than inventing a split.
_UNIT_ACTUAL_FIELDS: dict[str, tuple[str, str, str]] = {
    "image": ("image_successes", "image_cost_usd", ""),
    "music": ("music_succeeded", "audio_cost_usd", "sfx_succeeded"),
    "sfx": ("sfx_succeeded", "audio_cost_usd", "music_succeeded"),
}


def actuals_by_unit(output_dir: str | Path) -> dict[str, dict[str, Any]]:
    """Measured ``{kind: {"usd": $/unit, "backend": <id>}}`` from a real tree's
    ``generation_stats.json`` — the per-unit sibling of :func:`actuals_by_task`,
    and the reason a pack that has generated art forecasts from what it really
    paid instead of a published range (PixelLab's spans $0.008–$0.185).

    Same exclusions, same reason: a run that produced no units or spent nothing
    (every fake / none backend) calibrates nothing, so a fake tree falls back to
    the table. The recorded BACKEND rides along — a figure measured on one
    backend must never price another (:func:`_unit_actual` matches them).

    What comes back is a MEAN, and on the image lane a mean per BILLED CALL:
    the producer meters every call it makes, so one sprite that hit the alpha
    gate's retries counts several. It is therefore a best case per unit, never
    a ceiling — see ``worst_mult`` in :func:`_flat_block`.
    """
    stats = _generation_stats(output_dir)
    out: dict[str, dict[str, Any]] = {}
    for kind, (units_field, cost_field, sibling) in _UNIT_ACTUAL_FIELDS.items():
        units = int(stats.get(units_field, 0) or 0)
        spent = float(stats.get(cost_field, 0.0) or 0.0)
        shared = int(stats.get(sibling, 0) or 0) if sibling else 0
        if units <= 0 or spent <= 0.0 or shared > 0:
            continue
        backend = str(stats.get(f"{kind}_backend", "") or "")
        out[kind] = {"usd": spent / units, "backend": backend}
    return out


def _unit_actual(
    unit_actuals: dict[str, dict[str, Any]], kind: str, backend: str | None
) -> float | None:
    """The measured $/unit for ``kind`` when the recorded run used the very
    backend this forecast selected — otherwise ``None`` (the table prices it)."""
    entry = unit_actuals.get(kind)
    if not entry:
        return None
    recorded = str(entry.get("backend", "")).strip().lower()
    if not recorded or recorded != (backend or "").strip().lower():
        return None
    return float(entry["usd"])


def _tier_px(key: str) -> float | None:
    """``"0.5K"`` → 512.0 — the pixel edge a ``by_resolution`` key names."""
    try:
        return float(key.strip().rstrip("Kk")) * 1024
    except ValueError:
        return None


def _resolution_price(row: dict[str, Any], px: int | None) -> float | None:
    """The published price for a row that bills BY RESOLUTION (the nano-banana
    schedules), at the smallest tier that holds ``px`` — a 512px request on a
    1K-base model is the 0.5K rate, not the model's base-to-4K span. ``None``
    when the row is flat-priced or the caller named no size."""
    schedule = row.get("by_resolution")
    if not isinstance(schedule, dict) or not schedule or not px:
        return None
    tiers = [(edge, float(usd)) for key, usd in schedule.items() if (edge := _tier_px(key)) is not None]
    if not tiers:
        return None
    fits = [t for t in tiers if t[0] >= float(px)]
    return min(fits)[1] if fits else max(tiers)[1]


# ---------------------------------------------------------------------------
# LLM pricing — tokens per task × calls × the model's per-token row
# ---------------------------------------------------------------------------


def price_llm(
    calls_by_task: dict[str, float],
    cost_model: dict,
    resolve: Callable[[str], str | None],
    actuals: dict[str, dict],
    warnings: list[str],
) -> dict:
    """The ``llm`` block: per-task calls/model/tokens/usd, the call total,
    and ``usd.best`` / ``usd.worst`` (worst = best × (1 + worst_retries))."""
    task_costs = cost_model.get("tasks", {})
    default_task = cost_model.get("default_task", _DEFAULT_TASK)
    worst_mult = 1 + int(cost_model.get("worst_retries", 3))
    by_task: dict[str, dict] = {}
    total_calls = 0.0
    best_usd = 0.0
    for task, raw_calls in sorted(calls_by_task.items()):
        calls = float(raw_calls)  # one shape across templates: calls is a float
        tokens = actuals.get(task) or task_costs.get(task) or default_task
        model = resolve(task) or ""
        per_token = _llm_pricing_for(model, f"task {task}", warnings)
        usd = calls * (
            tokens["input_tokens"] * per_token["input"]
            + tokens["output_tokens"] * per_token["output"]
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


# ---------------------------------------------------------------------------
# Asset pricing — flat per-unit rows + the VLM token families
# ---------------------------------------------------------------------------


def _flat_block(
    kind: str,
    count: int,
    backend: str | None,
    warnings: list[str],
    *,
    unit_actuals: dict[str, dict[str, Any]] | None = None,
    px: int | None = None,
    model_override: str | None = None,
    worst_mult: float = 1.0,
) -> dict:
    """One flat-per-unit category's block, priced in the order measured-beats-
    guessed demands: this pack's own measured $/unit for the SELECTED backend
    first, then the row's by-resolution tier when the request's size is known,
    then the published range. An unpaid backend keeps its real $0 (the zero row
    has neither an actual nor a schedule).

    ``model_override`` is the model the CALL named (already checked against
    :mod:`canon.pricing`); it replaces the backend's default for both the
    quoted model and the rate.

    ``worst_mult`` is the measured path's HEADROOM, and only the measured
    path's: a measurement is a MEAN over the billed calls a run made, not the
    ceiling a call can hit, so quoting it as ``low == high`` would promise a
    "spend up to" the run can exceed. The table path needs none (it already
    spans ``usd``..``usd_high``) and neither does the tier path (a size-billed
    SKU at a known size really is a point). :func:`price_assets` passes the
    pack's own ``worst_retries`` for the lane whose units cost more than one
    billed call — the same knob, and the same ``best × (1 + worst_retries)``
    arithmetic, :func:`price_llm` already applies to its calibrated tokens, so
    the two lanes of one estimate agree about what a calibrated number owes a
    worst case."""
    model, row = _unit_row(kind, backend, warnings, model_override)
    # ``generation_stats.json`` records the backend a run used, never its model,
    # so a measured $/unit only speaks for the model that backend DEFAULTS to.
    # A call that names a different one prices off that model's own row: the
    # table beats a measurement of something else.
    on_default = model is not None and model == pricing.default_model(kind, backend)
    measured = _unit_actual(unit_actuals or {}, kind, backend) if on_default else None
    tier = _resolution_price(row, px) if model is not None else None
    if measured is not None:
        unit = measured
        unit_high = measured * max(1.0, float(worst_mult))
    elif tier is not None:
        # A named resolution is a POINT on the schedule, not a span: the row's
        # usd..usd_high band exists only because the size was unknown.
        unit = unit_high = tier
    else:
        unit = float(row.get("usd", 0.0))
        unit_high = float(row.get("usd_high", unit))
    return {
        "count": count,
        "usd": round(count * unit, 4),
        "low": round(count * unit, 4),
        "high": round(count * unit_high, 4),
        "backend": backend,
        "model": model,
        "unitCount": count,
        "accuracy": row.get("accuracy", pricing.ESTIMATED),
    }


def _vlm_block(
    families: dict[str, dict], cost_model: dict, backend: str | None, vlm_model: str, warnings: list[str]
) -> dict:
    """The ``vlm`` block in the original shape — ``{model, <family>…, usd}``
    — with each family priced at its named token row (``vlm_per_level``,
    ``vlm_per_actor``; a missing row falls back to ``vlm_per_level``, then
    the built-in default). A family is either ``{best, worst}`` (staleness-
    carried judgments) or a plain count. Empty families = ``{}``."""
    if not families:
        return {}
    per_token = _llm_pricing_for(vlm_model, "VLM judge", warnings)
    default_tokens = cost_model.get("vlm_per_level", _DEFAULT_VLM_TOKENS)
    best = worst = 0.0
    units_best = units_worst = 0
    detail: dict[str, Any] = {"model": vlm_model}
    for family, spec in families.items():
        tokens = cost_model.get(spec.get("tokens", "vlm_per_level")) or default_tokens
        per_call = tokens["input_tokens"] * per_token["input"] + tokens["output_tokens"] * per_token["output"]
        if "count" in spec:
            n_best = n_worst = int(spec["count"])
            detail[family] = n_best
        else:
            n_best, n_worst = int(spec.get("best", 0)), int(spec.get("worst", 0))
            detail[family] = {"best": n_best, "worst": n_worst}
        best += n_best * per_call
        worst += n_worst * per_call
        units_best += n_best
        units_worst += n_worst
    detail["usd"] = {"best": round(best, 4), "worst": round(worst, 4)}
    detail.update({
        "low": round(best, 4),
        "high": round(worst, 4),
        "backend": backend,
        "unitCount": units_best,
        "accuracy": pricing.ESTIMATED,
    })
    return detail


def price_assets(
    counts: dict,
    cost_model: dict,
    backends: dict[str, str | None],
    vlm_model: str,
    warnings: list[str],
    *,
    unit_actuals: dict[str, dict[str, Any]] | None = None,
    models: dict[str, str] | None = None,
) -> dict:
    """The ``assets`` block: images / music / sfx (flat per unit, priced by
    the selected backend's row, or by the model ``models`` names for that kind
    when the CALL named one) + the VLM families + the roll-up
    ``usd.best`` (= Σ unit price + vlm best) / ``usd.worst`` (= Σ the rows'
    published high + vlm worst).

    ``counts["image_px"]`` (optional) is the generation resolution the image
    units are requested at — a scope that knows it (one asset, one size) gets
    the by-resolution rate; a scope that mixes sizes must NOT set it.

    Only the IMAGE lane's measured price carries retry headroom: an image unit
    is metered per BILLED CALL and the producers retry (the alpha gate, the
    content-policy pass), so its measured $/unit is a mean the ceiling must sit
    above. A music/SFX unit is exactly one billed call at a flat per-call rate,
    so its measurement is a genuine point and inventing a spread for it would
    over-quote."""
    px = int(counts.get("image_px") or 0) or None
    named = models or {}
    # The same knob, read the same way, as price_llm's worst case.
    image_worst_mult = 1 + int(cost_model.get("worst_retries", 3))
    images = _flat_block("image", int(counts.get("images", 0)), backends.get("image"), warnings,
                         unit_actuals=unit_actuals, px=px, model_override=named.get("image"),
                         worst_mult=image_worst_mult)
    music = _flat_block("music", int(counts.get("music", 0)), backends.get("music"), warnings,
                        unit_actuals=unit_actuals, model_override=named.get("music"))
    sfx = _flat_block("sfx", int(counts.get("sfx", 0)), backends.get("sfx"), warnings,
                      unit_actuals=unit_actuals, model_override=named.get("sfx"))
    vlm = _vlm_block(counts.get("vlm") or {}, cost_model, backends.get("vlm"), vlm_model, warnings)
    return {"images": images, "music": music, "sfx": sfx, "vlm": vlm, "usd": _assets_usd(images, music, sfx, vlm)}


def _assets_usd(images: dict, music: dict, sfx: dict, vlm: dict) -> dict:
    flat = images["usd"] + music["usd"] + sfx["usd"]
    flat_high = images["high"] + music["high"] + sfx["high"]
    vlm_usd = vlm.get("usd", {"best": 0.0, "worst": 0.0}) if vlm else {"best": 0.0, "worst": 0.0}
    return {
        "best": round(flat + vlm_usd["best"], 4),
        "worst": round(flat_high + vlm_usd["worst"], 4),
    }


# ---------------------------------------------------------------------------
# The backend mask — fake / none / local read $0, counts stay visible
# ---------------------------------------------------------------------------


def _zero_llm(llm: dict) -> None:
    for entry in llm.get("by_task", {}).values():
        entry["usd"] = 0.0
    llm["usd"] = {"best": 0.0, "worst": 0.0}


def _zero_flat(block: dict) -> None:
    block["usd"] = 0.0
    block["low"] = 0.0
    block["high"] = 0.0


def apply_backend_mask(llm: dict, assets: dict, backends: dict[str, str | None]) -> None:
    """Zero the USD of any category whose backend does not bill (per
    :func:`canon.pricing.is_paid`); recompute the assets roll-up. Counts
    stay untouched (so the UI can still show '18 images · $0, fake')."""
    if not pricing.is_paid("llm", backends.get("llm")):
        _zero_llm(llm)
    for kind, key in (("image", "images"), ("music", "music"), ("sfx", "sfx")):
        if not pricing.is_paid(kind, backends.get(kind)):
            _zero_flat(assets[key])
    vlm = assets.get("vlm") or {}
    if vlm and not pricing.is_paid("vlm", backends.get("vlm")):
        vlm["usd"] = {"best": 0.0, "worst": 0.0}
        vlm["low"] = 0.0
        vlm["high"] = 0.0
    assets["usd"] = _assets_usd(assets["images"], assets["music"], assets["sfx"], vlm)


# ---------------------------------------------------------------------------
# The entry point
# ---------------------------------------------------------------------------


def _unit_count(llm: dict, assets: dict) -> int:
    vlm = assets.get("vlm") or {}
    return (
        int(round(llm.get("calls", 0.0)))
        + int(assets["images"]["count"])
        + int(assets["music"]["count"])
        + int(assets["sfx"]["count"])
        + int(vlm.get("unitCount", 0))
    )


def estimate(
    est: Estimator,
    params: dict,
    bible: Any = None,
    *,
    backends: dict[str, str] | None = None,
    primary_kind: str = "llm",
    actuals_dir: str | Path | None = None,
    template: str | None = None,
    unit_label: str | None = None,
    models: dict[str, str] | None = None,
) -> dict:
    """Price one forecast: ``count_fn`` → :func:`price_llm` +
    :func:`price_assets` → the backend mask → the roll-up.

    ``backends`` = the selected backend per kind (cradle's selectors);
    ``None`` prices at real-API rates (:data:`DEFAULT_PAID_BACKENDS`, the
    ``canon estimate`` hook's convention) with no mask. ``primary_kind``
    names the category whose backend/model the top-level additive keys report
    (``llm`` for world / per-level ops, ``image`` for an animation run,
    ``music`` for a track). ``actuals_dir`` is a real tree whose
    ``generation_stats.json`` calibrates BOTH sides — per-task tokens
    (:func:`actuals_by_task`) and per-unit asset dollars
    (:func:`actuals_by_unit`). ``unit_label`` is the scope's own "what work is
    this" copy, carried on the estimate so the card names the units it priced.
    ``models`` names the model to price a kind at (``{"llm": …}`` /
    ``{"image": …}``), beating the pack's routing table for both the dollars
    and the quoted ``model``. The CALLER owns that claim — it passes one only
    where the run really will use it. An id :mod:`canon.pricing` has no row for
    is ignored — loudly, in ``warnings`` — rather than pricing a paid call
    at $0.

    Returns ``{llm, assets, total_usd, warnings, low, high, backend, model,
    unitCount, accuracy, calibration[, template][, unitLabel]}`` — the caller
    prepends its own leading keys (``scope`` + ``backends`` for cradle,
    ``mode`` for the run hook) so the original key order is preserved.

    ``calibration`` is never silent: ``actuals`` when a figure in this estimate
    came from the pack's own measured runs, ``defaults`` when every figure is
    the shipped table's.
    """
    cost_model = est.cost_model()
    warnings: list[str] = []
    named = {
        kind: priced
        for kind, raw in (models or {}).items()
        if (want := str(raw or "").strip())
        and (priced := _priced_override(kind, want, warnings))
    }
    # A named LLM model replaces the whole routing table for this forecast, the
    # same way an explicit `--model` disables the table for a whole run.
    resolve = (lambda _task: named["llm"]) if "llm" in named else est.resolver()
    masked = backends is not None
    chosen: dict[str, str | None] = {
        kind: (backends.get(kind) if masked else DEFAULT_PAID_BACKENDS.get(kind))
        for kind in ("llm", "vlm", "image", "music", "sfx")
    }
    if est.vlm_model_fn is not None:
        vlm_model = est.vlm_model_fn()
    else:
        vlm_model = pricing.default_model("vlm", chosen.get("vlm")) or pricing.default_model("vlm", "anthropic") or ""

    counts = est.count_fn({**params, "cost_model": cost_model}, bible)
    calls_by_task = dict(counts.get("llm") or {})
    actuals = actuals_by_task(actuals_dir) if actuals_dir else {}
    unit_actuals = actuals_by_unit(actuals_dir) if actuals_dir else {}
    llm = price_llm(calls_by_task, cost_model, resolve, actuals, warnings)
    assets = price_assets(counts, cost_model, chosen, vlm_model, warnings,
                          unit_actuals=unit_actuals, models=named)
    if masked:
        apply_backend_mask(llm, assets, chosen)

    total = {
        "best": round(llm["usd"]["best"] + assets["usd"]["best"], 4),
        "worst": round(llm["usd"]["worst"] + assets["usd"]["worst"], 4),
    }
    if primary_kind == "llm":
        # A forecast that prices exactly ONE task names THAT task's model — a
        # per-agent table routes it, and the card would otherwise print the
        # table's default beside a price computed at the cheap tier's rate.
        by_task = llm.get("by_task") or {}
        single = next(iter(by_task.values()))["model"] if len(by_task) == 1 else ""
        model = single or resolve("_default") or est.default_model or ""
    else:
        block = assets.get({"image": "images"}.get(primary_kind, primary_kind)) or {}
        model = block.get("model")
    out: dict[str, Any] = {
        "llm": llm,
        "assets": assets,
        "total_usd": total,
        "warnings": warnings,
        "low": total["best"],
        "high": total["worst"],
        "backend": chosen.get(primary_kind),
        "model": model,
        "unitCount": _unit_count(llm, assets),
        "accuracy": pricing.ESTIMATED,
        "calibration": _calibration(calls_by_task, counts, chosen, actuals, unit_actuals, named),
    }
    if template is not None:
        out["template"] = template
    if unit_label is not None:
        out["unitLabel"] = unit_label
    return out


def _calibration(
    calls_by_task: dict[str, float],
    counts: dict,
    chosen: dict[str, str | None],
    actuals: dict[str, dict],
    unit_actuals: dict[str, dict[str, Any]],
    named: dict[str, str] | None = None,
) -> str:
    """``"actuals"`` when a figure in THIS estimate came from the pack's own
    measured runs, ``"defaults"`` otherwise.

    It answers "did calibration actually bite", not "does a stats file exist":
    a tree whose recorded tasks/backends are none of the ones this scope prices
    forecasts from the shipped tables, and must say so. Same for a call that
    NAMED a model the recorded runs never used — :func:`_flat_block` prices
    that off the table, so this must not claim the measurement.
    """
    if any(task in actuals for task in calls_by_task):
        return "actuals"
    for kind, key in (("image", "images"), ("music", "music"), ("sfx", "sfx")):
        override = (named or {}).get(kind)
        if override and override != pricing.default_model(kind, chosen.get(kind)):
            continue
        if int(counts.get(key, 0) or 0) and _unit_actual(unit_actuals, kind, chosen.get(kind)) is not None:
            return "actuals"
    return "defaults"


def db_row_task(pack_dir: str | Path, entity_type: str) -> tuple[str, str]:
    """``(cost-model task, kind word)`` for ONE db row of ``entity_type``, both
    read from the pack's own registry entry: ``EntityKind.phase_label`` is the
    very label the pipeline stamps on the generating call — so the pack's
    ``cost_model.json`` already carries its tokens and its recorded runs
    already calibrate it — and ``EntityKind.kind`` is that pack's own singular
    word for the row (``EntityKind.label`` is the plural collection name, which
    reads wrong on "1 … row").

    Registry data, never a list of kinds in this module — a pack that adds a
    type gets priced without a code change here. Raises ``ValueError`` for a
    type this pack does not have, which the caller renders as "unknown", never
    as a confident $0.
    """
    from canon.packs import resolve_pack

    entities = resolve_pack(Path(pack_dir)).spec.entities
    entity = entities.get(entity_type)
    if entity is None:
        raise ValueError(f"unknown db type {entity_type!r} (one of {sorted(entities)})")
    return str(entity.phase_label or entity_type), str(entity.kind or entity_type)


def strip_additive(result: dict) -> dict:
    """The original shape of an estimate: every additive key removed
    from the top level and from each asset block (``vlm.model`` kept — it
    predates this row). The identity fixtures compare against this.

    ``calibration`` is the one key whose additiveness depends on the shape: on
    the RUN hook's ``{mode, calibration, …}`` it predates the split, so a
    result carrying ``mode`` keeps it.
    """
    top_strip = set(TOP_ADDITIVE_KEYS)
    if _RUN_SHAPE_MARKER in result:
        top_strip.discard("calibration")
    out = {k: v for k, v in result.items() if k not in top_strip}
    assets = out.get("assets")
    if isinstance(assets, dict):
        stripped: dict[str, Any] = {}
        for key, block in assets.items():
            if key == "vlm" and isinstance(block, dict):
                stripped[key] = {k: v for k, v in block.items() if k not in VLM_ADDITIVE_KEYS}
            elif isinstance(block, dict) and key in ("images", "music", "sfx"):
                stripped[key] = {k: v for k, v in block.items() if k not in ADDITIVE_KEYS}
            else:
                stripped[key] = block
        out["assets"] = stripped
    return out

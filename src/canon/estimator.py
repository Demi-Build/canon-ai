"""``canon.estimator`` — the cost-forecast ENGINE every pack prices through
(master §3.1 row P0-7; Phase 0 W2.1.2 "split core/pack").

Extracted from ``canon.packs.platformer.estimate`` (the ~70% that was never
platformer-specific): the cost-model JSON schema + loader, the price lookup
(now :mod:`canon.pricing` — the only price source, §3.0-C), the paid /
backend-mask logic, the retry multipliers, the summation and the
per-generator breakdown shape cradle's ``CostEstimate`` reads (keys
unchanged), plus the additive §3.0-E keys ``low / high / backend / model /
unitCount`` (and ``accuracy``) on the top-level result and on every asset
block.

A pack contributes ONE pair — ``PackSpec.estimator = Estimator(count_fn,
cost_model_path, …)`` — where ``count_fn(params, bible | None) -> counts``
answers "which nodes fire how many times" for a scope::

    {
      "llm":    {"<task>": calls, ...},          # LLM calls per cost-model task
      "images": n, "music": n, "sfx": n,        # flat per-unit assets
      "vlm":    {"<family>": {"best": b, "worst": w, "tokens": "<cost-model key>"}
                 | {"count": n, "tokens": "<cost-model key>"}, ...},
    }

Everything numeric the pack owns is DATA in its ``cost_model.json``
(tokens per task, retry multiplier, counts-per-unit knobs, the fresh plan);
every DOLLAR comes from :mod:`canon.pricing` by the selected backend's
model — a cost model carries no price. The engine only counts and
multiplies.

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

#: The additive §3.0-E keys the engine appends to the top-level result and
#: to every asset block (the pre-P0-7 keys are untouched — cradle's
#: ``CostEstimate`` contract holds; ``vlm.model`` predates this row and is NOT
#: in the vlm block's additive set).
ADDITIVE_KEYS: tuple[str, ...] = ("low", "high", "backend", "model", "unitCount", "accuracy")
VLM_ADDITIVE_KEYS: tuple[str, ...] = ("low", "high", "backend", "unitCount", "accuracy")
TOP_ADDITIVE_KEYS: tuple[str, ...] = ADDITIVE_KEYS + ("template",)

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


def _unit_row(kind: str, backend: str | None, warnings: list[str]) -> tuple[str | None, dict[str, Any]]:
    """``(model, row)`` a flat-per-unit category prices by: the backend's
    default model's row when the backend bills, else the $0 row (an unpaid
    backend — fake / none / local — is a real $0, not an unpriced one)."""
    model = pricing.default_model(kind, backend)
    if model is None:
        return None, pricing.zero_row(kind)
    row = pricing.price_for(kind, model, warnings)
    return model, (row if row is not None else pricing.zero_row(kind))


def actuals_by_task(output_dir: str | Path) -> dict[str, dict]:
    """Per-task-prefix token averages from a real tree's
    ``generation_stats.json`` — measured beats guessed. Zero-token entries
    (fake runs) never calibrate."""
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


def _flat_block(kind: str, count: int, backend: str | None, warnings: list[str]) -> dict:
    model, row = _unit_row(kind, backend, warnings)
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
    """The ``vlm`` block in the pre-P0-7 shape — ``{model, <family>…, usd}``
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
) -> dict:
    """The ``assets`` block: images / music / sfx (flat per unit, priced by
    the selected backend's row) + the VLM families + the roll-up
    ``usd.best`` (= Σ unit price + vlm best) / ``usd.worst`` (= Σ the rows'
    published high + vlm worst)."""
    images = _flat_block("image", int(counts.get("images", 0)), backends.get("image"), warnings)
    music = _flat_block("music", int(counts.get("music", 0)), backends.get("music"), warnings)
    sfx = _flat_block("sfx", int(counts.get("sfx", 0)), backends.get("sfx"), warnings)
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
) -> dict:
    """Price one forecast: ``count_fn`` → :func:`price_llm` +
    :func:`price_assets` → the backend mask → the roll-up.

    ``backends`` = the selected backend per kind (cradle's selectors);
    ``None`` prices at real-API rates (:data:`DEFAULT_PAID_BACKENDS`, the
    ``canon estimate`` hook's convention) with no mask. ``primary_kind``
    names the category whose backend/model the top-level §3.0-E keys report
    (``llm`` for world / per-level ops, ``image`` for an animation run,
    ``music`` for a track). ``actuals_dir`` is a real tree whose
    ``generation_stats.json`` calibrates the token counts.

    Returns ``{llm, assets, total_usd, warnings, low, high, backend, model,
    unitCount, accuracy[, template]}`` — the caller prepends its own leading
    keys (``scope`` + ``backends`` for cradle, ``mode`` + ``calibration`` for
    the run hook) so the pre-P0-7 key order is preserved.
    """
    cost_model = est.cost_model()
    resolve = est.resolver()
    warnings: list[str] = []
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
    actuals = actuals_by_task(actuals_dir) if actuals_dir else {}
    llm = price_llm(dict(counts.get("llm") or {}), cost_model, resolve, actuals, warnings)
    assets = price_assets(counts, cost_model, chosen, vlm_model, warnings)
    if masked:
        apply_backend_mask(llm, assets, chosen)

    total = {
        "best": round(llm["usd"]["best"] + assets["usd"]["best"], 4),
        "worst": round(llm["usd"]["worst"] + assets["usd"]["worst"], 4),
    }
    if primary_kind == "llm":
        model = resolve("_default") or est.default_model or ""
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
    }
    if template is not None:
        out["template"] = template
    return out


def strip_additive(result: dict) -> dict:
    """The pre-P0-7 shape of an estimate: every additive §3.0-E key removed
    from the top level and from each asset block (``vlm.model`` kept — it
    predates this row). The identity fixtures compare against this."""
    out = {k: v for k, v in result.items() if k not in TOP_ADDITIVE_KEYS}
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

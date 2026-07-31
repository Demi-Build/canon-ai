"""Per-project spend ledger — an append-only record of what each paid op
cradle fired actually cost.

Lives beside the provenance journal at ``<pack>/.canon/spend.jsonl`` but is a
separate concern: the journal records artifact MUTATIONS (training signal); the
spend ledger records the DOLLARS a project has consumed, one line per paid op
cradle triggered. Cradle shells out to ``canon spend record`` after each op (it
never writes pack files directly — locked doctrine) and reads the running total
via ``canon spend list`` for its cost dashboard.

Entry shape (``schema="cradle-spend/v1"``)::

    {schema, ts, op, scope, level_id?, backends, estimate?:{best,worst},
     actual_usd?, tokens?:{input,output,calls}}

``actual_usd`` is the MEASURED cost from the op result (real returned tokens ×
price, or provider-reported asset cost); ``estimate`` is the pre-run forecast
shown at the confirm. A full New-Project run records its
``generation_stats.total_cost_usd`` as ``actual_usd``. Fake/`$0` ops record 0 —
they still belong in the ledger so the dashboard shows the whole history.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = "cradle-spend/v1"
LEDGER_NAME = "spend.jsonl"


def _ledger_path(pack_dir: str | Path) -> Path:
    return Path(pack_dir) / ".canon" / LEDGER_NAME


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def record_spend(pack_dir: str | Path, entry: dict, *, ts: str | None = None) -> dict:
    """Append one spend entry to the pack's ledger. Stamps ``schema`` + ``ts``
    (UTC ISO 8601) when absent; the entry is otherwise passed through verbatim
    so cradle owns its shape. Returns the stored line."""
    path = _ledger_path(pack_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = {"schema": SCHEMA, "ts": ts or entry.get("ts") or _now_iso(), **entry}
    line["schema"] = SCHEMA  # authoritative — never let the caller override it
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(line, default=str) + "\n")
    return line


def read_spend(pack_dir: str | Path) -> list[dict]:
    """All ledger entries in write order (empty list if none). Malformed lines
    are skipped, not fatal — the ledger is observability, not run state."""
    path = _ledger_path(pack_dir)
    if not path.is_file():
        return []
    out: list[dict] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):  # a valid-JSON scalar/array is not an entry
            out.append(obj)
    return out


def _num(entry: dict, *keys: str) -> float:
    """Dig a float out of a nested entry by key path; 0.0 if missing."""
    cur: Any = entry
    for k in keys:
        if not isinstance(cur, dict):
            return 0.0
        cur = cur.get(k)
    try:
        return float(cur)
    except (TypeError, ValueError):
        return 0.0


def summarize(pack_dir: str | Path) -> dict:
    """Roll the ledger up for the dashboard: ``total_actual_usd`` (measured
    spend across all ops) and ``total_estimate_usd`` (the sum of every op's
    pre-run forecast) as an independent estimated-vs-actual pair, plus a per-op
    breakdown carrying both figures so the dashboard can show forecast beside
    measured. ``actual`` is an entry's measured ``actual_usd`` (0 when absent,
    e.g. a still-running or fake op); ``estimate`` is its forecast ``estimate.best``.
    The two totals are separate sums — an op contributes to actual AND estimate,
    which is comparison, not double-counting."""
    entries = read_spend(pack_dir)
    by_op: dict[str, dict] = {}
    total_actual = 0.0
    total_estimate = 0.0
    for e in entries:
        op = str(e.get("op") or e.get("scope") or "unknown")
        has_actual = "actual_usd" in e and e.get("actual_usd") is not None
        actual = _num(e, "actual_usd") if has_actual else 0.0
        est = _num(e, "estimate", "best")
        agg = by_op.setdefault(
            op, {"count": 0, "actual_usd": 0.0, "estimate_usd": 0.0}
        )
        agg["count"] += 1
        agg["actual_usd"] += actual
        agg["estimate_usd"] += est
        total_actual += actual
        total_estimate += est
    return {
        "count": len(entries),
        "total_actual_usd": round(total_actual, 6),
        "total_estimate_usd": round(total_estimate, 6),
        "by_op": {
            op: {
                "count": a["count"],
                "actual_usd": round(a["actual_usd"], 6),
                "estimate_usd": round(a["estimate_usd"], 6),
            }
            for op, a in sorted(by_op.items())
        },
        "entries": entries,
    }

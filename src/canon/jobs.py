"""Per-project job ledger — an append-only record of every generation job
cradle ran in the background.

Lives beside the provenance journal and the spend ledger at
``<pack>/.canon/jobs.jsonl`` but is its own concern: the journal records
artifact MUTATIONS (training signal), the spend ledger records DOLLARS, and the
job ledger records the RUN of each queued generation — what op, how long it
took, whether it succeeded, and (the payoff) whether it actually CHANGED
anything. Cradle's background worker fires jobs off the UI thread and shells out
to ``canon jobs record`` on completion (it never writes pack files directly —
locked doctrine); the job tray reads history via ``canon jobs list``.

The ``changed`` signal answers the question a blocked-UI paid run left open:
"did it run at all, did it run but produce the same map, or did it change
something?" It comes from the op's own change signal (a diff of the provenance
journal around the op — a fresh journal event means real new bytes, because
``baseline_level`` is idempotent and no-op writes are suppressed).

Entry shape (``schema="cradle-jobs/v1"``)::

    {schema, ts, job_id, op, scope?, target?,
     status: "ok" | "no_change" | "failed" | "cancelled",
     backends?, estimate?:{best,worst}, actual_usd?, duration_ms?,
     changed: bool, changed_artifacts?:[..], error?,
     identity?, session?, batchId?}

The entry is otherwise passed through verbatim so cradle owns its shape (mirrors
:mod:`canon.spend`). ``status`` is a success signal: ``ok`` = ran and
changed something, ``no_change`` = ran cleanly but produced identical bytes,
``failed`` = the op errored, ``cancelled`` = ⏹ Stop ended it (row P1-A4.5's
contract; the VALUE joined at A6 — not a new schema, and :data:`STATUSES` is a
data tuple for labels, never a type: a later status renders without an edit).

Row P1-A6 (ASSUMPTION-8, P0 paper P.8.7) — **this file is RUN STATUS ONLY**.
Its ``actual_usd`` is informational and is NEVER summed by the cost dashboard;
money reconciles off the journal's ``costCents`` alone (one number, one
source). The additive lane fields ``identity`` (``canon.provenance.identity_for``
of the launching actor), ``session`` (the conversation id) and ``batchId`` let
the tray attribute a run without a second cost path. :func:`summarize` is
unchanged.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

SCHEMA = "cradle-jobs/v1"
LEDGER_NAME = "jobs.jsonl"

#: Statuses the tray knows how to label, in reading order — DATA, never a type
#: (P.8.8). ``cancelled`` joined at row P1-A6; an unknown status still renders.
STATUSES: tuple[str, ...] = ("ok", "no_change", "failed", "cancelled")


def _ledger_path(pack_dir: str | Path) -> Path:
    return Path(pack_dir) / ".canon" / LEDGER_NAME


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def record_job(pack_dir: str | Path, entry: dict, *, ts: str | None = None) -> dict:
    """Append one job entry to the pack's ledger. Stamps ``schema`` + ``ts``
    (UTC ISO 8601) when absent; the entry is otherwise passed through verbatim
    so cradle owns its shape. Returns the stored line."""
    path = _ledger_path(pack_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = {"schema": SCHEMA, "ts": ts or entry.get("ts") or _now_iso(), **entry}
    line["schema"] = SCHEMA  # authoritative — never let the caller override it
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(line, default=str) + "\n")
    return line


def read_jobs(pack_dir: str | Path) -> list[dict]:
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


def summarize(pack_dir: str | Path) -> dict:
    """Roll the ledger up for the job tray: total count plus per-op and
    per-status breakdowns (``by_op`` counts each op's runs; ``by_status`` counts
    ok / no_change / failed), and the full ``entries`` list (write order) so the
    tray can render the Completed tab. ``changed`` is a bool per entry, so the
    per-status counts already separate "ran and changed" (ok) from "ran, no
    change" (no_change)."""
    entries = read_jobs(pack_dir)
    by_op: dict[str, int] = {}
    by_status: dict[str, int] = {}
    for e in entries:
        op = str(e.get("op") or e.get("scope") or "unknown")
        status = str(e.get("status") or "unknown")
        by_op[op] = by_op.get(op, 0) + 1
        by_status[status] = by_status.get(status, 0) + 1
    return {
        "count": len(entries),
        "by_op": dict(sorted(by_op.items())),
        "by_status": dict(sorted(by_status.items())),
        "entries": entries,
    }

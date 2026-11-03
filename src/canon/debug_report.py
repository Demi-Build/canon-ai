"""Debug reports built from a pack's run log (``canon.runlog``).

``canon debug runs <pack>`` lists the recorded runs; ``canon debug report
<pack>`` turns one of them (the latest by default) into a report a person
can read in a minute or paste into a bug: what ran, what it called, what
failed and why, what it cost, where the time went, and what to try next.

The report reads the run log only, plus two cross-references for context:
the job ledger lines and the ``generation_stats.json`` written in the run's
window. Nothing here writes to the pack.

A run that spawned another canon process (``world new`` runs its template
runner as a child) is reported together with its children: their calls,
failures and logs are folded in and each line keeps the run it came from.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from canon.runlog import read_runlog

__all__ = ["build_report", "list_runs", "render_markdown"]

#: Status words, in the order a reader cares about them.
STATUSES = ("failed", "incomplete", "cancelled", "warnings", "ok")


def _parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _group(records: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    runs: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        runs.setdefault(str(record.get("run")), []).append(record)
    return runs


def _first(events: list[dict[str, Any]], kind: str) -> dict[str, Any] | None:
    return next((e for e in events if e.get("event") == kind), None)


def _last(events: list[dict[str, Any]], kind: str) -> dict[str, Any] | None:
    return next((e for e in reversed(events) if e.get("event") == kind), None)


def _status(events: list[dict[str, Any]]) -> str:
    end = _last(events, "run_end")
    if end is None:
        return "incomplete"
    steps = [e for e in events if e.get("event") == "step" and e.get("step") == "run_end"]
    if any(s.get("cancelled") for s in steps) or end.get("exit_code") == 3:
        return "cancelled"
    if not end.get("ok", True):
        return "failed"
    failed_calls = [e for e in events if e.get("event") == "call" and not e.get("ok") and not e.get("retrying")]
    warned = any(e.get("event") == "log" and e.get("level") in ("WARNING", "ERROR", "CRITICAL") for e in events)
    if failed_calls or warned or any(e.get("event") == "asset_failed" for e in events):
        return "warnings"
    return "ok"


def _short_command(command: Any) -> str:
    if not isinstance(command, list) or not command:
        return ""
    parts = [str(c) for c in command]
    # Drop the interpreter / script path: "canon", "python -m …" read the same.
    head = Path(parts[0]).name
    rest = parts[1:]
    if head.startswith("python") and rest[:1] == ["-m"]:
        head, rest = rest[1] if len(rest) > 1 else head, rest[2:]
    text = " ".join([head, *rest])
    return text if len(text) <= 160 else text[:157] + "…"


def _run_row(run_id: str, events: list[dict[str, Any]], children: list[str]) -> dict[str, Any]:
    start = _first(events, "run_start") or {}
    end = _last(events, "run_end") or {}
    calls = [e for e in events if e.get("event") == "call"]
    cost = sum(float(e.get("cost_usd") or 0.0) for e in calls)
    cost += sum(float(e.get("usd") or 0.0) for e in events if e.get("event") == "cost")
    return {
        "run": run_id,
        "started": start.get("ts") or (events[0].get("ts") if events else None),
        "ended": end.get("ts"),
        "command": _short_command(start.get("command")),
        "status": _status(events),
        "exit_code": end.get("exit_code"),
        "duration_ms": end.get("duration_ms"),
        "calls": len(calls),
        "failed_calls": sum(1 for e in calls if not e.get("ok")),
        "cost_usd": round(cost, 6),
        "parent": start.get("parent"),
        "children": children,
    }


def _children_of(runs: dict[str, list[dict[str, Any]]]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for run_id, events in runs.items():
        start = _first(events, "run_start") or {}
        parent = start.get("parent")
        if parent and parent in runs:
            out.setdefault(str(parent), []).append(run_id)
    return out


def list_runs(pack_dir: str | Path, *, limit: int = 20) -> list[dict[str, Any]]:
    """Recorded runs, newest first. A child run is listed under its parent
    (``children``) and not on its own."""
    runs = _group(read_runlog(pack_dir))
    children = _children_of(runs)
    nested = {c for kids in children.values() for c in kids}
    rows = []
    for run_id, events in runs.items():
        if run_id in nested:
            continue
        family = list(events)
        for child in children.get(run_id, []):
            family.extend(runs[child])
        family.sort(key=lambda e: str(e.get("ts") or ""))
        row = _run_row(run_id, family, children.get(run_id, []))
        # The parent's own status wins unless a child did worse.
        own = _status(events)
        row["status"] = min((own, row["status"]), key=STATUSES.index)
        rows.append(row)
    rows.sort(key=lambda r: str(r.get("started") or ""), reverse=True)
    return rows[:limit] if limit else rows


def _percent(n: int, d: int) -> str:
    return f"{(100 * n / d):.0f}%" if d else "—"


def _related(pack_dir: Path, started: datetime | None, ended: datetime | None) -> dict[str, Any]:
    """Job-ledger lines and the stats file written within the run's window."""
    out: dict[str, Any] = {}
    jobs_path = pack_dir / ".canon" / "jobs.jsonl"
    if jobs_path.is_file() and started is not None:
        hits = []
        for raw in jobs_path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                job = json.loads(raw)
            except json.JSONDecodeError:
                continue
            ts = _parse_ts(job.get("ts")) if isinstance(job, dict) else None
            if ts is None or ts < started:
                continue
            if ended is not None and (ts - ended).total_seconds() > 120:
                continue
            hits.append({k: job.get(k) for k in ("ts", "job_id", "op", "status", "error", "actual_usd") if k in job})
        if hits:
            out["jobs"] = hits[:10]
    stats_path = pack_dir / "generation_stats.json"
    if stats_path.is_file():
        try:
            stats = json.loads(stats_path.read_text(encoding="utf-8"))
            mtime = datetime.fromtimestamp(stats_path.stat().st_mtime).astimezone()
        except (OSError, json.JSONDecodeError):
            stats = None
            mtime = None
        if isinstance(stats, dict) and started is not None and mtime is not None and mtime >= started:
            out["generation_stats"] = {
                "total_cost_usd": stats.get("total_cost_usd"),
                "failures": len(stats.get("failures") or []),
                "backends": {
                    k: stats.get(f"{k}_backend") for k in ("llm", "vlm", "image", "music", "sfx")
                    if stats.get(f"{k}_backend")
                },
            }
    return out


def _suggestions(report: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for group in report["failures"]:
        status = group.get("status")
        who = group.get("provider") or "a provider"
        if status in (401, 403) or "PERMISSION" in str(group.get("message", "")).upper():
            out.append(
                f"{who} refused the credential or billing account ({group['count']}×): check the key "
                "with `canon providers test`, and the provider's billing page."
            )
        elif status == 429:
            out.append(f"{who} rate-limited {group['count']} call(s): lower the concurrency or retry later.")
        elif status in (400, 404, 422):
            out.append(f"{who} rejected {group['count']} request(s) as invalid: {group.get('hint') or 'fix the entry'}")
    shadowed = (report.get("env") or {}).get("shadowed_by_shell")
    if shadowed:
        out.append(
            "These keys in the env file were ignored because the shell already exports a different "
            f"value: {', '.join(shadowed)}. Unset them in the shell or update the shell's copy."
        )
    if report["run"]["status"] == "incomplete":
        out.append(
            "The run has no end record: the process was killed or crashed hard. The last events "
            "show what it was doing."
        )
    missing = [k["id"] for k in (report.get("env") or {}).get("keys", []) if not k.get("set")]
    if report["failures"] and missing:
        out.append(f"Provider keys not set for this run: {', '.join(missing)}.")
    # Keep order, drop repeats.
    return list(dict.fromkeys(out))


def build_report(pack_dir: str | Path, run_id: str | None = None) -> dict[str, Any]:
    """The report for ``run_id`` (or the latest top-level run). Raises
    ``LookupError`` when the pack has no run log or no such run."""
    pack = Path(pack_dir)
    records = read_runlog(pack)
    if not records:
        raise LookupError(f"no run log in {pack} (.canon/runlog.jsonl is missing or empty)")
    runs = _group(records)
    children = _children_of(runs)
    if run_id is None:
        listed = list_runs(pack, limit=1)
        run_id = listed[0]["run"]
    elif run_id not in runs:
        matches = [r for r in runs if r.startswith(run_id) or r.endswith(run_id)]
        if len(matches) != 1:
            raise LookupError(f"no run {run_id!r} in {pack} (see `canon debug runs`)")
        run_id = matches[0]

    own = runs[run_id]
    kids = children.get(run_id, [])
    events = list(own)
    for child in kids:
        events.extend(runs[child])
    events.sort(key=lambda e: str(e.get("ts") or ""))

    row = _run_row(run_id, events, kids)
    row["status"] = min((_status(own), row["status"]), key=STATUSES.index)
    start = _first(own, "run_start") or {}
    end = _last(own, "run_end") or {}
    row.update(
        canon_version=start.get("canon_version"),
        python=start.get("python"),
        platform=start.get("platform"),
        cwd=start.get("cwd"),
        full_command=start.get("command"),
    )

    calls = [e for e in events if e.get("event") == "call"]
    costs = [e for e in events if e.get("event") == "cost"]

    providers: dict[str, dict[str, Any]] = {}
    for c in calls:
        p = providers.setdefault(
            c.get("provider") or "unknown",
            {"provider": c.get("provider") or "unknown", "lanes": set(), "calls": 0, "ok": 0, "failed": 0,
             "total_ms": 0.0, "max_ms": 0.0, "cost_usd": 0.0, "input_tokens": 0, "output_tokens": 0},
        )
        p["lanes"].add(c.get("lane") or "?")
        p["calls"] += 1
        p["ok" if c.get("ok") else "failed"] += 1
        ms = float(c.get("duration_ms") or 0.0)
        p["total_ms"] += ms
        p["max_ms"] = max(p["max_ms"], ms)
        p["cost_usd"] += float(c.get("cost_usd") or 0.0)
        p["input_tokens"] += int(c.get("input_tokens") or 0)
        p["output_tokens"] += int(c.get("output_tokens") or 0)
    for c in costs:
        p = providers.setdefault(
            c.get("provider") or "unknown",
            {"provider": c.get("provider") or "unknown", "lanes": set(), "calls": 0, "ok": 0, "failed": 0,
             "total_ms": 0.0, "max_ms": 0.0, "cost_usd": 0.0, "input_tokens": 0, "output_tokens": 0},
        )
        p["lanes"].add(c.get("lane") or "?")
        p["cost_usd"] += float(c.get("usd") or 0.0)
    provider_rows = []
    for p in sorted(providers.values(), key=lambda x: (-x["calls"], x["provider"])):
        provider_rows.append({
            **p,
            "lanes": sorted(p["lanes"]),
            "total_ms": round(p["total_ms"], 1),
            "avg_ms": round(p["total_ms"] / p["calls"], 1) if p["calls"] else None,
            "max_ms": round(p["max_ms"], 1),
            "cost_usd": round(p["cost_usd"], 6),
        })

    # Final failures: a failed attempt the loop did NOT retry.
    groups: dict[tuple, dict[str, Any]] = {}
    for c in calls:
        if c.get("ok") or c.get("retrying"):
            continue
        err = c.get("error") if isinstance(c.get("error"), dict) else {}
        key = (c.get("provider"), err.get("error"), err.get("status"))
        g = groups.setdefault(key, {
            "provider": c.get("provider"), "lane": c.get("lane"), "error": err.get("error"),
            "status": err.get("status"), "retryable": err.get("retryable"), "count": 0,
            "message": err.get("message"), "hint": err.get("hint"), "request_ids": [], "labels": [],
            "first": c.get("ts"), "last": c.get("ts"),
        })
        g["count"] += 1
        g["last"] = c.get("ts")
        if c.get("label") and len(g["labels"]) < 8:
            g["labels"].append(c.get("label"))
        if err.get("request_id") and len(g["request_ids"]) < 3:
            g["request_ids"].append(err.get("request_id"))
    failures = sorted(groups.values(), key=lambda g: -g["count"])

    retried = sum(1 for c in calls if c.get("retrying"))
    asset_failed = []
    for e in events:
        if e.get("event") == "asset_failed" or (e.get("event") == "step" and e.get("step") == "asset_failed"):
            asset_failed.append({k: e.get(k) for k in (
                "ts", "kind", "target", "path", "provider", "error", "status", "message", "attempts", "hint",
            ) if e.get(k) is not None})

    slowest = sorted(calls, key=lambda c: -float(c.get("duration_ms") or 0.0))[:5]
    slow_keys = ("ts", "lane", "provider", "label", "model", "duration_ms", "ok")
    slowest = [{k: c.get(k) for k in slow_keys} for c in slowest]

    phases: list[dict[str, Any]] = []
    open_nodes: dict[str, dict[str, Any]] = {}
    for e in events:
        if e.get("event") != "step":
            continue
        kind, node = e.get("step"), e.get("node")
        if kind == "node_start" and node:
            entry = {"node": node, "started": e.get("ts"), "status": "running"}
            open_nodes[node] = entry
            phases.append(entry)
        elif kind in ("node_done", "node_failed") and node:
            entry = open_nodes.pop(node, None)
            if entry is None:
                entry = {"node": node, "started": None}
                phases.append(entry)
            entry["ended"] = e.get("ts")
            entry["status"] = "done" if kind == "node_done" else "failed"
            if kind == "node_failed" and e.get("error"):
                entry["error"] = e.get("error")
            a, b = _parse_ts(entry.get("started")), _parse_ts(entry.get("ended"))
            if a and b:
                entry["duration_ms"] = round((b - a).total_seconds() * 1000, 1)

    logs = [e for e in events if e.get("event") == "log" and e.get("level") in ("WARNING", "ERROR", "CRITICAL")]
    counted: Counter[tuple[str, str]] = Counter((e.get("level", ""), e.get("message", "")) for e in logs)
    problems = []
    seen: set[tuple[str, str]] = set()
    for e in logs:
        key = (e.get("level", ""), e.get("message", ""))
        if key in seen:
            continue
        seen.add(key)
        problems.append({
            "level": e.get("level"), "logger": e.get("logger"), "where": e.get("where"),
            "message": e.get("message"), "count": counted[key], "first": e.get("ts"),
            "traceback": e.get("traceback"),
        })
    problems = problems[:40]

    env_events = [e for e in events if e.get("event") == "env"]
    shadow = sorted({n for e in events if e.get("event") == "env_shadowed" for n in (e.get("names") or [])})
    env: dict[str, Any] = {}
    if env_events:
        env["keys"] = env_events[0].get("keys") or []
        env["env_file"] = env_events[0].get("env_file")
    if shadow:
        env["shadowed_by_shell"] = shadow

    exception = None
    for e in [end] + [_last(runs[k], "run_end") or {} for k in kids]:
        if e.get("traceback") or e.get("error"):
            exception = {"run": e.get("run"), "error": e.get("error"), "traceback": e.get("traceback")}
            break

    cost_calls = sum(float(c.get("cost_usd") or 0.0) for c in calls)
    cost_assets = sum(float(c.get("usd") or 0.0) for c in costs)
    report: dict[str, Any] = {
        "pack": str(pack),
        "run": row,
        "totals": {
            "calls": len(calls),
            "ok_calls": sum(1 for c in calls if c.get("ok")),
            "failed_attempts": sum(1 for c in calls if not c.get("ok")),
            "retried_attempts": retried,
            "final_failures": sum(g["count"] for g in failures),
            "assets_failed": len(asset_failed),
            "cost_usd": round(cost_calls + cost_assets, 6),
            "cost_tokens_usd": round(cost_calls, 6),
            "cost_assets_usd": round(cost_assets, 6),
            "input_tokens": sum(int(c.get("input_tokens") or 0) for c in calls),
            "output_tokens": sum(int(c.get("output_tokens") or 0) for c in calls),
            "warnings": sum(1 for e in logs if e.get("level") == "WARNING"),
            "errors": sum(1 for e in logs if e.get("level") in ("ERROR", "CRITICAL")),
        },
        "providers": provider_rows,
        "failures": failures,
        "asset_failures": asset_failed[:50],
        "slowest_calls": slowest,
        "phases": phases,
        "problems": problems,
        "exception": exception,
        "env": env,
        "last_events": [_brief(e) for e in events[-12:]],
        "related": _related(pack, _parse_ts(row.get("started")), _parse_ts(row.get("ended"))),
    }
    report["suggestions"] = _suggestions(report)
    return report


def _brief(e: dict[str, Any]) -> dict[str, Any]:
    keep = ("ts", "event", "step", "node", "item", "lane", "provider", "label", "ok", "level", "message",
            "duration_ms", "exit_code")
    out = {k: e.get(k) for k in keep if e.get(k) is not None}
    if isinstance(e.get("error"), dict):
        out["error"] = e["error"].get("message") or e["error"].get("error")
    elif e.get("error"):
        out["error"] = e.get("error")
    return out


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------


def _ms(value: Any) -> str:
    if value is None:
        return "—"
    ms = float(value)
    if ms < 1000:
        return f"{ms:.0f} ms"
    secs = ms / 1000
    if secs < 120:
        return f"{secs:.1f} s"
    return f"{int(secs // 60)}m {int(secs % 60):02d}s"


def _usd(value: Any) -> str:
    return f"${float(value or 0.0):.4f}"


def _cell(value: Any) -> str:
    return str(value if value is not None else "—").replace("|", "\\|").replace("\n", " ")


def render_markdown(report: dict[str, Any]) -> str:
    """The report as Markdown, ready to read or paste into an issue."""
    run, totals = report["run"], report["totals"]
    lines: list[str] = []
    lines.append(f"# Canon run report — {run['status'].upper()}")
    lines.append("")
    lines.append(f"- **Run:** `{run['run']}`" + (f" (+{len(run['children'])} child run)" if run["children"] else ""))
    lines.append(f"- **Command:** `{run['command']}`")
    lines.append(f"- **Pack:** `{report['pack']}`")
    lines.append(
        f"- **Started:** {run.get('started') or '—'} · **Duration:** {_ms(run.get('duration_ms'))}"
        f" · **Exit code:** {run.get('exit_code') if run.get('exit_code') is not None else '—'}"
    )
    lines.append(
        f"- **Calls:** {totals['calls']} ({totals['ok_calls']} ok, {totals['failed_attempts']} failed attempts, "
        f"{totals['retried_attempts']} retried) · **Final failures:** {totals['final_failures']}"
        f" · **Assets lost:** {totals['assets_failed']}"
    )
    lines.append(
        f"- **Cost:** {_usd(totals['cost_usd'])} (tokens {_usd(totals['cost_tokens_usd'])}, "
        f"assets {_usd(totals['cost_assets_usd'])}) · **Tokens:** {totals['input_tokens']} in / "
        f"{totals['output_tokens']} out"
    )
    lines.append(
        f"- **canon** {run.get('canon_version') or '?'} · Python {run.get('python') or '?'}"
        f" · {run.get('platform') or '?'}"
    )

    if report["suggestions"]:
        lines += ["", "## What to try", ""]
        lines += [f"- {s}" for s in report["suggestions"]]

    if report["exception"]:
        exc = report["exception"]
        lines += ["", "## Crash", "", f"`{exc.get('error')}`"]
        if exc.get("traceback"):
            lines += ["", "```", exc["traceback"].rstrip(), "```"]

    if report["failures"]:
        lines += ["", "## Failures", "",
                  "| Provider | Error | Status | Count | Message | Hint |", "|---|---|---|---|---|---|"]
        for g in report["failures"]:
            lines.append(
                f"| {_cell(g['provider'])} | {_cell(g['error'])} | {_cell(g['status'])} | {g['count']} "
                f"| {_cell((g.get('message') or '')[:200])} | {_cell(g.get('hint'))} |"
            )
        for g in report["failures"]:
            if g["labels"]:
                lines.append(f"- {g['provider']} · {g['error']}: {', '.join(map(str, g['labels']))}")

    if report["providers"]:
        lines += ["", "## Providers", "",
                  "| Provider | Lanes | Calls | OK | Failed | Avg | Max | Cost |", "|---|---|---|---|---|---|---|---|"]
        for p in report["providers"]:
            lines.append(
                f"| {_cell(p['provider'])} | {', '.join(p['lanes'])} | {p['calls']} | {p['ok']} | {p['failed']} "
                f"| {_ms(p['avg_ms'])} | {_ms(p['max_ms'])} | {_usd(p['cost_usd'])} |"
            )

    if report["asset_failures"]:
        lines += ["", "## Assets that did not land", ""]
        for a in report["asset_failures"][:25]:
            lines.append(
                f"- `{a.get('target') or a.get('path')}` ({a.get('kind', '?')}) — {a.get('provider', '?')} "
                f"{a.get('error', '')} {a.get('status') or ''}: {(a.get('message') or '')[:160]}"
            )

    if report["problems"]:
        lines += ["", "## Warnings and errors", ""]
        for p in report["problems"][:25]:
            times = f" ×{p['count']}" if p["count"] > 1 else ""
            lines.append(f"- **{p['level']}**{times} `{p.get('where') or p.get('logger')}` — {p['message'][:300]}")

    if report["phases"]:
        lines += ["", "## Phases", "", "| Phase | Status | Duration |", "|---|---|---|"]
        for ph in report["phases"]:
            lines.append(f"| {_cell(ph['node'])} | {ph.get('status')} | {_ms(ph.get('duration_ms'))} |")

    if report["slowest_calls"]:
        lines += ["", "## Slowest calls", ""]
        for c in report["slowest_calls"]:
            lines.append(
                f"- {_ms(c.get('duration_ms'))} — {c.get('provider')} {c.get('lane')} `{c.get('label') or ''}`"
                + ("" if c.get("ok") else " (failed)")
            )

    env = report.get("env") or {}
    if env:
        lines += ["", "## Keys at first call", ""]
        keys = env.get("keys") or []
        if keys:
            lines.append(", ".join(f"{k['id']} {'✓' if k.get('set') else '✗'}" for k in keys))
        if env.get("shadowed_by_shell"):
            lines.append(f"Shadowed by the shell: {', '.join(env['shadowed_by_shell'])}")

    related = report.get("related") or {}
    if related.get("generation_stats"):
        gs = related["generation_stats"]
        lines += ["", "## generation_stats.json", "",
                  f"total_cost_usd {gs.get('total_cost_usd')} · failures {gs.get('failures')} · "
                  + ", ".join(f"{k}={v}" for k, v in (gs.get("backends") or {}).items())]
    if related.get("jobs"):
        lines += ["", "## Job ledger", ""]
        for j in related["jobs"]:
            tail = f": {j['error']}" if j.get("error") else ""
            lines.append(f"- {j.get('ts')} {j.get('op')} → {j.get('status')}{tail}")

    if report["last_events"] and run["status"] in ("incomplete", "failed", "cancelled"):
        lines += ["", "## Last events", ""]
        for e in report["last_events"]:
            what = e.get("step") or e.get("event")
            detail = " ".join(str(e[k]) for k in ("node", "item", "provider", "label", "message", "error") if e.get(k))
            lines.append(f"- {e.get('ts')} {what} {detail}".rstrip())

    return "\n".join(lines) + "\n"

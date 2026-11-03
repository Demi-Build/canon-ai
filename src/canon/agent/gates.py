"""The engine-side gate ladder — what makes a code edit "done" (row P1-A7.5).

Phase 1 §7.1 and Trace A2: a code write is not finished when the diff lands,
it is finished when the engine copy still boots, still runs, and the levels it
touches still validate. Master §3.0-I makes the ladder per engine copy — it
runs against the evolved copy's engine, Godot today; W2.0's promoted pygame
copy gets the same shape, and W2.1 adds ``game_coder``'s tuning smoke.

Four rungs, each reporting pass/fail WITH ITS EVIDENCE:

``syntax``    the cheap gate. ``godot --headless --check-only --script <file>``
              when Godot is present AND the file is one it parses (GDScript —
              a ``.tscn`` gets "Can't load script" and exit 0, which is not a
              verdict), plus a structural read of the file (decodable,
              balanced delimiters) that needs no engine at all. The rung's
              ``authoritative`` flag is the difference, and the summary line
              prints it.
``boot``      ``godot --headless --path <PACK ROOT> --quit-after N``. The
              verdict is ``grep -c 'SCRIPT ERROR'`` over the output, NOT the
              exit code: Godot's exit code lies, and a wrong ``--path`` boots
              the editor and exits 0. ``--path`` is the tree root that holds
              ``project.godot`` — never ``<tree>/godot``.
``smoke``     a scripted run through the Godot ``PLAT_*`` mirror
              (``PLAT_LEVEL`` + ``PLAT_HOLD`` / ``PLAT_ACTIONS`` + ``PLAT_TRAJ``,
              ``--fixed-fps 60`` so the trajectory is comparable). The verdict
              is that the run PRODUCED ITS TRAJECTORY — exit 0 proves nothing.
``validate``  ``validate_level`` on the affected levels (pure canon Python;
              no engine, so it runs everywhere).

Ladder order is cheapest-first (``syntax`` before ``boot``): §7.1 enumerates
the boot rung first, but a file that does not parse makes the boot rung's
output unreadable, and spawning the engine to learn what ``--check-only``
answers in milliseconds is waste. Same four rungs, same evidence. A rung that
FAILS stops the ladder — the rungs after it report ``skipped`` naming where it
stopped, because a smoke run past a broken boot measures nothing.

GODOT MAY BE ABSENT on a machine that still authors packs. Detection is
``GODOT_BIN`` → PATH → the macOS app bundle (the same order cradle's
``play_game`` uses). With none found the engine rungs are SKIPPED and say so
by name — "godot not found — boot/smoke unproven" — the ladder's status is
``unproven``, never ``ok``, and the syntax and validate rungs still run. A
false green here would be worse than no ladder at all.

What this extends (doctrine 2): ``canon.agent.tools_vision.scrubbed_env``
(the ``PLAT_*`` scrub every headless spawn already uses) and A7's verify loop,
which CALLS this harness after a code edit — there is no second verify path.
``canon.agent.tools_read.GRID_VALIDATORS`` resolves the validate rung, so a
dungeon pack answers the structured "not yet" its own row owns.

Deliberately absent, by row ownership: the pygame-side ladder and the tuning
smoke (W2.0 / W2.1 — R3(c)), windowed runs (always user-launched), the panel's
rendering of these results (A5).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

#: The rungs, in run order (data — W2.1 appends its tuning smoke here).
GATE_RUNGS: tuple[str, ...] = ("syntax", "boot", "smoke", "validate")

#: Rung/ladder statuses. ``unproven`` is a first-class answer and is NOT a
#: pass: it is what an absent engine earns (doctrine 4, and A7's rule that a
#: verification we cannot run is never reported green).
GATE_STATUSES: tuple[str, ...] = ("ok", "failed", "unproven", "skipped")

#: The string whose COUNT is the boot/smoke verdict. Godot prints it for every
#: runtime script error and still exits 0.
SCRIPT_ERROR = "SCRIPT ERROR"

#: Where Godot is looked for, in order (the resolution cradle's launcher uses).
GODOT_ENV_VAR = "GODOT_BIN"
GODOT_PATH_NAMES: tuple[str, ...] = ("godot", "godot4", "Godot")
GODOT_APP_BUNDLES: tuple[str, ...] = (
    "/Applications/Godot.app/Contents/MacOS/Godot",
    "/Applications/Godot_mono.app/Contents/MacOS/Godot",
)

#: The named skip an absent engine earns — quoted verbatim in the result.
GODOT_MISSING = "godot not found — boot/smoke unproven"

#: Frame budgets. ``--quit-after`` counts FRAMES, not seconds.
BOOT_FRAMES = 120
SMOKE_FRAMES = 240
SMOKE_FPS = 60

#: Wall-clock ceiling for one engine spawn; a hung engine is a named failure.
GATE_TIMEOUT_S = 180.0

#: The scripted input the smoke rung drives when the caller names none — walk
#: right and jump, which exercises movement, collision and the jump arc the
#: A2 trace's edit is about.
DEFAULT_SMOKE_SCRIPT: dict[str, Any] = {"hold": "right", "jump_every": 40}

#: Trajectory lines below which the smoke rung calls the run empty.
MIN_TRAJ_LINES = 5

_OPENERS = {")": "(", "]": "[", "}": "{"}


# ---------------------------------------------------------------------------
# Godot detection
# ---------------------------------------------------------------------------


def godot_probe() -> dict:
    """Where Godot is, or why the engine rungs cannot run.

    ``{found, path, source}`` — ``source`` is ``GODOT_BIN`` | ``PATH`` |
    ``app_bundle``. A ``GODOT_BIN`` that points at nothing is reported as a
    problem rather than silently falling through: a broken override is a
    thing the user set and wants to hear about."""
    problems: list[str] = []
    override = os.environ.get(GODOT_ENV_VAR, "").strip()
    if override:
        if Path(override).is_file() and os.access(override, os.X_OK):
            return {"found": True, "path": override, "source": GODOT_ENV_VAR, "problems": problems}
        problems.append(f"{GODOT_ENV_VAR}={override!r} is not an executable file")
    for name in GODOT_PATH_NAMES:
        found = shutil.which(name)
        if found:
            return {"found": True, "path": found, "source": "PATH", "problems": problems}
    for bundle in GODOT_APP_BUNDLES:
        if Path(bundle).is_file() and os.access(bundle, os.X_OK):
            return {"found": True, "path": bundle, "source": "app_bundle", "problems": problems}
    looked = [GODOT_ENV_VAR, *GODOT_PATH_NAMES, *GODOT_APP_BUNDLES]
    return {
        "found": False,
        "path": None,
        "source": None,
        "problems": problems,
        "reason": f"{GODOT_MISSING}: set {GODOT_ENV_VAR}, or put godot on PATH (looked at {', '.join(looked)})",
    }


def godot_bin() -> str | None:
    """The Godot executable, or ``None``."""
    probe = godot_probe()
    return probe["path"] if probe["found"] else None


# ---------------------------------------------------------------------------
# Levels
# ---------------------------------------------------------------------------


def smoke_levels(pack_dir: str | Path, limit: int = 1) -> list[str]:
    """Level ids the smoke rung can boot when the run touched none — the
    pack's own first ids, read through row A3's ``grid_ids`` (one resolver,
    so a dungeon's ``room`` kind answers here without a second glob)."""
    pack = Path(pack_dir)
    try:
        from canon.agent.tools_read import grid_ids
        from canon.packs import resolve_pack

        spec = resolve_pack(pack).spec
        out: list[str] = []
        for grid in spec.grids.values():
            for entry in grid_ids(pack, str(grid.path_template)):
                if entry:
                    out.append(list(entry.values())[-1])
                if len(out) >= limit:
                    return out
        return out
    except Exception:  # noqa: BLE001 — a ladder never dies on level discovery
        return []


def _validator(pack: Path) -> Callable[[str], dict]:
    from canon.agent.tools_read import GRID_VALIDATORS, grid_verb_or_not_yet

    _, verb = grid_verb_or_not_yet(pack, GRID_VALIDATORS, "validate_level")
    return lambda level_id: verb(pack, level_id)


# ---------------------------------------------------------------------------
# The rungs
# ---------------------------------------------------------------------------


def _rung(name: str, status: str, **fields: Any) -> dict:
    ok = True if status == "ok" else (False if status == "failed" else None)
    return {"rung": name, "status": status, "ok": ok, **fields}


def _tail(text: str | None, limit: int = 800) -> str:
    return (text or "").strip()[-limit:]


def _script_errors(output: str) -> list[str]:
    """Every ``SCRIPT ERROR`` line — the real verdict, and its own evidence."""
    return [line.strip() for line in output.splitlines() if SCRIPT_ERROR in line]


def _run_godot(binary: str, argv: list[str], env_extra: dict[str, str]) -> dict:
    """One engine spawn: ``{cmd, returncode, output, timeout}``. Never raises —
    a timeout or an unlaunchable binary is evidence like any other."""
    from canon.agent.tools_vision import scrubbed_env

    cmd = [binary, *argv]
    try:
        proc = subprocess.run(  # noqa: S603 — our own engine, argv list, no shell
            cmd,
            env=scrubbed_env(env_extra),
            capture_output=True,
            text=True,
            timeout=GATE_TIMEOUT_S,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"cmd": cmd, "returncode": None, "output": "", "timeout": GATE_TIMEOUT_S}
    except OSError as exc:
        return {"cmd": cmd, "returncode": None, "output": f"{type(exc).__name__}: {exc}", "unlaunchable": True}
    return {
        "cmd": cmd,
        "returncode": proc.returncode,
        "output": (proc.stdout or "") + (proc.stderr or ""),
        "timeout": None,
    }


def structural_check(text: str) -> list[str]:
    """Engine-free problems in a source file: unbalanced ``()[]{}`` and a
    trailing line continuation. Deliberately tiny — this is the part of the
    syntax rung that survives an absent engine, NOT a GDScript parser, and it
    says so wherever it is reported."""
    problems: list[str] = []
    stack: list[tuple[str, int]] = []
    for number, line in enumerate(text.splitlines(), start=1):
        code = re.sub(r"#.*$", "", re.sub(r'"[^"\n]*"|\'[^\'\n]*\'', "", line))
        for char in code:
            if char in "([{":
                stack.append((char, number))
            elif char in _OPENERS:
                if not stack or stack[-1][0] != _OPENERS[char]:
                    problems.append(f"line {number}: unmatched {char!r}")
                else:
                    stack.pop()
    for char, number in stack:
        problems.append(f"line {number}: {char!r} is never closed")
    if text.rstrip("\n").endswith("\\"):
        problems.append("the file ends on a line continuation")
    return problems


def syntax_extensions(engine_id: str = "godot") -> tuple[str, ...]:
    """The file extensions this engine's cheap syntax gate can JUDGE — data,
    read from ``engine_ops.ENGINE_COPIES`` (the same table the write wall
    uses) rather than duplicated as a literal here."""
    from canon.engine_ops import ENGINE_COPIES

    return tuple(ENGINE_COPIES.get(engine_id, {}).get("syntax_extensions") or ())


def rung_syntax(pack: Path, paths: list[str], binary: str | None) -> dict:
    """The cheap gate: the structural read always, Godot's ``--check-only``
    when the engine is here. Only the engine's answer is authoritative — and
    only for the files it can actually parse.

    ``--check-only --script`` parses GDScript ONLY: handed a ``.tscn`` (a
    legal ``edit_project_code`` target) Godot prints ``ERROR: Can't load
    script`` — not ``SCRIPT ERROR`` — and exits 0, which would read as an
    engine-proven pass for a file the engine never judged. So the spawn is
    gated on :func:`syntax_extensions` and the rung drops ``authoritative``
    whenever any named file went unjudged, naming which. A spawn that timed
    out or would not launch is a FAILURE here for the same reason
    :func:`rung_boot` calls it one: nothing was parsed, so nothing is proven.
    """
    checks: list[dict] = []
    failed = False
    unjudged: list[str] = []
    parseable = syntax_extensions()
    for rel in paths:
        target = pack / rel
        entry: dict[str, Any] = {"path": rel}
        try:
            text = target.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            entry["structural"] = [f"{type(exc).__name__}: {exc}"]
            checks.append(entry)
            failed = True
            continue
        problems = structural_check(text)
        entry["structural"] = problems
        failed = failed or bool(problems)
        if binary is None:
            unjudged.append(rel)
        elif Path(rel).suffix not in parseable:
            unjudged.append(rel)
            entry["check_only"] = {
                "skipped": (
                    f"--check-only --script parses {', '.join(parseable) or 'nothing'} only — the engine did not "
                    f"judge {rel}; this is the structural read alone"
                )
            }
        else:
            spawn = _run_godot(binary, ["--headless", "--path", str(pack), "--check-only", "--script", str(target)], {})
            errors = _script_errors(spawn["output"])
            evidence = {
                "cmd": spawn["cmd"],
                "returncode": spawn["returncode"],
                "script_errors": errors,
                "output_tail": _tail(spawn["output"]),
            }
            if spawn.get("timeout") or spawn.get("unlaunchable"):
                # returncode is None for both, which must never read as a pass:
                # the parse never happened (rung_boot's rule, same reason).
                entry["check_only"] = {**evidence, "reason": "the engine did not finish"}
                failed = True
            else:
                entry["check_only"] = evidence
                failed = failed or bool(errors) or spawn["returncode"] != 0
        checks.append(entry)
    if not paths:
        return _rung("syntax", "skipped", reason="this edit touched no source file", checks=[])
    if binary is None:
        note = f"{GODOT_MISSING}: this is the structural read only — GDScript parsing is unproven"
    elif unjudged:
        note = (
            f"the engine did not parse {', '.join(unjudged)} (--check-only --script reads "
            f"{', '.join(parseable) or 'nothing'}) — those files got the structural read only"
        )
    else:
        note = None
    return _rung(
        "syntax",
        "failed" if failed else "ok",
        authoritative=binary is not None and not unjudged,
        checks=checks,
        unjudged=unjudged,
        note=note,
    )


def rung_boot(pack: Path, binary: str | None, frames: int = BOOT_FRAMES) -> dict:
    """Headless boot. ``--path`` is the TREE ROOT (the dir holding
    ``project.godot``); pointing it at ``<tree>/godot`` silently boots the
    editor and exits 0, which is the whole reason the verdict is the
    ``SCRIPT ERROR`` count."""
    if binary is None:
        return _rung("boot", "unproven", reason=GODOT_MISSING)
    if not (pack / "project.godot").is_file():
        # UNPROVEN, not skipped: a code edit against a `godot/**` file in a
        # tree with no project.godot is exactly the case where the engine
        # never spoke, and `skipped` is excluded from the ladder's status —
        # it would come back green with no engine evidence at all.
        return _rung(
            "boot", "unproven", reason=f"no project.godot in {pack} — this pack carries no Godot copy to boot"
        )
    spawn = _run_godot(binary, ["--headless", "--path", str(pack), "--quit-after", str(frames)], {})
    errors = _script_errors(spawn["output"])
    evidence = {
        "cmd": spawn["cmd"],
        "returncode": spawn["returncode"],
        "script_errors": len(errors),
        "first_errors": errors[:5],
        "output_tail": _tail(spawn["output"]),
        "verdict_is": f"grep -c '{SCRIPT_ERROR}' (Godot's exit code lies)",
    }
    if spawn.get("timeout") or spawn.get("unlaunchable"):
        return _rung("boot", "failed", reason="the engine did not finish", **evidence)
    return _rung("boot", "failed" if errors else "ok", **evidence)


def rung_smoke(
    pack: Path,
    binary: str | None,
    levels: list[str],
    *,
    script: dict | None = None,
    frames: int = SMOKE_FRAMES,
) -> dict:
    """A scripted run through the Godot ``PLAT_*`` mirror, verified by the
    TRAJECTORY it produced rather than by its exit code."""
    if binary is None:
        return _rung("smoke", "unproven", reason=GODOT_MISSING)
    if not (pack / "project.godot").is_file():
        # UNPROVEN for rung_boot's reason: the engine copy the edit touched
        # has no project to run, so nothing about the change was exercised.
        return _rung(
            "smoke", "unproven", reason=f"no project.godot in {pack} — this pack carries no Godot copy to run"
        )
    if not levels:
        return _rung("smoke", "skipped", reason="no level to run — the pack has none and the edit named none")
    from canon.agent.tools_vision import script_env

    level_id = levels[0]
    inputs = dict(DEFAULT_SMOKE_SCRIPT if script is None else script)
    out = Path(tempfile.mkdtemp(prefix="canon-gate-"))
    try:
        traj = out / "traj.txt"
        env = {"PLAT_LEVEL": level_id, "PLAT_TRAJ": str(traj), **script_env(inputs, "gate_smoke")}
        spawn = _run_godot(
            binary,
            ["--headless", "--path", str(pack), "--quit-after", str(frames), "--fixed-fps", str(SMOKE_FPS)],
            env,
        )
        lines = [ln for ln in traj.read_text(encoding="utf-8").splitlines() if ln.strip()] if traj.is_file() else []
    finally:
        shutil.rmtree(out, ignore_errors=True)
    errors = _script_errors(spawn["output"])
    evidence = {
        "cmd": spawn["cmd"],
        "level_id": level_id,
        "script": inputs,
        "returncode": spawn["returncode"],
        "script_errors": len(errors),
        "first_errors": errors[:5],
        "traj_lines": len(lines),
        "output_tail": _tail(spawn["output"]),
        "verdict_is": "the run produced its trajectory (exit 0 is not evidence)",
    }
    if errors:
        return _rung("smoke", "failed", reason=f"{len(errors)} {SCRIPT_ERROR} line(s) during the run", **evidence)
    if len(lines) < MIN_TRAJ_LINES:
        return _rung(
            "smoke",
            "failed",
            reason=(
                f"the run wrote {len(lines)} trajectory line(s) (< {MIN_TRAJ_LINES}) — it exited without playing, so "
                "nothing about the change was exercised"
            ),
            **evidence,
        )
    return _rung("smoke", "ok", **evidence)


def rung_validate(pack: Path, levels: list[str], validate: Callable[[str], dict] | None = None) -> dict:
    """``validate_level`` on the affected levels — no engine, so this rung
    runs on every machine."""
    if not levels:
        return _rung("validate", "skipped", reason="this edit affected no level")
    try:
        verb = validate if validate is not None else _validator(pack)
    except Exception as exc:  # noqa: BLE001 — an unresolvable validator is a finding, never a pass
        return _rung("validate", "unproven", reason=f"{type(exc).__name__}: {exc}", levels=levels)
    reports: list[dict] = []
    failed = False
    for level_id in levels:
        try:
            report = verb(level_id)
        except Exception as exc:  # noqa: BLE001
            reports.append({"level_id": level_id, "ok": False, "error": f"{type(exc).__name__}: {exc}"})
            failed = True
            continue
        ok = bool(report.get("ok")) if isinstance(report, dict) else False
        problems = _problems(report) if isinstance(report, dict) else ["the validator returned no report"]
        reports.append({"level_id": level_id, "ok": ok, "problems": problems})
        failed = failed or not ok
    return _rung("validate", "failed" if failed else "ok", levels=levels, reports=reports)


def _problems(report: dict) -> list[str]:
    """Flatten a ``validate_level`` report's problems (A7's ``_level_problems``
    shape, read tolerantly so a widened report never breaks a rung)."""
    out: list[str] = []
    for check in report.get("checks") or []:
        if isinstance(check, dict):
            for problem in check.get("problems") or []:
                out.append(f"{check.get('name') or 'check'}: {problem}")
    out.extend(str(finding) for finding in report.get("findings") or [])
    return out


# ---------------------------------------------------------------------------
# The ladder
# ---------------------------------------------------------------------------


def run_ladder(
    pack_dir: str | Path,
    *,
    paths: list[str] | tuple[str, ...] = (),
    levels: list[str] | tuple[str, ...] = (),
    validate: Callable[[str], dict] | None = None,
    script: dict | None = None,
    binary: str | None = None,
    probe: dict | None = None,
) -> dict:
    """Run the ladder for a code edit and answer one verdict with evidence.

    ``paths`` are the pack-relative files that changed, ``levels`` the levels
    affected (the run's own, or :func:`smoke_levels` when it touched none).
    ``validate`` lets the caller pass the REGISTERED ``validate_level`` (A7's
    verify loop does, so the ladder honours the same tool the loop uses);
    ``binary`` / ``probe`` let a test drive the engine legs deterministically.

    Status: ``failed`` if any rung failed, else ``unproven`` if any rung could
    not be proven, else ``ok``. ``unproven`` is what an absent engine earns
    and what an engine copy with no ``project.godot`` earns, so a code edit
    can never come back green without the engine having spoken. ``skipped``
    is the OTHER answer and it is not counted: it means the ladder had
    nothing to run for that rung (no source file, no level) or an earlier
    rung already failed and stopped it.
    """
    pack = Path(pack_dir)
    found = probe if probe is not None else godot_probe()
    engine = binary if binary is not None else (found.get("path") if found.get("found") else None)

    rungs: list[dict] = []
    stopped_at: str | None = None
    for name in GATE_RUNGS:
        if stopped_at is not None:
            rungs.append(_rung(name, "skipped", reason=f"the ladder stopped at the {stopped_at} rung"))
            continue
        if name == "syntax":
            rung = rung_syntax(pack, list(paths), engine)
        elif name == "boot":
            rung = rung_boot(pack, engine)
        elif name == "smoke":
            rung = rung_smoke(pack, engine, list(levels), script=script)
        else:
            rung = rung_validate(pack, list(levels), validate)
        rungs.append(rung)
        if rung["status"] == "failed":
            stopped_at = name

    unproven = [r["rung"] for r in rungs if r["status"] == "unproven"]
    status = "failed" if stopped_at else ("unproven" if unproven else "ok")
    out: dict[str, Any] = {
        "status": status,
        "rungs": rungs,
        "paths": list(paths),
        "levels": list(levels),
        "godot": found,
        "unproven": unproven,
    }
    if stopped_at:
        out["failed_rung"] = stopped_at
    if unproven:
        # An absent engine explains every unproven rung at once; anything else
        # (no project.godot, an unresolvable validator) reports its own words
        # rather than borrowing the "godot not found" line it did not earn.
        out["reason"] = found.get("reason") or "; ".join(
            f"{r['rung']}: {r.get('reason')}" for r in rungs if r["status"] == "unproven"
        )
    return out


def ladder_summary(ladder: dict) -> str:
    """One line per rung — what the transcript and the CLI print.

    A rung that judged without the engine carries ``authoritative: False``;
    the summary says so ("structural only") so the printed line and the rung
    body cannot disagree — a green ``syntax`` on a machine with no Godot is
    not the same claim as a green one with it."""
    parts = []
    for rung in ladder.get("rungs") or []:
        label = f"{rung['rung']}: {rung['status']}"
        if rung.get("authoritative") is False:
            label += " (structural only)"
        parts.append(label)
    line = f"gate ladder {ladder.get('status')} — " + " · ".join(parts)
    reason = ladder.get("reason")
    return f"{line} ({reason})" if reason else line


__all__ = [
    "BOOT_FRAMES",
    "DEFAULT_SMOKE_SCRIPT",
    "GATE_RUNGS",
    "GATE_STATUSES",
    "GATE_TIMEOUT_S",
    "GODOT_APP_BUNDLES",
    "GODOT_ENV_VAR",
    "GODOT_MISSING",
    "GODOT_PATH_NAMES",
    "MIN_TRAJ_LINES",
    "SCRIPT_ERROR",
    "SMOKE_FRAMES",
    "godot_bin",
    "godot_probe",
    "ladder_summary",
    "run_ladder",
    "rung_boot",
    "rung_smoke",
    "rung_syntax",
    "rung_validate",
    "smoke_levels",
    "structural_check",
    "syntax_extensions",
]

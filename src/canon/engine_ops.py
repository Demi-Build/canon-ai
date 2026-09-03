"""Editing a project's OWN engine copy — the verb behind ``edit_project_code``.

Row P1-A7.5 (master §3.1 stage 6; Phase 1 §4.A, §7.1, Trace A2). This is the
write half of the engine-copy model (master §3.0-I): a pack carries one
engine copy per attached engine — Godot today, promoted pygame at W2.0 — each
stamped in ``.engine.json`` and evolvable by ``game_coder``. The verb reaches
an engine COPY and nothing else.

What it extends (doctrine 2):

- ``canon.packs.platformer.godot_export`` — the stamp is that module's format
  (``STAMP_REL``, ``template_manifest``) and this row added its attribution
  half (``stamp_modified`` / ``clear_modified``). ``engine status`` and
  ``engine sync`` are unchanged verbs; sync already refuses a file that
  differs from its own stamp, which is exactly what an edit makes true.
- ``canon.provenance`` — the CAS + journal every canon write already uses.
  A code edit is an ordinary ``op: "edit"`` event on artifact
  ``code:<pack-relative path>``, so ``get_versions`` / ``get_history`` /
  ``restore`` need no new machinery: :func:`restore_code_file` is the same
  read-object-and-write-it-back shape ``restore_asset`` uses.
- ``canon.agent.tools_read.guard_path`` — the pack-root path guard (§3.2's
  ``data.rs`` precedent). This module adds the narrower engine-copy wall on
  top of it, not a second guard.

The wall, in refusal order (each a NAMED reason — doctrine 4):

1. the shared template (``godot_template/``) — template territory, dev-only;
2. canon's own source — the agent never edits canon (Phase 1 §7.2);
3. anything outside the pack (absolute, ``..``, a symlink escape, the CAS);
4. anything inside the pack but outside an engine copy (``godot/**`` today);
5. the engine stamp itself — it is the attribution record, not gameplay code;
6. a path that does not exist — a code edit evolves the engine copy it was
   given; creating new engine files is not this row's (it would have no stamp
   lineage for ``engine sync`` to reason about).

A unified diff is the input and it must apply CLEANLY: a hunk whose context
is not found is a named refusal (``diff_did_not_apply``) and NOTHING is
written. A diff whose result is byte-identical is refused too — a no-op must
not pollute the journal (the hygiene rule ``engine_sync`` and
``apply_level_edit`` already follow). A diff whose ``+++`` side is
``/dev/null`` is refused by name (``diff_deletes_the_file``): that shape
empties the file rather than evolving it, and it names its target only on the
``---`` line, which is why :func:`diff_paths` reads BOTH sides.

Deliberately absent, by row ownership: the gate ladder that runs after an
edit (``canon.agent.gates``, this same row), the ask-tier tool wrapper
(``canon.agent.tools_code``), the panel's code-diff card (A5 built it — this
verb feeds it the ``diff`` block), the promoted-pygame copy (W2.0) and
``game_coder``'s tuning smoke (W2.1).
"""

from __future__ import annotations

import difflib
import json
import re
from pathlib import Path
from typing import Any

from canon import provenance

#: The engine copies a pack can carry, by engine id (DATA — never a literal
#: union; W2.0's pygame promotion adds an entry, it does not edit code here).
#: ``roots`` are the pack-relative directories that ARE the copy;
#: ``stamp_module`` owns that copy's ``.engine.json`` format.
ENGINE_COPIES: dict[str, dict[str, Any]] = {
    "godot": {
        "roots": ("godot",),
        "stamp_module": "canon.packs.platformer.godot_export",
        "project_file": "project.godot",
        "extensions": (".gd", ".tscn", ".tres", ".cfg", ".json"),
        # The subset the engine's cheap syntax gate can actually JUDGE:
        # `godot --headless --check-only --script` parses GDScript and nothing
        # else — handed a .tscn it prints "ERROR: Can't load script" and exits
        # 0. The gate ladder's syntax rung reads this so a scene edit is never
        # reported as an engine-proven pass (canon.agent.gates.rung_syntax).
        "syntax_extensions": (".gd",),
    },
}

#: The artifact-id namespace a code edit journals under (``code:<rel path>``),
#: matching ``canon.agent.runs.write_target``'s ``code:`` write-lock target.
CODE_NAMESPACE = "code"

#: canon's own source tree — never editable, whatever a pack path claims.
CANON_SOURCE = Path(__file__).resolve().parent

#: The interim rule master §3.0-I puts on a code-evolved pack, stated where a
#: reader hits it (the probe, the prompt, this verb's result). W2.0's pygame
#: promotion DELETES this rule — the promoted copy is evolvable too and gets
#: its own ladder; until then the pygame surfaces run template physics.
TEMPLATE_PHYSICS_NOTE = (
    "This project's engine copy is code-evolved, so the pygame-side surfaces (capture_frames, run_trajectory, "
    "cradle's per-level ▶ Play) run TEMPLATE physics, not this pack's evolved Godot code — treat what they show "
    "as an advisory about the template, and prove gameplay changes on the Godot ladder. Interim rule (master "
    "§3.0-I): W2.0's pygame promotion gives the pack its own pygame copy and deletes it."
)

#: What the user is told about sync once a file is stamped modified.
SYNC_NOTE = "engine sync will now REFUSE to overwrite this file by name (pass --force to overwrite it anyway)."


class CodeEditRefused(ValueError):
    """A code edit was refused. ``str(exc)`` is a JSON document — the
    ``_emit_error`` shape every canon verb failure uses — so both the CLI and
    the model read ``error`` / ``message`` off it instead of parsing prose."""


def _refuse(kind: str, message: str, **fields: Any) -> CodeEditRefused:
    return CodeEditRefused(
        json.dumps({"error": kind, "message": message, **fields}, separators=(",", ":"), default=str)
    )


# ---------------------------------------------------------------------------
# The wall
# ---------------------------------------------------------------------------


def _stamp_module(engine_id: str):
    from importlib import import_module

    return import_module(str(ENGINE_COPIES[engine_id]["stamp_module"]))


def template_dirs() -> dict[str, Path]:
    """Each engine's SHARED template directory, resolved — the thing an edit
    may never reach (Phase 1 §7.2: shared-template changes route through the
    dev). Missing modules are skipped rather than fatal."""
    out: dict[str, Path] = {}
    for engine_id in ENGINE_COPIES:
        try:
            template = getattr(_stamp_module(engine_id), "TEMPLATE_DIR", None)
        except ImportError:  # pragma: no cover — the pack ships with the wheel
            continue
        if template is not None:
            out[engine_id] = Path(template).resolve()
    return out


def _inside(parent: Path, child: Path) -> bool:
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return True


def _refuse_untouchable(candidate: Path, raw: str) -> None:
    """The two walls that hold no matter how the path was spelled: the shared
    template and canon's source. Checked on the RESOLVED location, so an
    absolute path, a symlink or a pack that happens to live inside canon's
    checkout all answer the same named refusal."""
    resolved = candidate.expanduser()
    try:
        resolved = resolved.resolve()
    except OSError:  # pragma: no cover — unresolvable paths fall through
        resolved = resolved.absolute()
    for engine_id, template in template_dirs().items():
        if _inside(template, resolved):
            raise _refuse(
                "code_path_is_the_shared_template",
                f"{raw!r} is inside canon's shared {engine_id} template — every project copies it, so an edit "
                "there would change games nobody asked about. Edit this project's own copy "
                f"({', '.join(ENGINE_COPIES[engine_id]['roots'])}/…); template changes route through the dev.",
                path=raw,
                template=str(template),
            )
    if _inside(CANON_SOURCE, resolved):
        raise _refuse(
            "code_path_is_canon_source",
            f"{raw!r} is inside canon's own source — the agent never edits canon, cradle or a shared template "
            "(Phase 1 §7.2). Only this project's engine copy is editable.",
            path=raw,
            canon_source=str(CANON_SOURCE),
        )


def engine_of(rel: str) -> str | None:
    """The engine copy a pack-relative path belongs to, or ``None``."""
    parts = Path(rel).parts
    for engine_id, block in ENGINE_COPIES.items():
        for root in block["roots"]:
            if parts[: len(Path(root).parts)] == Path(root).parts:
                return engine_id
    return None


def guard_code_path(pack_dir: str | Path, rel: str) -> tuple[str, Path]:
    """``(engine_id, path)`` for an editable file, or ``CodeEditRefused``.

    The wall in the module docstring's order. The returned path is the
    original (unresolved) location so messages name what was asked for."""
    from canon.agent.tools_read import guard_path

    pack = Path(pack_dir)
    raw = rel if isinstance(rel, str) else ""
    if not raw or "\x00" in raw:
        raise _refuse("code_path_invalid", f"path must be a non-empty pack-relative path (got {rel!r})", path=str(rel))

    # An ABSOLUTE path is judged where it points; a pack-relative one is judged
    # inside the pack. Resolving a relative path bare would anchor it at the
    # process CWD — a directory the caller never named — so `godot/main.gd`
    # would refuse as canon source purely because the CLI happened to run from
    # inside canon's checkout. The two cases partition; neither is skipped.
    if Path(raw).is_absolute():
        _refuse_untouchable(Path(raw), raw)
    else:
        _refuse_untouchable(pack / raw, raw)
    try:
        target = guard_path(pack, raw)
    except ValueError as exc:
        raise _refuse(
            "code_path_escapes_the_pack",
            f"{exc} — a code edit may only touch THIS project's engine copy, never another pack's.",
            path=raw,
        ) from None

    engine_id = engine_of(raw)
    if engine_id is None:
        roots = sorted(root for block in ENGINE_COPIES.values() for root in block["roots"])
        raise _refuse(
            "code_path_outside_engine_copy",
            f"{raw!r} is in the pack but outside its engine copy. Code edits reach "
            f"{', '.join(f'{r}/**' for r in roots)} only — pack DATA is edited with the data verbs "
            "(apply_level_edit, update_row, …), which validate and journal per field.",
            path=raw,
            engine_roots=roots,
        )
    stamp_rel = str(getattr(_stamp_module(engine_id), "STAMP_REL", ""))
    if stamp_rel and Path(raw).as_posix() == Path(stamp_rel).as_posix():
        raise _refuse(
            "code_path_is_the_engine_stamp",
            f"{raw!r} is the engine stamp — the record of what canon wrote and who changed it since. It is written "
            "by the verbs (engine sync, engine edit), never edited as code.",
            path=raw,
        )
    if not target.is_file():
        raise _refuse(
            "code_path_missing",
            f"{raw!r} does not exist in this pack's engine copy. A code edit evolves a file the copy already has; "
            "adding new engine files has no stamp lineage for engine sync to reason about and is not this verb's.",
            path=raw,
        )
    return engine_id, target


# ---------------------------------------------------------------------------
# The unified diff
# ---------------------------------------------------------------------------

_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def diff_paths(patch: str) -> list[str]:
    """Every path a unified diff names, ``---`` and ``+++`` both (``a/`` /
    ``b/`` stripped, ``/dev/null`` dropped).

    Both sides are read because ``edit_project_code``'s one-file-per-edit
    check is the only reader: a deletion-shaped diff (``+++ /dev/null``)
    names its file on the ``---`` line alone, and reading ``+++`` only would
    let it through the check and truncate whatever ``path`` named."""
    out: list[str] = []
    for line in patch.splitlines():
        if line.startswith(("--- ", "+++ ")):
            value = line[4:].strip().split("\t")[0]
            if value in ("/dev/null", ""):
                continue
            parts = Path(value).parts
            named = Path(*parts[1:]).as_posix() if parts and parts[0] in ("a", "b") else value
            if named not in out:
                out.append(named)
    return out


def parse_hunks(patch: str) -> list[dict]:
    """Every hunk in a unified diff: ``{old_start, old, new}`` where ``old`` /
    ``new`` are the line lists the hunk expects and produces.

    The hunk HEADER's counts drive consumption, which is what makes a diff
    that deletes a line beginning ``--`` unambiguous — the classic unified
    diff trap when a parser sniffs prefixes instead."""
    lines = patch.splitlines()
    hunks: list[dict] = []
    index = 0
    while index < len(lines):
        match = _HUNK.match(lines[index])
        if match is None:
            index += 1
            continue
        old_start = int(match.group(1))
        old_left = int(match.group(2)) if match.group(2) is not None else 1
        new_left = int(match.group(4)) if match.group(4) is not None else 1
        old: list[str] = []
        new: list[str] = []
        added = removed = 0
        index += 1
        while index < len(lines) and (old_left > 0 or new_left > 0):
            line = lines[index]
            index += 1
            if line.startswith("\\"):  # "\ No newline at end of file"
                continue
            marker, body = (line[0], line[1:]) if line else (" ", "")
            if marker == " ":
                old.append(body)
                new.append(body)
                old_left -= 1
                new_left -= 1
            elif marker == "-":
                old.append(body)
                old_left -= 1
                removed += 1
            elif marker == "+":
                new.append(body)
                new_left -= 1
                added += 1
            else:
                raise _refuse(
                    "diff_malformed",
                    f"line {index} of the diff is neither context, addition nor removal: {line!r}",
                    line=line,
                )
        if old_left > 0 or new_left > 0:
            raise _refuse(
                "diff_truncated",
                f"the hunk at @@ -{old_start} @@ promises more lines than the diff carries "
                f"({old_left} old / {new_left} new missing)",
            )
        hunks.append({"old_start": old_start, "old": old, "new": new, "added": added, "removed": removed})
    return hunks


def apply_unified_diff(text: str, patch: str) -> tuple[str, dict]:
    """``(new_text, stats)`` — or ``CodeEditRefused``. Never partial.

    Each hunk is located by its OWN content: the lines it expects must appear
    in the file, and among equal candidates the one nearest the hunk header's
    line number wins. That tolerates a diff whose line numbers drifted (the
    model counting from a windowed read) while still refusing a diff written
    against code that is no longer there.
    """
    hunks = parse_hunks(patch)
    if not hunks:
        raise _refuse(
            "diff_empty",
            "no @@ hunks in the diff — pass a unified diff (`@@ -12,4 +12,6 @@` headers, ' ' context, '-' removed, "
            "'+' added lines) of the file's current text.",
        )
    lines = text.split("\n")
    added = removed = 0
    cursor = 0  # hunks apply in order; a later hunk never matches earlier text
    for position, hunk in enumerate(hunks, start=1):
        old, new = hunk["old"], hunk["new"]
        candidates = _candidates(lines, old, cursor)
        if not candidates:
            raise _refuse(
                "diff_did_not_apply",
                f"hunk {position} (@@ -{hunk['old_start']} @@) does not apply: the file does not contain the lines "
                "it expects. Nothing was written — read the file again and diff against its current text.",
                hunk=position,
                expected=old[:12],
            )
        hint = max(hunk["old_start"] - 1, cursor)
        at = min(candidates, key=lambda c: (abs(c - hint), c))
        lines[at : at + len(old)] = new
        cursor = at + len(new)
        added += int(hunk["added"])
        removed += int(hunk["removed"])
    return "\n".join(lines), {"hunks": len(hunks), "added": added, "removed": removed}


def _candidates(lines: list[str], old: list[str], cursor: int) -> list[int]:
    """Every index at/after ``cursor`` where ``old`` matches ``lines``."""
    if not old:
        return [cursor]
    limit = len(lines) - len(old)
    return [i for i in range(cursor, limit + 1) if lines[i : i + len(old)] == old]


def unified_diff(before: str, after: str, path: str) -> str:
    """The unified diff the transcript renders (A5's code card reads exactly
    this text — README §5's "a real unified diff with @@ hunk headers")."""
    return "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
        )
    )


def diff_block(before: str, after: str, path: str) -> dict:
    """The ``diff`` block a tool result carries for the panel's code card
    (``{kind, path, unified, added, removed}`` — cradle's
    ``agentState.diffOf`` shape). Counted from the RENDERED diff, so the
    numbers on the card and the text on the card always agree."""
    text = unified_diff(before, after, path)
    body = [line for line in text.splitlines() if not line.startswith(("---", "+++"))]
    return {
        "kind": "code",
        "path": path,
        "unified": text,
        "added": sum(1 for line in body if line.startswith("+")),
        "removed": sum(1 for line in body if line.startswith("-")),
    }


# ---------------------------------------------------------------------------
# The verb
# ---------------------------------------------------------------------------


def edit_project_code(
    pack_dir: str | Path,
    path: str,
    diff: str,
    *,
    actor: str = "user",
    session: str | None = None,
) -> dict:
    """Apply ``diff`` to ``path`` inside this project's own engine copy.

    Doctrine 1, in order: resolve the engine copy → the protected wall
    (:func:`guard_code_path`) → fail-closed validate (the diff must apply
    cleanly and must change something) → warnings → the ``modified`` stamp
    (this row's ``user_edited`` equivalent: attribution that makes
    ``engine sync`` refuse the file by name) → journal ``op: "edit"`` on
    ``code:<path>`` with before/after hashes → CAS snapshot of both.

    Returns ``{path, engine, new_hash, before_hash, added, removed, hunks,
    stamped, diff, note, sync}`` — ``diff`` being A5's code-card block.
    """
    pack = Path(pack_dir)
    engine_id, target = guard_code_path(pack, path)
    rel = Path(path).as_posix()

    if any(line[4:].strip().split("\t")[0] == "/dev/null" for line in diff.splitlines() if line.startswith("+++ ")):
        raise _refuse(
            "diff_deletes_the_file",
            f"the diff's +++ side is /dev/null, so it DELETES {rel!r} rather than changing it. A code edit evolves a "
            "file the engine copy already has; removing one has no stamp lineage for engine sync to reason about "
            "and is not this verb's.",
            path=rel,
        )
    named = diff_paths(diff)
    stray = [p for p in named if Path(p).as_posix() not in (rel, Path(rel).name)]
    if stray:
        raise _refuse(
            "diff_path_mismatch",
            f"the diff names {stray} but this call edits {rel!r} — one file per edit, and the diff must be that "
            "file's. Send one edit_project_code call per file.",
            path=rel,
            named=named,
        )

    try:
        before = target.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise _refuse(
            "code_path_not_text",
            f"{rel!r} is not UTF-8 text, so a unified diff cannot describe a change to it ({exc}).",
            path=rel,
        ) from None

    after, stats = apply_unified_diff(before, diff)
    if after == before:
        raise _refuse(
            "diff_no_change",
            f"the diff applies but leaves {rel!r} byte-identical — nothing was written and nothing was journaled "
            "(a no-op must not become a version).",
            path=rel,
        )

    # The stamp baseline is captured BEFORE the write: on a pack that predates
    # stamping there is no record of what canon put there, and a baseline taken
    # afterwards would record the edit itself — the file would read `stale` and
    # the next `engine sync` would silently overwrite the agent's work.
    stamp_module = _stamp_module(engine_id)
    stamp_module.ensure_stamp(pack)

    before_hash = provenance.snapshot_file(pack, target)
    target.write_text(after, encoding="utf-8")
    after_hash = provenance.snapshot_file(pack, target)

    stamp_module.stamp_modified(pack, rel, after_hash=after_hash, actor=actor, session=session, op="edit")

    provenance.record(
        pack,
        artifact_id=f"{CODE_NAMESPACE}:{rel}",
        op="edit",
        source="code",
        actor=actor,
        session=session,
        detail={"kind": "edit_project_code", "engine": engine_id, **stats},
        before_hash=before_hash,
        after_hash=after_hash,
    )
    return {
        "path": rel,
        "engine": engine_id,
        "before_hash": before_hash,
        "new_hash": after_hash,
        "stamped": "modified",
        **stats,
        "diff": diff_block(before, after, rel),
        "sync": SYNC_NOTE,
        "note": TEMPLATE_PHYSICS_NOTE,
    }


def restore_code_file(
    pack_dir: str | Path,
    target_id: str,
    version_hash: str,
    *,
    actor: str = "user",
    session: str | None = None,
) -> dict:
    """One-click restore for ``code:<pack-relative path>`` (doctrine 6).

    Writes the stored version's bytes back, journals ``op: "restore"`` (a NEW
    version — nothing is deleted), and CLEARS the ``modified`` stamp when the
    restored bytes are canon's again, so ``engine sync`` resumes managing the
    file and the pack stops reading code-evolved for it. Restoring to some
    other agent-written version keeps the stamp, re-attributed to this restore.
    """
    pack = Path(pack_dir)
    kind, _, rel = str(target_id).partition(":")
    if kind != CODE_NAMESPACE or not rel:
        raise _refuse(
            "restore_target_invalid",
            f"code restore targets are {CODE_NAMESPACE}:<pack-relative path> (got {target_id!r})",
            target=str(target_id),
        )
    engine_id, path = guard_code_path(pack, rel)
    rel = Path(rel).as_posix()

    if not any(
        event.get("artifact_id") == f"{CODE_NAMESPACE}:{rel}"
        and version_hash in (event.get("before_hash"), event.get("after_hash"))
        for event in provenance.all_events(pack)
    ):
        raise _refuse(
            "restore_not_in_lineage",
            f"{version_hash} is not part of {CODE_NAMESPACE}:{rel}'s history — restore only rewinds an artifact's "
            "own lineage.",
            target=f"{CODE_NAMESPACE}:{rel}",
            version_hash=version_hash,
        )
    data = provenance.read_object(pack, version_hash)

    before_hash = provenance.snapshot_file(pack, path)
    path.write_bytes(data)
    after_hash = provenance.snapshot_file(pack, path)

    stamp_module = _stamp_module(engine_id)
    canon_wrote = (stamp_module.read_stamp(pack) or {}).get("files", {}).get(rel)
    cleared = after_hash == canon_wrote
    if cleared:
        stamp_module.clear_modified(pack, rel)
    else:
        stamp_module.stamp_modified(pack, rel, after_hash=after_hash, actor=actor, session=session, op="restore")

    provenance.record(
        pack,
        artifact_id=f"{CODE_NAMESPACE}:{rel}",
        op="restore",
        source="code",
        actor=actor,
        session=session,
        detail={"kind": "restore_project_code", "engine": engine_id, "to": version_hash, "stamp_cleared": cleared},
        before_hash=before_hash,
        after_hash=after_hash,
    )
    return {
        "path": rel,
        "engine": engine_id,
        "restored_to": version_hash,
        "before_hash": before_hash,
        "new_hash": after_hash,
        "stamp_cleared": cleared,
        "stamped": None if cleared else "modified",
        "note": (
            f"{rel} matches what canon wrote again — the modified stamp is cleared and engine sync manages it once "
            "more."
            if cleared
            else f"{rel} still differs from what canon wrote, so it stays stamped modified. {SYNC_NOTE}"
        ),
    }


def code_evolved(pack_dir: str | Path) -> dict:
    """The probe's code-evolved block (§7.1's disclosure, master §3.0-I).

    ``{engine, present, stamped, code_evolved, modified, unstamped, stale,
    attribution, note}``. Never fatal: a pack with no engine copy, or an
    engine module that will not import, answers ``present: false`` with the
    reason rather than raising into a read tool.
    """
    pack = Path(pack_dir)
    for engine_id, block in ENGINE_COPIES.items():
        project = block.get("project_file")
        if project and not (pack / str(project)).is_file():
            continue
        try:
            status = _stamp_module(engine_id).engine_status(pack)
        except Exception as exc:  # noqa: BLE001 — a probe names its failure, never raises
            return {
                "engine": engine_id,
                "present": False,
                "code_evolved": False,
                "problem": f"{type(exc).__name__}: {exc}",
            }
        states = {str(f.get("path")): str(f.get("state")) for f in status.get("files") or []}
        evolved = bool(status.get("code_evolved"))
        out = {
            "engine": engine_id,
            "present": True,
            "stamped": bool(status.get("stamped")),
            "code_evolved": evolved,
            "modified": sorted(k for k, v in states.items() if v == "modified"),
            "unstamped": sorted(k for k, v in states.items() if v == "unstamped"),
            "stale": sorted(k for k, v in states.items() if v == "stale"),
            "attribution": status.get("attribution") or {},
        }
        if evolved:
            out["disclose"] = (
                "This pack's engine copy has agent- or hand-edited files. Say so in the transcript BEFORE running, "
                "capturing or launching anything with it (Phase 1 §7.1)."
            )
            out["note"] = TEMPLATE_PHYSICS_NOTE
        return out
    return {
        "engine": None,
        "present": False,
        "code_evolved": False,
        "reason": "this pack carries no engine copy (no project.godot) — it was generated without an engine",
    }


__all__ = [
    "CANON_SOURCE",
    "CODE_NAMESPACE",
    "ENGINE_COPIES",
    "SYNC_NOTE",
    "TEMPLATE_PHYSICS_NOTE",
    "CodeEditRefused",
    "apply_unified_diff",
    "code_evolved",
    "diff_block",
    "diff_paths",
    "edit_project_code",
    "engine_of",
    "guard_code_path",
    "parse_hunks",
    "restore_code_file",
    "template_dirs",
    "unified_diff",
]

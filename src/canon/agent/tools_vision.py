"""The auto-tier vision tools — eyes on the game (Phase 1 A7; master §3.1 stage 5).

``register_vision_tools(registry, pack_dir)`` adds the three §4.C/§4.D tools
row A3 deliberately left out to row A2's ``ToolRegistry``:

- ``capture_frames(level_id, ticks?, every?, script?)`` — spawns the pygame
  harness HEADLESS (``python -m canon.packs.platformer.play`` — the same
  module cradle's ▶ Play spawns, in the wheel since P0-4) with
  ``PLAT_CAPTURE`` + a fixed tick budget, reads the PNGs back as image
  attachments and DELETES them. Nothing lands in the pack.
- ``run_trajectory(level_id, inputs)`` — the same harness under
  ``PLAT_TRAJ`` + ``PLAT_HOLD`` / ``PLAT_HOLD_JUMP_EVERY`` / ``PLAT_ACTIONS``,
  answering a SUMMARY of the trajectory (never the raw per-tick file).
- ``view_asset(target)`` — an asset's bytes as an image attachment plus its
  metadata. This is the one stand-in row A1's eval corpus still carried
  (``canon.agent.evals.VIEW_ASSET``); from this row the corpus binds to the
  real spec like every other tool.

All three are tier ``"auto"`` per ASSUMPTION-6a: windowless, a fixed tick
count, they write nothing into the pack, and they execute the same engine
the user's own ▶ Play executes. The escape hatch is DATA, not a code
change — ``<pack>/.canon/agent/settings.json`` may carry::

    {"tool_tiers": {"capture_frames": "ask", "run_trajectory": "ask"}}

and the tier resolver (``PermissionEngine.tier_with``, row A6's seam for
"free never spend-confirms") re-reads that file on every call, so a demote
takes effect without a restart. Any tool name maps to any tier string —
neither vocabulary is a literal union (master §3.0-B).

It lives beside the grants (``.canon/agent/``, the agent's own durable
directory) rather than in ``.canon/registry.json``, because that registry is
SYNTHESIZED: a pack that has never been ``registry set`` has no registry
file, so the next write verb's ``registry_ops.ensure_registry`` commits a
synthesized document over anything hand-written there and the demote would
silently revert. ``.canon/registry.json`` → ``agent.tool_tiers`` is still
read as a secondary source (harmless in a pack whose registry already
exists); the settings file wins.

What this extends, and what it does not (doctrine 2):

- The PLAT_* protocol is the harness's own (``canon.packs.platformer.play``
  ``_Hooks``); this module SETS those variables, it does not add any.
- The launch is engine-resolved through the pack's engines block
  (§3.0-H/§3.0-I) by ``harness_launch``: an ``engines[]`` entry whose
  ``launch.headless`` block names a command wins; with none — the shape of
  every Phase 0/1 pack, whose only entry is Godot — the built-in pygame
  harness answers, and W2.0's promotion makes that a config row rather than
  a rewrite here.
- The env is SCRUBBED the way cradle's ``play_level`` scrubs it: every
  inherited ``PLAT_*`` is removed (a prefix rule, so a hook added later is
  covered without a second list) and only what this call means is set.
- Exit 0 is never trusted: each tool asserts the process actually produced
  output (frames on disk / traj lines) and raises a JSON-bodied
  ``HeadlessError`` naming what was missing — the engineering chip a wrong
  ``--path`` taught on the Godot side (the editor boots, exit code lies).
- Attachments ride the canonical ``image`` block ``canon.llm.chat``
  documents; the anthropic backend passes them through untouched. The
  TRANSCRIPT stores a reference, never the bytes
  (``canon.agent.conversations.ATTACHMENT_PLACEHOLDER``, Phase 1 §3.4) —
  images are re-attached only when a question needs eyes, by calling the
  tool again.

Deliberately absent, by row ownership: the panel's rendering of these
attachments (A5), VLM judging as a tool (a real vision backend is a
user-run paid leg — ``canon.backends.vlm_anthropic``; tests use
``FakeVLMBackend``), ``asset_lineage`` (A6's journal read side), the
engine gate ladder (A7.5), play sessions (W2.0).
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from canon.agent.registry import Tool, ToolRegistry
from canon.agent.tools_read import ToolInputError, compact, guard_path, validate_input
from canon.llm.chat import ToolSpec

#: The tier all three register under (ASSUMPTION-6a). Data, like every tier.
VISION_TIER = "auto"

#: The tools this row registers, in registration order.
VISION_TOOL_NAMES: tuple[str, ...] = ("capture_frames", "run_trajectory", "view_asset")

#: The two that spawn a process — the pair ASSUMPTION-6a's registry flag demotes.
HEADLESS_TOOL_NAMES: tuple[str, ...] = ("capture_frames", "run_trajectory")

#: Where the demote flag lives, primary: ``<pack>/.canon/agent/settings.json``
#: → ``tool_tiers`` (``{tool_name: tier}``; both vocabularies open — never a
#: literal union). This is the agent's own durable directory — the one
#: ``GrantStore`` (``.canon/agent/permissions.json``) already owns — because
#: ``.canon/registry.json`` is SYNTHESIZED: a pack that has never been
#: ``registry set`` has no registry file at all, so the first write verb calls
#: ``registry_ops.ensure_registry``, which commits a freshly synthesized
#: document over whatever was hand-written there. A safety setting that
#: silently reverts is the opposite of doctrine 4.
AGENT_SETTINGS_FILE = Path(".canon") / "agent" / "settings.json"
AGENT_TIER_PATH: tuple[str, ...] = ("tool_tiers",)

#: Secondary, and still honoured: ``.canon/registry.json`` → ``agent.tool_tiers``.
#: Safe in a pack whose registry already exists (``resolve_pack`` answers tier 1
#: with it and ``ensure_registry`` leaves it alone), so a flag set there before
#: this fallback existed keeps working. The settings file wins when both name a tool.
REGISTRY_TIER_PATH: tuple[str, ...] = ("agent", "tool_tiers")

#: The harness module cradle's ▶ Play spawns (P0-4 put it in the wheel).
HARNESS_MODULE = "canon.packs.platformer.play"

#: The engines-block id whose ``launch.headless`` block, once W2.0 promotes
#: the pygame copy, replaces ``HARNESS_MODULE`` as data (§3.0-I).
HARNESS_ENGINE_ID = "pygame"

#: Tick budget defaults — the harness's own (``PLAT_CAPTURE_TICKS`` /
#: ``PLAT_CAPTURE_EVERY``), restated so the tool's schema can advertise them.
DEFAULT_TICKS = 300
DEFAULT_EVERY = 30

#: Hard ceilings: a fixed budget is what makes these auto-tier (ASSUMPTION-6a).
MAX_TICKS = 3000
MAX_FRAMES = 8

#: Wall-clock ceiling for one harness process. A hung harness is a named
#: failure, never a wedged tool call.
HARNESS_TIMEOUT_S = 180.0

#: ``PLAT_HOLD`` vocabulary (the harness's; data, extendable).
HOLD_MODES: tuple[str, ...] = ("right", "left", "run_right", "run_left")

#: ``PLAT_ACTIONS`` vocabulary (single-frame inputs the hold modes cannot express).
ACTION_KINDS: tuple[str, ...] = ("down", "up")

#: suffix → media type for an attached asset (open map; an unknown suffix is
#: a named refusal rather than a guessed content type).
MEDIA_TYPES: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}

#: Non-row art targets ``view_asset`` resolves that no ``EntityKind.asset``
#: block covers yet (the row kinds answer from their own stamped data).
#: ``target kind -> (pack-relative manifest, the field holding the path(s))``.
#: W2.1's art verbs widen this vocabulary; each entry dissolves the moment
#: its subject becomes a row kind with an ``asset`` block.
_MANIFEST_TARGETS: dict[str, tuple[str, str]] = {
    "tilesheet": ("tileset/{id}/manifest.json", "tilesheet_path"),
    "backdrop": ("backdrop/{id}/manifest.json", "band_paths"),
}

#: Targets that are a fixed path in the tree (no manifest, no row).
_FIXED_TARGETS: dict[str, str] = {"player": "sprite/player/base.png"}


class HeadlessError(RuntimeError):
    """A headless harness run failed or produced nothing. ``str(exc)`` is a
    JSON document — the same ``_emit_error`` shape every canon verb failure
    uses — so the model reads ``error`` / ``returncode`` / ``stderr`` off the
    ``is_error`` tool result instead of parsing prose."""


def _fail(kind: str, message: str, **fields: Any) -> HeadlessError:
    return HeadlessError(compact({"error": kind, "message": message, **fields}))


# ---------------------------------------------------------------------------
# Attachments: canonical image blocks + their references
# ---------------------------------------------------------------------------


def sha256_of(data: bytes) -> str:
    """``sha256:<hex>`` — the digest form the CAS and the journal already use
    (``canon.provenance``). Nothing is STORED: an attachment is bytes on the
    wire, referenced by this hash in the transcript (§3.4)."""
    return "sha256:" + hashlib.sha256(data).hexdigest()


def media_type_for(name: str) -> str:
    """The media type for a file name, or ``ToolInputError`` naming the map."""
    suffix = Path(name).suffix.lower()
    media = MEDIA_TYPES.get(suffix)
    if media is None:
        raise ToolInputError(
            f"view_asset: {name!r} is not an attachable image (known suffixes: {sorted(MEDIA_TYPES)})"
        )
    return media


def image_block(data: bytes, media_type: str = "image/png") -> dict:
    """One canonical ``image`` content block (``canon.llm.chat``'s shape) —
    base64, provider-neutral, passed through untouched by the backends."""
    import base64

    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": media_type,
            "data": base64.standard_b64encode(data).decode("ascii"),
        },
    }


def attachment_ref(data: bytes, *, name: str, media_type: str, path: str | None = None, **extra: Any) -> dict:
    """The REFERENCE that rides in the summary and survives in the
    transcript: ``{name, path, sha256, bytes, media_type}`` (+ whatever the
    caller adds, e.g. the tick a frame came from). ``path`` is the
    pack-relative file for a real asset and ``None`` for an ephemeral
    capture — a frame is not a pack file and never claims to be."""
    return {
        "name": name,
        "path": path,
        "sha256": sha256_of(data),
        "bytes": len(data),
        "media_type": media_type,
        **extra,
    }


def blocks_result(summary: dict, images: list[dict]) -> list[dict]:
    """A tool result as content blocks: the compact JSON summary first (it
    carries the attachment refs), then the images. ``run_conversation``
    passes a canonical block list straight into the ``tool_result``."""
    return [{"type": "text", "text": compact(summary)}, *images]


def attachment_refs(result: Any) -> list[dict]:
    """The refs inside a block-list tool result, for the caller that wants
    the references without the bytes (the run manager folds these into a
    delegation's ``attachments`` — §5.1's result contract, §3.4's rule that
    only refs travel). Tolerant: anything else answers ``[]``."""
    if not isinstance(result, list) or not result:
        return []
    head = result[0]
    if not isinstance(head, dict) or head.get("type") != "text":
        return []
    try:
        payload = json.loads(head.get("text") or "")
    except ValueError:
        return []
    refs = payload.get("attachments") if isinstance(payload, dict) else None
    return [r for r in refs if isinstance(r, dict)] if isinstance(refs, list) else []


# ---------------------------------------------------------------------------
# The registry flag (ASSUMPTION-6a's escape hatch — data)
# ---------------------------------------------------------------------------


def _tier_at(path: Path, keys: tuple[str, ...], name: str) -> str | None:
    """``name``'s tier inside ``path`` under ``keys``, or ``None``. Never
    fatal: a missing, unreadable or malformed file configures nothing."""
    try:
        node: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    for key in keys:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    if not isinstance(node, dict):
        return None
    tier = node.get(name)
    return tier if isinstance(tier, str) and tier else None


def registry_tier(pack: Path, name: str) -> str | None:
    """The tier the pack's DATA configures for ``name``, or ``None``.

    Two sources, read fresh on every call (§3.4's "re-probe rather than
    trust"): ``<pack>/.canon/agent/settings.json`` → ``tool_tiers`` first —
    the agent's own durable directory, which nothing synthesizes over — then
    ``.canon/registry.json`` → ``agent.tool_tiers`` for a flag set there
    before the settings file existed. Neither is fatal: with no readable
    source the tool keeps its registered tier."""
    return _tier_at(pack / AGENT_SETTINGS_FILE, AGENT_TIER_PATH, name) or _tier_at(
        pack / ".canon" / "registry.json", REGISTRY_TIER_PATH, name
    )


def tier_resolver(pack: Path, name: str) -> Callable[[dict], str]:
    """The ``PermissionEngine.tier_with`` resolver for ``name`` — the registry
    flag when set, else ``VISION_TIER``. The engine fails closed on anything
    it does not recognise, so a nonsense flag keeps the registered tier."""

    def resolve(_tool_input: dict) -> str:
        return registry_tier(pack, name) or VISION_TIER

    resolve.__name__ = f"tier_for_{name}"
    return resolve


# ---------------------------------------------------------------------------
# The headless harness
# ---------------------------------------------------------------------------


def scrubbed_env(extra: dict[str, str]) -> dict[str, str]:
    """The child's environment: this process's, minus EVERY inherited
    ``PLAT_*`` hook, plus ``extra`` and a dummy video driver.

    The prefix rule is deliberate — cradle's ``play_level`` scrubs a literal
    13-name list, which a new hook silently escapes; ``PLAT_`` covers every
    hook the harness will ever read. ``SDL_VIDEODRIVER=dummy`` is what makes
    ASSUMPTION-6a's "windowless" literally true: the harness always calls
    ``pygame.display.set_mode``, so without it a window would open on the
    user's screen.
    """
    env = {k: v for k, v in os.environ.items() if not k.startswith("PLAT_")}
    env["SDL_VIDEODRIVER"] = "dummy"
    env.update(extra)
    return env


def harness_launch(pack: Path, level_id: str) -> list[str]:
    """The headless launch command, engine-resolved (§3.0-I).

    An ``engines[]`` entry (from the pack's registry, else the seed) whose
    ``launch.headless`` block carries a ``cmd``/``args`` pair wins, with
    ``{python}`` / ``{pack}`` / ``{level}`` substituted. No pack in Phases
    0–1 has one — their single entry is Godot, which cannot serve
    ``PLAT_CAPTURE`` — so the built-in pygame harness answers, spawned as a
    MODULE (``sys.executable -m``: the P0-4 wheel promotion, no script path,
    no checkout). W2.0's pygame promotion adds the entry and this resolver
    reads it: config, not rework.
    """
    for entry in _engine_entries(pack):
        if str(entry.get("id") or "") != HARNESS_ENGINE_ID:
            continue
        launch = entry.get("launch") if isinstance(entry.get("launch"), dict) else {}
        headless = launch.get("headless") if isinstance(launch.get("headless"), dict) else None
        if not headless or not headless.get("cmd"):
            continue
        fields = {"python": sys.executable, "pack": str(pack), "level": level_id}
        argv = [str(headless["cmd"]), *(str(a) for a in headless.get("args") or [])]
        return [a.format(**fields) for a in argv]
    return [sys.executable, "-m", HARNESS_MODULE, str(pack), level_id]


def _engine_entries(pack: Path) -> list[dict]:
    """The pack's engines block — the stamped registry's when it has one,
    else the seed's. Never fatal: an unresolvable pack answers ``[]`` and the
    built-in harness is used."""
    try:
        from canon.packs import resolve_pack

        resolved = resolve_pack(pack)
    except Exception:  # noqa: BLE001 — engine resolution never breaks a read tool
        return []
    registry = resolved.registry if isinstance(getattr(resolved, "registry", None), dict) else {}
    entries = registry.get("engines") if isinstance(registry.get("engines"), list) else None
    if entries is None:
        entries = list(resolved.spec.engines)
    return [e for e in entries if isinstance(e, dict)]


def script_env(script: dict, tool: str) -> dict[str, str]:
    """``{hold, jump_every, actions}`` → the ``PLAT_*`` the harness reads.

    One vocabulary for both headless tools: ``capture_frames``' ``script``
    and ``run_trajectory``'s ``inputs`` are the same object, so a finding
    from one reproduces on the other with the same argument.
    """
    env: dict[str, str] = {}
    hold = script.get("hold")
    if hold:
        if hold not in HOLD_MODES:
            raise ToolInputError(f"{tool}: hold must be one of {list(HOLD_MODES)} (got {hold!r})")
        env["PLAT_HOLD"] = str(hold)
    jump_every = script.get("jump_every")
    if jump_every:
        env["PLAT_HOLD_JUMP_EVERY"] = str(int(jump_every))
    actions = script.get("actions") or []
    if actions:
        for entry in actions:
            if entry.get("action") not in ACTION_KINDS:
                raise ToolInputError(
                    f"{tool}: action must be one of {list(ACTION_KINDS)} (got {entry.get('action')!r})"
                )
        env["PLAT_ACTIONS"] = ",".join(f"{int(e['frame'])}:{e['action']}" for e in actions)
    return env


def run_harness(pack: Path, level_id: str, env_extra: dict[str, str], *, tool: str) -> subprocess.CompletedProcess:
    """Spawn the headless harness once and return the finished process.

    Raises ``HeadlessError`` for a timeout or a non-zero exit. The CALLER
    still has to prove output landed — exit 0 is not evidence (the Godot
    lesson: a wrong ``--path`` boots the editor and exits clean).
    """
    argv = harness_launch(pack, level_id)
    try:
        proc = subprocess.run(  # noqa: S603 — our own harness, argv list, no shell
            argv,
            env=scrubbed_env(env_extra),
            capture_output=True,
            text=True,
            timeout=HARNESS_TIMEOUT_S,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise _fail(
            "harness_timeout",
            f"the headless harness did not finish within {HARNESS_TIMEOUT_S:g}s",
            tool=tool,
            level_id=level_id,
            cmd=argv,
        ) from exc
    except OSError as exc:
        raise _fail("harness_unlaunchable", f"could not start the harness: {exc}", tool=tool, cmd=argv) from exc
    if proc.returncode != 0:
        raise _fail(
            "harness_failed",
            f"the headless harness exited {proc.returncode}",
            tool=tool,
            level_id=level_id,
            returncode=proc.returncode,
            stderr=_tail(proc.stderr),
        )
    return proc


def _tail(text: str | None, limit: int = 600) -> str:
    value = (text or "").strip()
    return value[-limit:]


def _template_physics_note(pack: Path) -> str | None:
    """Master §3.0-I's interim rule, attached where a reader HITS it.

    These are the pygame surfaces the rule is about: for a code-evolved pack
    they run TEMPLATE physics, not the pack's evolved Godot code. Row A7.5's
    probe and per-turn prompt already disclose it before execution; this
    makes the caveat travel WITH the frames the model is about to reason
    from. W2.0's pygame promotion deletes the rule and this helper with it.
    Never fatal — a probe that cannot answer leaves the result unannotated
    rather than failing a capture over a note.
    """
    try:
        from canon.engine_ops import TEMPLATE_PHYSICS_NOTE, code_evolved

        return TEMPLATE_PHYSICS_NOTE if code_evolved(pack).get("code_evolved") else None
    except Exception:  # noqa: BLE001 — an unanswerable probe never breaks a capture
        return None


def _budget(tool_input: dict, key: str = "ticks") -> int:
    ticks = int(tool_input.get(key) or DEFAULT_TICKS)
    return max(1, min(ticks, MAX_TICKS))


# ---------------------------------------------------------------------------
# capture_frames
# ---------------------------------------------------------------------------


def capture_frames(pack: Path, tool_input: dict) -> list[dict]:
    """Headless frames of ``level_id``, attached as images and then deleted.

    The capture directory is a temp dir OUTSIDE the pack and is removed
    before this returns, so the pack's file list and hashes are identical
    before and after (the test pins exactly that). At most ``MAX_FRAMES``
    frames are attached, sampled evenly across what was captured, and the
    summary says how many were captured versus attached — never a silent
    truncation.
    """
    level_id = str(tool_input["level_id"])
    ticks = _budget(tool_input)
    every = max(1, int(tool_input.get("every") or DEFAULT_EVERY))
    script = tool_input.get("script") or {}
    env = {
        "PLAT_CAPTURE_TICKS": str(ticks),
        "PLAT_CAPTURE_EVERY": str(every),
        **script_env(script, "capture_frames"),
    }
    out = Path(tempfile.mkdtemp(prefix="canon-capture-"))
    try:
        env["PLAT_CAPTURE"] = str(out)
        proc = run_harness(pack, level_id, env, tool="capture_frames")
        shots = sorted(out.glob("*.png"))
        if not shots:
            # Exit 0 is not evidence: a harness that rendered nothing must be
            # a named failure, not an empty success the model reasons over.
            raise _fail(
                "harness_no_frames",
                "the harness exited cleanly but wrote no frames — the level may have failed to load",
                tool="capture_frames",
                level_id=level_id,
                ticks=ticks,
                stderr=_tail(proc.stderr),
            )
        chosen = _sample(shots, MAX_FRAMES)
        images: list[dict] = []
        refs: list[dict] = []
        for path in chosen:
            data = path.read_bytes()
            images.append(image_block(data, "image/png"))
            refs.append(
                attachment_ref(
                    data,
                    name=path.name,
                    media_type="image/png",
                    path=None,
                    tick=_tick_of(path.name),
                )
            )
        summary = {
            "tool": "capture_frames",
            "level_id": level_id,
            "ticks": ticks,
            "every": every,
            "script": script or None,
            "frames_captured": len(shots),
            "frames_attached": len(chosen),
            "attachments": refs,
            "wrote_to_pack": False,
            "note": (
                "Frames are ephemeral: captured to a temp dir, attached here, deleted. They are not pack files, "
                "so they are referenced by hash + tick and re-attached only by calling this tool again."
            ),
        }
        evolved = _template_physics_note(pack)
        if evolved:
            summary["code_evolved_note"] = evolved
        return blocks_result(summary, images)
    finally:
        shutil.rmtree(out, ignore_errors=True)


def _sample(paths: list[Path], limit: int) -> list[Path]:
    """At most ``limit`` paths, evenly spaced, first and last kept."""
    if len(paths) <= limit:
        return list(paths)
    step = (len(paths) - 1) / (limit - 1)
    return [paths[round(i * step)] for i in range(limit)]


def _tick_of(name: str) -> int | None:
    stem = Path(name).stem
    digits = stem.rpartition("_")[2]
    return int(digits) if digits.isdigit() else None


# ---------------------------------------------------------------------------
# run_trajectory
# ---------------------------------------------------------------------------


def run_trajectory(pack: Path, tool_input: dict) -> dict:
    """A deterministic scripted run of ``level_id``, summarized.

    POSITION ONLY, and that is a limitation worth stating out loud: the traj
    file is the same world-space dump ``main.gd`` emits, so it proves
    movement and pickup parity and structurally CANNOT see a rendering
    divergence — a sprite drawn at the wrong offset, a missing tile, a
    backdrop that never loaded all leave the trajectory byte-identical. A
    question about how the game LOOKS needs ``capture_frames``.

    The raw per-tick file never reaches the model (thousands of lines for a
    short run); this returns the summary and deletes the file.
    """
    level_id = str(tool_input["level_id"])
    inputs = tool_input.get("inputs") or {}
    ticks = _budget(inputs)
    env = {"PLAT_CAPTURE_TICKS": str(ticks), **script_env(inputs, "run_trajectory")}
    out = Path(tempfile.mkdtemp(prefix="canon-traj-"))
    try:
        traj = out / "traj.txt"
        env["PLAT_TRAJ"] = str(traj)
        proc = run_harness(pack, level_id, env, tool="run_trajectory")
        lines = traj.read_text(encoding="utf-8").splitlines() if traj.is_file() else []
        lines = [line for line in lines if line.strip()]
        if not lines:
            raise _fail(
                "harness_no_trajectory",
                "the harness exited cleanly but wrote no trajectory lines — the level may have failed to load",
                tool="run_trajectory",
                level_id=level_id,
                ticks=ticks,
                stderr=_tail(proc.stderr),
            )
        summary = {
            "tool": "run_trajectory",
            "level_id": level_id,
            "ticks": ticks,
            "inputs": inputs or None,
            "wrote_to_pack": False,
            **summarize_trajectory(lines),
            "note": (
                "Positions only (the main.gd traj format). This proves movement and pickups; it cannot see how the "
                "game LOOKS — use capture_frames for a rendering question."
            ),
        }
        evolved = _template_physics_note(pack)
        if evolved:
            summary["code_evolved_note"] = evolved
        return summary
    finally:
        shutil.rmtree(out, ignore_errors=True)


def summarize_trajectory(lines: list[str]) -> dict:
    """``<frame>|P:x:y:vx:coins|<enemy>:x:y:alerted,...`` lines → a compact
    summary: how far the player got, the vertical band it covered, whether it
    ever moved, coins collected, and which enemies alerted.

    A malformed line is counted, never fatal — the harness is the authority
    on its own format and a format drift must show as a number, not a crash.
    """
    first = last = None
    min_x = min_y = float("inf")
    max_x = max_y = float("-inf")
    coins = 0
    alerted: set[str] = set()
    enemies: set[str] = set()
    malformed = 0
    for line in lines:
        parts = line.split("|")
        if len(parts) < 2 or not parts[1].startswith("P:"):
            malformed += 1
            continue
        fields = parts[1].split(":")
        try:
            x, y, vx, got = float(fields[1]), float(fields[2]), float(fields[3]), int(fields[4])
        except (IndexError, ValueError):
            malformed += 1
            continue
        point = {"x": round(x, 3), "y": round(y, 3), "vx": round(vx, 3), "coins": got}
        first = first if first is not None else point
        last = point
        min_x, max_x = min(min_x, x), max(max_x, x)
        min_y, max_y = min(min_y, y), max(max_y, y)
        coins = max(coins, got)
        for token in filter(None, (parts[2] if len(parts) > 2 else "").split(",")):
            bits = token.split(":")
            if len(bits) == 4:
                enemies.add(bits[0])
                if bits[3] == "1":
                    alerted.add(bits[0])
    moved = bool(first and last and (abs(last["x"] - first["x"]) > 0.5 or abs(last["y"] - first["y"]) > 0.5))
    return {
        "frames": len(lines),
        "malformed_lines": malformed,
        "start": first,
        "end": last,
        "x_range": [round(min_x, 3), round(max_x, 3)] if first else None,
        "y_range": [round(min_y, 3), round(max_y, 3)] if first else None,
        "moved": moved,
        "coins": coins,
        "enemies_seen": sorted(enemies),
        "enemies_alerted": sorted(alerted),
    }


# ---------------------------------------------------------------------------
# view_asset
# ---------------------------------------------------------------------------


def resolve_asset(pack: Path, target: str) -> dict:
    """``target`` → ``{"kind", "id", "paths": [pack-relative], "meta": {...}}``.

    Resolution order, data first:

    1. a pack-relative PATH (``review/<stage>/l1_skinned.png``) — the escape
       hatch for anything the vocabularies below do not name, guarded by
       ``tools_read.guard_path`` (never outside the pack, never the CAS);
    2. a ROW kind whose ``EntityKind.asset`` block names the field holding
       its art (``enemy:<id>`` → ``sprite_path``) — stamped registry data,
       so a ``db define``d kind with an asset block resolves without a code
       change;
    3. the manifest-backed art no row owns yet (``tilesheet:<stage>``,
       ``backdrop:<stage>``) and the fixed paths (``player``).

    ``paths`` may be empty — a $0 pack whose sprite phase produced nothing
    leaves ``sprite_path`` blank, which is a legitimate state the tool
    REPORTS (with the verb that would fill it) rather than an error.
    """
    if "/" in target or target.endswith(tuple(MEDIA_TYPES)):
        path = guard_path(pack, target)
        if not path.is_file():
            raise _fail("asset_file_missing", f"no file at {target!r} in this pack", tool="view_asset", target=target)
        return {"kind": "path", "id": target, "paths": [target], "meta": {}}

    kind, _, rest = target.partition(":")
    if kind in _FIXED_TARGETS and not rest:
        rel = _FIXED_TARGETS[kind]
        return {"kind": kind, "id": kind, "paths": [rel] if (pack / rel).is_file() else [], "meta": {}}
    if not rest:
        raise ToolInputError(
            f"view_asset: target {target!r} needs an id (e.g. enemy:<id>); "
            f"known shapes: {_known_targets(pack)}"
        )
    row = _row_asset(pack, kind, rest)
    if row is not None:
        return row
    if kind in _MANIFEST_TARGETS:
        return _manifest_asset(pack, kind, rest)
    raise ToolInputError(f"view_asset: unknown target {target!r}; known shapes: {_known_targets(pack)}")


def _known_targets(pack: Path) -> list[str]:
    shapes = [f"{kind}:<id>" for kind in sorted(_asset_kinds(pack))]
    shapes += [f"{kind}:<id>" for kind in sorted(_MANIFEST_TARGETS)]
    shapes += sorted(_FIXED_TARGETS)
    return [*shapes, "<pack-relative path to an image>"]


def _asset_kinds(pack: Path) -> list[str]:
    """Row kinds whose ``EntityKind`` stamps an ``asset`` block."""
    try:
        from canon.packs import resolve_pack

        spec = resolve_pack(pack).spec
    except Exception:  # noqa: BLE001 — an unresolvable pack simply has no row targets
        return []
    return [kind for kind, entity in spec.entities.items() if getattr(entity, "asset", None)]


def _row_asset(pack: Path, kind: str, row_id: str) -> dict | None:
    """The row's art per its ``EntityKind.asset`` block, or ``None`` when
    this pack has no such kind (the caller tries the next vocabulary)."""
    try:
        from canon.packs import resolve_pack

        spec = resolve_pack(pack).spec
    except Exception:  # noqa: BLE001
        return None
    entity = spec.entities.get(kind)
    asset = getattr(entity, "asset", None) if entity is not None else None
    if not asset:
        return None
    from canon.agent.tools_read import db_row

    row = db_row(pack, {"type": kind, "id": row_id})["row"]
    field = str(asset.get("field") or "")
    hash_field = str(asset.get("hash_field") or "")
    raw = row.get(field) if field else None
    paths = [p for p in ([raw] if isinstance(raw, str) else list(raw or [])) if p]
    meta = {
        "artifact_id": row.get("artifact_id"),
        "name": row.get("name"),
        "review_status": row.get("review_status"),
        "asset_field": field or None,
        "stamped_hash": row.get(hash_field) if hash_field else None,
    }
    return {"kind": kind, "id": row_id, "paths": paths, "meta": meta}


def _manifest_asset(pack: Path, kind: str, stage_id: str) -> dict:
    rel_manifest, field = _MANIFEST_TARGETS[kind]
    manifest_path = pack / rel_manifest.format(id=stage_id)
    if not manifest_path.is_file():
        raise _fail(
            "asset_file_missing",
            f"no {kind} manifest for {stage_id!r} at {rel_manifest.format(id=stage_id)}",
            tool="view_asset",
            target=f"{kind}:{stage_id}",
        )
    document = json.loads(manifest_path.read_text(encoding="utf-8"))
    raw = document.get(field)
    paths = [p for p in ([raw] if isinstance(raw, str) else list(raw or [])) if p]
    meta = {
        "artifact_id": document.get("artifact_id"),
        "review_status": document.get("review_status"),
        "manifest": rel_manifest.format(id=stage_id),
        "asset_field": field,
    }
    return {"kind": kind, "id": stage_id, "paths": paths, "meta": meta}


def view_asset(pack: Path, tool_input: dict) -> list[dict]:
    """An asset's bytes as image attachments plus its metadata (§4.C).

    Reads only — no CAS write, no journal, no regeneration. A target that
    resolves but has no art on disk yet answers with ``images: 0`` and names
    the paid verb that would fill it (doctrine 4: disabled with a reason,
    never hidden); a target whose row CLAIMS a path that is not on disk is a
    named error, because that is drift, not emptiness.

    A target with more than ``MAX_FRAMES`` paths (a wide ``backdrop:<stage>``)
    attaches the first ``MAX_FRAMES`` and SAYS SO — ``images_available``
    beside ``images`` plus a note naming the escape hatch, the way
    ``capture_frames`` reports ``frames_captured`` beside ``frames_attached``.
    Never a silent truncation.
    """
    target = str(tool_input["target"])
    resolved = resolve_asset(pack, target)
    images: list[dict] = []
    refs: list[dict] = []
    for rel in resolved["paths"][:MAX_FRAMES]:
        path = guard_path(pack, rel)
        if not path.is_file():
            raise _fail(
                "asset_file_missing",
                f"{target!r} points at {rel!r}, which is not in this pack (drift: the row and the tree disagree)",
                tool="view_asset",
                target=target,
                path=rel,
            )
        data = path.read_bytes()
        media = media_type_for(rel)
        images.append(image_block(data, media))
        refs.append(attachment_ref(data, name=Path(rel).name, media_type=media, path=rel))
    summary = {
        "tool": "view_asset",
        "target": target,
        "kind": resolved["kind"],
        "id": resolved["id"],
        "paths": list(resolved["paths"]),
        "images": len(images),
        "images_available": len(resolved["paths"]),
        "attachments": refs,
        "metadata": resolved["meta"],
        "wrote_to_pack": False,
    }
    if not resolved["paths"]:
        summary["note"] = (
            "This target exists but has no art on disk yet (its asset field is empty). "
            "generate_asset would create it — a paid action that confirms with an estimate first."
        )
    elif len(resolved["paths"]) > len(images):
        attached = [ref["path"] for ref in refs]
        summary["note"] = (
            f"{len(resolved['paths'])} files here, {len(images)} attached (the cap is {MAX_FRAMES}): "
            f"{attached}. Ask for any of the rest by naming its pack-relative path as the target."
        )
    return blocks_result(summary, images)


# ---------------------------------------------------------------------------
# Schemas + registration
# ---------------------------------------------------------------------------

_LEVEL_ID = {"type": "string", "description": "Level id as describe_pack lists it, e.g. 'l1'."}

_SCRIPT = {
    "type": "object",
    "description": "Scripted input for the headless run; omit for a no-input session.",
    "properties": {
        "hold": {
            "type": "string",
            "enum": list(HOLD_MODES),
            "description": "A held direction for the whole run.",
        },
        "jump_every": {
            "type": "integer",
            "minimum": 0,
            "description": "Jump every N ticks while holding (0 = never).",
        },
        "actions": {
            "type": "array",
            "description": "Single-frame inputs the hold vocabulary cannot express (pipe entry, door entry).",
            "items": {
                "type": "object",
                "properties": {
                    "frame": {"type": "integer", "minimum": 0},
                    "action": {"type": "string", "enum": list(ACTION_KINDS)},
                },
                "required": ["frame", "action"],
                "additionalProperties": False,
            },
        },
    },
    "additionalProperties": False,
}

_TRAJ_INPUTS = {
    **_SCRIPT,
    "description": "Scripted input plus this run's tick budget.",
    "properties": {
        **_SCRIPT["properties"],
        "ticks": {"type": "integer", "minimum": 1, "maximum": MAX_TICKS, "description": f"Default {DEFAULT_TICKS}."},
    },
}

CAPTURE_SCHEMA = {
    "type": "object",
    "properties": {
        "level_id": _LEVEL_ID,
        "ticks": {
            "type": "integer",
            "minimum": 1,
            "maximum": MAX_TICKS,
            "description": f"Fixed tick budget for the session (default {DEFAULT_TICKS}).",
        },
        "every": {
            "type": "integer",
            "minimum": 1,
            "description": f"Save a frame every N ticks (default {DEFAULT_EVERY}).",
        },
        "script": _SCRIPT,
    },
    "required": ["level_id"],
    "additionalProperties": False,
}

TRAJECTORY_SCHEMA = {
    "type": "object",
    "properties": {"level_id": _LEVEL_ID, "inputs": _TRAJ_INPUTS},
    "required": ["level_id"],
    "additionalProperties": False,
}

VIEW_ASSET_SCHEMA = {
    "type": "object",
    "properties": {
        "target": {
            "type": "string",
            "description": (
                "enemy:<id> | item:<id> | player | tilesheet:<stage> | backdrop:<stage>, or a pack-relative "
                "path to an image (e.g. review/<stage>/l1_skinned.png)."
            ),
        }
    },
    "required": ["target"],
    "additionalProperties": False,
}

#: name → (description the model sees, schema, body, touches)
_TOOLS: dict[str, tuple[str, dict, Callable[[Path, dict], Any], str]] = {
    "capture_frames": (
        "Look at the level running: play it headless for a fixed tick budget and attach the frames as images. "
        "Windowless, deterministic, writes nothing into the pack. Use this for any question about how the game "
        "LOOKS — a trajectory cannot see rendering. Optional 'script' drives input (hold a direction, jump every "
        "N ticks); with none the player stands still.",
        CAPTURE_SCHEMA,
        capture_frames,
        "spawns the pygame harness headless; captures to a temp dir and deletes it; writes nothing into the pack",
    ),
    "run_trajectory": (
        "Play the level headless under scripted input and get a summary of where the player actually went: "
        "start/end position, x and y range, whether it moved, coins collected, which enemies alerted. POSITIONS "
        "ONLY — it proves movement and pickups and cannot see a rendering problem. Writes nothing into the pack.",
        TRAJECTORY_SCHEMA,
        run_trajectory,
        "spawns the pygame harness headless; the trajectory file is temporary and deleted",
    ),
    "view_asset": (
        "Look at an asset: its image bytes are attached and its metadata (paths, stamped hash, review status) "
        "returned. Reads only — it never regenerates or assigns anything.",
        VIEW_ASSET_SCHEMA,
        view_asset,
        "reads sprite / tilesheet / backdrop bytes",
    ),
}


def _bind(name: str, schema: dict, body: Callable[[Path, dict], Any], pack: Path) -> Callable[[dict], Any]:
    def run(tool_input: dict) -> Any:
        validate_input(name, schema, tool_input)
        result = body(pack, tool_input)
        # A block list rides back as tool_result CONTENT (text + images);
        # anything else is compact JSON, exactly like every other read tool.
        return result if isinstance(result, list) else compact(result)

    run.__name__ = name
    return run


def vision_tool_specs() -> list[ToolSpec]:
    """The specs alone (what the eval corpus and the panel's tool list show)."""
    return [ToolSpec(name=name, description=desc, input_schema=schema) for name, (desc, schema, _, _) in _TOOLS.items()]


def register_vision_tools(registry: ToolRegistry, pack_dir: str | Path) -> list[str]:
    """Register the three vision tools for ``pack_dir`` (tier ``"auto"``,
    ``VISION_TOOL_NAMES`` order) and return the names.

    The two headless tools also register a per-call tier resolver on the
    registry's permission engine, so ASSUMPTION-6a's demote is a line in
    ``.canon/registry.json`` rather than an edit here. Nothing is read at
    registration — a stub pack registers fine and every tool re-probes when
    it runs.
    """
    pack = Path(pack_dir)
    engine = registry.permissions
    names: list[str] = []
    for name in VISION_TOOL_NAMES:
        description, schema, body, touches = _TOOLS[name]
        spec = ToolSpec(name=name, description=description, input_schema=schema)
        registry.register(Tool(spec=spec, tier=VISION_TIER, run=_bind(name, schema, body, pack), touches=touches))
        if name in HEADLESS_TOOL_NAMES and hasattr(engine, "tier_with"):
            engine.tier_with(name, tier_resolver(pack, name))
        if hasattr(engine, "describe"):
            engine.describe(name, _describe(name))
        names.append(name)
    return names


def _describe(name: str) -> Callable[[dict], str]:
    def describe(tool_input: dict) -> str:
        if name == "view_asset":
            return f"view {tool_input.get('target')}"
        return f"{name.replace('_', ' ')} on {tool_input.get('level_id')}"

    return describe


__all__ = [
    "ACTION_KINDS",
    "AGENT_SETTINGS_FILE",
    "AGENT_TIER_PATH",
    "CAPTURE_SCHEMA",
    "DEFAULT_EVERY",
    "DEFAULT_TICKS",
    "HARNESS_ENGINE_ID",
    "HARNESS_MODULE",
    "HARNESS_TIMEOUT_S",
    "HEADLESS_TOOL_NAMES",
    "HOLD_MODES",
    "MAX_FRAMES",
    "MAX_TICKS",
    "MEDIA_TYPES",
    "REGISTRY_TIER_PATH",
    "TRAJECTORY_SCHEMA",
    "VIEW_ASSET_SCHEMA",
    "VISION_TIER",
    "VISION_TOOL_NAMES",
    "HeadlessError",
    "attachment_ref",
    "attachment_refs",
    "blocks_result",
    "capture_frames",
    "harness_launch",
    "image_block",
    "media_type_for",
    "register_vision_tools",
    "registry_tier",
    "resolve_asset",
    "run_harness",
    "run_trajectory",
    "script_env",
    "scrubbed_env",
    "sha256_of",
    "summarize_trajectory",
    "tier_resolver",
    "view_asset",
    "vision_tool_specs",
]

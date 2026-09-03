"""The roster — specialists as DATA (Phase 1 §5.2; row P1-A4.5).

A specialist is a configuration of the one loop: ``(role prompt, tool
allowlist, model tier, actor name)``. Each lives here as a JSON file plus a
markdown role prompt beside it (user decision 2026-09-01)::

    roster/
      core.md               the law every specialist shares (§3.1 layer 1)
      foreman.json + .md    the conversation
      level_designer.json + .md
      artist.json + .md
      writer.json + .md
      playtester.json + .md
      game_coder.json + .md

``<id>.json``::

    {"id": "level_designer", "label": "Level designer", "actor": "level_designer",
     "tools": ["describe_level", …],          # registry names; ∩ registry at use
     "model_tier": "cheap" | "mid" | "top",   # resolved through the pack's models.json
     "model": "…"}                             # or an explicit id (wins over the tier)

Adding a specialist is adding a json+md pair — ``load_roster`` globs the
directory, nothing enumerates ids in code (``tests/test_agent_runs.py``
proves a new pair appears without a code change). ``tools`` is an
allowlist of registry NAMES; names the registry does not know (tools whose
row has not landed — capture at A7, art at A6, code at A7.5) are dropped
at intersection time and reported loudly, never silently — see
``RunManager.subset``.

Model tiers reuse the ``models.json`` agent-tiers precedent verbatim: a
pack-local ``models.json`` wins, else the template's (``PackSpec.data_files
["models"]`` under its ``template_dir``); ``resolve_model`` answers the tier's
model id or ``None`` (no table, unknown tier) — the conversation's model
then serves.

Deliberately absent: routing quality (prompt + eval on the foreman, A7),
``mesh_smith`` (joins as config at W2.2), any per-actor permission (grants
govern tool names — §5.4).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: This package directory — where the shipped roster lives.
ROSTER_DIR = Path(__file__).parent

#: The shared law (§3.1 layer 1), read by ``canon.agent.prompt.assemble``.
CORE_FILE = ROSTER_DIR / "core.md"

#: The specialist every conversation starts as.
FOREMAN_ID = "foreman"

#: The tiers ``models.json`` names (data; a roster file may name any key the table has).
MODEL_TIERS: tuple[str, ...] = ("cheap", "mid", "top")


class RosterError(ValueError):
    """A roster file is malformed (missing id/tools/prompt, a json without its md, …)."""


@dataclass(frozen=True)
class Specialist:
    """One roster entry.

    Attributes:
        id: The specialist id (the ``<id>`` of its files; snake_case).
        label: Human label for cards and chips.
        actor: The ``<specialist>`` half of ``agent:<conversation>/<specialist>``
            (defaults to ``id``).
        tools: The tool allowlist — registry names, in file order.
        model_tier: A ``models.json`` tier, or ``None``.
        model: An explicit model id (wins over the tier), or ``None``.
        role_prompt: The markdown role prompt (§3.1 layer 4).
        path: Where the json lives (for the inspectable prompt view).
        extra: Any further keys the file carried (data rides along).
    """

    id: str
    label: str
    actor: str
    tools: tuple[str, ...]
    model_tier: str | None
    model: str | None
    role_prompt: str
    path: str
    extra: dict[str, Any] = field(default_factory=dict)

    def is_foreman(self) -> bool:
        return self.id == FOREMAN_ID


_KNOWN_KEYS = {"id", "label", "actor", "tools", "model_tier", "model"}


def load_specialist(json_path: str | Path) -> Specialist:
    """Read one ``<id>.json`` + its sibling ``<id>.md``. ``RosterError`` on
    a missing/invalid field or a missing prompt file."""
    path = Path(json_path)
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RosterError(f"{path}: cannot read roster json: {exc}") from exc
    if not isinstance(document, dict):
        raise RosterError(f"{path}: roster json must be an object")
    specialist_id = document.get("id")
    if not isinstance(specialist_id, str) or not specialist_id:
        raise RosterError(f"{path}: 'id' must be a non-empty string")
    if specialist_id != path.stem:
        raise RosterError(f"{path}: 'id' {specialist_id!r} must match the file name {path.stem!r}")
    if any(ch.isspace() or ch == "/" for ch in specialist_id):
        raise RosterError(f"{path}: 'id' may not contain whitespace or '/' (it becomes the actor)")
    tools = document.get("tools")
    if not isinstance(tools, list) or not all(isinstance(t, str) and t for t in tools):
        raise RosterError(f"{path}: 'tools' must be a list of tool names")
    tier = document.get("model_tier")
    if tier is not None and not isinstance(tier, str):
        raise RosterError(f"{path}: 'model_tier' must be a string when given")
    model = document.get("model")
    if model is not None and not isinstance(model, str):
        raise RosterError(f"{path}: 'model' must be a string when given")
    prompt_path = path.with_suffix(".md")
    if not prompt_path.is_file():
        raise RosterError(f"{path}: no role prompt beside it (expected {prompt_path.name})")
    actor = document.get("actor") or specialist_id
    if not isinstance(actor, str) or any(ch.isspace() or ch == "/" for ch in actor):
        raise RosterError(f"{path}: 'actor' must be a snake_case string")
    return Specialist(
        id=specialist_id,
        label=str(document.get("label") or specialist_id.replace("_", " ").capitalize()),
        actor=actor,
        tools=tuple(dict.fromkeys(tools)),
        model_tier=tier,
        model=model,
        role_prompt=prompt_path.read_text(encoding="utf-8"),
        path=str(path),
        extra={k: v for k, v in document.items() if k not in _KNOWN_KEYS},
    )


def load_roster(roster_dir: str | Path | None = None) -> dict[str, Specialist]:
    """Every ``<id>.json`` (+ ``<id>.md``) under ``roster_dir`` (default: the
    shipped roster), keyed by id, in file order. ``core.md`` is not a
    specialist and is skipped; a json without its md is a ``RosterError``."""
    root = Path(roster_dir) if roster_dir is not None else ROSTER_DIR
    out: dict[str, Specialist] = {}
    for path in sorted(root.glob("*.json")):
        specialist = load_specialist(path)
        out[specialist.id] = specialist
    return out


def core_law(roster_dir: str | Path | None = None) -> str:
    """The shared law — ``core.md`` (§3.1 layer 1)."""
    root = Path(roster_dir) if roster_dir is not None else ROSTER_DIR
    return (root / CORE_FILE.name).read_text(encoding="utf-8")


def models_path(pack_dir: str | Path) -> Path | None:
    """The ``models.json`` a pack's tiers resolve through: pack-local first,
    else the template's (``PackSpec.data_files["models"]`` under
    ``template_dir``); ``None`` when neither exists."""
    pack = Path(pack_dir)
    local = pack / "models.json"
    if local.is_file():
        return local
    try:
        from canon.packs import resolve_pack

        spec = resolve_pack(pack).spec
    except Exception:  # noqa: BLE001 — an unresolvable pack has no table; the conversation's model serves
        return None
    name = (getattr(spec, "data_files", None) or {}).get("models")
    template_dir = getattr(spec, "template_dir", None)
    if not name or template_dir is None:
        return None
    candidate = Path(template_dir) / name
    return candidate if candidate.is_file() else None


def resolve_model(pack_dir: str | Path, specialist: Specialist) -> str | None:
    """The model a specialist runs on for this pack: its explicit ``model``,
    else its ``model_tier`` through ``models_path``'s table, else ``None``
    (the conversation's model). Unknown tiers answer ``None`` — never a
    guess."""
    if specialist.model:
        return specialist.model
    if not specialist.model_tier:
        return None
    path = models_path(pack_dir)
    if path is None:
        return None
    try:
        tiers = json.loads(path.read_text(encoding="utf-8")).get("model_tiers") or {}
    except (OSError, ValueError):
        return None
    model = tiers.get(specialist.model_tier)
    return str(model) if isinstance(model, str) and model else None


__all__ = [
    "CORE_FILE",
    "FOREMAN_ID",
    "MODEL_TIERS",
    "ROSTER_DIR",
    "RosterError",
    "Specialist",
    "core_law",
    "load_roster",
    "load_specialist",
    "models_path",
    "resolve_model",
]

"""Skills and recipes — one loader, two kinds, template-integrity precedence (master §3.0-F; row P1-A4.5).

The user-buildable layer of the agent (Phase 1 §5.3). Two kinds of file,
one loader:

**Instruction skills** — markdown with a leading JSON front-matter block
(user decision 2026-09-01: JSON, no YAML dependency)::

    {"id": "headroom", "specialist": "level_designer",
     "allowlist": ["describe_level", "apply_level_edit"],
     "model": "claude-sonnet-5", "trigger": "headroom platforms clearance"}
    Our levels always leave 2 tiles of headroom above platforms …

The body is the instruction text appended to the acting specialist's
prompt (§3.1 layer 4) when the trigger matches the task (*augment*), and a
skill with its own allowlist is also *routable* — it appears in the
foreman's delegation menu as a lightweight specialist under its host.
``id`` defaults to the file stem; ``specialist`` is the host (``None`` =
any); ``allowlist`` ⊆ the host's — it is INTERSECTED at use, never
widening (``intersect``); ``model`` is a preference; ``trigger`` is the
one-line match text.

**Recipes** — JSON files (canon-authored, parameterized; the second kind
from day one so W2.2's ``mesh_smith`` bpy recipes are configuration)::

    {"id": "smooth_normals", "family": "bpy",
     "parameters": {"angle": {"type": "number", "min": 0, "max": 180}},
     "gates": {"max_tris": 20000},
     "script_template": "…"}

``validate_recipe`` is fail-closed: a bad id/family, a parameter with an
unknown type, ``min > max``, an empty ``choices``, a non-object ``gates``
or a missing ``script_template`` refuses the file (``RecipeError``). None
ship until W2.2 — the loader, validation and precedence exist now so a
recipe file is data on the day it lands.

**Precedence** (ratified, master Q5): project-local ``<pack>/.canon/agent/
skills/`` wins over the project store (``~/CradleProjects/.cradle/skills/``;
root overridable via ``CRADLE_PROJECT_STORE`` — P0-10 formalizes the
store) over the template's (none in Phase 1 — the slot exists). A project
may override its own copies; its expansions never flow back.

**Safety invariants** (tested in ``tests/test_agent_runs.py``): a skill
never widens permissions (allowlists intersect; tiers still apply — the
registry's tier is the tier); recipe bound/gate widening is never
Always-allow-eligible (``PermissionEngine.forbid_always`` for the recipe
family); no tool writes skill or recipe files — this module only reads,
and the grep test holds the line.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: The env var naming the project-store ROOT (``~/CradleProjects``).
STORE_ENV = "CRADLE_PROJECT_STORE"

#: The default project-store root.
DEFAULT_STORE = Path.home() / "CradleProjects"

#: Skills under the store root and under a pack.
STORE_SKILLS = Path(".cradle") / "skills"
PACK_SKILLS = Path(".canon") / "agent" / "skills"

#: Precedence, lowest first (later layers override by id).
SOURCES: tuple[str, ...] = ("template", "store", "project")

#: Recipe parameter types (data; an unknown type refuses the recipe).
PARAMETER_TYPES: tuple[str, ...] = ("integer", "number", "string", "boolean", "enum")

#: The recipe family every recipe belongs to is data; this is the reason
#: the permission engine gives for refusing "always" on recipe tools.
RECIPE_NEVER_ALWAYS = (
    "recipe bound/gate widening is never Always-allowable — it confirms per instance, like paid (master §3.0-F)"
)


class SkillError(ValueError):
    """A skill file is malformed; ``str(exc)`` names the file and the field."""


class RecipeError(ValueError):
    """A recipe file failed fail-closed validation; ``str(exc)`` names why."""


@dataclass(frozen=True)
class Skill:
    """One instruction skill (see the module docstring)."""

    id: str
    specialist: str | None
    allowlist: tuple[str, ...] | None
    model: str | None
    trigger: str
    body: str
    source: str
    path: str

    @property
    def routable(self) -> bool:
        """A skill with its own allowlist can be delegated to as a lightweight specialist."""
        return self.allowlist is not None


@dataclass(frozen=True)
class Recipe:
    """One validated recipe (see the module docstring)."""

    id: str
    family: str
    parameters: dict[str, dict[str, Any]]
    gates: dict[str, Any]
    script_template: str
    source: str
    path: str


@dataclass
class SkillSet:
    """What ``load_skills`` answers: skills and recipes by id after
    precedence, plus ``problems`` — every file refused, with its reason
    (disabled with a reason, never hidden)."""

    skills: dict[str, Skill] = field(default_factory=dict)
    recipes: dict[str, Recipe] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)

    def for_specialist(self, specialist: str) -> list[Skill]:
        """Skills hosted by ``specialist`` (or by any)."""
        return [s for s in self.skills.values() if s.specialist in (None, specialist)]

    def matched(self, specialist: str, task: str | None) -> list[Skill]:
        """The augment set for one run: hosted skills whose trigger matches ``task``."""
        return [s for s in self.for_specialist(specialist) if matches(s, task)]

    def routable(self) -> list[Skill]:
        """Skills the foreman may delegate to by id."""
        return [s for s in self.skills.values() if s.routable]


# ---------------------------------------------------------------------------
# Locations
# ---------------------------------------------------------------------------


def project_store_dir() -> Path:
    """The project-store root: ``$CRADLE_PROJECT_STORE`` or ``~/CradleProjects``."""
    raw = os.environ.get(STORE_ENV, "").strip()
    return Path(raw).expanduser() if raw else DEFAULT_STORE


def skill_dirs(pack_dir: str | Path, store_dir: str | Path | None = None, template_dir: str | Path | None = None):
    """``[(source, dir)]`` lowest precedence first."""
    store = Path(store_dir) if store_dir is not None else project_store_dir()
    out: list[tuple[str, Path]] = []
    if template_dir is not None:
        out.append(("template", Path(template_dir)))
    out.append(("store", store / STORE_SKILLS))
    out.append(("project", Path(pack_dir) / PACK_SKILLS))
    return out


# ---------------------------------------------------------------------------
# Parsing + validation
# ---------------------------------------------------------------------------


def parse_front_matter(text: str) -> tuple[dict[str, Any], str]:
    """Split a skill file into its leading JSON object and the body after
    it. The file must START with ``{`` (whitespace allowed); the object may
    span lines; the body is everything after it, left-stripped of one
    blank line. ``SkillError`` when there is no object or it is not JSON."""
    stripped = text.lstrip()
    if not stripped.startswith("{"):
        raise SkillError("a skill file starts with a JSON front-matter object ({...}) before the instruction text")
    try:
        document, end = json.JSONDecoder().raw_decode(stripped)
    except ValueError as exc:
        raise SkillError(f"front-matter is not valid JSON: {exc}") from exc
    if not isinstance(document, dict):
        raise SkillError("front-matter must be a JSON object")
    body = stripped[end:]
    if body.startswith("\r\n"):
        body = body[2:]
    elif body.startswith("\n"):
        body = body[1:]
    return document, body.strip("\n")


def _names(value: Any, path: Path, key: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
        raise SkillError(f"{path}: '{key}' must be a list of tool names")
    return tuple(dict.fromkeys(value))


def load_skill(path: str | Path, source: str) -> Skill:
    """Read one ``*.md`` skill; ``SkillError`` on a malformed file."""
    file = Path(path)
    try:
        text = file.read_text(encoding="utf-8")
    except OSError as exc:
        raise SkillError(f"{file}: cannot read: {exc}") from exc
    try:
        meta, body = parse_front_matter(text)
    except SkillError as exc:
        raise SkillError(f"{file}: {exc}") from exc
    skill_id = meta.get("id", file.stem)
    if not isinstance(skill_id, str) or not skill_id or any(ch.isspace() or ch == "/" for ch in skill_id):
        raise SkillError(f"{file}: 'id' must be a non-empty token without whitespace or '/'")
    specialist = meta.get("specialist")
    if specialist is not None and (not isinstance(specialist, str) or not specialist):
        raise SkillError(f"{file}: 'specialist' must be a specialist id when given")
    allowlist = _names(meta["allowlist"], file, "allowlist") if "allowlist" in meta else None
    model = meta.get("model")
    if model is not None and (not isinstance(model, str) or not model):
        raise SkillError(f"{file}: 'model' must be a model id when given")
    trigger = meta.get("trigger", "")
    if not isinstance(trigger, str):
        raise SkillError(f"{file}: 'trigger' must be a string")
    if not body.strip():
        raise SkillError(f"{file}: a skill needs instruction text after the front-matter")
    return Skill(
        id=skill_id,
        specialist=specialist,
        allowlist=allowlist,
        model=model,
        trigger=trigger.strip(),
        body=body,
        source=source,
        path=str(file),
    )


def _number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def validate_recipe(document: Any) -> dict[str, Any]:
    """Fail-closed validation of a recipe document; returns the normalized
    dict (``parameters`` / ``gates`` always present). ``RecipeError`` names
    the first problem."""
    if not isinstance(document, dict):
        raise RecipeError("a recipe is a JSON object")
    recipe_id = document.get("id")
    if not isinstance(recipe_id, str) or not recipe_id or any(ch.isspace() or ch == "/" for ch in recipe_id):
        raise RecipeError("'id' must be a non-empty token without whitespace or '/'")
    family = document.get("family")
    if not isinstance(family, str) or not family:
        raise RecipeError(f"{recipe_id}: 'family' must be a non-empty string (e.g. 'bpy')")
    parameters = document.get("parameters", {})
    if not isinstance(parameters, dict):
        raise RecipeError(f"{recipe_id}: 'parameters' must be an object of name → {{type, min, max, choices}}")
    normalized: dict[str, dict[str, Any]] = {}
    for name, spec in parameters.items():
        if not isinstance(name, str) or not name:
            raise RecipeError(f"{recipe_id}: parameter names must be non-empty strings")
        if not isinstance(spec, dict):
            raise RecipeError(f"{recipe_id}: parameter {name!r} must be an object")
        kind = spec.get("type")
        if kind not in PARAMETER_TYPES:
            raise RecipeError(f"{recipe_id}: parameter {name!r} type {kind!r} is not one of {list(PARAMETER_TYPES)}")
        entry: dict[str, Any] = {"type": kind}
        if kind in ("integer", "number"):
            low, high = spec.get("min"), spec.get("max")
            if low is None or high is None or not _number(low) or not _number(high):
                raise RecipeError(f"{recipe_id}: numeric parameter {name!r} needs numeric 'min' and 'max' bounds")
            if low > high:
                raise RecipeError(f"{recipe_id}: parameter {name!r} has min {low} > max {high}")
            entry["min"], entry["max"] = low, high
        elif kind == "enum":
            choices = spec.get("choices")
            valid = isinstance(choices, list) and bool(choices)
            valid = valid and all(isinstance(c, str | int | float) for c in choices)
            if not valid:
                raise RecipeError(f"{recipe_id}: enum parameter {name!r} needs a non-empty 'choices' list")
            entry["choices"] = list(choices)
        elif kind == "string":
            max_length = spec.get("max_length")
            if max_length is not None and (not isinstance(max_length, int) or max_length <= 0):
                raise RecipeError(f"{recipe_id}: string parameter {name!r} 'max_length' must be a positive integer")
            if max_length is not None:
                entry["max_length"] = max_length
        if "default" in spec:
            entry["default"] = spec["default"]
        normalized[name] = entry
    gates = document.get("gates", {})
    if not isinstance(gates, dict):
        raise RecipeError(f"{recipe_id}: 'gates' must be an object")
    script = document.get("script_template")
    if not isinstance(script, str) or not script.strip():
        raise RecipeError(f"{recipe_id}: 'script_template' must be a non-empty string")
    return {
        "id": recipe_id,
        "family": family,
        "parameters": normalized,
        "gates": dict(gates),
        "script_template": script,
    }


def load_recipe(path: str | Path, source: str) -> Recipe:
    """Read + validate one ``*.json`` recipe; ``RecipeError`` refuses it."""
    file = Path(path)
    try:
        document = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RecipeError(f"{file}: cannot read recipe json: {exc}") from exc
    try:
        normalized = validate_recipe(document)
    except RecipeError as exc:
        raise RecipeError(f"{file}: {exc}") from exc
    return Recipe(source=source, path=str(file), **normalized)


# ---------------------------------------------------------------------------
# The loader
# ---------------------------------------------------------------------------


def load_skills(
    pack_dir: str | Path,
    project_store_dir: str | Path | None = None,
    *,
    template_dir: str | Path | None = None,
) -> SkillSet:
    """Load every skill (``*.md``) and recipe (``*.json``) from the
    template's dir (none in Phase 1 unless given), the project store and
    the pack — in that order, so a later layer overrides an earlier one by
    id (project-local wins). Files that fail validation are refused and
    listed in ``problems``; nothing is written."""
    out = SkillSet()
    for source, directory in skill_dirs(pack_dir, project_store_dir, template_dir):
        if not directory.is_dir():
            continue
        for path in sorted(directory.iterdir()):
            if path.suffix == ".md" and path.is_file():
                try:
                    skill = load_skill(path, source)
                except SkillError as exc:
                    out.problems.append(str(exc))
                    continue
                out.skills[skill.id] = skill
            elif path.suffix == ".json" and path.is_file():
                try:
                    recipe = load_recipe(path, source)
                except RecipeError as exc:
                    out.problems.append(str(exc))
                    continue
                out.recipes[recipe.id] = recipe
    return out


# ---------------------------------------------------------------------------
# Use
# ---------------------------------------------------------------------------


def intersect(
    allowlist: tuple[str, ...] | list[str] | None, host_tools: tuple[str, ...] | list[str]
) -> tuple[list[str], list[str]]:
    """``(kept, dropped)``: the allowlist ∩ the host's tools, in host order —
    a skill never widens; ``None`` keeps the host's list whole."""
    host = list(dict.fromkeys(host_tools))
    if allowlist is None:
        return host, []
    wanted = set(allowlist)
    kept = [name for name in host if name in wanted]
    dropped = [name for name in dict.fromkeys(allowlist) if name not in set(host)]
    return kept, dropped


_TOKEN = re.compile(r"[a-z0-9_]{3,}")


def matches(skill: Skill, task: str | None) -> bool:
    """Does the skill's trigger match ``task``? Any trigger token (≥ 3
    chars, case-insensitive) appearing in the task matches; a skill with
    no trigger never auto-matches (it may still be routed to by id)."""
    if not task or not skill.trigger:
        return False
    words = set(_TOKEN.findall(task.lower()))
    return any(token in words for token in _TOKEN.findall(skill.trigger.lower()))


__all__ = [
    "DEFAULT_STORE",
    "PACK_SKILLS",
    "PARAMETER_TYPES",
    "RECIPE_NEVER_ALWAYS",
    "SOURCES",
    "STORE_ENV",
    "STORE_SKILLS",
    "Recipe",
    "RecipeError",
    "Skill",
    "SkillError",
    "SkillSet",
    "intersect",
    "load_recipe",
    "load_skill",
    "load_skills",
    "matches",
    "parse_front_matter",
    "project_store_dir",
    "skill_dirs",
    "validate_recipe",
]

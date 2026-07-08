"""JSON ↔ SkeletonSpec loader (PRD §4.1) — makes schemas user-editable files.

The format is **Canon field-spec JSON**, not JSON Schema draft-07: a direct
serialization of a :class:`SkeletonSpec`. Field order in the JSON object is
significant (it is the roll order, and ``depends_on`` must reference earlier
fields) — ``json.load`` preserves it.

File shape::

    {
      "schema_version": "1",
      "entity_type": "enemy",
      "fields": {
        "archetype":  {"choices": [["walker", 3], ["flyer", 1]]},
        "hp":         {"range": [8, 40]},
        "damage_die": {"lookup": [["walker", "d6"], ["flyer", "d4"]],
                        "depends_on": "archetype"}
      }
    }

Encoding rules (chosen so load(dump(spec)) rolls identically):

- ``choices`` and ``lookup`` are **pair lists**, not JSON objects. JSON
  object keys are always strings, which would silently corrupt int lookup
  keys (``lookup={1: "1d4"}``, keyed off ``room_level``) — pair lists keep
  key types intact.
- Open-for-LLM fields do not appear here at all: a SkeletonSpec only holds
  *rolled* fields; open fields are prompt-side (matching how packs work
  today, e.g. NPC_SPEC).
- ``schema_version`` is carried through load/dump verbatim. Nothing acts on
  it in v1 — stale-marking on version change (Option D-i) is Phase 2 wiring.

Specs using the ``post`` callback escape hatch cannot round-trip (callables
aren't data); ``dump_skeleton_spec`` refuses them loudly.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from canon.skeleton.core import SkeletonField, SkeletonSpec

#: Version written by dump_skeleton_spec and accepted by load_skeleton_spec.
SCHEMA_FORMAT_VERSION = "1"

_FIELD_KEYS = {
    "choices", "range", "lookup", "depends_on", "depends_on_context",
    "lookup_ranges",
}


class SchemaLoadError(ValueError):
    """A schema file failed to load. The message names the source and the
    offending field."""


# ---------------------------------------------------------------------------
# Load
# ---------------------------------------------------------------------------


def load_skeleton_spec(source: str | Path | dict) -> SkeletonSpec:
    """Load a Canon field-spec JSON document into a SkeletonSpec.

    ``source`` may be a path to a ``.json`` file or an already-parsed dict.
    Validation is SkeletonSpec's own (mode exclusivity, dependency order) and
    runs at load time — long before any generation. Errors name the source
    file and field.
    """
    if isinstance(source, (str, Path)):
        path = Path(source)
        label = str(path)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise SchemaLoadError(f"{label}: schema file not found.") from None
        except json.JSONDecodeError as exc:
            raise SchemaLoadError(f"{label}: invalid JSON — {exc}") from exc
    else:
        label = "<dict>"
        data = source

    if not isinstance(data, dict):
        raise SchemaLoadError(f"{label}: expected a JSON object at top level.")

    entity_type = data.get("entity_type")
    if not entity_type or not isinstance(entity_type, str):
        raise SchemaLoadError(f"{label}: missing or non-string 'entity_type'.")

    raw_fields = data.get("fields")
    if not isinstance(raw_fields, dict) or not raw_fields:
        raise SchemaLoadError(f"{label}: 'fields' must be a non-empty object.")

    fields: dict[str, SkeletonField] = {}
    for name, raw in raw_fields.items():
        fields[name] = _load_field(label, name, raw)

    try:
        spec = SkeletonSpec(entity_type=entity_type, fields=fields)
    except ValidationError as exc:
        # Spec-level failures are dependency-order errors; the pydantic
        # message already names the field.
        raise SchemaLoadError(f"{label}: {_first_error(exc)}") from exc

    version = data.get("schema_version", SCHEMA_FORMAT_VERSION)
    # Carried, not acted on (v1). Stashed on the spec via model attribute
    # would require a core change; loaders that need it read it from the
    # file. dump_skeleton_spec re-emits it.
    spec.__dict__["_schema_version"] = str(version)
    return spec


def _load_field(label: str, name: str, raw: Any) -> SkeletonField:
    where = f"{label}: field {name!r}"
    if not isinstance(raw, dict):
        raise SchemaLoadError(f"{where}: expected an object, got {type(raw).__name__}.")

    unknown = set(raw) - _FIELD_KEYS
    if unknown:
        raise SchemaLoadError(
            f"{where}: unknown key(s) {sorted(unknown)!r}. "
            f"Allowed: {sorted(_FIELD_KEYS)!r}."
        )

    kwargs: dict[str, Any] = {}
    if "choices" in raw:
        kwargs["choices"] = _pairs(where, "choices", raw["choices"])
    if "range" in raw:
        rng = raw["range"]
        if not isinstance(rng, list) or len(rng) != 2:
            raise SchemaLoadError(f"{where}: 'range' must be a two-element array.")
        kwargs["range"] = (rng[0], rng[1])
    if "lookup" in raw:
        kwargs["lookup"] = dict(_pairs(where, "lookup", raw["lookup"]))
    if "depends_on" in raw:
        kwargs["depends_on"] = raw["depends_on"]
    if "depends_on_context" in raw:
        kwargs["depends_on_context"] = raw["depends_on_context"]
    if "lookup_ranges" in raw:
        if not isinstance(raw["lookup_ranges"], bool):
            raise SchemaLoadError(f"{where}: 'lookup_ranges' must be a bool.")
        kwargs["lookup_ranges"] = raw["lookup_ranges"]

    try:
        return SkeletonField(**kwargs)
    except ValidationError as exc:
        raise SchemaLoadError(f"{where}: {_first_error(exc)}") from exc


def _pairs(where: str, key: str, raw: Any) -> list[tuple[Any, Any]]:
    """Decode a pair-list ([[k, v], ...]). Rejects JSON objects for lookup —
    object keys are strings and would corrupt int keys."""
    if not isinstance(raw, list):
        raise SchemaLoadError(
            f"{where}: '{key}' must be a list of [key, value] pairs "
            f"(not a JSON object — object keys lose their types)."
        )
    out: list[tuple[Any, Any]] = []
    for i, item in enumerate(raw):
        if not isinstance(item, list) or len(item) != 2:
            raise SchemaLoadError(
                f"{where}: '{key}' entry {i} must be a two-element array; got {item!r}."
            )
        out.append((item[0], item[1]))
    return out


def _first_error(exc: ValidationError) -> str:
    err = exc.errors()[0]
    return str(err.get("msg", exc))


# ---------------------------------------------------------------------------
# Dump
# ---------------------------------------------------------------------------


def dump_skeleton_spec(spec: SkeletonSpec) -> dict:
    """Serialize a SkeletonSpec to the Canon field-spec dict.

    Raises ValueError for specs using the ``post`` callback — callables are
    not data and cannot round-trip; express the derivation declaratively or
    keep that spec Python-side.
    """
    if spec.post is not None:
        raise ValueError(
            f"SkeletonSpec {spec.entity_type!r} has a `post` callback; "
            "callbacks cannot be serialized to schema JSON. Drop the callback "
            "or keep this spec as a Python literal."
        )

    fields: dict[str, dict] = {}
    for name, field in spec.fields.items():
        raw: dict[str, Any] = {}
        if field.choices is not None:
            raw["choices"] = [[value, weight] for value, weight in field.choices]
        if field.range is not None:
            raw["range"] = list(field.range)
        if field.lookup is not None:
            raw["lookup"] = [[key, value] for key, value in field.lookup.items()]
        if field.depends_on is not None:
            raw["depends_on"] = field.depends_on
        if field.depends_on_context is not None:
            raw["depends_on_context"] = field.depends_on_context
        fields[name] = raw

    return {
        "schema_version": spec.__dict__.get("_schema_version", SCHEMA_FORMAT_VERSION),
        "entity_type": spec.entity_type,
        "fields": fields,
    }


def save_skeleton_spec(spec: SkeletonSpec, path: str | Path) -> None:
    """Write a spec to disk as Canon field-spec JSON (2-space indent,
    matching the persistence convention)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(dump_skeleton_spec(spec), indent=2) + "\n", encoding="utf-8"
    )

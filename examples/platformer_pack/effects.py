"""Stage-effect vocabulary — ambient atmosphere as data (E.7 applied).

Effect VALUES are generated data: the stage agent picks records
``{"name": ..., "params": {...}}`` that land on ``Stage.effects`` and
ride into stage.json for every play surface. Effect KINDS are hardened
code — a kind is only real if an interpreter enforces it (Godot
particles, pygame dots). Unknown names are carried inert (open
carriage): a stage file can sketch "aurora" today and it starts working
the day its interpreter lands.

v1 kinds:

- ``particles_falling`` — snow, rain, embers, drifting ash: one
  interpreter, different params. ``density`` (particles on screen),
  ``speed`` (fall px/s), ``size`` (px), ``color`` (hex), ``drift``
  (horizontal px/s, signed).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

#: kind -> param name -> (min, max, default). Params are clamped in code
#: (computable fix), never round-tripped to the model.
KINDS: dict[str, dict[str, tuple[float, float, float]]] = {
    "particles_falling": {
        "density": (1, 200, 40),
        "speed": (10, 400, 80),
        "size": (1, 8, 2),
        "drift": (-120, 120, 0),
    },
}

_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


#: Unit hints per param, shown in the prompt — the first real run
#: returned 0-1 normalized values because the vocabulary advertised
#: names without ranges (prompts must carry their constraints, I1).
_UNITS: dict[str, str] = {
    "density": "particles on screen",
    "speed": "fall px/s",
    "size": "px",
    "drift": "horizontal px/s",
}


def describe_vocabulary() -> str:
    """Prompt fragment advertising the effect kinds, their param RANGES,
    and units — a bare param name invites scale-guessing."""
    kinds = ", ".join(
        "{}({}, color '#rrggbb')".format(
            name,
            ", ".join(
                f"{param} {lo:g}-{hi:g} {_UNITS.get(param, '')}".rstrip()
                for param, (lo, hi, _default) in params.items()
            ),
        )
        for name, params in KINDS.items()
    )
    return kinds


def sanitize_effects(
    raw: Any, warn: Callable[[str], None] | None = None
) -> list[dict]:
    """Validate/repair generated effect records. Known kinds get their
    numeric params clamped to bounds and color checked (code fixes what
    code can compute); unknown kinds ride through inert."""
    out: list[dict] = []
    if not isinstance(raw, list):
        return out
    for record in raw:
        if not isinstance(record, dict):
            continue
        name = str(record.get("name", "")).strip()
        if not name:
            continue
        params = record.get("params")
        params = dict(params) if isinstance(params, dict) else {}
        bounds = KINDS.get(name)
        if bounds is None:
            out.append({"name": name, "params": params})  # inert carriage
            continue
        clean: dict[str, Any] = {}
        for key, (lo, hi, default) in bounds.items():
            try:
                value = float(params.get(key, default))
            except (TypeError, ValueError):
                value = default
            clamped = min(hi, max(lo, value))
            if warn is not None and clamped != value:
                warn(
                    f"effects: {name}.{key}={value:g} out of bounds "
                    f"[{lo:g}, {hi:g}]; clamped to {clamped:g}."
                )
            clean[key] = clamped
        color = str(params.get("color", ""))
        clean["color"] = color if _HEX.match(color) else "#e8e8f0"
        out.append({"name": name, "params": clean})
    return out

"""GameRules — per-GAME behavior policy, as data (PRD Appendix E.7).

Water containment, enemy/water interaction, and platform pass-through are
not engine truths; they're design decisions that vary per game (and later
per level). The split that keeps this honest:

- **Rule VALUES are template data.** They load from a per-game JSON file
  (``game_rules.json`` next to the schemas; ``--rules`` on the runner) and
  ship in ``manifest.json`` — a different game edits a file, not code.
- **Rule KINDS are hardened code.** A rule is only real if something
  enforces it — a validator kickback, a prompt line, or runtime physics.
  Known keys below are typed, enforced, and tested.
- **The carrier is open** (``extra="allow"``): unknown keys ride through
  to the manifest untouched, carried-but-INERT. That lets a game file
  sketch a future rule today; it starts working the day its enforcement
  lands, with no data migration. Adding a real rule = add the typed field
  here + its enforcement point.

Per-level / per-placement rule overrides (cascade) are Phase 3b; the
game-wide file is the root of that cascade.

v1 enforced policies:

- ``water_containment``: "contained" — pools must be walled in on both
  sides (or reach the level edge); layout validator + prompt enforce.
  "free" — open-sided water allowed (waterfall-style levels).
- ``enemy_water_policy``: "swimmers_only" — swimmers live in water, land
  enemies refuse to enter it (placement validation + runtime movement).
  "forbidden" — no enemies in water at all. "amphibious" — anyone
  anywhere.
- ``platform_drop_through``: whether Down+jump drops the player through
  one-way platforms.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

#: The pack's default game — the template other games copy and edit.
DEFAULT_RULES_PATH = Path(__file__).parent / "game_rules.json"


class GameRules(BaseModel):
    model_config = ConfigDict(extra="allow")

    water_containment: Literal["contained", "free"] = "contained"
    enemy_water_policy: Literal["swimmers_only", "forbidden", "amphibious"] = (
        "swimmers_only"
    )
    platform_drop_through: bool = True


def load_rules(path: str | Path = DEFAULT_RULES_PATH) -> GameRules:
    """Load a game's rules file. Unknown keys are preserved (inert until
    an enforcement point exists for them); known keys are validated."""
    return GameRules.model_validate(json.loads(Path(path).read_text()))


DEFAULT_RULES = load_rules()

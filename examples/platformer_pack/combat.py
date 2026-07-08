"""CombatSpec — per-GAME combat tuning, as data (combat v1).

The GameRules/tiles split applied to combat numbers: **values in data,
interpreters in code**. Hearts, stomp damage, bounce, i-frames, and the
spawn-safety radius load from a per-game JSON file (``combat.json`` next
to the schemas; ``--combat`` on the runner) and ship in ``manifest.json``
— a different game edits a file, not code. Policy TOGGLES (checkpoint
enemy reset, spawn grace) are ``GameRules`` kinds, not numbers here.

One source of truth consumed by the placement validator and the pygame
harness directly; the Godot template mirrors the same arithmetic from
the manifest's combat block (kept in sync like JUMP_HEADROOM).

The helpers below are the combat arithmetic itself — every consumer
resolves sizes, footprints, stomp counts, and heart costs through them
(or their documented main.gd mirror), never ad hoc:

- **Effective size** = ``definition.size * variant.size``. Definitions
  roll a discrete size ({1.0, 1.5, 2.0} in schema v4); variants multiply.
- **Occupancy**: a body of effective size S needs ``max(1, min(2,
  int(S)))`` supported columns and ``ceil(S)`` rows of clearance — REAL
  2x2 cells for a 2.0 body, one column with headroom for 1.5.
- **Stomps**: an enemy dies after ``ceil(hp * hp_mult / stomp_damage)``
  stomps. The v4 schema's hp bands (4-6 / 7-12 / 13-18) make that 1/2/3
  by size tier at the default ``stomp_damage`` — and the v3 range the
  first real run rolled (6-24) lands at a playable 1-4.
- **Contact damage** in integer HEARTS: ``max(1, round(damage *
  damage_mult))``. Hazard tiles cost ``params.damage`` hearts (default
  1); damaging volumes drain ``params.damage_per_second`` into the same
  heart pool via an accumulator — the DAMAGE_BUDGET twins are gone.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

#: The pack's default game — the template other games copy and edit.
DEFAULT_COMBAT_PATH = Path(__file__).parent / "combat.json"


class CombatSpec(BaseModel):
    model_config = ConfigDict(extra="allow")  # _doc etc. ride through

    #: Player heart pool; refills on (re)spawn.
    player_max_hearts: int = Field(default=3, ge=1)
    #: HP removed per stomp — the divisor that turns definition hp into
    #: a stomp count.
    stomp_damage: int = Field(default=6, ge=1)
    #: Upward bounce after a stomp, as a fraction of jump velocity.
    stomp_bounce_factor: float = Field(default=0.7, gt=0.0)
    #: Post-hit invulnerability, seconds. Volume drain ignores it
    #: (continuous hazard); contact and hazard hits grant it.
    hurt_iframes_s: float = Field(default=1.0, ge=0.0)
    #: Spawn SHIELD: how long the player stays untouchable AFTER the
    #: first input that ends the spawn grace. Enemies may roam across a
    #: checkpoint (that is normal gameplay) — this window is what makes
    #: respawning next to a camping chaser fair. Full invincibility:
    #: contact, hazards, and volume drain. Active only while the
    #: ``GameRules.spawn_grace`` policy is "until_move".
    spawn_grace_s: float = Field(default=4.0, ge=0.0)
    #: No enemy within this many columns of the spawn column (on the
    #: spawn row). Violations are TOOL-repaired by a column nudge at
    #: placement validation — never an LLM round-trip.
    spawn_safety_columns: int = Field(default=3, ge=0)


def load_combat(path: str | Path = DEFAULT_COMBAT_PATH) -> CombatSpec:
    """Load a game's combat tuning. Unknown keys are preserved (inert
    until an interpreter exists); known keys are validated."""
    return CombatSpec.model_validate(json.loads(Path(path).read_text()))


DEFAULT_COMBAT = load_combat()


# ---------------------------------------------------------------------------
# Combat arithmetic (mirrored in godot_template/godot/main.gd — keep in sync)
# ---------------------------------------------------------------------------


def effective_size(definition_size: float, variant_size: float = 1.0) -> float:
    """The one size every consumer renders/collides/validates with."""
    return float(definition_size or 1.0) * float(variant_size or 1.0)


def occupancy(size: float) -> tuple[int, int]:
    """(columns, rows) of grid cells a body of effective ``size`` needs:
    supported columns wide, clear rows tall (headroom). 1.0 → 1x1,
    1.5 → 1 column 2 rows, 2.0 → the real 2x2."""
    cols = max(1, min(2, int(size)))
    rows = max(1, math.ceil(size - 1e-9))
    return cols, rows


def stomps_to_kill(hp: float, hp_mult: float, stomp_damage: int) -> int:
    """Stomps needed to kill: ceil(effective hp / stomp damage), never 0."""
    return max(1, math.ceil(float(hp) * float(hp_mult or 1.0) / stomp_damage))


def contact_hearts(damage: float, damage_mult: float = 1.0) -> int:
    """Integer hearts a contact hit costs the player, never 0."""
    return max(1, round(float(damage or 1.0) * float(damage_mult or 1.0)))

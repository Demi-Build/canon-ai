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
  "free" — open-sided pools allowed (waterfall-style levels). Since 3b
  this governs every VOLUME tile (water, lava, mud); the key keeps its
  historical name — renaming it would be a data migration.
- ``enemy_water_policy``: "swimmers_only" — swimmers live in water, land
  enemies refuse to enter it (placement validation + runtime movement).
  "forbidden" — no enemies in water at all. "amphibious" — anyone
  anywhere. "seabed" (water arc, the pack default) — swimmers stay in
  water like swimmers_only, and a LAND enemy may occupy water ONLY when
  its own placement/home cell is submerged over solid ground: a wading
  patroller posted on an underwater flat paces its beat there, but land
  enemies on dry ground still never migrate into pools.
- ``platform_drop_through``: whether Down+jump drops the player through
  one-way platforms.
- ``variant_caps`` (3b): per-level at-most-N caps per enemy-variant name
  (variants.json vocabulary). Enforced at placement validation and offered
  in the placement prompt. A variant name absent from this map is uncapped.
- ``checkpoint_enemy_reset`` (combat v1): killed enemies come back (alive,
  at their placement) when the player dies and respawns at a checkpoint.
  Enforced in both play surfaces' respawn paths.
- ``spawn_grace`` (combat v1): "until_move" — after level start or a
  respawn the player takes no damage and blinks until the first movement
  input; that first input then starts a full-invincibility SHIELD window
  (``CombatSpec.spawn_grace_s`` — the number lives in combat.json) so
  respawning beside a checkpoint-camping enemy stays fair. Enemies are
  NOT frozen during grace — aggressive ones may already be closing in;
  the untouchable window plus the spawn-safety radius are what keep the
  respawn fair (and what let a no-input frame capture actually show the
  chase); "off" — no grace at all. Enforced in both play surfaces
  (damage gates).
- ``rarity_caps`` (multi-stage ecology): per-level at-most-N caps per
  enemy RARITY tier ("rare"/"uncommon"/... — schema v5 rolls the tier
  onto the definition). Enforced at placement validation and stated in
  the placement prompt; an absent tier is uncapped. This is what makes
  rares actually rare on the ground, not just in the fiction.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

#: The pack's default game — the template other games copy and edit.
DEFAULT_RULES_PATH = Path(__file__).parent / "game_rules.json"


class GameRules(BaseModel):
    model_config = ConfigDict(extra="allow")

    water_containment: Literal["contained", "free"] = "contained"
    enemy_water_policy: Literal[
        "swimmers_only", "forbidden", "amphibious", "seabed"
    ] = "seabed"
    platform_drop_through: bool = True
    #: Seconds a BREAKABLE floor tile holds once the player stands on it,
    #: before it crumbles and drops them (both play surfaces read it; the
    #: reachability sim ignores the timing, treating breakables as permanent
    #: footholds — v1 conservative).
    break_delay_s: float = 0.6
    variant_caps: dict[str, int] = {
        "elite": 1, "champion": 1, "relentless": 1, "emberborn": 1,
    }
    checkpoint_enemy_reset: bool = True
    #: Seconds a killed enemy lingers playing its DEATH animation before
    #: vanishing (both play surfaces; frozen in place, no collision). Old
    #: manifests without the key keep the vanish-on-kill-frame behavior.
    death_linger_s: float = 0.5
    spawn_grace: Literal["until_move", "off"] = "until_move"
    rarity_caps: dict[str, int] = {"rare": 1, "uncommon": 2}
    #: Enemy eyesight for aggro detection, by LOCOMOTION archetype — a data
    #: description of each mover's field of view that BOTH play surfaces
    #: apply. The shape KINDS are hardened code (the detection routine both
    #: surfaces mirror); which shape each archetype uses, and the numbers,
    #: are data here. Shapes: "omni" = 360 degrees (swimmers see any
    #: direction); "hemisphere" = the 180-degree forward half-plane, above
    #: AND below (flyers); "forward" = a narrow cone in front, within
    #: "vband" rows of the enemy's own height (ground walkers only see
    #: what's ahead at their level); "none" = blind (immobile sentries).
    #: An archetype absent from this map falls back to "omni" in the
    #: consumers. Aggro is gated by BOTH range and this FOV.
    enemy_sight: dict[str, dict] = {
        "patroller": {"fov": "forward", "vband": 2},
        # Hoppers scan a taller band (their own arc carries them up) —
        # WITHOUT an entry an archetype silently defaults to omni.
        "hopper": {"fov": "forward", "vband": 3},
        "swimmer": {"fov": "omni"},
        "flyer": {"fov": "hemisphere"},
        "sentry": {"fov": "none"},
    }
    #: Aggressive enemies (a non-passive rolled ``aggro`` tier) pursue at
    #: this multiple of their patrol speed — the "increased speed" of a
    #: committed chase. Patrol and the walk home stay at base speed.
    chase_speed_mult: float = 1.5
    #: Flight tuning for the flyer locomotion (both play surfaces read it).
    #: An AGGRESSIVE flyer, IDLE, hovers near its spawn — a vertical bob of
    #: ``hover_amp`` cells at ``hover_freq`` plus a slow horizontal sway of
    #: +/-``hover_sway`` cells at ``sway_speed`` that scans its 180-degree cone
    #: both ways. When it SPOTS the player it hunts FROM ABOVE: it never
    #: descends to ground-chase — it stays on its altitude "plane" and
    #: dive-bombs in committed parabolic arcs. Each ``swoop_period``-second
    #: cycle is a ``swoop_duration``-second PLUNGE (a parabola aimed where the
    #: player was, down and back up to the plane) then a recovery on the plane
    #: (bob + reposition toward the player). Its horizontal reach is
    #: leash-bounded. A PASSIVE flyer patrols horizontally and dives on the
    #: same cycle but at a fixed ``swoop_depth``, not aimed at the player.
    flyer: dict[str, float] = {
        "hover_amp": 0.4,
        "hover_freq": 3.0,
        "hover_sway": 2.0,
        "sway_speed": 1.5,
        "swoop_period": 3.0,
        "swoop_duration": 1.0,
        "swoop_depth": 3.0,
    }


def load_rules(path: str | Path = DEFAULT_RULES_PATH) -> GameRules:
    """Load a game's rules file. Unknown keys are preserved (inert until
    an enforcement point exists for them); known keys are validated."""
    return GameRules.model_validate(json.loads(Path(path).read_text()))


DEFAULT_RULES = load_rules()

# ---------------------------------------------------------------------------
# Per-level overrides (combat/level-picks arc) — the reserved cascade's
# first real layer: the STAGE PLAN may flag a rule/movement twist per
# level ("the deep vents: no drop-through"), but only from the CLOSED
# vocabulary below, validated FAIL-CLOSED (design choice = LLM,
# mechanics + bounds = code).
# ---------------------------------------------------------------------------

#: The pack's override vocabulary — data, like the rules themselves.
DEFAULT_OVERRIDES_PATH = Path(__file__).parent / "rule_overrides.json"


def load_override_vocabulary(
    path: str | Path = DEFAULT_OVERRIDES_PATH,
) -> dict[str, dict]:
    """Load the closed per-level override vocabulary: key -> {type,
    band?, target?}. ``target: "movement"`` keys override
    PlayerMovementSpec; the rest override GameRules."""
    raw = json.loads(Path(path).read_text())
    return dict(raw.get("keys", {}))


DEFAULT_OVERRIDE_VOCABULARY = load_override_vocabulary()


def validate_overrides(
    proposed: dict,
    vocabulary: dict[str, dict] | None = None,
) -> tuple[dict, dict, list[str]]:
    """FAIL-CLOSED validation of a proposed per-level override dict:
    returns ``(rules_overrides, movement_overrides, problems)`` — only
    vocabulary keys whose value parses to the declared type AND lands
    inside the declared band survive; everything else is dropped with a
    note (the caller warns). Values are coerced to the declared type so
    an LLM float never rides into an int field."""
    vocab = DEFAULT_OVERRIDE_VOCABULARY if vocabulary is None else vocabulary
    rules_out: dict = {}
    movement_out: dict = {}
    problems: list[str] = []
    for key, value in dict(proposed or {}).items():
        spec = vocab.get(str(key))
        if spec is None:
            problems.append(
                f"override {key!r} is not in the vocabulary "
                f"({sorted(vocab)}) — dropped."
            )
            continue
        kind = str(spec.get("type", "float"))
        try:
            if kind == "bool":
                if not isinstance(value, bool):
                    raise ValueError("not a bool")
                coerced: Any = value
            elif kind == "int":
                coerced = int(value)
            else:
                coerced = float(value)
        except (TypeError, ValueError):
            problems.append(
                f"override {key!r}={value!r} is not a {kind} — dropped."
            )
            continue
        band = spec.get("band")
        if band is not None and not (band[0] <= coerced <= band[1]):
            problems.append(
                f"override {key!r}={coerced!r} is outside the allowed "
                f"band {band} — dropped."
            )
            continue
        if str(spec.get("target", "rules")) == "movement":
            movement_out[str(key)] = coerced
        else:
            rules_out[str(key)] = coerced
    return rules_out, movement_out, problems

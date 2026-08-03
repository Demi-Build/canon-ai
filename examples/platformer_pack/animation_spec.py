"""The animation VOCABULARY and SELECTION LADDER as data.

Which states an actor has — and which one the game plays at any moment — used
to be module constants and two hand-written ladders. That is wrong for a
generator: a different platformer wants ``swim``, ``climb``, ``wall_slide`` or
``attack_windup``, and a boss wants states its archetype-mates don't have.

Two layers, both overridable per pack via ``animation.json`` at the pack root
(the same pack-local override idiom as ``schemas/<type>.json``, resolved at RUN
TIME so regen and resume see edits):

``states``   the vocabulary — per state: the motion ``brief`` woven into the
             authoring prompt, the fallback ``frames`` count, and the ``loop``
             mode both surfaces play it at.
``sets``     which states an actor gets, keyed ``player`` | ``enemy`` |
             ``enemy.<archetype>``. An individual enemy row may override with
             its own ``animation_states`` list.
``ladders``  ordered ``{state, when, why}`` rules per actor kind. The FIRST
             rule whose condition holds contributes its state, then the next,
             and so on; anything that matches nothing falls through to the
             tail. This is exactly the shape the hand-written ladders had, so
             the defaults below reproduce them rule for rule.

**Conditions stay deliberately small.** They compare NAMED SIGNALS the engines
already track (see ``PLAYER_SIGNALS`` / ``ENEMY_SIGNALS``) — a bare bool, or
one of ``gt``/``lt``/``gte``/``lte``/``eq``. Anything compound (``braking`` is
"holding away from your own momentum, fast enough to matter") is computed in
engine code and exposed as a named signal. That split is the point: a new
STATE is data, a new SIGNAL is gameplay, and gameplay must exist identically in
both engines.

``main.gd`` mirrors ``eval_ladder`` and reads the same file.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

#: Pack-local override filename (pack root, beside manifest.json).
ANIMATION_SPEC_REL = "animation.json"

#: The signals the PLAYER ladder may test. Every one is tracked by both play
#: surfaces at the selection site; adding to this list is an engine change.
PLAYER_SIGNALS: tuple[str, ...] = (
    "on_ground",    # bool — feet supported this frame
    "descending",   # bool — past the peak (vy > 0)
    "moving",       # bool — a horizontal input is held
    "braking",      # bool — input opposes carried momentum, |vx| > 0.5
    "landing",      # bool — inside the one-shot touchdown window
    "submerged",    # bool — standing in a volume (water) tile
    "vy",           # number — vertical velocity, for thresholds
    "vx",           # number — horizontal velocity, for thresholds
)

#: The signals the ENEMY ladder may test.
ENEMY_SIGNALS: tuple[str, ...] = (
    "alive",        # bool
    "hurt",         # bool — inside the hit-recoil window
    "grounded",     # bool — false mid-hop for a hopper
    "moving",       # bool — advancing along its beat
    "submerged",    # bool — its home cell is a volume
)

#: The built-in vocabulary. Every entry that existed as a module constant in
#: vlm_qa.py is reproduced here verbatim, so a pack with no animation.json
#: generates and plays byte-identically to before this file existed.
DEFAULT_STATES: dict[str, dict] = {
    "idle": {
        "brief": "at rest / holding position (a flyer hovers, a sentry barely stirs)",
        "frames": 2, "loop": "loop",
    },
    "walk": {
        "brief": "actively moving along its path (for a flyer/swimmer: flight/swim)",
        "frames": 4, "loop": "loop",
    },
    "hurt": {"brief": "recoiling from a hit — a brief flinch", "frames": 2, "loop": "once"},
    "death": {
        "brief": "defeated — collapse, tumble, or fade", "frames": 4, "loop": "once",
    },
    "jump": {
        "brief": "the RISING launch — a compact crouch then push-off, body "
                 "gathered and tucked, moving UP; a rounded rising silhouette, "
                 "clearly NOT the stretched trailing-limb fall",
        "frames": 6, "loop": "once",
    },
    "fall": {
        "brief": "the DESCENT past the peak — body stretched tall and vertical, "
                 "arms, limbs and hair trailing UPWARD; a tall narrow silhouette",
        "frames": 3, "loop": "loop",
    },
    "land": {
        "brief": "the touchdown SQUASH — body compressed low and WIDE, legs fully "
                 "folded, an implied dust puff; a short wide silhouette, clearly "
                 "flatter than idle",
        "frames": 2, "loop": "once",
    },
    "skid": {
        "brief": "braking against carried momentum — torso leaned BACK, legs "
                 "thrust FORWARD and braced; a diagonal leaning silhouette, "
                 "distinct from an upright walk",
        "frames": 2, "loop": "loop",
    },
    # Shipped as DATA to prove the mechanism: no engine branch names `swim`,
    # it is selected purely by the `submerged` rule below.
    "swim": {
        "brief": "propelling through water — body horizontal and undulating, "
                 "limbs sweeping in a stroke cycle, hair and trailing parts "
                 "drifting; clearly buoyant, never a walking pose",
        "frames": 4, "loop": "loop",
    },
}

#: Which states an actor gets. `enemy.<archetype>` widens the base enemy set —
#: the hopper entry reproduces the old `enemy_animation_states` special case.
#:
#: These are deliberately IDENTICAL to the pre-data sets, `swim` included:
#: adding a state to a default set changes what every future run GENERATES
#: (and bills). `swim` is defined in the vocabulary and wired into both
#: ladders, but stays inert until a pack opts in via animation.json —
#: `{"sets": {"player": ["idle","walk","jump","fall","land","skid","swim"]}}`.
#: That is the whole point: a new state costs one line of data, not a release.
DEFAULT_SETS: dict[str, list[str]] = {
    "player": ["idle", "walk", "jump", "fall", "land", "skid"],
    "enemy": ["idle", "walk", "hurt", "death"],
    "enemy.hopper": ["idle", "walk", "hurt", "death", "jump"],
}

#: The player ladder, rule for rule as the hand-written one. Order IS the
#: priority. `swim` sits first so water wins over the airborne branch — a
#: submerged player is never "falling".
DEFAULT_PLAYER_LADDER: list[dict] = [
    {"state": "swim", "when": {"submerged": True}, "why": "in water"},
    {
        "state": "fall",
        "when": {"on_ground": False, "descending": True},
        "why": "airborne · past the peak",
    },
    {"state": "jump", "when": {"on_ground": False}, "why": "airborne · rising"},
    {
        "state": "skid",
        "when": {"on_ground": True, "braking": True},
        "why": "grounded · braking against momentum",
    },
    {
        "state": "land",
        "when": {"on_ground": True, "landing": True},
        "why": "grounded · touchdown window",
    },
    {
        "state": "walk",
        "when": {"on_ground": True, "moving": True},
        "why": "grounded · moving",
    },
]

#: The enemy ladder. `death` first and unconditional-on-!alive mirrors the old
#: "if not alive: [death, hurt, idle]" branch, whose tail differs from the
#: living tail — so the dead case carries its own explicit tail.
DEFAULT_ENEMY_LADDER: list[dict] = [
    {"state": "death", "when": {"alive": False}, "why": "defeated",
     "tail": ["hurt", "idle"]},
    {"state": "hurt", "when": {"alive": True, "hurt": True},
     "why": "recoiling from a hit"},
    {"state": "jump", "when": {"alive": True, "grounded": False},
     "why": "airborne (mid-hop)"},
    {"state": "swim", "when": {"alive": True, "submerged": True, "moving": True},
     "why": "swimming its beat"},
    {"state": "walk", "when": {"alive": True, "moving": True},
     "why": "moving along its path"},
]

#: Appended after every matching rule — the loud fallback to a state the actor
#: is guaranteed to have. Per actor kind because the enemy tail is longer.
DEFAULT_TAILS: dict[str, list[str]] = {
    "player": ["idle", "walk"],
    "enemy": ["idle", "walk", "hurt", "death"],
}

#: Reason reported when nothing matched and the tail carried the pick.
DEFAULT_WHY: dict[str, str] = {
    "player": "grounded · at rest",
    "enemy": "holding position",
}


def load_spec(pack_dir: str | Path | None) -> dict:
    """The effective animation spec: built-in defaults, overlaid by a pack's
    ``animation.json`` when present. Overlay is per top-level section and per
    STATE key, so a pack can retune one state's frame count without restating
    the vocabulary. A missing or unreadable file yields the defaults — the
    animation system never hard-fails on a bad override, it falls back loudly
    via :func:`validate_spec`."""
    spec = {
        "states": {k: dict(v) for k, v in DEFAULT_STATES.items()},
        "sets": {k: list(v) for k, v in DEFAULT_SETS.items()},
        "ladders": {
            "player": [dict(r) for r in DEFAULT_PLAYER_LADDER],
            "enemy": [dict(r) for r in DEFAULT_ENEMY_LADDER],
        },
        "tails": {k: list(v) for k, v in DEFAULT_TAILS.items()},
        "why": dict(DEFAULT_WHY),
    }
    if pack_dir is None:
        return spec
    path = Path(pack_dir) / ANIMATION_SPEC_REL
    if not path.is_file():
        return spec
    try:
        over = json.loads(path.read_text())
    except (OSError, ValueError):
        return spec
    if not isinstance(over, dict):
        return spec
    for state, entry in (over.get("states") or {}).items():
        if isinstance(entry, dict):
            spec["states"].setdefault(state, {}).update(entry)
    for key, states in (over.get("sets") or {}).items():
        if isinstance(states, list):
            spec["sets"][key] = [str(s) for s in states]
    for kind, rules in (over.get("ladders") or {}).items():
        if isinstance(rules, list):
            spec["ladders"][kind] = [r for r in rules if isinstance(r, dict)]
    for kind, tail in (over.get("tails") or {}).items():
        if isinstance(tail, list):
            spec["tails"][kind] = [str(s) for s in tail]
    for kind, why in (over.get("why") or {}).items():
        spec["why"][kind] = str(why)
    return spec


def validate_spec(spec: dict) -> list[str]:
    """Problems with an effective spec, as human-readable strings. Empty list
    = usable. Fail-closed in the same spirit as ``db schema``: a ladder naming
    a state the vocabulary doesn't define, or a signal the engines don't track,
    is a mistake that would otherwise show up as a silently missing animation.
    """
    problems: list[str] = []
    states = spec.get("states") or {}
    for key, names in (spec.get("sets") or {}).items():
        for n in names:
            if n not in states:
                problems.append(f"set {key!r} names undefined state {n!r}")
    for kind, rules in (spec.get("ladders") or {}).items():
        allowed = PLAYER_SIGNALS if kind == "player" else ENEMY_SIGNALS
        for i, rule in enumerate(rules):
            st = rule.get("state")
            if st not in states:
                problems.append(f"{kind} rule {i} names undefined state {st!r}")
            for sig in (rule.get("when") or {}):
                if sig not in allowed:
                    problems.append(
                        f"{kind} rule {i} tests unknown signal {sig!r} "
                        f"(engines track: {', '.join(allowed)})"
                    )
    for kind, tail in (spec.get("tails") or {}).items():
        for n in tail:
            if n not in states:
                problems.append(f"tail {kind!r} names undefined state {n!r}")
    return problems


def states_for(spec: dict, kind: str, archetype: str = "", row: Any = None) -> list[str]:
    """The states ONE actor authors and generates.

    Resolution order, most specific first: the actor's own
    ``animation_states`` (a row field — "this boss has a slam"), then
    ``enemy.<archetype>``, then the bare kind. Unknown names are dropped
    rather than generated blind.
    """
    states = spec.get("states") or {}
    explicit = None
    if row is not None:
        explicit = getattr(row, "animation_states", None)
        if explicit is None and isinstance(row, dict):
            explicit = row.get("animation_states")
    if isinstance(explicit, (list, tuple)) and explicit:
        return [str(s) for s in explicit if str(s) in states]
    sets = spec.get("sets") or {}
    if kind != "player" and archetype:
        keyed = sets.get(f"{kind}.{archetype}")
        if keyed:
            return [s for s in keyed if s in states]
    return [s for s in (sets.get(kind) or []) if s in states]


def _holds(cond: Any, value: Any) -> bool:
    """One signal test. A bare bool/str/number compares by equality (with the
    usual truthiness for bools); a dict carries one comparison op. Unknown ops
    fail CLOSED — a typo must not silently make a rule always fire."""
    if isinstance(cond, dict):
        for op, want in cond.items():
            try:
                if op == "gt" and not (float(value) > float(want)):
                    return False
                elif op == "lt" and not (float(value) < float(want)):
                    return False
                elif op == "gte" and not (float(value) >= float(want)):
                    return False
                elif op == "lte" and not (float(value) <= float(want)):
                    return False
                elif op == "eq" and value != want:
                    return False
                elif op not in ("gt", "lt", "gte", "lte", "eq"):
                    return False
            except (TypeError, ValueError):
                return False
        return True
    if isinstance(cond, bool):
        return bool(value) is cond
    return value == cond


def eval_ladder(rules: list, signals: dict, tail: list, default_why: str) -> tuple[list, str]:
    """``(candidate states in priority order, why)`` — the data-driven form of
    the hand-written ladders.

    Every rule whose condition holds contributes its state, in declaration
    order; ``why`` comes from the FIRST match (the state the actor will play,
    assuming it has the art). A matching rule may carry its own ``tail``, which
    replaces the kind's default — the dead-enemy branch needs a shorter one.
    Mirror of main.gd's ``_eval_ladder``.
    """
    picked: list[str] = []
    why = default_why
    use_tail = tail
    for rule in rules:
        when = rule.get("when") or {}
        if all(_holds(c, signals.get(sig)) for sig, c in when.items()):
            state = str(rule.get("state", ""))
            if state and state not in picked:
                picked.append(state)
            if len(picked) == 1:
                why = str(rule.get("why", default_why))
                if isinstance(rule.get("tail"), list):
                    use_tail = [str(s) for s in rule["tail"]]
    return picked + [s for s in use_tail if s not in picked], why

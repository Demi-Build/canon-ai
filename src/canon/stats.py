"""Stat-budget redistribution — game-agnostic.

``fix_stats`` takes a per-archetype stat *template* (the desired shape, e.g. a
mage's INT high) and redistributes it to hit a point *budget* while keeping each
stat inside its role's range. It is a parameterized port of MazeWorld's
``_fix_stats`` (``src/generate/class_gen.py``): every number — the budget, the
role ranges, the stat names — is supplied by the caller. Canon core hardcodes
none of them, so a pack (or a future user-edited template) owns the stat scheme
entirely. The template's emphasis is preserved: primaries stay high because the
caller put them in the ``primary`` role with a high range.
"""

from __future__ import annotations


def fix_stats(
    template: dict[str, int],
    stat_roles: dict[str, list[str]],
    role_ranges: dict[str, tuple[int, int]],
    budget: int,
    stat_names: list[str],
    *,
    default_value: int = 10,
) -> dict[str, int]:
    """Clamp a stat template to role ranges, then redistribute to ``budget``.

    Args:
        template: ``{stat: value}`` starting point (LLM- or template-supplied).
            Missing stats default to ``default_value``.
        stat_roles: ``{"primary": [...], "secondary": [...], "dump": [...]}`` —
            which stats fill which role for this archetype. Roles beyond these
            three are treated as their own range if present in ``role_ranges``.
        role_ranges: ``{role: (lo, hi)}`` inclusive clamp range per role. Stats
            not named in any role fall back to the ``"dump"`` range.
        budget: Target sum of all stats after redistribution.
        stat_names: The full ordered list of stat keys for this game.
        default_value: Value for a stat absent from ``template``.

    Returns:
        ``{stat: int}`` for every name in ``stat_names``, clamped to role
        ranges and summing to ``budget`` when the ranges allow it. If the budget
        is unreachable within the ranges, returns the closest achievable total.
    """
    dump_range = role_ranges.get("dump", (default_value, default_value))

    values: dict[str, int] = {}
    for stat in stat_names:
        raw = template.get(stat, default_value)
        values[stat] = int(raw) if isinstance(raw, (int, float)) else default_value

    # Clamp each role's stats to that role's range.
    assigned: set[str] = set()
    for role, members in stat_roles.items():
        lo, hi = role_ranges.get(role, dump_range)
        for stat in members:
            if stat in values:
                values[stat] = max(lo, min(hi, values[stat]))
                assigned.add(stat)

    unassigned = [s for s in stat_names if s not in assigned]
    dump_lo, dump_hi = dump_range
    for stat in unassigned:
        values[stat] = max(dump_lo, min(dump_hi, values[stat]))

    # Redistribute ±1 at a time to hit the budget, never leaving role ranges.
    # Order favors moving secondary/dump/unassigned before touching primaries,
    # so an archetype's defining stats are the last to be sacrificed.
    diff = sum(values.values()) - budget
    adjustable = (
        list(stat_roles.get("secondary", []))
        + list(stat_roles.get("dump", []))
        + unassigned
        + list(stat_roles.get("primary", []))
    )
    if adjustable and diff != 0:
        idx = 0
        guard = len(adjustable) * 30
        while diff != 0 and idx < guard:
            stat = adjustable[idx % len(adjustable)]
            if stat in unassigned:
                lo, hi = dump_lo, dump_hi
            else:
                role = next(
                    (r for r, members in stat_roles.items() if stat in members),
                    "dump",
                )
                lo, hi = role_ranges.get(role, dump_range)
            if diff > 0 and values[stat] > lo:
                values[stat] -= 1
                diff -= 1
            elif diff < 0 and values[stat] < hi:
                values[stat] += 1
                diff += 1
            idx += 1

    return values

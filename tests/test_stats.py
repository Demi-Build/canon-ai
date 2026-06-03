"""Tests for canon.stats.fix_stats — parameterized stat-budget redistribution."""

from __future__ import annotations

from canon.stats import fix_stats

STAT_NAMES = ["STR", "DEX", "CON", "INT", "WIS", "CHA", "LUCK"]
RANGES = {"primary": (14, 18), "secondary": (12, 16), "dump": (8, 12)}

# A MazeWorld-shaped mage: INT primary, WIS/DEX secondary, STR/CON/CHA dump.
MAGE_ROLES = {"primary": ["INT"], "secondary": ["WIS", "DEX"], "dump": ["STR", "CON", "CHA"]}
MAGE_TEMPLATE = {"STR": 8, "CON": 10, "DEX": 12, "INT": 16, "WIS": 14, "CHA": 10, "LUCK": 10}


def test_sums_to_budget():
    out = fix_stats(MAGE_TEMPLATE, MAGE_ROLES, RANGES, budget=95, stat_names=STAT_NAMES)
    assert sum(out.values()) == 95


def test_budget_is_not_hardcoded():
    """Different budgets are honored — nothing in core pins a number. Budgets
    chosen within the reachable range the mage clamps allow (~70..98)."""
    for budget in (72, 80, 90, 95):
        out = fix_stats(MAGE_TEMPLATE, MAGE_ROLES, RANGES, budget=budget, stat_names=STAT_NAMES)
        assert sum(out.values()) == budget, (budget, out)


def test_unreachable_budget_returns_closest():
    """A budget above the ranges' max sum returns the max achievable, not a crash."""
    out = fix_stats(MAGE_TEMPLATE, MAGE_ROLES, RANGES, budget=999, stat_names=STAT_NAMES)
    assert out["INT"] == 18  # pinned to primary max
    assert sum(out.values()) == 18 + 16 + 16 + 12 + 12 + 12 + 12  # 98 (range max)


def test_primary_stays_highest_for_mage():
    """Archetype emphasis preserved: INT (primary) ends >= every other stat."""
    out = fix_stats(MAGE_TEMPLATE, MAGE_ROLES, RANGES, budget=95, stat_names=STAT_NAMES)
    assert out["INT"] == max(out.values())
    assert out["INT"] >= 14  # within primary range


def test_all_stats_within_role_ranges():
    out = fix_stats(MAGE_TEMPLATE, MAGE_ROLES, RANGES, budget=95, stat_names=STAT_NAMES)
    assert 14 <= out["INT"] <= 18
    for s in ("WIS", "DEX"):
        assert 12 <= out[s] <= 16
    for s in ("STR", "CON", "CHA"):
        assert 8 <= out[s] <= 12


def test_warrior_shape_differs_from_mage():
    """A STR-primary template yields a STR-dominant block — proving the shape
    comes from the template/roles (pack data), not core."""
    warrior_roles = {"primary": ["STR", "CON"], "secondary": ["DEX", "CHA"], "dump": ["INT", "WIS"]}
    warrior_template = {"STR": 16, "CON": 14, "DEX": 12, "INT": 8, "WIS": 10, "CHA": 10, "LUCK": 10}
    out = fix_stats(warrior_template, warrior_roles, RANGES, budget=95, stat_names=STAT_NAMES)
    assert sum(out.values()) == 95
    assert out["STR"] >= 14 and out["CON"] >= 14
    assert out["INT"] <= 12 and out["WIS"] <= 12


def test_missing_template_stats_default():
    out = fix_stats({"INT": 18}, MAGE_ROLES, RANGES, budget=95, stat_names=STAT_NAMES)
    assert set(out) == set(STAT_NAMES)
    assert sum(out.values()) == 95


def test_custom_stat_names_and_ranges():
    """Entirely different game: 3 stats, custom ranges/budget — no STR/95 baked in."""
    names = ["power", "speed", "luck"]
    roles = {"primary": ["power"], "secondary": ["speed"], "dump": ["luck"]}
    ranges = {"primary": (5, 9), "secondary": (3, 6), "dump": (1, 3)}
    out = fix_stats({"power": 9, "speed": 5, "luck": 2}, roles, ranges, budget=15, stat_names=names)
    assert sum(out.values()) == 15
    assert 5 <= out["power"] <= 9

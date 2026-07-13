"""Section model + stitcher (sectioned-levels phase, chunk A)."""
from __future__ import annotations

import pytest

from canon.pipeline.rng import derive_rng

pytest.importorskip("numpy")

from examples.platformer_pack.dsl import stamp  # noqa: E402
from examples.platformer_pack.level import seam_summary  # noqa: E402
from examples.platformer_pack.movement import DEFAULT_MOVEMENT  # noqa: E402
from examples.platformer_pack.sections import (  # noqa: E402
    DEFAULT_VOCAB,
    SECTION_OVERLAP,
    composite,
    plan_sections,
    section_owner_of_x,
)
from examples.platformer_pack.validate import (  # noqa: E402
    auto_bridge_grid,
    place_exit,
    reachable_cells,
    snap_checkpoints_grid,
    snap_spawn_grid,
)


class TestSectionVocab:
    def test_vocab_loads_with_both_axes(self) -> None:
        assert "runway" in DEFAULT_VOCAB and "gauntlet" in DEFAULT_VOCAB
        axes = {a.axis for a in DEFAULT_VOCAB.values()}
        assert axes == {"horizontal", "vertical"}
        # feature_bias / flavor steer the prompt — every archetype carries them.
        for arch in DEFAULT_VOCAB.values():
            assert arch.flavor and isinstance(arch.feature_bias, dict)

    def test_unknown_keys_ride_through(self) -> None:
        # The carrier is open (extra="allow") — a game file may sketch a knob.
        from examples.platformer_pack.sections import SectionArchetype

        a = SectionArchetype.model_validate({"axis": "horizontal", "future": 7})
        assert a.model_dump().get("future") == 7


class TestPlanSections:
    def test_plan_is_deterministic_and_tiles_the_width(self) -> None:
        plan1 = plan_sections(72, 16, 2, derive_rng("s", "plat:layout", "l1"))
        plan2 = plan_sections(72, 16, 2, derive_rng("s", "plat:layout", "l1"))
        key = lambda p: [(s.archetype, s.length, s.x_off) for s in p]  # noqa: E731
        assert key(plan1) == key(plan2)  # deterministic in the seed
        # Sections tile the axis with the shared overlap and reach the edge.
        assert plan1[0].archetype == "runway"  # gentle opener = safe spawn
        assert plan1[-1].x_off + plan1[-1].length >= 72
        for a, b in zip(plan1, plan1[1:]):
            assert b.x_off == a.x_off + a.length - SECTION_OVERLAP

    def test_only_axis_matching_archetypes_are_used(self) -> None:
        plan = plan_sections(80, 16, 3, derive_rng("x", "p", "l"), axis="horizontal")
        assert all(DEFAULT_VOCAB[s.archetype].axis == "horizontal" for s in plan)

    def test_tiny_level_gets_one_section(self) -> None:
        plan = plan_sections(10, 16, 1, derive_rng("t", "p", "l"))
        assert len(plan) == 1


class TestComposite:
    def test_stitch_offsets_grid_and_markers(self) -> None:
        left = stamp("floor(0,19)\nspawn(2)\nexit(18)", 20, 16)
        right = stamp("floor(0,19)\nplatform(5,11,3)\nspawn(2)\nexit(18)", 20, 16)
        res = composite([(left, 0, 0), (right, 14, 0)], 34, 16)
        assert res.grid.shape == (16, 34)
        # spawn from section 0; exit from the LAST section, both offset.
        assert res.spawn == (2, 13)
        assert res.exit is not None and res.exit[0] == 19 + 14  # relocated + off
        # the right section's platform (local col 5, row 11) lands at col 19.
        assert int(res.grid[11, 5 + 14]) != 0

    def test_non_empty_wins_keeps_seam_solid(self) -> None:
        # A later section with an empty overlap must NOT erase earlier terrain.
        left = stamp("floor(0,19)\nspawn(2)\nexit(18)", 20, 16)
        right = stamp("floor(10,19)\nspawn(11)\nexit(18)", 20, 16)  # empty cols 0-9
        res = composite([(left, 0, 0), (right, 14, 0)], 34, 16)
        # overlap cols 14-19: left had floor there, right is empty -> stays floor
        assert int(res.grid[14, 16]) != 0  # ground row still solid across seam


class TestSectionOwner:
    def test_owner_maps_columns_last_wins_overlap(self) -> None:
        plan = plan_sections(72, 16, 2, derive_rng("s", "plat:layout", "l1"))
        # Every column resolves to a section whose x-range contains it.
        for x in (0, plan[1].x_off, 71):
            i = section_owner_of_x(plan, x)
            assert plan[i].x_off <= x < plan[i].x_off + plan[i].length
        # A shared overlap column resolves to the LATER section (composite
        # layering), so a seam failure routes to the section that owns it.
        seam_col = plan[1].x_off  # first column of section 1 (in the overlap)
        assert section_owner_of_x(plan, seam_col) == 1


class TestSeamSummary:
    def test_flat_edge_reports_standable_footing(self) -> None:
        res = stamp("floor(0,19)\nspawn(2)", 20, 16, validate_markers=False)
        summary = seam_summary(res)
        assert "standable footing" in summary and "y=13" in summary

    def test_pit_edge_reports_no_footing(self) -> None:
        res = stamp("floor(0,13)\npit(14,19)\nspawn(2)", 20, 16, validate_markers=False)
        summary = seam_summary(res)
        assert "NO footing" in summary


class TestGridRepair:
    """The grid-native twins of the text repair tools — they operate on the
    composited whole grid + markers (there is no whole-level DSL to rewrite)."""

    def test_place_exit_is_rightmost_floor_and_excludes_spawn(self) -> None:
        res = stamp("floor(0,29)\nspawn(2)", 30, 16, validate_markers=False)
        assert place_exit(res.grid) == (29, 13)
        assert place_exit(res.grid, exclude={29}) == (28, 13)

    def test_place_exit_none_when_no_open_floor(self) -> None:
        import numpy as np

        grid = np.zeros((16, 10), dtype=np.int8)  # no floor at all
        assert place_exit(grid) is None

    def test_snap_spawn_grid_moves_stranded_spawn(self) -> None:
        # spawn column has no floor under it (a gap) -> snap to nearest floor.
        res = stamp("floor(0,29)\npit(2,4)\nspawn(10)", 30, 16, validate_markers=False)
        spawn, moves = snap_spawn_grid(res.grid, (3, 13), (29, 13))
        assert spawn != (3, 13) and moves  # relocated off the pit

    def test_snap_checkpoints_grid_moves_and_preserves_others(self) -> None:
        res = stamp(
            "floor(0,29)\npit(9,11)\nspawn(2)\ncheckpoint(10)",
            30, 16, validate_markers=False,
        )
        triggers, moves = snap_checkpoints_grid(res.grid, res.triggers)
        cp = [t for t in triggers if t.type == "checkpoint"][0]
        assert cp.x != 10 and moves  # the checkpoint over the pit was snapped

    def test_auto_bridge_grid_repairs_wide_gap(self) -> None:
        # A 9-wide gap (dx beyond a running jump) — the tool stamps a bridge.
        res = stamp(
            "floor(0,20)\nfloor(30,47)\nspawn(2)", 48, 16, validate_markers=False
        )
        exit_ = place_exit(res.grid)
        grid, bridges, problems = auto_bridge_grid(
            res.grid, res.spawn, exit_, DEFAULT_MOVEMENT, triggers=res.triggers
        )
        assert bridges and all(op.startswith("platform(") for op in bridges)
        assert not problems
        assert exit_ in reachable_cells(grid, res.spawn, DEFAULT_MOVEMENT)

    def test_auto_bridge_grid_leaves_design_problems(self) -> None:
        # An unstandable exit (covered by a wall) is a DESIGN failure — never
        # bridged; returned for the owning section to fix.
        res = stamp(
            "floor(0,29)\nspawn(2)\nwall(29,12,13)", 30, 16, validate_markers=False
        )
        grid, bridges, problems = auto_bridge_grid(
            res.grid, res.spawn, (29, 13), DEFAULT_MOVEMENT
        )
        assert problems and not bridges

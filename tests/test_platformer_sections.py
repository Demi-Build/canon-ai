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
    plan_level,
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


class TestLevelBlueprint:
    """G1: a level's BLUEPRINT is rolled up front — section count is capped and
    width-scaled, checkpoints scale with the count and are STITCHER-placed
    (reachable, both axes)."""

    def test_section_count_capped_and_scales_with_width(self) -> None:
        from examples.platformer_pack.sections import MAX_SECTIONS, plan_level

        counts = {}
        for w in (48, 72, 104, 132):
            lp = plan_level(w, 16, 3, derive_rng("s", "p", "l"))
            assert len(lp.sections) <= MAX_SECTIONS
            counts[w] = len(lp.sections)
        # wider levels get MORE (chunky) sections, never more than the cap.
        assert counts[48] <= counts[104] <= counts[132] <= MAX_SECTIONS
        assert counts[132] < 8  # was ~6-8 pre-cap; now capped at 5

    def test_checkpoint_count_follows_the_rule(self) -> None:
        # 0 for 1-2 sections, 1 for 3-4, 2 for 5 — interior sections only.
        from examples.platformer_pack.sections import _checkpoint_sections

        assert _checkpoint_sections(1) == []
        assert _checkpoint_sections(2) == []
        assert len(_checkpoint_sections(3)) == 1
        assert len(_checkpoint_sections(4)) == 1
        assert len(_checkpoint_sections(5)) == 2
        for n in range(1, 6):
            cps = _checkpoint_sections(n)
            assert all(0 < i < n - 1 for i in cps)  # never the spawn/exit section

    def test_blueprint_carries_a_single_exit_now_but_a_list(self) -> None:
        lp = plan_level(120, 24, 3, derive_rng("s", "p", "l"))
        assert len(lp.exits) == 1  # v1: one exit...
        assert lp.exits[0].section_idx == len(lp.sections) - 1  # ...at the last
        # owner_of routes a cell to its section (used by failure routing).
        assert lp.owner_of(0, 0) == 0

    def test_stitcher_places_reachable_checkpoints_both_axes(self) -> None:
        from examples.platformer_pack.validate import (
            place_checkpoints_grid,
            reachable_cells,
        )

        # HORIZONTAL: two sections' worth of flat floor, checkpoint in section 1.
        res = stamp("floor(0,59)\nspawn(2)", 60, 16, validate_markers=False)
        h_sections = [
            type("S", (), {"x_off": 0, "y_off": 0, "length": 34})(),
            type("S", (), {"x_off": 28, "y_off": 0, "length": 32})(),
        ]
        cps = place_checkpoints_grid(
            res.grid, (2, 13), (59, 13), h_sections, [1], "horizontal",
            DEFAULT_MOVEMENT,
        )
        assert len(cps) == 1 and 28 <= cps[0].x < 60
        reached = reachable_cells(res.grid, (2, 13), DEFAULT_MOVEMENT)
        assert (cps[0].x, cps[0].y) in reached  # reachable by construction

        # VERTICAL: a ladder shaft — the checkpoint lands on a reachable rung.
        climb = "floor(0,11)\nspawn(2)\n" + "\n".join(
            f"platform({2 + i % 2},{12 - 2 * i},3)" for i in range(6)
        )
        vres = stamp(climb, 12, 16, validate_markers=False)
        v_sections = [type("S", (), {"x_off": 0, "y_off": 0, "length": 16})()]
        vcps = place_checkpoints_grid(
            vres.grid, vres.spawn, (2, 1), v_sections, [0], "vertical",
            DEFAULT_MOVEMENT,
        )
        vreached = reachable_cells(vres.grid, vres.spawn, DEFAULT_MOVEMENT)
        assert not vcps or (vcps[0].x, vcps[0].y) in vreached


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
        assert place_exit(res.grid, exclude={(29, 13)}) == (28, 13)

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


class TestAxisAwareRepair:
    """G2: the locate/route/bridge layer is TARGET-RELATIVE (not horizontal-
    biased), so vertical climbs repair correctly and reachability breaks route
    to the section owning the GAP, not the far-off target."""

    def test_place_exit_vertical_keeps_a_summit_above_the_spawn(self) -> None:
        # The summit sits directly above the spawn's COLUMN — a per-column
        # exclude used to drop it; a per-CELL exclude keeps it.
        g = stamp(
            "floor(0,7)\nspawn(2)\nplatform(2,1,1)\nplatform(3,3,1)\n"
            "platform(2,5,1)", 8, 12, validate_markers=False,
        ).grid
        summit = place_exit(g, exclude={(2, 9)}, axis="vertical")
        assert summit is not None and summit[1] <= 2  # a TOP cell, not dropped

    def test_locate_break_frontier_is_high_on_a_climb(self) -> None:
        from examples.platformer_pack.validate import (
            _locate_break,
            standable_cells,
        )

        climb = stamp(
            "floor(0,9)\nspawn(2)\nplatform(2,11,3)\nplatform(3,9,3)\n"
            "platform(2,7,3)\nplatform(3,2,3)", 10, 16, validate_markers=False,
        )
        stand = standable_cells(climb.grid)
        reached = reachable_cells(climb.grid, climb.spawn, DEFAULT_MOVEMENT)
        frontier, nearest = _locate_break(stand, reached, (4, 1))
        # frontier is the reached cell nearest the HIGH target — small y, not
        # the bottom-right floor corner the old max-column pick returned.
        assert frontier[1] <= 7
        assert max(c[1] for c in reached) >= 13  # the floor WAS reached, but...
        assert frontier[1] < max(c[1] for c in reached)  # ...not chosen

    def test_auto_bridge_bridges_a_vertical_gap(self) -> None:
        vg = stamp(
            "floor(0,11)\nspawn(2)\nplatform(2,13,3)\nplatform(4,11,3)\n"
            "platform(2,4,3)", 12, 18, validate_markers=False,
        )
        summit = place_exit(vg.grid, exclude={vg.spawn}, axis="vertical")
        grid, bridges, problems = auto_bridge_grid(
            vg.grid, vg.spawn, summit, DEFAULT_MOVEMENT
        )
        assert bridges and not problems  # was: junk bridge + fallback
        assert summit in reachable_cells(grid, vg.spawn, DEFAULT_MOVEMENT)

    def test_owner_routes_by_the_break_gap_not_the_target(self) -> None:
        from examples.platformer_pack.level import _owner_of_problem

        plan = plan_level(132, 16, 3, derive_rng("s", "p", "l")).sections
        msg = (
            "exit at (129, 14) is not reachable from spawn (2, 14). The player "
            "gets as far as (40, 10) but cannot reach the next foothold at "
            "(48, 8): ... [break@44,9]"
        )
        owner = _owner_of_problem(plan, [msg], "horizontal")
        # routes to the GAP's section (~col 44), NOT the target's (col 129).
        ps = plan[owner]
        assert ps.x_off <= 44 < ps.x_off + ps.length
        assert owner != len(plan) - 1  # not the far-right target section

    def test_whole_fallback_is_axis_aware(self) -> None:
        """G6: the last-resort whole fallback is a climbable LADDER for a
        vertical level (summit exit, reachable from the base) — not a
        degenerate horizontal floor."""
        from examples.platformer_pack.level import _whole_fallback
        from examples.platformer_pack.tiles import DEFAULT_TILES

        v = _whole_fallback(18, 60, "vertical", DEFAULT_TILES)
        assert v.spawn is not None and v.exit is not None
        assert v.exit[1] < v.spawn[1]  # exit is ABOVE the spawn (a summit)
        assert v.exit in reachable_cells(v.grid, v.spawn, DEFAULT_MOVEMENT)

        h = _whole_fallback(48, 16, "horizontal", DEFAULT_TILES)
        assert h.spawn is not None and h.exit is not None
        assert h.exit[0] > h.spawn[0]  # exit to the right of the spawn

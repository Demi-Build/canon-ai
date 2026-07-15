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

    def test_shift_translates_into_the_receiving_frame(self) -> None:
        """§1b seam frame bug: a vertical seam printed the LOWER section's
        local rows (y=1..5), which read as the RECEIVER's own top — the
        shift translates them so 'lowest platforms near these footholds'
        means the receiver's BOTTOM rows."""
        # A ladder rung near the giver's top: standing cell at y=2.
        res = stamp(
            "platform(3,3,3)", 20, 28, validate_markers=False
        )
        raw = seam_summary(res, axis="vertical")
        assert "y=2" in raw
        # Receiver sits one section above: len_receiver=28, overlap=6 →
        # shift dy = len_receiver - overlap = 22 → the same foothold is the
        # receiver's row 24 (its bottom band).
        shifted = seam_summary(res, axis="vertical", shift=(0, 22))
        assert "y=24" in shifted


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

    def test_place_exit_allows_elevated_right_edge(self) -> None:
        """The plat_seaside5 l8 finding: a level whose right side is RAISED
        terrain (the entire ground-level standing row occupied) used to exit
        at the last ground-level column — a third of the map. Elevated
        right-edge terrain is a legal exit spot now (the horizontal analogue
        of the vertical summit); a flat level's exit is unchanged."""
        res = stamp(
            "floor(0,19)\nspawn(2)\n"
            + "\n".join(f"wall({x},12,13)" for x in range(10, 20)),
            20, 16, validate_markers=False,
        )
        # Old rule: (9, 13) — the last ground column. New: atop the terrain.
        assert place_exit(res.grid, exclude={res.spawn}) == (19, 11)
        # Classic flat level: identical to the old behavior.
        flat = stamp("floor(0,19)\nspawn(2)", 20, 16, validate_markers=False)
        assert place_exit(flat.grid, exclude={flat.spawn}) == (19, 13)

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


class TestReachabilityRepairEscalation:
    """§1b climb fix: reachability is COMPUTABLE, so auto_bridge_grid
    escalates (bridge → mount-open → climb lane) instead of bouncing an
    unfixable 'No stepping platform fits here' back to the LLM — the loop
    that whole-laddered every plat_seaside3 vertical climb."""

    def test_slab_beyond_jump_height_is_computably_healed(self) -> None:
        """A full-width solid slab mid-shaft (a model laying its section
        floor without pitting it) is unfixable by ADDING platforms alone —
        the old code returned 'No stepping platform fits here' and burned the
        LLM regen rounds. The escalation must HEAL it (bridges up to the
        slab, then a mount-open through it / a carved lane past it)."""
        res = stamp(
            "floor(0,9)\nspawn(2)\nledge(0,9,10)", 10, 20,
            validate_markers=False,
        )
        summit = place_exit(res.grid, exclude={res.spawn}, axis="vertical")
        assert summit is not None and summit[1] < 10
        grid, added, problems = auto_bridge_grid(
            res.grid, res.spawn, summit, DEFAULT_MOVEMENT
        )
        assert problems == [], f"the slab should heal computably: {problems}"
        assert any(n.startswith("# repair") for n in added), added
        assert summit in reachable_cells(grid, res.spawn, DEFAULT_MOVEMENT)

    def test_sealed_foothold_gets_mount_opened(self) -> None:
        """A foothold within jump height whose own SOLID support seals the
        shaft (solid ledge rungs — the l4 break class): the support strip is
        converted to one-way so the arc mounts THROUGH it."""
        res = stamp(
            "floor(0,9)\nspawn(2)\nledge(0,9,15)", 10, 20,
            validate_markers=False,
        )
        summit = place_exit(res.grid, exclude={res.spawn}, axis="vertical")
        assert summit == (0, 14)  # standing on the sealing ledge
        grid, added, problems = auto_bridge_grid(
            res.grid, res.spawn, summit, DEFAULT_MOVEMENT
        )
        assert problems == [], f"mount-open should heal the seal: {problems}"
        assert any("mount-open" in n for n in added), added
        assert summit in reachable_cells(grid, res.spawn, DEFAULT_MOVEMENT)

    def test_lane_clears_hazard_row_and_drops_its_records(self) -> None:
        """A hazard row crossing the only climbable shaft (the l9 break
        class: G7 hazard→pit conversions stacking at a seam): standing cells
        inside a hazard row are unusable and rungs can't be placed in it, so
        the LANE must clear the notch — and the cleared cells' hazard RECORDS
        must be dropped so no invisible damage cell survives."""
        from canon.bible.platformer import SparseMaskEntry
        from examples.platformer_pack.tiles import DEFAULT_TILES

        res = stamp("floor(0,9)\nspawn(2)", 10, 20, validate_markers=False)
        floor_id = DEFAULT_TILES.by_name["floor"].id
        spike = DEFAULT_TILES.by_name["spike"].id
        # Solid block rows 0..16 everywhere EXCEPT a 2-column shaft at 3-4
        # (row 0 included, so no standing spot exists on top of the block);
        # a ledge inside the shaft at row 9 is the only high foothold.
        res.grid[0:17, 0:3] = floor_id
        res.grid[0:17, 5:10] = floor_id
        res.grid[9, 3:5] = floor_id
        # The hazard row crosses the shaft below the ledge.
        res.grid[12, 3:5] = spike
        records = [SparseMaskEntry(x=x, y=12, type="spike") for x in (3, 4)]
        summit = place_exit(res.grid, exclude={res.spawn}, axis="vertical")
        assert summit == (3, 8)  # standing on the shaft ledge
        grid, added, problems = auto_bridge_grid(
            res.grid, res.spawn, summit, DEFAULT_MOVEMENT, hazards=records
        )
        assert problems == [], f"the hazard-sealed shaft should heal: {problems}"
        assert any("climb-lane" in n for n in added), added
        assert summit in reachable_cells(grid, res.spawn, DEFAULT_MOVEMENT)
        # The shaft's hazard cells were cleared AND their records dropped —
        # no record may survive on a non-hazard cell.
        hz = DEFAULT_TILES.ids("hazard")
        assert records == [] or all(
            int(grid[e.y, e.x]) in hz for e in records
        )
        assert len(records) < 2, f"hazard records not dropped: {records}"

    def test_paid_climb_traces_stitch_clean(self) -> None:
        """THE §1b oracle: the three plat_seaside3 vertical climbs (every
        section passed locally, all three shipped the whole-ladder fallback)
        must now stitch CLEAN on the first pass — real content, sim-verified
        summit path, placed checkpoint, no stale hazard records."""
        import json
        from pathlib import Path

        from examples.platformer_pack.level import (
            _SectionState,
            _stitch_and_repair,
        )
        from examples.platformer_pack.rules import DEFAULT_RULES
        from examples.platformer_pack.sections import PlannedSection
        from examples.platformer_pack.tiles import DEFAULT_TILES

        fixture = json.loads(
            (
                Path(__file__).parent / "fixtures/plat_seaside3_climbs.json"
            ).read_text()
        )
        assert set(fixture) == {"l4", "l7", "l9"}
        for lid, spec in fixture.items():
            w, h = spec["width"], spec["height"]
            states = []
            for s in spec["sections"]:
                ps = PlannedSection(
                    s["archetype"], s["length"],
                    x_off=s["x_off"], y_off=s["y_off"],
                )
                res = stamp(
                    s["dsl"], w, s["length"], tiles=DEFAULT_TILES,
                    validate_markers=False,
                )
                states.append(
                    _SectionState(
                        ps=ps, dsl=s["dsl"], res=res,
                        fell_back=False, attempts=[],
                    )
                )
            whole, bridges, snaps, problems = _stitch_and_repair(
                states, w, h, "vertical", DEFAULT_MOVEMENT, DEFAULT_RULES,
                DEFAULT_TILES, spec["checkpoint_sections"],
            )
            assert problems == [], f"{lid} still fails: {problems}"
            # Real design shipped, not a 3-row-signature ladder (the shipped
            # fallbacks measured 116-138 cells / 3 signatures; the real
            # stitches measure 587-892 / 66-80).
            above = int((whole.grid[: h - 2, :] != 0).sum())
            sigs = len({tuple(whole.grid[y, :]) for y in range(h - 2)})
            assert above > 300, f"{lid}: only {above} above-floor cells"
            assert sigs > 20, f"{lid}: only {sigs} distinct rows"
            reached = reachable_cells(
                whole.grid, whole.spawn, DEFAULT_MOVEMENT
            )
            assert whole.exit in reached, f"{lid}: summit not reachable"
            hz = DEFAULT_TILES.ids("hazard")
            stale = [
                (e.x, e.y)
                for e in whole.hazards
                if int(whole.grid[e.y, e.x]) not in hz
            ]
            assert stale == [], f"{lid}: stale hazard records {stale}"
            cps = [t for t in whole.triggers if t.type == "checkpoint"]
            assert len(cps) == len(spec["checkpoint_sections"]), lid


class TestSequentialHandoff:
    """User-locked 2026-07-14: each section prompt carries the immediate
    predecessor's full DSL rebased into the receiver's frame + everything
    occupying the shared band + digests of the level so far."""

    def test_rebase_dsl_shifts_every_coordinate_role(self) -> None:
        from examples.platformer_pack.dsl import rebase_dsl

        src = (
            "floor(0,20)\nplatform(10,10,4)\nledge(13,16,11)\n"
            "wall(3,4,9)\ncarve(1,2,3,4)\nvolume(water,5,8,12)\n"
            "water_cloud(2,3,6,7)\nhazard_strip(spike,6,9)\nreward(4,5)\n"
            "spawn(2)"
        )
        out = rebase_dsl(src, -15, 0)
        assert "floor(-15,5)" in out
        assert "platform(-5,10,4)" in out  # len NOT shifted
        assert "ledge(-2,1,11)" in out  # y NOT shifted at dx-only
        assert "wall(-12,4,9)" in out  # rows untouched
        assert "carve(-14,2,-12,4)" in out
        assert "volume(water,-10,-7,12)" in out  # name arg rides through
        assert "water_cloud(-13,3,-9,7)" in out
        assert "hazard_strip(spike,-9,-6)" in out
        assert "reward(-11,5)" in out and "spawn(-13)" in out
        # Vertical shift moves rows only.
        vout = rebase_dsl("platform(3,5,2)\nledge(0,4,9)", 0, 22)
        assert "platform(3,27,2)" in vout and "ledge(0,4,31)" in vout

    def test_rebase_dsl_drops_junk_lines(self) -> None:
        from examples.platformer_pack.dsl import rebase_dsl

        out = rebase_dsl("floor(0,5)\nnot a dsl line\nplatform(2,3,2)", 1, 0)
        assert out == "floor(1,6)\nplatform(3,3,2)"

    def test_overlap_occupancy_lists_categories_in_receiver_frame(
        self,
    ) -> None:
        from examples.platformer_pack.level import overlap_occupancy
        from examples.platformer_pack.tiles import DEFAULT_TILES

        # A 20-wide section: wall + spike + water near its RIGHT edge.
        res = stamp(
            "floor(0,19)\nwall(16,10,13)\nhazard_strip(spike,17,18)\n"
            "water_cloud(15,5,18,7)",
            20, 16, validate_markers=False,
        )
        # Receiver sits at x_off 14 relative to this section (advance
        # 20-6): shift = -14 puts the shared band at receiver cols 0..5.
        s = overlap_occupancy(
            res, axis="horizontal", tiles=DEFAULT_TILES, shift=(-14, 0)
        )
        assert "SOLID" in s and "hazard" in s and "liquid" in s
        assert "x 2" in s  # the wall at giver-16 is receiver-2
        # An empty band says so.
        empty = stamp("floor(0,7)", 20, 16, validate_markers=False)
        empty.grid[:, :] = 0
        s2 = overlap_occupancy(empty, axis="horizontal", tiles=DEFAULT_TILES)
        assert "open air" in s2

    def test_overlap_occupancy_incoming_side_for_successors(self) -> None:
        from examples.platformer_pack.level import overlap_occupancy
        from examples.platformer_pack.tiles import DEFAULT_TILES

        # Successor's content near its LEFT edge (the band it shares with
        # the section being regenerated before it).
        res = stamp(
            "floor(0,19)\nwall(2,10,13)", 20, 16, validate_markers=False,
        )
        s = overlap_occupancy(
            res, axis="horizontal", tiles=DEFAULT_TILES,
            shift=(14, 0), side="incoming",
        )
        assert "x 16" in s  # giver-2 lands at receiver-16 (the right band)


class TestL2CorridorTrap:
    """plat_seaside4 l2 (the one whole-fallback of the H-series validation
    run): a seam corridor trap — s0's capped ledge + s1's left-edge tower —
    that no additive/mount/lane repair can open and whose terminal
    single-owner rescue used to lose to the NEIGHBOUR's overlap content.
    The trio: headroom-qualified targets, takeoff-column lane, and the
    both-neighbours terminal fallback that keeps s2's real content."""

    def _fixture(self):
        import json
        from pathlib import Path

        return json.loads(
            (
                Path(__file__).parent / "fixtures/plat_seaside4_l2.json"
            ).read_text()
        )

    def test_break_locator_skips_one_tall_pockets(self) -> None:
        """The old run burned every repair round aiming at (15,12) — a
        standable cell under a solid ledge no 2-tall body fits into. The
        located break must now name a body-usable foothold."""
        from examples.platformer_pack.level import (
            _SectionState,
            _stitch_and_repair,
        )
        from examples.platformer_pack.movement import DEFAULT_MOVEMENT
        from examples.platformer_pack.rules import DEFAULT_RULES
        from examples.platformer_pack.sections import PlannedSection
        from examples.platformer_pack.tiles import DEFAULT_TILES

        fx = self._fixture()
        w, h = fx["width"], fx["height"]
        states = [
            _SectionState(
                ps=PlannedSection(
                    s["archetype"], s["length"],
                    x_off=s["x_off"], y_off=s["y_off"],
                ),
                dsl=s["dsl"],
                res=stamp(
                    s["dsl"], s["length"], h, tiles=DEFAULT_TILES,
                    validate_markers=False,
                ),
                fell_back=False,
                attempts=[],
            )
            for s in fx["sections"]
        ]
        whole, bridges, snaps, problems = _stitch_and_repair(
            states, w, h, "horizontal", DEFAULT_MOVEMENT, DEFAULT_RULES,
            DEFAULT_TILES, fx["checkpoint_sections"],
        )
        # Still a genuine design failure (the trap is real)...
        assert problems
        # ...but no longer fixated on the 1-tall pocket: the named foothold
        # must have headroom.
        assert "(15, 12)" not in problems[0]

    def test_terminal_neighbor_fallback_saves_the_real_sections(
        self,
    ) -> None:
        """Drive the REAL _generate_sectioned_level with a scripted LLM that
        always answers with the paid run's section designs — the whole-level
        rounds burn exactly like the paid run, but the terminal path now
        falls back the owner AND its seam neighbour, so s2's real islands
        content ships instead of a whole-flat level."""
        import re as _re

        from examples.platformer_pack.level import _generate_sectioned_level
        from examples.platformer_pack.movement import DEFAULT_MOVEMENT
        from examples.platformer_pack.rules import DEFAULT_RULES
        from examples.platformer_pack.tiles import DEFAULT_TILES

        fx = self._fixture()
        w, h = fx["width"], fx["height"]
        by_index = {i: s["dsl"] for i, s in enumerate(fx["sections"])}

        class _ScriptedLLM:
            def generate(self, request, phase=""):
                m = _re.search(
                    r"### SECTION: \w+ \((\d+) of \d+\)", request.user_message
                )
                return by_index[int(m.group(1)) - 1]

        class _Cfg:
            max_retries = 3
            seed = "<same hostile seed>"

        from examples.platformer_pack import PlatformerPrompts

        class _Ctx:
            config = _Cfg()
            llm = _ScriptedLLM()
            prompts = PlatformerPrompts()

        result, dsl_text, fell_back, attempts, bridges, snaps, diag = (
            _generate_sectioned_level(
                _Ctx(), level_id="l2", brief="b",
                knobs={"difficulty": fx["difficulty"]},
                width=w, height=h, axis="horizontal",
                movement=DEFAULT_MOVEMENT, rules=DEFAULT_RULES,
                tiles=DEFAULT_TILES, seed=_Cfg.seed,
                phase_name="plat:layout",
            )
        )
        assert fell_back is True  # the trap is real; sections DID fall back
        assert diag["whole_fallback"] is False, (
            "the neighbour rescue should beat the whole-level nuke"
        )
        rounds = diag["stitch_rounds"]
        assert any("terminal_neighbor_fallback" in r for r in rounds)
        assert rounds[-1]["problems"] == []
        # s2 (islands, the last ~40 columns) survives with real content
        # above the floor rows.
        s2 = fx["sections"][2]
        lo = s2["x_off"]
        assert (result.grid[: h - 2, lo:] != 0).any(), (
            "the real islands section was nuked"
        )


class TestDoorwayCarve:
    """The climb lane's horizontal analogue (found live by the plat_seaside4
    l8 replay): a lateral break whose span is a WALL RUN gets a body-height
    doorway carved through it."""

    def test_walled_lateral_break_gets_a_doorway(self) -> None:
        from examples.platformer_pack.tiles import DEFAULT_TILES

        # A 4-column full-height wall run splitting a flat floor.
        res = stamp(
            "floor(0,19)\nspawn(2)\n" + "\n".join(
                f"wall({c},0,13)" for c in range(8, 12)
            ),
            20, 16, validate_markers=False,
        )
        exit_ = place_exit(res.grid, DEFAULT_TILES, exclude={res.spawn})
        assert exit_ is not None and exit_[0] > 12
        grid, added, problems = auto_bridge_grid(
            res.grid, res.spawn, exit_, DEFAULT_MOVEMENT
        )
        assert problems == [], f"the doorway should heal it: {problems}"
        assert any("doorway" in n for n in added), added
        assert exit_ in reachable_cells(grid, res.spawn, DEFAULT_MOVEMENT)
        # The wall survives above the doorway (a door, not a demolition).
        assert any(int(grid[y, 9]) != 0 for y in range(0, 10))

    def test_wide_blocks_stay_design_failures(self) -> None:
        """The doorway is capped (8 columns) — a 10-column solid block is a
        DESIGN failure the owning section must fix, not a tunnel-boring job."""
        from examples.platformer_pack.tiles import DEFAULT_TILES

        res = stamp(
            "floor(0,29)\nspawn(2)\n" + "\n".join(
                f"wall({c},0,13)" for c in range(10, 20)
            ),
            30, 16, validate_markers=False,
        )
        exit_ = place_exit(res.grid, DEFAULT_TILES, exclude={res.spawn})
        grid, added, problems = auto_bridge_grid(
            res.grid, res.spawn, exit_, DEFAULT_MOVEMENT
        )
        assert problems, "a 10-wide block must stay a design failure"

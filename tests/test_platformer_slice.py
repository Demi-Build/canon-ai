"""Tests for the platformer vertical slice (examples/platformer_pack).

Covers the deterministic core (DSL/stamp/validators/colors), the schema
files loading through canon.skeleton.loader, and the end-to-end fake-backend
run: tree shape, byte-determinism, and the §6.3 hash-recompute contract.
"""

from __future__ import annotations

import hashlib
import json
import random
import re
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("PIL")

from canon.backends.testing import FakeLLMBackend  # noqa: E402
from canon.bible.models import Bible  # noqa: E402
from canon.bible.platformer import TileType  # noqa: E402
from canon.config import CanonConfig  # noqa: E402
from canon.llm.client import LLMClient  # noqa: E402
from canon.pipeline.runner import PipelineContext, run_pipeline  # noqa: E402
from canon.skeleton.loader import load_skeleton_spec  # noqa: E402
from examples.platformer_pack import PlatformerPrompts, compose_pipeline  # noqa: E402
from examples.platformer_pack.dsl import DslError, parse_dsl, stamp  # noqa: E402
from examples.platformer_pack.movement import DEFAULT_MOVEMENT  # noqa: E402
from examples.platformer_pack.phases import SCHEMAS_DIR, placeholder_color  # noqa: E402
from examples.platformer_pack.tiles import TileRegistry, load_tiles  # noqa: E402
from examples.platformer_pack.validate import (  # noqa: E402
    check_level,
    check_placements,
    standable_cells,
    volume_cells,
)
from examples.platformer_pack.variants import load_variants  # noqa: E402
from examples.run_platformer_slice import (  # noqa: E402
    _FAKE_LAYOUTS,
    _REFERENCE_DIMS,
    make_fake_responder,
)
from tests.treediff import assert_trees_byte_identical, tree_files  # noqa: E402

W, H = 48, 16
#: Schema-rolled dims per level (level_layout.json lookups).
LEVEL_DIMS = {"l1": (48, 16), "l2": (56, 16), "l3": (64, 18)}


# ---------------------------------------------------------------------------
# DSL + stamp
# ---------------------------------------------------------------------------


class TestDsl:
    def test_stamp_deterministic(self) -> None:
        a = stamp(_FAKE_LAYOUTS["l1"], W, H)
        b = stamp(_FAKE_LAYOUTS["l1"], W, H)
        assert (a.grid == b.grid).all()
        assert a.spawn == b.spawn and a.exit == b.exit

    def test_agents_never_touch_cells(self) -> None:
        """I3: the DSL string fully determines the grid; ops are the only
        surface. Sanity-check the semantic mapping."""
        result = stamp("floor(0,47)\nspike(10,11)\nspawn(2)\nexit(45)", W, H)
        assert int(result.grid[H - 2, 5]) == TileType.FLOOR
        assert int(result.grid[H - 3, 10]) == TileType.SPIKE
        assert result.spawn == (2, H - 3)
        assert len(result.hazards) == 2

    @pytest.mark.parametrize(
        ("text", "match"),
        [
            ("flor(0,4)", "unknown op"),
            ("floor(0)", "takes 2 args"),
            # The l8 fallback: a 4-arg wall — the rejection must name the
            # exact signature so the model stops adding a fourth number.
            ("floor(0,20)\nwall(5,8,12,14)\nspawn(2)\nexit(18)",
             r"wall takes 3 args \(x, y1, y2\)"),
            ("floor(0,x)", "must be integers"),
            ("floor(0,47)\nspawn(2)", "missing exit"),
            ("floor(0,47)\nexit(45)", "missing spawn"),
            ("floor(0,47)\nspawn(2)\nspawn(3)\nexit(4)", "more than once"),
            # (spike over no-ground now AUTO-CONVERTS to a pit — see
            # TestSteppedSlopes.test_hazard_strip_over_gap_becomes_a_pit.
            # A pour with no basin, an OOB ledge row, and a pool over
            # damaged floor auto-REPAIR now — see the tests below.)
            ("gibberish", "not a valid op"),
        ],
    )
    def test_strict_errors(self, text: str, match: str) -> None:
        with pytest.raises(DslError, match=match):
            stamp(text, W, H)

    def test_volume_without_basin_becomes_free_water(self) -> None:
        """§1b #3 (the l8 whole-flat killer): a contained pour over a gap
        used to hard-raise 'no solid basin' and the model never converged.
        The bottomless columns are now re-interpreted as FREE water (the
        deliberate-spout semantics of water_wall-over-pit), recorded."""
        res = stamp(
            "floor(0,47)\ngap(20,24)\nwater(20,24,12)\nspawn(2)\nexit(45)",
            W, H,
        )
        assert any("no solid basin" in r for r in res.repairs)
        assert int(res.grid[12, 22]) == TileType.WATER  # the water stays
        assert (22, 12) in res.free_volume  # containment-exempt

    def test_oob_ledge_row_is_clamped(self) -> None:
        """§1b #2: 'ledge: row 15 outside 1..13' was the #1 remaining raise
        class (worst on narrow shafts) — the row now clamps in, recorded."""
        res = stamp("floor(0,47)\nledge(5,9,15)\nspawn(2)\nexit(45)", W, H)
        assert any("clamped" in r for r in res.repairs)
        assert int(res.grid[H - 3, 7]) == TileType.FLOOR  # at the clamp row

    def test_pool_over_damaged_floor_is_clipped(self) -> None:
        """The sunlit-run l3 class: the program lays floor, then its own
        wall/gap overwrite/remove it, then pool() overlaps the damage. The
        pool now CLIPS to the floored columns (the model shuffled the water
        op for three retries without touching its own gap) — the repair
        note still names what occupies each dropped column."""
        text = (
            "floor(0,47)\ngap(18,21)\nwall(17,10,14)\n"
            "pool(water,17,22)\nspawn(2)\nexit(45)"
        )
        res = stamp(text, W, H)
        note = next(r for r in res.repairs if r.startswith("pool("))
        assert "17 (wall)" in note
        assert "18 (empty)" in note and "21 (empty)" in note
        assert "clipped" in note
        assert int(res.grid[H - 2, 22]) == TileType.WATER  # kept column
        assert int(res.grid[H - 2, 18]) == 0  # gap column stays a gap

    def test_parse_accepts_semicolons_and_comments(self) -> None:
        ops = parse_dsl("# a comment\nfloor(0,10); spawn(2)\nexit(8)")
        assert [op for op, _ in ops] == ["floor", "spawn", "exit"]

    def test_all_canned_layouts_valid(self) -> None:
        # Canned layouts are authored against the schema's per-level dims.
        for level_id, dsl_text in _FAKE_LAYOUTS.items():
            width, height = LEVEL_DIMS[level_id]
            result = stamp(dsl_text, width, height)
            problems = check_level(
                result.grid, result.spawn, result.exit, DEFAULT_MOVEMENT,
                free_volume=result.free_volume,
            )
            assert not problems, f"{level_id}: {problems}"


# ---------------------------------------------------------------------------
# Validators
# ---------------------------------------------------------------------------


class TestValidators:
    def test_rising_costs_horizontal_range(self) -> None:
        """``max_dx_for_rise`` (round-4 play test: 'platforms too high') is
        now the CONSERVATIVE FEEDBACK vocabulary — the reachability decision
        itself is the jump-arc simulation, which is if anything more generous
        on diagonals. These values still drive the located-fix message so it
        teaches the rising-costs-range constraint. Values from the shared
        ballistic model."""
        from examples.platformer_pack.movement import max_dx_for_rise

        assert max_dx_for_rise(DEFAULT_MOVEMENT, 3) == 3  # full rise: close
        assert max_dx_for_rise(DEFAULT_MOVEMENT, 2) == 4
        assert max_dx_for_rise(DEFAULT_MOVEMENT, 1) == 4  # capped at width
        assert max_dx_for_rise(DEFAULT_MOVEMENT, 0) == 4  # flat: box rule
        assert max_dx_for_rise(DEFAULT_MOVEMENT, 4) == -1  # over max rise

        # A rise-3 platform 6 columns out is beyond even a running jump's
        # arc — the simulation flags the break and the message teaches the
        # rising-costs-range constraint. (platform row 11 -> stand atop row
        # 10 = rise 3 from row 13; dx 6 from the col-10 frontier.)
        result = stamp(
            "floor(0,10)\nplatform(16,11,4)\nfloor(30,47)\n"
            "spawn(2)\nexit(45)", W, H,
        )
        problems = check_level(
            result.grid, result.spawn, result.exit, DEFAULT_MOVEMENT
        )
        assert problems and "rising costs range" in problems[0]
        # A rise-3 step 3 columns out (within the arc) is makeable.
        near = stamp(
            "floor(0,10)\nplatform(13,11,4)\nledge(19,26,9)\n"
            "floor(30,47)\nspawn(2)\nexit(45)", W, H,
        )
        assert not check_level(
            near.grid, near.spawn, near.exit, DEFAULT_MOVEMENT
        )

    def test_run_jump_width_derives_from_physics(self) -> None:
        """Ticket 4: the running-jump clearance the prompts advertise is
        DERIVED from the same ballistic model the simulation integrates
        (airtime at run_speed, COMFORT-shaved) — not a hardcoded literal.
        Default physics keeps the familiar 6; lighter gravity hangs the
        player longer and widens it, heavier gravity shrinks it."""
        from examples.platformer_pack.movement import (
            PlayerMovementSpec,
            run_jump_width,
        )

        assert run_jump_width(DEFAULT_MOVEMENT) == 6
        low_g = PlayerMovementSpec.model_validate(
            {**DEFAULT_MOVEMENT.model_dump(), "gravity": 20.0}
        )
        heavy = PlayerMovementSpec.model_validate(
            {**DEFAULT_MOVEMENT.model_dump(), "gravity": 60.0}
        )
        assert run_jump_width(low_g) > 6  # the low-gravity finale case
        assert run_jump_width(heavy) < 6

    def test_validator_approved_jumps_land_in_harness_physics(self) -> None:
        """Validator ↔ play-surface PARITY, proven by simulation: replay
        the pygame/Godot integration — WITH run-up momentum (the old
        version of this test used the pre-momentum instant-speed loop, so
        a green suite was false confidence) — for every boundary jump the
        feedback vocabulary promises. ``max_dx_for_rise`` is what prompts
        and located-fix messages tell the model is jumpable; each promise
        must land in the real physics GIVEN the documented run-up."""
        m = DEFAULT_MOVEMENT

        def simulate(dx_cells: int, rise: int, runway_cells: float) -> bool:
            # Mirrors examples/platformer_play.py's momentum step: vx
            # accelerates on the GROUND toward run_speed across the runway,
            # the jump PRESERVES vx, air control is weak (air_accel), then
            # gravity + the landing check, same constants and frame order.
            g, dt = m.gravity, 1.0 / 60.0
            vx, px = 0.0, -float(runway_cells)
            while px < 0.0:  # the run-up, holding RUN toward the edge
                vx = min(vx + m.ground_accel * dt, m.run_speed)
                px += vx * dt
                if vx <= 0.0:
                    break  # no accel configured: dead stop at the edge
            v0 = (2.0 * g * (m.jump_height + 0.4)) ** 0.5
            platform_row = 13 - rise + 1  # stand atop = 13 - rise
            py, vy = 13.0, -v0
            for _ in range(240):
                vx = min(vx + m.air_accel * dt, m.run_speed)  # weak drift
                px += vx * dt
                vy += g * dt
                prev_bottom = py + 0.99
                new_y = py + vy * dt
                feet = new_y + 0.99
                over = (px + 0.85 >= dx_cells) and (px + 0.15 < dx_cells + 4)
                if (
                    vy > 0
                    and over
                    and int(feet) == platform_row
                    and prev_bottom <= float(platform_row)
                ):
                    return True
                if new_y > 13.0:  # fell back without landing
                    return False
                py = new_y
            return False

        from examples.platformer_pack.movement import max_dx_for_rise

        # Every rise the vocabulary allows, at its maximum promised dx,
        # must land with the documented run-up available.
        for rise in range(1, m.jump_height + 1):
            dx = max_dx_for_rise(m, rise)
            assert simulate(dx, rise, m.run_up_cells), (
                f"the vocabulary promises rise {rise} at dx {dx} but the "
                "harness physics cannot land it — parity broken"
            )
        # Clearly-outside jumps must fail even with a generous runway.
        assert not simulate(6, 3, 8.0)
        # And the promises must NOT assume a run-up that isn't there: a
        # dead-stop rise-1 hop at dx 1 (the minimum the prompts rely on —
        # 'each foothold within jump height AND close horizontally').
        assert simulate(1, 1, 0.0)

    def test_brake_and_air_momentum_rules(self) -> None:
        """The reworked momentum (ticket 1), mirroring the byte-identical
        platformer_play.py / main.gd step: a REVERSAL brakes at brake_accel
        (a decisive turnaround, quicker than the gentle build-up); air control
        builds toward the target but NEVER sheds same-direction overspeed
        (momentum carries); an air reversal is the one exception — a WEAK
        air-brake at 0.3x."""
        m = DEFAULT_MOVEMENT
        dt = 1.0 / 60.0
        assert m.brake_accel > m.ground_accel  # braking is decisive
        assert m.air_accel < m.ground_accel  # speed is built on the ground

        def step(vx: float, dx: int, gmove: bool) -> float:
            desired = dx * m.run_speed
            if vx * dx < 0.0:
                rate = (m.brake_accel if gmove else 0.3 * m.brake_accel) * dt
            elif gmove:
                rate = m.ground_accel * dt
            elif abs(vx) < abs(desired):
                rate = m.air_accel * dt
            else:
                rate = 0.0
            if rate:
                vx = (
                    min(vx + rate, desired) if vx < desired
                    else max(vx - rate, desired)
                )
            return vx

        # ground reversal sheds MORE per frame than a same-direction build
        shed_brake = m.run_speed - step(m.run_speed, -1, True)
        gained_build = step(0.0, 1, True) - 0.0
        assert shed_brake > gained_build
        # air, same direction at/over target: momentum HELD (no shed)
        assert step(m.run_speed, 1, False) == m.run_speed
        # air reversal DOES shed speed (the one exception) but weaker than
        # the ground brake
        shed_air = m.run_speed - step(m.run_speed, -1, False)
        assert 0.0 < shed_air < shed_brake

    def test_marker_error_names_floor_columns(self) -> None:
        """A real model probed spawn columns 2, 3, 4... into fallback —
        the error must say where floor actually is."""
        with pytest.raises(DslError, match=r"columns 20-30, 40-42"):
            stamp("floor(20,30)\nfloor(40,42)\nspawn(2)\nexit(25)", W, H)
        with pytest.raises(DslError, match=r"no ground floor exists yet"):
            stamp("spawn(2)\nexit(25)", W, H)

    def test_unreachable_exit_flagged(self) -> None:
        # A gap wider than jump_width with no stepping stones.
        result = stamp("floor(0,10)\nfloor(20,47)\nspawn(2)\nexit(45)", W, H)
        problems = check_level(result.grid, result.spawn, result.exit, DEFAULT_MOVEMENT)
        assert problems and "not reachable" in problems[0]

    def test_reachability_feedback_hands_over_the_fix(self) -> None:
        """The message must name the frontier, the unreachable foothold,
        the failing constraint, AND the literal op to add — location-free
        'add platforms' looped the real model into fallback, and a
        located-but-do-the-arithmetic version still did."""
        result = stamp("floor(0,10)\nfloor(20,47)\nspawn(2)\nexit(45)", W, H)
        problems = check_level(result.grid, result.spawn, result.exit, DEFAULT_MOVEMENT)
        message = problems[0]
        assert "as far as (10, 13)" in message  # frontier: edge of first floor
        assert "(20, 13)" in message  # nearest unreachable foothold
        assert "horizontal distance 10 exceeds max jump distance 4" in message
        assert "ADD THIS ONE LINE" in message
        assert "platform(14,12,2)" in message  # verified: cells free, arc ok
        assert "wider than 3 columns" in message

    def test_auto_bridge_repairs_reachability_in_code(self) -> None:
        """Code-for-computation: bridging a located break is arithmetic,
        so the TOOL appends the platforms — no LLM round-trip. Under run-up
        momentum the tool must build CLIMBABLE (offset) steps, since a jump
        can't go straight up onto a platform in its own column."""
        from examples.platformer_pack.validate import auto_bridge

        cases = (
            # The l3 real-run loop, now beyond a running jump's reach: a
            # 9-wide gap (dx 10 >> the ~7-cell simulated max) on the ground.
            "floor(0,30)\nfloor(40,47)\nspawn(2)\nexit(45)",
            # A high ledge (rise 7): the tool builds offset stepping platforms
            # up to it (a vertical stack would be unclimbable under momentum).
            "floor(0,10)\nledge(15,25,7)\nfloor(30,47)\nspawn(2)\nexit(45)",
        )
        for dsl_text in cases:
            repaired, added, problems = auto_bridge(
                dsl_text, W, H, DEFAULT_MOVEMENT
            )
            assert not problems, f"auto_bridge never converged: {problems}"
            assert added and all(op.startswith("platform(") for op in added)
            assert "# auto-bridge" in repaired  # authorship visible in DSL
            result = stamp(repaired, W, H)
            assert not check_level(
                result.grid, result.spawn, result.exit, DEFAULT_MOVEMENT
            )

    def test_simulation_rejects_jumps_into_impassable_terrain(self) -> None:
        """The reachability edge test is now a real jump-arc SIMULATION
        (can_reach): a jump into a cliff taller than the player can clear is
        rejected, but a WALL SHORT ENOUGH TO JUMP OVER is allowed (the old
        arc_clear heuristic over-conservatively rejected any solid in the
        flight band; simulation is more accurate). Hazards are flown over."""
        import numpy as np

        from examples.platformer_pack.validate import can_reach

        m = DEFAULT_MOVEMENT
        # A full-height cliff at col 3 (floor at row 8): impassable.
        g = np.zeros((10, 8), dtype=np.int8)
        g[8, :] = 1
        g[9, :] = 1
        for r in range(0, 8):
            g[r, 3] = 3
        assert not can_reach(g, (1, 7), (5, 7), m)  # jumps into the cliff
        # A low wall (top only 1 cell above the foothold) is cleared.
        g2 = np.zeros((10, 8), dtype=np.int8)
        g2[8, :] = 1
        g2[9, :] = 1
        for r in (6, 7):
            g2[r, 3] = 3
        assert can_reach(g2, (1, 7), (5, 7), m)  # hops the low wall
        # A hazard column is flown over, never a blocker.
        g3 = np.zeros((10, 8), dtype=np.int8)
        g3[8, :] = 1
        g3[9, :] = 1
        for r in range(5, 8):
            g3[r, 3] = 10  # spike column
        assert can_reach(g3, (1, 7), (5, 7), m)

    def test_l1_arc_blocked_exit_regression(self) -> None:
        """The exact clover_hills/l1 grid from the first paid run: its exit
        sat behind a 5-cell cliff and was 'reachable' only through jumps
        that fly into solid rock, so check_level returned [] and the level
        shipped UNBEATABLE. It must now report a break and be code-bridged.
        (Encoded: . empty, F floor, p platform, W wall, ^ spike, ~ water.)"""
        import re as _re

        import numpy as np

        from examples.platformer_pack.validate import (
            _locate_break,
            _suggest_bridge,
            can_reach,
            reachable_cells,
        )

        rows = [
            "...........................................",
            "...........................................",
            "...........................................",
            "...........................................",
            "...........................................",
            "...........................................",
            "...........................................",
            "...........................................",
            "......F.............................F......",
            ".....FF......F......................FF.....",
            "....FFF.ppp.FFFFFFF.................FFF....",
            "...FFFF....FFFFF....................FFFF...",
            "..FFFFF...FFFFFFF.............^^^...FFFFF..",
            "FFFFFFFFFFFFFFFFFFFF~~~~~~FFFFFFFFFFFFFFFFF",
            "WWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWW",
        ]
        dec = {".": 0, "F": 1, "p": 2, "W": 3, "^": 10, "~": 20}
        g = np.array([[dec[c] for c in r] for r in rows], dtype=np.int8)
        spawn, exit_ = (1, 12), (42, 12)
        m = DEFAULT_MOVEMENT

        # The two edges that faked reachability fly into the x36 cliff — the
        # simulation lands the player short of them, not on the far foothold.
        assert not can_reach(g, (35, 12), (38, 9), m)
        assert not can_reach(g, (35, 12), (39, 10), m)
        # So the exit is no longer falsely reachable, and the break is seen.
        assert exit_ not in reachable_cells(g, spawn, m)
        problems = check_level(g, spawn, exit_, m)
        assert problems and "not reachable" in problems[0]
        # And it is code-bridgeable: one suggested platform reconnects it
        # (this is one auto_bridge iteration, applied at the grid level).
        stand = standable_cells(g)
        reached = reachable_cells(g, spawn, m)
        frontier, nearest = _locate_break(stand, reached, exit_)
        op = _suggest_bridge(g, frontier, nearest, m)
        assert op is not None, "no valid bridge found for the l1 cliff"
        col, row, length = map(
            int, _re.match(r"platform\((\d+),(\d+),(\d+)\)", op).groups()
        )
        for i in range(length):
            g[row, col + i] = 2  # paint the one-way platform the tool chose
        assert exit_ in reachable_cells(g, spawn, m)

    def test_up_through_platform_is_unbeatable(self) -> None:
        """The l6 pathology: a shelf capped by a solid slab is 'reachable'
        under the old heuristic only via a straight-up jump THROUGH the slab
        (arc_clear short-circuited every same-column edge). The simulation
        bonks the slab's underside — the shelf is an island. A control with
        clear headroom above a one-way platform stays reachable, proving the
        strictness is aimed at capped columns, not at vertical ascents."""
        import numpy as np

        from examples.platformer_pack.validate import (
            can_reach,
            check_level,
            reachable_cells,
        )

        m = DEFAULT_MOVEMENT
        dec = {".": 0, "F": 1, "p": 2}
        capped = np.array([[dec[c] for c in r] for r in [
            ".........",   # shelf standable cells (atop the slab); exit (4,0)
            "FFFFFFFFF",   # full-width slab
            ".........",
            ".........",
            ".........",   # pocket floor's standable row; spawn (0,4)
            "FFFFFFFFF",   # floor
        ]], dtype=np.int8)
        assert (4, 0) not in reachable_cells(capped, (0, 4), m)
        assert not can_reach(capped, (0, 4), (4, 0), m)  # straight-up bonk
        problems = check_level(capped, (0, 4), (4, 0), m)
        assert problems and "not reachable" in problems[0]

        headroom = np.array([[dec[c] for c in r] for r in [
            ".........",   # clear sky above the platform
            "...pp....",   # one-way platform -> shelf (3,0)
            ".........",   # spawn (0,2) standable row
            "FFFFFFFFF",   # floor
        ]], dtype=np.int8)
        assert (3, 0) in reachable_cells(headroom, (0, 2), m)

    def test_mount_from_below_is_unbeatable(self) -> None:
        """The l4 pathology: a step up whose top is capped by a slab ONE
        cell above it. The old heuristic accepted the mount (adjacent
        columns leave no cells 'strictly between' for arc_clear to inspect);
        the simulation has the player bonk the ceiling and fall back. The
        identical step WITHOUT the cap is a normal, makeable jump — so the
        cap, not the mount, is what the simulation rejects."""
        import numpy as np

        from examples.platformer_pack.validate import can_reach, reachable_cells

        m = DEFAULT_MOVEMENT
        dec = {".": 0, "F": 1}
        capped = np.array([[dec[c] for c in r] for r in [
            ".........",
            "..FFFFF..",   # slab capping the step tops (cols 2..6)
            ".........",   # (4,2) target: standable atop the step, capped above
            "...FFF...",   # step solids
            "...FFF...",   # step base; spawn (0,4) on the floor
            "FFFFFFFFF",
        ]], dtype=np.int8)
        assert (4, 2) not in reachable_cells(capped, (0, 4), m)
        assert not can_reach(capped, (2, 4), (4, 2), m)

        uncapped = np.array([[dec[c] for c in r] for r in [
            ".........",
            ".........",   # no cap
            ".........",   # (4,2) standable atop the step
            "...FFF...",
            "...FFF...",
            "FFFFFFFFF",
        ]], dtype=np.int8)
        assert (4, 2) in reachable_cells(uncapped, (0, 4), m)

    def test_wide_gap_needs_run_up_momentum(self) -> None:
        """Run-up momentum: a wide (6-cell) bottomless PIT is crossable only
        with a running jump, which needs RUNWAY behind the takeoff. Given a
        long flat approach it's reachable; from a no-runway island takeoff it
        is NOT — the run-and-jump technique some gaps deliberately demand. A
        normal (3-cell) gap needs no run-up at all."""
        import numpy as np

        from examples.platformer_pack.validate import reachable_cells

        m = DEFAULT_MOVEMENT

        def scene(rows: list[str]):
            dec = {".": 0, "F": 1}
            w = max(len(r) for r in rows)
            return np.array(
                [[dec[c] for c in r.ljust(w, ".")] for r in rows], dtype=np.int8
            )

        # Long left floor (runway), 6-wide bottomless pit, right floor.
        run_up = scene([
            "...................",
            "FFFFFFFFFF......FFFF",
            "FFFFFFFFFF......FFFF",
        ])
        assert (17, 0) in reachable_cells(run_up, (0, 0), m)

        # Takeoff is a 1-cell island (pit on the approach side) → no runway →
        # only a weak standing jump → the 6-wide pit is uncrossable.
        no_runway = scene([
            "...................",
            "FFF.F......FFFFFFFF",
            "FFF.F......FFFFFFFF",
        ])
        assert (17, 0) not in reachable_cells(no_runway, (0, 0), m)

        # A 3-wide pit is an ordinary walk-jump — no run-up required.
        narrow = scene([
            "...................",
            "FFF...FFFFFFFFFFFFF",
            "FFF...FFFFFFFFFFFFF",
        ])
        assert (17, 0) in reachable_cells(narrow, (0, 0), m)

    def test_structural_roles_get_separated(self) -> None:
        """Ground/platform/wall within a few luminance points of each
        other (the second real palette) are indistinguishable in play —
        pairwise spacing is arithmetic, so a tool spreads them."""
        from examples.platformer_pack.style import (
            MIN_ROLE_SEPARATION,
            _luminance,
            enforce_contrast,
            separate_structural_roles,
        )
        from examples.platformer_pack.tiles import DEFAULT_TILES

        palette = {  # the real run's near-identical browns
            "background": "#1a1208", "ground": "#4b3b2b",
            "platform": "#6b4a2a", "wall": "#473b32", "breakable": "#a08050",
            "box": "#8a6a3a", "danger": "#c43a0a", "urchin": "#b9466e",
            "mine": "#aa5537", "water": "#4a3d1a",
        }
        out, adjusted = separate_structural_roles(palette, DEFAULT_TILES)
        assert adjusted, "near-identical structural roles must be spread"
        lums = sorted(_luminance(out[r]) for r in ("ground", "platform", "wall"))
        for a, b in zip(lums, lums[1:]):
            assert b - a >= MIN_ROLE_SEPARATION - 1, lums
        # And the background readability bar still holds afterwards.
        out, _ = enforce_contrast(out, DEFAULT_TILES)
        bg = _luminance(out["background"])
        for role in ("ground", "platform", "wall"):
            assert abs(_luminance(out[role]) - bg) >= 40 - 1

    def test_exit_relocates_to_rightmost_floored_column(self) -> None:
        """exit(x)'s x is advisory (play-test decision): the exit zone is
        the rightmost column with open ground floor — side-scroller
        'leave to the right' — and consumers win on the COLUMN, any row."""
        # Plain floor: exit lands on the last column, not the declared 45.
        result = stamp("floor(0,47)\nspawn(2)\nexit(45)", W, H)
        assert result.exit == (47, 13)
        # A spike on the last columns pushes the exit left of them.
        result = stamp("floor(0,47)\nspike(46,47)\nspawn(2)\nexit(45)", W, H)
        assert result.exit == (45, 13)
        # A pit at the right edge: exit lands before the pit.
        result = stamp("floor(0,47)\npit(44,47)\nspawn(2)\nexit(20)", W, H)
        assert result.exit == (43, 13)

    def test_snap_spawn_moves_to_valid_ground(self) -> None:
        """The first multi-stage run lost THREE levels to fallback whose
        final attempts failed ONLY on spawn placement — the exact column
        is arithmetic, same contract as checkpoint snapping. Both
        observed classes snap: no-floor-under-spawn (raised left edge)
        and standing-cell-covered (terrain stamped over the spawn)."""
        from examples.platformer_pack.validate import snap_spawn

        # No ground floor at column 2 (the real l3 attempt-1 message).
        text, moves = snap_spawn(
            "floor(10,47)\nspawn(2)\nexit(45)", W, H,
        )
        assert moves == ["spawn(2) -> spawn(10)"]
        result = stamp(text, W, H)
        assert result.spawn == (10, H - 3)

        # Terrain covers the spawn's standing cell (the real l2 message).
        covered = f"floor(0,47)\nledge(0,6,{H - 3})\nspawn(2)\nexit(45)"
        text, moves = snap_spawn(covered, W, H)
        assert moves == ["spawn(2) -> spawn(7)"]
        assert stamp(text, W, H).spawn == (7, H - 3)

        # A valid spawn is untouched; the exit column is never chosen.
        text, moves = snap_spawn("floor(0,47)\nspawn(2)\nexit(45)", W, H)
        assert moves == []
        text, moves = snap_spawn("floor(44,47)\nspawn(45)\nexit(45)", W, H)
        assert "spawn(45)" not in text and moves

    def test_snap_checkpoints_moves_to_valid_ground(self) -> None:
        """Checkpoint columns are a lookup, not a design decision — the
        second real run's l3 burned all three attempts on them while the
        validator recited the valid columns. Both observed classes snap:
        no-floor-under-it and standing-cell-occupied (spike)."""
        from examples.platformer_pack.validate import snap_checkpoints

        # Gap under column 22 (the real l1 attempt-1 message) → snaps out.
        text, moves = snap_checkpoints(
            "floor(0,19)\nfloor(26,47)\nspawn(2)\ncheckpoint(22)\nexit(45)",
            W, H,
        )
        assert moves == ["checkpoint(22) -> checkpoint(19)"]
        assert "checkpoint(19)" in text
        result = stamp(text, W, H)
        assert any(t.type == "checkpoint" for t in result.triggers)

        # Spike on the standing cell (the real l3 attempt-2 message).
        text, moves = snap_checkpoints(
            "floor(0,47)\nspike(30,32)\nspawn(2)\ncheckpoint(31)\nexit(45)",
            W, H,
        )
        assert moves == ["checkpoint(31) -> checkpoint(29)"]

        # Valid checkpoints are never touched.
        text, moves = snap_checkpoints(
            "floor(0,47)\nspawn(2)\ncheckpoint(24)\nexit(45)", W, H,
        )
        assert moves == [] and "checkpoint(24)" in text

        # Nothing valid within the bound: left for stamp's feedback.
        text, moves = snap_checkpoints(
            "floor(0,10)\nfloor(40,47)\nspawn(2)\ncheckpoint(25)\nexit(45)",
            W, H,
        )
        assert moves == [] and "checkpoint(25)" in text

    def test_auto_bridge_bails_on_dead_ops_instead_of_looping(self) -> None:
        """When no candidate placement fits, the tool returns the problems
        for design feedback immediately — it must never append an op that
        changed nothing (the first real run burned its whole bound on 8
        copies of the same dead platform)."""
        import numpy as np

        from examples.platformer_pack.tiles import DEFAULT_TILES
        from examples.platformer_pack.validate import _suggest_bridge

        # A sealed box: every cell around the break is solid — nothing fits.
        solid = next(t.id for t in DEFAULT_TILES.tiles if t.name == "floor")
        grid = np.full((16, 48), solid, dtype=np.int8)
        assert _suggest_bridge(grid, (9, 13), (9, 9), DEFAULT_MOVEMENT) is None

    def test_auto_bridge_never_touches_design_problems(self) -> None:
        """A covered spawn has many valid fixes — that's the agent's
        decision. The tool must return it untouched for LLM feedback."""
        from examples.platformer_pack.validate import auto_bridge

        covered_spawn = "floor(0,47)\nplatform(0,13,6)\nspawn(2)\nexit(45)"
        repaired, added, problems = auto_bridge(
            covered_spawn, W, H, DEFAULT_MOVEMENT
        )
        assert repaired == covered_spawn and not added
        assert problems and "covered by a PLATFORM" in problems[0]

    def test_unreachable_layout_is_bridged_not_retried(
        self, tmp_path: Path
    ) -> None:
        """E2E (sectioned): an unreachable-but-well-designed section is
        accepted with WHOLE-GRID tool bridges — no section retry, no
        fallback, playable. The grid-native auto_bridge repairs the composited
        level, mirroring the single-blob behaviour."""
        good = make_fake_responder()
        sent: dict[str, str] = {}

        def responder(request):
            msg = request.user_message
            m = re.search(r"### SECTION: (\w+) \((\d+) of (\d+)\)", msg)
            gm = re.search(r"Grid: (\d+) wide x (\d+) tall", msg)
            if "### LEVEL: l1" in msg and m and gm:
                idx, total = int(m.group(2)) - 1, int(m.group(3))
                lw = int(gm.group(1))
                # Only the LAST section (whose cells all survive the seam
                # overlap) and only once: a 9-wide gap centred in its floor —
                # beyond a running jump's ~7-cell reach, so the whole-level
                # bridger must add a stepping platform. (>=18: fits a centred
                # gap(mid-4,mid+4) with floor margins on both sides.)
                if idx == total - 1 and "gap_dsl" not in sent and lw >= 18:
                    mid = lw // 2
                    sent["gap_dsl"] = (
                        f"floor(0,{lw - 1})\ngap({mid - 4},{mid + 4})"
                    )
                    return sent["gap_dsl"]
            return good(request)

        ctx = _run_slice(tmp_path / "run", responder=responder)
        assert "gap_dsl" in sent, "the wide-gap section was never generated"
        assert not any(
            "layout" in w for w in ctx.artifacts.get("slice_warnings", [])
        )
        dsl_text = ctx.artifacts["dsl_texts"]["l1"]
        assert sent["gap_dsl"] in dsl_text and "# auto-bridge" in dsl_text
        # The stored collision layer IS the bridged, TRAVERSABLE level — the
        # exit is reachable from spawn across the once-uncrossable gap.
        # (check_level's containment rule needs the free_volume set, which the
        # persisted grid drops; reachability is the property we assert here.)
        from examples.platformer_pack.validate import reachable_cells

        with np.load(tmp_path / "run" / ctx.bible.levels["l1"].collision) as d:
            grid = d["collision"]
        level = ctx.bible.levels["l1"]
        assert level.exit in reachable_cells(grid, level.spawn, DEFAULT_MOVEMENT)


    def test_unstandable_spawn_feedback_names_the_occupant(self) -> None:
        """Feedback must say WHY: a platform stamped over spawn previously
        produced three identical blind retries against the real backend."""
        result = stamp("floor(0,47)\nplatform(0,13,6)\nspawn(2)\nexit(45)", W, H)
        problems = check_level(result.grid, result.spawn, result.exit, DEFAULT_MOVEMENT)
        assert problems and "PLATFORM" in problems[0] and "spawn" in problems[0]

    def test_unstandable_spawn_feedback_names_missing_ground(self) -> None:
        # A USER-EDITED grid can still strand the spawn (the stamp now
        # catches gap-under-marker itself — see the final-grid tests);
        # check_level's diagnosis covers grids the DSL never produced.
        result = stamp("floor(0,47)\nspawn(2)\nexit(45)", W, H)
        result.grid[14, 1:4] = 0  # ground row out from under spawn
        result.grid[15, 1:4] = 0  # bedrock too
        problems = check_level(result.grid, result.spawn, result.exit, DEFAULT_MOVEMENT)
        assert problems and "no solid ground beneath" in problems[0]

    def test_gap_after_spawn_caught_on_the_final_grid(self) -> None:
        """Ops after a marker used to escape its validation (checkpoint
        on floor, later gap under it — the record lied). Markers now
        validate against the FINISHED grid."""
        with pytest.raises(DslError, match=r"spawn: column 2 has no floor"):
            stamp("floor(0,47)\nspawn(2)\nexit(45)\ngap(1,3)", W, H)

    def test_layout_retry_prompt_carries_previous_attempt(
        self, tmp_path: Path
    ) -> None:
        """Repair, not re-roll: the retry prompt must contain the rejected
        DSL next to the diagnosis, so the model patches one design.
        (A SYNTAX failure — the classic 4-arg wall. Geometry failures no
        longer reach the model at all: OOB clamps, pools clip, spouts
        convert — the stamp repairs them in code.)"""
        good = make_fake_responder()
        layout_calls: list[str] = []
        failed_once = {"done": False}
        sent: dict[str, str] = {}

        def responder(request):
            msg = request.user_message
            if "### TASK: layout" in msg:
                layout_calls.append(msg)
                # Fail the FIRST section once with the l8 signature typo —
                # a wall with a fourth argument (arity still raises; the
                # retry loop is exactly for these).
                if not failed_once["done"] and "### SECTION:" in msg:
                    failed_once["done"] = True
                    m = re.search(r"Grid: (\d+) wide x (\d+) tall", msg)
                    lw = int(m.group(1))
                    sent["bad_dsl"] = (
                        f"floor(0,{lw - 1})\nwall(5,8,12,14)\nspawn(2)"
                    )
                    return sent["bad_dsl"]
            return good(request)

        _run_slice(tmp_path / "run", responder=responder)

        retries = [m for m in layout_calls if "previous layout attempt" in m]
        assert retries, "no layout retry prompt captured"
        assert sent["bad_dsl"] in retries[0]
        assert "rejected because" in retries[0]
        assert "changing as little as possible" in retries[0]
        # And the diagnosis rides along with the rejected output.
        assert "wall takes 3 args" in retries[0]

    def test_whole_level_design_failure_retries_owning_section(
        self, tmp_path: Path
    ) -> None:
        """A section can pass LOCAL validation (stamp) yet break the WHOLE
        level — an UNCONTAINED pool spills only when composited. The stitcher
        routes that whole-level failure to the owning section by x-range and
        regenerates only IT (located feedback), never the flat whole-level
        fallback."""
        good = make_fake_responder()
        failed = {"done": False}
        seams: dict[str, str] = {}

        def responder(request):
            msg = request.user_message
            m = re.search(r"### SECTION: (\w+) \((\d+) of (\d+)\)", msg)
            gm = re.search(r"Grid: (\d+) wide x (\d+) tall", msg)
            # Break an INTERIOR section (not the first) once, with an
            # uncontained volume — legal locally, spills whole-level.
            if m and gm and not failed["done"] and int(m.group(2)) > 1:
                failed["done"] = True
                lw, h = int(gm.group(1)), int(gm.group(2))
                g = h - 2
                seams["bad"] = f"floor(0,{lw - 1})\nvolume(water,4,6,{g - 2})"
                return seams["bad"]
            return good(request)

        ctx = _run_slice(tmp_path / "run", responder=responder)
        assert failed["done"], "no interior section was broken"
        # Repaired by regenerating the owning section — NOT a whole-level
        # fallback (no layout warning, the level is genuine generated content).
        assert not any(
            "layout" in w for w in ctx.artifacts.get("slice_warnings", [])
        )
        assert all(
            level.layout_fallback is False
            for level in ctx.bible.levels.values()
        )

    def test_repairable_geometry_no_longer_falls_back(
        self, tmp_path: Path
    ) -> None:
        """The paid-run failure storm: a section that pours a hazard over a gap
        (and stairs off the ground) used to hard-raise every retry -> flat
        fallback. Now stamp() auto-repairs it (hazard->pit, floor under stairs),
        so the section validates on attempt 1 and the level ships REAL content
        with no fallback + no layout warning."""
        good = make_fake_responder()
        hit = {"done": False}

        def responder(request):
            msg = request.user_message
            m = re.search(r"### SECTION: (\w+) \((\d+) of (\d+)\)", msg)
            gm = re.search(r"Grid: (\d+) wide x (\d+) tall", msg)
            if m and gm and int(m.group(2)) == 2 and int(m.group(3)) >= 3:
                hit["done"] = True
                lw = int(gm.group(1))
                # A hazard over a gap + stairs off the ground — both were
                # hard-raises before the code-not-LLM repairs.
                return (
                    f"floor(0,{lw - 1})\ngap(6,9)\nhazard_strip(spike,6,9)\n"
                    "stairs_up(11,13)\ncarve(11,1,13,1)"
                )
            return good(request)

        ctx = _run_slice(tmp_path / "run", responder=responder)
        assert hit["done"], "no >=3-section level exercised the repair"
        assert not any(
            "layout" in w for w in ctx.artifacts.get("slice_warnings", [])
        )
        assert all(lv.layout_fallback is False for lv in ctx.bible.levels.values())

    def test_terminal_fallback_is_local_not_a_whole_level_nuke(
        self, tmp_path: Path
    ) -> None:
        """G3: when a section can't be repaired, ONLY it falls back — the
        sections that passed SURVIVE (the 1z bug nuked the whole composite to a
        flat floor, discarding good sections). An interior section that ALWAYS
        spills whole-level (uncontained pool) drives the outer loop to its
        terminal branch; the level must still carry real above-floor content."""
        import numpy as np

        good = make_fake_responder()
        broke: set[str] = set()

        def responder(request):
            msg = request.user_message
            lm = re.search(r"### LEVEL: (\w+)", msg)
            m = re.search(r"### SECTION: (\w+) \((\d+) of (\d+)\)", msg)
            gm = re.search(r"Grid: (\d+) wide x (\d+) tall", msg)
            # Section index 1 (0-based) of a >=3-section level, ALWAYS: a
            # locally-valid full-height 10-column WALL BLOCK — too tall to
            # bridge over, too WIDE for the doorway carve (max 8 cols), not a
            # containment spill: genuinely unrepairable. The owner regenerates
            # the same block every retry, so the outer loop exhausts and hits
            # the terminal branch.
            if m and gm and lm and int(m.group(2)) == 2 and int(m.group(3)) >= 3:
                broke.add(lm.group(1))
                lw, h = int(gm.group(1)), int(gm.group(2))
                return f"floor(0,{lw - 1})\n" + "\n".join(
                    f"wall({c},0,{h - 3})"
                    for c in range(lw // 2 - 5, lw // 2 + 5)
                )
            return good(request)

        run = tmp_path / "run"
        ctx = _run_slice(run, responder=responder)
        assert broke, "no >=3-section level was broken"
        fallbacks = [
            lv for lv in ctx.bible.levels.values() if lv.layout_fallback
        ]
        assert fallbacks, "the unfixable section should flag a (partial) fallback"
        for lv in fallbacks:
            with np.load(run / lv.collision) as d:
                grid = d["collision"]
            # NOT a whole-level flat nuke: the surviving sections put real
            # content ABOVE the two floor rows (a flat fallback is empty there).
            assert (grid[: lv.grid_height - 2, :] != 0).any(), (
                f"{lv.level_id} was flat-nuked — passed sections were discarded"
            )
            # Still walkable: spawn + exit are real standable cells.
            assert lv.spawn is not None and lv.exit is not None

    def test_containment_spill_repairs_to_free_water_no_fallback(
        self, tmp_path: Path
    ) -> None:
        """G4 (code-not-LLM): an interior section that ALWAYS pours an
        uncontained pool used to loop to a fallback; now the spill is
        re-interpreted as free-standing water in CODE — the level ships clean
        (no fallback, no layout warning) with the water preserved."""
        import numpy as np

        good = make_fake_responder()
        broke: set[str] = set()

        def responder(request):
            msg = request.user_message
            lm = re.search(r"### LEVEL: (\w+)", msg)
            m = re.search(r"### SECTION: (\w+) \((\d+) of (\d+)\)", msg)
            gm = re.search(r"Grid: (\d+) wide x (\d+) tall", msg)
            if m and gm and lm and int(m.group(2)) == 2 and int(m.group(3)) >= 3:
                broke.add(lm.group(1))
                lw, h = int(gm.group(1)), int(gm.group(2))
                g = h - 2
                # An uncontained pool at the section's left interior — spills,
                # but is code-repairable to a free-water feature.
                return f"floor(0,{lw - 1})\nvolume(water,4,6,{g - 2})"
            return good(request)

        run = tmp_path / "run"
        ctx = _run_slice(run, responder=responder)
        assert broke, "no >=3-section level was broken"
        # No layout warning, no fallback anywhere — the spill was repaired.
        assert not any(
            "layout" in w for w in ctx.artifacts.get("slice_warnings", [])
        )
        affected = [lv for lv in ctx.bible.levels.values() if lv.level_id in broke]
        for lv in affected:
            assert lv.layout_fallback is False, f"{lv.level_id} fell back"
            with np.load(run / lv.collision) as d:
                assert (d["collision"] == int(TileType.WATER)).any()  # water kept

    def test_fallback_trace_written_even_when_all_attempts_passed(
        self, tmp_path: Path
    ) -> None:
        """The plat_seaside3 l3/l6 evidence-deletion bug: a level whose
        sections all PASS local validation can still fall back in the
        WHOLE-LEVEL stitch loop — but the trace used to key on failed
        attempts alone (and even unlinked itself on an all-passed log), so
        the deciding whole-level problems vanished. The trace must now be
        written whenever the level fell back, recording every stitch round's
        problems and the terminal decision."""
        good = make_fake_responder()
        broke: set[str] = set()

        def responder(request):
            msg = request.user_message
            lm = re.search(r"### LEVEL: (\w+)", msg)
            m = re.search(r"### SECTION: (\w+) \((\d+) of (\d+)\)", msg)
            gm = re.search(r"Grid: (\d+) wide x (\d+) tall", msg)
            # An interior section that ALWAYS ships a locally-valid
            # full-height 10-column wall block — unbridgeable AND too wide
            # for the doorway carve, yet never a failed section attempt.
            if m and gm and lm and int(m.group(2)) == 2 and int(m.group(3)) >= 3:
                broke.add(lm.group(1))
                lw, h = int(gm.group(1)), int(gm.group(2))
                return f"floor(0,{lw - 1})\n" + "\n".join(
                    f"wall({c},0,{h - 3})"
                    for c in range(lw // 2 - 5, lw // 2 + 5)
                )
            return good(request)

        run = tmp_path / "run"
        ctx = _run_slice(run, responder=responder)
        fallbacks = [
            lv for lv in ctx.bible.levels.values() if lv.layout_fallback
        ]
        assert fallbacks, "the wall barrier should force fallbacks"
        for lv in fallbacks:
            trace_path = (
                run / "review" / lv.stage_id
                / f"{lv.level_id}_layout_attempts.json"
            )
            assert trace_path.exists(), (
                f"{lv.level_id} fell back but wrote no attempt trace"
            )
            trace = json.loads(trace_path.read_text())
            assert trace["fallback"] is True
            assert trace["whole_fallback"] or trace["section_fallbacks"]
            assert trace["axis"] in ("horizontal", "vertical")
            # The deciding evidence: per-round whole-level problems.
            rounds = trace["stitch_rounds"]
            assert rounds and rounds[0]["problems"]
            # The terminal round names the section it force-fell-back.
            assert any("terminal_local_fallback" in r for r in rounds)

    def test_regen_rounds_accumulate_attempts_in_the_trace(
        self, tmp_path: Path
    ) -> None:
        """A whole-level regen used to REPLACE the owning section's state,
        discarding its earlier attempt log (the paid traces under-reported
        what actually happened). Sequence for one section: fail locally once
        (bad DSL) → pass locally with a whole-level-breaking wall → regen
        clean. The trace must show all three attempts, the regen one tagged
        with its round."""
        good = make_fake_responder()
        calls = {"n": 0}
        hit: set[str] = set()

        def responder(request):
            msg = request.user_message
            lm = re.search(r"### LEVEL: (\w+)", msg)
            m = re.search(r"### SECTION: (\w+) \((\d+) of (\d+)\)", msg)
            gm = re.search(r"Grid: (\d+) wide x (\d+) tall", msg)
            # First stage only (l1-l3 are always horizontal); first
            # qualifying level's SECOND section walks the scripted sequence.
            if (
                m and gm and lm
                and lm.group(1) in ("l1", "l2", "l3")
                and int(m.group(2)) == 2
                and (not hit or lm.group(1) in hit)
            ):
                hit.add(lm.group(1))
                calls["n"] += 1
                lw, h = int(gm.group(1)), int(gm.group(2))
                if calls["n"] == 1:
                    return "not a dsl at all"
                if calls["n"] == 2:
                    return f"floor(0,{lw - 1})\n" + "\n".join(
                    f"wall({c},0,{h - 3})"
                    for c in range(lw // 2 - 5, lw // 2 + 5)
                )
            return good(request)

        run = tmp_path / "run"
        ctx = _run_slice(run, responder=responder)
        assert calls["n"] >= 3, "the scripted section never regenerated"
        (lid,) = hit
        lv = ctx.bible.levels[lid]
        assert lv.layout_fallback is False, "the regen should have healed it"
        trace = json.loads(
            (
                run / "review" / lv.stage_id / f"{lid}_layout_attempts.json"
            ).read_text()
        )
        assert trace["fallback"] is False
        mine = [a for a in trace["attempts"] if a["section"] == 1]
        outcomes = [a["outcome"] for a in mine]
        assert outcomes == ["failed_validation", "passed", "passed"], (
            f"regen discarded earlier attempts: {outcomes}"
        )
        assert "regen_round" not in mine[0] and "regen_round" not in mine[1]
        assert mine[2]["regen_round"] == 1
        rounds = trace["stitch_rounds"]
        assert rounds[0]["problems"], "round 0 should carry the wall break"
        assert not rounds[-1]["problems"], "the regen round should stitch clean"

    def test_stray_checkpoint_section_never_flats_the_level(
        self, tmp_path: Path
    ) -> None:
        """The suspected l3 whole-flat class (§1b #4): an interior section
        keeps emitting a stray checkpoint at an unstandable cell — locally
        VALID (marker checks are skipped), but the old stitch validated
        strays inside the bridge loop BEFORE stripping them, burning every
        whole-level round on a marker about to be discarded. Now stripped
        first: zero rounds burned, the level ships real content."""
        good = make_fake_responder()
        broke: set[str] = set()

        def responder(request):
            msg = request.user_message
            lm = re.search(r"### LEVEL: (\w+)", msg)
            m = re.search(r"### SECTION: (\w+) \((\d+) of (\d+)\)", msg)
            gm = re.search(r"Grid: (\d+) wide x (\d+) tall", msg)
            if m and gm and lm and int(m.group(2)) == 2 and int(m.group(3)) >= 3:
                broke.add(lm.group(1))
                lw, h = int(gm.group(1)), int(gm.group(2))
                return (
                    f"floor(0,{lw - 1})\nwall(10,{h - 4},{h - 3})\n"
                    "checkpoint(10)"
                )
            return good(request)

        ctx = _run_slice(tmp_path / "run", responder=responder)
        assert broke, "no >=3-section level was exercised"
        assert not any(
            "layout" in w for w in ctx.artifacts.get("slice_warnings", [])
        )
        assert all(
            lv.layout_fallback is False for lv in ctx.bible.levels.values()
        )

    def test_stray_section_checkpoint_never_burns_stitch_rounds(self) -> None:
        """A checkpoint() a section illegally emitted (checkpoints are
        stitcher-owned since G1) used to reach check_level through the bridge
        loop BEFORE the stitcher stripped it — an unstandable stray failed
        the whole level for a marker about to be discarded anyway. Strays are
        now stripped before the bridge/validate loop."""
        from examples.platformer_pack.level import (
            _SectionState,
            _stitch_and_repair,
        )
        from examples.platformer_pack.movement import DEFAULT_MOVEMENT
        from examples.platformer_pack.rules import DEFAULT_RULES
        from examples.platformer_pack.sections import PlannedSection
        from examples.platformer_pack.tiles import DEFAULT_TILES

        width, height = 40, 16
        plan = [
            PlannedSection("runway", 26, x_off=0, y_off=0),
            PlannedSection("gauntlet", 20, x_off=20, y_off=0),
        ]
        # Section 1 tucks a checkpoint under a wall pillar that occupies its
        # standing cell — unstandable, and NOT a reachability problem, so it
        # used to short-circuit auto_bridge_grid into returning it as residue.
        dsls = [
            "floor(0,25)\nspawn(2)",
            f"floor(0,19)\nwall(10,{height - 4},{height - 3})\ncheckpoint(10)",
        ]
        states = [
            _SectionState(
                ps=ps,
                dsl=text,
                res=stamp(
                    text, ps.length, height, tiles=DEFAULT_TILES,
                    validate_markers=False,
                ),
                fell_back=False,
                attempts=[],
            )
            for ps, text in zip(plan, dsls)
        ]
        whole, bridges, snaps, problems = _stitch_and_repair(
            states, width, height, "horizontal", DEFAULT_MOVEMENT,
            DEFAULT_RULES, DEFAULT_TILES, checkpoint_sections=[],
        )
        assert problems == [], f"stray checkpoint burned a round: {problems}"
        assert not [t for t in whole.triggers if t.type == "checkpoint"]

    def test_placement_retry_prompt_carries_previous_attempt(
        self, tmp_path: Path
    ) -> None:
        good = make_fake_responder()
        placement_calls: list[str] = []
        failed_once = {"done": False}
        # A DESIGN failure (mid-air placement) — spawn-crowding no longer
        # reaches the model at all; the nudge tool repairs it.
        bad_json = '{"placements": [{"enemy_id": "cinder_beetle", "x": 10, "y": 5}]}'

        def responder(request):
            msg = request.user_message
            if "### TASK: placement" in msg:
                placement_calls.append(msg)
                if not failed_once["done"]:
                    failed_once["done"] = True
                    return bad_json  # mid-air, nothing to stand on
            return good(request)

        _run_slice(tmp_path / "run", responder=responder)

        retries = [m for m in placement_calls if "previous placements attempt" in m]
        assert retries and bad_json in retries[0]
        assert "not a standable cell" in retries[0]

    def test_placement_prompt_includes_spawn(self, tmp_path: Path) -> None:
        """The 'stay away from spawn' rule is only followable if the prompt
        says where spawn is."""
        good = make_fake_responder()
        placement_prompts = []

        def spy(request):
            if "### TASK: placement" in request.user_message:
                placement_prompts.append(request.user_message)
            return good(request)

        _run_slice(tmp_path / "run", responder=spy)
        assert placement_prompts
        assert all("Player spawn: [" in m for m in placement_prompts)

    def test_placement_prompt_carries_section_encounter_map(
        self, tmp_path: Path
    ) -> None:
        """Chunk B: the placement prompt carries the per-section ENCOUNTER map
        (recomputed deterministically) so enemies concentrate in combat/mixed
        stretches. The map matches the plan the layout phase actually
        stitched (parsed from the combined section DSL)."""
        import re as _re

        from examples.platformer_pack.level import (
            level_section_plan,
            section_encounter_summary,
        )

        good = make_fake_responder()
        placement_prompts: dict[str, str] = {}

        def spy(request):
            msg = request.user_message
            if "### TASK: placement" in msg:
                lid = _re.search(r"### LEVEL: (\w+)", msg).group(1)
                placement_prompts[lid] = msg
            return good(request)

        ctx = _run_slice(tmp_path / "run", responder=spy)
        assert placement_prompts
        for m in placement_prompts.values():
            assert "Section map" in m and "cols 0-" in m

        # The recomputed plan MATCHES the plan the layout phase stitched: the
        # combined DSL records each section as "### SECTION i: arch @x=X"
        # (horizontal) or "@y=Y" (vertical).
        for lid, level in ctx.bible.levels.items():
            plan = level_section_plan(ctx, level)
            stitched = _re.findall(
                r"### SECTION \d+: (\w+) @[xy]=(\d+)",
                ctx.artifacts["dsl_texts"][lid],
            )
            off = "x_off" if level.layout_axis == "horizontal" else "y_off"
            assert [(a, int(o)) for a, o in stitched] == [
                (ps.archetype, getattr(ps, off)) for ps in plan
            ], lid
            # …and that plan's encounter map is what the prompt carried.
            assert (
                section_encounter_summary(plan, axis=level.layout_axis)
                in placement_prompts[lid]
            )

    def test_placement_rules(self) -> None:
        result = stamp(_FAKE_LAYOUTS["l1"], W, H)
        spawn = result.spawn
        defs = {
            "beetle": {"archetype": "patroller", "size": 1.0},
            "fish": {"archetype": "swimmer", "size": 1.0},
        }
        accepted, problems, repairs = check_placements(
            result.grid,
            [
                {"enemy_id": "beetle", "x": 20, "y": 13},  # ok (under the tier)
                {"enemy_id": "beetle", "x": 3, "y": 13},  # spawn-crowding: NUDGED
                {"enemy_id": "beetle", "x": 41, "y": 13},  # spike cell
                {"enemy_id": "ghost", "x": 20, "y": 13},  # unknown id
                {"enemy_id": "beetle", "x": 10, "y": 5},  # mid-air
                {"enemy_id": "beetle", "x": 28, "y": 12},  # land enemy in water
                {"enemy_id": "fish", "x": 28, "y": 12},  # swimmer in water: ok
                {"enemy_id": "fish", "x": 20, "y": 13},  # swimmer on land
                # variant rides through; legacy elite bool maps to "elite"
                {"enemy_id": "fish", "x": 27, "y": 13, "elite": True},
            ],
            spawn,
            defs,
        )
        # Two CODE repairs now: beetle@3 slides to the nearest standable
        # column outside the spawn radius (8 — the pool at 5-7 doesn't
        # support land enemies); and beetle@(28,12), dropped in DEEP water
        # under the seabed policy, SNAPS to the wet flat below it at (28,13)
        # rather than kicking back (ticket 5a).
        assert [(p["enemy_id"], p["x"], p["y"]) for p in accepted] == [
            ("beetle", 20, 13), ("beetle", 8, 13), ("beetle", 28, 13),
            ("fish", 28, 12), ("fish", 27, 13),
        ]
        assert accepted[4]["variant"] == "elite"
        assert len(repairs) == 2
        assert any(
            "spawn-safety nudge" in r and "(3, 13) -> (8, 13)" in r
            for r in repairs
        )
        assert any(
            "wader snapped" in r and "(28, 12) -> (28, 13)" in r
            for r in repairs
        )
        assert len(problems) == 4
        assert any("swimmers must be placed inside water" in p for p in problems)

    def test_flyer_off_top_names_the_top_not_a_clamped_row_zero(self) -> None:
        # A flyer whose body runs off the TOP of the level must SAY so — not
        # report a clamped row-0 cell as "EMPTY, not empty" (ticket 5b).
        import numpy as np

        grid = np.zeros((6, 6), dtype=np.int8)
        grid[5, :] = 1  # floor below, so the airspace-over-ground check is moot
        defs = {"bat": {"archetype": "flyer", "size": 2.0}}
        _, problems, _ = check_placements(
            grid, [{"enemy_id": "bat", "x": 2, "y": 0}], (0, 5), defs,
        )
        assert problems and "runs off the TOP" in problems[0]
        assert "not empty" not in problems[0]  # the old self-contradiction


class TestCappedOnewayRepair:
    """Ticket 2: a one-way platform sealed by a slab above it is a broken
    promise (you can't jump up into it). CARVE the cap to restore it; DELETE
    the dead platform only when carving would spill water / hit a hazard;
    leave Pattern-A (flush) one-ways and ALL solid geometry alone."""

    def _grid(self, rows: list[str]):
        import numpy as np

        # empty / floor / one-way / wall / water
        m = {".": 0, "F": 1, "P": 2, "W": 3, "~": 20}
        return np.array([[m[c] for c in row] for row in rows], dtype=np.int8)

    def test_pattern_b_carves_the_cap_and_keeps_the_platform(self) -> None:
        from examples.platformer_pack.tiles import DEFAULT_TILES
        from examples.platformer_pack.validate import (
            repair_capped_oneways_grid,
        )

        grid = self._grid([
            "......",  # 0
            "..W...",  # 1  the capping block
            "......",  # 2  the standing pocket (empty)
            "..P...",  # 3  the one-way platform
            "FFFFFF",  # 4
        ])
        notes = repair_capped_oneways_grid(grid, DEFAULT_TILES)
        assert grid[1, 2] == 0  # cap carved
        assert grid[3, 2] == 2  # one-way PRESERVED (jump-up restored)
        assert notes and "carved the cap" in notes[0]

    def test_water_on_cap_deletes_the_dead_platform(self) -> None:
        from examples.platformer_pack.tiles import DEFAULT_TILES
        from examples.platformer_pack.validate import (
            repair_capped_oneways_grid,
        )

        grid = self._grid([
            "..~...",  # 0  contained water resting on the cap
            "..W...",  # 1  the cap
            "......",  # 2  pocket
            "..P...",  # 3  one-way
            "FFFFFF",  # 4
        ])
        notes = repair_capped_oneways_grid(
            grid, DEFAULT_TILES, free_volume={(2, 0)},
        )
        assert grid[1, 2] == 3  # cap NOT carved (would spill)
        assert grid[3, 2] == 0  # dead one-way dropped instead
        assert notes and "spill water" in notes[0]

    def test_pattern_a_flush_one_way_is_left_alone(self) -> None:
        from examples.platformer_pack.tiles import DEFAULT_TILES
        from examples.platformer_pack.validate import (
            repair_capped_oneways_grid,
        )

        grid = self._grid([
            "......",  # 0
            "..W...",  # 1
            "..W...",  # 2  standing cell itself SOLID (flush) = decorative
            "..P...",  # 3  one-way jammed under the solid
            "FFFFFF",  # 4
        ])
        before = grid.copy()
        assert repair_capped_oneways_grid(grid, DEFAULT_TILES) == []
        assert (grid == before).all()

    def test_solid_corridor_never_touched(self) -> None:
        from examples.platformer_pack.tiles import DEFAULT_TILES
        from examples.platformer_pack.validate import (
            repair_capped_oneways_grid,
        )

        # a 1-tall SOLID corridor is intentional terrain — no one-ways at all
        grid = self._grid([
            "WWWWWW",  # 0  ceiling
            "......",  # 1  1-tall gap (a trap corridor)
            "WWWWWW",  # 2  floor
        ])
        before = grid.copy()
        assert repair_capped_oneways_grid(grid, DEFAULT_TILES) == []
        assert (grid == before).all()

    def test_usable_one_way_untouched(self) -> None:
        from examples.platformer_pack.tiles import DEFAULT_TILES
        from examples.platformer_pack.validate import (
            repair_capped_oneways_grid,
        )

        grid = self._grid([
            "......",  # 0
            "......",  # 1  >= 2 open cells above the pocket
            "......",  # 2  pocket
            "..P...",  # 3  one-way with clear headroom
            "FFFFFF",  # 4
        ])
        before = grid.copy()
        assert repair_capped_oneways_grid(grid, DEFAULT_TILES) == []
        assert (grid == before).all()


# ---------------------------------------------------------------------------
# Colors + schemas
# ---------------------------------------------------------------------------


class TestDatabasesDriveReview:
    def test_placeholder_colors_distinct_and_not_red(self) -> None:
        colors = [placeholder_color(i) for i in range(8)]
        assert len(set(colors)) == 8
        for color in colors:
            r, g, b = (int(color[i : i + 2], 16) for i in (1, 3, 5))
            # Reserved spike band: strongly-red hues are nudged away.
            assert not (r > 180 and g < 80 and b < 80), f"{color} reads as spike red"

    def test_enemy_hue_reservations_come_from_palette(self) -> None:
        """The reserved bands derive from the game's ACTUAL hazard/volume
        palette hues — a lava game reserves orange, not hardcoded blue."""
        import colorsys

        from examples.platformer_pack.phases import (
            placeholder_color,
            reserved_hue_bands,
        )
        from examples.platformer_pack.tiles import load_tiles

        lava_tiles = load_tiles(
            Path(__file__).parent.parent / "examples/lava_world/tile_types.json"
        )
        palette = {"danger": "#e0453a", "lava": "#e8722c"}
        bands = reserved_hue_bands(palette, lava_tiles)
        assert len(bands) == 2  # spike + lava hues, no hardcoded blue band
        for i in range(8):
            color = placeholder_color(i, bands)
            r, g, b = (int(color[j : j + 2], 16) / 255 for j in (1, 3, 5))
            hue = colorsys.rgb_to_hsv(r, g, b)[0] * 360
            for lo, hi in bands:
                inside = (lo <= hue <= hi) if lo <= hi else (hue >= lo or hue <= hi)
                assert not inside, f"{color} sits in reserved band {lo}-{hi}"

    def test_placeholder_luminance_floor(self) -> None:
        """RB1: with stage-background luminances supplied, every
        placeholder clears ACTOR_BG_MIN_LUMA via a hue-preserving shift;
        without them the function is byte-identical to before."""
        from examples.platformer_pack import color as cm
        from examples.platformer_pack.phases import (
            ACTOR_BG_MIN_LUMA,
            placeholder_color,
        )

        for i in range(8):
            base = placeholder_color(i)
            assert placeholder_color(i, background_lums=()) == base
            # Backgrounds bracketing the color's own luminance force the
            # shift; distant backgrounds leave the color untouched.
            near = (cm.luminance(base) - 5.0, cm.luminance(base) + 5.0)
            shifted = placeholder_color(i, background_lums=near)
            assert (
                min(abs(cm.luminance(shifted) - b) for b in near)
                >= ACTOR_BG_MIN_LUMA
            )
            base_hue, shifted_hue = cm.hue_of(base), cm.hue_of(shifted)
            if base_hue is not None and shifted_hue is not None:
                assert cm.hue_distance(base_hue, shifted_hue) <= 2.0
            far = (cm.luminance(base) + 120.0 % 255,)
            if abs(cm.luminance(base) - far[0]) >= ACTOR_BG_MIN_LUMA:
                assert placeholder_color(i, background_lums=far) == base

    def test_canned_slice_placeholders_clear_every_background(
        self, tmp_path: Path
    ) -> None:
        """RB1 e2e: on the canned run, every enemy AND item placeholder
        sits >= ACTOR_BG_MIN_LUMA from every stage background — the
        dark-navy-beetle-on-near-black class is structurally gone."""
        from examples.platformer_pack import color as cm
        from examples.platformer_pack.phases import ACTOR_BG_MIN_LUMA

        run = tmp_path / "run"
        ctx = _run_slice(run)
        manifest = json.loads((run / "manifest.json").read_text())
        bg_lums = [
            cm.luminance(palette["background"])
            for palette in manifest["palettes"].values()
        ]
        actors = [
            ("enemy", eid, e.stats["placeholder_color"])
            for eid, e in ctx.bible.enemy_definitions.items()
        ] + [
            ("item", iid, it.stats["placeholder_color"])
            for iid, it in ctx.bible.items.items()
        ]
        assert actors and bg_lums
        for kind, ident, color in actors:
            lum = cm.luminance(color)
            for bg in bg_lums:
                assert abs(lum - bg) >= ACTOR_BG_MIN_LUMA, (
                    f"{kind} {ident} placeholder {color} (L={lum:.0f}) "
                    f"within {ACTOR_BG_MIN_LUMA} of a background (L={bg:.0f})"
                )

    def test_background_bands_derive_from_palette(self, tmp_path: Path) -> None:
        """No hardcoded sky grays: horizon bands lighten the palette's
        background color, sampled from the empty tileset slot."""
        from PIL import Image

        run = tmp_path / "run"
        ctx = _run_slice(run)
        level = ctx.bible.levels["l1"]
        img = Image.open(
            run / f"review/{level.stage_id}/l1.png"
        ).convert("RGB")
        # Top band pixel vs bottom-band empty pixel: same hue family
        # (scaled background #2b2331), top strictly lighter.
        top = img.getpixel((0, 4))
        low = img.getpixel((0, (level.grid_height - 5) * 16))
        assert sum(top) > sum(low)
        base = (0x2B, 0x23, 0x31)  # canned style background
        assert low == base  # bottom band = the palette background itself

    def test_schema_files_load_via_loader(self) -> None:
        for name in ("enemy.json", "level_layout.json"):
            spec = load_skeleton_spec(SCHEMAS_DIR / name)
            assert spec.fields  # non-empty, validated at load time

    def test_difficulty_escalates_by_level_position(self) -> None:
        """Difficulty keys off the level_number CONTEXT, not a roll —
        level 3 must be harder than level 1 for every seed."""
        from canon.pipeline.rng import derive_rng
        from canon.skeleton.core import roll_skeleton

        spec = load_skeleton_spec(SCHEMAS_DIR / "level_layout.json")
        for seed in ("a", "b", "emberfall_001"):
            knobs = [
                roll_skeleton(
                    spec,
                    derive_rng(seed, "plat:layout", f"l{n}"),
                    context={"level_number": n},
                )
                for n in (1, 2, 3)
            ]
            assert [k["difficulty"] for k in knobs] == [1, 2, 3]
            assert (
                knobs[0]["hazard_count"]
                < knobs[1]["hazard_count"]
                < knobs[2]["hazard_count"]
            )


# ---------------------------------------------------------------------------
# End-to-end (fake backend)
# ---------------------------------------------------------------------------


def _run_slice(
    output_dir: Path,
    seed: str = "emberfall_001",
    responder=None,
    engine: str = "json",
    **compose_kwargs,
) -> PipelineContext:
    adapter = None
    if engine == "godot":
        from canon.adapters import GodotOutputAdapter

        adapter = GodotOutputAdapter(output_dir)
    ctx = PipelineContext(
        bible=Bible.empty(seed=seed),
        config=CanonConfig(seed=seed, output_dir=output_dir),
        rng=random.Random(seed),
        llm=LLMClient(FakeLLMBackend(responder or make_fake_responder())),
        prompts=PlatformerPrompts(),
        adapter=adapter,
    )
    run_pipeline(compose_pipeline(engine=engine, **compose_kwargs), ctx)
    return ctx


class TestEndToEnd:
    def test_tree_shape_and_determinism(self, tmp_path: Path) -> None:
        run_a, run_b = tmp_path / "a", tmp_path / "b"
        _run_slice(run_a)
        _run_slice(run_b)

        files_a = tree_files(run_a)
        expected = {
            Path("world.json"),
            Path("manifest.json"),
            Path("stage/ashen_depths/stage.json"),
            Path("tileset/ashen_depths/tilesheet.png"),
            Path("review/legend.png"),
            Path("level/ashen_depths/l1/collision.npz"),
            Path("level/ashen_depths/l1/level.json"),
            Path("level/ashen_depths/l1/entities.json"),
        }
        assert expected.issubset(set(files_a))

        assert_trees_byte_identical(run_a, run_b)

    def test_hash_recompute_contract(self, tmp_path: Path) -> None:
        """§6.3: stored content hashes must match a recompute from disk."""
        ctx = _run_slice(tmp_path / "run")
        for level in ctx.bible.levels.values():
            disk = (tmp_path / "run" / level.collision).read_bytes()
            assert level.collision_hash == "sha256:" + hashlib.sha256(disk).hexdigest()
        tileset = ctx.bible.tilesets["ashen_depths"]
        disk = (tmp_path / "run" / tileset.tilesheet_path).read_bytes()
        assert tileset.tilesheet_hash == "sha256:" + hashlib.sha256(disk).hexdigest()

    def test_placements_reference_enemy_database(self, tmp_path: Path) -> None:
        ctx = _run_slice(tmp_path / "run")
        assert ctx.bible.enemy_definitions
        for level in ctx.bible.levels.values():
            assert level.entities, f"{level.level_id} has no placements"
            for placement in level.entities:
                enemy_id = placement.ref.split(":", 1)[1]
                assert enemy_id in ctx.bible.enemy_definitions
        # Every enemy carries a placeholder color for the review surfaces.
        for enemy in ctx.bible.enemy_definitions.values():
            assert enemy.stats["placeholder_color"].startswith("#")

    def test_placements_stand_on_generated_grid(self, tmp_path: Path) -> None:
        ctx = _run_slice(tmp_path / "run")
        for level in ctx.bible.levels.values():
            with np.load(tmp_path / "run" / level.collision) as data:
                grid = data["collision"]
            stand, volume = standable_cells(grid), volume_cells(grid)
            for placement in level.entities:
                enemy_id = placement.ref.split(":", 1)[1]
                archetype = ctx.bible.enemy_definitions[enemy_id].archetype
                if archetype == "flyer":
                    # Airborne: an open-air cell, never a ground stand or water.
                    assert tuple(placement.pos) not in stand
                    assert tuple(placement.pos) not in volume
                    continue
                if placement.overrides.get("variant") == "emberborn":
                    # Hazard-immune: posted ON a footed hazard cell.
                    from examples.platformer_pack.validate import (
                        hazard_stand_cells,
                    )
                    assert tuple(placement.pos) in hazard_stand_cells(
                        grid
                    ), f"{enemy_id} at {placement.pos}"
                    continue
                expected = volume if archetype == "swimmer" else stand
                assert tuple(placement.pos) in expected, (
                    f"{enemy_id} ({archetype}) at {placement.pos}"
                )

    def test_canned_fake_places_a_flyer_in_open_air(self, tmp_path: Path) -> None:
        """The canned fake exercises the flyer path end-to-end at $0: a
        flyer-rolling seed yields a flyer definition placed in an open-air
        cell (never a ground stand or water) — the air-summary + fake air
        pool + validator airborne branch all on the same path. (Seed
        re-anchored when the hopper archetype joined the roll table —
        'kestrel' rolls a flyer under the v8 weights.)"""
        ctx = _run_slice(tmp_path / "run", seed="kestrel")
        flyers = {
            eid
            for eid, e in ctx.bible.enemy_definitions.items()
            if e.archetype == "flyer"
        }
        assert flyers, "the 'zephyr' seed should roll at least one flyer"
        placed_in_air = False
        for level in ctx.bible.levels.values():
            with np.load(tmp_path / "run" / level.collision) as data:
                grid = data["collision"]
            stand, volume = standable_cells(grid), volume_cells(grid)
            for placement in level.entities:
                eid = placement.ref.split(":", 1)[1]
                if eid in flyers:
                    pos = tuple(placement.pos)
                    assert pos not in stand and pos not in volume, (
                        f"flyer {eid} at {pos} is not open air"
                    )
                    placed_in_air = True
        assert placed_in_air, "no flyer was placed in any level"

    def test_spawn_exit_first_class_fields(self, tmp_path: Path) -> None:
        """spawn/exit are Level fields (not trigger records) and land on
        standable cells; checkpoints are the trigger records (3b) and are
        standable too."""
        ctx = _run_slice(tmp_path / "run")
        for level in ctx.bible.levels.values():
            assert level.spawn is not None and level.exit is not None
            with np.load(tmp_path / "run" / level.collision) as data:
                cells = standable_cells(data["collision"])
            assert tuple(level.spawn) in cells
            assert tuple(level.exit) in cells
            checkpoints = [t for t in level.triggers if t.type == "checkpoint"]
            if level.parent_level:
                # Secret rooms are checkpoint-free by blueprint (1-2
                # sections → count rule 0; death ejects to the parent).
                assert not checkpoints, f"{level.level_id} has a checkpoint"
            else:
                assert checkpoints, f"{level.level_id} has no checkpoint"
            for t in checkpoints:
                assert (t.x, t.y) in cells
        # And they round-trip through level.json for the harness.
        level_doc = json.loads(
            (tmp_path / "run/level/ashen_depths/l1/level.json").read_text()
        )
        assert level_doc["spawn"] is not None and level_doc["exit"] is not None
        assert level_doc["triggers"][0]["type"] == "checkpoint"
        # The triggers LAYER file carries the same records (§6.4).
        triggers_doc = json.loads(
            (tmp_path / "run/level/ashen_depths/l1/triggers.json").read_text()
        )
        assert triggers_doc == level_doc["triggers"]

    def test_clean_run_has_no_warnings(self, tmp_path: Path) -> None:
        ctx = _run_slice(tmp_path / "run")
        assert ctx.artifacts.get("slice_warnings", []) == []
        manifest = json.loads((tmp_path / "run/manifest.json").read_text())
        assert manifest["warnings"] == []

    def test_fallback_is_loudly_surfaced(self, tmp_path: Path) -> None:
        """A run that only 'succeeds' via fallback content must say so in
        artifacts AND manifest.json — never silently."""
        good = make_fake_responder()

        def broken_layouts(request):
            if "### TASK: layout" in request.user_message:
                return "not a dsl at all"
            return good(request)

        ctx = _run_slice(tmp_path / "run", responder=broken_layouts)
        warnings = ctx.artifacts["slice_warnings"]
        assert any("FALLBACK layout" in w for w in warnings)
        manifest = json.loads((tmp_path / "run/manifest.json").read_text())
        assert manifest["warnings"] == warnings
        # Fallback levels are flat floors at each level's schema-rolled dims.
        for level in ctx.bible.levels.values():
            assert level.spawn == (2, level.grid_height - 3)

    def test_fallback_attempt_trace_is_persisted(self, tmp_path: Path) -> None:
        """The l3 failure class: per-attempt validation reasons and the
        rejected DSL must survive ON DISK (console WARNINGs scroll away),
        and the Level must be queryable as fallback — not a failed
        generation wearing a suit."""
        good = make_fake_responder()

        def broken_layouts(request):
            if "### TASK: layout" in request.user_message:
                return "not a dsl at all"
            return good(request)

        ctx = _run_slice(tmp_path / "run", responder=broken_layouts)
        retries = getattr(ctx.config, "max_retries", 3)
        for level in ctx.bible.levels.values():
            assert level.layout_fallback is True
            trace_path = (
                tmp_path / "run" / "review" / level.stage_id
                / f"{level.level_id}_layout_attempts.json"
            )
            trace = json.loads(trace_path.read_text())
            assert trace["fallback"] is True
            # Sectioned levels aggregate EVERY section's attempts — each of
            # the N sections burned all `retries` before falling back.
            assert trace["attempts"]
            assert len(trace["attempts"]) % retries == 0
            for a in trace["attempts"]:
                assert a["outcome"] == "failed_validation"
                assert a["reasons"]
                assert a["content"] == "not a dsl at all"
                assert "section" in a  # tagged to its owning section
            # The flag round-trips through level.json for status tools.
            level_doc = json.loads(
                (
                    tmp_path / "run" / "level" / level.stage_id
                    / level.level_id / "level.json"
                ).read_text()
            )
            assert level_doc["layout_fallback"] is True

    def test_layout_retries_escalate_token_budget(self, tmp_path: Path) -> None:
        """The l3 class: identical caps across retries make a truncated
        program unrecoverable — a section's layout attempts must ESCALATE
        (×1.5) across retries. (Sectioned levels budget per section by LOCAL
        area, so the base value varies; the escalation ratio is the invariant.)
        """
        good = make_fake_responder()
        seen: list[int | None] = []

        def broken_layouts(request):
            if "### TASK: layout" in request.user_message:
                seen.append(request.max_tokens)
                return "not a dsl at all"
            return good(request)

        _run_slice(tmp_path / "run", responder=broken_layouts)
        # The first section's three attempts climb base → 1.5x → 2.25x.
        assert seen[0] is not None
        assert seen[1] == int(seen[0] * 1.5)
        assert seen[2] == int(seen[1] * 1.5)

    def test_clean_run_writes_no_attempt_trace(self, tmp_path: Path) -> None:
        """Attempt traces are failure evidence only — a clean run must not
        grow diagnostic files (keeps the byte-identical run contract
        meaningful)."""
        ctx = _run_slice(tmp_path / "run")
        assert not list((tmp_path / "run").glob("review/**/*_attempts.json"))
        assert all(
            level.layout_fallback is False
            for level in ctx.bible.levels.values()
        )

    def test_duplicate_enemy_names_prompted_and_deduped(
        self, tmp_path: Path
    ) -> None:
        """A model that reuses names gets told what's taken (used-names in
        the prompt + retry feedback); ids stay unique via numeric backstop."""
        good = make_fake_responder()
        enemy_calls = []

        def same_name_enemies(request):
            if "### TASK: enemy" in request.user_message:
                enemy_calls.append(request.user_message)
                return json.dumps({"name": "Wraith Moth", "flavor": "again"})
            return good(request)

        ctx = _run_slice(tmp_path / "run", responder=same_name_enemies)

        # Later enemy prompts must name what's already taken.
        later_calls = [m for m in enemy_calls if "### INDEX: 1" in m]
        assert later_calls and all("already taken" in m.lower() or "Wraith Moth" in m for m in later_calls)
        # Retry feedback fired for the stubborn duplicate.
        assert any("already taken" in m for m in enemy_calls)
        # IDs are still unique (fallback names or numeric suffix backstop).
        ids = list(ctx.bible.enemy_definitions)
        assert len(ids) == len(set(ids)) == 4

    def test_layer_files_and_hash_contract(self, tmp_path: Path) -> None:
        """3a core: the full §6.4 layer set per level, every hash on the
        Bible matching a recompute from disk."""
        run = tmp_path / "run"
        ctx = _run_slice(run)
        layer_files = (
            "collision.npz", "terrain.npz", "background.npz",
            "hazards.json", "triggers.json", "entities.json",
            "foreground.json", "level.json",
        )
        for level in ctx.bible.levels.values():
            level_dir = run / "level" / level.stage_id / level.level_id
            for name in layer_files:
                assert (level_dir / name).exists(), f"{level.level_id}/{name}"
            for rel, stored in (
                (level.collision, level.collision_hash),
                (level.terrain, level.terrain_hash),
                (level.background, level.background_hash),
                (f"level/{level.stage_id}/{level.level_id}/hazards.json",
                 level.hazards_hash),
                (f"level/{level.stage_id}/{level.level_id}/triggers.json",
                 level.triggers_hash),
                (f"level/{level.stage_id}/{level.level_id}/entities.json",
                 level.entities_hash),
                (f"level/{level.stage_id}/{level.level_id}/foreground.json",
                 level.foreground_hash),
            ):
                disk = (run / rel).read_bytes()
                assert stored == "sha256:" + hashlib.sha256(disk).hexdigest(), rel

    def test_step_parents_follow_the_chain(self, tmp_path: Path) -> None:
        """§6.1 within-level edges, recorded for the Phase 2 orchestrator."""
        ctx = _run_slice(tmp_path / "run")
        for level in ctx.bible.levels.values():
            sp = level.step_parents
            prefix = f"level:{level.stage_id}/{level.level_id}"
            assert sp["collision"] == [f"{prefix}/layout"]
            assert f"{prefix}/collision" in sp["terrain"]
            assert any(p.startswith("tileset:") for p in sp["terrain"])
            assert f"{prefix}/collision" in sp["hazards"]
            assert f"{prefix}/collision" in sp["entities"]
            assert f"{prefix}/hazards" in sp["entities"]
            assert f"{prefix}/collision" in sp["foreground"]

    def test_3a_features_land_in_layers(self, tmp_path: Path) -> None:
        """Water pool, ledge tier, swimmer-in-water, variant placements,
        checkpoints, decor, and variable dims — each visible in the right
        artifact. (Seed re-anchored when the hopper archetype joined the
        roll table — 'emberfall_002' keeps a swimmer in the 4-pool under
        the v8 weights.)"""
        run = tmp_path / "run"
        ctx = _run_slice(run, seed="emberfall_002")
        levels = ctx.bible.levels

        # Variable dims: schema-rolled RANGES, banded by difficulty —
        # rolled values land inside their band and escalate in width.
        bands = {
            "l1": ((48, 72), (14, 16)),
            "l2": ((72, 104), (16, 20)),
            "l3": ((100, 132), (18, 26)),
        }
        for lid, ((w_lo, w_hi), (h_lo, h_hi)) in bands.items():
            level = levels[lid]
            assert w_lo <= level.grid_width <= w_hi, lid
            assert h_lo <= level.grid_height <= h_hi, lid
        assert levels["l1"].grid_width < levels["l3"].grid_width
        # Water present in every canned MAIN level's collision layer
        # (secret rooms are deliberately dry chambers — vault/lair).
        for level in levels.values():
            if level.parent_level:
                continue
            with np.load(run / level.collision) as data:
                assert (data["collision"] == int(TileType.WATER)).any(), (
                    f"{level.level_id} has no water"
                )
        # The swimmer sits in water (also covered by placement test); the
        # roster rolled one at this seed.
        archetypes = {e.archetype for e in ctx.bible.enemy_definitions.values()}
        assert "swimmer" in archetypes
        # Exactly one elite + one champion per canned level, riding on
        # overrides as NAMES the manifest vocabulary resolves (3b).
        for level in levels.values():
            named = [
                p.overrides["variant"]
                for p in level.entities
                if p.overrides.get("variant")
            ]
            # emberborn is OPTIONAL per level (the fake posts it only
            # where a footed hazard cell exists); the mandatory trio is
            # always stamped.
            assert [
                n for n in sorted(named) if n != "emberborn"
            ] == ["champion", "elite", "relentless"]
            # entities.json carries the variant field for consumers.
            entities_doc = json.loads(
                (run / f"level/{level.stage_id}/{level.level_id}/entities.json")
                .read_text()
            )
            doc_named = [e["variant"] for e in entities_doc if e["variant"]]
            assert [n for n in doc_named if n != "emberborn"] == [
                "elite", "champion", "relentless",
            ]
        # Foreground decor landed inline + in its layer file.
        for level in levels.values():
            assert level.foreground
            file_decor = json.loads(
                (run / f"level/{level.stage_id}/{level.level_id}/foreground.json")
                .read_text()
            )
            assert len(file_decor) == len(level.foreground)
        # Tileset slots carry category semantics + named tiles + params.
        tileset = ctx.bible.tilesets["ashen_depths"]
        semantics = {s.collision for s in tileset.slots}
        assert semantics == {"empty", "solid", "one_way", "hazard", "volume"}
        water_slot = next(s for s in tileset.slots if s.name == "water")
        assert water_slot.params["speed_factor"] == 0.55
        # Manifest ships the full game vocabulary for play surfaces.
        manifest = json.loads((run / "manifest.json").read_text())
        assert [t["name"] for t in manifest["tiles"]] == [
            "empty", "floor", "platform", "wall", "breakable", "box",
            "spike", "urchin", "mine", "water",
        ]
        assert [v["name"] for v in manifest["variants"]] == [
            "elite", "champion", "emberborn", "relentless",
        ]

    def test_water_reachability_model(self) -> None:
        """A pool wider than jump_width is crossable by swimming; the same
        span as a dry gap is not."""
        from examples.platformer_pack.validate import reachable_cells

        contained = stamp(
            "floor(0,47)\nwall(19,12,13)\nwall(31,12,13)\n"
            "water(20,30,12)\nspawn(2)\nexit(45)", W, H,
        )
        assert not check_level(
            contained.grid, contained.spawn, contained.exit, DEFAULT_MOVEMENT
        )
        reached = reachable_cells(contained.grid, contained.spawn, DEFAULT_MOVEMENT)
        assert (25, 13) in reached  # swimming through the pool

        dry = stamp("floor(0,19)\nfloor(31,47)\nspawn(2)\nexit(45)", W, H)
        assert check_level(dry.grid, dry.spawn, dry.exit, DEFAULT_MOVEMENT)

    def test_water_containment_rule(self) -> None:
        """GameRules decides: open-sided pools fail 'contained' with a
        locate-and-instruct message, pass 'free' (waterfall games)."""
        from examples.platformer_pack.rules import GameRules

        open_pool = stamp(
            "floor(0,47)\nwater(20,30,12)\nspawn(2)\nexit(45)", W, H
        )
        problems = check_level(
            open_pool.grid, open_pool.spawn, open_pool.exit, DEFAULT_MOVEMENT
        )
        assert problems and "spills out" in problems[0] and "wall(" in problems[0]

        free_rules = GameRules(water_containment="free")
        assert not check_level(
            open_pool.grid, open_pool.spawn, open_pool.exit, DEFAULT_MOVEMENT,
            rules=free_rules,
        )

    def test_rules_are_template_data(self, tmp_path: Path) -> None:
        """E.7 split: values load from a per-game file; unknown keys are
        carried into manifest.json inert (open carriage); known keys stay
        validated (hardened enforcement)."""
        import pydantic

        from examples.platformer_pack.rules import (
            DEFAULT_RULES_PATH,
            GameRules,
            load_rules,
        )

        # The pack's template file is the source of DEFAULT_RULES.
        assert load_rules(DEFAULT_RULES_PATH).water_containment == "contained"

        # A future rule sketched in data rides through, inert.
        custom = tmp_path / "my_game_rules.json"
        custom.write_text(json.dumps({
            "water_containment": "free",
            "lava_swimmable": True,  # no enforcement exists yet
        }))
        rules = load_rules(custom)
        assert rules.water_containment == "free"
        assert rules.model_dump()["lava_swimmable"] is True

        # Known keys stay validated — a typo'd VALUE fails loudly.
        with pytest.raises(pydantic.ValidationError):
            GameRules.model_validate({"water_containment": "sideways"})

    def test_manifest_carries_composed_rules(self, tmp_path: Path) -> None:
        """The manifest reflects the rules the run actually used —
        including inert extras — not the pack defaults."""
        from examples.platformer_pack.rules import GameRules

        run = tmp_path / "run"
        custom = GameRules(water_containment="free", lava_swimmable=True)
        ctx = PipelineContext(
            bible=Bible.empty(seed="s"),
            config=CanonConfig(seed="s", output_dir=run),
            rng=random.Random(0),
            llm=LLMClient(FakeLLMBackend(make_fake_responder())),
            prompts=PlatformerPrompts(),
        )
        run_pipeline(compose_pipeline(rules=custom), ctx)
        manifest = json.loads((run / "manifest.json").read_text())
        assert manifest["rules"]["water_containment"] == "free"
        assert manifest["rules"]["lava_swimmable"] is True

    def test_enemy_water_policy_rules(self) -> None:
        from examples.platformer_pack.rules import GameRules

        pool = stamp(
            "floor(0,47)\nwall(19,12,13)\nwall(31,12,13)\n"
            "water(20,30,12)\nspawn(2)\nexit(45)", W, H,
        )
        defs = {
            "fish": {"archetype": "swimmer", "size": 1.0},
            "beetle": {"archetype": "patroller", "size": 1.0},
        }
        in_water = [{"enemy_id": "fish", "x": 25, "y": 12},
                    {"enemy_id": "beetle", "x": 25, "y": 12}]

        # forbidden: nobody in water.
        accepted, problems, _ = check_placements(
            pool.grid, in_water, pool.spawn, defs,
            rules=GameRules(enemy_water_policy="forbidden"),
        )
        assert not accepted and len(problems) == 2
        # amphibious: everybody allowed.
        accepted, problems, _ = check_placements(
            pool.grid, in_water, pool.spawn, defs,
            rules=GameRules(enemy_water_policy="amphibious"),
        )
        assert len(accepted) == 2 and not problems

    def test_edit_detection_and_stale_cascade(self, tmp_path: Path) -> None:
        """Phase 2 §6.3 on real 3a data: a hand-edited collision file marks
        that step user_edited, its §6.1 descendants stale, ancestors and
        sibling levels untouched."""
        from canon.pipeline.orchestrator import detect_edits

        run = tmp_path / "run"
        ctx = _run_slice(run)

        # Pristine tree: nothing flagged.
        clean = detect_edits(ctx.bible, run)
        assert not clean.user_edited and not clean.stale and not clean.missing

        # Simulate a user editing l2's collision mask on disk.
        target = run / "level/ashen_depths/l2/collision.npz"
        target.write_bytes(target.read_bytes() + b"edited")
        report = detect_edits(ctx.bible, run)

        prefix = "level:ashen_depths/l2"
        assert report.user_edited == [f"{prefix}/collision"]
        # Descendants through step_parents edges — including transitive
        # (entities <- hazards <- collision).
        for step in ("terrain", "background", "hazards", "triggers",
                     "entities", "foreground"):
            assert f"{prefix}/{step}" in report.stale, step
        # Ancestors and sibling levels untouched.
        assert not any("l1" in aid or "l3" in aid for aid in report.stale)
        assert not any(aid.startswith("tileset") for aid in report.user_edited)
        # Statuses landed on the Bible: entity + node_status.
        from canon.bible.artifacts import ArtifactStatus

        assert ctx.bible.levels["l2"].status is ArtifactStatus.USER_EDITED
        assert (
            ctx.bible.metadata.node_status[f"{prefix}/collision"]
            is ArtifactStatus.USER_EDITED
        )
        assert (
            ctx.bible.metadata.node_status[f"{prefix}/terrain"]
            is ArtifactStatus.STALE
        )
        assert ctx.bible.levels["l1"].status is not ArtifactStatus.STALE

    def test_godot_engine_output(self, tmp_path: Path) -> None:
        """--engine godot: playable project files + grid.json siblings,
        with the canonical tree unchanged (npz + hashes identical)."""
        json_run, godot_run = tmp_path / "json", tmp_path / "godot"
        ctx_json = _run_slice(json_run)
        ctx_godot = _run_slice(godot_run, engine="godot")

        # Project files present.
        assert (godot_run / "project.godot").exists()
        assert (godot_run / "godot/main.tscn").exists()
        assert (godot_run / "godot/main.gd").exists()
        assert not (json_run / "project.godot").exists()  # json engine: none
        # 2D HDR ships statically: main.gd's "glow" effect interpreter is
        # silently inert without it.
        assert "viewport/hdr_2d=true" in (godot_run / "project.godot").read_text()

        # Grid siblings match the canonical npz content.
        for level in ctx_godot.bible.levels.values():
            sibling = godot_run / level.collision.replace(".npz", ".grid.json")
            with np.load(godot_run / level.collision) as data:
                canonical = data["collision"].tolist()
            assert json.loads(sibling.read_text())["collision"] == canonical

        # Canonical artifacts byte-identical across engines; hashes agree.
        for rel in ("world.json", "manifest.json"):
            assert (json_run / rel).read_bytes() == (godot_run / rel).read_bytes()
        for level_id, level in ctx_json.bible.levels.items():
            assert level.collision_hash == ctx_godot.bible.levels[level_id].collision_hash

    def test_godot_engine_deterministic(self, tmp_path: Path) -> None:
        run_a, run_b = tmp_path / "a", tmp_path / "b"
        _run_slice(run_a, engine="godot")
        _run_slice(run_b, engine="godot")
        assert_trees_byte_identical(run_a, run_b)

    def test_positive_generation_logging(self, tmp_path: Path, caplog) -> None:
        """Successful generations are logged at INFO (MazeWorld parity) —
        reviewers need evidence of what worked, not just what fell back."""
        import logging

        with caplog.at_level(logging.INFO):
            ctx = _run_slice(tmp_path / "run")
        text = caplog.text
        # Level counts include rolled secret rooms (multi-room arc) —
        # dynamic, since the roll is seed-dependent data.
        n = len(ctx.bible.levels)
        assert "WorldPhase produced world" in text
        assert "StagePhase planned stage" in text
        assert "EnemyGeneratorPhase produced a 4-creature world pool" in text
        assert re.search(r"Layout l1 \(difficulty 1, \d+x\d+\)", text)
        assert re.search(r"Layout l3 \(difficulty 3, \d+x\d+\)", text)
        assert "Placement l1: " in text
        assert f"TileAssignmentPhase mapped {n} levels" in text
        assert f"BackgroundPhase wrote {n}" in text
        assert "Decor l1: " in text
        assert "PlaceholderTilesetPhase wrote" in text
        assert f"RenderPhase wrote {n} level renders" in text
        assert "Slice complete" in text and "0 warning(s)" in text

    def test_provenance_stamped(self, tmp_path: Path) -> None:
        ctx = _run_slice(tmp_path / "run")
        for entity in (
            ctx.bible.world,
            *ctx.bible.stages.values(),
            *ctx.bible.enemy_definitions.values(),
            *ctx.bible.levels.values(),
            *ctx.bible.tilesets.values(),
        ):
            assert entity.provenance_hash.startswith("sha256:"), entity


# ---------------------------------------------------------------------------
# Phase 3b: tile registry, generic ops, checkpoints, variants, lava world
# ---------------------------------------------------------------------------


def _registry(tiles: list[dict]) -> TileRegistry:
    return TileRegistry.model_validate({"tiles": tiles})


_BASE_TILES = [
    {"id": 0, "name": "empty", "category": "empty"},
    {"id": 1, "name": "floor", "category": "solid"},
    {"id": 2, "name": "platform", "category": "one_way"},
    {"id": 3, "name": "wall", "category": "solid"},
]


class TestTileRegistry:
    def test_default_registry_mirrors_tiletype(self) -> None:
        """The framework-default enum and the pack's registry file must
        agree — the enum exists so framework code has stable names."""
        from examples.platformer_pack.tiles import DEFAULT_TILES

        assert {t.name.upper(): t.id for t in DEFAULT_TILES.tiles} == {
            m.name: int(m) for m in TileType
        }

    def test_band_violation_fails_loudly(self) -> None:
        import pydantic

        with pytest.raises(pydantic.ValidationError, match="band"):
            _registry(
                _BASE_TILES
                + [{"id": 5, "name": "acid", "category": "volume"}]  # <20
            )

    def test_missing_structural_tile_fails(self) -> None:
        import pydantic

        with pytest.raises(pydantic.ValidationError, match="floor"):
            _registry([t for t in _BASE_TILES if t["name"] != "floor"])

    def test_duplicate_ids_fail(self) -> None:
        import pydantic

        with pytest.raises(pydantic.ValidationError, match="duplicate"):
            _registry(
                _BASE_TILES
                + [{"id": 3, "name": "girder", "category": "solid"}]
            )

    def test_empty_tile_contract(self) -> None:
        import pydantic

        with pytest.raises(pydantic.ValidationError, match="empty"):
            _registry([t for t in _BASE_TILES if t["name"] != "empty"])

    def test_new_content_is_a_data_entry(self) -> None:
        """The template claim: swimmable lava and a laser hazard exist the
        moment they're written down — same categories, zero new code."""
        tiles = _registry(
            _BASE_TILES
            + [
                {"id": 11, "name": "laser", "category": "hazard"},
                {
                    "id": 21, "name": "lava", "category": "volume",
                    "params": {"damage_per_second": 1.5, "speed_factor": 0.4},
                },
            ]
        )
        result = stamp(
            "floor(0,47)\nwall(19,12,13)\nwall(31,12,13)\n"
            "volume(lava,20,30,12)\nhazard_strip(laser,40,41)\n"
            "spawn(2)\nexit(45)",
            W, H, tiles=tiles,
        )
        assert int(result.grid[12, 25]) == 21
        assert int(result.grid[13, 40]) == 11
        assert result.hazards[0].type == "floor_laser"
        assert not check_level(
            result.grid, result.spawn, result.exit, DEFAULT_MOVEMENT,
            tiles=tiles,
        )


class TestBreakableFloors:
    """Chunk D: breakable(x1,x2) stamps a crumbling floor — a solid foothold
    over a bottomless pit that a consumer's fuse removes in play."""

    def test_breakable_stamps_solid_over_a_cleared_pit(self) -> None:
        from examples.platformer_pack.tiles import DEFAULT_TILES

        bid = DEFAULT_TILES.by_name["breakable"].id
        assert DEFAULT_TILES.by_name["breakable"].category == "solid"
        res = stamp(
            "floor(0,9)\nbreakable(10,12)\nfloor(13,23)\nspawn(2)\nexit(22)",
            24, 16,
        )
        g = res.grid
        # breakable on the ground row, bedrock CLEARED below (a pit to fall in)
        assert [int(g[14, x]) for x in (9, 10, 12, 13)] == [1, bid, bid, 1]
        assert [int(g[15, x]) for x in (10, 11, 12)] == [0, 0, 0]

    def test_breakable_is_a_reachable_foothold(self) -> None:
        # A solid breakable counts as footing, so a level bridged only by a
        # breakable stretch is reachable (v1 ignores the break timing).
        from examples.platformer_pack.validate import reachable_cells

        res = stamp(
            "floor(0,9)\nbreakable(10,12)\nfloor(13,23)\nspawn(2)\nexit(22)",
            24, 16,
        )
        assert res.exit in reachable_cells(res.grid, res.spawn, DEFAULT_MOVEMENT)
        assert not check_level(
            res.grid, res.spawn, res.exit, DEFAULT_MOVEMENT
        )

    def test_breakable_rejected_without_registry_tile(self) -> None:
        # A game whose registry has no breakable tile rejects the op loudly.
        from examples.platformer_pack.tiles import load_tiles

        no_break = load_tiles()
        no_break.tiles = [t for t in no_break.tiles if t.name != "breakable"]
        with pytest.raises(DslError, match="no 'breakable' tile"):
            stamp("floor(0,9)\nbreakable(3,5)\nspawn(2)\nexit(8)",
                  10, 16, tiles=no_break)


class TestGenericOps:
    def test_aliases_resolve_through_registry(self) -> None:
        """water()/spike() are ergonomic spellings of volume()/
        hazard_strip() — identical grids either way."""
        via_alias = stamp(_FAKE_LAYOUTS["l1"], W, H)
        via_generic = stamp(
            _FAKE_LAYOUTS["l1"]
            .replace("volume(water,", "water(")
            .replace("hazard_strip(spike,", "spike("),
            W, H,
        )
        assert (via_alias.grid == via_generic.grid).all()

    def test_alias_without_tile_names_the_vocabulary(self) -> None:
        """A game without water must tell the agent what it has instead."""
        lava_only = _registry(
            _BASE_TILES
            + [
                {"id": 10, "name": "spike", "category": "hazard"},
                {"id": 20, "name": "lava", "category": "volume"},
            ]
        )
        with pytest.raises(DslError, match=r"no tile named 'water'.*lava"):
            stamp("floor(0,47)\nwater(20,30,12)\nspawn(2)\nexit(45)",
                  W, H, tiles=lava_only)

    def test_category_mismatch_is_rejected(self) -> None:
        with pytest.raises(DslError, match="'spike' is a hazard tile"):
            stamp("floor(0,47)\nvolume(spike,20,30,12)\nspawn(2)\nexit(45)",
                  W, H)

    def test_name_arg_must_be_identifier(self) -> None:
        with pytest.raises(DslError, match="tile NAME first"):
            stamp("floor(0,47)\nvolume(7,20,30,12)\nspawn(2)\nexit(45)", W, H)

    def test_pool_op_carves_a_flush_contained_pool(self) -> None:
        """pool() sinks a volume INTO the ground: surface flush with the
        walking row, floor banks contain it, bedrock is the basin — the
        construction real models kept attempting via volume()-over-pit."""
        result = stamp(
            "floor(0,47)\npool(water,20,25)\nspawn(2)\nexit(45)", W, H
        )
        assert int(result.grid[H - 2, 22]) == TileType.WATER  # flush
        assert int(result.grid[H - 1, 22]) == TileType.WALL  # bedrock basin
        assert int(result.grid[H - 2, 19]) == TileType.FLOOR  # bank
        # Contained + reachable with no extra ops.
        assert not check_level(
            result.grid, result.spawn, result.exit, DEFAULT_MOVEMENT
        )
        # Swimmers live in it (flush pools are volume cells like any).
        accepted, problems, _ = check_placements(
            result.grid,
            [{"enemy_id": "fish", "x": 22, "y": 14}],
            result.spawn,
            {"fish": {"archetype": "swimmer", "size": 1.0}},
        )
        assert accepted and not problems

    def test_pool_off_floor_is_dropped_with_a_note(self) -> None:
        # §1b #3: a pool aimed at never-floored columns used to raise; now
        # it clips to floored columns — none here, so the op drops, and the
        # note still names the occupying tile per column (located).
        res = stamp("floor(0,10)\npool(water,20,25)\nspawn(2)\nexit(8)", W, H)
        note = next(r for r in res.repairs if r.startswith("pool("))
        assert "20 (empty)" in note and "dropped" in note
        water_id = load_tiles().by_name["water"].id
        assert not (res.grid == water_id).any()

    def test_no_basin_pour_keeps_water_as_free_feature(self) -> None:
        """The no-basin loop (volume poured over a pit, three identical
        real-model retries into the l8 whole-flat) now KEEPS the water as a
        free-standing spout feature instead of raising (§1b #3)."""
        res = stamp(
            "floor(0,47)\ngap(20,24)\nwater(20,24,12)\nspawn(2)\nexit(45)",
            W, H,
        )
        assert any(
            "no solid basin" in r and "free-standing" in r
            for r in res.repairs
        )
        water_id = load_tiles().by_name["water"].id
        assert int(res.grid[13, 22]) == water_id
        assert (22, 13) in res.free_volume

    def test_pour_on_ground_row_snaps_up_in_code(self) -> None:
        """The observed real-model failure loop: surface poured ON the
        ground floor row. The old error message computed the exact fix
        ("use surface row N-1") — computable fixes are tool work, so the
        stamp now APPLIES it: snap the surface one row up, loudly."""
        # H=16 → ground row 14; pouring at 14 hits the floor itself.
        result = stamp(
            "floor(0,47)\nwater(20,25,14)\nspawn(2)\nexit(45)", W, H
        )
        water_id = load_tiles().by_name["water"].id
        assert all(
            int(result.grid[13, x]) == water_id for x in range(20, 26)
        )
        assert int(result.grid[14, 20]) == load_tiles().by_name["floor"].id
        assert len(result.repairs) == 1
        assert "snapped to open row 13" in result.repairs[0]

    def test_pour_on_terrain_snaps_up_in_code(self) -> None:
        """The l4 fallback: the model poured a water surface onto a hill/
        wall, three 'surface must be open air' rejects into a flat level.
        Lifting the surface to the first open row is arithmetic, so the
        stamp now APPLIES it instead of burning retries."""
        water_id = load_tiles().by_name["water"].id
        # A wall pillar at column 20 (rows 10-14) with water aimed at row 12
        # — that row is solid at column 20, so it must snap up above it.
        result = stamp(
            "floor(0,47)\nwall(20,10,14)\nwater(19,21,12)\nspawn(2)\nexit(45)",
            W, H,
        )
        assert result.repairs and "snapped up to the first open row" in (
            result.repairs[0]
        )
        # Water now sits above the pillar top (row 9), not inside it.
        assert int(result.grid[9, 19]) == water_id
        assert int(result.grid[12, 20]) != water_id  # the wall is intact

    def test_pour_on_ground_row_under_blockers_repairs_not_raises(
        self,
    ) -> None:
        """When the on-top surface row is partly blocked (the third real l3
        run poured 24-30 under its own spike strip at 28-30), the pour used
        to hard-raise 'cannot share the surface'. The repair chain now
        composes: snap off the ground row → lift above the blocking terrain
        → pour (blocked columns become a thin free film over the spikes) —
        every step recorded, water kept, spikes intact and lethal."""
        res = stamp(
            "floor(0,47)\nspike(22,23)\nwater(20,25,14)\n"
            "spawn(2)\nexit(45)",
            W, H,
        )
        assert any("snapped to row 13" in r for r in res.repairs)
        assert any("snapped up to the first open row" in r for r in res.repairs)
        water_id = load_tiles().by_name["water"].id
        assert int(res.grid[13, 20]) == water_id  # open column keeps water
        spike_id = load_tiles().by_name["spike"].id
        assert int(res.grid[13, 22]) == spike_id  # blocker intact
        assert int(res.grid[12, 22]) == water_id  # film above, free water
        assert (22, 12) in res.free_volume

    def test_layout_prompt_carries_pool_recipe(self, tmp_path: Path) -> None:
        """Teach the recipe up front, not only in retry feedback."""
        good = make_fake_responder()
        prompts = []

        def spy(request):
            if "### TASK: layout" in request.user_message:
                prompts.append(request.user_message)
            return good(request)

        _run_slice(tmp_path / "run", responder=spy)
        assert prompts
        for message in prompts:
            assert "Pool recipe" in message
            assert "OPEN AIR" in message

    def test_layout_system_prompt_states_arg_contract(self) -> None:
        """Per-task system prompt tells the layout agent the op-argument
        contract — the l8 4-arg-wall fix works at the system level, backed
        by an isolated wall example in the vocab."""
        req = PlatformerPrompts().layout_generation(
            "l1", "a brief", {"difficulty": 1}, W, H, DEFAULT_MOVEMENT,
        )
        assert "argument COUNT" in req.system
        assert "never more" in req.system.lower()
        # A worked, in-bounds wall example (column anchored to width now).
        wm = re.search(r"Example: wall\((\d+), (\d+), (\d+)\)", req.user_message)
        assert wm and int(wm.group(1)) < W
        assert "NOT a rectangle" in req.user_message

    def test_section_prompt_reflects_archetype_character(self) -> None:
        """Chunk B: a section prompt translates the archetype's feature_bias
        into a DISTINCT op backbone, plus intensity pacing + water level — so
        a gauntlet, a cave, and a runway read as different sections to the
        Layout Agent (same legal vocabulary throughout)."""
        from examples.platformer_pack.sections import DEFAULT_VOCAB

        p = PlatformerPrompts()

        def sec(name: str) -> str:
            a = DEFAULT_VOCAB[name]
            return p.section_layout(
                "l1", "b", name, a.flavor, a.feature_bias, 1, 3, 24, H,
                DEFAULT_MOVEMENT, intensity=a.intensity, water=a.water,
            ).user_message

        g, c, r = sec("gauntlet"), sec("cave"), sec("runway")
        # Distinct backbone families -> distinct ops emphasised.
        assert "build it mostly from platform, ledge, gap, pit" in g
        assert "build it mostly from floor, wall, carve" in c
        assert "build it mostly from floor" in r
        assert "do NOT use gap, pit, hazard_strip" in r
        # Water dimension: a dry runway vs an optional-water gauntlet.
        assert "DRY — place NO water" in r
        assert "Water is OPTIONAL here" in g
        # Intensity pacing differs (high gauntlet vs low runway).
        assert "dense and demanding" in g
        assert "breather" in r

    def test_section_prompt_teaches_clouds_and_alcoves(self) -> None:
        """Chunk E: the section vocabulary offers water_cloud + reward, teaches
        floating swim-up clouds and secret alcoves, and a cave's feature_bias
        translates the new families (cloud -> water_cloud, secret -> reward)."""
        from examples.platformer_pack.sections import DEFAULT_VOCAB

        a = DEFAULT_VOCAB["cave"]
        msg = PlatformerPrompts().section_layout(
            "l1", "b", "cave", a.flavor, a.feature_bias, 1, 3, 24, H,
            DEFAULT_MOVEMENT, intensity=a.intensity, water=a.water,
        ).user_message
        # Ops advertised.
        assert "water_cloud(x1,y1,x2,y2)" in msg
        assert "reward(x,y)" in msg
        # Feature guidance present.
        assert "floating CLOUD of water" in msg
        assert "Secret alcoves" in msg
        # Cave bias (cloud:1, secret:2) sprinkles the new ops in.
        assert "sprinkle in" in msg and "water_cloud" in msg and "reward" in msg

    def test_section_prompt_example_coords_fit_a_narrow_vertical(self) -> None:
        """G5: worked-example COLUMNS are anchored to the section width, so a
        narrow vertical shaft never advertises out-of-bounds columns (the
        hardcoded 19/20/25/26 overran a 16-26-wide climb and the model copied
        them into a DslError). The floorless branch also frames the valid
        columns and forbids the floor-dependent ops."""
        W, H = 16, 24
        m = PlatformerPrompts().section_layout(
            "x/l7", "climb", "ascent", "up", {}, 1, 3, W, H,
            DEFAULT_MOVEMENT, axis="vertical", water="optional",
        ).user_message
        first = [
            int(c)
            for c in re.findall(
                r"(?:wall|platform|ledge|carve|floor|gap)\((\d+)", m
            )
        ]
        second = [
            int(c)
            for c in re.findall(
                r"(?:pool|volume|water_cloud|water_block)\(\w+,(\d+)", m
            )
        ]
        assert first and all(c < W for c in first + second)  # all in-bounds
        assert "NO ground floor" in m and f"valid columns 0..{W - 1}" in m
        assert "do NOT use floor/pool/hazard_strip" in m

    def test_vertical_section_prompt_forbids_exit_and_teaches_rungs(
        self,
    ) -> None:
        """§1b #1 prevention: the vertical branch never forbade exit() (the
        horizontal one always did) — every paid climb section emitted one.
        And the climb backbone must be ONE-WAY platform() rungs: the physics
        cannot mount through a solid ledge, so ledges are side tiers, never
        full-width. The seam hand-off must speak the RECEIVING section's row
        numbers (the seam frame bug left giver-frame rows in)."""
        W, H = 19, 29
        m = PlatformerPrompts().section_layout(
            "x/l7", "climb", "ascent", "up", {}, 1, 3, W, H,
            DEFAULT_MOVEMENT, axis="vertical", water="optional",
            seam="standable footing near the top — y=25: x 3-5",
        ).user_message
        assert "Do NOT place an exit" in m
        assert "jump UP THROUGH" in m
        assert "use ledges sparingly" in m
        assert "IN YOUR OWN row numbers" in m
        assert f"rows {H - 6}..{H - 1} overlap" in m
        # The first (bottom) section also must not place an exit.
        first = PlatformerPrompts().section_layout(
            "x/l7", "climb", "ascent", "up", {}, 0, 3, W, H,
            DEFAULT_MOVEMENT, axis="vertical", water="optional",
        ).user_message
        assert "Do NOT place an exit" in first

    def test_physics_guidance_speaks_the_validators_vocabulary(self) -> None:
        """Ticket 4: the paid l7 loop rejected on 'max jump distance 4' — a
        number the prompt never stated in that vocabulary. The physics block
        now names it foothold-to-foothold, tables max_dx_for_rise per rise,
        and derives the running-jump figure from the movement object."""
        from examples.platformer_pack.movement import run_jump_width

        m = PlatformerPrompts().section_layout(
            "l1", "b", "gauntlet", "f", {}, 1, 3, 24, H, DEFAULT_MOVEMENT,
        ).user_message
        assert "max jump distance 4 columns foothold-to-foothold" in m
        assert "rise 1 -> 4, rise 2 -> 4, rise 3 -> 3" in m
        rj = run_jump_width(DEFAULT_MOVEMENT)
        assert rj == 6  # default physics: the prompt value is unchanged
        assert f"clears up to ~{rj} columns" in m
        # The whole-level prompt shares the same paragraph.
        whole = PlatformerPrompts().layout_generation(
            "l1", "b", {"difficulty": 1}, W, H, DEFAULT_MOVEMENT,
        ).user_message
        assert "max jump distance 4 columns foothold-to-foothold" in whole

    def test_overridden_physics_reshapes_the_prompt_numbers(self) -> None:
        """Ticket 4: a per-level movement override (the low-gravity finale,
        gravity 20) must change the DERIVED numbers — longer hang time
        widens the advertised running jump and lifts the full-rise reach to
        the width cap — so no '~6' literal survives to lie about this
        level's own physics."""
        from examples.platformer_pack.movement import (
            PlayerMovementSpec,
            run_jump_width,
        )

        low_g = PlayerMovementSpec.model_validate(
            {**DEFAULT_MOVEMENT.model_dump(), "gravity": 20.0}
        )
        rj = run_jump_width(low_g)
        assert rj > 6
        m = PlatformerPrompts().section_layout(
            "l9", "b", "gauntlet", "f", {}, 1, 3, 24, H, low_g,
        ).user_message
        assert f"clears up to ~{rj} columns" in m
        assert f"wider than ~{rj} columns" in m
        assert f"(4-{rj} column) gap" in m
        assert "~6 columns" not in m  # the hardcoded literal is gone
        assert "rise 1 -> 4, rise 2 -> 4, rise 3 -> 4" in m
        assert "max jump distance 4 columns" in m  # jump_width untouched

    def test_difficulty_three_adds_the_challenge_paragraph(self) -> None:
        """Ticket 4: difficulty >= 3 appends ONE paragraph steering the
        model to build challenge from enemies/hazards/precision INSIDE the
        physics envelope; lower difficulties (and the default, so the
        level.py threading lands independently) keep the shorter prompt."""

        def sec(difficulty: int) -> str:
            return PlatformerPrompts().section_layout(
                "l1", "b", "gauntlet", "f", {}, 1, 3, 24, H,
                DEFAULT_MOVEMENT, difficulty=difficulty,
            ).user_message

        hard = sec(3)
        assert "HIGH-DIFFICULTY" in hard
        assert "the simulator rejects anything beyond them" in hard
        for d in (1, 2):
            assert "HIGH-DIFFICULTY" not in sec(d)
        base = PlatformerPrompts().section_layout(
            "l1", "b", "gauntlet", "f", {}, 1, 3, 24, H, DEFAULT_MOVEMENT,
        ).user_message
        assert base == sec(1)  # omitting the kwarg is difficulty 1

    def test_enemy_system_prompt_forbids_extra_fields(self) -> None:
        """Per-task system prompt: the enemy agent returns only the named
        fields and never restates the rolled mechanics (schema-aware
        generation — the general fix the user asked for)."""
        req = PlatformerPrompts().enemy_generation(
            {"archetype": "sentry", "hp": 9}, "a theme", "a roster", 0,
        )
        low = req.system.lower()
        assert "return only" in low
        assert "add no other fields" in low


class TestCheckpoints:
    def test_checkpoint_lands_in_triggers(self) -> None:
        result = stamp(
            "floor(0,47)\ncheckpoint(20)\nspawn(2)\nexit(45)", W, H
        )
        assert [(t.x, t.y, t.type) for t in result.triggers] == [
            (20, H - 3, "checkpoint")
        ]

    def test_checkpoint_needs_floor(self) -> None:
        with pytest.raises(DslError, match="checkpoint.*no floor"):
            stamp("floor(0,10)\ncheckpoint(20)\nspawn(2)\nexit(8)", W, H)

    def test_duplicate_checkpoint_column_rejected(self) -> None:
        with pytest.raises(DslError, match="more than once"):
            stamp(
                "floor(0,47)\ncheckpoint(20)\ncheckpoint(20)\n"
                "spawn(2)\nexit(45)", W, H,
            )


class TestSecretAlcoves:
    """Chunk E: reward(x,y) marks a hidden collectible inside a carved niche.
    Layout-only — it lands in the triggers layer (a NEW trigger type) and the
    play surfaces ignore it, so it survives the stitcher untouched."""

    def test_reward_lands_in_triggers_in_open_air(self) -> None:
        # A niche carved into a wall, a reward tucked inside.
        result = stamp(
            "floor(0,47)\nwall(20,10,13)\ncarve(20,11,20,11)\nreward(20,11)\n"
            "spawn(2)\nexit(45)", W, H,
        )
        assert [(t.x, t.y, t.type) for t in result.triggers] == [
            (20, 11, "reward")
        ]

    def test_reward_on_solid_is_dropped_not_raised(self) -> None:
        # code-not-LLM: a reward on solid is a cosmetic mistake — dropped with a
        # repair note, never a fallback.
        result = stamp(
            "floor(0,47)\nwall(20,10,13)\nreward(20,11)\nspawn(2)\nexit(45)",
            W, H,
        )
        assert not any(t.type == "reward" for t in result.triggers)
        assert any("reward" in r for r in result.repairs)

    def test_reward_covered_by_a_later_op_is_dropped(self) -> None:
        """Records mirror the FINAL grid (like hazards): a reward whose cell a
        later op fills is not emitted as a phantom trigger."""
        result = stamp(
            "floor(0,47)\nreward(20,8)\nwall(20,7,9)\nspawn(2)\nexit(45)", W, H,
        )
        assert not any(t.type == "reward" for t in result.triggers)

    def test_reward_survives_snap_and_composite(self) -> None:
        """A reward is a non-checkpoint trigger: snap_checkpoints_grid leaves
        it untouched, and composite offsets it by the section origin — so it
        rides through the stitcher into the whole level."""
        from examples.platformer_pack.sections import composite
        from examples.platformer_pack.validate import snap_checkpoints_grid

        sec = stamp(
            "floor(0,23)\nwall(10,10,13)\ncarve(10,11,10,11)\nreward(10,11)\n"
            "spawn(2)", 24, H, validate_markers=False,
        )
        whole = composite([(sec, 30, 0)], 60, H)
        assert (40, 11, "reward") in [
            (t.x, t.y, t.type) for t in whole.triggers
        ]
        kept, moves = snap_checkpoints_grid(whole.grid, whole.triggers)
        assert (40, 11, "reward") in [(t.x, t.y, t.type) for t in kept]
        assert not moves  # only checkpoints move

    def test_fake_cave_section_hides_a_reward_niche(self) -> None:
        """The $0 fake exercises the alcove path: a cave section (with the
        reward + cloud ops advertised) stamps clean and tucks a reward into a
        niche off the floor, plus swimmable cloud water."""
        from examples.run_platformer_slice import _fake_section

        dsl = _fake_section(
            24, 16, "cave", 1, 3, "water", "spike",
            has_water_cloud=True, has_reward=True,
        )
        assert "reward(" in dsl
        res = stamp(dsl, 24, 16, validate_markers=False)
        assert any(t.type == "reward" for t in res.triggers)
        assert (res.grid == int(TileType.WATER)).any()  # the cloud

    def test_fake_climb_section_floats_a_water_cloud(self) -> None:
        from examples.run_platformer_slice import _fake_section

        dsl = _fake_section(
            18, 40, "climb", 0, 2, "water", "spike", has_water_cloud=True,
        )
        assert "volume_cloud(" in dsl  # registry-generic cloud spelling
        res = stamp(dsl, 18, 40, validate_markers=False)
        assert (res.grid == int(TileType.WATER)).any()


#: Verbatim from review/ashen_grove/l3_layout_attempts.json of the third
#: real (paid) run — the trace machinery exists exactly so failures turn
#: into fixtures. 64x18, difficulty 3.
_L3_ATTEMPT_1 = (
    "spawn(2)\nfloor(0,63)\ngap(14,17)\ngap(38,41)\n"
    "platform(20,13,4)\nplatform(44,13,4)\nplatform(10,14,3)\n"
    "platform(50,11,3)\nhazard_strip(spike,6,9)\nhazard_strip(spike,28,30)\n"
    "hazard_strip(spike,55,57)\nwall(23,14,16)\nvolume(water,24,30,16)\n"
    "wall(31,14,16)\nwall(47,13,16)\nvolume(water,48,52,13)\n"
    "wall(53,13,16)\ncheckpoint(34)\nexit(61)"
)


class TestL3TraceRegression:
    """Replays of the third real run's failed l3 attempts (the trace the
    attempt-log machinery was built to capture). Attempts 1-2 failed ONLY
    on op order (spawn declared one line above its floor); the genuine
    design conflict (pool poured under its own spike strip) stayed hidden
    until attempt 3 because one error was reported per attempt."""

    def test_spawn_before_floor_is_not_an_error(self) -> None:
        """Attempts 1-2's only reported reason must no longer fire: the
        final grid HAS floor under column 2."""
        try:
            stamp(_L3_ATTEMPT_1, 64, 18)
        except DslError as exc:
            assert not any("spawn" in p for p in exc.problems)

    def test_real_conflict_repairs_on_attempt_one(self) -> None:
        """The pool/spike overlap (24-30 vs 28-30) used to hide behind the
        spawn false positive for two attempts, then hard-raise located on
        the third. It now costs ZERO attempts (§1b #3): the surface snaps
        off the ground row, lifts above the spikes, and the spike columns
        keep a free water film — every step recorded, spikes intact."""
        res = stamp(_L3_ATTEMPT_1, 64, 18)
        assert any("snapped to row 15" in r for r in res.repairs)
        assert any(
            "no solid basin" in r and "[28, 29, 30]" in r
            for r in res.repairs
        )
        water_id = load_tiles().by_name["water"].id
        spike_id = load_tiles().by_name["spike"].id
        assert all(int(res.grid[14, x]) == water_id for x in range(24, 31))
        assert all(int(res.grid[15, x]) == water_id for x in range(24, 28))
        assert all(int(res.grid[15, x]) == spike_id for x in range(28, 31))
        assert {(28, 14), (29, 14), (30, 14)} <= res.free_volume

    def test_deconflicted_layout_stamps_with_snap_repair(self) -> None:
        """Drop the conflicting spike strip and the layout the model
        designed is valid: the first pool snaps onto the floor top in
        code, the second pool was always fine."""
        text = _L3_ATTEMPT_1.replace("hazard_strip(spike,28,30)\n", "")
        result = stamp(text, 64, 18)
        water_id = load_tiles().by_name["water"].id
        assert result.spawn == (2, 15)
        assert all(
            int(result.grid[15, x]) == water_id for x in range(24, 31)
        )  # pool A snapped to the standing row
        assert all(
            int(result.grid[y, x]) == water_id
            for x in range(48, 53)
            for y in (13, 14, 15)
        )  # pool B as designed
        assert len(result.repairs) == 1
        assert "volume(water,24,30,16)" in result.repairs[0]
        assert (34, 15, "checkpoint") in [
            (t.x, t.y, t.type) for t in result.triggers
        ]

    def test_marker_order_is_irrelevant(self) -> None:
        """Attempt 3 differed from attempt 1 only by marker position —
        the two orderings must stamp identical levels."""
        text = _L3_ATTEMPT_1.replace("hazard_strip(spike,28,30)\n", "")
        reordered = "\n".join(
            [ln for ln in text.splitlines() if not ln.startswith("spawn")]
            + ["spawn(2)"]
        )
        a, b = stamp(text, 64, 18), stamp(reordered, 64, 18)
        assert (a.grid == b.grid).all()
        assert a.spawn == b.spawn and a.exit == b.exit
        assert a.repairs == b.repairs

    def test_all_marker_problems_reported_together(self) -> None:
        """Two marker failures must land in ONE attempt's feedback —
        serialized discovery burned the l3 retry budget."""
        with pytest.raises(DslError) as exc_info:
            stamp(
                "floor(10,20)\nspawn(2)\ncheckpoint(30)\nexit(15)", W, H
            )
        problems = exc_info.value.problems
        assert any(p.startswith("spawn") for p in problems)
        assert any(p.startswith("checkpoint") for p in problems)


class TestSteppedSlopes:
    """Slopes v1: staircase/pyramid ops — stacked flat solids over the
    existing physics; no new collision category."""

    def test_stairs_up_profile(self) -> None:
        result = stamp("floor(0,47)\nstairs_up(10,13)\nspawn(2)\nexit(45)", W, H)
        floor_id = int(TileType.FLOOR)
        # H=16 → ground row 14. Column 10: 1 block (row 13); column 13: 4.
        for i, x in enumerate(range(10, 14)):
            h = i + 1
            assert all(
                int(result.grid[14 - k, x]) == floor_id for k in range(1, h + 1)
            ), f"column {x} should be {h} blocks tall"
            assert int(result.grid[14 - h - 1, x]) == 0  # air above the step

    def test_stairs_down_and_pyramid_profiles(self) -> None:
        down = stamp("floor(0,47)\nstairs_down(10,12)\nspawn(2)\nexit(45)", W, H)
        assert int(down.grid[11, 10]) == int(TileType.FLOOR)  # 3 tall
        assert int(down.grid[13, 12]) == int(TileType.FLOOR)  # 1 tall
        assert int(down.grid[12, 12]) == 0
        hill = stamp("floor(0,47)\npyramid(20,24)\nspawn(2)\nexit(45)", W, H)
        heights = [1, 2, 3, 2, 1]
        for i, x in enumerate(range(20, 25)):
            top = 14 - heights[i]
            assert int(hill.grid[top, x]) == int(TileType.FLOOR)
            assert int(hill.grid[top - 1, x]) == 0

    def test_stairs_auto_lay_floor_when_off_the_ground(self) -> None:
        # code-not-LLM: a ramp with no ground under it gets floor laid, not a
        # fallback (the #-heavy real-run failure class).
        res = stamp("floor(0,20)\nstairs_up(25,27)\nspawn(2)\nexit(18)", W, H)
        assert int(res.grid[H - 2, 25]) == TileType.FLOOR  # floor laid under it
        assert int(res.grid[H - 2, 26]) == TileType.FLOOR
        assert int(res.grid[H - 3, 26]) == TileType.FLOOR  # a step rose
        assert any("laid floor" in r for r in res.repairs)

    def test_hazard_strip_over_gap_becomes_a_pit(self) -> None:
        # A hazard with no floor auto-converts to a PIT (hazard at the bottom),
        # and a hazard over a WALL sits on it (any solid counts, not just floor).
        pit = stamp(
            "floor(0,20)\ngap(10,12)\nhazard_strip(spike,10,12)\nspawn(2)\n"
            "exit(18)", W, H,
        )
        assert int(pit.grid[H - 1, 11]) == TileType.SPIKE  # hazard at the bottom
        assert any("pit" in r for r in pit.repairs)
        on_wall = stamp(
            "floor(0,20)\nwall(10,11,14)\nhazard_strip(spike,10,10)\nspawn(2)\n"
            "exit(18)", W, H,
        )
        assert int(on_wall.grid[H - 3, 10]) == TileType.SPIKE  # sits on the wall

    def test_tall_stairs_cap_leaves_air_above(self) -> None:
        # A 20-column ramp on a 16-tall grid plateaus instead of sealing
        # the level: cap = ground_row - 2 = 12 blocks.
        result = stamp("floor(0,47)\nstairs_up(10,29)\nspawn(2)\nexit(45)", W, H)
        assert int(result.grid[2, 29]) == int(TileType.FLOOR)  # capped top
        assert int(result.grid[1, 29]) == 0  # air stays above

    def test_stepped_slope_is_climbable(self) -> None:
        """Every riser is 1 cell — reachability over the whole slope must
        hold with the standard movement (the whole point of v1 slopes)."""
        result = stamp(
            "floor(0,47)\npyramid(18,26)\nspawn(2)\nexit(45)", W, H
        )
        problems = check_level(
            result.grid, result.spawn, result.exit, DEFAULT_MOVEMENT
        )
        assert not problems

    def test_fake_layouts_carry_stairs(self) -> None:
        for level_id, (w, h, _d) in _REFERENCE_DIMS.items():
            assert "stairs_up(" in _FAKE_LAYOUTS[level_id]
            result = stamp(_FAKE_LAYOUTS[level_id], w, h)
            problems = check_level(
                result.grid, result.spawn, result.exit, DEFAULT_MOVEMENT,
                triggers=result.triggers,
                free_volume=result.free_volume,
            )
            assert not problems, (level_id, problems)

    def test_water_wall_fills_down_to_terrain_and_skips_containment(self) -> None:
        """Water as a FEATURE (playtest direction): a free-standing wall
        of water the player swims up — deliberately exempt from the
        basin/containment rule."""
        result = stamp(
            "floor(0,47)\nwater_wall(10,11,6)\nspawn(2)\nexit(45)", W, H,
        )
        # Filled from row 6 down to the floor (ground row H-2), 2 wide.
        assert int(result.grid[6, 10]) == TileType.WATER
        assert int(result.grid[H - 3, 11]) == TileType.WATER
        assert int(result.grid[5, 10]) == TileType.EMPTY
        assert (10, 6) in result.free_volume
        problems = check_level(
            result.grid, result.spawn, result.exit, DEFAULT_MOVEMENT,
            free_volume=result.free_volume,
        )
        assert not problems
        # Without the exemption the same grid would (correctly) spill —
        # proving the rule still guards ordinary pools.
        assert check_level(
            result.grid, result.spawn, result.exit, DEFAULT_MOVEMENT
        )

    def test_water_wall_over_a_pit_runs_out_the_bottom(self) -> None:
        """A spout: over a bottomless gap the wall reaches the bottom
        edge — sinking past it is a fall death, the deliberate hazard the
        user asked for. (Over a pit() the spikes at the bottom stop the
        fill — also a legitimate spout floor.)"""
        result = stamp(
            "floor(0,47)\ngap(10,11)\nwater_wall(10,11,8)\nspawn(2)\nexit(45)",
            W, H,
        )
        assert int(result.grid[H - 1, 10]) == TileType.WATER  # bottom edge

    def test_water_wall_is_climbable_by_reachability(self) -> None:
        """Swim up the wall, leap out at the top — the existing volume
        reachability rules make water walls vertical paths for free."""
        from examples.platformer_pack.validate import reachable_cells

        # A high ledge reachable ONLY through the adjacent water wall.
        text = (
            "floor(0,47)\nledge(13,16,6)\nwater_wall(10,11,5)\n"
            "spawn(2)\nexit(45)"
        )
        result = stamp(text, W, H)
        problems = check_level(
            result.grid, result.spawn, result.exit, DEFAULT_MOVEMENT,
            free_volume=result.free_volume,
            triggers=result.triggers,
        )
        assert not problems
        reached = reachable_cells(result.grid, result.spawn, DEFAULT_MOVEMENT)
        assert (14, 5) in reached  # standing on the ledge, via the water

    def test_water_block_floats_and_clips_occupied_cells(self) -> None:
        result = stamp(
            "floor(0,47)\nwater_block(20,4,22,5)\nspawn(2)\nexit(45)", W, H,
        )
        assert int(result.grid[4, 21]) == TileType.WATER
        assert (21, 4) in result.free_volume
        problems = check_level(
            result.grid, result.spawn, result.exit, DEFAULT_MOVEMENT,
            free_volume=result.free_volume,
        )
        assert not problems
        # code-not-LLM: a block poured ON the ground floor CLIPS to open air
        # (nothing open here -> dropped, with a repair note) instead of raising.
        clipped = stamp(
            f"floor(0,47)\nwater_block(20,{H - 2},22,{H - 2})\n"
            "spawn(2)\nexit(45)", W, H,
        )
        assert int(clipped.grid[H - 2, 21]) == TileType.FLOOR  # floor kept
        assert (21, H - 2) not in clipped.free_volume  # no water on the floor
        assert any("clipped" in r for r in clipped.repairs)

    def test_water_cloud_puffs_the_corners_and_is_swim_up(self) -> None:
        """Chunk E: water_cloud is a free-water swim-up pocket like
        water_block, but its four corners are trimmed to a rounded silhouette
        (only when big enough — the swim-up centre column always survives),
        and it is containment-exempt."""
        from examples.platformer_pack.validate import reachable_cells

        # 5 wide x 3 tall -> corners trimmed, centre full.
        result = stamp(
            "floor(0,47)\nwater_cloud(20,4,24,6)\nspawn(2)\nexit(45)", W, H,
        )
        assert int(result.grid[4, 20]) == TileType.EMPTY  # trimmed corner
        assert int(result.grid[4, 24]) == TileType.EMPTY
        assert int(result.grid[5, 22]) == TileType.WATER  # centre body
        assert int(result.grid[6, 20]) == TileType.EMPTY  # trimmed corner
        assert (22, 5) in result.free_volume
        # Containment-exempt (free water FEATURE), reachable as a swim path.
        assert not check_level(
            result.grid, result.spawn, result.exit, DEFAULT_MOVEMENT,
            free_volume=result.free_volume,
        )
        # A high ledge reachable ONLY by swimming up through the cloud (same
        # geometry the water_wall climb test proves, via free-water swim rules).
        climb = stamp(
            "floor(0,47)\nledge(13,16,6)\nwater_cloud(10,5,11,13)\n"
            "spawn(2)\nexit(45)", W, H,
        )
        reached = reachable_cells(climb.grid, climb.spawn, DEFAULT_MOVEMENT)
        assert (14, 5) in reached  # standing on the high ledge, via the cloud

    def test_water_cloud_small_stays_rectangular_and_clips_solids(self) -> None:
        """A 2-tall (or narrow) cloud keeps every cell — no degenerate trim —
        and, like any free-water op, it CLIPS around terrain it overlaps."""
        result = stamp(
            "floor(0,47)\nwater_cloud(20,4,23,5)\nspawn(2)\nexit(45)", W, H,
        )
        # height 2 -> no corner trim; all four corners are water.
        for x, y in ((20, 4), (23, 4), (20, 5), (23, 5)):
            assert int(result.grid[y, x]) == TileType.WATER
        # code-not-LLM: a cloud overlapping a wall clips to the open cells (the
        # wall column stays a wall, the rest of the pocket fills) — no raise.
        clipped = stamp(
            "floor(0,47)\nwall(21,4,6)\nwater_cloud(20,4,23,6)\n"
            "spawn(2)\nexit(45)", W, H,
        )
        assert int(clipped.grid[4, 21]) == TileType.WALL  # wall untouched
        assert int(clipped.grid[5, 20]) == TileType.WATER  # rest of pocket fills
        assert any("clipped" in r for r in clipped.repairs)

    def test_fake_surface_anchors_skip_cloud_shoulders(self) -> None:
        """Regression (Chunk E): a rounded water_cloud exposes single-cell
        'shoulder' surface cells (a trimmed top corner leaves a lone top cell
        whose neighbour is submerged). The fake placement picker must offer only
        2-wide-capable FLAT-TOP anchors, or a 2-wide surface swimmer gets
        dropped by the footprint check (masked on emberfall_001, hit on others)."""
        from examples.run_platformer_slice import _fake_spots

        # A trimmed 5x3 cloud: top row cols 3-5, middle 2-6, bottom 3-5.
        vol = "water: y=8: x 3-5; y=9: x 2-6; y=10: x 3-5"
        msg = (
            "Standable cells (x, y are grid coords, y from top): \n"
            f"Volume cells by tile (swimmers ONLY go here): {vol}\n"
            "Open-air cells (FLYERS hover here, above the ground): none\n"
            "Player spawn: [0, 0]\n"
        )
        water = {(3, 8), (4, 8), (5, 8), (2, 9), (3, 9), (4, 9), (5, 9),
                 (6, 9), (3, 10), (4, 10), (5, 10)}
        surface = _fake_spots(msg)["surface"]
        assert surface  # some anchor IS offered
        for x, y in surface:
            # top-of-water AND a right neighbour that is ALSO top-of-water,
            # so a 2-wide surface body fits (cols x, x+1 both on the surface).
            assert (x, y - 1) not in water
            assert (x + 1, y) in water and (x + 1, y - 1) not in water
        # the shoulders (cols 2 & 6 at row 9) are never offered.
        assert (2, 9) not in surface and (6, 9) not in surface

    def test_water_less_volume_world_is_offered_generic_cloud_ops(self) -> None:
        """A lava world (volume tile, no 'water') must be advertised the
        registry-generic volume_* free-water ops — NOT the water_* aliases,
        which resolve the absent 'water' tile and would fail. Both the op it is
        told to use AND the fake it drives must stamp clean."""
        lava = _registry(
            _BASE_TILES
            + [
                {"id": 10, "name": "spike", "category": "hazard"},
                {"id": 20, "name": "lava", "category": "volume",
                 "params": {"damage_per_second": 5}},
            ]
        )
        msg = PlatformerPrompts().section_layout(
            "l1", "b", "islands", "f", {"cloud": 2}, 1, 3, 24, 16,
            DEFAULT_MOVEMENT, tiles=lava,
        ).user_message
        assert "volume_cloud(name,x1,y1,x2,y2)" in msg
        assert "water_cloud(x1,y1,x2,y2)" not in msg  # would break in lava
        assert "water_block(x1,y1,x2,y2)" not in msg
        assert "CLOUD of lava" in msg
        # The advertised op actually stamps in this registry.
        res = stamp(
            "floor(0,23)\nvolume_cloud(lava,5,3,9,6)\nspawn(2)\nexit(22)",
            24, 16, tiles=lava,
        )
        assert (res.grid == lava.by_name["lava"].id).any()

    def test_unreachable_checkpoint_flagged(self) -> None:
        """check_level validates checkpoints like spawn/exit — standable
        AND reachable, with the locate-and-instruct message."""
        result = stamp(
            "floor(0,47)\nledge(20,22,5)\ncheckpoint(2)\nplatform(1,13,3)\n"
            "spawn(6)\nexit(45)", W, H,
        )
        # A later platform() covered the checkpoint's cell.
        problems = check_level(
            result.grid, result.spawn, result.exit, DEFAULT_MOVEMENT,
            triggers=result.triggers,
        )
        assert problems and "checkpoint" in problems[0]
        assert "PLATFORM" in problems[0]


class TestVariantCaps:
    def test_unknown_variant_names_vocabulary(self) -> None:
        result = stamp(_FAKE_LAYOUTS["l1"], W, H)
        accepted, problems, _ = check_placements(
            result.grid,
            [{"enemy_id": "beetle", "x": 14, "y": 13, "variant": "mega"}],
            result.spawn,
            {"beetle": {"archetype": "patroller", "size": 1.0}},
        )
        assert not accepted
        assert problems and "unknown variant 'mega'" in problems[0]
        assert "champion" in problems[0] and "elite" in problems[0]

    def test_caps_from_game_rules_enforced(self) -> None:
        from examples.platformer_pack.rules import GameRules

        result = stamp(_FAKE_LAYOUTS["l1"], W, H)
        accepted, problems, _ = check_placements(
            result.grid,
            [
                {"enemy_id": "beetle", "x": 20, "y": 13, "variant": "elite"},
                {"enemy_id": "beetle", "x": 24, "y": 13, "variant": "elite"},
            ],
            result.spawn,
            {"beetle": {"archetype": "patroller", "size": 1.0}},
            rules=GameRules(variant_caps={"elite": 1}),
        )
        assert [p["x"] for p in accepted] == [20]
        assert problems and "at most 1 'elite'" in problems[0]

    def test_uncapped_variant_rides_free(self) -> None:
        from examples.platformer_pack.rules import GameRules

        result = stamp(_FAKE_LAYOUTS["l1"], W, H)
        accepted, problems, _ = check_placements(
            result.grid,
            [
                {"enemy_id": "beetle", "x": 20, "y": 13, "variant": "champion"},
                {"enemy_id": "beetle", "x": 24, "y": 13, "variant": "champion"},
            ],
            result.spawn,
            {"beetle": {"archetype": "patroller", "size": 1.0}},
            rules=GameRules(variant_caps={}),  # no cap on champion
        )
        assert len(accepted) == 2 and not problems

    def test_placement_prompt_offers_vocabulary(self, tmp_path: Path) -> None:
        good = make_fake_responder()
        prompts = []

        def spy(request):
            if "### TASK: placement" in request.user_message:
                prompts.append(request.user_message)
            return good(request)

        _run_slice(tmp_path / "run", responder=spy)
        assert prompts
        for message in prompts:
            assert '"variant": one of' in message
            assert "at most 1 per level" in message


class TestStyleGuide:
    """The palette agent: theme → role palette, enforced for coverage
    and readability, painted onto the tilesheet through the color_role
    seam — no consumer changes."""

    def test_palette_validators_locate_and_instruct(self) -> None:
        from examples.platformer_pack.style import check_palette
        from examples.platformer_pack.tiles import DEFAULT_TILES

        good = {
            "background": "#2b2331", "ground": "#6e5a4e",
            "platform": "#b8804a", "wall": "#5b4d5e", "breakable": "#a08050",
            "box": "#b87d2d", "danger": "#e0453a", "urchin": "#b9466e",
            "mine": "#aa5537", "water": "#3a6ea5",
        }
        assert check_palette(good, DEFAULT_TILES) == []

        missing = {k: v for k, v in good.items() if k != "water"}
        problems = check_palette(missing, DEFAULT_TILES)
        assert problems and "missing role 'water'" in problems[0]

        bad_hex = dict(good, wall="grayish")
        problems = check_palette(bad_hex, DEFAULT_TILES)
        assert problems and '"#rrggbb"' in problems[0]

        invisible = dict(good, ground="#2c2432")  # ~= background
        problems = check_palette(invisible, DEFAULT_TILES)
        assert problems and "nearly invisible" in problems[0]
        assert "lighten or darken" in problems[0]

        cold_hazard = dict(good, danger="#3a45e0")  # blue spikes
        problems = check_palette(cold_hazard, DEFAULT_TILES)
        assert problems and "doesn't read as dangerous" in problems[0]

    def test_palette_paints_the_sheet_and_ships_everywhere(
        self, tmp_path: Path
    ) -> None:
        from PIL import Image

        run = tmp_path / "run"
        ctx = _run_slice(run)
        tileset = ctx.bible.tilesets["ashen_depths"]
        # Recorded on the artifact, in the manifest, and as the seed file.
        # Ground is the canned #6e5a4e AFTER the separation tool spreads
        # it from the too-close wall (#5b4d5e → ground lifted to #776459).
        assert tileset.palette["ground"] == "#776459"
        manifest = json.loads((run / "manifest.json").read_text())
        assert manifest["palettes"]["ashen_depths"] == tileset.palette
        style_doc = json.loads(
            (run / "style/ashen_depths/style.json").read_text()
        )
        assert style_doc["palette"]["danger"] == "#e0453a"
        # And the sheet is actually painted with it: sample the floor slot.
        sheet = Image.open(run / tileset.tilesheet_path).convert("RGB")
        floor_slot = next(s for s in tileset.slots if s.name == "floor")
        x, y, _w, _h = floor_slot.px_region
        assert sheet.getpixel((x + 1, y + 1)) == (0x77, 0x64, 0x59)

    def test_fallback_palette_is_loud(self, tmp_path: Path) -> None:
        good = make_fake_responder()

        def broken_style(request):
            if "### TASK: style" in request.user_message:
                return "definitely not json"
            return good(request)

        ctx = _run_slice(tmp_path / "run", responder=broken_style)
        warnings = ctx.artifacts["slice_warnings"]
        assert any("PLACEHOLDER palette" in w for w in warnings)
        # Fallback still paints the sheet with the hardcoded colors.
        tileset = ctx.bible.tilesets["ashen_depths"]
        assert tileset.palette["ground"] == "#6e6e78"

    def test_contrast_is_repaired_in_code_not_retried(self) -> None:
        """Code-for-computation: luminance distance is arithmetic. A too-
        dark color is shifted to the readability bar (hue kept); passing
        colors are untouched. This killed a real 3-retry loop of the
        model nudging dark water against a dark dusk background."""
        from examples.platformer_pack.style import (
            MIN_CONTRAST,
            _luminance,
            check_palette,
            enforce_contrast,
        )
        from examples.platformer_pack.tiles import DEFAULT_TILES

        # The exact palette shape the real model looped on.
        palette = {
            "background": "#2b1f2e", "ground": "#7a5c3a",
            "platform": "#c8843a", "wall": "#c0a882", "breakable": "#a08050",
            "box": "#b87d2d", "danger": "#e84210", "urchin": "#b9466e",
            "mine": "#aa5537",
            "water": "#1a4a6b",  # distance ~32: fails
        }
        repaired, adjusted = enforce_contrast(palette, DEFAULT_TILES)
        assert set(adjusted) == {"water"}
        assert check_palette(repaired, DEFAULT_TILES) == []
        bg_lum = _luminance(repaired["background"])
        assert abs(_luminance(repaired["water"]) - bg_lum) >= MIN_CONTRAST
        # Hue survives: still a blue (b > r).
        r, _g, b = (
            int(repaired["water"][i : i + 2], 16) for i in (1, 3, 5)
        )
        assert b > r
        # Untouched roles pass through byte-identical.
        assert repaired["danger"] == palette["danger"]

    def test_low_contrast_palette_accepted_e2e(self, tmp_path: Path) -> None:
        """A palette failing only on contrast must NOT retry or fall back
        — the tool repairs it and the run stays warning-free."""
        good = make_fake_responder()
        style_calls: list[str] = []

        def moody_style(request):
            if "### TASK: style" in request.user_message:
                style_calls.append(request.user_message)
                return json.dumps({"palette": {
                    "background": "#2b1f2e", "ground": "#7a5c3a",
                    "platform": "#c8843a", "wall": "#c0a882",
                    "breakable": "#a08050", "box": "#b87d2d",
                    "danger": "#e84210", "urchin": "#b9466e",
                    "mine": "#aa5537", "water": "#1a4a6b",
                }})
            return good(request)

        ctx = _run_slice(tmp_path / "run", responder=moody_style)
        assert len(style_calls) == 1  # accepted first try — tool repaired
        assert ctx.artifacts.get("slice_warnings", []) == []
        manifest = json.loads((tmp_path / "run/manifest.json").read_text())
        from examples.platformer_pack.style import check_palette
        from examples.platformer_pack.tiles import DEFAULT_TILES

        assert check_palette(manifest["palettes"]["ashen_depths"], DEFAULT_TILES) == []
        assert manifest["palettes"]["ashen_depths"]["water"] != "#1a4a6b"  # repaired
        assert manifest["palettes"]["ashen_depths"]["danger"] == "#e84210"  # untouched

    def test_style_prompt_carries_constraints(self, tmp_path: Path) -> None:
        """I1: the agent reads its constraints in the prompt."""
        good = make_fake_responder()
        prompts = []

        def spy(request):
            if "### TASK: style" in request.user_message:
                prompts.append(request.user_message)
            return good(request)

        _run_slice(tmp_path / "run", responder=spy)
        assert len(prompts) == 1
        message = prompts[0]
        assert (
            "### ROLES: background,ground,platform,wall,breakable,box,danger,"
            "urchin,mine,water"
            in message
        )
        assert "luminance distance >= 40" in message
        assert "dangerous at a glance" in message

    def test_safe_volume_hue_clamp_fires_on_lava_hued_water(self) -> None:
        """RB1: a SWIMMABLE volume wearing a hazard-family hue snaps to
        the safe-liquid band — luminance preserved, other roles
        byte-identical, idempotent. (The paid fire biome shipped water
        6 degrees from `danger`.)"""
        from examples.platformer_pack import color as cm
        from examples.platformer_pack.style import (
            MIN_VOLUME_HAZARD_HUE_SEP,
            _luminance,
            separate_safe_volumes_from_hazards,
        )
        from examples.platformer_pack.tiles import DEFAULT_TILES

        palette = {
            "background": "#181820", "ground": "#776459",
            "platform": "#96783c", "wall": "#464650",
            "breakable": "#cda04b", "box": "#be7d2d",
            "danger": "#e0453a", "urchin": "#b9466e", "mine": "#aa5537",
            "water": "#8f2208",  # lava-hued but swimmable: must move
        }
        out, adjusted = separate_safe_volumes_from_hazards(
            palette, DEFAULT_TILES
        )
        assert set(adjusted) == {"water"}
        water_hue = cm.hue_of(out["water"])
        assert water_hue is not None
        for role in ("danger", "urchin", "mine"):
            hazard_hue = cm.hue_of(out[role])
            if hazard_hue is not None:
                assert (
                    cm.hue_distance(water_hue, hazard_hue)
                    >= MIN_VOLUME_HAZARD_HUE_SEP
                )
        assert abs(_luminance(out["water"]) - _luminance(palette["water"])) <= 1.5
        for role in palette:
            if role != "water":
                assert out[role] == palette[role]
        again, adjusted2 = separate_safe_volumes_from_hazards(
            out, DEFAULT_TILES
        )
        assert adjusted2 == {} and again == out

    def test_safe_volume_clamp_leaves_distant_water_alone(self) -> None:
        """The canned blue water (~211 degrees, >=128 from every hazard)
        must pass untouched — the clamp is a repair, not a restyle."""
        from examples.platformer_pack.style import (
            separate_safe_volumes_from_hazards,
        )
        from examples.platformer_pack.tiles import DEFAULT_TILES

        palette = {
            "background": "#181820", "ground": "#776459",
            "platform": "#96783c", "wall": "#464650",
            "breakable": "#cda04b", "box": "#be7d2d",
            "danger": "#e0453a", "urchin": "#b9466e", "mine": "#aa5537",
            "water": "#3a6ea5",
        }
        out, adjusted = separate_safe_volumes_from_hazards(
            palette, DEFAULT_TILES
        )
        assert adjusted == {} and out == palette

    def test_damaging_volume_keeps_warm_hue(self) -> None:
        """Lava (damage_per_second on the volume tile) is deliberately
        exempt — warm hue = harmful is CORRECT for a damaging liquid."""
        from examples.platformer_pack.style import (
            separate_safe_volumes_from_hazards,
        )
        from examples.platformer_pack.tiles import load_tiles

        lava_tiles = load_tiles(
            Path(__file__).parent.parent / "examples/lava_world/tile_types.json"
        )
        palette = {"danger": "#e0453a", "lava": "#e8722c"}
        out, adjusted = separate_safe_volumes_from_hazards(palette, lava_tiles)
        assert adjusted == {} and out["lava"] == "#e8722c"


class TestLavaWorld:
    """The 3b acceptance test: an alternate game folder (copied rules +
    tiles with lava-world entries) produces an observably different
    playable game — data only, no code."""

    LAVA_DIR = Path(__file__).parent.parent / "examples" / "lava_world"

    def _run(self, output_dir: Path) -> PipelineContext:
        from examples.platformer_pack.rules import load_rules

        return _run_slice(
            output_dir,
            rules=load_rules(self.LAVA_DIR / "game_rules.json"),
            tiles=load_tiles(self.LAVA_DIR / "tile_types.json"),
            variants=load_variants(),
        )

    def test_lava_world_generates_clean_and_different(
        self, tmp_path: Path
    ) -> None:
        run = tmp_path / "lava"
        ctx = self._run(run)
        # No fallbacks: the canned responder adapted to the lava
        # vocabulary offered by the registry-driven prompt.
        assert ctx.artifacts.get("slice_warnings", []) == []

        manifest = json.loads((run / "manifest.json").read_text())
        assert manifest["rules"]["enemy_water_policy"] == "amphibious"
        assert manifest["rules"]["platform_drop_through"] is False
        assert "lava" in [t["name"] for t in manifest["tiles"]]
        # The style agent styles THIS game's roles too (basalt ground,
        # lava pool) — data-driven end to end.
        assert manifest["palettes"]["ashen_depths"]["lava"] == "#e8722c"
        # Canned #5a4f5c, lifted by the separation tool (wall too close).
        assert manifest["palettes"]["ashen_depths"]["basalt"] == "#6e6470"

        # The volume in the collision grid IS lava, damaging by data.
        tileset = ctx.bible.tilesets["ashen_depths"]
        lava_slot = next(s for s in tileset.slots if s.collision == "volume")
        assert lava_slot.name == "lava"
        assert lava_slot.params["damage_per_second"] == 1.0
        for level in ctx.bible.levels.values():
            if level.parent_level:
                continue  # secret rooms are dry chambers (vault/lair)
            with np.load(run / level.collision) as data:
                assert (data["collision"] == lava_slot.tile_type).any()

    def test_lava_world_differs_from_default_on_disk(
        self, tmp_path: Path
    ) -> None:
        default_run, lava_run = tmp_path / "default", tmp_path / "lava"
        _run_slice(default_run)
        self._run(lava_run)
        # Same seed, same template — different game where the data says
        # so (tilesheet colors, manifest vocabulary), identical where it
        # doesn't (world lore comes from the same canned responder).
        assert (
            (default_run / "tileset/ashen_depths/tilesheet.png").read_bytes()
            != (lava_run / "tileset/ashen_depths/tilesheet.png").read_bytes()
        )
        assert (
            (default_run / "world.json").read_bytes()
            == (lava_run / "world.json").read_bytes()
        )

    def test_lava_world_deterministic(self, tmp_path: Path) -> None:
        run_a, run_b = tmp_path / "a", tmp_path / "b"
        self._run(run_a)
        self._run(run_b)
        assert_trees_byte_identical(run_a, run_b)


class TestCarve:
    """carve(x1,y1,x2,y2) — rectangular subtraction for irregular ledges
    and varied silhouettes (design-variety: ops, not prompt coaching)."""

    def test_carve_clears_rect_and_leaves_neighbors(self) -> None:
        result = stamp(
            "floor(0,47)\nledge(10,20,9)\ncarve(12,9,14,9)\n"
            "spawn(2)\nexit(45)",
            W, H,
        )
        assert (result.grid[9, 12:15] == 0).all()
        assert result.grid[9, 10] != 0 and result.grid[9, 20] != 0

    def test_carve_swaps_reversed_corners(self) -> None:
        a = stamp(
            "floor(0,47)\nledge(10,20,9)\ncarve(14,9,12,9)\nspawn(2)\nexit(45)",
            W, H,
        )
        b = stamp(
            "floor(0,47)\nledge(10,20,9)\ncarve(12,9,14,9)\nspawn(2)\nexit(45)",
            W, H,
        )
        assert (a.grid == b.grid).all()

    def test_carve_cannot_cut_ground_or_bedrock(self) -> None:
        # §1b #2: a carve aimed at the ground/bedrock rows used to raise;
        # it now DROPS with a note — and the guarantee it protected still
        # holds: the ground row is untouched.
        res = stamp(
            f"floor(0,47)\ncarve(5,{H - 2},6,{H - 2})\nspawn(2)\nexit(45)",
            W, H,
        )
        assert any(
            r.startswith("carve(") and "dropped" in r for r in res.repairs
        )
        assert int(res.grid[H - 2, 5]) == TileType.FLOOR

    def test_carve_drops_cleared_hazard_records(self) -> None:
        """Records must mirror the FINAL grid: a carve over a spike strip
        removes both the cells and their sparse hazard records."""
        result = stamp(
            "floor(0,47)\nspike(20,24)\ncarve(22,13,22,13)\nspawn(2)\nexit(45)",
            W, H,
        )
        assert int(result.grid[13, 22]) == 0
        spike_xs = sorted(
            h.x for h in result.hazards if h.type.startswith("floor_")
        )
        assert spike_xs == [20, 21, 23, 24]


class TestVerticalSections:
    """Chunk C: some later-stage levels are VERTICAL climbs — tall + narrow,
    spawn at the bottom, exit at the summit, stitched bottom-to-top."""

    def test_vertical_climb_generates_reachable_and_framed(
        self, tmp_path: Path
    ) -> None:
        from examples.platformer_pack.validate import reachable_cells

        # A 3-stage world: stage-1 levels stay horizontal (intros), later
        # stages roll some vertical climbs.
        ctx = _run_slice(tmp_path / "run", num_stages=3)
        verticals = [
            lv for lv in ctx.bible.levels.values()
            if lv.layout_axis == "vertical"
        ]
        assert verticals, "no vertical climb rolled in a 3-stage world"
        # Stage-1 levels (a world's intro) are ALWAYS horizontal.
        for lid in ("l1", "l2", "l3"):
            assert ctx.bible.levels[lid].layout_axis == "horizontal"
        for lv in verticals:
            assert lv.grid_height > lv.grid_width  # a tall shaft
            assert lv.view_rows is not None  # framed by height
            # spawn at the BOTTOM, exit (summit) at the TOP.
            assert lv.spawn[1] > lv.grid_height // 2
            assert lv.exit[1] < lv.grid_height // 2
            assert lv.layout_fallback is False
            # The climb is beatable: the summit is reachable from the base.
            with np.load(tmp_path / "run" / lv.collision) as d:
                grid = d["collision"]
            assert lv.exit in reachable_cells(grid, lv.spawn, DEFAULT_MOVEMENT)


class TestPerLevelView:
    """Deliberate per-level framing exceptions (T1): the stage plan tags a
    level intimate/vista; everything else stays on the game-global scale."""

    def test_vista_finale_lands_on_level_and_manifest(
        self, tmp_path: Path
    ) -> None:
        ctx = _run_slice(tmp_path / "run")
        levels = ctx.bible.levels
        # Canned stage plan: standard, standard, vista finale.
        assert levels["l1"].view_cells is None
        assert levels["l2"].view_cells is None
        assert levels["l3"].view_cells == 30
        level_doc = json.loads(
            (tmp_path / "run/level/ashen_depths/l3/level.json").read_text()
        )
        assert level_doc["view_cells"] == 30

    def test_view_presets_clamp_and_default(self) -> None:
        from examples.platformer_pack.graphics import GraphicsSpec

        gfx = GraphicsSpec(view_presets={"vista": 300, "intimate": 2})
        assert gfx.view_for("vista") == 60
        assert gfx.view_for("intimate") == 8
        assert gfx.view_for("standard") is None
        assert gfx.view_for("") is None


class TestAnimFramePick:
    """The pure candidate → (state, frame-index) selector, shared in spirit
    with main.gd's GDScript mirror (_anim_pick + _anim_index). The CALLER
    builds the candidate priority list from runtime signals and owns the
    state-change latch; states carry per-frame durations + a loop mode."""

    S = {
        "idle": {"durs": [0.25, 0.25], "loop": "loop"},
        "walk": {"durs": [0.10] * 4, "loop": "loop"},
        "jump": {"durs": [0.09] * 6, "loop": "once"},
    }

    def test_first_present_candidate_wins(self) -> None:
        from examples.platformer_play import pick_anim_frame

        # player airborne: jump leads the list and exists
        assert pick_anim_frame(["jump", "walk", "idle"], 0.0, self.S)[0] == "jump"
        # enemy hurt priority, but no hurt frames here → walk
        assert pick_anim_frame(["hurt", "walk", "idle"], 0.0, self.S)[0] == "walk"
        assert pick_anim_frame(["idle", "walk"], 0.0, self.S)[0] == "idle"

    def test_frame_index_advances_and_wraps(self) -> None:
        from examples.platformer_play import pick_anim_frame

        # walk: uniform 0.10 x 4 looping → idx = int(t/0.10) % 4
        assert pick_anim_frame(["walk"], 0.00, self.S)[1] == 0
        assert pick_anim_frame(["walk"], 0.15, self.S)[1] == 1
        assert pick_anim_frame(["walk"], 0.35, self.S)[1] == 3
        assert pick_anim_frame(["walk"], 0.45, self.S)[1] == 0  # wraps

    def test_falls_through_when_no_candidate_exists(self) -> None:
        from examples.platformer_play import pick_anim_frame

        only_idle = {"idle": {"durs": [0.2] * 3, "loop": "loop"}}
        # candidates absent → first state in the dict
        assert pick_anim_frame(["jump", "walk"], 0.0, only_idle)[0] == "idle"

    def test_deterministic(self) -> None:
        from examples.platformer_play import pick_anim_frame

        a = pick_anim_frame(["walk"], 0.37, self.S)
        b = pick_anim_frame(["walk"], 0.37, self.S)
        assert a == b

    def test_once_clamps_at_the_last_frame(self) -> None:
        from examples.platformer_play import _anim_index

        durs = [0.1, 0.1, 0.1]
        assert _anim_index(0.05, durs, "once") == 0
        assert _anim_index(0.15, durs, "once") == 1
        assert _anim_index(0.25, durs, "once") == 2
        assert _anim_index(0.31, durs, "once") == 2  # holds, never wraps
        assert _anim_index(9.99, durs, "once") == 2

    def test_ping_pong_walks_out_and_back(self) -> None:
        from examples.platformer_play import _anim_index

        durs = [0.1] * 4  # cycle: 0 1 2 3 2 1, then wraps to 0
        seq = [_anim_index(0.05 + 0.1 * k, durs, "ping_pong") for k in range(7)]
        assert seq == [0, 1, 2, 3, 2, 1, 0]
        # a single frame ping-pongs in place
        assert _anim_index(0.5, [0.1], "ping_pong") == 0

    def test_unequal_durations_walk_cumulatively(self) -> None:
        from examples.platformer_play import _anim_index

        durs = [0.05, 0.2, 0.05]  # sum 0.3
        assert _anim_index(0.04, durs, "loop") == 0
        assert _anim_index(0.06, durs, "loop") == 1
        assert _anim_index(0.24, durs, "loop") == 1
        assert _anim_index(0.26, durs, "loop") == 2
        assert _anim_index(0.31, durs, "loop") == 0  # wraps into frame 0

    def test_old_shape_back_compat_scalar_duration(self) -> None:
        """frames.json entries WITHOUT the new keys keep today's behavior:
        the scalar duration_ms fans out uniformly and the state loops."""
        from examples.platformer_play import _anim_timing

        assert _anim_timing({"duration_ms": 120}, 4) == ([0.12] * 4, "loop")
        # keyless entry → the 120ms default, still looping
        assert _anim_timing({}, 2) == ([0.12, 0.12], "loop")
        # new keys win when present and consistent
        assert _anim_timing(
            {"durations_ms": [100, 200], "loop": "once", "duration_ms": 120}, 2
        ) == ([0.1, 0.2], "once")
        # a desynced durations list falls back to the uniform scalar
        assert _anim_timing(
            {"durations_ms": [100], "duration_ms": 80}, 3
        ) == ([0.08] * 3, "loop")
        # durations floor at 1ms so the index math never divides by zero
        assert _anim_timing({"durations_ms": [0, 50], "loop": "loop"}, 2) == (
            [0.001, 0.05],
            "loop",
        )

    def test_unknown_loop_mode_wraps_like_loop(self) -> None:
        from examples.platformer_play import _anim_index

        durs = [0.1] * 4
        for t in (0.05, 0.15, 0.45, 1.05):
            assert _anim_index(t, durs, "bogus") == _anim_index(t, durs, "loop")

    def test_state_change_restarts_at_frame_zero(self) -> None:
        """The consumers' latch zeroes the clock on a picked-state change;
        index at t=0 must be frame 0 in every loop mode (both surfaces
        reset then re-index the same tick)."""
        from examples.platformer_play import _anim_index

        for mode in ("loop", "once", "ping_pong"):
            assert _anim_index(0.0, [0.1, 0.2, 0.1], mode) == 0


class TestOobAndParserRepairs:
    """§1b #2 + #5: out-of-bounds coordinates CLAMP/DROP (recorded) instead
    of raising, and lenient stamping SKIPS lines that are not shaped like
    ops. Each case replays a real plat_seaside3 failing attempt."""

    def test_real_oob_attempts_now_stamp_clean(self) -> None:
        """The three real OOB raises from the paid climb traces — each
        burned a section retry; all must now stamp with a recorded repair."""
        # l4 s3 attempt 1: 'breakable: column 16 outside 0..15.'
        res = stamp(
            "floor(0,15)\nbreakable(5,16)", 16, 26, validate_markers=False,
        )
        assert any("clamped" in r for r in res.repairs)
        assert int(res.grid[24, 15]) != 0 and int(res.grid[24, 5]) != 0
        # l9 s2 attempt 1: 'platform: column 23 outside 0..22.'
        res = stamp(
            "platform(22,20,2)", 23, 29, validate_markers=False,
        )
        assert any("clamped" in r for r in res.repairs)
        assert int(res.grid[20, 22]) != 0
        # l7 s3 attempt 1: 'ledge: row 27 outside 1..26.' (the model's own
        # attempt 2 moved it to row 26 — the clamp computes the same fix).
        res = stamp(
            "ledge(0,4,27)", 19, 29, validate_markers=False,
        )
        assert any("row 27 clamped to 26" in r for r in res.repairs)
        assert int(res.grid[26, 2]) != 0

    def test_fully_outside_ops_drop_with_a_note(self) -> None:
        res = stamp(
            "floor(0,15)\nledge(20,25,10)\nwall(19,2,9)\nreward(18,3)",
            16, 20, validate_markers=False,
        )
        drops = [r for r in res.repairs if "dropped" in r]
        assert len(drops) == 3, res.repairs
        # Nothing stamped above the floor rows.
        assert not (res.grid[:18, :] != 0).any()

    def test_lenient_stamp_skips_prose_and_truncation(self) -> None:
        """l2 s1 emitted a reasoning paragraph as line 1; l5 s3 died on a
        truncated 'carve(18,0,20'; l1 s1 on a bare 'platform' — one bad
        line must not void the section (§1b #5)."""
        text = (
            "Looking at the error: the player reaches (41, 11) but cannot\n"
            "floor(0,15)\n"
            "platform\n"
            "ledge(2,6,10)\n"
            "carve(8,1,10"
        )
        res = stamp(text, 16, 20, validate_markers=False, lenient=True)
        skips = [r for r in res.repairs if "skipped" in r]
        assert len(skips) == 3, res.repairs
        assert int(res.grid[18, 3]) != 0  # floor survived
        assert int(res.grid[10, 3]) != 0  # ledge survived

    def test_strict_stamp_still_raises_on_debris(self) -> None:
        """Code-authored DSL (fallbacks, bridges, tests) stays strict — our
        own typos must fail loudly, not vanish."""
        with pytest.raises(DslError, match="not a valid op"):
            stamp("floor(0,15)\ncarve(8,1,10", 16, 20, validate_markers=False)

    def test_arity_errors_still_raise_for_the_retry_loop(self) -> None:
        """A well-formed op with wrong arity is a correctable mistake the
        retry loop demonstrably fixes (l4 s2 fixed 'breakable(6,19,8)' on
        attempt 2) — lenient mode must NOT swallow it."""
        with pytest.raises(DslError, match="takes 2 args"):
            stamp(
                "floor(0,15)\nbreakable(6,19,8)", 16, 26,
                validate_markers=False, lenient=True,
            )


#: Verbatim from review/ashveil_peaks/l8_layout_attempts.json (plat_seaside3
#: paid run) — section 3 (islands, local 28x26), attempt 1. All three
#: attempts died on contained water poured over the section's own gap
#: (volume 'no solid basin' / pool 'not solid floor'), the section fell
#: back, and the whole level shipped FLAT (§1b #3).
_L8_S3_ATTEMPT_1 = (
    "floor(0,27)\ngap(1,4)\ngap(6,9)\ngap(11,14)\ngap(16,19)\ngap(21,24)\n"
    "wall(0,18,24)\nwall(5,20,24)\nwall(10,18,24)\nwall(15,20,24)\n"
    "wall(20,18,24)\nwall(25,20,24)\nledge(0,4,18)\nledge(6,10,16)\n"
    "ledge(11,15,14)\nledge(16,20,12)\nledge(21,25,10)\nledge(22,27,7)\n"
    "platform(2,15,3)\nplatform(7,13,3)\nplatform(12,11,3)\n"
    "platform(17,9,3)\nplatform(22,7,3)\nbreakable(3,4)\nbreakable(8,9)\n"
    "breakable(13,14)\nbreakable(18,19)\nhazard_strip(spike,1,4)\n"
    "hazard_strip(spike,6,9)\nhazard_strip(spike,11,14)\n"
    "hazard_strip(spike,16,19)\nhazard_strip(spike,21,24)\n"
    "water_cloud(1,10,5,13)\nwater_cloud(6,8,10,11)\nwater_cloud(16,6,20,9)\n"
    "wall(26,22,23)\nvolume(water,22,25,23)\nwall(21,22,23)\n"
    "reward(23,22)\ncarve(23,21,24,22)"
)


class TestPoolVolumeRepairOracle:
    """§1b #3 oracle: the real l8 section that whole-flatted the level must
    now stamp clean on its FIRST attempt, water preserved as a free
    feature."""

    def test_l8_islands_section_stamps_clean(self) -> None:
        res = stamp(
            _L8_S3_ATTEMPT_1, 28, 26, validate_markers=False, lenient=True,
        )
        # The killer op — volume over the section's own gap(21,24) —
        # became a recorded free-water spout, not a raise.
        assert any(
            "no solid basin" in r and "free-standing" in r
            for r in res.repairs
        ), res.repairs
        water_id = load_tiles().by_name["water"].id
        assert (res.grid == water_id).any()
        assert res.free_volume  # the spout is containment-exempt
        # Real content everywhere above the floor rows.
        assert int((res.grid[:24, :] != 0).sum()) > 100


class TestSequentialHandoffPrompts:
    """The handoff BLOCK in section prompts (user-locked 2026-07-14): the
    predecessor's rebased DSL + occupied band + 'do not rebuild' contract,
    history digests, and (regen only) the successor's shared band."""

    def test_prompt_carries_predecessor_and_contract(self) -> None:
        m = PlatformerPrompts().section_layout(
            "l1", "b", "gauntlet", "f", {}, 1, 3, 26, H, DEFAULT_MOVEMENT,
            predecessor="floor(-15,5)\nwall(-12,4,9)",
            predecessor_occupied="SOLID (floor/ledge/wall): y=14: x 0-5",
            history=None,
        ).user_message
        assert "ALREADY BUILT" in m and "to your LEFT" in m
        assert "floor(-15,5)" in m
        assert "negative columns lie left of your edge" in m
        assert f"columns 0..{5}" in m
        assert "Do NOT rebuild or contradict" in m
        # Vertical framing speaks rows, below-edge.
        v = PlatformerPrompts().section_layout(
            "l4", "b", "climb", "f", {}, 2, 4, 16, 26, DEFAULT_MOVEMENT,
            axis="vertical",
            predecessor="platform(2,27,3)",
            predecessor_occupied="one-way platform: y=21: x 2-4",
            history="s0 climb: 1x floor, 9x platform",
            successor_occupied="SOLID (floor/ledge/wall): y=2: x 0-3",
        ).user_message
        assert "the section BELOW you" in v
        assert "rows greater than 25 lie below your bottom edge" in v
        assert f"rows {26 - 6}..{25}" in v
        assert "The level so far (for cohesion): s0 climb" in v
        assert "The NEXT section (ABOVE you) is ALREADY BUILT" in v

    def test_e2e_second_section_sees_first_sections_dsl(
        self, tmp_path: Path
    ) -> None:
        """Threading: section 2's real prompt must contain section 1's
        actual DSL rebased into section 2's coordinates."""
        good = make_fake_responder()
        captured: list[str] = []

        def spy(request):
            msg = request.user_message
            if "### TASK: layout" in msg and re.search(
                r"### SECTION: \w+ \(2 of \d+\)", msg
            ):
                captured.append(msg)
            return good(request)

        _run_slice(tmp_path / "run", responder=spy)
        assert captured
        for msg in captured:
            assert "ALREADY BUILT" in msg
            assert "Do NOT rebuild or contradict" in msg
            # The fake's first section always lays a floor + spawn; rebased
            # into the receiver's frame the floor starts at a negative col
            # (horizontal) or the spawn row shifts below (vertical).
            assert re.search(r"floor\(-?\d+,", msg)

    def test_e2e_regen_prompt_sees_both_neighbours(
        self, tmp_path: Path
    ) -> None:
        """A whole-level regen of an interior section gets the successor's
        shared band too (l8's s1 regenerated three times blind)."""
        good = make_fake_responder()
        calls = {"n": 0}
        hit: set[str] = set()
        regen_prompts: list[str] = []

        def responder(request):
            msg = request.user_message
            lm = re.search(r"### LEVEL: (\w+)", msg)
            m = re.search(r"### SECTION: (\w+) \((\d+) of (\d+)\)", msg)
            gm = re.search(r"Grid: (\d+) wide x (\d+) tall", msg)
            if (
                m and gm and lm
                and lm.group(1) in ("l1", "l2", "l3")
                and int(m.group(2)) == 2
                and int(m.group(3)) >= 3
                and (not hit or lm.group(1) in hit)
            ):
                hit.add(lm.group(1))
                calls["n"] += 1
                lw, h = int(gm.group(1)), int(gm.group(2))
                if "The NEXT section" in msg:
                    regen_prompts.append(msg)
                if calls["n"] == 1:
                    # Locally valid, whole-level unbridgeable: burn a round.
                    return f"floor(0,{lw - 1})\n" + "\n".join(
                    f"wall({c},0,{h - 3})"
                    for c in range(lw // 2 - 5, lw // 2 + 5)
                )
            return good(request)

        _run_slice(tmp_path / "run", responder=responder)
        assert regen_prompts, "no regen prompt carried the successor band"
        assert all("mesh with them" in p for p in regen_prompts)


class TestAnimPreviewTargets:
    """PLAT_ANIM boots the animation VIEWER instead of a play session, so an
    animation can be judged in the same surface that renders the game. The
    target grammar is the only part testable without a display."""

    def test_named_targets_resolve_to_base_sprites(self, tmp_path) -> None:
        from examples.platformer_play import _anim_preview_targets

        assert _anim_preview_targets(tmp_path, "player") == [
            ("player", "sprite/player/base.png")
        ]
        assert _anim_preview_targets(tmp_path, "enemy:blind_eel") == [
            ("enemy:blind_eel", "sprite/enemy/blind_eel/base.png")
        ]
        assert _anim_preview_targets(tmp_path, "item:coin") == [
            ("item:coin", "sprite/item/coin/base.png")
        ]

    def test_all_walks_the_player_then_every_enemy(self, tmp_path) -> None:
        from examples.platformer_play import _anim_preview_targets

        for eid in ("wisp", "aphid"):
            (tmp_path / "sprite" / "enemy" / eid).mkdir(parents=True)
        labels = [label for label, _ in _anim_preview_targets(tmp_path, "all")]
        assert labels == ["player", "enemy:aphid", "enemy:wisp"]  # sorted, player first

    def test_all_on_a_pack_with_no_sprites_still_offers_the_player(
        self, tmp_path
    ) -> None:
        # A static-only pack must not explode — the viewer says "no sprite".
        from examples.platformer_play import _anim_preview_targets

        assert _anim_preview_targets(tmp_path, "all") == [
            ("player", "sprite/player/base.png")
        ]

    def test_unknown_target_fails_loudly(self, tmp_path) -> None:
        from examples.platformer_play import _anim_preview_targets

        with pytest.raises(SystemExit, match="unknown target"):
            _anim_preview_targets(tmp_path, "bogus:thing")
        with pytest.raises(SystemExit, match="unknown target"):
            _anim_preview_targets(tmp_path, "enemy")  # no id

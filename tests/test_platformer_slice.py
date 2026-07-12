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
            ("floor(0,5)\nspike(10,11)\nspawn(2)\nexit(4)", "no ground"),
            ("floor(0,5)\nwater(10,12,12)\nspawn(2)\nexit(4)", "no solid basin"),
            ("floor(0,47)\ngap(20,24)\nwater(20,24,12)\nspawn(2)\nexit(45)", "no solid basin"),
            ("floor(0,47)\nledge(5,9,15)\nspawn(2)\nexit(45)", "outside 1"),
            ("gibberish", "not a valid op"),
        ],
    )
    def test_strict_errors(self, text: str, match: str) -> None:
        with pytest.raises(DslError, match=match):
            stamp(text, W, H)

    def test_pool_conflict_names_the_occupying_tiles(self) -> None:
        """The sunlit-run l3 class: the program lays floor, then its own
        wall/gap overwrite/remove it, then pool() overlaps the damage.
        'lay floor there first' was unfollowable — the error must name
        what actually occupies each bad ground-row column."""
        text = (
            "floor(0,47)\ngap(18,21)\nwall(17,10,14)\n"
            "pool(water,17,22)\nspawn(2)\nexit(45)"
        )
        with pytest.raises(DslError) as excinfo:
            stamp(text, W, H)
        message = str(excinfo.value)
        assert "17 (wall)" in message
        assert "18 (empty)" in message and "21 (empty)" in message
        assert "removed or overwrote the floor" in message

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

    def test_validator_approved_jumps_land_in_harness_physics(self) -> None:
        """Validator ↔ play-surface PARITY, proven by simulation: replay
        the pygame/Godot integration (same constants, same frame order)
        for every boundary jump the arc rule approves — each must land.
        This is the guard against round-2/4/5's recurring 'platforms are
        too high': the validator may only promise jumps the physics
        delivers."""
        m = DEFAULT_MOVEMENT

        def simulate(dx_cells: int, rise: int) -> bool:
            # Mirrors examples/platformer_play.py: jump event sets vy,
            # horizontal moves, gravity applies, then vertical+landing.
            g, s, dt = m.gravity, m.run_speed, 1.0 / 60.0
            v0 = (2.0 * g * (m.jump_height + 0.4)) ** 0.5
            platform_row = 13 - rise + 1  # stand atop = 13 - rise
            px, py, vy = 0.0, 13.0, -v0
            for _ in range(240):
                px += s * dt  # holding toward the platform
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

        # Every rise the validator allows, at its maximum approved dx,
        # must land in the integrated physics.
        for rise in range(1, m.jump_height + 1):
            dx = max_dx_for_rise(m, rise)
            assert simulate(dx, rise), (
                f"validator approves rise {rise} at dx {dx} but the "
                "harness physics cannot land it — parity broken"
            )
        # And clearly-outside jumps must fail in sim too (sanity).
        assert not simulate(6, 3)

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
        frontier, nearest = _locate_break(stand, reached)
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
            "platform": "#6b4a2a", "wall": "#473b32",
            "danger": "#c43a0a", "water": "#4a3d1a",
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
        """E2E: an unreachable-but-well-designed layout is accepted on
        attempt 1 with tool bridges — no retry, no fallback, playable."""
        good = make_fake_responder()
        layout_calls: list[str] = []
        sent: dict[str, str] = {}

        def responder(request):
            msg = request.user_message
            if "### TASK: layout" in msg and "### LEVEL: l1" in msg:
                layout_calls.append(msg)
                # Dims are schema-rolled ranges — build the unreachable
                # layout against whatever grid the prompt advertises: a
                # 9-wide gap (dx 10, beyond a running jump's ~7-cell reach)
                # between two floor runs.
                m = re.search(r"Grid: (\d+) wide x (\d+) tall", msg)
                right = int(m.group(1)) - 1
                sent["gap_dsl"] = (
                    f"floor(0,{right - 19})\nfloor({right - 9},{right})\n"
                    f"checkpoint(20)\nspawn(2)\nexit({right - 2})"
                )
                return sent["gap_dsl"]
            return good(request)

        ctx = _run_slice(tmp_path / "run", responder=responder)
        assert len(layout_calls) == 1  # accepted first try — tool repaired
        assert not any(
            "layout" in w for w in ctx.artifacts.get("slice_warnings", [])
        )
        dsl_text = ctx.artifacts["dsl_texts"]["l1"]
        assert dsl_text.startswith(sent["gap_dsl"]) and "# auto-bridge" in dsl_text
        # The stored collision layer IS the bridged, traversable level.
        with np.load(tmp_path / "run" / ctx.bible.levels["l1"].collision) as d:
            grid = d["collision"]
        level = ctx.bible.levels["l1"]
        assert not check_level(grid, level.spawn, level.exit, DEFAULT_MOVEMENT)


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
        (A DESIGN failure — a pool poured over its own gap. Covered
        spawns and reachability breaks no longer reach the model at all;
        the snap/bridge tools repair those.)"""
        good = make_fake_responder()
        layout_calls: list[str] = []
        failed_once = {"done": False}
        sent: dict[str, str] = {}

        def responder(request):
            msg = request.user_message
            if "### TASK: layout" in msg:
                layout_calls.append(msg)
                if not failed_once["done"]:
                    failed_once["done"] = True
                    # Pour a pool over a gap the same program cut — the
                    # sunlit run's un-repairable design failure class.
                    m = re.search(r"Grid: (\d+) wide x (\d+) tall", msg)
                    width = int(m.group(1))
                    sent["bad_dsl"] = (
                        f"floor(0,{width - 1})\ngap(10,13)\n"
                        f"pool(water,10,13)\n"
                        f"spawn(2)\nexit({width - 3})"
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
        assert "cannot sink in" in retries[0]

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
        # Spawn safety is a CODE repair now: beetle@3 slides to the
        # nearest standable column outside the radius (8 — the pool at
        # 5-7 doesn't support land enemies) instead of kicking back.
        assert [(p["enemy_id"], p["x"]) for p in accepted] == [
            ("beetle", 20), ("beetle", 8), ("fish", 28), ("fish", 27),
        ]
        assert accepted[3]["variant"] == "elite"
        assert len(repairs) == 1 and "spawn-safety nudge" in repairs[0]
        assert "(3, 13) -> (8, 13)" in repairs[0]
        assert len(problems) == 5
        assert any("swimmers must be placed inside water" in p for p in problems)
        assert any("only swimmers go in water" in p for p in problems)


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

        files_a = sorted(p.relative_to(run_a) for p in run_a.rglob("*") if p.is_file())
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

        for rel in files_a:
            assert (run_a / rel).read_bytes() == (run_b / rel).read_bytes(), (
                f"{rel} differs between identical-seed runs"
            )

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
                expected = volume if archetype == "swimmer" else stand
                assert tuple(placement.pos) in expected, (
                    f"{enemy_id} ({archetype}) at {placement.pos}"
                )

    def test_canned_fake_places_a_flyer_in_open_air(self, tmp_path: Path) -> None:
        """The canned fake exercises the flyer path end-to-end at $0: a
        flyer-rolling seed yields a flyer definition placed in an open-air
        cell (never a ground stand or water) — the air-summary + fake air
        pool + validator airborne branch all on the same path."""
        ctx = _run_slice(tmp_path / "run", seed="zephyr")
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
            assert len(trace["attempts"]) == retries
            for a in trace["attempts"]:
                assert a["outcome"] == "failed_validation"
                assert a["reasons"]
                assert a["content"] == "not a dsl at all"
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
        program unrecoverable — layout attempts must climb 768→1152→1728."""
        good = make_fake_responder()
        seen: list[int | None] = []

        def broken_layouts(request):
            if "### TASK: layout" in request.user_message:
                seen.append(request.max_tokens)
                return "not a dsl at all"
            return good(request)

        _run_slice(tmp_path / "run", responder=broken_layouts)
        assert seen[:3] == [768, 1152, 1728]

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
        artifact."""
        run = tmp_path / "run"
        ctx = _run_slice(run)
        levels = ctx.bible.levels

        # Variable dims: schema-rolled RANGES, banded by difficulty —
        # rolled values land inside their band and escalate in width.
        bands = {
            "l1": ((40, 52), (14, 16)),
            "l2": ((52, 66), (16, 20)),
            "l3": ((64, 84), (16, 24)),
        }
        for lid, ((w_lo, w_hi), (h_lo, h_hi)) in bands.items():
            level = levels[lid]
            assert w_lo <= level.grid_width <= w_hi, lid
            assert h_lo <= level.grid_height <= h_hi, lid
        assert levels["l1"].grid_width < levels["l3"].grid_width
        # Water present in every canned level's collision layer.
        for level in levels.values():
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
            assert sorted(named) == ["champion", "elite", "relentless"]
            # entities.json carries the variant field for consumers.
            entities_doc = json.loads(
                (run / f"level/{level.stage_id}/{level.level_id}/entities.json")
                .read_text()
            )
            assert [e["variant"] for e in entities_doc if e["variant"]] == [
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
            "empty", "floor", "platform", "wall", "spike", "water",
        ]
        assert [v["name"] for v in manifest["variants"]] == [
            "elite", "champion", "relentless",
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
        files = sorted(p.relative_to(run_a) for p in run_a.rglob("*") if p.is_file())
        for rel in files:
            assert (run_a / rel).read_bytes() == (run_b / rel).read_bytes(), rel

    def test_positive_generation_logging(self, tmp_path: Path, caplog) -> None:
        """Successful generations are logged at INFO (MazeWorld parity) —
        reviewers need evidence of what worked, not just what fell back."""
        import logging

        with caplog.at_level(logging.INFO):
            _run_slice(tmp_path / "run")
        text = caplog.text
        assert "WorldPhase produced world" in text
        assert "StagePhase planned stage" in text
        assert "EnemyGeneratorPhase produced a 4-creature world pool" in text
        assert re.search(r"Layout l1 \(difficulty 1, \d+x\d+\)", text)
        assert re.search(r"Layout l3 \(difficulty 3, \d+x\d+\)", text)
        assert "Placement l1: " in text
        assert "TileAssignmentPhase mapped 3 levels" in text
        assert "BackgroundPhase wrote 3" in text
        assert "Decor l1: " in text
        assert "PlaceholderTilesetPhase wrote" in text
        assert "RenderPhase wrote 3 level renders" in text
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

    def test_pool_needs_ground_floor(self) -> None:
        # The error names the occupying tile per bad column (here: never
        # floored at all, so 'empty') — located, not just prohibitive.
        with pytest.raises(DslError, match=r"20 \(empty\).*cannot sink in"):
            stamp("floor(0,10)\npool(water,20,25)\nspawn(2)\nexit(8)", W, H)

    def test_no_basin_message_teaches_pool_op(self) -> None:
        """The no-basin loop (volume poured over a pit, three identical
        real-model retries) must point at the op that DOES build sunken
        pools."""
        with pytest.raises(DslError, match=r"use pool\(water,20,24\)"):
            stamp(
                "floor(0,47)\ngap(20,24)\nwater(20,24,12)\nspawn(2)\nexit(45)",
                W, H,
            )

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

    def test_pour_on_ground_row_under_blockers_names_the_conflict(
        self,
    ) -> None:
        """When the on-top surface row is blocked, the old recipe was
        advice that could not work (the third real l3 run poured 24-30
        under its own spike strip at 28-30 — following the recipe would
        have failed again). The error must name the located conflict."""
        with pytest.raises(DslError) as exc_info:
            stamp(
                "floor(0,47)\nspike(22,23)\nwater(20,25,14)\n"
                "spawn(2)\nexit(45)",
                W, H,
            )
        message = str(exc_info.value)
        assert "IS the ground floor row" in message
        assert "ON TOP of the floor" in message
        assert "spike at column(s) 22, 23" in message
        assert "cannot share the surface" in message

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
        assert "wall(19, 12, 13)" in req.user_message  # worked example
        assert "NOT a rectangle" in req.user_message

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

    def test_real_conflict_surfaces_on_attempt_one(self) -> None:
        """The pool/spike overlap (24-30 vs 28-30) must be reported the
        FIRST time, located, instead of hiding behind the spawn false
        positive for two attempts."""
        with pytest.raises(DslError) as exc_info:
            stamp(_L3_ATTEMPT_1, 64, 18)
        message = str(exc_info.value)
        assert "spike at column(s) 28, 29, 30" in message
        assert "24-30" in message

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

    def test_stairs_need_ground_floor(self) -> None:
        with pytest.raises(DslError, match="stairs_up: column 25 has no ground"):
            stamp("floor(0,20)\nstairs_up(25,27)\nspawn(2)\nexit(18)", W, H)

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

    def test_water_block_floats_and_rejects_occupied_cells(self) -> None:
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
        with pytest.raises(DslError, match="must be open air"):
            stamp(
                f"floor(0,47)\nwater_block(20,{H - 2},22,{H - 2})\n"
                "spawn(2)\nexit(45)", W, H,
            )

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
            "platform": "#b8804a", "wall": "#5b4d5e",
            "danger": "#e0453a", "water": "#3a6ea5",
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
            "platform": "#c8843a", "wall": "#c0a882",
            "danger": "#e84210", "water": "#1a4a6b",  # distance ~32: fails
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
                    "danger": "#e84210", "water": "#1a4a6b",
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
        assert "### ROLES: background,ground,platform,wall,danger,water" in message
        assert "luminance distance >= 40" in message
        assert "dangerous at a glance" in message


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
        for rel in sorted(
            p.relative_to(run_a) for p in run_a.rglob("*") if p.is_file()
        ):
            assert (run_a / rel).read_bytes() == (run_b / rel).read_bytes(), rel


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
        with pytest.raises(DslError, match="carve: rows"):
            stamp(
                f"floor(0,47)\ncarve(5,{H - 2},6,{H - 2})\nspawn(2)\nexit(45)",
                W, H,
            )

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
    with main.gd's GDScript mirror. The CALLER builds the candidate priority
    list from runtime signals (enemy: hurt>walk>idle; player: jump>walk>idle)."""

    S = {
        "idle": {"count": 2, "dur": 0.25},
        "walk": {"count": 4, "dur": 0.10},
        "jump": {"count": 6, "dur": 0.09},
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

        # walk: dur 0.10, 4 frames → idx = int(t/0.10) % 4
        assert pick_anim_frame(["walk"], 0.00, self.S)[1] == 0
        assert pick_anim_frame(["walk"], 0.15, self.S)[1] == 1
        assert pick_anim_frame(["walk"], 0.35, self.S)[1] == 3
        assert pick_anim_frame(["walk"], 0.45, self.S)[1] == 0  # wraps

    def test_falls_through_when_no_candidate_exists(self) -> None:
        from examples.platformer_play import pick_anim_frame

        only_idle = {"idle": {"count": 3, "dur": 0.2}}
        # candidates absent → first state in the dict
        assert pick_anim_frame(["jump", "walk"], 0.0, only_idle)[0] == "idle"

    def test_deterministic(self) -> None:
        from examples.platformer_play import pick_anim_frame

        a = pick_anim_frame(["walk"], 0.37, self.S)
        b = pick_anim_frame(["walk"], 0.37, self.S)
        assert a == b

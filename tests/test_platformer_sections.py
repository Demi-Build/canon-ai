"""Section model + stitcher (sectioned-levels phase, chunk A)."""
from __future__ import annotations

from canon.pipeline.rng import derive_rng
from examples.platformer_pack.dsl import stamp
from examples.platformer_pack.sections import (
    DEFAULT_VOCAB,
    SECTION_OVERLAP,
    composite,
    plan_sections,
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
